from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import AsyncGenerator

import pytest
import pytest_asyncio
from httpx import ASGITransport, AsyncClient
from PIL import Image, ImageDraw

# Ensure backend root is importable and isolate test DB/data directory
BACKEND_DIR = Path(__file__).resolve().parents[1]
if str(BACKEND_DIR) not in sys.path:
    sys.path.insert(0, str(BACKEND_DIR))

_TEST_DATA_DIR = Path(tempfile.mkdtemp(prefix="narezki_test_data_"))
os.environ.setdefault("NAREZKI_DATA_DIR", str(_TEST_DATA_DIR))
os.environ.setdefault("NAREZKI_MODELS_DIR", str(BACKEND_DIR / "data" / "models"))

from app.core.cuda_env import setup_cuda_dlls

setup_cuda_dlls()

from app.core.config import get_ffmpeg_path, get_ffprobe_path
from app.core.database import AsyncSessionLocal, init_db
from app.core.queue_manager import queue_manager
from app.main import _register_job_handlers, app


def _run_sync(cmd: list[str]) -> None:
    kwargs = {"creationflags": 0x08000000} if sys.platform == "win32" else {}
    proc = subprocess.run(cmd, capture_output=True, text=True, check=False, **kwargs)
    if proc.returncode != 0:
        raise RuntimeError(f"Command failed ({proc.returncode}): {' '.join(cmd)}\n{proc.stderr}")


def _synthesize_speech_wav(wav_path: Path) -> bool:
    """Try generating real spoken speech WAV via Windows System.Speech so Whisper transcribes actual words."""
    if sys.platform != "win32":
        return False
    ps_script = (
        "Add-Type -AssemblyName System.Speech; "
        "$s = New-Object System.Speech.Synthesis.SpeechSynthesizer; "
        "$s.Rate = 0; "
        f"$s.SetOutputToWaveFile('{str(wav_path).replace(chr(39), chr(39)*2)}'); "
        "$s.Speak('Welcome to Narezki studio! Watch this incredible anime battle highlight right now.'); "
        "$s.Dispose();"
    )
    proc = subprocess.run(
        ["powershell", "-NoProfile", "-Command", ps_script],
        capture_output=True,
        text=True,
        creationflags=0x08000000,
        timeout=20,
    )
    return proc.returncode == 0 and wav_path.exists() and wav_path.stat().st_size > 10000


@pytest.fixture(scope="session")
def fixtures_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    d = tmp_path_factory.mktemp("media_fixtures")
    ffmpeg = get_ffmpeg_path()

    # 1. Create a 12-second 1920x1080 video with:
    #    - 0-4s: Ultralytics real person image panning across frame + speech audio
    #    - 4-8s: Rapid 0.6s color scene cuts + loud audio spikes
    #    - 8-12s: Static high-contrast scene + ambient tone
    person_img = d / "person_frame.jpg"
    try:
        import ultralytics

        ultralytics_assets = Path(ultralytics.__file__).resolve().parent / "assets"
        cand = ultralytics_assets / "zidane.jpg"
        if not cand.exists():
            cand = ultralytics_assets / "bus.jpg"
        if cand.exists():
            shutil.copy2(cand, person_img)
    except Exception:
        pass

    if not person_img.exists():
        img = Image.new("RGB", (640, 480), (40, 80, 160))
        ImageDraw.Draw(img).rectangle([200, 100, 440, 460], fill=(240, 200, 160))
        img.save(person_img)

    # Pan the real image over a 1920x1080 dark canvas for 4 seconds
    seg1 = d / "seg1.mp4"
    _run_sync(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=#181828:s=1920x1080:r=30:d=4",
            "-loop",
            "1",
            "-t",
            "4",
            "-i",
            str(person_img),
            "-filter_complex",
            "[1:v]scale=-2:900[p];[0:v][p]overlay=x='100+t*140':y=90[v]",
            "-map",
            "[v]",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(seg1),
        ]
    )

    # Rapid scene cuts (6 cuts in 4s -> triggers action burst + scene_detect)
    seg2_parts: list[Path] = []
    colors = ["#ef4444", "#22c55e", "#3b82f6", "#eab308", "#ec4899", "#06b6d4"]
    for idx, col in enumerate(colors):
        p = d / f"cut_{idx}.mp4"
        _run_sync(
            [
                ffmpeg,
                "-y",
                "-f",
                "lavfi",
                "-i",
                f"color=c={col}:s=1920x1080:r=30:d=0.67",
                "-c:v",
                "libx264",
                "-preset",
                "ultrafast",
                "-pix_fmt",
                "yuv420p",
                str(p),
            ]
        )
        seg2_parts.append(p)

    seg3 = d / "seg3.mp4"
    _run_sync(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "testsrc2=size=1920x1080:rate=30:duration=4",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(seg3),
        ]
    )

    concat_list = d / "concat.txt"
    all_segs = [seg1, *seg2_parts, seg3]
    concat_list.write_text(
        "\n".join(f"file '{s.as_posix()}'" for s in all_segs),
        encoding="utf-8",
    )

    silent_video = d / "silent_master.mp4"
    _run_sync(
        [
            ffmpeg,
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            str(concat_list),
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(silent_video),
        ]
    )

    # Build 12s audio: speech in first 4.5s (or quiet tone) + LOUD burst at 4.5-7.5s + quiet tail
    speech_wav = d / "speech.wav"
    has_speech = _synthesize_speech_wav(speech_wav)
    sample_mp4 = d / "sample_1080p.mp4"

    if has_speech:
        _run_sync(
            [
                ffmpeg,
                "-y",
                "-i",
                str(silent_video),
                "-i",
                str(speech_wav),
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=12",
                "-filter_complex",
                "[1:a]aresample=48000,apad=whole_dur=12[sp];"
                "[2:a]volume='if(between(t,4.5,7.5),0.95,0.03)':eval=frame[spk];"
                "[sp][spk]amix=inputs=2:duration=shortest:normalize=0[aout]",
                "-map",
                "0:v",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(sample_mp4),
            ]
        )
    else:
        _run_sync(
            [
                ffmpeg,
                "-y",
                "-i",
                str(silent_video),
                "-f",
                "lavfi",
                "-i",
                "sine=frequency=440:sample_rate=48000:duration=12",
                "-filter_complex",
                "[1:a]volume='if(between(t,4.5,7.5),0.95,0.03)':eval=frame[aout]",
                "-map",
                "0:v",
                "-map",
                "[aout]",
                "-c:v",
                "copy",
                "-c:a",
                "aac",
                "-b:a",
                "192k",
                "-shortest",
                str(sample_mp4),
            ]
        )

    # 2. Sponsor video ad (3s 1280x720 with tone audio)
    ad_video = d / "ad_video.mp4"
    _run_sync(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "color=c=#7c3aed:s=1280x720:r=30:d=3",
            "-f",
            "lavfi",
            "-i",
            "sine=frequency=660:sample_rate=48000:duration=3",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            "-c:a",
            "aac",
            str(ad_video),
        ]
    )

    # 3. Transparent RGBA PNG overlay
    ad_png = d / "ad_overlay.png"
    ov = Image.new("RGBA", (320, 100), (124, 58, 237, 210))
    ImageDraw.Draw(ov).rectangle([4, 4, 315, 95], outline=(255, 255, 255, 255), width=4)
    ov.save(ad_png)

    # 4. Split-screen background video (5s 1280x720 silent)
    bg_video = d / "bg_split.mp4"
    _run_sync(
        [
            ffmpeg,
            "-y",
            "-f",
            "lavfi",
            "-i",
            "mandelbrot=size=1280x720:rate=30",
            "-t",
            "5",
            "-c:v",
            "libx264",
            "-preset",
            "ultrafast",
            "-pix_fmt",
            "yuv420p",
            str(bg_video),
        ]
    )

    assert sample_mp4.exists()
    assert ad_video.exists()
    assert ad_png.exists()
    assert bg_video.exists()
    # Ensure ffprobe is callable too
    assert Path(get_ffprobe_path()).exists()
    return d


@pytest_asyncio.fixture(scope="session")
async def api_client() -> AsyncGenerator[AsyncClient, None]:
    await init_db()
    _register_job_handlers()
    await queue_manager.start(num_workers=2)
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as client:
        yield client
    await queue_manager.stop()
