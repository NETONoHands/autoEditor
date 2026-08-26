import argparse
import logging
import os
import subprocess

from pipeline_contracts import VideoMetadata, validate_video_file


LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def analyze_video(video_path: str) -> VideoMetadata:
    metadata = validate_video_file(video_path)

    LOGGER.info(
        "Video metadata: %.2fs, %sx%s | Classification: %s",
        metadata.duration_seconds,
        metadata.width,
        metadata.height,
        metadata.classification,
    )
    return metadata


def classify_video_duration(video_path: str) -> str:
    return analyze_video(video_path).classification


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