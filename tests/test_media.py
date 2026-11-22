import pytest

from autoclip import media
from autoclip.media import MediaError


def test_probe_reads_the_sample(sample_video):
    info = media.probe(sample_video)
    assert info.duration == pytest.approx(12.0, abs=0.3)
    assert (info.width, info.height) == (320, 180)
    assert info.has_video and info.has_audio


def test_probe_without_ffprobe_uses_the_ffmpeg_banner(sample_video, monkeypatch):
    monkeypatch.setattr(media, "have_ffprobe", lambda: False)
    info = media.probe(sample_video)
    assert info.duration == pytest.approx(12.0, abs=0.3)
    assert (info.width, info.height) == (320, 180)
    assert info.has_video and info.has_audio


def test_probe_reports_a_missing_file(tmp_path, monkeypatch):
    monkeypatch.setattr(media, "have_ffprobe", lambda: False)
    with pytest.raises(MediaError):
        media.probe(tmp_path / "nope.mp4")


def test_android_lookup_prefers_the_bundled_binary(tmp_path, monkeypatch):
    binary = tmp_path / "libffmpegbin.so"
    binary.write_bytes(b"")
    monkeypatch.setenv("ANDROID_ARGUMENT", "/data/user/0/org.liveclip")
    monkeypatch.delenv("AUTOCLIP_FFMPEG", raising=False)
    monkeypatch.setattr(media, "_android_native_dir", lambda: tmp_path)
    assert media.ffmpeg_bin() == str(binary)
