# Whop clipper

An agent that turns long-form videos into short, campaign-compliant clips for
[Whop Content Rewards](https://whop.com) clipping campaigns.

```
campaign brief (TOML) + source videos
  -> download (yt-dlp)
  -> word-level transcript (faster-whisper, local)
  -> Claude picks the best moments that follow the campaign's rules
  -> 1080x1920 clips with burned-in word-by-word captions and an opening hook (ffmpeg)
  -> post captions/hashtags + SUBMIT.md checklist
```

Posting the clips and submitting post links on Whop stays manual: you review each clip before it goes out
under your account.

## Setup

Needs Python 3.11+ and `ffmpeg` on your PATH.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
export ANTHROPIC_API_KEY=sk-ant-...
```

The first run downloads the Whisper model (`small` by default, about 500 MB).

## Use

1. Copy `campaigns/example.toml` to `campaigns/<campaign>.toml` and fill it in from the campaign page on Whop:
   clip length, rules, banned content, required hashtags/mentions, and the source video links.
2. Run it:

   ```bash
   clipper run campaigns/<campaign>.toml
   ```

3. Open `out/<campaign>/SUBMIT.md`. For each clip there's the video, a `.txt` with the title/caption/hashtags,
   Claude's reason for picking it and its rule check, plus a checkbox per platform.

### Editing Claude's picks

```bash
clipper run campaigns/<campaign>.toml --plan-only   # stop after selection
# edit out/<campaign>/<source-id>/plan.json (change start/end, titles, drop clips)
clipper render campaigns/<campaign>.toml            # render from the edited plans
```

`plan.json` is reused on later runs; pass `--repick` to ask Claude again.

### Options

| Flag | Default | |
|---|---|---|
| `--source URL` | | Add a source on the command line (repeatable) |
| `--whisper-model` | `small` | `tiny`/`base` are faster, `medium`/`large-v3` more accurate |
| `--out` / `--work` | `out` / `work` | Output and download/transcript cache directories |

Campaign file settings: `layout = "blur"` puts the full frame over a blurred background, `"crop"` centre-crops to
fill the screen (better for a single talking head). `burn_captions` and `show_hook` toggle the overlays.

## How clip selection works

`clipper/picker.py` sends the campaign brief and the timestamped transcript to Claude (`claude-opus-5-5`, structured
output). The prompt puts rule compliance first, then looks for a hook in the first two seconds, a clip that stands
alone, and a payoff before the cut. Picks are then snapped to word boundaries, clamped to the campaign's length limits,
and de-overlapped. Server-side refusal fallback (`fallbacks: "default"`) is on, so a declined request is retried on
another model automatically.

Cost: one request per source video. A one-hour transcript is about 15k input tokens.

## Tests

```bash
pytest
```
