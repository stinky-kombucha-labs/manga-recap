"""
Manhwa Recap Translator - Configuration
"""

from pathlib import Path

# Base paths
PROJECT_ROOT = Path("/home/user/PycharmProjects/Manhwa_Recap")

# Input/Output
VIDEO_INPUT_DIR = PROJECT_ROOT / "video"
VIDEO_OUTPUT_DIR = PROJECT_ROOT / "video_output"

# Temp directories
TEMP_DIR = PROJECT_ROOT / "temp"
FRAMES_DIR = TEMP_DIR / "frames"
FRAMES_UNIQUE_DIR = TEMP_DIR / "frames_unique"
FRAMES_TRANSLATED_DIR = TEMP_DIR / "frames_translated"
AUDIO_DIR = TEMP_DIR / "audio"

# Supported video formats
VIDEO_EXTENSIONS = {".mp4", ".mkv", ".avi", ".webm", ".mov"}

# Frame extraction settings
FRAME_FORMAT = "png"  # png for quality, jpg for speed

# Deduplication settings
HASH_SIZE = 16  # perceptual hash size
SIMILARITY_THRESHOLD = 5  # lower = stricter deduplication (0-64)

# TTS settings
TTS_VOICE = "Dmytro"
TTS_DEVICE = "cuda"  # cuda, cpu, mps

# Translation settings
TARGET_LANG = "UKR"
SOURCE_LANG = "ENG"


def get_video_files():
    """Get all video files from input directory."""
    videos = []
    for ext in VIDEO_EXTENSIONS:
        videos.extend(VIDEO_INPUT_DIR.glob(f"*{ext}"))
    return sorted(videos)


def clean_temp_dir(subdir: Path = None):
    """Clean temporary directory or specific subdirectory."""
    import shutil
    target = subdir or TEMP_DIR
    if target.exists():
        shutil.rmtree(target)
    target.mkdir(parents=True, exist_ok=True)