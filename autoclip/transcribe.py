"""Automatic captions: speech-to-text through any OpenAI-compatible endpoint.

Nothing here is tied to one vendor. Point ``AUTOCLIP_ASR_URL`` at an
OpenAI-compatible ``/audio/transcriptions`` service and set ``AUTOCLIP_ASR_KEY``;
Groq, OpenAI and DashScope all work with the same request.

Standard library only, so the packaged build gains no new dependency.
"""

from __future__ import annotations

import importlib.util
import json
import os
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from pathlib import Path

from .media import MediaError, ffmpeg_bin, run

PRESETS = {
    # name: (base url, model) -- all speak the OpenAI transcription API
    "groq": ("https://api.groq.com/openai/v1", "whisper-large-v3-turbo"),
    "openai": ("https://api.openai.com/v1", "whisper-1"),
    "together": ("https://api.together.xyz/v1", "openai/whisper-large-v3"),
    "deepinfra": ("https://api.deepinfra.com/v1/openai", "openai/whisper-large-v3"),
}
DEFAULT_PRESET = "groq"


class TranscribeError(MediaError):
    pass


@dataclass(frozen=True)
class Cue:
    start: float
    end: float
    text: str


@dataclass(frozen=True)
class AsrConfig:
    url: str | None
    key: str
    model: str
    language: str | None = None
    timeout: float = 180.0
    provider: str = "openai"  # openai | local


def config_from_env() -> AsrConfig:
    """Read the ASR settings, or explain what is missing."""
    preset = os.environ.get("AUTOCLIP_ASR_PRESET", DEFAULT_PRESET)
    if preset == "local":
        return AsrConfig(
            url=None,
            key="",
            model=os.environ.get("AUTOCLIP_ASR_MODEL", "base"),
            language=os.environ.get("AUTOCLIP_ASR_LANGUAGE") or None,
            provider="local",
        )
    key = os.environ.get("AUTOCLIP_ASR_KEY") or os.environ.get("OPENAI_API_KEY")
    if not key:
        raise TranscribeError(
            "no speech-to-text key: set AUTOCLIP_ASR_KEY (free tier at "
            "console.groq.com works out of the box)"
        )
    base = os.environ.get("AUTOCLIP_ASR_URL")
    model = os.environ.get("AUTOCLIP_ASR_MODEL")
    if not base:
        if preset not in PRESETS:
            raise TranscribeError(f"unknown AUTOCLIP_ASR_PRESET {preset!r}")
        base, preset_model = PRESETS[preset]
        model = model or preset_model
    url = (
        base
        if base.rstrip("/").endswith("/audio/transcriptions")
        else f"{base.rstrip('/')}/audio/transcriptions"
    )
    return AsrConfig(
        url=url,
        key=key,
        model=model or "whisper-1",
        language=os.environ.get("AUTOCLIP_ASR_LANGUAGE") or None,
    )


def _multipart(fields: dict[str, str], audio: Path) -> tuple[bytes, str]:
    boundary = f"----liveclip{uuid.uuid4().hex}"
    body = bytearray()
    for name, value in fields.items():
        body += (
            f'--{boundary}\r\nContent-Disposition: form-data; name="{name}"\r\n\r\n{value}\r\n'
        ).encode()
    body += (
        f"--{boundary}\r\n"
        f'Content-Disposition: form-data; name="file"; filename="{audio.name}"\r\n'
        "Content-Type: audio/wav\r\n\r\n"
    ).encode()
    body += audio.read_bytes()
    body += f"\r\n--{boundary}--\r\n".encode()
    return bytes(body), f"multipart/form-data; boundary={boundary}"


def _post(config: AsrConfig, audio: Path) -> dict:
    fields = {"model": config.model, "response_format": "verbose_json"}
    if config.language:
        fields["language"] = config.language
    body, content_type = _multipart(fields, audio)
    request = urllib.request.Request(
        config.url,
        data=body,
        headers={"Authorization": f"Bearer {config.key}", "Content-Type": content_type},
    )
    try:
        with urllib.request.urlopen(request, timeout=config.timeout) as response:
            payload = json.loads(response.read() or b"{}")
    except urllib.error.HTTPError as error:
        detail = error.read().decode("utf-8", "replace")[:300]
        raise TranscribeError(f"speech-to-text failed ({error.code}): {detail}") from error
    except (urllib.error.URLError, TimeoutError, json.JSONDecodeError) as error:
        raise TranscribeError(f"speech-to-text failed: {error}") from error
    if not isinstance(payload, dict):
        raise TranscribeError("speech-to-text returned an unexpected payload")
    return payload


def _cues_from_payload(payload: dict, duration: float) -> list[Cue]:
    cues: list[Cue] = []
    for segment in payload.get("segments") or []:
        text = str(segment.get("text") or "").strip()
        if not text:
            continue
        start = float(segment.get("start") or 0.0)
        end = float(segment.get("end") or start)
        cues.append(Cue(start=start, end=max(end, start + 0.2), text=text))
    if not cues:
        text = str(payload.get("text") or "").strip()
        if text:
            cues.append(Cue(start=0.0, end=max(duration, 1.0), text=text))
    return cues


def asr_ready() -> bool:
    """True when captions can run: a key is set or faster-whisper is installed."""
    if os.environ.get("AUTOCLIP_ASR_KEY") or os.environ.get("OPENAI_API_KEY"):
        return True
    return importlib.util.find_spec("faster_whisper") is not None


def _wav_samples(path: Path):
    """16 kHz mono PCM as float32, so faster-whisper never needs PyAV."""
    import wave

    import numpy as np

    with wave.open(str(path), "rb") as handle:
        if handle.getframerate() != 16000 or handle.getnchannels() != 1:
            raise TranscribeError("captions need 16 kHz mono audio")
        raw = handle.readframes(handle.getnframes())
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def _transcribe_local(audio: Path, config: AsrConfig) -> list[Cue]:
    """Offline captions through faster-whisper, when the user installed it."""
    try:
        from faster_whisper import WhisperModel
    except ImportError as error:
        raise TranscribeError(
            "offline captions need faster-whisper: pip install faster-whisper"
        ) from error
    samples = _wav_samples(audio)
    model = WhisperModel(config.model or "base", device="cpu", compute_type="int8")
    segments, _info = model.transcribe(
        samples, language=config.language, vad_filter=True, beam_size=1
    )
    cues = []
    for segment in segments:
        text = (segment.text or "").strip()
        if text:
            cues.append(Cue(start=float(segment.start), end=float(segment.end), text=text))
    return cues


def transcribe(audio: Path, config: AsrConfig | None = None, duration: float = 0.0) -> list[Cue]:
    config = config or config_from_env()
    if config.provider == "local":
        return _transcribe_local(Path(audio), config)
    return _cues_from_payload(_post(config, Path(audio)), duration)


def extract_audio(input_args: list[str], out_path: Path) -> Path:
    """Decode the window described by ``input_args`` into 16 kHz mono WAV."""
    out_path.parent.mkdir(parents=True, exist_ok=True)
    run(
        [
            "-v",
            "error",
            *input_args,
            "-vn",
            "-ac",
            "1",
            "-ar",
            "16000",
            "-c:a",
            "pcm_s16le",
            str(out_path),
        ]
    )
    return out_path


def format_timestamp(seconds: float) -> str:
    total_ms = max(0, int(round(seconds * 1000)))
    hours, rest = divmod(total_ms, 3_600_000)
    minutes, rest = divmod(rest, 60_000)
    secs, millis = divmod(rest, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def write_srt(cues: list[Cue], path: Path) -> Path:
    lines = []
    for number, cue in enumerate(cues, start=1):
        lines.append(str(number))
        lines.append(f"{format_timestamp(cue.start)} --> {format_timestamp(cue.end)}")
        lines.append(cue.text)
        lines.append("")
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("\n".join(lines), encoding="utf-8")
    return path


def captions_for(input_args: list[str], duration: float, srt_path: Path) -> Path:
    """Transcribe a clip window and write the matching SRT next to it."""
    wav = srt_path.with_suffix(".wav")
    try:
        extract_audio(input_args, wav)
        cues = transcribe(wav, duration=duration)
    finally:
        wav.unlink(missing_ok=True)
    if not cues:
        raise TranscribeError("no speech was recognised in this clip")
    return write_srt(cues, srt_path)


__all__ = [
    "PRESETS",
    "AsrConfig",
    "Cue",
    "TranscribeError",
    "asr_ready",
    "captions_for",
    "config_from_env",
    "extract_audio",
    "ffmpeg_bin",
    "format_timestamp",
    "transcribe",
    "write_srt",
]
