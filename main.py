import argparse
import logging
import os
from typing import Any, Dict, Optional

from analyzer import classify_video_duration
from enhancer import enhance_video_with_ffmpeg
from formatter import format_video_by_classification


LOGGER = logging.getLogger(__name__)


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def run_pipeline(
    raw_video_path: str,
    subtitle_path: str,
    project_root: Optional[str] = None,
    lut_path: str = "lut.cube",
    output_directory: Optional[str] = None,
) -> Dict[str, Any]:
    configure_logging()

    resolved_project_root = os.path.abspath(project_root or os.getcwd())
    resolved_output_directory = os.path.abspath(
        output_directory or os.path.join(resolved_project_root, "Output")
    )
    os.makedirs(resolved_output_directory, exist_ok=True)

    classification = classify_video_duration(raw_video_path)
    LOGGER.info("Step 1/3 completed: analyzer (%s)", classification)

    base_treated_path = os.path.join(resolved_output_directory, "base_tratada.mp4")
    treated_video_path = enhance_video_with_ffmpeg(
        raw_video_path,
        output_path=base_treated_path,
        lut_path=lut_path,
    )
    LOGGER.info("Step 2/3 completed: enhancer")

    formatting_result = format_video_by_classification(
        treated_video_path,
        subtitle_path,
        classification,
        output_directory=resolved_output_directory,
    )
    LOGGER.info("Step 3/3 completed: formatter")

    LOGGER.info(
        "FFmpeg exports remain NVENC 10M in enhancer and formatter encoded outputs"
    )

    return {
        "project_root": resolved_project_root,
        "output_directory": resolved_output_directory,
        "classification": classification,
        "base_treated_path": treated_video_path,
        "formatted_outputs": formatting_result,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run analyzer, enhancer and formatter in sequence.",
    )
    parser.add_argument("video", help="Path to the raw input video")
    parser.add_argument("srt", help="Path to the respective SRT subtitle file")
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root folder. Defaults to current working directory.",
    )
    parser.add_argument(
        "--lut-path",
        default="lut.cube",
        help="Placeholder LUT file path used during enhancement.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <project-root>/Output.",
    )
    return parser


def main() -> int:
    configure_logging()

    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        run_pipeline(
            args.video,
            args.srt,
            project_root=args.project_root,
            lut_path=args.lut_path,
            output_directory=args.output_dir,
        )
    except Exception as exc:
        LOGGER.exception("Pipeline failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())