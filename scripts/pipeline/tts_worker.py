"""
TTS worker — called via subprocess using the tts project venv.

Usage:
    /home/user/PycharmProjects/tts/.venv/bin/python3 tts_worker.py <text> <output.wav>
"""

import io
import sys
import warnings
import logging
from pathlib import Path

warnings.filterwarnings("ignore")
logging.disable(logging.CRITICAL)

text = sys.argv[1]
output_path = Path(sys.argv[2])

from ukrainian_tts.tts import TTS, Voices, Stress  # noqa: E402

tts = TTS(device="cuda")
buf = io.BytesIO()
tts.tts(text, Voices.Oleksa.value, Stress.Dictionary.value, buf)
output_path.parent.mkdir(parents=True, exist_ok=True)
output_path.write_bytes(buf.getvalue())
