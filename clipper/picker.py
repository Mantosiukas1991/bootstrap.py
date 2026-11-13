"""Ask Claude to pick the clip-worthy moments that fit the campaign brief."""

from __future__ import annotations

import json
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from .campaign import Campaign
from .transcribe import Segment, Word, all_words, transcript_for_prompt

MODEL = "claude-opus-5-5"

SYSTEM = """You are a short-form video editor who clips long-form content for paid clipping campaigns \
(creators pay per view for clips posted to TikTok, YouTube Shorts and Instagram Reels).

Your job: read a timestamped transcript and choose the moments most likely to hold attention and get views, \
while strictly following the campaign brief. A clip that breaks a campaign rule earns nothing, so rule \
compliance beats virality.

What makes a strong clip:
- The first 2 seconds hook the viewer (a bold claim, a question, conflict, a surprising number, a punchline setup).
- It stands alone: a viewer with no context understands it. Start at the beginning of a thought, never mid-sentence.
- It has a payoff (answer, punchline, reveal, strong opinion) before it ends. End right after the payoff.
- Emotion or tension: disagreement, a story, a confession, a hot take, a funny exchange.
- No dead air, filler intros, sponsor reads, or "like and subscribe" segments unless the brief asks for them.

Rules for timestamps:
- Use the transcript's timestamps. Start a clip at the start time of the segment where the thought begins and end \
it at the end time of the segment containing the payoff.
- Respect the campaign's min/max length.
- Clips must not overlap each other.

Captions you write for the post (not the burned-in subtitles):
- Write in the campaign's language, natural and platform-native, no hashtag spam.
- Include every required hashtag and mention exactly as given, plus a few relevant hashtags.
- Do not claim to be the creator or misrepresent who is speaking.
"""


class ClipPick(BaseModel):
    start: float = Field(description="Clip start in seconds.")
    end: float = Field(description="Clip end in seconds.")
    title: str = Field(description="Short title for the post (YouTube Shorts title), under 80 characters.")
    hook: str = Field(description="On-screen hook text shown for the first seconds, max 8 words.")
    caption: str = Field(description="Post caption including required mentions and call to action.")
    hashtags: list[str] = Field(description="Hashtags including the required ones, each starting with #.")
    virality_score: int = Field(description="1-10 estimate of how well this clip will perform.")
    why: str = Field(description="One or two sentences on why this moment works.")
    rule_check: str = Field(description="How this clip satisfies each campaign rule; flag anything uncertain.")


class ClipPlan(BaseModel):
    clips: list[ClipPick]


def pick_clips(campaign: Campaign, segments: list[Segment], source_label: str, client: anthropic.Anthropic | None = None) -> list[ClipPick]:
    client = client or anthropic.Anthropic()
    prompt = (
        f"<campaign_brief>\n{campaign.brief_text()}\n</campaign_brief>\n\n"
        f"<source>{source_label}</source>\n\n"
        f"<transcript>\n{transcript_for_prompt(segments)}\n</transcript>\n\n"
        f"Pick up to {campaign.clips_per_source} clips, best first. Return fewer if the content doesn't support that many "
        "good, rule-compliant clips."
    )
    response = client.beta.messages.parse(
        model=MODEL,
        max_tokens=16000,
        system=SYSTEM,
        messages=[{"role": "user", "content": prompt}],
        output_format=ClipPlan,
        output_config={"effort": "high"},
        betas=["server-side-fallback-2026-07-01"],
        fallbacks="default",
    )
    if response.stop_reason == "refusal":
        detail = response.stop_details.explanation if response.stop_details else ""
        raise RuntimeError(f"Claude declined to select clips for {source_label}: {detail}")
    if response.stop_reason == "max_tokens":
        raise RuntimeError("clip selection hit max_tokens; lower clips_per_source")
    plan = response.parsed_output
    if plan is None:
        raise RuntimeError("no structured clip plan returned")
    return fit_clips(plan.clips, all_words(segments), campaign.min_seconds, campaign.max_seconds)


def fit_clips(clips: list[ClipPick], words: list[Word], min_s: float, max_s: float) -> list[ClipPick]:
    """Snap clip bounds to word boundaries, enforce length limits and drop overlaps."""
    fitted: list[ClipPick] = []
    for clip in clips:
        start, end = snap_to_words(clip.start, clip.end, words)
        if end - start > max_s:
            # trim at the last word that ends inside the limit
            inside = [w.end for w in words if start < w.end <= start + max_s]
            end = max(inside) if inside else start + max_s
        if end - start < min_s:
            continue
        if any(start < f.end and end > f.start for f in fitted):
            continue
        fitted.append(clip.model_copy(update={"start": round(start, 2), "end": round(end, 2)}))
    return fitted


def snap_to_words(start: float, end: float, words: list[Word], pad: float = 0.15) -> tuple[float, float]:
    """Move start back to the first word it cuts and end forward to the last word it cuts, with small padding."""
    if not words:
        return start, end
    s = start
    for w in words:
        if w.start <= start < w.end:
            s = w.start
            break
    e = end
    for w in words:
        if w.start < end <= w.end:
            e = w.end
            break
    return max(0.0, s - pad), e + pad


def save_plan(clips: list[ClipPick], path: Path) -> None:
    path.write_text(json.dumps([c.model_dump() for c in clips], indent=2, ensure_ascii=False))


def load_plan(path: Path) -> list[ClipPick]:
    return [ClipPick.model_validate(d) for d in json.loads(path.read_text())]
