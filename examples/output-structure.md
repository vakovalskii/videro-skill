# Структура вывода

После `python scripts/videro.py lecture.mp4` появляется:

```
videro-out/lecture/
├── timeline.json        # сцены/главы + хайлайты + транскрипт (схема — в README)
├── subtitles.srt        # субтитры (если задан SPEECHCORE_TOKEN)
├── poster.jpg           # обложка (кадр ~3с)
└── hls/
    ├── master.m3u8      # плейлист-мастер (адаптивное качество)
    ├── v0/index.m3u8 + seg*.ts   # 720p
    ├── v1/index.m3u8 + seg*.ts   # 480p
    └── v2/index.m3u8 + seg*.ts   # 360p
```

## Быстрый локальный просмотр HLS

```bash
# VLC
vlc videro-out/lecture/hls/master.m3u8
# ffplay
ffplay videro-out/lecture/hls/master.m3u8
```

Для веб-плеера подойдёт [hls.js](https://github.com/video-dev/hls.js/); в Safari `.m3u8`
играется нативно через `<video src="…/master.m3u8">`.

## Собрать главы из timeline.json

Сцены с одинаковым `topic` идут подряд → границы глав. Мини-пример на Python:

```python
import json
tl = json.load(open("videro-out/lecture/timeline.json", encoding="utf-8"))
chapters, prev = [], None
for s in tl["scenes"]:
    if s["topic"] != prev:
        chapters.append({"start": s["start"], "title": s["topic"] or s["caption"]})
        prev = s["topic"]
for c in chapters:
    m, sec = divmod(int(c["start"]), 60)
    print(f"{m:02d}:{sec:02d}  {c['title']}")
```

Или просто: `python scripts/ask.py videro-out/lecture/timeline.json "сделай оглавление с таймкодами"`.
