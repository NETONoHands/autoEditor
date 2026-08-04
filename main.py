import argparse
import logging
import os
from typing import Any, Dict, Optional

from cutter import cut_from_whisper_json
from enhancer import enhance_video_with_ffmpeg
from setup_manager import setup_manager
from subtitles import apply_subtitles_with_ffmpeg
from transcriber import transcribe_video_to_json


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
    silence_threshold: float = 0.5,
    lut_path: str = "lut.cube",
    final_output_path: Optional[str] = None,
) -> Dict[str, Any]:
    configure_logging()

    setup_result = setup_manager(raw_video_path, subtitle_path, project_root=project_root)
    resolved_project_root = setup_result["project_root"]
    backup_video_path = setup_result["video_backup_path"]
    backup_subtitle_path = setup_result["subtitle_backup_path"]

    LOGGER.info("Step 1/5 completed: setup_manager")

    transcription_result = transcribe_video_to_json(
        backup_video_path,
        backup_subtitle_path,
        project_root=resolved_project_root,
        model_name="base",
    )
    whisper_json_path = transcription_result["output_json_path"]

    LOGGER.info("Step 2/5 completed: transcriber")

    cuts_output_path = os.path.join(resolved_project_root, "Output", "cortes_brutos.mp4")
    cutting_result = cut_from_whisper_json(
        whisper_json_path,
        backup_video_path,
        output_path=cuts_output_path,
        silence_threshold=silence_threshold,
    )

    LOGGER.info("Step 3/5 completed: cutter")

    treated_output_path = os.path.join(resolved_project_root, "Output", "tratado.mp4")
    treated_video_path = enhance_video_with_ffmpeg(
        cutting_result["output_path"],
        output_path=treated_output_path,
        lut_path=lut_path,
    )

    LOGGER.info("Step 4/5 completed: enhancer")

    default_final_output = os.path.join(resolved_project_root, "Output", "final_legendado.mp4")
    subtitled_video_path = apply_subtitles_with_ffmpeg(
        treated_video_path,
        backup_subtitle_path,
        output_path=final_output_path or default_final_output,
    )

    LOGGER.info("Step 5/5 completed: subtitles")

    return {
        "project_root": resolved_project_root,
        "video_backup_path": backup_video_path,
        "subtitle_backup_path": backup_subtitle_path,
        "whisper_json_path": whisper_json_path,
        "speech_intervals": cutting_result["intervals"],
        "cortes_brutos_path": cutting_result["output_path"],
        "tratado_path": treated_video_path,
        "final_video_path": subtitled_video_path,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Run the full autoEditor pipeline: setup, transcribe, cut, enhance and subtitle.",
    )
    parser.add_argument("video", help="Path to the raw input video")
    parser.add_argument("srt", help="Path to the respective SRT subtitle file")
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root folder. Defaults to current working directory.",
    )
    parser.add_argument(
        "--silence-threshold",
        type=float,
        default=0.5,
        help="Maximum silence gap in seconds allowed inside a speech block.",
    )
    parser.add_argument(
        "--lut-path",
        default="lut.cube",
        help="Placeholder LUT file path used during enhancement.",
    )
    parser.add_argument(
        "--final-output",
        default=None,
        help="Optional final output path. Defaults to Output/final_legendado.mp4.",
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
            silence_threshold=args.silence_threshold,
            lut_path=args.lut_path,
            final_output_path=args.final_output,
        )
    except Exception as exc:
        LOGGER.exception("Pipeline failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())