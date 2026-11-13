"""Command line entry point.

    clipper go <brief-link>                 # read the brief, download media, research hooks, plan and render clips
    clipper brief <brief-link>              # only create campaigns/<name>.toml from the brief
    clipper run campaigns/<name>.toml       # everything after the brief, from a (possibly edited) campaign file
    clipper run ... --plan-only             # stop after planning so you can edit plan.json
    clipper render campaigns/<name>.toml    # render from existing (edited) plan.json files
"""

from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .campaign import Campaign, load_campaign, save_campaign
from .source import IMAGE_EXT, VIDEO_EXT, fetch_link, pick_logo, require_ffmpeg, source_id


def slugify(text: str, max_len: int = 40) -> str:
    s = "".join(c if c.isalnum() else "-" for c in text.lower())
    s = "-".join(p for p in s.split("-") if p)
    return s[:max_len].rstrip("-") or "clip"


def post_text(clip, campaign: Campaign) -> str:
    """Caption in the order briefs usually require: text, required lines, disclosure on its own line, hashtags."""
    body = re.sub(r"(^|\s)#\w+", "", clip.caption).strip()  # hashtags go after the disclosure tag
    lines = [body]
    required = [*campaign.required_caption_lines, *campaign.required_mentions]
    if campaign.caption_cta:
        required.append(campaign.caption_cta)
    for req in required:
        if req and req.lower() not in body.lower():
            lines.append(req)

    disclosure = campaign.disclosure_tag.strip()
    tags = [t if t.startswith("#") else f"#{t}" for t in [*campaign.required_hashtags, *clip.hashtags]]
    tags = [t for t in dict.fromkeys(tags) if t.lower() != disclosure.lower()]
    if campaign.max_extra_hashtags is not None:
        tags = tags[: campaign.max_extra_hashtags]

    text = "\n".join(lines)
    if disclosure:
        text += f"\n\n{disclosure}"
    if tags:
        text += ("\n" if disclosure else "\n\n") + " ".join(tags)
    return f"TITLE: {clip.title}\n\nON-SCREEN TEXT: {clip.hook}\n\nCAPTION:\n{text}\n"


def prepare_assets(campaign: Campaign, work: Path, interactive: bool) -> Path | None:
    if campaign.watermark and Path(campaign.watermark).expanduser().exists():
        return Path(campaign.watermark).expanduser()
    images: list[Path] = []
    for link in campaign.asset_links:
        print(f"\n== asset {link}")
        try:
            images += [p for p in fetch_link(link, work, interactive) if p.suffix.lower() in IMAGE_EXT]
        except Exception as e:
            print(f"   FAILED: {e}", file=sys.stderr)
    logo = pick_logo(images)
    if logo:
        print(f"   logo: {logo}")
    elif campaign.watermark_required:
        print("   WARNING: the campaign requires a logo watermark but none was downloaded. "
              "Set `watermark = \"path/to/logo.png\"` in the campaign file.", file=sys.stderr)
    return logo


def process_video(campaign: Campaign, video: Path, label: str, args, out_root: Path, playbook: str,
                  logo: Path | None) -> list[dict]:
    from .frames import keyframes, probe
    from .picker import load_plan, pick_clips, save_plan
    from .render import render_clip
    from .transcribe import all_words, transcribe

    work = Path(args.work)
    info = probe(video)
    src_dir = out_root / f"{slugify(video.stem, 30)}-{source_id(str(video))[:6]}"
    src_dir.mkdir(parents=True, exist_ok=True)
    plan_path = src_dir / "plan.json"
    print(f"\n== {video.name}  ({info['duration']:.0f}s)")

    segments = []
    if info["has_audio"]:
        segments = transcribe(video, work / "transcripts", args.whisper_model, campaign.language or None)
    print(f"   transcript: {len(segments)} segments")

    if args.command == "render" or (plan_path.exists() and not args.repick):
        if not plan_path.exists():
            print("   no plan.json yet; run `clipper run` first")
            return []
        clips = load_plan(plan_path)
        print(f"   using existing plan ({len(clips)} clips)")
    else:
        frames = keyframes(video, work / "frames")
        print(f"   keyframes: {len(frames)}")
        clips = pick_clips(campaign, segments, label, info["duration"], frames, playbook)
        save_plan(clips, plan_path)
        print(f"   Claude planned {len(clips)} clips -> {plan_path}")

    if args.plan_only:
        return []

    words = all_words(segments)
    results = []
    for i, clip in enumerate(clips, 1):
        name = f"{i:02d}-{slugify(clip.title)}"
        mp4 = src_dir / f"{name}.mp4"
        subs = campaign.subtitles == "on" or (campaign.subtitles == "auto" and clip.subtitles)
        print(f"   rendering {mp4.name} ({clip.duration:.1f}s, {len(clip.spans)} span(s), score {clip.virality_score})")
        render_clip(video, [(s.start, s.end) for s in clip.spans], mp4, words, has_audio=info["has_audio"],
                    layout=campaign.layout, hook=clip.hook if campaign.show_hook else "",
                    hook_seconds=campaign.hook_seconds, subtitles=subs, logo=logo,
                    logo_position=campaign.watermark_position)
        (src_dir / f"{name}.txt").write_text(post_text(clip, campaign))
        results.append({"file": str(mp4), "source": label, "duration": round(clip.duration, 1), **clip.model_dump()})
    return results


def write_checklist(campaign: Campaign, results: list[dict], out_root: Path) -> Path:
    lines = [f"# {campaign.name}: clips to post", ""]
    for label, url in (("Brief", campaign.brief_url), ("Campaign", campaign.whop_url)):
        if url:
            lines.append(f"{label}: {url}  ")
    lines += ["", "Watch every clip before posting. Post from your own account, then submit the post link on the "
              "campaign page. Hook research: `hook_playbook.md`.", ""]
    for r in sorted(results, key=lambda r: -r["virality_score"]):
        lines.append(f"## [{r['virality_score']}/10] {r['title']}")
        lines.append(f"- File: `{r['file']}` ({r['duration']}s)")
        lines.append(f"- On-screen text: {r['hook']}")
        lines.append(f"- Angle: {r['angle']}")
        lines.append(f"- Why: {r['why']}")
        lines.append(f"- Rule check: {r['rule_check']}")
        for p in campaign.platforms:
            lines.append(f"- [ ] Posted to {p}  link: ")
        lines.append("- [ ] Submitted")
        lines.append("")
    path = out_root / "SUBMIT.md"
    path.write_text("\n".join(lines))
    (out_root / "manifest.json").write_text(json.dumps(results, indent=2, ensure_ascii=False))
    return path


def cmd_brief(url: str, campaigns_dir: Path) -> Path:
    from .brief import extract_campaign, fetch_brief

    print(f"Reading brief {url}")
    doc = fetch_brief(url)
    print(f"   {len(doc.text)} characters, {len(doc.links)} links")
    campaign = extract_campaign(doc)
    path = save_campaign(campaign, campaigns_dir / f"{campaign.slug}.toml")
    (campaigns_dir / f"{campaign.slug}.brief.txt").write_text(doc.text)
    print(f"   campaign: {campaign.name}")
    print(f"   footage links: {len(campaign.sources)}, asset links: {len(campaign.asset_links)}")
    print(f"   saved {path}  (check it, especially rules and caption lines)")
    return path


def cmd_run(campaign_path: Path, args) -> int:
    require_ffmpeg()
    campaign = load_campaign(campaign_path)
    sources = [*campaign.sources, *args.source]
    if not sources:
        raise SystemExit("no sources: add `sources = [...]` to the campaign file or pass --source")
    work = Path(args.work)
    out_root = Path(args.out) / campaign.slug
    out_root.mkdir(parents=True, exist_ok=True)

    logo = prepare_assets(campaign, work, args.interactive)

    playbook = ""
    if not args.no_research and args.command != "render":
        from .research import research_hooks

        pb_path = out_root / "hook_playbook.md"
        print("\n== researching hooks" + (" (cached)" if pb_path.exists() and not args.refresh_research else ""))
        try:
            playbook = research_hooks(campaign, pb_path, refresh=args.refresh_research)
            print(f"   playbook: {pb_path}")
        except Exception as e:
            print(f"   research FAILED, continuing without it: {e}", file=sys.stderr)

    results: list[dict] = []
    for link in sources:
        print(f"\n== footage {link}")
        try:
            files = fetch_link(link, work, args.interactive)
        except Exception as e:
            print(f"   FAILED: {e}", file=sys.stderr)
            continue
        videos = [f for f in files if f.suffix.lower() in VIDEO_EXT]
        print(f"   {len(videos)} video(s)")
        for video in videos:
            try:
                results += process_video(campaign, video, video.name, args, out_root, playbook, logo)
            except Exception as e:  # keep going with other videos
                print(f"   FAILED on {video.name}: {e}", file=sys.stderr)

    if results:
        print(f"\nDone. {len(results)} clips. Checklist: {write_checklist(campaign, results, out_root)}")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="clipper", description="Clip official footage for paid clipping campaigns.")
    p.add_argument("command", choices=["go", "brief", "run", "render"])
    p.add_argument("target", help="Brief link (go/brief) or campaign TOML file (run/render)")
    p.add_argument("--source", action="append", default=[], help="Extra footage link/path (repeatable)")
    p.add_argument("--campaigns", default="campaigns", help="Where `brief` saves campaign files")
    p.add_argument("--out", default="out", help="Output directory (default: out)")
    p.add_argument("--work", default="work", help="Download/transcript cache (default: work)")
    p.add_argument("--whisper-model", default="small", help="faster-whisper model: tiny/base/small/medium/large-v3")
    p.add_argument("--plan-only", action="store_true", help="Stop after planning so you can edit plan.json")
    p.add_argument("--repick", action="store_true", help="Plan again even if plan.json exists")
    p.add_argument("--no-research", action="store_true", help="Skip web research of hooks")
    p.add_argument("--refresh-research", action="store_true", help="Redo hook research even if cached")
    p.add_argument("--interactive", action="store_true",
                   help="If a download link can't be handled automatically, open a browser window for you")
    args = p.parse_args(argv)

    if args.command in ("go", "brief"):
        path = cmd_brief(args.target, Path(args.campaigns))
        if args.command == "brief":
            return 0
        args.command = "run"
        return cmd_run(path, args)
    return cmd_run(Path(args.target), args)


if __name__ == "__main__":
    raise SystemExit(main())
