"""Drive the Lapa LLM translation worker from the main pipeline.

The main venv (Python 3.10, torch) does NOT have llama_cpp installed and does
not need it. Translation runs in the Lapa project's own venv via a subprocess,
exactly like TTS runs in its own venv. We pass a job file in and read a result
file out; the worker's progress is streamed to this console via stderr.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
from pathlib import Path

_WORKER = Path(__file__).resolve().parent / "lapa_worker.py"


def translate_texts(items: list[tuple[str, str]], translation_cfg: dict,
                    prompt_template: str | None = None) -> dict[str, str]:
    """Translate ``items`` (list of ``(key, english_text)``) to Ukrainian.

    Returns ``{key: ukrainian_text}``. Loads the GGUF model once for the whole
    batch. Raises if the worker fails so the caller can stop instead of writing
    empty translations. ``prompt_template`` (with a ``{text}`` placeholder) overrides
    the default minimal prompt — used by the repair pass for a corrective prompt.
    """
    if not items:
        return {}

    lapa_python = translation_cfg.get("lapa_python")
    model_path = translation_cfg.get("model_path")
    if not lapa_python or not Path(lapa_python).exists():
        raise FileNotFoundError(
            f"translation.lapa_python not found: {lapa_python!r}\n"
            f"Set it in config.json to the Lapa venv python (the venv that has llama_cpp)."
        )
    if not model_path or not Path(model_path).exists():
        raise FileNotFoundError(
            f"translation.model_path not found: {model_path!r}\n"
            f"Point it at a Lapa GGUF, e.g. .../models/lapa-v0.1.2-instruct-Q4_K_M.gguf"
        )

    job = {
        "items": [{"id": key, "text": text} for key, text in items],
        "prompt_template": prompt_template,
        "model": {
            "path": model_path,
            "n_gpu_layers": translation_cfg.get("n_gpu_layers", -1),
            "n_ctx": translation_cfg.get("n_ctx", 4096),
            "flash_attn": translation_cfg.get("flash_attn", True),
        },
        "gen": {
            "temperature": translation_cfg.get("temperature", 0.1),
            "top_p": translation_cfg.get("top_p", 0.9),
            "top_k": translation_cfg.get("top_k", 25),
            "repeat_penalty": translation_cfg.get("repeat_penalty", 1.0),
            "stop": translation_cfg.get("stop", ["<eos>", "<end_of_turn>"]),
        },
    }

    with tempfile.TemporaryDirectory() as tmp:
        job_path = Path(tmp) / "job.json"
        out_path = Path(tmp) / "out.json"
        job_path.write_text(json.dumps(job, ensure_ascii=False), encoding="utf-8")

        cmd = [str(lapa_python), str(_WORKER), str(job_path), str(out_path)]
        # stderr (model load + per-block progress) streams straight to the console;
        # we only read the JSON result from out_path.
        result = subprocess.run(cmd)
        if result.returncode != 0:
            raise RuntimeError(f"Lapa worker failed (exit {result.returncode}).")
        if not out_path.exists():
            raise RuntimeError("Lapa worker produced no output file.")
        return json.loads(out_path.read_text(encoding="utf-8"))
