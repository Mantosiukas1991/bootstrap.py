"""Render 9:16 clips: join spans, fit to vertical, add on-screen text, subtitles and logo watermark."""

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
Style: Hook,{{font}},72,&H00FFFFFF,&H00FFFFFF,&H00000000,&HC8000000,-1,0,0,0,100,100,0,0,3,18,0,8,90,90,330,1

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""

HIGHLIGHT = r"{\c&H00D7FF&}"  # gold (ASS colours are BGR)
RESET = r"{\c&HFFFFFF&}"

LOGO_W = 240
LOGO_MARGIN = 60
LOGO_POS = {
    "top-left": f"{LOGO_MARGIN}:{LOGO_MARGIN + 60}",
    "top-right": f"W-w-{LOGO_MARGIN}:{LOGO_MARGIN + 60}",
    "top-center": f"(W-w)/2:{LOGO_MARGIN + 60}",
    "bottom-left": f"{LOGO_MARGIN}:H-h-{LOGO_MARGIN + 220}",
    "bottom-right": f"W-w-{LOGO_MARGIN}:H-h-{LOGO_MARGIN + 220}",
}


def ass_time(t: float) -> str:
    cs = int(round(max(0.0, t) * 100))
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


def words_on_timeline(words: list[Word], spans: list[tuple[float, float]]) -> list[Word]:
    """Map source-time words into output time for spans played back to back."""
    out: list[Word] = []
    offset = 0.0
    for start, end in spans:
        for w in words:
            if w.end > start and w.start < end:
                out.append(Word(max(w.start, start) - start + offset, min(w.end, end) - start + offset, w.text))
        offset += end - start
    return out


def build_ass(words: list[Word], duration: float, hook: str = "", hook_seconds: float = 0,
              font: str = "Inter", uppercase: bool = True) -> str:
    """ASS file for one clip. `words` are already in output time."""
    lines = [ASS_HEADER.replace("{font}", font)]
    if hook:
        hook_end = duration if hook_seconds <= 0 else min(hook_seconds, duration)
        lines.append(f"Dialogue: 1,{ass_time(0)},{ass_time(hook_end)},Hook,,0,0,0,,{ass_escape(hook)}")
    for chunk in chunk_words(words):
        texts = [ass_escape(w.text.upper() if uppercase else w.text) for w in chunk]
        for i, w in enumerate(chunk):
            end = chunk[i + 1].start if i + 1 < len(chunk) else w.end  # hold until next word: no flicker
            end = max(end, w.start + 0.05)
            parts = [f"{HIGHLIGHT}{t}{RESET}" if j == i else t for j, t in enumerate(texts)]
            lines.append(f"Dialogue: 0,{ass_time(w.start)},{ass_time(end)},Caption,,0,0,0,,{' '.join(parts)}")
    return "\n".join(lines) + "\n"


def filter_graph(spans: list[tuple[float, float]], layout: str, has_audio: bool, ass_name: str | None,
                 logo: bool, logo_position: str) -> str:
    parts: list[str] = []
    concat_in = ""
    # each span is its own input (fast -ss seek), so input i = span i and the logo is input len(spans)
    for i, _ in enumerate(spans):
        parts.append(f"[{i}:v]setpts=PTS-STARTPTS,fps=30,format=yuv420p[v{i}]")
        if has_audio:
            parts.append(f"[{i}:a]asetpts=PTS-STARTPTS,aresample=44100[a{i}]")
        concat_in += f"[v{i}]" + (f"[a{i}]" if has_audio else "")
    parts.append(f"{concat_in}concat=n={len(spans)}:v=1:a={1 if has_audio else 0}[vc]" + ("[ac]" if has_audio else ""))

    if layout == "crop":
        parts.append(f"[vc]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},setsar=1[vl]")
    else:
        parts.append(
            f"[vc]split=2[bgsrc][fgsrc];"
            f"[bgsrc]scale={W}:{H}:force_original_aspect_ratio=increase,crop={W}:{H},gblur=sigma=40,eq=brightness=-0.08[bg];"
            f"[fgsrc]scale={W}:{H}:force_original_aspect_ratio=decrease[fg];"
            f"[bg][fg]overlay=(W-w)/2:(H-h)/2,setsar=1[vl]"
        )
    last = "vl"
    if logo:
        parts.append(f"[{len(spans)}:v]scale={LOGO_W}:-1,format=rgba[logo];[{last}][logo]overlay={LOGO_POS[logo_position]}[vw]")
        last = "vw"
    if ass_name:
        parts.append(f"[{last}]ass={ass_name}[v]")
    else:
        parts.append(f"[{last}]null[v]")
    return ";".join(parts)


def render_clip(video: Path, spans: list[tuple[float, float]], out_path: Path, words: list[Word], *,
                has_audio: bool = True, layout: str = "blur", hook: str = "", hook_seconds: float = 0,
                subtitles: bool = True, logo: Path | None = None, logo_position: str = "top-right",
                font: str = "Inter") -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    duration = sum(e - s for s, e in spans)
    ass_name = None
    timeline_words = words_on_timeline(words, spans) if subtitles else []
    if timeline_words or hook:
        ass_path = out_path.with_suffix(".ass")
        ass_path.write_text(build_ass(timeline_words, duration, hook, hook_seconds, font=font))
        ass_name = ass_path.name

    cmd = ["ffmpeg", "-y", "-hide_banner", "-loglevel", "error"]
    for s, e in spans:
        cmd += ["-ss", f"{s:.3f}", "-t", f"{e - s:.3f}", "-i", str(video.resolve())]
    if logo:
        cmd += ["-i", str(Path(logo).resolve())]
    cmd += [
        "-filter_complex", filter_graph(spans, layout, has_audio, ass_name, bool(logo), logo_position),
        "-map", "[v]", *(["-map", "[ac]"] if has_audio else []),
        "-c:v", "libx264", "-preset", "medium", "-crf", "18", "-pix_fmt", "yuv420p", "-r", "30",
        *(["-c:a", "aac", "-b:a", "192k"] if has_audio else []),
        "-movflags", "+faststart",
        out_path.name,
    ]
    # run inside the output dir so the ass= filter gets a plain relative filename (no path escaping)
    subprocess.run(cmd, check=True, cwd=out_path.parent)
    return out_path
