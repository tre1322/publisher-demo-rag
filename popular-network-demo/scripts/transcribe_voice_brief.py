"""Transcribe the captured PMC voice interview locally.

Reads a downloaded OGG recording, runs faster-whisper, writes:
  - <stem>_transcript.txt  — plain text (concatenated segments)
  - <stem>_segments.json   — list of {start, end, text} for review/debugging

Usage:
  uv run --with faster-whisper python scripts/transcribe_voice_brief.py \\
    data/voice-brief/2_2.ogg
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("audio", help="Path to the OGG/WAV/MP3 recording")
    ap.add_argument("--model", default="small.en", help="faster-whisper model name")
    ap.add_argument("--compute-type", default="int8", help="int8 (CPU), float16 (GPU)")
    args = ap.parse_args()

    audio_path = Path(args.audio).resolve()
    if not audio_path.exists():
        print(f"FAIL  audio not found: {audio_path}", file=sys.stderr)
        return 1

    from faster_whisper import WhisperModel

    print(f"Loading faster-whisper model={args.model!r} compute_type={args.compute_type!r}...")
    t0 = time.time()
    model = WhisperModel(args.model, device="cpu", compute_type=args.compute_type)
    print(f"  loaded in {time.time() - t0:.1f}s")

    print(f"Transcribing {audio_path.name} ({audio_path.stat().st_size / 1024 / 1024:.1f} MB)...")
    t0 = time.time()
    segments, info = model.transcribe(
        str(audio_path),
        language="en",
        vad_filter=True,
        vad_parameters={"min_silence_duration_ms": 500},
    )
    print(f"  language={info.language} probability={info.language_probability:.2f} "
          f"duration={info.duration:.1f}s")

    out_dir = audio_path.parent
    stem = audio_path.stem
    txt_path = out_dir / f"{stem}_transcript.txt"
    seg_path = out_dir / f"{stem}_segments.json"

    collected: list[dict] = []
    with txt_path.open("w", encoding="utf-8") as txt:
        for seg in segments:
            line = seg.text.strip()
            if not line:
                continue
            txt.write(line + "\n")
            collected.append({"start": seg.start, "end": seg.end, "text": line})
            # Live progress: a dot per ~30s of audio decoded.
            if len(collected) % 20 == 0:
                pct = seg.end / info.duration * 100
                print(f"  ... {pct:5.1f}% ({seg.end:6.1f}s / {info.duration:.0f}s)  "
                      f"{len(collected)} segments")

    seg_path.write_text(json.dumps(collected, indent=2), encoding="utf-8")
    elapsed = time.time() - t0
    print(f"\nDone in {elapsed:.1f}s.")
    print(f"  transcript: {txt_path}")
    print(f"  segments:   {seg_path}  ({len(collected)} segments)")
    print(f"\nFirst 600 chars:\n{txt_path.read_text(encoding='utf-8')[:600]}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
