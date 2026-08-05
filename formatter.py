import argparse
import logging
import os
import shutil
import subprocess
from typing import Dict, Optional


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
        if "h264_nvenc" in stderr or "nvenc" in stderr.lower() or "Function not implemented" in stderr:
            LOGGER.warning("NVENC failed; retrying with libx264 CPU")
            fallback_command = command.copy()
            fallback_command[fallback_command.index("h264_nvenc")] = "libx264"
            fallback_command[fallback_command.index("-rc") + 1] = "crf"
            fallback_command[fallback_command.index("-b:v") + 1] = "23"
            fallback_command[fallback_command.index("-minrate") + 1] = "0"
            fallback_command[fallback_command.index("-maxrate") + 1] = "0"
            fallback_command[fallback_command.index("-bufsize") + 1] = "0"
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


def escape_path_for_ffmpeg_filter(path: str) -> str:
    escaped = path.replace("\\", "/")
    escaped = escaped.replace(":", "\\:")
    escaped = escaped.replace("'", "\\'")
    return escaped


def build_center_crop_9x16_filter() -> str:
    # Corta a largura baseada na altura (ih * 9/16)
    # Mantém a altura original (ih)
    # Centraliza o eixo X pegando a largura original menos a nova dividido por 2 ((iw-ow)/2)
    # Eixo Y fica no topo (0)
    return "crop=ih*9/16:ih:(iw-ow)/2:0"


def run_ffmpeg(command: list[str]) -> None:
    LOGGER.info("Running FFmpeg command: %s", " ".join(command))
    subprocess.run(command, check=True)


def format_video_by_classification(
    base_tratada_path: str,
    subtitle_path: str,
    classification: str,
    output_directory: Optional[str] = None,
) -> Dict[str, str]:
    configure_logging()

    normalized_classification = classification.strip().lower()
    if normalized_classification not in {"short", "long"}:
        raise ValueError("classification must be 'short' or 'long'")

    resolved_video_path = validate_input_file(base_tratada_path, "Input video")
    resolved_subtitle_path = validate_input_file(subtitle_path, "Subtitle SRT")
    resolved_output_directory = os.path.abspath(
        output_directory or ensure_output_directory(os.getcwd())
    )
    os.makedirs(resolved_output_directory, exist_ok=True)

    crop_filter = build_center_crop_9x16_filter()

    if normalized_classification == "short":
        final_vertical_legendado = os.path.join(
            resolved_output_directory,
            "final_vertical_legendado.mp4",
        )
        subtitle_filter = f"subtitles=filename='{escape_path_for_ffmpeg_filter(resolved_subtitle_path)}'"
        video_filter = f"{crop_filter},{subtitle_filter}"

        short_command = [
            "ffmpeg",
            "-y",
            "-i",
            resolved_video_path,
            "-vf",
            video_filter,
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
            final_vertical_legendado,
        ]
        _run_ffmpeg_with_fallback(short_command)

        return {
            "classification": normalized_classification,
            "final_vertical_legendado": final_vertical_legendado,
        }

    final_horizontal = os.path.join(resolved_output_directory, "final_horizontal.mp4")
    final_vertical = os.path.join(resolved_output_directory, "final_vertical.mp4")

    long_horizontal_command = [
        "ffmpeg",
        "-y",
        "-i",
        resolved_video_path,
        "-c",
        "copy",
        final_horizontal,
    ]
    run_ffmpeg(long_horizontal_command)

    long_vertical_command = [
        "ffmpeg",
        "-y",
        "-i",
        resolved_video_path,
        "-vf",
        crop_filter,
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
        final_vertical,
    ]
    _run_ffmpeg_with_fallback(long_vertical_command)

    copied_srt_path = os.path.join(
        resolved_output_directory,
        os.path.basename(resolved_subtitle_path),
    )
    shutil.copy2(resolved_subtitle_path, copied_srt_path)
    LOGGER.info("Copied subtitle to %s", copied_srt_path)

    return {
        "classification": normalized_classification,
        "final_horizontal": final_horizontal,
        "final_vertical": final_vertical,
        "copied_srt": copied_srt_path,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Format base_tratada.mp4 outputs based on short/long classification.",
    )
    parser.add_argument("video", help="Path to base_tratada.mp4")
    parser.add_argument("srt", help="Path to the original SRT file")
    parser.add_argument("classification", help="short or long")
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to Output in current project.",
    )
    return parser


def main() -> int:
    configure_logging()

    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        format_video_by_classification(
            args.video,
            args.srt,
            args.classification,
            output_directory=args.output_dir,
        )
    except Exception as exc:
        LOGGER.exception("Formatting failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())