import argparse
import logging
import os
import subprocess
from typing import Optional

def escape_path_for_ffmpeg_filter(path: str) -> str:
    escaped = path.replace("\\", "/")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    return escaped

def _build_ffmpeg_command(
    input_path: str,
    output_path: str,
    video_filter: str,
    lut_path: Optional[str] = None,
) -> list[str]:
    command = [
        "ffmpeg",
        "-y",
        "-i",
        input_path,
        "-af",
        "afftdn,loudnorm=I=-14:LRA=11:TP=-1.5",
    ]

    video_filters = []
    if video_filter:
        video_filters.append(video_filter)
    if lut_path and os.path.isfile(lut_path):
        caminho_escapado = escape_path_for_ffmpeg_filter(lut_path)
        video_filters.append(f"lut3d=file='{caminho_escapado}'")
    else:
        LOGGER.info("LUT not found or invalid; continuing without LUT")

    if video_filters:
        command.extend(["-vf", ",".join(video_filters)])

    command.extend(
        [
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
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
            output_path,
        ]
    )
    return command


def _run_ffmpeg_with_fallback(command: list[str]) -> None:
    try:
        completed = subprocess.run(command, check=True, capture_output=True, text=True)
        if completed.stdout:
            LOGGER.info("FFmpeg stdout: %s", completed.stdout.strip())
        if completed.stderr:
            LOGGER.info("FFmpeg stderr: %s", completed.stderr.strip())
    except subprocess.CalledProcessError as exc:
        stderr = (exc.stderr or "").strip()
        LOGGER.error("FFmpeg failed with stderr: %s", stderr)
        if "h264_nvenc" in stderr or "nvenc" in stderr.lower() or "Function not implemented" in stderr or "Nvidia driver" in stderr:
            LOGGER.warning("NVENC failed; retrying with libx264 CPU")
            
            # Reconstrói o comando limpo para a CPU
            input_file = command[command.index("-i") + 1]
            output_file = command[-1]
            
            fallback_command = [
                "ffmpeg", "-y", "-i", input_file,
                "-af", command[command.index("-af") + 1]
            ]
            fallback_command.extend(["-map", "0:v:0", "-map", "0:a:0?"])
            if "-vf" in command:
                fallback_command.extend(["-vf", command[command.index("-vf") + 1]])
            
            fallback_command.extend([
                "-c:v", "libx264", "-crf", "23", "-preset", "fast",
                "-c:a", "aac", output_file
            ])
            
            completed = subprocess.run(fallback_command, check=True, capture_output=True, text=True)
            if completed.stdout:
                LOGGER.info("FFmpeg fallback stdout: %s", completed.stdout.strip())
            if completed.stderr:
                LOGGER.info("FFmpeg fallback stderr: %s", completed.stderr.strip())
            return

        raise


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
    cut_video_path: str,
    output_path: Optional[str] = None,
    lut_path: str = "lut.cube",
) -> str:
    configure_logging()

    resolved_input_path = validate_input_file(cut_video_path, "Input video")
    project_root = os.path.abspath(os.getcwd())
    output_directory = ensure_output_directory(project_root)

    resolved_output_path = os.path.abspath(
        output_path or os.path.join(output_directory, "base_tratada.mp4")
    )
    os.makedirs(os.path.dirname(resolved_output_path) or output_directory, exist_ok=True)

    command = _build_ffmpeg_command(
        resolved_input_path,
        resolved_output_path,
        video_filter="",
        lut_path=lut_path,
    )

    LOGGER.info("Running FFmpeg command: %s", " ".join(command))
    _run_ffmpeg_with_fallback(command)

    if not os.path.isfile(resolved_output_path):
        raise RuntimeError(f"Enhanced video was not created: {resolved_output_path}")

    LOGGER.info("Enhanced video saved to %s", resolved_output_path)
    return resolved_output_path


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Apply audio and video enhancement filters to a cut video.",
    )
    parser.add_argument("input", help="Path to the cut video file")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to Output/base_tratada.mp4.",
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