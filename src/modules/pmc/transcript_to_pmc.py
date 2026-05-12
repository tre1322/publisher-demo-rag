"""Transcript -> structured PMC markdown.

Single Claude call. Inputs:
  - the canonical interview script (defines the expected output structure)
  - the quantitative form data (already filled by the owner)
  - the raw interview transcript

Output: structured markdown PMC. Status starts as 'draft'; owner reviews
and accepts in the dashboard.

W2.1 supports manual transcript paste only. W2.2 will replace the
transcript source (Twilio/LiveKit -> text) without changing this module.

Per the plan (~/.claude/plans/for-project-amplora-we-cozy-corbato.md):
"Single LLM call (Claude Sonnet for now; sovereign-AI migration in
Phase 1.5+)."

Risk register #1: "If the interview agent loses the thread, the
resulting pmc.md is garbage and every downstream agent inherits the
problem. Mitigation: scripted skeleton + LLM flexibility, NOT
free-form LLM. Owner-review gate before pmc.md is canonical."
The scripted skeleton is the qualitative-questions list materialized
verbatim into the prompt.
"""

import json
import logging
import os

from src.modules.pmc.interview_script import (
    INTERVIEW_TONE,
    SCRIPT_VERSION,
    qualitative_questions,
)

logger = logging.getLogger(__name__)


GENERATOR_PROMPT_VERSION = "v4"
DEFAULT_MODEL = os.getenv("PMC_MODEL", "claude-sonnet-4-20250514")


# v2 (2026-05-10) introduces the STRATEGIC SUMMARY block. The plan
# generator (W3) reads this block first; it's the contract between PMC
# and downstream agents. Synthesized from the question answers, NOT a
# new question to the owner. Adding/removing fields here means bumping
# GENERATOR_PROMPT_VERSION.
#
# v3 (2026-05-11) splits the old AMPLIFY/MUTE binary into three buckets.
# The Westbrook real-LLM test showed the LLM correctly hedging when the
# owner's "muted" service was actually the cash cow — a hedge the v2
# prompt forbade. v3 acknowledges the third state operationally:
#   AMPLIFY  — headline this, build campaigns around it
#   MAINTAIN — keep serving customers who ask, do not headline
#   MUTE     — actively refer out or sunset
# Adds 1 field; field count: 12 → 13.
#
# v4 (2026-05-11) adds VOICE. Westbrook real-LLM round 3 (with engineered
# voice mismatch) showed the voice_and_tone AGENT NOTE parroting the
# owner's self-description ("warm, welcoming, family-friendly") even
# when the transcript voice was clearly dry/blunt and brand_guardrails
# explicitly prohibited "family" language. The PMC ended up internally
# contradictory: same document said "be warm" and "no family language."
# v4 forces a synthesis-layer voice signal grounded in transcript
# evidence (not the owner's self-description) and asks the prompt to
# explicitly flag the gap when the owner's self-description disagrees.
# Owners systematically over-warm — for VOICE we observe, not ask.
# Adds 1 field; field count: 13 → 14.
STRATEGIC_SUMMARY_FIELDS: list[str] = [
    "TARGET",            # who we're trying to reach (primary persona)
    "ANTI-TARGET",       # whose attention we don't want
    "AMPLIFY",           # services/products to push hard
    "MAINTAIN",          # cash-cow services to keep, but not headline
    "MUTE / DO NOT PUSH",  # services to refer out or sunset
    "POSITIONING",       # one-sentence positioning statement
    "PROOF",             # the credibility evidence we lead with
    "CHANNEL PRIORITY",  # ordered channel mix recommendation
    "SEASONALITY",       # the calendar shape
    "CONVERSION ACTION", # primary CTA + lead handling
    "COMPETITIVE FRAME", # who we differentiate against
    "VOICE",             # actual voice from transcript (NOT self-description)
    "BRAND GUARDRAILS",  # what we never say or do
    "SUCCESS METRIC",    # owner's own definition of success
]


def _build_prompt(quantitative: dict, transcript: str) -> str:
    """Compose the LLM prompt. Script + quantitative + transcript -> structured PMC.

    Don't free-form this. The script is materialized verbatim so the LLM
    extracts spans in the same order/structure every run; that's what
    makes the PMC reliably parseable by downstream agents.
    """
    qualitative_qs = qualitative_questions()
    sections_outline = "\n".join(
        f"  - `{q.key}` ({q.category.value}) — {q.prompt}" for q in qualitative_qs
    )
    quantitative_block = json.dumps(quantitative, indent=2, ensure_ascii=False)
    summary_outline = "\n".join(f"  - {f}: " for f in STRATEGIC_SUMMARY_FIELDS)

    return f"""You are extracting a Product Marketing Context (PMC) from a recorded
interview with the owner of a Main Street business. Tone of source
material: {INTERVIEW_TONE}. The PMC will be the canonical input for
every downstream marketing agent — content drafter, GBP manager, review
responder, plan generator. Get it right.

The PMC has TWO LAYERS:

  Layer 1 — STRATEGIC SUMMARY at the top: a synthesized executive view
  the plan generator reads first. This is where you condense the entire
  interview into the 12 decisions a marketing plan must make.

  Layer 2 — Question-by-question sections below, in the owner's voice,
  with direct quotes preserved. Used for nuance + downstream agents that
  need full context.

PRE-INTERVIEW QUANTITATIVE DATA (already filled by the owner — incorporate verbatim, don't summarize):
{quantitative_block}

INTERVIEW TOPICS (each must become an H2 section in Layer 2; if the
transcript didn't cover one, write "Not discussed — to revisit"):
{sections_outline}

INTERVIEW TRANSCRIPT:
\"\"\"
{transcript}
\"\"\"

RULES — LAYER 1 (STRATEGIC SUMMARY):
  A. Start the PMC with an H1 "Product Marketing Context" line.
  B. Then a "## STRATEGIC SUMMARY" H2 block with EXACTLY these
     bullets, in this order, each filled in 1-3 sentences synthesized
     from the answers (NOT a re-quote — your strategic synthesis):
{summary_outline}
  C. If a field can't be confidently synthesized (transcript didn't
     cover it, or answer was unclear), write "[NEEDS REVIEW] — short
     reason." Don't fabricate.
  D. The STRATEGIC SUMMARY is the contract with the plan generator.
     Be decisive.

     AMPLIFY / MAINTAIN / MUTE: the owner's own classification from
     the `priority_services` answer is authoritative. Use what they
     said.

       AMPLIFY  = services the owner placed in the "more customers"
                  pile. Headline these — every campaign is built
                  around them.
       MAINTAIN = services the owner placed in the "fine where they
                  are" pile. Keep serving customers who ask for them;
                  do NOT headline. Usually the cash-cow or funnel
                  services that sustain the business but aren't the
                  growth story.
       MUTE     = services the owner placed in the "refer out / stop
                  offering" pile. Redirect customers who ask; do not
                  promote.

     If the owner skipped a service entirely, infer from transcript
     signals (margin, capacity, owner enthusiasm, funnel value) and
     flag the inference with "[NEEDS REVIEW] — owner did not classify;
     inferred from <signal>."

  E. VOICE specifically — owners systematically describe themselves as
     warmer, friendlier, and more polished than they actually sound.
     For voice we OBSERVE, not ask.

     Synthesize the VOICE field from the owner's ACTUAL TRANSCRIPT TEXT
     across the whole interview, not from their self-description in the
     `voice_and_tone` answer. Inspect:
       - How they answer hard questions (clipped, warm, defensive?)
       - Vocabulary register (technical, plain, slangy, formal)
       - Self-deprecation, dry humor, pride, urgency markers
       - Anything in `brand_guardrails` that prohibits a voice trait
         (e.g. "no 'family' language" rules out a family-friendly voice
         even if the owner described themselves that way)

     If the owner's self-description in `voice_and_tone` disagrees with
     the transcript evidence, the EVIDENCE WINS. In both the Layer 1
     VOICE field AND the Layer 2 `voice_and_tone` AGENT NOTE, flag the
     gap explicitly: "Owner described voice as X; transcript evidence
     is Y; write to Y." When they agree, no special flag — just describe
     the voice plainly.

RULES — LAYER 2 (question-by-question sections):
  1. Each topic gets its own H2 section. Use the section key from the
     outline as the H2 heading. Write the answer in the OWNER'S OWN
     VOICE wherever the transcript supports direct quotes.
  2. Quote the owner directly (in italics + quotation marks) for any
     answer that captures voice/tone, switching incentives, origin
     story, or differentiation. Don't paraphrase those.
  3. After the STRATEGIC SUMMARY, include a compact business header:
     business name, address, hours, founded, drawn from the
     quantitative data.
  4. For every qualitative section, end with a one-line "AGENT NOTE:"
     — what a downstream content drafter should remember when writing
     posts in this owner's voice (e.g., "AGENT NOTE: avoid corporate
     phrasing; owner uses 'we', not 'our team'.").
  5. Output ONLY the markdown. No preamble, no closing sentence. Do
     not use emojis unless the owner used them.
  6. If a topic is unclear or contradicted in the transcript, flag it
     inline with "[NEEDS REVIEW]" and a one-line explanation. The
     owner will see and resolve these in the dashboard.
"""


def generate_pmc_from_transcript(
    quantitative: dict,
    transcript: str,
    *,
    model: str | None = None,
    _client=None,
) -> tuple[str, dict]:
    """Run the LLM once. Return (qualitative_md, generation_metadata).

    Args:
        quantitative: pre-interview form answers, by question key.
        transcript: raw transcript text.
        model: override the model env default.
        _client: dependency-injected Anthropic client (None = real). The
            smoke test passes a fake here so the pipeline is hermetically
            testable without an API key.

    Returns:
        (qualitative_md, metadata dict) — metadata has model name +
        prompt version + script version, all of which get persisted on
        the PMC row.
    """
    if not transcript.strip():
        raise ValueError("transcript is empty; nothing to extract")

    prompt = _build_prompt(quantitative, transcript)
    used_model = model or DEFAULT_MODEL

    if _client is None:
        # Local import: anthropic is an optional dep at boot. Failing here
        # produces a clean error instead of crashing app startup.
        from anthropic import Anthropic  # type: ignore[import-not-found]

        _client = Anthropic()

    response = _client.messages.create(
        model=used_model,
        max_tokens=4096,
        messages=[{"role": "user", "content": prompt}],
    )
    qualitative_md = response.content[0].text if response.content else ""

    return qualitative_md, {
        "model": used_model,
        "prompt_version": GENERATOR_PROMPT_VERSION,
        "script_version": SCRIPT_VERSION,
    }
