"""Get campaign media onto disk: videos to clip and assets such as logos.

Tries, in order: local path, Google Drive (gdown), yt-dlp (YouTube and ~1000 sites), then a headless browser
that clicks the share page's Download buttons (Frame.io, MediaSilo, Dropbox...). Results are cached per link.
"""

from __future__ import annotations

import hashlib
import json
import re
import shutil
import zipfile
from pathlib import Path

from .brief import unwrap_link

VIDEO_EXT = {".mp4", ".mov", ".m4v", ".mkv", ".webm", ".avi"}
IMAGE_EXT = {".png", ".jpg", ".jpeg", ".webp"}


def source_id(source: str) -> str:
    return hashlib.sha1(source.encode()).hexdigest()[:12]


def require_ffmpeg() -> None:
    for tool in ("ffmpeg", "ffprobe"):
        if shutil.which(tool) is None:
            raise SystemExit(f"{tool} not found on PATH; install ffmpeg first")


def _expand(files: list[Path], dest: Path) -> list[Path]:
    """Unzip archives, return media files."""
    out: list[Path] = []
    for f in files:
        if f.suffix.lower() == ".zip":
            target = dest / f.stem
            with zipfile.ZipFile(f) as z:
                for member in z.infolist():
                    # guard against path traversal in archive names
                    name = Path(member.filename)
                    if member.is_dir() or name.is_absolute() or ".." in name.parts or name.name.startswith("."):
                        continue
                    path = target / name
                    path.parent.mkdir(parents=True, exist_ok=True)
                    with z.open(member) as src, open(path, "wb") as dst:
                        shutil.copyfileobj(src, dst)
                    out.append(path)
        else:
            out.append(f)
    return [f for f in out if f.suffix.lower() in VIDEO_EXT | IMAGE_EXT]


def _gdrive(url: str, dest: Path) -> list[Path]:
    import gdown

    if "/folders/" in url:
        files = gdown.download_folder(url, output=str(dest), quiet=True) or []
        return [Path(f) for f in files]
    path = gdown.download(url, output=str(dest) + "/", quiet=True, fuzzy=True)
    return [Path(path)] if path else []


def _ytdlp(url: str, dest: Path) -> list[Path]:
    import yt_dlp

    opts = {
        "outtmpl": str(dest / "%(title).80s.%(ext)s"),
        "format": "bv*[height<=2160]+ba/b",
        "merge_output_format": "mp4",
        "quiet": True,
        "noprogress": True,
        "no_warnings": True,
        "ignoreerrors": False,
    }
    with yt_dlp.YoutubeDL(opts) as ydl:
        ydl.extract_info(url, download=True)
    return [p for p in dest.iterdir() if p.suffix.lower() in VIDEO_EXT]


def fetch_link(link: str, work_dir: Path, interactive: bool = False) -> list[Path]:
    """Download everything behind one link. Returns media files (videos and images)."""
    local = Path(link).expanduser()
    if local.exists():
        if local.is_dir():
            return sorted(p for p in local.rglob("*") if p.suffix.lower() in VIDEO_EXT | IMAGE_EXT)
        return [local.resolve()]

    url = unwrap_link(link)
    dest = work_dir / "media" / source_id(url)
    manifest = dest / ".files.json"
    if manifest.exists():
        files = [Path(p) for p in json.loads(manifest.read_text())]
        if files and all(p.exists() for p in files):
            return files
    dest.mkdir(parents=True, exist_ok=True)

    files: list[Path] = []
    errors: list[str] = []
    if re.search(r"drive\.google\.com|docs\.google\.com/(file|uc)", url):
        try:
            files = _gdrive(url, dest)
        except Exception as e:
            errors.append(f"google drive: {e}")
    if not files and not re.search(r"frame\.io|f\.io/|mediasilo|wetransfer|notion\.", url):
        try:
            files = _ytdlp(url, dest)
        except Exception as e:
            errors.append(f"yt-dlp: {str(e).splitlines()[0][:200]}")
    if not files:
        from .browser import browser_download

        try:
            files = browser_download(url, dest, interactive=interactive)
        except Exception as e:
            errors.append(f"browser: {e}")

    files = _expand(files, dest)
    if not files:
        raise RuntimeError(f"couldn't download anything from {url} ({'; '.join(errors) or 'no files found'}). "
                           "Download it yourself and put the file path in the campaign file's `sources`, "
                           "or rerun with --interactive.")
    manifest.write_text(json.dumps([str(f) for f in files]))
    return files


def pick_logo(images: list[Path]) -> Path | None:
    """Prefer a white/light logo variant: it reads best over video."""
    if not images:
        return None
    for pat in (r"wht|white|light|neg", r"logo"):
        hits = [p for p in images if re.search(pat, p.name, re.I)]
        if hits:
            return hits[0]
    return images[0]
