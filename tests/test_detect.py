import numpy as np

from autoclip.config import DetectConfig
from autoclip.detect import (
    detect_peaks,
    find_peaks,
    pcm_db,
    rolling_median,
    robust_z,
    score_series,
)

SR = 16000
HOP = 0.25


def _tone(duration: float, bursts: list[tuple[float, float]]) -> np.ndarray:
    t = np.arange(int(SR * duration)) / SR
    signal = 0.005 * np.sin(2 * np.pi * 220 * t)
    for start, end in bursts:
        gate = ((t > start) & (t < end)).astype(np.float64)
        signal = signal + 0.7 * gate * np.sin(2 * np.pi * 880 * t)
    return signal.astype(np.float32)


def test_pcm_db_tracks_loud_bursts():
    db = pcm_db(_tone(8.0, [(4.0, 5.0)]), SR, HOP)
    assert db.shape == (32,)
    assert db[17] > db[4] + 25


def test_pcm_db_handles_empty_audio():
    assert pcm_db(np.zeros(0, dtype=np.float32), SR, HOP).size == 0


def test_rolling_median_keeps_length_for_even_windows():
    x = np.arange(50, dtype=np.float64)
    for window in (2, 4, 7, 50, 99):
        assert rolling_median(x, window).shape == x.shape


def test_robust_z_ignores_constant_signal():
    assert np.allclose(robust_z(np.zeros(40), 9), 0.0)


def test_find_peaks_finds_separated_bursts():
    times = HOP * (np.arange(240) + 0.5)
    score = np.zeros_like(times)
    for centre in (20.0, 40.0):
        score[(times > centre - 0.5) & (times < centre + 0.5)] = 1.0
    idx = find_peaks(times, score, DetectConfig())
    assert len(idx) == 2
    assert abs(times[idx[0]] - 20.0) < 1.0
    assert abs(times[idx[1]] - 40.0) < 1.0


def test_find_peaks_is_silent_without_excitement():
    times = HOP * (np.arange(240) + 0.5)
    assert find_peaks(times, np.zeros_like(times), DetectConfig()) == []


def test_score_series_aligns_audio_and_motion():
    audio_times = HOP * (np.arange(120) + 0.5)
    audio = np.full(120, -50.0)
    audio[30:40] = -6.0
    motion_times = 0.25 * (np.arange(120) + 0.5)
    motion = np.zeros(120)
    motion[30:40] = 0.5
    times, score = score_series(audio, audio_times, motion, motion_times, DetectConfig())
    assert times.shape == score.shape
    assert score.max() > 0.5
    assert score[0] < 0.2


def test_detect_peaks_on_generated_video(sample_video):
    peaks = detect_peaks(sample_video, DetectConfig())
    times = [p.time for p in peaks]
    assert len(times) == 2
    assert abs(times[0] - 3.2) < 1.5
    assert abs(times[1] - 9.2) < 1.5
