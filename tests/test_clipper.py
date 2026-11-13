import json
import subprocess
from pathlib import Path

import pytest

from clipper.brief import BriefDoc, extract_campaign, google_doc_id, html_to_text, unwrap_link
from clipper.campaign import Campaign, load_campaign, save_campaign
from clipper.cli import post_text
from clipper.frames import keyframes, probe
from clipper.picker import ClipPick, Span, fit_clips, load_plan, snap_to_words
from clipper.render import ass_time, build_ass, chunk_words, render_clip, words_on_timeline
from clipper.source import _expand, pick_logo
from clipper.transcribe import Word

ROOT = Path(__file__).resolve().parents[1]


def words_every(n: int, step: float = 0.5) -> list[Word]:
    return [Word(i * step, i * step + step * 0.8, f"w{i}") for i in range(n)]


def pick(*spans, **kw) -> ClipPick:
    base = dict(title="t", hook="h", caption="c", hashtags=["#a"], subtitles=True, angle="x",
                virality_score=5, why="y", rule_check="ok")
    return ClipPick(spans=[Span(start=s, end=e) for s, e in spans], **{**base, **kw})


def make_video(path: Path, seconds: int = 6, size: str = "1280x720", audio: bool = True) -> Path:
    cmd = ["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", f"testsrc2=size={size}:rate=30"]
    if audio:
        cmd += ["-f", "lavfi", "-i", "sine=frequency=440"]
    cmd += ["-t", str(seconds), *(["-shortest"] if audio else []), str(path)]
    subprocess.run(cmd, check=True)
    return path


def dims(path: Path) -> str:
    out = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries", "stream=width,height",
                          "-of", "csv=p=0", str(path)], capture_output=True, text=True)
    return out.stdout.strip()


# --- campaign / brief ---

def test_example_campaign_loads():
    c = load_campaign(ROOT / "campaigns" / "example.toml")
    assert c.slug == "example-podcast-clips" and c.disclosure_tag == "#Ad"


def test_campaign_rejects_bad_lengths():
    with pytest.raises(ValueError):
        Campaign(name="x", min_seconds=60, max_seconds=30)


def test_campaign_toml_round_trip(tmp_path):
    c = Campaign(name="Warzone S1", required_caption_lines=['🎵 Agust D - “Haegeum”', 'say "hi"\\'],
                 max_extra_hashtags=3, sources=["https://x.y/z?a=1&b=2"])
    assert load_campaign(save_campaign(c, tmp_path / "c.toml")) == c


def test_google_links():
    assert google_doc_id("https://docs.google.com/document/u/0/d/1upUZF4TCmqv6AiebqL1fTBZ-P9Ju9fn6QDZznPoG46M/mobilebasic") \
        == "1upUZF4TCmqv6AiebqL1fTBZ-P9Ju9fn6QDZznPoG46M"
    assert unwrap_link("https://www.google.com/url?q=https://f.io/NsqLMqR0&amp;sa=D") == "https://f.io/NsqLMqR0"


def test_html_to_text_keeps_lines_and_links():
    text, links = html_to_text('<p>Rule one</p><p>Logo: <a href="https://www.google.com/url?q=https://f.io/abc&amp;sa=D">here</a></p>')
    assert text.splitlines() == ["Rule one", "Logo: here"]
    assert links == ["https://f.io/abc"]


# --- planning ---

def test_snap_extends_to_word_edges():
    s, e = snap_to_words(1.2, 2.7, words_every(10), pad=0)
    assert (s, e) == (1.0, pytest.approx(2.9))


def test_fit_clips_length_and_duplicates():
    words = words_every(400)  # 200 s of speech
    clips = [pick((0, 30)), pick((2, 31)), pick((50, 52)), pick((60, 200)), pick((100, 110), (5, 15))]
    out = fit_clips(clips, words, min_s=15, max_s=60)
    assert [round(c.duration) for c in out] == [30, 60, 20]  # dup dropped, short dropped, long trimmed
    assert out[2].spans[0].start > out[2].spans[1].start  # span order kept


def test_fit_clips_music_only():
    out = fit_clips([pick((3.2, 7.9), (12.0, 19.5))], [], 5, 30, source_duration=18.0)
    assert out[0].spans[-1].end == 18.0


def test_load_old_plan_format(tmp_path):
    p = tmp_path / "plan.json"
    p.write_text(json.dumps([{"start": 1, "end": 20, "title": "t", "hook": "h", "caption": "c", "hashtags": [],
                              "virality_score": 5, "why": "y", "rule_check": "ok"}]))
    assert load_plan(p)[0].duration == 19


# --- captions ---

def test_post_text_follows_brief_order():
    c = Campaign(name="x", required_caption_lines=["@callofduty", "Season 1 out now"], disclosure_tag="#Ad",
                 max_extra_hashtags=3)
    clip = pick((0, 20), caption="Hajin looks unreal #warzone", hashtags=["#Warzone", "#Ad", "#CoD", "#Gaming", "#Extra"])
    text = post_text(clip, c).split("CAPTION:\n")[1]
    assert text.splitlines() == ["Hajin looks unreal", "@callofduty", "Season 1 out now", "", "#Ad",
                                 "#Warzone #CoD #Gaming"]


# --- rendering ---

def test_chunking_breaks_on_pause_and_punctuation():
    ws = [Word(0, 0.2, "Hi."), Word(0.3, 0.5, "this"), Word(0.6, 0.8, "is"), Word(2.0, 2.2, "late")]
    assert [len(c) for c in chunk_words(ws)] == [1, 2, 1]


def test_words_on_timeline_joins_spans():
    out = words_on_timeline(words_every(20), [(5.0, 6.0), (1.0, 2.0)])
    assert [w.text for w in out] == ["w10", "w11", "w2", "w3"]
    assert out[2].start == pytest.approx(1.0)


def test_ass_hook_and_words():
    ass = build_ass(words_every(3), duration=4.0, hook="Wait for it")
    assert "Wait for it" in ass and ass_time(4.0) in ass and "W1" in ass


def test_render_spans_logo_no_audio(tmp_path):
    src = make_video(tmp_path / "src.mp4", seconds=8, audio=False)
    logo = tmp_path / "logo.png"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "color=white:size=400x200",
                    "-frames:v", "1", str(logo)], check=True)
    out = render_clip(src, [(4.0, 6.0), (1.0, 2.5)], tmp_path / "clip.mp4", [], has_audio=False,
                      hook="Hook", logo=logo)
    assert dims(out) == "1080,1920"
    assert probe(out)["duration"] == pytest.approx(3.5, abs=0.15)


def test_render_with_audio_and_subtitles(tmp_path):
    src = make_video(tmp_path / "src.mp4", seconds=6, size="720x1280")
    out = render_clip(src, [(1.0, 4.0)], tmp_path / "c.mp4", words_every(12), layout="crop", hook="H")
    assert dims(out) == "1080,1920" and probe(out)["has_audio"]


def test_keyframes(tmp_path):
    frames = keyframes(make_video(tmp_path / "v.mp4", seconds=10), tmp_path / "frames", max_frames=8)
    assert 3 <= len(frames) <= 8 and all(p.exists() for _, p in frames)


# --- media helpers ---

def test_zip_expansion_blocks_traversal(tmp_path):
    import zipfile
    z = tmp_path / "a.zip"
    with zipfile.ZipFile(z, "w") as f:
        f.writestr("logos/COD_WZ_WHT.png", b"x")
        f.writestr("../evil.png", b"x")
        f.writestr("notes.txt", b"x")
    files = _expand([z], tmp_path)
    assert [p.name for p in files] == ["COD_WZ_WHT.png"]
    assert not (tmp_path.parent / "evil.png").exists()


def test_pick_logo_prefers_white():
    assert pick_logo([Path("COD_WZ_Stacked_BLK.png"), Path("COD_WZ_Stacked_WHT.png")]).name == "COD_WZ_Stacked_WHT.png"


# --- Claude request shape (mocked) ---

def test_extract_campaign_request(monkeypatch):
    import anthropic
    import httpx2

    seen = {}
    draft = dict(name="Warzone S1", creator="Call of Duty", platforms=["tiktok"], sources=["https://m/x"],
                 asset_links=["https://f.io/a"], context_links=[], min_seconds=10, max_seconds=60, rules=["r"],
                 banned=["b"], required_caption_lines=["@callofduty"], disclosure_tag="#Ad", max_extra_hashtags=3,
                 on_screen_text_examples=[], watermark_required=True, burn_subtitles="auto", language="en", notes="")

    def handler(req):
        seen["body"] = json.loads(req.content)
        return httpx2.Response(200, json={"id": "m", "type": "message", "role": "assistant", "model": "claude-opus-5-5",
                                          "content": [{"type": "text", "text": json.dumps(draft)}],
                                          "stop_reason": "end_turn", "stop_sequence": None,
                                          "usage": {"input_tokens": 1, "output_tokens": 1}})

    client = anthropic.Anthropic(api_key="x", http_client=anthropic.DefaultHttpxClient(transport=httpx2.MockTransport(handler)))
    c = extract_campaign(BriefDoc(url="u", text="brief", links=["https://f.io/a"]), client=client)
    assert c.watermark_required and c.disclosure_tag == "#Ad" and c.brief_url == "u"
    assert seen["body"]["fallbacks"] == "default"
