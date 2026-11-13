"""Render 9:16 clips with burned-in word-by-word captions and an opening hook."""

from __future__ import annotations

import subprocess
from pathlib import Path

from .transcribe import Word

W, H = 1080, 1920

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {W}
PlayResY: {H}
WrapStyle: 0
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Caption,{{font}},88,&H00FFFFFF,&H00FFFFFF,&H00000000,&H64000000,-1,0,0,0,100,100,0,0,1,7,2,2,80,80,560,1
Style: Hook,{{font}},76,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC8000000,-1,0,0,0,100,100,0,0,3,18,0,8,90,90,260,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

HIGHLIGHT = r"{\c&H00D7FF&}"  # gold (ASS colours are BGR)
RESET = r"{\c&HFFFFFF&}"


def ass_time(t: float) -> str:
    t = max(0.0, t)
    cs = int(round(t * 100))
    h, cs = divmod(cs, 360000)
    m, cs = divmod(cs, 6000)
    s, cs = divmod(cs, 100)
    return f"{h}:{m:02d}:{s:02d}.{cs:02d}"


def ass_escape(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")").replace("\n", " ")


def chunk_words(words: list[Word], max_words: int = 3, max_gap: float = 0.6) -> list[list[Word]]:
    """Group words into short on-screen phrases, breaking on pauses and sentence ends."""
    chunks: list[list[Word]] = []
    cur: list[Word] = []
    for w in words:
        if cur and (len(cur) >= max_words or w.start - cur[-1].end > max_gap or cur[-1].text[-1:] in ".?!"):
            chunks.append(cur)
            cur = []
        cur.append(w)
    if cur:
        chunks.append(cur)
    return chunks


def build_ass(words: list[Word], clip_start: float, clip_end: float, hook: str = "", font: str = "Inter",
              uppercase: bool = True) -> str:
    """ASS subtitle file for one clip. Word times are absolute; output times are clip-relative."""
    inside = [Word(max(w.start, clip_start) - clip_start, min(w.end, clip_end) - clip_start, w.text)
              for w in words if w.end > clip_start and w.start < clip_end]
    lines = [ASS_HEADER.replace("{font}", font)]
    duration = clip_end - clip_start

    if hook:
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(min(3.0, duration))},Hook,,0,0,0,,{ass_escape(hook)}")

    for chunk in chunk_words(inside):
        texts = [ass_escape(w.text.upper() if uppercase else w.text) for w in chunk]
        for i, w in enumerate(chunk):
            start = w.start
            # hold each highlight until the next word starts so captions don't flicker
            end = chunk[i + 1].start if i + 1 < len(chunk) else w.end
            if end <= start:
                end = start + 0.05
            parts = [f"{HIGHLIGHT}{t}{RESET}" if j == i else t for j, t in enumerate(texts)]
            lines.append(f"Dialogue: 0,{ass_time(start)},{ass_time(end)},Caption,,0,0,0,,{' '.join(parts)}")
    return "\n".join(lines) + "\n"


def video_filter(layout: str, ass_name: str | None) -> str:
    if layout == "crop":
        chain = f"[0:v]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1"
    else:  # blurred background with the full frame centred on top
        chain = (
            f"[0:v]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=40,eq=brightness=-0.08[bg];"
            f"[fgsrc]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1"
        )
    if ass_name:
        chain += f",ass={ass_name}"
    return chain + "[v]"


def render_clip(video: Path, start: float, end: float, out_path: Path, words: list[Word], *, layout: str = "blur",
                hook: str = "", burn_captions: bool = True, font: str = "Inter") -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    ass_name = None
    if burn_captions or hook:
        ass_path = out_path.with_suffix(".ass")
        ass_path.write_text(build_ass(words if burn_captions else [], start, end, hook, font=font))
        ass_name = ass_path.name

    cmd = [
        "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
        "-ss", f"{start:.3f}", "-i", str(video.resolve()), "-t", f"{end - start:.3f}",
        "-filter_complex", video_filter(layout, ass_name),
        "-map", "[v]", "-map", "0:a?",
        "-c:v", "libx264", "-preset", "medium", "-crf", "20", "-pix_fmt", "yuv420p", "-r", "30",
        "-c:a", "aac", "-b:a", "160k", "-ar", "44100",
        "-movflags", "+faststart",
        out_path.name,
    ]
    # run inside the output dir so the ass= filter gets a plain relative filename (no path escaping)
    subprocess.run(cmd, check=True, cwd=out_path.parent)
    return out_path
