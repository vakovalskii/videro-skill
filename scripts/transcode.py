"""HLS-транскод исходника в адаптивные рендишены + постер (чистый ffmpeg).

profile:
  user = 3 качества (720/480/360) — дефолт, экономит CPU/сторедж
  full = 4 качества (+1080)

На CPU-боксе кодек = libx264. На GPU (NVENC) — выставить HLS_VCODEC=h264_nvenc
+ HLS_PRESET=p4, контракт не меняется.
Выход: <out>/hls/{master.m3u8, v{0..n}/index.m3u8+seg*.ts} + <out>/poster.jpg
"""
from __future__ import annotations

import os
import subprocess

VCODEC = os.getenv("HLS_VCODEC", "libx264")     # h264_nvenc на GPU-боксе
PRESET = os.getenv("HLS_PRESET", "veryfast")     # nvenc → p4/p5; videotoolbox → пусто
HLS_TIME = os.getenv("HLS_TIME", "6")

# (ширина, высота, битрейт) — сверху вниз по качеству
_R1080 = (1920, 1080, "5000k")
_R720 = (1280, 720, "2400k")
_R480 = (854, 480, "1100k")
_R360 = (640, 360, "600k")
PROFILES = {"full": [_R1080, _R720, _R480, _R360], "user": [_R720, _R480, _R360]}


def _renditions(profile: str):
    return PROFILES.get(profile, PROFILES["user"])


def ffprobe_duration(path: str) -> float:
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration",
         "-of", "default=nk=1:nw=1", path], capture_output=True, text=True, check=True)
    return float(out.stdout.strip() or 0)


def transcode(src: str, out_dir: str, profile: str = "user", progress_cb=None) -> None:
    """Один ffmpeg-проход: split→scale→encode→HLS master+варианты."""
    rends = _renditions(profile)
    n = len(rends)
    hls = os.path.join(out_dir, "hls")
    for i in range(n):
        os.makedirs(os.path.join(hls, f"v{i}"), exist_ok=True)

    split = "".join(f"[{chr(97+i)}]" for i in range(n))        # [a][b][c]
    fc = [f"[0:v]split={n}{split}"]
    for i, (w, h, _b) in enumerate(rends):
        fc.append(f"[{chr(97+i)}]scale=w={w}:h={h}[v{i}o]")
    filter_complex = ";".join(fc)

    cmd = ["ffmpeg", "-y", "-hide_banner", "-i", src, "-filter_complex", filter_complex]
    for i, (_w, _h, b) in enumerate(rends):
        cmd += ["-map", f"[v{i}o]", f"-c:v:{i}", VCODEC]
        if PRESET:                       # libx264/nvenc принимают -preset; videotoolbox — нет (HLS_PRESET= пусто)
            cmd += ["-preset", PRESET]
        cmd += [f"-b:v:{i}", b, f"-maxrate:v:{i}", b, f"-bufsize:v:{i}", b,
                "-g", "48", "-keyint_min", "48", "-sc_threshold", "0", f"-tag:v:{i}", "avc1"]
    for _ in range(n):                                          # аудио на каждый вариант
        cmd += ["-map", "a:0?"]
    cmd += ["-c:a", "aac", "-b:a", "128k", "-ac", "2",
            "-f", "hls", "-hls_time", HLS_TIME, "-hls_playlist_type", "vod",
            "-hls_flags", "independent_segments",
            "-hls_segment_filename", os.path.join(hls, "v%v", "seg%04d.ts"),
            "-master_pl_name", "master.m3u8",
            "-var_stream_map", " ".join(f"v:{i},a:{i}" for i in range(n)),
            os.path.join(hls, "v%v", "index.m3u8")]
    subprocess.run(cmd, check=True)


def make_poster(src: str, out_dir: str, at_sec: float = 3.0) -> str:
    dst = os.path.join(out_dir, "poster.jpg")
    subprocess.run(["ffmpeg", "-y", "-ss", f"{at_sec:.1f}", "-i", src, "-frames:v", "1",
                    "-vf", "scale=-2:720", "-q:v", "3", dst], check=True,
                   capture_output=True)
    return dst
