"""Synthesize a PMC-style voice brief from a transcript via Claude.

Reads <stem>_transcript.txt, feeds it to Claude Sonnet 4.6 with a synthesis
prompt that targets the W2.1 PMC v4 shape (AMPLIFY / MAINTAIN / MUTE buckets,
VOICE field, observe-not-ask discipline), and writes the JSON result to
data/voice-brief/<business>.json.

Usage:
  uv run python scripts/synthesize_voice_brief.py \\
    data/voice-brief/2_2_transcript.txt \\
    --business-name "Quadd.ai" \\
    --owner "Trevor Slette" \\
    --out data/voice-brief/quadd_ai.json
"""
from __future__ import annotations

import argparse
import json
import os
import sys
from pathlib import Path

# Load .env so ANTHROPIC_API_KEY is available the same way the dashboard loads it.
# override=True is deliberate — see app/main.py for the rationale (shells that
# export ANTHROPIC_API_KEY="" defeat dotenv's default override=False).
from dotenv import find_dotenv, load_dotenv

load_dotenv(find_dotenv(usecwd=True), override=True)


SYSTEM_PROMPT = """You are a marketing strategist reading the transcript of a \
voice interview between an Amplora marketing-pipeline agent and a small business \
owner. Your job is to produce a "voice brief" — a structured JSON artifact that \
downstream marketing agents will use to write copy on this business's behalf.

Read the entire transcript carefully. Be precise and observational. Use the \
OWNER's actual words and phrasings wherever possible. Do not paraphrase into \
marketing-speak. If the interview was cut short and a section has nothing to \
populate, return an empty list or empty string for that section — do NOT invent \
content the owner didn't speak about.

Return ONLY a single JSON object (no preamble, no markdown fences) with this \
exact schema:

{
  "voice": "2-3 sentences describing the owner's tone, register, and characteristic phrasings, based on the actual words and rhythm in the transcript (e.g. 'speaks in short declarative sentences', 'uses agricultural metaphors', 'self-deprecating about technical details'). Observe, don't ask.",
  "amplify": [
    {"label": "short title", "detail": "1-2 sentences in the owner's voice"}
  ],
  "maintain": [
    {"label": "short title", "detail": "1-2 sentences"}
  ],
  "mute": [
    {"label": "short title", "detail": "1-2 sentences"}
  ],
  "audience": "1-2 sentences describing the customer profile in the owner's words",
  "value_prop": "the owner's stated value proposition, in their own phrasing where possible",
  "customer_language": ["distinctive phrases customers use (in the owner's quotes)"],
  "proof_points": [
    {"label": "credibility marker", "detail": "supporting context"}
  ],
  "constraints": ["operational or strategic constraints the owner named"],
  "seasonal_patterns": ["any seasonal or temporal rhythms mentioned"],
  "notes": "anything else that doesn't fit above but matters for the marketing agent"
}

AMPLIFY = things the owner explicitly wants to play up (distinctive strengths, \
customer-pull factors, proof points worth featuring).

MAINTAIN = parts of the business the owner is satisfied with and doesn't want \
to change focus on.

MUTE = things the owner explicitly wants to play down (weaknesses, regrets, \
push-factors, things they don't want to be known for).

If the interview ended mid-question (e.g. the agent froze with X questions \
left), still produce the brief from what was covered. Mark unfilled sections \
with empty values rather than guessing.
"""


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("transcript", help="Path to <stem>_transcript.txt")
    ap.add_argument("--business-name", required=True)
    ap.add_argument("--owner", default="")
    ap.add_argument("--out", required=True, help="Output JSON path")
    ap.add_argument("--model", default="claude-sonnet-4-6")
    args = ap.parse_args()

    transcript_path = Path(args.transcript).resolve()
    if not transcript_path.exists():
        print(f"FAIL  transcript not found: {transcript_path}", file=sys.stderr)
        return 1

    transcript = transcript_path.read_text(encoding="utf-8")
    if len(transcript) < 200:
        print(f"WARN  transcript is suspiciously short ({len(transcript)} chars)", file=sys.stderr)

    api_key = os.environ.get("ANTHROPIC_API_KEY")
    if not api_key:
        print("FAIL  ANTHROPIC_API_KEY not in env", file=sys.stderr)
        return 1

    from anthropic import Anthropic

    client = Anthropic(api_key=api_key)
    user_msg = (
        f"Business: {args.business_name}\n"
        f"Owner: {args.owner or '(unspecified)'}\n\n"
        f"--- INTERVIEW TRANSCRIPT ---\n\n{transcript}\n\n--- END TRANSCRIPT ---\n\n"
        f"Produce the voice-brief JSON now."
    )

    print(f"Synthesizing voice brief from {transcript_path.name} "
          f"({len(transcript):,} chars) via {args.model}...")
    resp = client.messages.create(
        model=args.model,
        max_tokens=4096,
        system=SYSTEM_PROMPT,
        messages=[{"role": "user", "content": user_msg}],
    )
    text = "".join(b.text for b in resp.content if getattr(b, "type", None) == "text").strip()

    # Strip code fences if Claude added them despite the instruction.
    if text.startswith("```"):
        text = text.strip("`").lstrip("json").strip()

    try:
        brief = json.loads(text)
    except json.JSONDecodeError as exc:
        print(f"FAIL  Claude returned non-JSON:\n{text[:600]}\n\nError: {exc}", file=sys.stderr)
        return 2

    # Stamp source metadata for traceability.
    brief["_source"] = {
        "business_name": args.business_name,
        "owner": args.owner,
        "transcript_path": str(transcript_path),
        "model": args.model,
        "input_tokens": resp.usage.input_tokens,
        "output_tokens": resp.usage.output_tokens,
    }

    out_path = Path(args.out).resolve()
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(brief, indent=2), encoding="utf-8")
    print(f"\nWrote voice brief to {out_path}")
    print(f"  tokens: in={resp.usage.input_tokens} out={resp.usage.output_tokens}")
    print(f"\n=== VOICE ===\n{brief.get('voice', '(empty)')}\n")
    print(f"=== AMPLIFY ({len(brief.get('amplify', []))} items) ===")
    for item in brief.get("amplify", [])[:5]:
        print(f"  - {item.get('label', '?')}: {item.get('detail', '?')[:120]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
