"""LiveKit voice agent worker — W2.2 voice interview.

Runs as a separate process from the FastAPI app:

    uv run python -m src.modules.pmc.voice_agent dev      # local dev
    uv run python -m src.modules.pmc.voice_agent start    # production

The worker registers with LiveKit Cloud as `PMC_AGENT_NAME` and is
dispatched into a room by /business/pmc/voice/start. On dispatch:

  1. Read room metadata (session_id, callback_token, owner_name, org_name)
     set by voice_provisioning.start_voice_session().
  2. Build the system prompt from INTERVIEW_SCRIPT (the canonical question
     backbone — voice questions in declared order, with follow_up_hints
     materialized so Claude can probe naturally).
  3. Wire Deepgram STT → Claude Sonnet 4.6 LLM → Cartesia Sonic-3 TTS,
     with Silero VAD handling turn boundaries.
  4. Greet the owner first (Day 0 lesson: never let an owner speak into
     silence while STT cold-starts).
  5. Accumulate the transcript by listening to `conversation_item_added`
     events. Each finalized turn appends to `transcript_lines`.
  6. On session close (owner ended, agent ended, error): POST the
     accumulated transcript to /business/pmc/voice/complete with the
     HMAC-signed callback_token. Server runs the existing PMC pipeline.
  7. Publish a `redirect` data message before disconnecting so the
     browser navigates to the review page.

Day 2 scope: linear script-only state, no `mark_question_covered` tool,
no pacing injection, no recording. Day 3 adds those.

Per Trevor's NON-NEGOTIABLE: this code can only be declared done after a
real call. The hermetic smoke (Day 4) covers prompt/state logic; only a
real-audio test proves the SDK wiring works end-to-end.
"""

from __future__ import annotations

import asyncio
import json
import logging
import time
from typing import Any

import httpx
from livekit.agents import (
    Agent,
    AgentSession,
    ChatContext,
    ChatMessage,
    ConversationItemAddedEvent,
    JobContext,
    RunContext,
    WorkerOptions,
    cli,
    function_tool,
)
from livekit.plugins import anthropic, cartesia, deepgram, silero

from src.core.config import (
    ANTHROPIC_API_KEY,
    CARTESIA_API_KEY,
    CARTESIA_VOICE_ID,
    DEEPGRAM_API_KEY,
    PMC_AGENT_NAME,
    PMC_INTERVIEW_PAUSE_CAP_SECONDS,
    PMC_INTERVIEW_TARGET_SECONDS,
    PMC_VOICE_CALLBACK_BASE_URL,
    PMC_VOICE_RECORDING_ENABLED,
)
from src.modules.pmc.interview_script import (
    INTERVIEW_LENGTH_CAP_MINUTES,
    INTERVIEW_TARGET_MINUTES,
    INTERVIEW_TONE,
    qualitative_questions,
)
from src.modules.pmc.voice_provisioning import (
    recording_is_configured,
    start_recording_egress,
)

logger = logging.getLogger(__name__)


# ── System prompt assembly ────────────────────────────────────────────


def _format_question(idx: int, q) -> str:
    """Render one Question into the system prompt as a numbered block."""
    weight_label = {3: "MUST COVER", 2: "important", 1: "nice-to-have"}.get(
        q.weight, "important"
    )
    lines = [
        f"### Q{idx}. [{weight_label}] {q.category.value.replace('_', ' ').title()}",
        f"Ask: {q.prompt}",
    ]
    if q.follow_up_hints:
        lines.append("Follow-up hints (use only if the first answer is short):")
        for hint in q.follow_up_hints:
            lines.append(f"  - {hint}")
    return "\n".join(lines)


def build_system_prompt(owner_name: str, org_name: str) -> str:
    """Assemble the warm-personal interview instructions for Claude.

    The script is materialized verbatim so Claude sees the full backbone.
    Pacing rules are encoded but no live timer injection yet — that's Day 3.
    """
    questions_block = "\n\n".join(
        _format_question(i + 1, q) for i, q in enumerate(qualitative_questions())
    )

    return f"""You are the Amplora marketing interviewer — a warm, curious
voice that {org_name} hired to learn how to market the business better. The
business owner you're talking to is named {owner_name}. Use their first
name naturally, the way a friend who knows the business would.

## Your job
Conduct a {INTERVIEW_TARGET_MINUTES}-minute conversation (hard cap
{INTERVIEW_LENGTH_CAP_MINUTES} minutes) covering the questions below. The
transcript of this conversation feeds the marketing plan we'll build for
{org_name}, so substance matters more than speed.

## Tone — {INTERVIEW_TONE}
- "Tell me how you got into the business" → not → "I have 12 questions
  about your operation."
- Sound like a friend who already knows the business, not an enterprise
  procurement form.
- Verbose owners give the richest interviews. Don't rush them.
- Use contractions ("I'd", "you've", "we're") — this is spoken, not written.
- No markdown, no bullet points, no headers. Speak in complete sentences.
- One question at a time. Wait for a real answer before moving on.

## Tracking what you've covered — the mark_question_covered tool
You have one tool: `mark_question_covered(question_key, brief_summary)`.
Call it AS SOON as the owner has given a substantive answer to a question
and you're moving on — not at the end of the call, not in batches.

DO call it for: any answer that gives you a fact, a story, or a judgment
you could write into the marketing plan. Brief answers count if they're
real ("we mostly serve farmers within 20 miles" is enough for
ideal_customer).

DO NOT call it for: pure deflection ("I don't know", "skip that one").
If the owner truly passes, move on without marking.

The `question_key` is the Q-block key (lowercase, snake_case) — e.g.
`origin_story`, `ideal_customer`, `priority_services`. Use the exact key
from the question headings below. The `brief_summary` is a one-sentence
note about what they said.

## Pacing — adaptive, data-driven
- Target ~{INTERVIEW_TARGET_MINUTES} minutes. Hard cap {INTERVIEW_LENGTH_CAP_MINUTES}.
- Before each of your turns you'll see a PACING SNAPSHOT showing
  elapsed time, percent of target consumed, and how many must-cover
  and nice-to-have questions remain. Use it to make a judgment.
- If a "MUST COVER" question gets a thin answer, use the follow-up
  hints to probe — those are the questions the marketing plan depends on.
- If an "important" (nice-to-have) question gets a thin answer and
  time is tight, accept it and move on.

### When elapsed_pct ≥ 75 AND must-cover remain
Narrate pacing out loud — naturally, like a friend keeping an eye on
the clock — and offer to defer nice-to-have items so you can still
get the must-covers. Something like:

  "We're about three-quarters of the way through and I still want
  to make sure I get [X, Y, Z]. Mind if we save [the nice-to-have
  topic] for another time?"

Don't lecture about the schedule. Don't list every remaining question.
One natural sentence, then continue.

### When all MUST COVER are marked OR elapsed ≥ hard cap
Thank {owner_name} and wrap up. One closing line, e.g. "We've covered
everything I needed — thank you, {owner_name}. I'll have your marketing
profile ready for you to review in a couple minutes."

## When to wrap
After your closing line, the call ends and we generate the marketing
profile. Don't say goodbye more than once. Don't keep talking after the
wrap-up line.

## Questions to cover (in order, but adapt naturally)

{questions_block}

## Hard rules
- Never invent facts about {org_name} or the owner. If you don't know
  something, ask.
- Never lecture about marketing — you're listening, not consulting.
- Never read the question number aloud. The numbering is for your
  reference only.
- If {owner_name} says something racist, illegal, or about harming
  themselves, gently redirect or end the call. We can't help with those.

You are speaking out loud through a text-to-speech system. Write
responses as natural spoken English. Begin with a warm greeting that
introduces yourself and the purpose of the call, then ask Q1.
"""


# ── Transcript accumulator ────────────────────────────────────────────


class TranscriptAccumulator:
    """Captures each finalized chat turn into a single transcript blob.

    The downstream PMC pipeline expects a flat string (see
    transcript_to_pmc.generate_pmc_from_transcript), so we format
    interleaved turns as:

        Interviewer: <text>
        {owner_first_name}: <text>
        Interviewer: <text>
        ...
    """

    def __init__(self, owner_name: str):
        self.lines: list[str] = []
        self.owner_label = owner_name.split()[0] if owner_name else "Owner"

    def add(self, role: str, text: str) -> None:
        text = (text or "").strip()
        if not text:
            return
        speaker = "Interviewer" if role == "assistant" else self.owner_label
        self.lines.append(f"{speaker}: {text}")

    def as_blob(self) -> str:
        return "\n\n".join(self.lines)


# ── Coverage tracker (drives pacing + browser dots) ───────────────────


class CoverageTracker:
    """Accumulates which qualitative questions Claude has marked covered.

    Owns:
        - the canonical question list (frozen at agent start)
        - the ordered list of covered keys
        - the start_monotonic baseline used for elapsed_seconds

    Lifetime: one per voice job. Not thread-safe — only touched from the
    asyncio loop, which serializes accesses for us.
    """

    def __init__(self, target_seconds: int) -> None:
        self.questions = list(qualitative_questions())
        self.target_seconds = max(target_seconds, 60)  # don't divide by zero
        self.start_monotonic = time.monotonic()
        self.covered_keys: list[str] = []
        self.summaries: dict[str, str] = {}
        self._known_keys = {q.key for q in self.questions}
        # Pause state: while paused, elapsed_seconds() freezes so the
        # pacing rule doesn't penalize the interview for time the owner
        # spent dealing with whatever interrupted them.
        self._paused_seconds = 0.0
        self._pause_start: float | None = None

    def pause(self) -> bool:
        """Begin a pause. Returns True iff newly paused (idempotent)."""
        if self._pause_start is not None:
            return False
        self._pause_start = time.monotonic()
        return True

    def resume(self) -> bool:
        """End the current pause. Returns True iff there was a pause to end."""
        if self._pause_start is None:
            return False
        self._paused_seconds += time.monotonic() - self._pause_start
        self._pause_start = None
        return True

    @property
    def is_paused(self) -> bool:
        return self._pause_start is not None

    def mark(self, key: str, summary: str) -> bool:
        """Record `key` as covered. Returns True iff this was the first time.

        Unknown keys are accepted (with a WARNING) so Claude hallucinating
        a key doesn't crash the interview — the marketing plan can still
        be generated from the transcript even if the dots are slightly off.
        """
        if key not in self._known_keys:
            logger.warning(
                "mark_question_covered: unknown key %r (accepted)", key
            )
        if key in self.covered_keys:
            return False
        self.covered_keys.append(key)
        if summary:
            self.summaries[key] = summary[:240]
        return True

    def elapsed_seconds(self) -> int:
        """Wall-clock seconds since interview start, excluding paused time.

        While paused, this freezes at the pause-start timestamp — the
        pacing snapshot is read every turn but no turns happen while
        paused, so the freeze is mostly a safety net for any code path
        that reads the snapshot independently.
        """
        baseline = self._pause_start if self._pause_start is not None else time.monotonic()
        return int(baseline - self.start_monotonic - self._paused_seconds)

    def _remaining_keys(self, weight: int) -> list[str]:
        covered = set(self.covered_keys)
        return [
            q.key
            for q in self.questions
            if q.weight == weight and q.key not in covered
        ]

    def snapshot(self) -> dict[str, Any]:
        """Pacing context — what the InterviewAgent injects per turn."""
        elapsed = self.elapsed_seconds()
        w3 = self._remaining_keys(3)
        w2 = self._remaining_keys(2)
        return {
            "elapsed_seconds": elapsed,
            "target_seconds": self.target_seconds,
            "elapsed_pct": round(elapsed / self.target_seconds * 100, 1),
            "weight3_remaining": w3,
            "weight2_remaining": w2,
            "covered_count": len(self.covered_keys),
            "total": len(self.questions),
        }

    def browser_coverage_msg(self) -> dict[str, Any]:
        """Data-message payload — pmc_interview.js handleAgentMessage('coverage')."""
        snap = self.snapshot()
        return {
            "type": "coverage",
            "total": snap["total"],
            "covered": snap["covered_count"],
            # "current" tells the JS which dot to paint blue. One past
            # the covered count is the question Claude is presumably on.
            "current": min(snap["covered_count"], snap["total"] - 1),
            "weight3_remaining": len(snap["weight3_remaining"]),
        }


# ── Interview agent (injects pacing snapshot per turn) ────────────────


class InterviewAgent(Agent):
    """Agent subclass with a `on_user_turn_completed` hook that prepends a
    PACING SNAPSHOT system message to the chat context before Claude
    generates a reply.

    The snapshot is what makes the pacing rule in the system prompt
    actionable — without live timing data, Claude has no way to know
    when 75% of target has elapsed.
    """

    def __init__(
        self,
        *,
        instructions: str,
        tracker: CoverageTracker,
        tools: list[Any],
    ) -> None:
        super().__init__(instructions=instructions, tools=tools)
        self.tracker = tracker

    async def on_user_turn_completed(
        self, turn_ctx: ChatContext, new_message: ChatMessage
    ) -> None:
        snap = self.tracker.snapshot()
        m, s = divmod(snap["elapsed_seconds"], 60)
        target_m = snap["target_seconds"] // 60
        total_w3 = sum(1 for q in self.tracker.questions if q.weight == 3)
        total_w2 = sum(1 for q in self.tracker.questions if q.weight == 2)
        # Note: this snapshot is fresh-per-turn; older snapshots remain
        # in chat_ctx but Claude treats the most recent one as ground
        # truth. ~6 lines × ~20 turns = negligible token overhead vs
        # Sonnet's 200K context. We could prune prior snapshots, but
        # the historical trail gives Claude a sense of how time is moving.
        content = (
            "PACING SNAPSHOT (visible to you, not to the owner):\n"
            f"  elapsed: {m}m{s:02d}s of {target_m}m target ({snap['elapsed_pct']}%)\n"
            f"  must-cover remaining: {len(snap['weight3_remaining'])} of {total_w3}\n"
            f"  nice-to-have remaining: {len(snap['weight2_remaining'])} of {total_w2}\n"
            f"  covered so far: {snap['covered_count']} of {snap['total']}\n"
        )
        if snap["weight3_remaining"]:
            content += f"  must-cover keys left: {', '.join(snap['weight3_remaining'])}\n"
        turn_ctx.add_message(role="system", content=content)


# ── Callback POST to /voice/complete ──────────────────────────────────


async def post_transcript(
    *,
    callback_token: str,
    transcript: str,
    duration_seconds: int,
    partial: bool,
    recording_url: str | None = None,
) -> dict[str, Any]:
    """POST the assembled transcript to the FastAPI app.

    Returns the server's JSON response on success, raises on failure.
    Caller is responsible for retrying — but the server is idempotent
    on session_id, so retrying the same payload is safe.
    """
    url = f"{PMC_VOICE_CALLBACK_BASE_URL.rstrip('/')}/business/pmc/voice/complete"
    payload = {
        "transcript": transcript,
        "duration_seconds": duration_seconds,
        "partial": partial,
        "recording_url": recording_url,
    }
    headers = {"X-Agent-Callback-Token": callback_token}
    timeout = httpx.Timeout(60.0, connect=10.0)
    async with httpx.AsyncClient(timeout=timeout) as client:
        resp = await client.post(url, json=payload, headers=headers)
        resp.raise_for_status()
        return resp.json()


# ── Entrypoint ────────────────────────────────────────────────────────


async def entrypoint(ctx: JobContext) -> None:
    """One agent job — handles a single voice interview from join to disconnect."""
    await ctx.connect()
    logger.info("agent connected to room %s", ctx.room.name)

    # Read metadata set by voice_provisioning.start_voice_session.
    raw_meta = ctx.room.metadata or "{}"
    try:
        meta = json.loads(raw_meta)
    except json.JSONDecodeError as e:
        logger.error("room metadata not JSON: %s; raw=%r", e, raw_meta[:200])
        await ctx.shutdown(reason="bad_metadata")
        return

    session_id = meta.get("session_id")
    callback_token = meta.get("callback_token")
    owner_name = meta.get("owner_name") or "there"
    org_name = meta.get("org_name") or "your business"

    if not session_id or not callback_token:
        logger.error(
            "room metadata missing session_id/callback_token: %s", list(meta)
        )
        await ctx.shutdown(reason="missing_metadata")
        return

    logger.info(
        "starting interview: session=%s owner=%r org=%r",
        session_id, owner_name, org_name,
    )

    # Wait for the owner to actually join before greeting (no point greeting
    # an empty room — Deepgram cold-starts on first audio, Cartesia warms up
    # on first speak, but if the owner isn't there yet they hear nothing).
    participant = await ctx.wait_for_participant()
    logger.info("participant joined: %s", participant.identity)

    # Build agent + tracker + tool. The tracker has to outlive every turn
    # but be scoped to this one job — so it's constructed here, not at
    # module top-level.
    instructions = build_system_prompt(owner_name=owner_name, org_name=org_name)
    tracker = CoverageTracker(target_seconds=PMC_INTERVIEW_TARGET_SECONDS)

    async def publish_coverage_to_browser() -> None:
        """Send a fresh coverage update to the browser dots. Non-fatal on error."""
        try:
            data_payload = json.dumps(tracker.browser_coverage_msg()).encode("utf-8")
            await ctx.room.local_participant.publish_data(
                data_payload, reliable=True
            )
        except Exception as e:
            logger.warning("coverage data-message publish failed: %s", e)

    @function_tool(
        name="mark_question_covered",
        description=(
            "Record that the owner has substantively answered one of the "
            "interview questions. Call AS SOON as you have a real answer "
            "and are moving on (not in batches at the end). Pass the exact "
            "question_key (lowercase, snake_case, from the Q-block headings) "
            "and a one-sentence brief_summary of what they said. Skip "
            "calling this if the owner truly passed on a question — just "
            "move on. Calling this twice on the same key is harmless."
        ),
    )
    async def mark_question_covered(
        context: RunContext,
        question_key: str,
        brief_summary: str,
    ) -> str:
        newly = tracker.mark(question_key, brief_summary)
        snap = tracker.snapshot()
        logger.info(
            "mark_question_covered: key=%r newly=%s covered=%d/%d w3_left=%d",
            question_key, newly, snap["covered_count"], snap["total"],
            len(snap["weight3_remaining"]),
        )
        # Update browser dots immediately so coverage feels live.
        await publish_coverage_to_browser()
        if newly:
            return (
                f"Marked '{question_key}' as covered. "
                f"{len(snap['weight3_remaining'])} must-cover topics left, "
                f"{len(snap['weight2_remaining'])} nice-to-have left, "
                f"{snap['elapsed_pct']}% of target time elapsed."
            )
        return (
            f"'{question_key}' was already marked earlier. "
            f"{len(snap['weight3_remaining'])} must-cover topics left."
        )

    agent = InterviewAgent(
        instructions=instructions,
        tracker=tracker,
        tools=[mark_question_covered],
    )

    session = AgentSession(
        stt=deepgram.STT(
            model="nova-3",
            language="en-US",
            api_key=DEEPGRAM_API_KEY,
            interim_results=True,
            smart_format=True,
            punctuate=True,
        ),
        llm=anthropic.LLM(
            model="claude-sonnet-4-6",
            api_key=ANTHROPIC_API_KEY,
            temperature=0.4,  # conversational, not 0.0 — we want warmth
        ),
        tts=cartesia.TTS(
            api_key=CARTESIA_API_KEY,
            voice=CARTESIA_VOICE_ID,
            # sonic-2 is older but more consistent across network conditions
            # than sonic-3 (default). sonic-3's slightly higher quality on a
            # good day isn't worth shaky audio on a real-world connection.
            model="sonic-2",
            # Without this, Cartesia synthesizes audio in lockstep with
            # Claude's token bursts — uneven token timing → uneven audio
            # → "shaky and choppy" agent voice. text_pacing puts a paced
            # buffer between LLM and TTS so audio comes out at a constant
            # rate regardless of upstream jitter.
            text_pacing=True,
        ),
        vad=silero.VAD.load(),
    )

    transcript = TranscriptAccumulator(owner_name=owner_name)
    start_time = time.monotonic()
    end_requested = asyncio.Event()
    owner_first_name = owner_name.split()[0] if owner_name else "there"
    pause_watchdog_task: asyncio.Task[None] | None = None

    # Capture each finalized turn for the transcript blob — but only when
    # not paused (mic is muted client-side during pause, so this is a
    # belt-and-suspenders check).
    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent) -> None:
        if tracker.is_paused:
            return
        item = ev.item
        role = getattr(item, "role", "")
        # ChatMessage.content is a list of ChatContent objects; concat the
        # textual parts. Non-text parts (images, audio frames) are skipped.
        content = getattr(item, "content", []) or []
        texts: list[str] = []
        for c in content:
            if isinstance(c, str):
                texts.append(c)
            else:
                # ChatContent may have a `.text` attribute or be a pydantic model
                txt = getattr(c, "text", None)
                if txt:
                    texts.append(txt)
        joined = " ".join(t for t in texts if t).strip()
        if joined:
            transcript.add(role, joined)
            logger.debug("turn captured: %s: %s", role, joined[:80])

    async def _do_pause() -> None:
        """Stop in-flight TTS and freeze the tracker. Idempotent."""
        if not tracker.pause():
            return
        logger.info("interview paused")
        try:
            await session.interrupt()
        except Exception as e:
            # Non-fatal — interrupt may fail if agent wasn't speaking.
            logger.debug("session.interrupt during pause: %s", e)
        # Echo state to the browser for telemetry; the JS already handled
        # the local UI on click but a confirming message helps diagnosis.
        try:
            ack = json.dumps({"type": "state", "pill": "Paused", "live": False}).encode("utf-8")
            await ctx.room.local_participant.publish_data(ack, reliable=True)
        except Exception as e:
            logger.debug("paused-ack publish failed: %s", e)

    async def _do_resume() -> None:
        """Un-freeze the tracker and say a brief welcome-back. Idempotent."""
        if not tracker.resume():
            return
        logger.info(
            "interview resumed (pause cap was %ds)", PMC_INTERVIEW_PAUSE_CAP_SECONDS
        )
        # Echo state.
        try:
            ack = json.dumps({"type": "state", "pill": "Listening", "live": True}).encode("utf-8")
            await ctx.room.local_participant.publish_data(ack, reliable=True)
        except Exception as e:
            logger.debug("resumed-ack publish failed: %s", e)
        # Brief welcome-back so the owner knows the agent is back online.
        # Kept short so Claude can take over naturally on the owner's next turn.
        try:
            await session.say(
                f"Welcome back, {owner_first_name}. Whenever you're ready to keep going.",
                allow_interruptions=True,
            )
        except Exception as e:
            logger.warning("welcome-back say failed: %s", e)

    async def _pause_watchdog() -> None:
        """Auto-end the interview if the pause exceeds PMC_INTERVIEW_PAUSE_CAP_SECONDS."""
        try:
            await asyncio.sleep(PMC_INTERVIEW_PAUSE_CAP_SECONDS)
        except asyncio.CancelledError:
            return
        if tracker.is_paused:
            logger.warning(
                "pause cap %ds reached without resume — auto-ending interview",
                PMC_INTERVIEW_PAUSE_CAP_SECONDS,
            )
            end_requested.set()

    # Listen for end-of-call + pause/resume signals from the browser.
    def _on_data(packet) -> None:
        nonlocal pause_watchdog_task
        try:
            data = packet.data if hasattr(packet, "data") else packet
            text = bytes(data).decode("utf-8")
            msg = json.loads(text)
        except Exception as e:
            logger.warning("bad data packet: %s", e)
            return
        msg_type = msg.get("type")
        if msg_type in {"end_requested", "user_disconnected"}:
            logger.info("end signal from browser: %s", msg_type)
            end_requested.set()
        elif msg_type == "pause_requested":
            logger.info("pause signal from browser")
            asyncio.create_task(_do_pause())
            # Cancel any prior watchdog (defensive — shouldn't happen)
            if pause_watchdog_task and not pause_watchdog_task.done():
                pause_watchdog_task.cancel()
            pause_watchdog_task = asyncio.create_task(_pause_watchdog())
        elif msg_type == "resume_requested":
            logger.info("resume signal from browser")
            if pause_watchdog_task and not pause_watchdog_task.done():
                pause_watchdog_task.cancel()
                pause_watchdog_task = None
            asyncio.create_task(_do_resume())

    ctx.room.on("data_received", _on_data)

    await session.start(agent=agent, room=ctx.room)
    logger.info("agent session started")

    # Kick off the LiveKit Egress recording (Day 3). The interview can
    # continue if this fails — we log a warning, the disclosure banner
    # on the page is honest about "if recording is unavailable", and
    # the marketing-profile generation is unaffected.
    recording_url: str | None = None
    if PMC_VOICE_RECORDING_ENABLED and recording_is_configured():
        try:
            _egress_id, recording_url = await start_recording_egress(
                room_name=ctx.room.name,
                session_id=session_id,
                organization_id=meta["org_id"],
            )
            logger.info("recording egress started: %s", recording_url)
        except Exception as e:
            logger.warning(
                "egress start failed (interview continues without recording): %s", e
            )
    elif not PMC_VOICE_RECORDING_ENABLED:
        logger.info("recording disabled by PMC_VOICE_RECORDING_ENABLED")
    else:
        logger.warning(
            "recording skipped: Spaces not configured (see SPACES_* env vars)"
        )

    # Paint the initial dot row in the browser (all dots empty, current=0).
    await publish_coverage_to_browser()

    # Greet first. Without this, owner sits in silence while Claude waits
    # for the first user turn — and Deepgram's cold-start makes that gap
    # uncomfortable. The session.say bypasses LLM and goes straight to TTS.
    await session.say(
        f"Hi {owner_name.split()[0]}, this is your Amplora marketing "
        f"interviewer. Thanks for setting aside some time. I've got a "
        f"handful of questions about {org_name} — about your customers, "
        f"what makes you different, what you want to grow. There are no "
        f"wrong answers. Whenever you're ready, let's start: how did you "
        f"first get into this business?",
        allow_interruptions=True,
    )

    # Wait until end requested OR session naturally closes (agent said goodbye).
    # Hard cap on 60 minutes per INTERVIEW_LENGTH_CAP_MINUTES.
    try:
        await asyncio.wait_for(
            end_requested.wait(), timeout=INTERVIEW_LENGTH_CAP_MINUTES * 60
        )
        partial = True
        end_reason = "browser_end_signal"
    except asyncio.TimeoutError:
        partial = False
        end_reason = "hard_cap_reached"
        logger.warning("hard cap %s min reached — wrapping up", INTERVIEW_LENGTH_CAP_MINUTES)

    duration = int(time.monotonic() - start_time)
    logger.info(
        "ending interview: reason=%s duration=%ds turns=%d",
        end_reason, duration, len(transcript.lines),
    )

    # Give the agent a moment to finish any in-flight speech, then close.
    try:
        await session.drain()
    except Exception as e:
        logger.warning("session.drain failed (non-fatal): %s", e)

    blob = transcript.as_blob()
    if not blob:
        logger.error("transcript is empty — not posting callback (session=%s)", session_id)
        await session.aclose()
        return

    # POST to /voice/complete. Retry up to 3 times — idempotent on session_id.
    server_response: dict[str, Any] | None = None
    for attempt in range(1, 4):
        try:
            server_response = await post_transcript(
                callback_token=callback_token,
                transcript=blob,
                duration_seconds=duration,
                partial=partial,
                recording_url=recording_url,
            )
            logger.info(
                "callback succeeded (attempt %d): pmc_id=%s",
                attempt, server_response.get("pmc_id"),
            )
            break
        except Exception as e:
            logger.warning(
                "callback failed (attempt %d/3): %s", attempt, e
            )
            if attempt < 3:
                await asyncio.sleep(2 * attempt)

    # Tell the browser to navigate to the review page.
    if server_response and server_response.get("redirect_to"):
        try:
            data_payload = json.dumps(
                {"type": "redirect", "to": server_response["redirect_to"]}
            ).encode("utf-8")
            await ctx.room.local_participant.publish_data(
                data_payload, reliable=True
            )
            logger.info("redirect data message published")
            # Brief pause so the message actually flushes before we close.
            await asyncio.sleep(0.5)
        except Exception as e:
            logger.warning("redirect publish failed (browser will poll instead): %s", e)

    await session.aclose()


# ── Module entrypoint ─────────────────────────────────────────────────


if __name__ == "__main__":
    # `uv run python -m src.modules.pmc.voice_agent dev` registers a worker
    # with LiveKit Cloud using LIVEKIT_URL / LIVEKIT_API_KEY / LIVEKIT_API_SECRET
    # from .env (loaded via src.core.config side-effect on import above).
    cli.run_app(
        WorkerOptions(
            entrypoint_fnc=entrypoint,
            agent_name=PMC_AGENT_NAME,
        )
    )
