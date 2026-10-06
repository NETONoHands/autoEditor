import argparse
import json
import logging
import os
import subprocess
from typing import Any, Dict, List, Optional

from face_tracker import detect_face_crop_x
from json_to_ass import convert_json_to_ass
from pipeline_contracts import normalize_captions_payload, serialize_captions
from segmentation import SubtitleCue, _format_timestamp


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


def has_manual_crop(
    crop_x: Optional[int],
    crop_y: Optional[int],
    crop_w: Optional[int],
    crop_h: Optional[int],
) -> bool:
    return (
        crop_x is not None
        and crop_y is not None
        and crop_w is not None
        and crop_h is not None
        and crop_w > 0
        and crop_h > 0
    )


def build_vertical_crop_filter(
    crop_x: Optional[int] = None,
    crop_y: Optional[int] = None,
    crop_w: Optional[int] = None,
    crop_h: Optional[int] = None,
    face_crop_x: Optional[int] = None,
) -> str:
    if (
        crop_x is not None
        and crop_y is not None
        and crop_w is not None
        and crop_h is not None
        and crop_w > 0
        and crop_h > 0
    ):
        # Keep the full source height; use the selected camera box only to anchor the horizontal crop.
        safe_x = max(0, int(crop_x))
        crop_width = int(crop_w)
        crop_x_expression = (
            f"max(0\\,min(iw-ow\\,{safe_x}+{crop_width}/2-ow/2))"
        )
        crop_filter = f"crop=min(ih*9/16\\,iw):ih:{crop_x_expression}:0"
    elif face_crop_x is None:
        crop_filter = build_center_crop_9x16_filter()
    else:
        crop_x_expression = f"max(0\\,min(iw-ow\\,{max(0, int(face_crop_x))}))"
        crop_filter = f"crop=min(ih*9/16\\,iw):ih:{crop_x_expression}:0"

    return (
        f"{crop_filter},"
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
    )


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
    crop_y: Optional[int] = None,
    crop_w: Optional[int] = None,
    crop_h: Optional[int] = None,
    face_crop_x: Optional[int] = None,
) -> str:
    filters = [
        build_vertical_crop_filter(crop_x, crop_y, crop_w, crop_h, face_crop_x),
        build_vertical_safe_area_filter(safe_area),
    ]
    title_y, _ = _safe_text_margins(safe_area)
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
    subtitle_filter = f"ass=filename='{escape_path_for_ffmpeg_filter(subtitle_path)}'"
    subtitle_fonts_dir = FONTS_DIR if os.path.isdir(FONTS_DIR) else None
    if subtitle_fonts_dir:
        subtitle_filter += f":fontsdir='{escape_path_for_ffmpeg_filter(subtitle_fonts_dir)}'"
    filters.append(subtitle_filter)
    return ",".join(filters)


def build_srt_from_captions(
    captions: List[Dict[str, Any]],
    words_per_line: int = 4,
    lines_per_cue: int = 2,
) -> str:
    # Legendas queimadas agrupam palavras em cues; os tempos originais do JSON não são arredondados.
    max_words = words_per_line * lines_per_cue
    chunks = [captions[index:index + max_words] for index in range(0, len(captions), max_words)]
    cues = [
        SubtitleCue(
            "\n".join(
                " ".join(str(word["word"]) for word in chunk[line:line + words_per_line])
                for line in range(0, len(chunk), words_per_line)
            ),
            float(chunk[0]["start"]),
            float(chunk[-1]["end"]),
        )
        for chunk in chunks
        if chunk
    ]
    blocks = [
        "\n".join([str(index), f"{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}", cue.text])
        for index, cue in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


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
    captions: List[Dict[str, Any]],
    classification: str,
    output_directory: Optional[str] = None,
    output_stem: str = "final",
    title: str = "",
    face_tracking: bool = True,
    safe_area: float = 0.0,
    crop_x: Optional[int] = None,
    crop_y: Optional[int] = None,
    crop_w: Optional[int] = None,
    crop_h: Optional[int] = None,
) -> Dict[str, str]:
    configure_logging()

    normalized_classification = classification.strip().lower()
    if normalized_classification not in {"short", "long"}:
        raise ValueError("classification must be 'short' or 'long'")

    resolved_video_path = validate_input_file(base_tratada_path, "Input video")
    resolved_output_directory = os.path.abspath(
        output_directory or ensure_output_directory(os.getcwd())
    )
    os.makedirs(resolved_output_directory, exist_ok=True)

    captions_json_path = os.path.join(resolved_output_directory, f"{output_stem}.json")
    with open(captions_json_path, "w", encoding="utf-8") as captions_file:
        json.dump(captions, captions_file, ensure_ascii=False, indent=2)
    LOGGER.info("Saved captions JSON to %s", captions_json_path)
    _, subtitle_margin_v = _safe_text_margins(safe_area)
    subtitle_margin_h = int(round(1080 * safe_area))
    captions_ass_path = convert_json_to_ass(
        captions_json_path,
        os.path.join(resolved_output_directory, f"{output_stem}.ass"),
        margin_h=subtitle_margin_h,
        margin_v=subtitle_margin_v,
    )

    # Crop manual completo dispensa o face_tracker.
    face_crop_x = None
    if not has_manual_crop(crop_x, crop_y, crop_w, crop_h):
        face_crop_x = resolve_vertical_crop_x(resolved_video_path, face_tracking)
    vertical_filter = build_vertical_composite_filter(
        captions_ass_path,
        title=title,
        crop_x=crop_x,
        safe_area=safe_area,
        crop_y=crop_y,
        crop_w=crop_w,
        crop_h=crop_h,
        face_crop_x=face_crop_x,
    )
    return _render_classified_outputs(
        resolved_video_path,
        vertical_filter,
        normalized_classification,
        resolved_output_directory,
        output_stem,
        captions_json_path,
        captions_ass_path,
    )


def _render_classified_outputs(
    resolved_video_path: str,
    vertical_filter: str,
    normalized_classification: str,
    resolved_output_directory: str,
    output_stem: str,
    captions_json_path: str,
    captions_ass_path: str,
) -> Dict[str, str]:

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
            "captions_json": captions_json_path,
            "captions_ass": captions_ass_path,
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

    return {
        "classification": normalized_classification,
        "final_horizontal": final_horizontal,
        "final_vertical": final_vertical,
        "captions_json": captions_json_path,
        "captions_ass": captions_ass_path,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Format base_tratada.mp4 outputs based on short/long classification.",
    )
    parser.add_argument("video", help="Path to base_tratada.mp4")
    parser.add_argument("captions", help="Path to the captions JSON file ([{word, start, end}, ...])")
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
        with open(args.captions, encoding="utf-8-sig") as captions_file:
            captions = serialize_captions(normalize_captions_payload(json.load(captions_file)))
        format_video_by_classification(
            args.video,
            captions,
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