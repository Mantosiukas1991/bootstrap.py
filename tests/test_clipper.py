import subprocess
from pathlib import Path

import pytest

from clipper.campaign import Campaign, load_campaign
from clipper.cli import post_text
from clipper.picker import ClipPick, fit_clips, snap_to_words
from clipper.render import ass_time, build_ass, chunk_words, render_clip
from clipper.transcribe import Word

ROOT = Path(__file__).resolve().parents[1]


def words_every(n: int, step: float = 0.5) -> list[Word]:
    return [Word(i * step, i * step + step * 0.8, f"w{i}") for i in range(n)]


def pick(start: float, end: float, **kw) -> ClipPick:
    base = dict(title="t", hook="h", caption="c", hashtags=["#a"], virality_score=5, why="y", rule_check="ok")
    return ClipPick(start=start, end=end, **{**base, **kw})


def test_example_campaign_loads():
    c = load_campaign(ROOT / "campaigns" / "example.toml")
    assert c.slug == "example-podcast-clips"
    assert "#examplepod" in c.brief_text()


def test_campaign_rejects_bad_lengths():
    with pytest.raises(ValueError):
        Campaign(name="x", min_seconds=60, max_seconds=30)


def test_snap_extends_to_word_edges():
    words = words_every(10)  # word 2 spans 1.0-1.4, word 5 spans 2.5-2.9
    s, e = snap_to_words(1.2, 2.7, words, pad=0)
    assert (s, e) == (1.0, pytest.approx(2.9))


def test_fit_clips_enforces_length_and_overlap():
    words = words_every(400)  # 200 s of speech
    clips = [pick(0, 30), pick(10, 40), pick(50, 52), pick(60, 200)]
    out = fit_clips(clips, words, min_s=15, max_s=60)
    assert len(out) == 2  # overlap dropped, too-short dropped
    assert out[1].start >= 59 and out[1].end - out[1].start <= 60.2


def test_chunking_breaks_on_pause_and_punctuation():
    ws = [Word(0, 0.2, "Hi."), Word(0.3, 0.5, "this"), Word(0.6, 0.8, "is"), Word(2.0, 2.2, "late")]
    assert [len(c) for c in chunk_words(ws)] == [1, 2, 1]


def test_ass_times_are_clip_relative():
    ass = build_ass(words_every(10), clip_start=1.0, clip_end=3.0, hook="Wait for it")
    assert ass_time(0) in ass and "Wait for it" in ass
    assert "W2" in ass and "W0" not in ass


def test_post_text_adds_required_bits():
    c = Campaign(name="x", required_hashtags=["#req"], required_mentions=["@me"], caption_cta="Link in bio")
    text = post_text(pick(0, 20), c)
    assert "#req" in text and "@me" in text and "Link in bio" in text


def test_render_produces_vertical_video(tmp_path):
    src = tmp_path / "src.mp4"
    subprocess.run(["ffmpeg", "-y", "-loglevel", "error", "-f", "lavfi", "-i", "testsrc=size=1280x720:rate=30",
                    "-f", "lavfi", "-i", "sine=frequency=440", "-t", "6", "-shortest", str(src)], check=True)
    out = render_clip(src, 1.0, 4.0, tmp_path / "clip.mp4", words_every(12), hook="Hook text")
    probe = subprocess.run(["ffprobe", "-v", "error", "-select_streams", "v:0", "-show_entries",
                            "stream=width,height", "-of", "csv=p=0", str(out)], capture_output=True, text=True)
    assert probe.stdout.strip() == "1080,1920"
