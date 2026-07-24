# Videro — AI-разметка видео одной командой

Превращает видеофайл в:

- **`timeline.json`** — сцены/главы, хайлайты, транскрипт с таймкодами;
- **`subtitles.srt`** — субтитры;
- **`hls/master.m3u8`** — адаптивный HLS-стрим для веб-плеера;
- **`poster.jpg`** — обложка.

Вся «магия» (распознавание сцен, оглавление, хайлайты, коррекция терминов) считается
на инфраструктуре **NeuralDeep Hub** по твоему `sk-`ключу через OpenAI-совместимый API.
Локально нужен только `ffmpeg`. Ни базы, ни S3, ни серверов — чистый CLI.

> Это open-source выжимка пайплайна из «Мастерской» на hub.neuraldeep.ru — того, что
> размечает видео-уроки. Здесь — только препроцессор, чтобы любой мог гонять то же самое
> у себя, оплачивая модели на Hub.

## Как это работает

```
видео.mp4
  ├─ ffmpeg  → HLS (720/480/360) + poster.jpg          [локально]
  └─ ffmpeg scene-detect → окна сцен
       ├─ 3 кадра/сцена → vision-модель (qwen3.6-fp8)  → caption/topic/highlight  [Hub /v1]
       ├─ аудио → SpeechCore (whisperX) → транскрипт+диаризация                    [Hub]
       └─ коррекция субтитров по OCR-контексту          → subtitles.srt            [Hub /v1]
  → timeline.json
```

---

## 1. Получить токен

1. Зарегистрируйся на **https://hub.neuraldeep.ru** (Yandex-вход или email/пароль).
2. Открой доступ к моделям — нужен доступ к **vision-модели `qwen3.6-fp8`**:
   - **Кошелёк (PAYG):** пополни баланс и включи тумблер PAYG — платишь по токенам, доступен весь каталог; **или**
   - **Подписка** (starter/pro) — если тариф включает нужные модели.
3. В кабинете → **Ключи** создай API-ключ `sk-...`.
4. (Опционально) для субтитров/транскрипта нужен доступ к SpeechCore
   (**https://speechcore.neuraldeep.ru**, тот же Hub-аккаунт) — используй его `sk-`токен как `SPEECHCORE_TOKEN`.

```bash
export NEURALDEEP_API_KEY=sk-...        # обязательно
export SPEECHCORE_TOKEN=sk-...          # опционально (субтитры/транскрипт)
```

> Endpoint по умолчанию — `https://api.neuraldeep.ru/v1`. API OpenAI-совместимый,
> так что при желании можно нацелить на свой шлюз через `ND_BASE_URL`
> (нужна мультимодальная модель с `response_format: json_schema`).

## 2. Установка

```bash
git clone <этот-репозиторий> && cd videro-skill
pip install -r requirements.txt          # только requests
# ffmpeg:  macOS → brew install ffmpeg | Ubuntu → sudo apt install -y ffmpeg
ffmpeg -version && ffprobe -version      # проверка
```

## 3. Запуск

```bash
python scripts/videro.py путь/к/видео.mp4
# → videro-out/видео/{timeline.json, subtitles.srt, poster.jpg, hls/master.m3u8}
```

Флаги:

| флаг | смысл |
|---|---|
| `--out DIR` | папка вывода (default `./videro-out/<имя>`) |
| `--profile user\|full` | `user` = 720/480/360 (деф), `full` = +1080p |
| `--lang ru\|en\|…` | язык речи для ASR |
| `--no-hls` | только разметка (без транскода) — быстрее и дешевле |

Спросить видео (Q&A по `timeline.json`):

```bash
python scripts/ask.py videro-out/видео/timeline.json "сделай оглавление с таймкодами"
```

Пакетно:

```bash
for f in ./videos/*.mp4; do python scripts/videro.py "$f" --out "build/$(basename "${f%.*}")"; done
```

## Использование агентом (Claude Code / Codex)

Репозиторий — самодостаточный **скилл**. Кинь агенту ссылку на репо:

- **Claude Code** читает `SKILL.md` (frontmatter + инструкции) и сам доводит пользователя
  от «нет токена» до готового `timeline.json`.
- **Codex** и прочие — читают `AGENTS.md` (тот же контракт).

Скрипты в `scripts/` можно просто скопировать в проект пользователя и применить — они ни от чего в этом репо не зависят, кроме `requests` и `ffmpeg`.

## Формат `timeline.json`

```jsonc
{
  "duration_sec": 612.3,
  "model": "qwen3.6-fp8",
  "scenes": [
    {
      "start": 0.0, "end": 24.5, "n_frames": 3,
      "screen_type": "slide",          // slide/code/terminal/demo/browser/talking_head/other
      "caption": "Введение и цели урока",
      "action": "Спикер представляет план и ключевые темы.",
      "on_screen_text": "Agenda: setup, demo, Q&A",
      "topic": "Введение",             // группируй по topic → главы
      "highlight": false,
      "highlight_reason": "",
      "importance": 2                  // 1–5
    }
  ],
  "highlights": [ { "start": 120.0, "end": 156.0, "caption": "...", "reason": "...", "importance": 5 } ],
  "transcript": [ { "start": 0.4, "end": 3.1, "text": "...", "speaker": "SPEAKER_00" } ],
  "srt": "1\n00:00:00,400 --> 00:00:03,100\n...\n"
}
```

## Стоимость и производительность

- Разметка идёт **кадрами** (не video-native): часовое видео → десятки vision-запросов.
  Ориентируйся на баланс кошелька Hub.
- `--no-hls` пропускает транскод (экономит CPU/время, если HLS не нужен).
- Есть GPU с NVENC? — `export HLS_VCODEC=h264_nvenc HLS_PRESET=p4` (код не меняется).

## Тюнинг (env)

Полный список — в `.env.template`. Частое: `NDT_VISION_MODEL`, `FRAMES_PER_SCENE`,
`MAX_SCENE_SEC`, `ANALYZE_WORKERS`, `ND_BASE_URL`.

## Ограничения

- Разметка сцен требует **мультимодальной** модели с `image_url` + строгим `json_schema`
  (дефолтный `qwen3.6-fp8` умеет; чисто текстовая модель — нет).
- Субтитры/транскрипт требуют **ASR-с-таймкодами** (SpeechCore). Без `SPEECHCORE_TOKEN`
  шаг тихо пропускается, `transcript`/`srt` будут пустыми.
- `FIX_PROMPT` в `scripts/analyze.py` заточен под IT/AI-контент (чинит англ. тех-термины) —
  под другой домен подправь промпт.

## Лицензия

MIT — см. `LICENSE`.
