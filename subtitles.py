import argparse
import logging
import os
import subprocess
from typing import Optional


LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def validate_input_file(file_path: str, file_label: str) -> str:
    normalized_path = os.path.abspath(file_path)

    if not os.path.isfile(normalized_path):
        raise FileNotFoundError(f"{file_label} not found: {normalized_path}")

    return normalized_path


def ensure_output_directory(project_root: str) -> str:
    output_directory = os.path.join(project_root, "Output")
    os.makedirs(output_directory, exist_ok=True)
    return output_directory


def escape_path_for_ffmpeg_filter(path: str) -> str:
    # The subtitles filter expects POSIX-like separators and escaped drive colon on Windows.
    escaped = path.replace("\\", "/")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    return escaped


def apply_subtitles_with_ffmpeg(
    video_path: str,
    subtitle_path: str,
    output_path: Optional[str] = None,
) -> str:
    configure_logging()

    resolved_video_path = validate_input_file(video_path, "Input video")
    resolved_subtitle_path = validate_input_file(subtitle_path, "Subtitle SRT")
    project_root = os.path.abspath(os.getcwd())
    output_directory = ensure_output_directory(project_root)

    resolved_output_path = os.path.abspath(
        output_path or os.path.join(output_directory, "final_legendado.mp4")
    )
    os.makedirs(os.path.dirname(resolved_output_path) or output_directory, exist_ok=True)

    subtitle_filter = f"subtitles=filename='{escape_path_for_ffmpeg_filter(resolved_subtitle_path)}'"

    command = [
        "ffmpeg",
        "-y",
        "-i",
        resolved_video_path,
        "-vf",
        subtitle_filter,
        "-c:v",
        "h264_nvenc",
        "-rc",
        "cbr",
        "-b:v",
        "10M",
        "-minrate",
        "10M",
        "-maxrate",
        "10M",
        "-bufsize",
        "20M",
        "-c:a",
        "copy",
        resolved_output_path,
    ]

    LOGGER.info("Running FFmpeg command: %s", " ".join(command))
    subprocess.run(command, check=True)

    if not os.path.isfile(resolved_output_path):
        raise RuntimeError(f"Subtitled video was not created: {resolved_output_path}")

    LOGGER.info("Subtitled video saved to %s", resolved_output_path)
    return resolved_output_path


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Burn subtitles into a video and output final_legendado.mp4.",
    )
    parser.add_argument("video", help="Path to the input video")
    parser.add_argument("srt", help="Path to the SRT subtitle file")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to Output/final_legendado.mp4.",
    )
    return parser


def main() -> int:
    configure_logging()

    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        apply_subtitles_with_ffmpeg(
            args.video,
            args.srt,
            output_path=args.output,
        )
    except Exception as exc:
        LOGGER.exception("Subtitle rendering failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
