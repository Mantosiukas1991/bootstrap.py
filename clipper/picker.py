"""Ask Claude to plan the clips: which moments, in what order, with what on-screen text and caption."""

from __future__ import annotations

import json
from pathlib import Path

import anthropic
from pydantic import BaseModel, Field

from .campaign import Campaign
from .frames import frame_blocks
from .llm import MODEL, check_stop, fallback_kwargs
from .transcribe import Segment, Word, all_words, transcript_for_prompt

SYSTEM = """You are a short-form video editor who clips official footage for paid clipping campaigns \
(brands pay per view for clips posted to TikTok, Instagram Reels and YouTube Shorts).

Thousands of clippers get the same footage. Your clips must stop the scroll and look different from the pile, \
while following every campaign rule. A rule-breaking clip earns nothing, so rule compliance beats virality.

You get: the campaign brief, a hook playbook researched for this campaign, keyframes from the footage with \
timestamps, and a timestamped transcript (which may be song lyrics, or empty for music-only footage).

How to build a clip:
- A clip is 1-4 spans of the source, played in the order you list them. Use one span for a self-contained moment \
(a dialogue exchange, an explanation). Use several spans to build a tight sequence from the strongest shots \
(for a trailer: the most striking reveals), but keep it honest: never combine shots to imply something the footage \
doesn't show.
- The first second must be the strongest visual or line in the clip. Don't open on logos, black frames or slow fades.
- Speech must not be cut mid-sentence. For music-only footage, cut on scene changes.
- Respect the campaign's min/max length (total of all spans).
- Each clip needs a clearly different angle from the others (different moment, different hook pattern) so they \
don't compete with each other or look like spam.

On-screen text (hook):
- Use the playbook's hook patterns and the brief's example lines. Under 10 words, true, specific, matches what is \
on screen. No exaggeration, rage bait or made-up facts if the brief forbids them; never claim a trailer is gameplay.
- If the brief requires a phrase in caption or on-screen text, include it in the caption.

Post caption:
- Sound like a genuine fan. Write your own words, don't copy the brief's example caption.
- Do not put hashtags in the caption text; list them in `hashtags` (topic hashtags only, no disclosure tag). \
Required lines and the disclosure tag are added by the tool.
- Only facts that the brief, context or footage support.
"""


class Span(BaseModel):
    start: float = Field(description="Start time in seconds in the source.")
    end: float = Field(description="End time in seconds in the source.")


class ClipPick(BaseModel):
    spans: list[Span] = Field(description="1-4 source spans, played in this order.")
    title: str = Field(description="Short post title (used for YouTube Shorts and file names), under 80 characters.")
    hook: str = Field(description="On-screen text, under 10 words.")
    caption: str = Field(description="Post caption text, without hashtags or required lines.")
    hashtags: list[str] = Field(description="Topic hashtags, each starting with #, best first.")
    subtitles: bool = Field(description="True if the clip has spoken dialogue worth subtitling (not song lyrics).")
    angle: str = Field(description="The hook pattern / angle this clip uses, in a few words.")
    virality_score: int = Field(description="1-10 estimate of how well this clip will perform.")
    why: str = Field(description="One or two sentences on why this clip stands out.")
    rule_check: str = Field(description="How this clip meets each campaign rule; flag anything uncertain.")

    @property
    def duration(self) -> float:
        return sum(s.end - s.start for s in self.spans)

    @property
    def start(self) -> float:
        return self.spans[0].start

    @property
    def end(self) -> float:
        return self.spans[-1].end


class ClipPlan(BaseModel):
    clips: list[ClipPick]


def pick_clips(campaign: Campaign, segments: list[Segment], source_label: str, duration: float,
               frames: list | None = None, playbook: str = "", client: anthropic.Anthropic | None = None) -> list[ClipPick]:
    client = client or anthropic.Anthropic()
    transcript = transcript_for_prompt(segments) or "(no speech detected)"
    content: list[dict] = [{"type": "text", "text": (
        f"<campaign_brief>\n{campaign.brief_text()}\n</campaign_brief>\n\n"
        + (f"<hook_playbook>\n{playbook}\n</hook_playbook>\n\n" if playbook else "")
        + f"<source name=\"{source_label}\" duration_seconds=\"{duration:.1f}\"/>\n\n"
        f"<transcript>\n{transcript}\n</transcript>\n\nKeyframes from the footage follow."
    )}]
    content += frame_blocks(frames or [])
    content.append({"type": "text", "text": (
        f"Plan up to {campaign.clips_per_source} clips from this source, best first. Return fewer if the footage "
        "doesn't support that many strong, rule-compliant, clearly different clips."
    )})

    with client.beta.messages.stream(
        model=MODEL,
        max_tokens=32000,
        system=SYSTEM,
        messages=[{"role": "user", "content": content}],
        output_format=ClipPlan,
        output_config={"effort": "high"},
        **fallback_kwargs(),
    ) as stream:
        response = stream.get_final_message()
    check_stop(response, f"planning clips for {source_label}")
    plan = response.parsed_output
    if plan is None:
        raise RuntimeError("no structured clip plan returned")
    return fit_clips(plan.clips, all_words(segments), campaign.min_seconds, campaign.max_seconds, duration)


def snap_to_words(start: float, end: float, words: list[Word], pad: float = 0.15) -> tuple[float, float]:
    """Extend a span so it doesn't cut a word in half, with a little padding."""
    s, e = start, end
    for w in words:
        if w.start <= start < w.end:
            s = w.start - pad
            break
    for w in words:
        if w.start < end <= w.end:
            e = w.end + pad
            break
    return max(0.0, s), e


def fit_clips(clips: list[ClipPick], words: list[Word], min_s: float, max_s: float,
              source_duration: float | None = None) -> list[ClipPick]:
    """Clean spans, enforce total length and drop near-duplicate clips."""
    fitted: list[ClipPick] = []
    for clip in clips:
        spans: list[Span] = []
        total = 0.0
        for sp in clip.spans[:4]:
            s, e = snap_to_words(sp.start, sp.end, words)
            if source_duration:
                e = min(e, source_duration)
            if e - s < 0.3:
                continue
            if total + (e - s) > max_s:  # trim the last span to fit, at a word end if possible
                limit = s + (max_s - total)
                ends = [w.end for w in words if s < w.end <= limit]
                e = max(ends) if ends else limit
                if e - s >= 0.3:
                    spans.append(Span(start=round(s, 2), end=round(e, 2)))
                break
            spans.append(Span(start=round(s, 2), end=round(e, 2)))
            total += e - s
        if not spans:
            continue
        candidate = clip.model_copy(update={"spans": spans})
        if candidate.duration < min_s:
            continue
        if any(_overlap(candidate, f) > 0.6 for f in fitted):
            continue
        fitted.append(candidate)
    return fitted


def _overlap(a: ClipPick, b: ClipPick) -> float:
    shared = sum(max(0.0, min(x.end, y.end) - max(x.start, y.start)) for x in a.spans for y in b.spans)
    return shared / max(0.01, min(a.duration, b.duration))


def save_plan(clips: list[ClipPick], path: Path) -> None:
    path.write_text(json.dumps([c.model_dump() for c in clips], indent=2, ensure_ascii=False))


def load_plan(path: Path) -> list[ClipPick]:
    data = json.loads(path.read_text())
    out = []
    for d in data:
        if "spans" not in d and "start" in d:  # plans from the first version
            d["spans"] = [{"start": d.pop("start"), "end": d.pop("end")}]
        d.setdefault("subtitles", True)
        d.setdefault("angle", "")
        out.append(ClipPick.model_validate(d))
    return out
