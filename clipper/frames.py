"""Keyframes so Claude can see the footage, not just hear it (trailers are mostly music and visuals)."""

from __future__ import annotations

import base64
import json
import re
import subprocess
from pathlib import Path


def probe(video: Path) -> dict:
    out = subprocess.run(["ffprobe", "-v", "error", "-show_entries", "format=duration:stream=codec_type,width,height",
                          "-of", "json", str(video)], capture_output=True, text=True, check=True)
    data = json.loads(out.stdout)
    streams = data.get("streams", [])
    v = next((s for s in streams if s.get("codec_type") == "video"), {})
    return {
        "duration": float(data.get("format", {}).get("duration", 0) or 0),
        "width": v.get("width"), "height": v.get("height"),
        "has_audio": any(s.get("codec_type") == "audio" for s in streams),
    }


def keyframes(video: Path, cache_dir: Path, max_frames: int = 60, scene_threshold: float = 0.3) -> list[tuple[float, Path]]:
    """Frames at scene changes (plus a regular grid so long static shots are covered). Returns (time, jpg)."""
    out_dir = cache_dir / f"{video.stem}.frames"
    index = out_dir / "index.json"
    if index.exists():
        return [(t, out_dir / name) for t, name in json.loads(index.read_text())]
    out_dir.mkdir(parents=True, exist_ok=True)

    duration = probe(video)["duration"] or 1.0
    res = subprocess.run(
        ["ffmpeg", "-hide_banner", "-i", str(video), "-vf", f"select='gt(scene,{scene_threshold})',showinfo",
         "-f", "null", "-"], capture_output=True, text=True)
    times = [float(t) for t in re.findall(r"pts_time:([\d.]+)", res.stderr)]
    step = max(2.0, duration / max_frames)
    grid = [i * step + step / 2 for i in range(int(duration // step))]
    merged = sorted(set(round(t, 2) for t in [0.5, *times, *grid] if t < duration))
    # thin out to max_frames, keeping spread
    if len(merged) > max_frames:
        merged = [merged[round(i * (len(merged) - 1) / (max_frames - 1))] for i in range(max_frames)]

    frames = []
    for i, t in enumerate(merged):
        path = out_dir / f"{i:03d}.jpg"
        subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-ss", f"{t:.2f}", "-i", str(video), "-frames:v", "1",
                        "-vf", "scale=448:-2", "-q:v", "5", str(path)], check=True)
        if path.exists():
            frames.append((t, path))
    index.write_text(json.dumps([(t, p.name) for t, p in frames]))
    return frames


def frame_blocks(frames: list[tuple[float, Path]]) -> list[dict]:
    """Message content blocks: a timestamp label followed by the image."""
    blocks: list[dict] = []
    for t, path in frames:
        blocks.append({"type": "text", "text": f"Frame at {t:.1f}s"})
        blocks.append({"type": "image", "source": {"type": "base64", "media_type": "image/jpeg",
                                                   "data": base64.b64encode(path.read_bytes()).decode()}})
    return blocks
