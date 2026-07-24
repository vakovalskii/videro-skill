"""Видео → timeline JSON (сцены + транскрипт + субтитры) через OpenAI-совместимый API.

Пайплайн (без нативного video_url — многие шлюзы режут video-часть, режем кадры сами):
  1. ffmpeg scene-detect → границы сцен по таймкодам (+ split длинных / merge коротких)
  2. аудио → SpeechCore (whisperX) → транскрипт с таймстемпами + диаризация  (опционально)
  3. на сцену: 3 кадра (image_url) + речь окна → vision-модель, structured output
  4. контекстная коррекция субтитров (OCR-якорь чинит англ. тех-термины)

Требует vision-модель с image_url + response_format json_schema (напр. qwen3.6-fp8 на
api.neuraldeep.ru). Субтитры — только если задан SPEECHCORE_TOKEN, иначе шаг пропускается.

Env: NEURALDEEP_API_KEY, ND_BASE_URL, NDT_VISION_MODEL, SPEECHCORE_API, SPEECHCORE_TOKEN.
"""
from __future__ import annotations

import base64
import concurrent.futures as cf
import json
import os
import re
import subprocess
import tempfile
import time

import requests

NDT_BASE = (os.getenv("ND_BASE_URL", "https://api.neuraldeep.ru/v1")).rstrip("/")
NDT_KEY = os.getenv("NEURALDEEP_API_KEY", "")
VISION_MODEL = os.getenv("NDT_VISION_MODEL", "qwen3.6-fp8")
FIX_MODEL = os.getenv("NDT_FIX_MODEL", VISION_MODEL)
SPEECHCORE_API = (os.getenv("SPEECHCORE_API", "https://speechcore.neuraldeep.ru/api")).rstrip("/")
SPEECHCORE_TOKEN = os.getenv("SPEECHCORE_TOKEN", "")
WORKERS = int(os.getenv("ANALYZE_WORKERS", "4"))
MAX_SCENE_SEC = float(os.getenv("MAX_SCENE_SEC", "30"))
FRAMES_PER_SCENE = int(os.getenv("FRAMES_PER_SCENE", "3"))


def _log(cb, stage: str, pct: int):
    if cb:
        try:
            cb(stage, pct)
        except Exception:
            pass


# ── ffmpeg ────────────────────────────────────────────────────────────────
def detect_scene_cuts(path: str, threshold: float = 0.3) -> list[float]:
    proc = subprocess.run(
        ["ffmpeg", "-hide_banner", "-nostats", "-i", path, "-filter:v",
         f"scale=-2:320,select='gt(scene,{threshold})',showinfo", "-an", "-f", "null", "-"],
        capture_output=True, text=True)
    return sorted({round(float(m.group(1)), 2)
                   for m in re.finditer(r"pts_time:([0-9.]+)", proc.stderr)})


def build_scenes(duration: float, cuts: list[float], max_sec: float,
                 min_sec: float = 2.0) -> list[tuple[float, float]]:
    marks = sorted({0.0, *[c for c in cuts if 0 < c < duration], duration})
    scenes: list[tuple[float, float]] = []
    for a, b in zip(marks, marks[1:]):
        if b - a < min_sec and scenes:
            scenes[-1] = (scenes[-1][0], b)
            continue
        t = a
        while b - t > max_sec:
            scenes.append((t, t + max_sec)); t += max_sec
        if b - t < min_sec and scenes:        # хвост короче min_sec — приклеиваем к предыдущей
            scenes[-1] = (scenes[-1][0], b)
        else:
            scenes.append((t, b))
    return scenes


def extract_frames(path: str, start: float, end: float, n: int, height: int = 720) -> list[bytes]:
    if end <= start:
        end = start + 0.1
    frames = []
    for i in range(n):
        t = start + (end - start) * (i + 0.5) / n
        r = subprocess.run(
            ["ffmpeg", "-ss", f"{t:.2f}", "-i", path, "-frames:v", "1",
             "-vf", f"scale=-2:{height}", "-q:v", "3", "-f", "image2", "-"],
            capture_output=True)
        if r.returncode == 0 and r.stdout:
            frames.append(r.stdout)
    return frames


def extract_audio(path: str, dst: str) -> bool:
    r = subprocess.run(
        ["ffmpeg", "-y", "-i", path, "-vn", "-ac", "1", "-ar", "16000",
         "-c:a", "libopus", "-b:a", "24k", dst], capture_output=True)
    return r.returncode == 0 and os.path.exists(dst) and os.path.getsize(dst) > 0


# ── SpeechCore (опционально) ──────────────────────────────────────────────────
def transcribe(audio_path: str, lang: str) -> list[dict]:
    if not SPEECHCORE_TOKEN:
        return []
    h = {"Authorization": f"Bearer {SPEECHCORE_TOKEN}"}
    with open(audio_path, "rb") as f:
        r = requests.post(f"{SPEECHCORE_API}/upload", headers=h,
                          files={"file": f}, params={"language": lang, "diarize": "true"})
    r.raise_for_status()
    task_id = r.json()["task_id"]
    while True:
        s = requests.get(f"{SPEECHCORE_API}/transcriptions/{task_id}/status", headers=h).json()
        if s.get("status") in ("completed", "failed"):
            break
        time.sleep(5)
    if s.get("status") == "failed":
        return []
    res = requests.get(f"{SPEECHCORE_API}/transcriptions/{task_id}", headers=h).json()
    return [{"start": float(seg.get("start", 0) or 0), "end": float(seg.get("end", 0) or 0),
             "text": (seg.get("text") or "").strip(), "speaker": seg.get("speaker")}
            for seg in res.get("segments", [])]


def transcript_in_window(segs: list[dict], start: float, end: float) -> str:
    lines = []
    for s in segs:
        if s["end"] > start and s["start"] < end and s["text"]:
            spk = f"[{s['speaker']}] " if s.get("speaker") else ""
            lines.append(f"{spk}{s['text']}")
    return "\n".join(lines)


# ── vision (structured output) ─────────────────────────────────────────────────
SCENE_SCHEMA = {
    "type": "json_schema",
    "json_schema": {"name": "SceneAnalysis", "strict": True, "schema": {
        "type": "object", "additionalProperties": False, "properties": {
            "screen_type": {"type": "string"}, "caption": {"type": "string"},
            "action": {"type": "string"}, "on_screen_text": {"type": "string"},
            "topic": {"type": "string"}, "highlight": {"type": "boolean"},
            "highlight_reason": {"type": "string"}, "importance": {"type": "integer"}},
        "required": ["screen_type", "caption", "action", "on_screen_text",
                     "topic", "highlight", "highlight_reason", "importance"]}}}
_EMPTY = {"screen_type": "", "caption": "", "action": "", "on_screen_text": "",
          "topic": "", "highlight": False, "highlight_reason": "", "importance": 0}


def fmt_ts(sec: float) -> str:
    h, rem = divmod(int(sec), 3600); m, s = divmod(rem, 60)
    return f"{h:02d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def describe_scene(frames: list[bytes], speech: str, start: float, end: float) -> dict:
    if not frames:
        return dict(_EMPTY)
    hint = ("Ты аналитик видео для монтажёра. По кадрам и речи фрагмента заполни поля строго по схеме:\n"
            "- screen_type: slide/code/terminal/demo/browser/talking_head/other;\n"
            "- caption: КОРОТКИЙ заголовок 4-8 слов, НЕ копируй текст с экрана;\n"
            "- action: что происходит, 1-2 предложения;\n"
            "- on_screen_text: ключевой текст с экрана (OCR), до ~400 символов;\n"
            "- topic: тема для группировки в главы;\n"
            "- highlight + highlight_reason + importance(1-5).")
    content = [{"type": "text", "text": f"{hint}\n\nТаймкод: {fmt_ts(start)}–{fmt_ts(end)}." +
                (f"\n\nРечь:\n{speech}" if speech else "\n\n(речь отсутствует)")}]
    for fr in frames:
        content.append({"type": "image_url", "image_url": {
            "url": f"data:image/jpeg;base64,{base64.b64encode(fr).decode()}"}})
    r = requests.post(f"{NDT_BASE}/chat/completions",
                      headers={"Authorization": f"Bearer {NDT_KEY}", "Content-Type": "application/json"},
                      json={"model": VISION_MODEL, "max_tokens": 1600, "temperature": 0.3,
                            "chat_template_kwargs": {"enable_thinking": False},
                            "response_format": SCENE_SCHEMA,
                            "messages": [{"role": "user", "content": content}]}, timeout=180)
    r.raise_for_status()
    txt = (r.json()["choices"][0]["message"].get("content") or "").strip()
    try:
        return {**_EMPTY, **json.loads(txt)}
    except Exception:
        return {**_EMPTY, "action": txt[:500]}


# ── коррекция субтитров (OCR-якорь) ──────────────────────────────────────────
_NUM = re.compile(r"^\s*\d+[.)]\s*")
FIX_PROMPT = (
    "Ты корректор субтитров. Ниже пронумерованные ASR-сегменты русской речи из видеоурока "
    "про разработку и AI. ASR исказил английские тех-термины. Исправь ТОЛЬКО ошибки "
    "распознавания и терминологию по OCR-тексту с экрана. Правила:\n"
    "- сохрани смысл и разговорный стиль;\n"
    "- НЕ добавляй/не удаляй/не объединяй сегменты — верни РОВНО столько строк, сколько на входе;\n"
    "- правильный регистр названий (Claude Code, iTerm2, VS Code, http proxy, MCP, Cursor, Docker…);\n"
    "- уже верный сегмент — верни как есть.")
FIX_SCHEMA = {"type": "json_schema", "json_schema": {"name": "FixedSubs", "strict": True, "schema": {
    "type": "object", "additionalProperties": False,
    "properties": {"fixed": {"type": "array", "items": {"type": "string"}}}, "required": ["fixed"]}}}


def _ocr_window(scenes, t0, t1, cap=1200):
    seen, out = set(), []
    for s in scenes:
        if s.get("end", 0) > t0 and s.get("start", 0) < t1:
            txt = (s.get("on_screen_text") or "").strip()
            if txt and txt not in seen:
                seen.add(txt); out.append(txt)
    return "\n".join(out)[:cap]


def _fix_call(prompt_text: str, n: int, retries: int = 3):
    for a in range(retries):
        try:
            r = requests.post(f"{NDT_BASE}/chat/completions",
                              headers={"Authorization": f"Bearer {NDT_KEY}", "Content-Type": "application/json"},
                              json={"model": FIX_MODEL, "max_tokens": 4000, "temperature": 0.1,
                                    "chat_template_kwargs": {"enable_thinking": False},
                                    "response_format": FIX_SCHEMA,
                                    "messages": [{"role": "user", "content": prompt_text}]}, timeout=180)
            r.raise_for_status()
            fixed = json.loads(r.json()["choices"][0]["message"]["content"]).get("fixed", [])
            return [_NUM.sub("", x) for x in fixed] if len(fixed) == n else None
        except Exception:
            if a == retries - 1:
                return None


def fix_subtitles(transcript: list[dict], scenes: list[dict], batch: int = 20):
    if not transcript:
        return
    sc = sorted(scenes, key=lambda s: s.get("start", 0))
    batches = [(i, transcript[i:i + batch]) for i in range(0, len(transcript), batch)]

    def do(b):
        i, segs = b
        ocr = _ocr_window(sc, segs[0]["start"], segs[-1]["end"])
        lines = "\n".join(f"{k+1}. {s['text']}" for k, s in enumerate(segs))
        fixed = _fix_call(f"{FIX_PROMPT}\n\n=== OCR-контекст ===\n{ocr or '(нет)'}\n\n"
                          f"=== Сегменты ({len(segs)}) ===\n{lines}", len(segs))
        return i, ([s["text"] for s in segs] if fixed is None else fixed)

    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for i, fixed in ex.map(do, batches):
            for k, newtext in enumerate(fixed):
                seg = transcript[i + k]
                if newtext != seg["text"]:
                    seg["text_raw"] = seg["text"]; seg["text"] = newtext


def build_srt(segs: list[dict]) -> str:
    def ts(sec):
        ms = int((sec - int(sec)) * 1000); h, r = divmod(int(sec), 3600); m, s = divmod(r, 60)
        return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"
    return "\n".join(f"{k+1}\n{ts(s['start'])} --> {ts(s['end'])}\n{s['text']}\n"
                     for k, s in enumerate(segs) if s.get("text"))


# ── оркестрация ──────────────────────────────────────────────────────────────
def analyze(video_path: str, duration: float, lang: str = "ru", progress_cb=None) -> dict:
    if not NDT_KEY:
        raise RuntimeError("нет NEURALDEEP_API_KEY")

    _log(progress_cb, "аудио → транскрипт (SpeechCore)", 15)
    transcript: list[dict] = []
    with tempfile.NamedTemporaryFile(suffix=".ogg", delete=False) as tf:
        apath = tf.name
    try:
        if extract_audio(video_path, apath):
            transcript = transcribe(apath, lang)
    finally:
        os.path.exists(apath) and os.unlink(apath)

    _log(progress_cb, "детекция сцен", 30)
    cuts = detect_scene_cuts(video_path)
    windows = build_scenes(duration, cuts, MAX_SCENE_SEC)

    _log(progress_cb, f"разметка {len(windows)} сцен (vision)", 35)

    def one(idx_ab):
        idx, (a, b) = idx_ab
        frames = extract_frames(video_path, a, b, FRAMES_PER_SCENE)
        try:
            sc = describe_scene(frames, transcript_in_window(transcript, a, b), a, b)
        except Exception as e:
            sc = {**_EMPTY, "action": f"[error: {e}]"}
        return idx, {"start": a, "end": b, "n_frames": len(frames), **sc}

    res: list = [None] * len(windows)
    done = 0
    with cf.ThreadPoolExecutor(max_workers=WORKERS) as ex:
        for idx, row in ex.map(one, list(enumerate(windows))):
            res[idx] = row
            done += 1
            if len(windows):
                _log(progress_cb, f"сцены {done}/{len(windows)}", 35 + int(45 * done / len(windows)))
    scenes = [s for s in res if s]

    _log(progress_cb, "коррекция субтитров", 82)
    fix_subtitles(transcript, scenes)

    highlights = sorted(
        [{"start": s["start"], "end": s["end"], "caption": s["caption"],
          "reason": s["highlight_reason"], "importance": s["importance"]}
         for s in scenes if s.get("highlight")],
        key=lambda x: x["importance"], reverse=True)

    return {"duration_sec": round(duration, 2), "model": VISION_MODEL,
            "scenes": scenes, "highlights": highlights,
            "transcript": transcript, "srt": build_srt(transcript)}
