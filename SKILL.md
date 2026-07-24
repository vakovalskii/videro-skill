---
name: videro-video-markup
description: >-
  Превратить видеофайл в структурированную AI-разметку — главы/сцены, хайлайты,
  транскрипт с таймкодами, субтитры (.srt) и адаптивный HLS-стрим. Используй,
  когда пользователь просит «разметить видео», «сделать главы/тайм-коды»,
  «субтитры к видео», «нарезать хайлайты», «обработать лекцию/урок/запись
  экрана» или подготовить видео к плееру. Работает поверх OpenAI-совместимого
  API NeuralDeep Hub (api.neuraldeep.ru) по sk-ключу пользователя.
---

# Videro — AI-разметка видео

Локальный препроцессор: `видео → timeline.json (главы/сцены + хайлайты + транскрипт) + subtitles.srt + HLS + poster`.
Всё считается по sk-ключу пользователя на NeuralDeep Hub. Локально нужен только `ffmpeg` и Python 3.10+.
Ни БД, ни S3, ни серверов — чистый CLI.

## Когда применять
Пользователь дал видео (или папку с видео) и хочет любую из: главы/оглавление по таймкодам,
субтитры, транскрипт, хайлайты/нарезку ключевых моментов, обложку, HLS для веб-плеера,
или «спросить видео» (Q&A по содержимому).

## Предпосылки (проверь и при отсутствии — доведи пользователя)
1. `ffmpeg` и `ffprobe` в PATH — `ffmpeg -version`. Если нет: macOS `brew install ffmpeg`,
   Debian/Ubuntu `sudo apt install -y ffmpeg`.
2. Python-зависимость: `pip install -r requirements.txt` (только `requests`).
3. **Токен `NEURALDEEP_API_KEY` (sk-...)** — как получить, см. `README.md` → «1. Получить токен».
   Кратко: регистрация на https://hub.neuraldeep.ru → пополнить кошелёк / оформить подписку
   (нужен доступ к vision-модели `qwen3.6-fp8`) → создать API-ключ. Экспортировать:
   `export NEURALDEEP_API_KEY=sk-...`
4. (Опционально) `SPEECHCORE_TOKEN` для субтитров/транскрипта. Без него разметка сцен/глав
   всё равно работает, но `transcript`/`subtitles.srt` будут пустыми.

## Как запускать (бери команды из scripts/ и применяй)
Основной прогон — один файл:
```bash
export NEURALDEEP_API_KEY=sk-...
export SPEECHCORE_TOKEN=sk-...        # опционально, для субтитров
python scripts/videro.py путь/к/видео.mp4
# → videro-out/видео/{timeline.json, subtitles.srt, poster.jpg, hls/master.m3u8}
```
Полезные флаги: `--out <dir>`, `--profile user|full` (full добавляет 1080p),
`--lang ru|en|…`, `--no-hls` (только разметка, без транскода — быстрее/дешевле).

Q&A по готовому видео:
```bash
python scripts/ask.py videro-out/видео/timeline.json "сделай оглавление с таймкодами"
```

Пакетно (папка видео):
```bash
for f in ./videos/*.mp4; do python scripts/videro.py "$f" --out "build/$(basename "${f%.*}")"; done
```

## Что отдать пользователю по итогу
- `timeline.json` — машинное представление (сцены с `start/end/caption/topic/importance/highlight`,
  массив `highlights`, `transcript`, `srt`). Из `scenes` сгруппируй по `topic` — получишь главы.
- `subtitles.srt` — готовые субтитры.
- `hls/master.m3u8` — можно проиграть в любом HLS-плеере (hls.js, Safari, VLC).
- При просьбе «оглавление/хайлайты текстом» — прогони `ask.py` или сам собери из `timeline.json`.

## Тюнинг (env, необязательно)
`NDT_VISION_MODEL` (модель разметки), `FRAMES_PER_SCENE` (кадров на сцену, деф 3),
`MAX_SCENE_SEC` (макс. длина сцены, деф 30), `ANALYZE_WORKERS` (параллелизм, деф 4),
`HLS_VCODEC=h264_nvenc`+`HLS_PRESET=p4` (если есть GPU-NVENC). Полный список — `.env.template`.

## Границы
- Разметка сцен требует **мультимодальной** модели (image+json_schema). Текстовая модель не подойдёт —
  дефолтный `qwen3.6-fp8` на Hub это умеет.
- Субтитры требуют ASR-с-таймкодами (SpeechCore). Без `SPEECHCORE_TOKEN` шаг тихо пропускается.
- Обработка идёт кадрами (не video-native): для часовой лекции это десятки vision-запросов — оценивай стоимость по кошельку Hub.
