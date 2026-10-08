import json
import logging
import re
import subprocess
from typing import Dict, List, Optional

LOGGER = logging.getLogger(__name__)

_SILENCE_START_RE = re.compile(r"silence_start:\s*(-?\d+(?:\.\d+)?)")
_SILENCE_END_RE = re.compile(r"silence_end:\s*(-?\d+(?:\.\d+)?)")


def _has_audio_stream(video_path: str) -> Optional[bool]:
    """True/False se o ffprobe conseguiu determinar; None se não foi possível sondar."""
    command = [
        "ffprobe",
        "-v", "error",
        "-select_streams", "a",
        "-show_entries", "stream=index",
        "-of", "json",
        video_path,
    ]
    try:
        result = subprocess.run(command, capture_output=True, text=True, check=True)
        return bool(json.loads(result.stdout).get("streams"))
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, AttributeError):
        LOGGER.warning("Não foi possível sondar streams de áudio de %s com ffprobe", video_path)
        return None


def detect_silences(
    video_path: str,
    noise: str = "-35dB",
    duration: float = 0.3,
) -> List[Dict[str, float]]:
    """Detect silent intervals in a media file using FFmpeg's ``silencedetect``.

    Args:
        video_path: Path to the input video/audio file.
        noise: Noise tolerance threshold (e.g. ``"-35dB"``).
        duration: Minimum silence duration, in seconds, to be reported.

    Returns:
        A list of ``{"start": float, "end": float}`` dicts, in seconds,
        ordered by time.

    Raises:
        FileNotFoundError: If the ``ffmpeg`` executable is not available.
        RuntimeError: If FFmpeg exits with a non-zero status.
    """
    if _has_audio_stream(video_path) is False:
        LOGGER.warning("Vídeo sem faixa de áudio; deteção de silêncio ignorada: %s", video_path)
        return []

    command = [
        "ffmpeg",
        "-hide_banner",
        "-nostats",
        "-i", video_path,
        "-af", f"silencedetect=noise={noise}:d={duration}",
        "-f", "null",
        "-",
    ]
    result = subprocess.run(command, capture_output=True, text=True, check=False)
    if result.returncode != 0:
        raise RuntimeError(
            f"FFmpeg failed (exit {result.returncode}): {result.stderr.strip()}"
        )

    silences: List[Dict[str, float]] = []
    current_start: Optional[float] = None

    for line in result.stderr.splitlines():
        start_match = _SILENCE_START_RE.search(line)
        if start_match:
            current_start = float(start_match.group(1))
            continue

        end_match = _SILENCE_END_RE.search(line)
        if end_match and current_start is not None:
            silences.append({"start": current_start, "end": float(end_match.group(1))})
            current_start = None

    # Silence running until end of file: FFmpeg emits silence_start without silence_end.
    if current_start is not None:
        total = _probe_duration(result.stderr)
        if total is not None and total > current_start:
            silences.append({"start": current_start, "end": total})

    return silences


def _probe_duration(stderr: str) -> Optional[float]:
    """Extract the media duration from FFmpeg's ``Duration: HH:MM:SS.xx`` line."""
    match = re.search(r"Duration:\s*(\d+):(\d+):(\d+(?:\.\d+)?)", stderr)
    if not match:
        return None
    hours, minutes, seconds = match.groups()
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
