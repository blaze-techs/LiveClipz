import json

from autoclip.cli import main


def test_detect_reports_peaks_as_json(sample_video, capsys):
    assert main(["detect", str(sample_video), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["duration"] > 11.0
    assert len(payload["peaks"]) == 2
    assert all(0.0 <= peak["time"] <= 12.0 for peak in payload["peaks"])


def test_doctor_reports_the_runtime(capsys):
    assert main(["doctor"]) == 0
    out = capsys.readouterr().out
    assert "ffmpeg: found" in out
    assert "numpy" in out
