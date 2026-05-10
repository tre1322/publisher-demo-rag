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


GENERATOR_PROMPT_VERSION = "v2"
DEFAULT_MODEL = os.getenv("PMC_MODEL", "claude-sonnet-4-20250514")


# v2 (2026-05-10) introduces the STRATEGIC SUMMARY block. The plan
# generator (W3) reads this block first; it's the contract between PMC
# and downstream agents. Synthesized from the question answers, NOT a
# new question to the owner. Adding/removing fields here means bumping
# GENERATOR_PROMPT_VERSION.
STRATEGIC_SUMMARY_FIELDS: list[str] = [
    "TARGET",            # who we're trying to reach (primary persona)
    "ANTI-TARGET",       # whose attention we don't want
    "AMPLIFY",           # services/products to push hard
    "MUTE / DO NOT PUSH",  # services we offer but don't want to grow
    "POSITIONING",       # one-sentence positioning statement
    "PROOF",             # the credibility evidence we lead with
    "CHANNEL PRIORITY",  # ordered channel mix recommendation
    "SEASONALITY",       # the calendar shape
    "CONVERSION ACTION", # primary CTA + lead handling
    "COMPETITIVE FRAME", # who we differentiate against
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
     Be decisive — "Push transmission work, mute oil changes" not
     "could focus on transmission work potentially."

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
