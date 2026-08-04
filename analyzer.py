import argparse
import logging
import os
import subprocess


LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def classify_video_duration(video_path: str) -> str:
    resolved_video_path = os.path.abspath(video_path)

    if not os.path.isfile(resolved_video_path):
        raise FileNotFoundError(f"Video not found: {resolved_video_path}")

    command = [
        "ffprobe",
        "-v",
        "error",
        "-show_entries",
        "format=duration",
        "-of",
        "default=noprint_wrappers=1:nokey=1",
        resolved_video_path,
    ]

    result = subprocess.run(
        command,
        check=True,
        capture_output=True,
        text=True,
    )

    duration_output = result.stdout.strip()
    if not duration_output:
        raise RuntimeError("ffprobe did not return duration output")

    duration_seconds = float(duration_output)
    classification = "short" if duration_seconds <= 180 else "long"

    LOGGER.info(
        "Video duration: %.2f seconds | Classification: %s",
        duration_seconds,
        classification,
    )

    return classification


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Classify video duration as short or long using ffprobe.",
    )
    parser.add_argument("video", help="Path to the video file")
    return parser


def main() -> int:
    configure_logging()

    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        print(classify_video_duration(args.video))
    except Exception as exc:
        LOGGER.exception("Video analysis failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())