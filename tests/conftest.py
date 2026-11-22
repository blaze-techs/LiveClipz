import subprocess

import pytest

from autoclip.media import ffmpeg_bin, have_ffmpeg

BURSTS = r"between(t\,3\,4.2)+between(t\,9\,10.2)"


@pytest.fixture(scope="session")
def sample_video(tmp_path_factory):
    """12s clip: quiet tone with two loud + flashing hype moments."""
    if not have_ffmpeg():
        pytest.skip("ffmpeg is required for media tests")
    path = tmp_path_factory.mktemp("media") / "sample.mp4"
    subprocess.run(
        [
            ffmpeg_bin(),
            "-hide_banner",
            "-y",
            "-v",
            "error",
            "-f",
            "lavfi",
            "-i",
            "color=c=0x101018:s=320x180:r=30:d=12",
            "-f",
            "lavfi",
            "-i",
            f"aevalsrc='0.006*sin(2*PI*220*t)+0.7*sin(2*PI*880*t)*({BURSTS})':s=44100:d=12",
            "-vf",
            f"drawbox=x=0:y=0:w=iw:h=ih:color=white:t=fill:enable='{BURSTS}'",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            "-b:a",
            "96k",
            "-shortest",
            str(path),
        ],
        check=True,
    )
    return path
