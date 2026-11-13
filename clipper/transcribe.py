"""Word-level transcription with faster-whisper, cached as JSON next to the video."""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass
from pathlib import Path


@dataclass
class Word:
    start: float
    end: float
    text: str


@dataclass
class Segment:
    start: float
    end: float
    text: str
    words: list[Word]


def transcribe(video: Path, cache_dir: Path, model_size: str = "small", language: str | None = None) -> list[Segment]:
    cache_dir.mkdir(parents=True, exist_ok=True)
    cache = cache_dir / f"{video.stem}.{model_size}.transcript.json"
    if cache.exists():
        return load_segments(cache)

    from faster_whisper import WhisperModel

    model = WhisperModel(model_size, device="auto", compute_type="auto")
    raw, _info = model.transcribe(str(video), word_timestamps=True, vad_filter=True, language=language)
    segments: list[Segment] = []
    for s in raw:
        words = [Word(float(w.start), float(w.end), w.word.strip()) for w in (s.words or []) if w.word.strip()]
        segments.append(Segment(float(s.start), float(s.end), s.text.strip(), words))

    cache.write_text(json.dumps([asdict(s) for s in segments], ensure_ascii=False))
    return segments


def load_segments(path: Path) -> list[Segment]:
    data = json.loads(path.read_text())
    return [Segment(d["start"], d["end"], d["text"], [Word(**w) for w in d["words"]]) for d in data]


def all_words(segments: list[Segment]) -> list[Word]:
    return [w for s in segments for w in s.words]


def transcript_for_prompt(segments: list[Segment]) -> str:
    """Compact timestamped transcript: one line per segment."""
    return "\n".join(f"[{s.start:.1f}-{s.end:.1f}] {s.text}" for s in segments)
