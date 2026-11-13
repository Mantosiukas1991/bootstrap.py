"""Fetch source videos (URL via yt-dlp, or a local file)."""

from __future__ import annotations

import hashlib
import shutil
from pathlib import Path


def source_id(source: str) -> str:
    return hashlib.sha1(source.encode()).hexdigest()[:12]


def fetch(source: str, work_dir: Path) -> Path:
    """Return a local mp4 path for `source`, downloading once and caching in work_dir."""
    work_dir.mkdir(parents=True, exist_ok=True)
    local = Path(source).expanduser()
    if local.exists():
        return local.resolve()

    sid = source_id(source)
    cached = sorted(work_dir.glob(f"{sid}.*"))
    cached = [p for p in cached if p.suffix in {".mp4", ".mkv", ".webm", ".mov"}]
    if cached:
        return cached[0]

    import yt_dlp  # imported lazily so local-file runs don't need it

    opts = {
        "outtmpl": str(work_dir / f"{sid}.%(ext)s"),
        "format": "bv*[height<=1080]+ba/b[height<=1080]/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "noprogress": True,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        info = ydl.extract_info(source, download=True)
        path = Path(ydl.prepare_filename(info)).with_suffix(".mp4")
    if not path.exists():
        # yt-dlp may keep the original container when no merge happened
        found = [p for p in work_dir.glob(f"{sid}.*") if p.suffix != ".part"]
        if not found:
            raise RuntimeError(f"download produced no file for {source}")
        path = found[0]
    return path


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} not found on PATH; install ffmpeg first")
