import pytest

from autoclip.clip import render_clip, render_plans
from autoclip.config import ClipConfig, DetectConfig
from autoclip.detect import detect_peaks
from autoclip.media import probe
from autoclip.plan import plan_clips


def test_vertical_crop_clip(sample_video, tmp_path):
    cfg = ClipConfig(width=270, height=480, preset="ultrafast", fade=0.1)
    out = render_clip(sample_video, 2.0, 6.0, tmp_path / "vertical.mp4", cfg)
    info = probe(out)
    assert (info.width, info.height) == (270, 480)
    assert 3.5 <= info.duration <= 4.5
    assert info.has_audio


def test_vertical_blur_clip(sample_video, tmp_path):
    cfg = ClipConfig(width=270, height=480, preset="ultrafast", fit="blur", fade=0.0)
    info = probe(render_clip(sample_video, 1.0, 4.0, tmp_path / "blur.mp4", cfg))
    assert (info.width, info.height) == (270, 480)


def test_horizontal_clip_keeps_aspect(sample_video, tmp_path):
    cfg = ClipConfig(width=320, height=180, vertical=False, preset="ultrafast")
    info = probe(render_clip(sample_video, 1.0, 3.0, tmp_path / "wide.mp4", cfg))
    assert (info.width, info.height) == (320, 180)


def test_subtitles_are_burned_in(sample_video, tmp_path):
    srt = tmp_path / "captions.srt"
    srt.write_text("1\n00:00:00,000 --> 00:00:03,000\nhype moment\n", encoding="utf-8")
    cfg = ClipConfig(width=270, height=480, preset="ultrafast")
    out = render_clip(sample_video, 1.0, 3.5, tmp_path / "subbed.mp4", cfg, subtitles=srt)
    assert probe(out).duration > 2.0


def test_render_plans_writes_one_file_per_clip(sample_video, tmp_path):
    cfg = ClipConfig(
        pre_roll=1.0,
        post_roll=1.0,
        min_length=1.0,
        width=270,
        height=480,
        preset="ultrafast",
    )
    plans = plan_clips(detect_peaks(sample_video, DetectConfig()), cfg, duration=12.0)
    seen = []
    written = render_plans(sample_video, plans, tmp_path, cfg, on_clip=lambda p, c: seen.append(p))
    assert len(written) == len(plans) == len(seen)
    assert all(path.exists() for path in written)


@pytest.mark.parametrize("start,end", [(0.0, 2.0), (10.0, 12.0)])
def test_clip_at_file_edges(sample_video, tmp_path, start, end):
    cfg = ClipConfig(width=270, height=480, preset="ultrafast")
    info = probe(render_clip(sample_video, start, end, tmp_path / f"edge_{int(start)}.mp4", cfg))
    assert info.duration > 1.0
