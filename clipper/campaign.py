"""Campaign brief: what a Whop Content Rewards campaign asks clippers to make.

Copy the brief from the campaign page on Whop into a TOML file (see campaigns/example.toml).
"""

from __future__ import annotations

import tomllib
from pathlib import Path
from typing import Literal

from pydantic import BaseModel, Field, model_validator


class Campaign(BaseModel):
    name: str
    whop_url: str = ""
    creator: str = Field("", description="Whose content is being clipped (name/handle).")
    rate_per_1k_views: float | None = Field(None, description="Payout in USD per 1,000 views, for your notes.")
    platforms: list[str] = Field(default_factory=lambda: ["tiktok", "youtube_shorts", "instagram_reels"])

    sources: list[str] = Field(default_factory=list, description="URLs (anything yt-dlp supports) or local file paths.")

    min_seconds: float = 15
    max_seconds: float = 60
    clips_per_source: int = 5

    rules: list[str] = Field(default_factory=list, description="Campaign requirements, copied from the brief.")
    banned: list[str] = Field(default_factory=list, description="Content the campaign forbids.")
    required_hashtags: list[str] = Field(default_factory=list)
    required_mentions: list[str] = Field(default_factory=list, description="@handles that must be in the caption.")
    caption_cta: str = Field("", description="Call to action the campaign wants in captions, if any.")
    language: str = "en"

    layout: Literal["blur", "crop"] = "blur"
    burn_captions: bool = True
    show_hook: bool = True

    @model_validator(mode="after")
    def _check_lengths(self) -> "Campaign":
        if self.min_seconds <= 0 or self.max_seconds <= self.min_seconds:
            raise ValueError("need 0 < min_seconds < max_seconds")
        return self

    @property
    def slug(self) -> str:
        keep = "".join(c if c.isalnum() else "-" for c in self.name.lower())
        return "-".join(p for p in keep.split("-") if p) or "campaign"

    def brief_text(self) -> str:
        """Human-readable brief for the model prompt."""
        lines = [f"Campaign: {self.name}"]
        if self.creator:
            lines.append(f"Creator being clipped: {self.creator}")
        lines.append(f"Target platforms: {', '.join(self.platforms)}")
        lines.append(f"Clip length: {self.min_seconds:g}-{self.max_seconds:g} seconds")
        if self.rules:
            lines.append("Rules:\n" + "\n".join(f"- {r}" for r in self.rules))
        if self.banned:
            lines.append("Not allowed:\n" + "\n".join(f"- {b}" for b in self.banned))
        if self.required_hashtags:
            lines.append("Required hashtags: " + " ".join(self.required_hashtags))
        if self.required_mentions:
            lines.append("Required mentions: " + " ".join(self.required_mentions))
        if self.caption_cta:
            lines.append(f"Caption call to action: {self.caption_cta}")
        lines.append(f"Language: {self.language}")
        return "\n".join(lines)


def load_campaign(path: str | Path) -> Campaign:
    with open(path, "rb") as f:
        data = tomllib.load(f)
    return Campaign.model_validate(data)
