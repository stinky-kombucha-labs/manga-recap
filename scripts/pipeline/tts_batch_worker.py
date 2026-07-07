"""
Batch TTS worker — loads Oleksa model ONCE, processes all pages in a chapter.

Called by tts.py with a JSON file containing {page_num: text} pairs.
Writes WAV files alongside the JSON, then exits.

Usage (internal):
    /path/to/tts/.venv/python3 tts_batch_worker.py batch.json /output/dir/
"""

import io
import json
import re
import sys
import warnings
import logging
from pathlib import Path

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

batch_json = Path(sys.argv[1])
out_dir = Path(sys.argv[2])

tasks = json.loads(batch_json.read_text(encoding="utf-8"))

import numpy as np                                  # noqa: E402
import soundfile as sf                              # noqa: E402
from ukrainian_tts.tts import TTS, Voices, Stress   # noqa: E402

tts = TTS(device="cuda")

# Synthesize SENTENCE BY SENTENCE, not the whole page at once: on long inputs
# the VITS model flattens prosody (questions stop sounding like questions), and
# bubble boundaries get no pause. Sentences keep their terminal punctuation —
# the model does receive ?/!/... — and are joined with a short silence.
_SENT_SPLIT = re.compile(r"(?<=[.!?…])\s+")
_PAUSE_SEC = 0.35


def _synth_page(text: str) -> tuple[np.ndarray, int]:
    chunks = [c.strip() for c in _SENT_SPLIT.split(text) if c.strip()]
    waves = []
    rate = 22050
    for chunk in chunks:
        buf = io.BytesIO()
        tts.tts(chunk, Voices.Oleksa.value, Stress.Dictionary.value, buf)
        buf.seek(0)
        wav, rate = sf.read(buf, dtype="float32")
        waves.append(wav)
    if not waves:
        return np.zeros(rate, dtype="float32"), rate
    pause = np.zeros(int(rate * _PAUSE_SEC), dtype="float32")
    joined = waves[0]
    for w in waves[1:]:
        joined = np.concatenate([joined, pause, w])
    return joined, rate


for page_num, text in tasks.items():
    if not text.strip():
        continue
    # NO exists-skip here: the caller (step3) already decides which pages need
    # (re-)voicing via the narration-hash audio cache. Skipping existing files
    # silently kept STALE audio whenever a translation changed.
    out_path = out_dir / f"{int(page_num):04d}.wav"
    try:
        wav, rate = _synth_page(text)
        sf.write(str(out_path), wav, rate, "PCM_16", format="wav")
        print(f"OK {page_num}: {out_path.stat().st_size} bytes")
    except Exception as e:
        print(f"FAIL {page_num}: {e}")
