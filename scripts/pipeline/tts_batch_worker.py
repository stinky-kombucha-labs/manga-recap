"""
Batch TTS worker — loads Oleksa model ONCE, processes all pages in a chapter.

Called by tts.py with a JSON file containing {page_num: text} pairs.
Writes WAV files alongside the JSON, then exits.

Usage (internal):
    /path/to/tts/.venv/python3 tts_batch_worker.py batch.json /output/dir/
"""

import io
import json
import sys
import warnings
import logging
from pathlib import Path

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

batch_json = Path(sys.argv[1])
out_dir = Path(sys.argv[2])

tasks = json.loads(batch_json.read_text(encoding="utf-8"))

from ukrainian_tts.tts import TTS, Voices, Stress  # noqa: E402

tts = TTS(device="cuda")

for page_num, text in tasks.items():
    if not text.strip():
        continue
    out_path = out_dir / f"{int(page_num):04d}.wav"
    if out_path.exists():
        continue
    try:
        buf = io.BytesIO()
        tts.tts(text, Voices.Oleksa.value, Stress.Dictionary.value, buf)
        out_path.write_bytes(buf.getvalue())
        print(f"OK {page_num}: {len(buf.getvalue())} bytes")
    except Exception as e:
        print(f"FAIL {page_num}: {e}")
