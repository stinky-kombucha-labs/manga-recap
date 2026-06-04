#!/usr/bin/env python3
"""Lapa LLM translation worker — runs INSIDE the Lapa venv, not the main one.

Invoked as a subprocess by pipeline/translate.py (same pattern as the TTS
worker): the main pipeline never imports llama_cpp, it just hands this script a
job file and reads the result file back.

    <lapa_python> lapa_worker.py <job.json> <out.json>

job.json:  {"items": [{"id": "1|0", "text": "..."}], "model": {...}, "gen": {...}}
out.json:  {"1|0": "український переклад", ...}

The model loads ONCE, then every block is translated with the shortest possible
prompt. Lapa (Gemma-3 based) degrades when the system prompt is stuffed with
instructions/glossaries, so we use the bare "Переклади українською:" form that
the sister Lapa project found most reliable.
"""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path

from llama_cpp import Llama

# Strip a leading "Переклад:" / "Translation:" label the model sometimes adds.
_LABEL_RE = re.compile(r"^(Переклад|Українською|Translation)\s*:\s*", re.IGNORECASE)


def _clean(raw: str) -> str:
    raw = (raw or "").strip()
    raw = _LABEL_RE.sub("", raw).strip()
    # On the rare multi-line answer, keep the longest non-empty line (the actual
    # translation, not a stray note).
    if "\n" in raw:
        parts = [p.strip() for p in raw.splitlines() if p.strip()]
        if parts:
            raw = max(parts, key=len)
    return raw.strip()


def _estimate_tokens(text: str) -> int:
    return int(len(text) / 2.5) + 10


def main() -> None:
    job = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    out_path = Path(sys.argv[2])

    model_cfg = job.get("model", {})
    gen = job.get("gen", {})
    items = job.get("items", [])

    load_kwargs = {
        "model_path": model_cfg["path"],
        "n_gpu_layers": model_cfg.get("n_gpu_layers", -1),
        "n_ctx": model_cfg.get("n_ctx", 4096),
        "verbose": False,
    }
    if model_cfg.get("flash_attn") is not None:
        load_kwargs["flash_attn"] = bool(model_cfg["flash_attn"])

    print(f"[lapa] loading {Path(model_cfg['path']).name} ...", file=sys.stderr, flush=True)
    llm = Llama(**load_kwargs)
    print(f"[lapa] model ready — {len(items)} block(s) to translate", file=sys.stderr, flush=True)

    stop = gen.get("stop", ["<eos>", "<end_of_turn>"])
    # Prompt stays minimal (Lapa degrades on long prompts). A "repair" pass can pass a
    # short corrective template via job["prompt_template"] with a {text} placeholder.
    template = job.get("prompt_template") or "Переклади українською:\n\n{text}"
    results: dict[str, str] = {}
    total = len(items)
    for i, item in enumerate(items, 1):
        key = str(item["id"])
        text = (item.get("text") or "").strip()
        if not text:
            results[key] = ""
            continue

        messages = [{"role": "user", "content": template.replace("{text}", text)}]
        params = {
            "temperature": gen.get("temperature", 0.1),
            "top_p": gen.get("top_p", 0.9),
            "top_k": gen.get("top_k", 25),
            "repeat_penalty": gen.get("repeat_penalty", 1.0),
            "max_tokens": max(128, _estimate_tokens(text) * 2),
            "stop": stop,
        }
        resp = llm.create_chat_completion(messages=messages, **params)
        translation = _clean(resp["choices"][0]["message"]["content"])
        results[key] = translation
        preview = translation.replace("\n", " ")
        if len(preview) > 60:
            preview = preview[:57] + "..."
        print(f"[lapa] [{i}/{total}] {preview}", file=sys.stderr, flush=True)

    out_path.write_text(json.dumps(results, ensure_ascii=False), encoding="utf-8")
    print(f"[lapa] done -> {out_path}", file=sys.stderr, flush=True)


if __name__ == "__main__":
    main()
