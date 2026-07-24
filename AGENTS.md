# AGENTS.md — Videro (для Codex CLI и других агентов)

Это репозиторий-**скилл**: он превращает видеофайл в AI-разметку
(главы/сцены + хайлайты + транскрипт + субтитры + HLS) поверх OpenAI-совместимого
API NeuralDeep Hub. Инструкции для агента — в **`SKILL.md`** (тот же контракт, что и
для Claude Code). Прочитай его целиком перед действиями.

## TL;DR для агента
1. Убедись, что есть `ffmpeg`/`ffprobe` и `pip install -r requirements.txt`.
2. Убедись, что задан `NEURALDEEP_API_KEY` (как получить — `README.md`, раздел «1. Получить токен»).
3. Запусти препроцессор из `scripts/`:
   ```bash
   python scripts/videro.py <видео> --out <dir>
   ```
4. Результат — в `<dir>/{timeline.json, subtitles.srt, poster.jpg, hls/master.m3u8}`.
   Для текстового оглавления/ответов по видео — `scripts/ask.py <timeline.json> "<вопрос>"`.

Ничего не деплоит и не требует БД/S3. Всё, что нужно, — локальный ffmpeg + sk-ключ.
Скрипты в `scripts/` самодостаточны: их можно копировать в проект пользователя как есть.
