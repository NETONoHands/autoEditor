import argparse
import logging
import os
import shutil
import subprocess
from typing import Dict, Optional

from face_tracker import detect_face_crop_x


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


def build_vertical_crop_filter(crop_x: Optional[int] = None) -> str:
    if crop_x is None:
        return build_center_crop_9x16_filter()
    return f"crop=ih*9/16:ih:{max(0, int(crop_x))}:0"


def build_vertical_safe_area_filter(safe_area: float) -> str:
    if not 0.0 <= safe_area <= 0.2:
        raise ValueError("safe_area must be between 0.0 and 0.2")
    return "scale=1080:1920"


def _safe_text_margins(safe_area: float) -> tuple[int, int]:
    safe_area_pixels = int(round(1920 * safe_area))
    return max(80, safe_area_pixels), max(120, safe_area_pixels)


def escape_drawtext_text(text: str) -> str:
    return (
        text.replace("\\", "\\\\")
        .replace(":", "\\:")
        .replace("'", "\\'")
        .replace("%", "%%")
    )


FONTS_DIR = os.path.join(os.path.dirname(os.path.abspath(__file__)), "assets", "fonts")


def resolve_title_fontfile() -> Optional[str]:
    # Fontes embutidas (Anton, DejaVu Sans Bold) evitam depender de fontes do SO; Arial Bold do SO fica como último recurso.
    configured = os.getenv("TAPA_NA_LATA_TITLE_FONT")
    candidates = [
        configured,
        os.path.join(FONTS_DIR, "Anton-Regular.ttf"),
        os.path.join(FONTS_DIR, "DejaVuSans-Bold.ttf"),
        r"C:\Windows\Fonts\arialbd.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    ]
    return next((path for path in candidates if path and os.path.isfile(path)), None)


def resolve_subtitle_font() -> tuple[str, Optional[str]]:
    # O nome de família precisa bater com o nome interno da fonte, não com o nome do arquivo.
    candidates = [
        ("Sansation", os.path.join(FONTS_DIR, "Sansation-Regular.ttf")),
        ("Roboto", os.path.join(FONTS_DIR, "Roboto-Regular.ttf")),
        ("DejaVu Sans", os.path.join(FONTS_DIR, "DejaVuSans.ttf")),
    ]
    for family, path in candidates:
        if os.path.isfile(path):
            return family, FONTS_DIR

    LOGGER.warning("No bundled subtitle font found; falling back to system font lookup for Arial")
    return "Arial", None


def build_vertical_composite_filter(
    subtitle_path: str,
    title: str = "",
    crop_x: Optional[int] = None,
    safe_area: float = 0.0,
) -> str:
    filters = [build_vertical_crop_filter(crop_x), build_vertical_safe_area_filter(safe_area)]
    title_y, subtitle_margin_v = _safe_text_margins(safe_area)
    subtitle_margin_h = int(round(1080 * safe_area))
    if title.strip():
        fontfile = resolve_title_fontfile()
        if fontfile is None:
            LOGGER.warning("Title font not found; skipping drawtext to avoid FFmpeg fontconfig failure")
        else:
            escaped_fontfile = escape_path_for_ffmpeg_filter(fontfile)
            filters.append(
                "drawtext="
                f"fontfile='{escaped_fontfile}':"
                f"text='{escape_drawtext_text(title.strip())}':"
                "fontsize=42:fontcolor=yellow:"
                f"borderw=3:bordercolor=black:x=(w-text_w)/2:y={title_y}:"
                "enable='between(t,0,5)':alpha='if(lt(t,4),1,5-t)'"
            )
    subtitle_family, subtitle_fonts_dir = resolve_subtitle_font()
    subtitle_filter_parts = [f"subtitles=filename='{escape_path_for_ffmpeg_filter(subtitle_path)}'"]
    if subtitle_fonts_dir:
        subtitle_filter_parts.append(f"fontsdir='{escape_path_for_ffmpeg_filter(subtitle_fonts_dir)}'")
    subtitle_filter_parts.append(
        f"force_style='FontName={subtitle_family},FontSize=10,PrimaryColour=&H00FFFFFF,"
        "OutlineColour=&H00000000,BorderStyle=1,Outline=2,Shadow=1,"
        f"Alignment=2,MarginL={subtitle_margin_h},MarginR={subtitle_margin_h},MarginV={subtitle_margin_v}'"
    )
    filters.append(":".join(subtitle_filter_parts))
    return ",".join(filters)


def resolve_vertical_crop_x(video_path: str, face_tracking: bool) -> Optional[int]:
    if not face_tracking:
        return None
    try:
        return detect_face_crop_x(video_path)
    except Exception as exc:
        LOGGER.warning("Face tracking failed; using center crop: %s", exc)
        return None


def run_ffmpeg(command: list[str]) -> None:
    LOGGER.info("Running FFmpeg command: %s", " ".join(command))
    subprocess.run(command, check=True)


def format_video_by_classification(
    base_tratada_path: str,
    subtitle_path: str,
    classification: str,
    output_directory: Optional[str] = None,
    output_stem: str = "final",
    title: str = "",
    face_tracking: bool = True,
    safe_area: float = 0.0,
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

    crop_x = resolve_vertical_crop_x(resolved_video_path, face_tracking)
    vertical_filter = build_vertical_composite_filter(
        resolved_subtitle_path,
        title=title,
        crop_x=crop_x,
        safe_area=safe_area,
    )

    if normalized_classification == "short":
        final_vertical_legendado = os.path.join(
            resolved_output_directory,
            f"{output_stem}-vertical-legendado.mp4",
        )
        final_horizontal_legendado = os.path.join(
            resolved_output_directory,
            f"{output_stem}-horizontal-legendado.mp4",
        )
        video_filter = vertical_filter

        short_command = [
            "ffmpeg",
            "-y",
            "-i",
            resolved_video_path,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
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

        horizontal_command = [
            "ffmpeg",
            "-y",
            "-i",
            resolved_video_path,
            "-map",
            "0:v:0",
            "-map",
            "0:a:0?",
            "-vf",
            f"scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
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
            final_horizontal_legendado,
        ]
        _run_ffmpeg_with_fallback(horizontal_command)

        return {
            "classification": normalized_classification,
            "final_vertical_legendado": final_vertical_legendado,
            "final_horizontal_legendado": final_horizontal_legendado,
        }

    final_horizontal = os.path.join(resolved_output_directory, f"{output_stem}-horizontal.mp4")
    final_vertical = os.path.join(resolved_output_directory, f"{output_stem}-vertical.mp4")

    long_horizontal_command = [
        "ffmpeg",
        "-y",
        "-i",
        resolved_video_path,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        "scale=1920:1080:force_original_aspect_ratio=decrease,pad=1920:1080:(ow-iw)/2:(oh-ih)/2",
        "-c:v",
        "libx264",
        "-c:a",
        "aac",
        final_horizontal,
    ]
    run_ffmpeg(long_horizontal_command)

    long_vertical_command = [
        "ffmpeg",
        "-y",
        "-i",
        resolved_video_path,
        "-map",
        "0:v:0",
        "-map",
        "0:a:0?",
        "-vf",
        vertical_filter,
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
        f"{output_stem}.srt",
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
            output_stem="final",
        )
    except Exception as exc:
        LOGGER.exception("Formatting failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())