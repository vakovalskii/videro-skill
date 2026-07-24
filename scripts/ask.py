#!/usr/bin/env python3
"""ask — вопрос к обработанному видео по его timeline.json.

Аналог кнопки «✨ Спросить» в плеере: отвечает по главам/сценам с таймкодами.

  export NEURALDEEP_API_KEY=sk-...
  python scripts/ask.py videro-out/lecture/timeline.json "о чём вторая половина?"
"""
from __future__ import annotations

import json
import os
import sys

import requests

BASE = os.getenv("ND_BASE_URL", "https://api.neuraldeep.ru/v1").rstrip("/")
KEY = os.getenv("NEURALDEEP_API_KEY", "")
MODEL = os.getenv("NDT_ASK_MODEL", os.getenv("NDT_VISION_MODEL", "qwen3.6-fp8"))


def fmt_ts(sec: float) -> str:
    m, s = divmod(int(sec), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:d}:{s:02d}"


def main():
    if len(sys.argv) < 3 or not KEY:
        sys.exit("usage: NEURALDEEP_API_KEY=sk-... python ask.py <timeline.json> <вопрос…>")
    with open(sys.argv[1], encoding="utf-8") as f:
        tl = json.load(f)
    question = " ".join(sys.argv[2:])

    ctx = [f"[{fmt_ts(s['start'])}] {s.get('caption', '')}: {s.get('action', '')}"
           for s in tl.get("scenes", [])]
    context = "\n".join(ctx)[:12000]

    sys_prompt = ("Ты помощник по видео. Отвечай строго по контексту (главы с таймкодами). "
                  "Указывай таймкоды в формате [M:SS], когда ссылаешься на момент.")
    r = requests.post(f"{BASE}/chat/completions",
                      headers={"Authorization": f"Bearer {KEY}", "Content-Type": "application/json"},
                      json={"model": MODEL, "temperature": 0.3,
                            "chat_template_kwargs": {"enable_thinking": False},
                            "messages": [{"role": "system", "content": sys_prompt},
                                         {"role": "user",
                                          "content": f"Видео (главы):\n{context}\n\nВопрос: {question}"}]},
                      timeout=120)
    r.raise_for_status()
    print(r.json()["choices"][0]["message"]["content"])


if __name__ == "__main__":
    main()
