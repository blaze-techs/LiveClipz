import sys

import pytest

from autoclip.config import ClipConfig, DetectConfig, LiveConfig
from autoclip.live import LiveClipper, SegmentRecorder, resolve_source
from autoclip.media import MediaError, probe


def test_recorder_keeps_an_unbounded_segment_index(tmp_path):
    recorder = SegmentRecorder("screen", tmp_path, segment_seconds=2.0)
    command = recorder.command()
    assert "-segment_list_size" in command
    assert command[command.index("-segment_list_size") + 1] == "0"


def test_resolve_source_treats_plain_files_as_files(tmp_path):
    video = tmp_path / "input.mp4"
    video.write_bytes(b"")
    args, is_file = resolve_source(str(video))
    assert is_file
    assert args == ["-i", str(video)]


def test_resolve_source_maps_screen_capture():
    args, is_file = resolve_source("screen")
    assert not is_file
    expected = "x11grab" if sys.platform.startswith("linux") else None
    if expected:
        assert expected in args


def test_android_rejects_unimplemented_capture(monkeypatch):
    monkeypatch.setenv("ANDROID_ARGUMENT", "/data/user/0/org.liveclip")
    with pytest.raises(MediaError):
        resolve_source("screen")


def test_live_session_cuts_clips_while_recording(sample_video, tmp_path):
    events = []
    clipper = LiveClipper(
        str(sample_video),
        tmp_path / "out",
        detect=DetectConfig(min_gap=4.0),
        clip=ClipConfig(
            pre_roll=1.5, post_roll=1.5, min_length=1.5, max_length=6.0,
            width=270, height=480, preset="ultrafast", fade=0.0,
        ),
        live=LiveConfig(segment_seconds=2.0, buffer_seconds=40.0, tick=0.2, confirm_delay=0.0),
        on_event=events.append,
    )
    clips = clipper.run()

    assert len(clips) >= 2
    for path in clips:
        info = probe(path)
        assert (info.width, info.height) == (270, 480)
        assert info.duration > 1.0
    assert [e["type"] for e in events].count("clip") == len(clips)
    assert any(e["type"] == "state" and e["state"] == "live" for e in events)
    assert not [e for e in events if e["type"] == "error"]
