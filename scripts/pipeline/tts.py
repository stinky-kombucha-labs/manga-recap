"""
TTS module — batch Ukrainian audio generation via Oleksa voice.

Loads the model ONCE per chapter (batch worker) to avoid cold-start failures.
"""

from __future__ import annotations

import json
import subprocess
import tempfile
from pathlib import Path

_BATCH_WORKER = Path(__file__).parent / "tts_batch_worker.py"
_TTS_CWD = Path("/home/user/PycharmProjects/tts")  # model files are relative to this dir


def generate_chapter_audio(page_texts: dict[int, str], out_dir: Path, tts_python: str) -> None:
    """
    Generate WAV for all pages in a chapter in one subprocess call (model loads once).

    page_texts: {page_num: narration_text}
    Writes {page_num:04d}.wav to out_dir.
    """
    tasks = {str(k): v for k, v in page_texts.items() if v and v.strip()}
    if not tasks:
        return

    out_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json", delete=False,
                                     encoding="utf-8") as f:
        json.dump(tasks, f, ensure_ascii=False)
        tmp_path = Path(f.name)

    try:
        result = subprocess.run(
            [tts_python, str(_BATCH_WORKER), str(tmp_path), str(out_dir.resolve())],
            capture_output=True,
            timeout=600,
            cwd=str(_TTS_CWD),
        )
        if result.returncode != 0:
            print(f"  [tts] batch error:\n{result.stderr.decode()[-400:]}")
        else:
            for line in result.stdout.decode().splitlines():
                print(f"  [tts] {line}")
    except Exception as e:
        print(f"  [tts] error: {e}")
    finally:
        tmp_path.unlink(missing_ok=True)


def page_text(blocks: list[dict]) -> str:
    """Concatenate all translated blocks into one narration string."""
    parts = [b.get("translation") or b.get("text", "") for b in blocks]
    return " ".join(p.strip() for p in parts if p.strip())
