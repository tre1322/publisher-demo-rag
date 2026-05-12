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
    ConversationItemAddedEvent,
    JobContext,
    WorkerOptions,
    cli,
)
from livekit.plugins import anthropic, cartesia, deepgram, silero

from src.core.config import (
    ANTHROPIC_API_KEY,
    CARTESIA_API_KEY,
    CARTESIA_VOICE_ID,
    DEEPGRAM_API_KEY,
    PMC_AGENT_NAME,
    PMC_VOICE_CALLBACK_BASE_URL,
)
from src.modules.pmc.interview_script import (
    INTERVIEW_LENGTH_CAP_MINUTES,
    INTERVIEW_TARGET_MINUTES,
    INTERVIEW_TONE,
    qualitative_questions,
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

## Pacing
- Target ~{INTERVIEW_TARGET_MINUTES} minutes. Hard cap {INTERVIEW_LENGTH_CAP_MINUTES}.
- If a "MUST COVER" question gets a thin answer, use the follow-up hints
  below to probe. If an "important" or "nice-to-have" question gets a
  thin answer and time is tight, accept it and move on.
- When you've covered all the MUST COVER questions OR you're approaching
  the hard cap, thank {owner_name} and wrap up. Say something like
  "We've covered everything I needed — thank you, {owner_name}. I'll have
  your marketing profile ready for you to review in a couple minutes."

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


# ── Callback POST to /voice/complete ──────────────────────────────────


async def post_transcript(
    *,
    callback_token: str,
    transcript: str,
    duration_seconds: int,
    partial: bool,
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

    # Build agent and session.
    instructions = build_system_prompt(owner_name=owner_name, org_name=org_name)

    agent = Agent(instructions=instructions)

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
        ),
        vad=silero.VAD.load(),
    )

    transcript = TranscriptAccumulator(owner_name=owner_name)
    start_time = time.monotonic()
    end_requested = asyncio.Event()

    # Capture each finalized turn for the transcript blob.
    @session.on("conversation_item_added")
    def _on_item(ev: ConversationItemAddedEvent) -> None:
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

    # Listen for end-of-call signals from the browser (End button, tab close).
    def _on_data(packet) -> None:
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

    ctx.room.on("data_received", _on_data)

    # Start the session — Day 2 has no recording (Day 3 adds Egress to Spaces).
    await session.start(agent=agent, room=ctx.room)
    logger.info("agent session started")

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
