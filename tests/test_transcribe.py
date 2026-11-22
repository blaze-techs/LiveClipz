import json
import threading
from http.server import BaseHTTPRequestHandler, HTTPServer

import pytest

from autoclip import transcribe
from autoclip.transcribe import AsrConfig, Cue, TranscribeError


class _Handler(BaseHTTPRequestHandler):
    payload: dict = {}
    status: int = 200
    seen: dict = {}

    def do_POST(self):  # noqa: N802 (http.server API)
        length = int(self.headers.get("Content-Length", "0"))
        body = self.rfile.read(length)
        type(self).seen = {
            "path": self.path,
            "auth": self.headers.get("Authorization"),
            "body": body,
        }
        data = json.dumps(self.payload).encode()
        self.send_response(self.status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def log_message(self, *args):
        pass


@pytest.fixture
def asr_server():
    handler = type("Handler", (_Handler,), {"payload": {}, "status": 200, "seen": {}})
    server = HTTPServer(("127.0.0.1", 0), handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    yield handler, f"http://127.0.0.1:{server.server_port}/v1"
    server.shutdown()
    server.server_close()


def test_config_defaults_to_groq(monkeypatch):
    monkeypatch.setenv("AUTOCLIP_ASR_KEY", "k")
    monkeypatch.delenv("AUTOCLIP_ASR_URL", raising=False)
    monkeypatch.delenv("AUTOCLIP_ASR_PRESET", raising=False)
    config = transcribe.config_from_env()
    assert config.url == "https://api.groq.com/openai/v1/audio/transcriptions"
    assert config.model == "whisper-large-v3-turbo"


def test_config_accepts_a_full_endpoint(monkeypatch):
    monkeypatch.setenv("AUTOCLIP_ASR_KEY", "k")
    monkeypatch.setenv("AUTOCLIP_ASR_URL", "https://example.test/v1/audio/transcriptions")
    monkeypatch.setenv("AUTOCLIP_ASR_MODEL", "custom")
    config = transcribe.config_from_env()
    assert config.url == "https://example.test/v1/audio/transcriptions"
    assert config.model == "custom"


def test_config_requires_a_key(monkeypatch):
    monkeypatch.delenv("AUTOCLIP_ASR_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(TranscribeError, match="AUTOCLIP_ASR_KEY"):
        transcribe.config_from_env()


def test_transcribe_posts_the_audio_and_reads_segments(asr_server, tmp_path, monkeypatch):
    handler, url = asr_server
    handler.payload = {
        "text": "hello world",
        "segments": [
            {"start": 0.0, "end": 1.5, "text": " hello"},
            {"start": 1.5, "end": 3.25, "text": " world "},
        ],
    }
    monkeypatch.setenv("AUTOCLIP_ASR_KEY", "secret-key")
    monkeypatch.setenv("AUTOCLIP_ASR_URL", url)
    audio = tmp_path / "clip.wav"
    audio.write_bytes(b"RIFFfake")

    cues = transcribe.transcribe(audio, duration=3.25)

    assert [c.text for c in cues] == ["hello", "world"]
    assert cues[1].start == 1.5 and cues[1].end == 3.25
    assert handler.seen["path"] == "/v1/audio/transcriptions"
    assert handler.seen["auth"] == "Bearer secret-key"
    body = handler.seen["body"]
    assert b'name="model"' in body and b"whisper" in body
    assert b'name="file"; filename="clip.wav"' in body
    assert b"RIFFfake" in body


def test_transcribe_falls_back_to_the_plain_text(asr_server, tmp_path, monkeypatch):
    handler, url = asr_server
    handler.payload = {"text": "one long line"}
    monkeypatch.setenv("AUTOCLIP_ASR_KEY", "k")
    monkeypatch.setenv("AUTOCLIP_ASR_URL", url)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    cues = transcribe.transcribe(audio, duration=7.0)
    assert len(cues) == 1
    assert cues[0].text == "one long line"
    assert cues[0].end == 7.0


def test_transcribe_reports_http_errors(asr_server, tmp_path, monkeypatch):
    handler, url = asr_server
    handler.status = 401
    handler.payload = {"error": "bad key"}
    monkeypatch.setenv("AUTOCLIP_ASR_KEY", "k")
    monkeypatch.setenv("AUTOCLIP_ASR_URL", url)
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    with pytest.raises(TranscribeError, match="401"):
        transcribe.transcribe(audio)


def test_write_srt_formats_timestamps(tmp_path):
    path = transcribe.write_srt(
        [Cue(0.0, 1.5, "first"), Cue(61.25, 62.0, "second")],
        tmp_path / "out.srt",
    )
    text = path.read_text()
    assert "1\n00:00:00,000 --> 00:00:01,500\nfirst" in text
    assert "2\n00:01:01,250 --> 00:01:02,000\nsecond" in text


def _bottom_band_max_luma(path, at=2.0):
    """Brightest pixel in the lower sixth: captions live there."""
    import subprocess

    from autoclip.media import ffmpeg_bin

    graph = "crop=iw:ih/6:0:ih*5/6,signalstats,metadata=print:file=-"
    result = subprocess.run(
        [
            ffmpeg_bin(),
            "-v",
            "error",
            "-ss",
            str(at),
            "-i",
            str(path),
            "-frames:v",
            "1",
            "-vf",
            graph,
            "-f",
            "null",
            "-",
        ],
        capture_output=True,
        text=True,
        check=True,
    )
    values = [float(line.split("=")[1]) for line in result.stdout.splitlines() if "YMAX" in line]
    return max(values) if values else 0.0


def test_clip_can_burn_in_automatic_captions(asr_server, sample_video, tmp_path, monkeypatch):
    from autoclip import clip
    from autoclip.config import ClipConfig
    from autoclip.media import probe

    handler, url = asr_server
    handler.payload = {"segments": [{"start": 0.0, "end": 2.0, "text": "hype moment"}]}
    monkeypatch.setenv("AUTOCLIP_ASR_KEY", "k")
    monkeypatch.setenv("AUTOCLIP_ASR_URL", url)

    target = tmp_path / "captioned.mp4"
    clip.render_clip(sample_video, 1.0, 4.0, target, ClipConfig(), captions=True)

    assert target.exists() and target.stat().st_size > 0
    srt = target.with_suffix(".srt")
    assert srt.exists() and "hype moment" in srt.read_text()
    assert b"RIFF" in handler.seen["body"]
    assert (probe(target).width, probe(target).height) == (1080, 1920)


def test_captions_are_drawn_in_the_lower_band(asr_server, tmp_path, monkeypatch):
    import subprocess

    from autoclip import clip
    from autoclip.config import ClipConfig
    from autoclip.media import ffmpeg_bin

    dark = tmp_path / "dark.mp4"
    subprocess.run(
        [
            ffmpeg_bin(),
            "-v",
            "error",
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x101010:s=640x360:r=30:d=6",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=220:duration=6",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-shortest",
            str(dark),
        ],
        check=True,
    )

    handler, url = asr_server
    handler.payload = {"segments": [{"start": 0.0, "end": 5.0, "text": "drawn here"}]}
    monkeypatch.setenv("AUTOCLIP_ASR_KEY", "k")
    monkeypatch.setenv("AUTOCLIP_ASR_URL", url)

    plain = tmp_path / "plain.mp4"
    clip.render_clip(dark, 0.0, 5.0, plain, ClipConfig())
    captioned = tmp_path / "captioned.mp4"
    clip.render_clip(dark, 0.0, 5.0, captioned, ClipConfig(), captions=True)

    assert _bottom_band_max_luma(plain, at=1.0) < 80
    assert _bottom_band_max_luma(captioned, at=1.0) > 200
    assert not plain.with_suffix(".srt").exists()


def test_captions_without_a_key_are_an_error(sample_video, tmp_path, monkeypatch):
    from autoclip import clip
    from autoclip.config import ClipConfig

    monkeypatch.delenv("AUTOCLIP_ASR_KEY", raising=False)
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    with pytest.raises(TranscribeError, match="AUTOCLIP_ASR_KEY"):
        clip.render_clip(sample_video, 1.0, 4.0, tmp_path / "x.mp4", ClipConfig(), captions=True)


def test_asr_config_is_plain_data():
    config = AsrConfig(url="u", key="k", model="m")
    assert config.language is None and config.timeout > 0


def test_local_preset_needs_no_key(monkeypatch):
    monkeypatch.delenv("AUTOCLIP_ASR_KEY", raising=False)
    monkeypatch.setenv("AUTOCLIP_ASR_PRESET", "local")
    monkeypatch.setenv("AUTOCLIP_ASR_MODEL", "tiny")
    config = transcribe.config_from_env()
    assert config.provider == "local"
    assert config.url is None and config.model == "tiny"


def test_local_without_faster_whisper_explains(monkeypatch, tmp_path):
    import sys

    monkeypatch.setitem(sys.modules, "faster_whisper", None)
    config = AsrConfig(url=None, key="", model="tiny", provider="local")
    audio = tmp_path / "a.wav"
    audio.write_bytes(b"x")
    with pytest.raises(TranscribeError, match="faster-whisper"):
        transcribe.transcribe(audio, config)


def test_unknown_preset_is_reported(monkeypatch):
    monkeypatch.setenv("AUTOCLIP_ASR_KEY", "k")
    monkeypatch.setenv("AUTOCLIP_ASR_PRESET", "nope")
    monkeypatch.delenv("AUTOCLIP_ASR_URL", raising=False)
    with pytest.raises(TranscribeError, match="unknown AUTOCLIP_ASR_PRESET"):
        transcribe.config_from_env()
