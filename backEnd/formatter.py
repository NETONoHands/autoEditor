import argparse
import json
import logging
import math
import os
import shutil
import subprocess
from typing import Any, Dict, List, Optional, Tuple

from face_tracker import detect_face_crop_x
from json_to_ass import convert_json_to_ass
from pipeline_contracts import normalize_captions_payload, serialize_captions
from segmentation import SubtitleCue, _format_timestamp


# Estilo de acessibilidade para legendas verticais (TikTok/Reels)
# Garante legibilidade e posicionamento fora da UI nativa.
VERTICAL_SUBTITLE_FORCE_STYLE = (
    "Fontname=Arial,Fontsize=90,Bold=-1,"
    "PrimaryColour=&H00FFFFFF,OutlineColour=&H00000000,BackColour=&H80000000,"
    "BorderStyle=1,Outline=4,Shadow=2,Alignment=2,MarginV=450"
)


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


def _manual_crop_ints(
    crop_x: Optional[int],
    crop_y: Optional[int],
    crop_w: Optional[int],
    crop_h: Optional[int],
) -> Optional[Tuple[int, int, int, int]]:
    """Devolve (x, y, w, h) inteiros e seguros, ou None se algum valor for nulo/inválido."""
    values = []
    for value in (crop_x, crop_y, crop_w, crop_h):
        if value is None or isinstance(value, bool):
            return None
        try:
            number = float(value)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(number):
            return None
        values.append(int(number))
    x, y, w, h = values
    if w <= 0 or h <= 0:
        return None
    return max(0, x), max(0, y), w, h


def has_manual_crop(
    crop_x: Optional[int],
    crop_y: Optional[int],
    crop_w: Optional[int],
    crop_h: Optional[int],
) -> bool:
    return _manual_crop_ints(crop_x, crop_y, crop_w, crop_h) is not None


def build_vertical_crop_filter(
    crop_x: Optional[int] = None,
    crop_y: Optional[int] = None,
    crop_w: Optional[int] = None,
    crop_h: Optional[int] = None,
    face_crop_x: Optional[int] = None,
) -> str:
    manual_crop = _manual_crop_ints(crop_x, crop_y, crop_w, crop_h)
    if manual_crop is not None:
        x, y, w, h = manual_crop
        return (
            f"crop={w}:{h}:{x}:{y},"
            "scale=1080:1920:force_original_aspect_ratio=decrease,"
            "pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
        )

    if face_crop_x is None:
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
    if not 0.0 <= safe_area <= 0.2:
        raise ValueError("safe_area must be between 0.0 and 0.2")
    filters = [
        build_vertical_crop_filter(crop_x, crop_y, crop_w, crop_h, face_crop_x),
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
    escaped_subtitle_path = escape_path_for_ffmpeg_filter(subtitle_path)
    subtitle_filter = f"subtitles='{escaped_subtitle_path}':force_style='{VERTICAL_SUBTITLE_FORCE_STYLE}'"
    subtitle_fonts_dir = FONTS_DIR if os.path.isdir(FONTS_DIR) else None
    if subtitle_fonts_dir:
        subtitle_filter += f":fontsdir='{escape_path_for_ffmpeg_filter(subtitle_fonts_dir)}'"
    filters.append(subtitle_filter)
    return ",".join(filters)


def has_split_screen_crops(
    crop_x: Optional[int],
    crop_y: Optional[int],
    crop_w: Optional[int],
    crop_h: Optional[int],
    content_crop_x: Optional[int],
    content_crop_y: Optional[int],
    content_crop_w: Optional[int],
    content_crop_h: Optional[int],
) -> bool:
    return has_manual_crop(crop_x, crop_y, crop_w, crop_h) and has_manual_crop(
        content_crop_x, content_crop_y, content_crop_w, content_crop_h
    )


def build_split_screen_filter(
    crop_x: int,
    crop_y: int,
    crop_w: int,
    crop_h: int,
    content_crop_x: int,
    content_crop_y: int,
    content_crop_w: int,
    content_crop_h: int,
    subtitle_path: str,
    title: str = "",
    safe_area: float = 0.0,
) -> str:
    """Monta o -filter_complex 1080x1920: câmera no topo, conteúdo colado embaixo, título e legenda por cima."""
    cam_crop = _manual_crop_ints(crop_x, crop_y, crop_w, crop_h)
    content_crop = _manual_crop_ints(content_crop_x, content_crop_y, content_crop_w, content_crop_h)
    if cam_crop is None or content_crop is None:
        raise ValueError("Os cortes da câmera e do conteúdo devem ser válidos (não nulos e com largura/altura > 0).")
    cam_x, cam_y, cam_w, cam_h = cam_crop
    cont_x, cont_y, cont_w, cont_h = content_crop
    escaped_subtitle_path = escape_path_for_ffmpeg_filter(subtitle_path)
    subtitle_filter = f"subtitles='{escaped_subtitle_path}':force_style='{VERTICAL_SUBTITLE_FORCE_STYLE}'"
    if os.path.isdir(FONTS_DIR):
        subtitle_filter += f":fontsdir='{escape_path_for_ffmpeg_filter(FONTS_DIR)}'"
    title_node = "[composed]null[with_title]"
    if title.strip():
        fontfile = resolve_title_fontfile()
        if fontfile is None:
            LOGGER.warning("Title font not found; skipping drawtext to avoid FFmpeg fontconfig failure")
        else:
            title_y, _ = _safe_text_margins(safe_area)
            title_node = (
                "[composed]drawtext="
                f"fontfile='{escape_path_for_ffmpeg_filter(fontfile)}':"
                f"text='{escape_drawtext_text(title.strip())}':"
                "fontsize=42:fontcolor=yellow:"
                f"borderw=3:bordercolor=black:x=(w-text_w)/2:y={title_y}:"
                "enable='between(t,0,5)':alpha='if(lt(t,0.5),t/0.5,if(lt(t,4.5),1,(5-t)/0.5))'"
                "[with_title]"
            )
    return (
        "[0:v]split=3[bg_orig][cam_orig][cont_orig];"
        "[bg_orig]scale=1080:1920:force_original_aspect_ratio=increase,"
        "crop=1080:1920,boxblur=20:20[bg];"
        f"[cam_orig]crop={cam_w}:{cam_h}:{cam_x}:{cam_y},"
        "scale=1080:-1[cam_scaled];"
        f"[cont_orig]crop={cont_w}:{cont_h}:{cont_x}:{cont_y},scale=1080:-1[cont_scaled];"
        "[bg][cont_scaled]overlay=0:1920-h[bg_with_cont];"
        "[bg_with_cont][cam_scaled]overlay=0:0[composed];"
        f"{title_node};"
        f"[with_title]{subtitle_filter},format=yuv420p[vid_out]"
    )


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
    captions_path: str,
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
    content_crop_x: Optional[int] = None,
    content_crop_y: Optional[int] = None,
    content_crop_w: Optional[int] = None,
    content_crop_h: Optional[int] = None,
    subtitle_font: str = "Arial",
    subtitle_color_preset: str = "white_black_outline",
    subtitle_position_y: str = "bottom",
    subtitle_scale: float = 1.0,
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

    # O SRT de entrada agora é usado como base direta para as legendas.
    # Copiamos para o diretório de saída para manter a consistência do projeto.
    final_srt_path = os.path.join(resolved_output_directory, f"{output_stem}.srt")
    shutil.copy2(captions_path, final_srt_path)
    LOGGER.info("Copied SRT to %s", final_srt_path)
    
    # Se precisarmos de ASS para filtros específicos ou legado, podemos converter aqui.
    # Mas o filtro 'subtitles' do FFmpeg aceita SRT diretamente.
    # Vamos manter captions_ass_path apontando para o próprio SRT ou converter se necessário.
    # Por agora, seguindo a lógica de simplificação:
    captions_ass_path = final_srt_path 
    captions_json_path = os.path.join(resolved_output_directory, f"{output_stem}.json")
    # Criamos um JSON vazio para não quebrar dependências que esperam o arquivo.
    with open(captions_json_path, "w", encoding="utf-8") as f:
        json.dump([], f)

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
    split_screen_filter = None
    if has_split_screen_crops(
        crop_x, crop_y, crop_w, crop_h,
        content_crop_x, content_crop_y, content_crop_w, content_crop_h,
    ):
        split_screen_filter = build_split_screen_filter(
            crop_x, crop_y, crop_w, crop_h,
            content_crop_x, content_crop_y, content_crop_w, content_crop_h,
            captions_ass_path,
            title=title,
            safe_area=safe_area,
        )
    return _render_classified_outputs(
        resolved_video_path,
        vertical_filter,
        normalized_classification,
        resolved_output_directory,
        output_stem,
        captions_json_path,
        captions_ass_path,
        split_screen_filter,
    )


def _render_classified_outputs(
    resolved_video_path: str,
    vertical_filter: str,
    normalized_classification: str,
    resolved_output_directory: str,
    output_stem: str,
    captions_json_path: str,
    captions_ass_path: str,
    split_screen_filter: Optional[str] = None,
) -> Dict[str, str]:
    # Com os dois recortes definidos usa -filter_complex; senão mantém o -vf anterior.
    if split_screen_filter:
        vertical_video_args = ["-filter_complex", split_screen_filter, "-map", "[vid_out]"]
    else:
        vertical_video_args = ["-map", "0:v:0", "-vf", vertical_filter]

    if normalized_classification == "short":
        final_vertical_legendado = os.path.join(
            resolved_output_directory,
            f"{output_stem}-vertical-legendado.mp4",
        )
        final_horizontal_legendado = os.path.join(
            resolved_output_directory,
            f"{output_stem}-horizontal-legendado.mp4",
        )

        short_command = [
            "ffmpeg",
            "-y",
            "-i",
            resolved_video_path,
            *vertical_video_args,
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
        *vertical_video_args,
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