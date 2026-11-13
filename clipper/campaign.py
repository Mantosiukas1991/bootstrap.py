"""Campaign brief: what a clipping campaign asks clippers to make.

Created automatically from a brief link (`clipper go <url>`), or written by hand (see campaigns/example.toml).
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Campaign(BaseModel):
    name: str
    brief_url: str = ""
    whop_url: str = ""
    creator: str = Field("", description="Brand or creator whose content is clipped.")
    rate_per_1k_views: float | None = None
    platforms: list[str] = Field(default_factory=lambda: ["tiktok", "youtube_shorts", "instagram_reels"])

    sources: list[str] = Field(default_factory=list, description="Links/paths to the official footage.")
    asset_links: list[str] = Field(default_factory=list, description="Links to logos/overlays the campaign provides.")
    context_links: list[str] = Field(default_factory=list, description="Background reading (blog posts, press).")

    min_seconds: float = 15
    max_seconds: float = 60
    clips_per_source: int = 5

    rules: list[str] = Field(default_factory=list)
    banned: list[str] = Field(default_factory=list)

    required_caption_lines: list[str] = Field(default_factory=list,
                                              description="Lines every caption must contain, e.g. @mentions, song credit.")
    disclosure_tag: str = Field("", description="Ad disclosure hashtag placed first, on its own line, e.g. #Ad.")
    required_hashtags: list[str] = Field(default_factory=list)
    required_mentions: list[str] = Field(default_factory=list)
    max_extra_hashtags: int | None = Field(None, description="Cap on hashtags besides the disclosure tag.")
    caption_cta: str = ""
    on_screen_text_examples: list[str] = Field(default_factory=list)
    language: str = "en"

    layout: Literal["blur", "crop"] = "blur"
    subtitles: Literal["auto", "on", "off"] = Field("auto", description="auto = only when the clip has speech.")
    show_hook: bool = True
    hook_seconds: float = Field(0, description="How long on-screen text stays; 0 = whole clip.")
    watermark: str = Field("", description="Path to a logo PNG to overlay, if the campaign requires one.")
    watermark_required: bool = False
    watermark_position: Literal["top-left", "top-right", "bottom-left", "bottom-right", "top-center"] = "top-right"
    notes: str = ""

    @model_validator(mode="after")
    def _check_lengths(self) -> "Campaign":
        if self.min_seconds <= 0 or self.max_seconds <= self.min_seconds:
            raise ValueError("need 0 < min_seconds < max_seconds")
        return self

    @property
    def slug(self) -> str:
        keep = "".join(c if c.isalnum() else "-" for c in self.name.lower())
        return "-".join(p for p in keep.split("-") if p)[:60] or "campaign"

    def brief_text(self) -> str:
        """Human-readable brief for model prompts."""
        lines = [f"Campaign: {self.name}"]
        if self.creator:
            lines.append(f"Brand/creator: {self.creator}")
        lines.append(f"Target platforms: {', '.join(self.platforms)}")
        lines.append(f"Clip length: {self.min_seconds:g}-{self.max_seconds:g} seconds")

        def block(title: str, items: list[str]) -> None:
            if items:
                lines.append(title + ":\n" + "\n".join(f"- {i}" for i in items))

        block("Rules", self.rules)
        block("Not allowed", self.banned)
        block("Every caption must contain these lines", self.required_caption_lines)
        if self.disclosure_tag:
            lines.append(f"Disclosure: {self.disclosure_tag} on its own line, first hashtag after the caption text")
        if self.max_extra_hashtags is not None:
            lines.append(f"At most {self.max_extra_hashtags} other hashtags")
        if self.required_hashtags:
            lines.append("Required hashtags: " + " ".join(self.required_hashtags))
        if self.required_mentions:
            lines.append("Required mentions: " + " ".join(self.required_mentions))
        if self.caption_cta:
            lines.append(f"Caption call to action: {self.caption_cta}")
        block("Example on-screen text lines", self.on_screen_text_examples)
        if self.watermark_required:
            lines.append("A logo watermark is added to every clip automatically.")
        if self.notes:
            lines.append(f"Notes: {self.notes}")
        lines.append(f"Language: {self.language}")
        return "\n".join(lines)


def load_campaign(path: str | Path) -> Campaign:
    with open(path, "rb") as f:
        return Campaign.model_validate(tomllib.load(f))


def _toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, (int, float)):
        return repr(v)
    if isinstance(v, str):
        return json.dumps(v, ensure_ascii=False)  # JSON strings are valid TOML basic strings
    if isinstance(v, list):
        if not v:
            return "[]"
        return "[\n" + "".join(f"  {_toml_value(x)},\n" for x in v) + "]"
    raise TypeError(f"can't write {type(v)} to TOML")


def save_campaign(campaign: Campaign, path: str | Path) -> Path:
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    lines = ["# Generated by `clipper brief`. Edit anything that looks wrong, then run `clipper run` on this file.", ""]
    for key, value in campaign.model_dump().items():
        if value is None:
            continue
        lines.append(f"{key} = {_toml_value(value)}")
    path.write_text("\n".join(lines) + "\n")
    return path
