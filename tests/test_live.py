import sys

import pytest

from autoclip.android import AndroidScreenSource, screen_source
from autoclip.config import ClipConfig, DetectConfig, LiveConfig
from autoclip.live import (
    LiveClipper,
    SegmentRecorder,
    audio_input,
    open_source,
    resolve_source,
)
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


def test_audio_device_adds_a_second_input():
    args, _ = resolve_source("screen", audio_device="pulse:@DEFAULT_MONITOR@")
    assert args.count("-i") == 2
    assert args[-4:] == ["-f", "pulse", "-i", "@DEFAULT_MONITOR@"]


def test_audio_input_uses_platform_default_format(monkeypatch):
    monkeypatch.setattr(sys, "platform", "linux")
    assert audio_input("my-monitor") == ["-f", "pulse", "-i", "my-monitor"]


def test_audio_device_maps_the_second_input():
    from autoclip.live import CommandSource

    source = CommandSource("screen", audio_device="pulse:default")
    assert source.maps() == ["-map", "0:v:0?", "-map", "1:a:0?"]


def test_audio_device_can_come_from_the_environment(monkeypatch):
    monkeypatch.setenv("AUTOCLIP_AUDIO_DEVICE", "pulse:monitor")
    source = open_source("screen")
    assert source.audio_device == "pulse:monitor"
    assert source.maps() == ["-map", "0:v:0?", "-map", "1:a:0?"]


def test_environment_audio_device_is_ignored_for_files(tmp_path, monkeypatch):
    monkeypatch.setenv("AUTOCLIP_AUDIO_DEVICE", "pulse:monitor")
    video = tmp_path / "input.mp4"
    video.write_bytes(b"")
    source = open_source(str(video))
    assert source.maps() == ["-map", "0:v:0?", "-map", "0:a:0?"]


def test_android_screen_uses_the_projection_bridge(monkeypatch):
    monkeypatch.setenv("ANDROID_ARGUMENT", "/data/user/0/org.liveclip")
    source = open_source("screen")
    assert isinstance(source, AndroidScreenSource)


def test_android_camera_is_rejected_with_a_clear_message(monkeypatch):
    monkeypatch.setenv("ANDROID_ARGUMENT", "/data/user/0/org.liveclip")
    with pytest.raises(MediaError, match="camera capture is not wired"):
        open_source("camera")


def test_android_capture_feeds_ffmpeg_from_sockets(tmp_path):
    source = screen_source(socket_dir=tmp_path, with_audio=False)
    args = source.input_args()
    assert args[:3] == ["-f", "h264", "-r"]
    assert args[-1] == f"unix://{tmp_path / 'liveclip_v.sock'}"
    assert source.maps() == ["-map", "0:v:0?"]


def test_android_capture_adds_audio_socket_when_available(tmp_path):
    source = screen_source(socket_dir=tmp_path)
    source._has_audio = True
    args = source.input_args()
    assert "-f" in args and "s16le" in args
    assert args[-1] == f"unix://{tmp_path / 'liveclip_a.sock'}"
    assert source.maps() == ["-map", "0:v:0?", "-map", "1:a:0?"]


def test_android_source_stops_cleanly_without_starting(tmp_path):
    source = screen_source(socket_dir=tmp_path)
    source.stop()
    assert source._capture is None


def test_live_session_cuts_clips_while_recording(sample_video, tmp_path):
    events = []
    clipper = LiveClipper(
        str(sample_video),
        tmp_path / "out",
        detect=DetectConfig(min_gap=4.0),
        clip=ClipConfig(
            pre_roll=1.5,
            post_roll=1.5,
            min_length=1.5,
            max_length=6.0,
            width=270,
            height=480,
            preset="ultrafast",
            fade=0.0,
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
