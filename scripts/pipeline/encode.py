"""
Encode module — assembles rendered pages + TTS audio into a chapter MP4.

Each page is shown for max(page_duration_min, audio_length) seconds.
"""

from __future__ import annotations

import subprocess
import tempfile
from pathlib import Path

import wave


def _wav_duration(wav_path: Path) -> float:
    try:
        with wave.open(str(wav_path), "rb") as f:
            return f.getnframes() / f.getframerate()
    except Exception:
        return 0.0


def _silence_wav(out_path: Path, duration: float = 1.0, sample_rate: int = 22050) -> None:
    """Write a short silent WAV file."""
    import struct
    n_samples = int(duration * sample_rate)
    with wave.open(str(out_path), "w") as f:
        f.setnchannels(1)
        f.setsampwidth(2)
        f.setframerate(sample_rate)
        f.writeframes(struct.pack(f"<{n_samples}h", *([0] * n_samples)))


def encode_chapter(
    page_entries: list[dict],
    out_path: Path,
    fps: int = 24,
    page_duration_min: float = 5.0,
    crf: int = 18,
) -> None:
    """
    page_entries: list of {"image": Path, "audio": Path | None}
    Creates one MP4 where each page is shown for its audio duration (min page_duration_min s).
    """
    out_path.parent.mkdir(parents=True, exist_ok=True)
    use_nvenc = _check_nvenc()

    segment_paths = []

    with tempfile.TemporaryDirectory() as tmp:
        tmp_dir = Path(tmp)

        for i, entry in enumerate(page_entries):
            img_path = entry["image"]
            audio_path = entry.get("audio")

            # Determine duration
            if audio_path and audio_path.exists():
                duration = max(page_duration_min, _wav_duration(audio_path))
            else:
                duration = page_duration_min
                # Generate silence if no audio
                audio_path = tmp_dir / f"silence_{i:04d}.wav"
                _silence_wav(audio_path, duration)

            seg_path = tmp_dir / f"seg_{i:04d}.mp4"
            _encode_segment(img_path, audio_path, seg_path, duration, fps, use_nvenc, crf)
            segment_paths.append(seg_path)

        _concat_segments(segment_paths, out_path, tmp_dir)


def _check_nvenc() -> bool:
    try:
        r = subprocess.run(["ffmpeg", "-hide_banner", "-encoders"],
                           capture_output=True, text=True, timeout=10)
        return "h264_nvenc" in r.stdout
    except Exception:
        return False


def _encode_segment(img: Path, audio: Path, out: Path, duration: float,
                    fps: int, use_nvenc: bool, crf: int) -> None:
    # Use framerate=1 for still-image slides — far smaller files, no quality loss.
    # NVENC has resolution limits (max ~8192×8192 but buggy at 2912×4120); always use libx264.
    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-loop", "1", "-framerate", "1",
        "-i", str(img),
        "-i", str(audio),
        "-map", "0:v:0", "-map", "1:a:0",
        "-t", f"{duration:.3f}",
        "-c:a", "aac", "-b:a", "192k",
        "-c:v", "libx264", "-preset", "fast", "-crf", str(crf),
        "-pix_fmt", "yuv420p",
        "-shortest",
        str(out),
    ]
    subprocess.run(cmd, check=True, capture_output=True)


def _concat_segments(segments: list[Path], out_path: Path, tmp_dir: Path) -> None:
    concat_file = tmp_dir / "concat.txt"
    with concat_file.open("w") as f:
        for seg in segments:
            f.write(f"file '{seg}'\n")

    cmd = [
        "ffmpeg", "-hide_banner", "-y",
        "-f", "concat", "-safe", "0",
        "-i", str(concat_file),
        "-c", "copy",
        str(out_path),
    ]
    subprocess.run(cmd, check=True, capture_output=True)
