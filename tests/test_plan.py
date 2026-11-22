from autoclip.config import ClipConfig
from autoclip.detect import Peak
from autoclip.plan import plan_clips

CFG = ClipConfig(pre_roll=2.0, post_roll=2.0, min_length=2.0, max_length=8.0, merge_gap=0.5)


def test_window_is_centred_on_the_peak():
    plans = plan_clips([Peak(10.0, 0.9)], CFG)
    assert len(plans) == 1
    assert plans[0].start == 8.0
    assert plans[0].end == 12.0
    assert plans[0].duration == 4.0


def test_window_is_clamped_to_the_file_bounds():
    plans = plan_clips([Peak(0.5, 0.8)], CFG, duration=60.0)
    assert plans[0].start == 0.0
    assert plans[0].end == 2.5


def test_overlapping_windows_merge_and_keep_best_score():
    plans = plan_clips([Peak(10.0, 0.4), Peak(11.0, 0.95)], CFG)
    assert len(plans) == 1
    assert plans[0].score == 0.95
    assert plans[0].peak == 11.0


def test_separated_peaks_stay_separate():
    plans = plan_clips([Peak(10.0, 0.4), Peak(20.0, 0.9)], CFG)
    assert len(plans) == 2


def test_long_window_is_capped_at_max_length():
    cfg = ClipConfig(pre_roll=10.0, post_roll=10.0, min_length=2.0, max_length=6.0)
    plans = plan_clips([Peak(30.0, 0.9)], cfg)
    assert plans[0].duration <= 6.0
    assert plans[0].start <= 30.0 <= plans[0].end


def test_short_window_is_dropped():
    cfg = ClipConfig(pre_roll=0.2, post_roll=0.2, min_length=2.0)
    assert plan_clips([Peak(10.0, 0.9)], cfg) == []


def test_merged_span_is_split_at_max_length():
    cfg = ClipConfig(pre_roll=3.0, post_roll=3.0, min_length=2.0, max_length=10.0, merge_gap=2.0)
    plans = plan_clips([Peak(10.0, 0.5), Peak(16.0, 0.5), Peak(22.0, 0.5)], cfg)
    assert len(plans) == 2
    assert all(p.duration <= 10.0 for p in plans)


def test_with_length_keeps_nearby_peaks_as_separate_clips():
    cfg = ClipConfig().with_length(6.0)
    plans = plan_clips([Peak(10.0, 0.5), Peak(18.0, 0.5)], cfg)
    assert len(plans) == 2
    assert all(5.0 <= p.duration <= 6.0 for p in plans)


def test_max_clips_keeps_the_best_ones_in_time_order():
    cfg = ClipConfig(pre_roll=1.0, post_roll=1.0, min_length=1.0, max_clips=2)
    peaks = [Peak(10.0, 0.3), Peak(30.0, 0.99), Peak(50.0, 0.7)]
    plans = plan_clips(peaks, cfg)
    assert [round(p.start) for p in plans] == [29, 49]
