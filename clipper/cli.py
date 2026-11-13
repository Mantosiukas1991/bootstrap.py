"""Command line entry point.

    clipper run campaigns/my-campaign.toml            # download, transcribe, pick, render
    clipper run campaigns/my-campaign.toml --plan-only  # stop after Claude picks clips (edit plan.json, then render)
    clipper render campaigns/my-campaign.toml          # re-render from existing plan.json files
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from .campaign import Campaign, load_campaign
from .picker import ClipPick, load_plan, pick_clips, save_plan
from .render import render_clip
from .source import fetch, require_ffmpeg, source_id
from .transcribe import all_words, transcribe


def slugify(text: str, max_len: int = 40) -> str:
    s = "".join(c if c.isalnum() else "-" for c in text.lower())
    s = "-".join(p for p in s.split("-") if p)
    return s[:max_len].rstrip("-") or "clip"


def post_text(clip: ClipPick, campaign: Campaign) -> str:
    tags = list(dict.fromkeys([*clip.hashtags, *campaign.required_hashtags]))  # keep order, dedupe
    caption = clip.caption
    for m in campaign.required_mentions:
        if m.lower() not in caption.lower():
            caption += f" {m}"
    if campaign.caption_cta and campaign.caption_cta.lower() not in caption.lower():
        caption += f"\n{campaign.caption_cta}"
    return f"TITLE: {clip.title}\n\nCAPTION:\n{caption}\n\n{' '.join(tags)}\n"


def process_source(campaign: Campaign, source: str, args: argparse.Namespace, out_root: Path) -> list[dict]:
    work = Path(args.work) / "videos"
    src_dir = out_root / source_id(source)
    src_dir.mkdir(parents=True, exist_ok=True)
    plan_path = src_dir / "plan.json"

    print(f"\n== {source}")
    video = fetch(source, work)
    print(f"   video: {video}")
    segments = transcribe(video, Path(args.work) / "transcripts", args.whisper_model, campaign.language or None)
    print(f"   transcript: {len(segments)} segments")

    if args.command == "render" or (plan_path.exists() and not args.repick):
        if not plan_path.exists():
            print("   no plan.json yet; run `clipper run` first")
            return []
        clips = load_plan(plan_path)
        print(f"   using existing plan ({len(clips)} clips)")
    else:
        clips = pick_clips(campaign, segments, source)
        save_plan(clips, plan_path)
        print(f"   Claude picked {len(clips)} clips -> {plan_path}")

    if args.plan_only:
        return []

    words = all_words(segments)
    results = []
    for i, clip in enumerate(clips, 1):
        name = f"{i:02d}-{slugify(clip.title)}"
        mp4 = src_dir / f"{name}.mp4"
        print(f"   rendering {mp4.name} ({clip.start:.1f}-{clip.end:.1f}s, score {clip.virality_score})")
        render_clip(video, clip.start, clip.end, mp4, words, layout=campaign.layout,
                    hook=clip.hook if campaign.show_hook else "", burn_captions=campaign.burn_captions)
        (src_dir / f"{name}.txt").write_text(post_text(clip, campaign))
        results.append({"file": str(mp4), "source": source, **clip.model_dump()})
    return results


def write_checklist(campaign: Campaign, results: list[dict], out_root: Path) -> Path:
    lines = [f"# {campaign.name}: clips to post", ""]
    if campaign.whop_url:
        lines += [f"Campaign: {campaign.whop_url}", ""]
    lines += [
        "For each clip: watch it, post it to each platform from an account you own, then submit the post link "
        "on the Whop campaign page. Most campaigns only pay for posts submitted there.",
        "",
    ]
    for r in sorted(results, key=lambda r: -r["virality_score"]):
        lines.append(f"## [{r['virality_score']}/10] {r['title']}")
        lines.append(f"- File: `{r['file']}`")
        lines.append(f"- Source: {r['source']} @ {r['start']:.1f}-{r['end']:.1f}s")
        lines.append(f"- Why: {r['why']}")
        lines.append(f"- Rule check: {r['rule_check']}")
        for p in campaign.platforms:
            lines.append(f"- [ ] Posted to {p}  link: ")
        lines.append("- [ ] Submitted on Whop")
        lines.append("")
    path = out_root / "SUBMIT.md"
    path.write_text("\n".join(lines))
    (out_root / "manifest.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    return path


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clipper", description="Clip long-form videos for Whop Content Rewards campaigns.")
    p.add_argument("command", choices=["run", "render"])
    p.add_argument("campaign", help="Campaign TOML file")
    p.add_argument("--source", action="append", default=[], help="Extra source URL/path (repeatable)")
    p.add_argument("--out", default="out", help="Output directory (default: out)")
    p.add_argument("--work", default="work", help="Download/transcript cache (default: work)")
    p.add_argument("--whisper-model", default="small", help="faster-whisper model: tiny/base/small/medium/large-v3")
    p.add_argument("--plan-only", action="store_true", help="Stop after clip selection so you can edit plan.json")
    p.add_argument("--repick", action="store_true", help="Ask Claude again even if plan.json exists")
    args = p.parse_args(argv)

    require_ffmpeg()
    campaign = load_campaign(args.campaign)
    sources = [*campaign.sources, *args.source]
    if not sources:
        p.error("no sources: add `sources = [...]` to the campaign file or pass --source")

    out_root = Path(args.out) / campaign.slug
    results: list[dict] = []
    for src in sources:
        try:
            results += process_source(campaign, src, args, out_root)
        except Exception as e:  # keep going with other sources
            print(f"   FAILED: {e}", file=sys.stderr)

    if results:
        print(f"\nDone. {len(results)} clips. Checklist: {write_checklist(campaign, results, out_root)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
