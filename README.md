# Clipper

Paste a clipping campaign's brief link. The agent reads the brief, downloads the official footage and logo,
researches which hooks are getting reach right now, and renders vertical clips that follow every rule in the brief.

```
clipper go "https://docs.google.com/document/d/.../mobilebasic"
```

```
brief link (Google Doc, Notion, any page)
  -> Claude extracts rules, caption requirements, footage + logo links   campaigns/<name>.toml
  -> downloads footage and assets (Drive, YouTube, Frame.io, MediaSilo, Dropbox...)
  -> Claude researches current hooks with web search                      out/<name>/hook_playbook.md
  -> per video: transcript (Whisper) + keyframes (scene changes)
  -> Claude plans clips: spans, on-screen text, caption, angle            out/<name>/<video>/plan.json
  -> ffmpeg renders 1080x1920 with on-screen text, subtitles, logo        out/<name>/<video>/*.mp4
  -> caption files in the brief's required format + SUBMIT.md checklist
```

Posting and submitting stay with you: watch every clip, post from your own account, submit the link.

## Setup

Needs Python 3.11+ and `ffmpeg`.

```bash
python -m venv .venv && . .venv/bin/activate
pip install -e ".[dev]"
playwright install chromium        # headless browser for Frame.io / MediaSilo / Notion pages
export ANTHROPIC_API_KEY=sk-ant-...
```

The first run also downloads the Whisper speech model (`small`, about 500 MB).

## Use

```bash
clipper go <brief-link>                    # everything in one go
```

Or step by step:

```bash
clipper brief <brief-link>                 # creates campaigns/<name>.toml; check rules and caption lines
clipper run campaigns/<name>.toml --plan-only
# edit out/<name>/<video>/plan.json if you want (spans, on-screen text, captions, drop clips)
clipper render campaigns/<name>.toml
```

Open `out/<name>/SUBMIT.md`: clips sorted by score, each with its on-screen text, angle, why it stands out, the rule
check, and a `.txt` with the post caption.

### What each part does

**Brief reading.** Google Docs are read directly; other pages are opened in a headless browser. Claude pulls out
footage links, logo links, background links, every rule and ban, the exact required caption lines (mentions, song
credits), the disclosure tag, the hashtag limit, example on-screen text, and whether a watermark is required.

**Downloads.** Each link is tried with Google Drive, then yt-dlp, then a headless browser that clicks the page's
Download buttons. Zips are unpacked. If a page needs a login or isn't recognised, `--interactive` opens a browser
window: download the files yourself and the agent picks them up. You can also put local file paths in `sources`.

**Hook research.** Claude runs web searches for what's working on the target platforms now, how the launch and
niche are being talked about, and what the saturated angle will be (what most clippers will post), then writes a
playbook of hook patterns with ready-to-use lines for this campaign. Everything is filtered through the campaign
rules (for example, no exaggerated hooks or false claims). It's cached; `--refresh-research` redoes it.
Web search finds articles, guides and reports. It can't read view counts on TikTok directly, so treat the
playbook as informed guidance, and the playbook says where evidence is thin.

**Clip planning.** Claude sees the brief, the playbook, keyframes from the footage and the transcript, so
music-only trailers work as well as talking videos. A clip can join up to 4 spans (strongest shots first) or be one
continuous moment. Each clip gets a different angle so they don't look like spam.

**Rendering.** 1080x1920, blurred background (`layout = "blur"`) or fill (`"crop"`), on-screen text, word-by-word
subtitles only when there's dialogue (`subtitles = "auto"`), and the campaign logo (white variant preferred).
Original audio only: nothing is added.

**Captions.** Written as: caption text, required lines, the disclosure tag on its own line, then up to the allowed
number of hashtags.

### Options

| Flag | |
|---|---|
| `--interactive` | Open a browser window when a download link can't be handled automatically |
| `--plan-only` / `--repick` | Stop after planning / plan again |
| `--no-research` / `--refresh-research` | Skip / redo hook research |
| `--source PATH_OR_URL` | Add footage without editing the campaign file |
| `--whisper-model` | `tiny`/`base` faster, `medium`/`large-v3` more accurate (default `small`) |

## Cost

Claude (`claude-opus-5-5`): one request to read the brief, one research request with up to 12 searches, and one
planning request per video (keyframe images included). Typically well under a dollar per campaign. Transcription
and rendering run on your computer for free.

## Tests

```bash
pytest
```
