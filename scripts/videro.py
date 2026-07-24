#!/usr/bin/env python3
"""videro — превратить локальное видео в структурированную разметку одним прогоном.

Вход:  путь к видеофайлу (mp4/mov/mkv/webm/m4v).
Выход (в --out, по умолчанию ./videro-out/<имя>):
  hls/master.m3u8 + варианты   — адаптивный HLS-стрим для плеера
  poster.jpg                   — обложка (кадр)
  timeline.json                — сцены/главы, хайлайты, транскрипт (схема — в README)
  subtitles.srt                — субтитры (если задан SPEECHCORE_TOKEN)

Всё считается по твоему sk-ключу на NeuralDeep Hub (OpenAI-совместимый /v1).
Никакой БД, S3 или очереди — только локальный ffmpeg и HTTP к api.neuraldeep.ru.
Разметку сцен делает vision-модель (по умолчанию qwen3.6-fp8).

Использование:
  export NEURALDEEP_API_KEY=sk-...
  python scripts/videro.py path/to/lecture.mp4
  python scripts/videro.py lecture.mp4 --out ./build --profile user --lang ru
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import time
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import analyze          # noqa: E402
import transcode        # noqa: E402


def _progress(stage: str, pct: int):
    print(f"  [{pct:3d}%] {stage}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="video → timeline.json + HLS + srt (NeuralDeep Hub)")
    ap.add_argument("video", help="путь к видеофайлу")
    ap.add_argument("--out", default=None, help="папка вывода (default ./videro-out/<имя>)")
    ap.add_argument("--profile", choices=["user", "full"], default="user",
                    help="user=3 качества (720/480/360), full=+1080")
    ap.add_argument("--lang", default="ru", help="язык речи для ASR (default ru)")
    ap.add_argument("--no-hls", action="store_true",
                    help="пропустить транскод (только timeline.json + srt)")
    args = ap.parse_args()

    if not os.getenv("NEURALDEEP_API_KEY"):
        sys.exit("нет NEURALDEEP_API_KEY — см. README, раздел «1. Получить токен»")
    src = os.path.abspath(args.video)
    if not os.path.isfile(src):
        sys.exit(f"файл не найден: {src}")

    out = args.out or os.path.join("videro-out", Path(src).stem)
    os.makedirs(out, exist_ok=True)
    t0 = time.time()

    print(f"▶ {src}\n  → {out}")
    duration = transcode.ffprobe_duration(src)
    print(f"  длительность: {duration:.1f}s")

    if not args.no_hls:
        print("• транскод → HLS")
        transcode.transcode(src, out, profile=args.profile)
        transcode.make_poster(src, out)

    print("• анализ (сцены + транскрипт + субтитры)")
    timeline = analyze.analyze(src, duration, lang=args.lang, progress_cb=_progress)

    with open(os.path.join(out, "timeline.json"), "w", encoding="utf-8") as f:
        json.dump(timeline, f, ensure_ascii=False, indent=2)
    if timeline.get("srt"):
        with open(os.path.join(out, "subtitles.srt"), "w", encoding="utf-8") as f:
            f.write(timeline["srt"])

    n_sc = len(timeline.get("scenes", []))
    n_hl = len(timeline.get("highlights", []))
    n_tr = len(timeline.get("transcript", []))
    print(f"\n✔ готово за {time.time() - t0:.0f}s: {n_sc} сцен, {n_hl} хайлайтов, "
          f"{n_tr} сегментов транскрипта")
    print(f"  timeline: {os.path.join(out, 'timeline.json')}")
    if timeline.get("srt"):
        print(f"  субтитры: {os.path.join(out, 'subtitles.srt')}")
    if not args.no_hls:
        print(f"  плеер:    {os.path.join(out, 'hls', 'master.m3u8')}")


if __name__ == "__main__":
    main()
