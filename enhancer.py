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


def enhance_video_with_ffmpeg(
    cortes_brutos_path: str,
    output_path: Optional[str] = None,
    lut_path: str = "lut.cube",
) -> str:
    configure_logging()

    resolved_input_path = validate_input_file(cortes_brutos_path, "Input video")
    project_root = os.path.abspath(os.getcwd())
    output_directory = ensure_output_directory(project_root)

    resolved_output_path = os.path.abspath(
        output_path or os.path.join(output_directory, "tratado.mp4")
    )
    os.makedirs(os.path.dirname(resolved_output_path) or output_directory, exist_ok=True)

    audio_filter = "afftdn,loudnorm=I=-14:LRA=11:TP=-1.5"
    video_filter = f"lut3d=file='{lut_path}'"

    command = [
        "ffmpeg",
        "-y",
        "-i",
        resolved_input_path,
        "-vf",
        video_filter,
        "-af",
        audio_filter,
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
        "aac",
        resolved_output_path,
    ]

    LOGGER.info("Running FFmpeg command: %s", " ".join(command))
    subprocess.run(command, check=True)

    if not os.path.isfile(resolved_output_path):
        raise RuntimeError(f"Enhanced video was not created: {resolved_output_path}")

    LOGGER.info("Enhanced video saved to %s", resolved_output_path)
    return resolved_output_path


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply audio and video enhancement filters to cortes_brutos.mp4.",
    )
    parser.add_argument("input", help="Path to cortes_brutos.mp4")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to Output/tratado.mp4.",
    )
    parser.add_argument(
        "--lut-path",
        default="lut.cube",
        help="Placeholder LUT file path used by the video filter.",
    )
    return parser


def main() -> int:
    configure_logging()

    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        enhance_video_with_ffmpeg(
            args.input,
            output_path=args.output,
            lut_path=args.lut_path,
        )
    except Exception as exc:
        LOGGER.exception("Enhancement failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())