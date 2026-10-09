import argparse
import logging
import os
import tempfile
from typing import Any, Callable, Dict, Optional

from analyzer import analyze_video
from cutter import (
    DEFAULT_SILENCE_THRESHOLD,
    compute_auto_remove_intervals,
    compute_keep_intervals,
    cut_video_with_ffmpeg,
)
from enhancer import enhance_video_with_ffmpeg
from formatter import format_video_by_classification
from pipeline_contracts import (
    InputValidationError,
    build_output_stem,
    load_captions_json,
    validate_dependencies,
    validate_output_video,
    validate_video_file,
    validate_vertical_output,
)
from segmentation import (
    extract_caption_intervals,
    rebase_captions_to_intervals,
    rebase_captions_to_window,
    split_at_speech_boundaries,
    split_video_with_ffmpeg,
)


LOGGER = logging.getLogger(__name__)
DEFAULT_LUT_PATH = os.path.join(
    os.path.dirname(os.path.abspath(__file__)),
    "assets",
    "color",
    "Assets",
    "Vivid LUTs 3.cube",
)


def configure_logging() -> None:
    if logging.getLogger().handlers:
        return

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s | %(levelname)s | %(name)s | %(message)s",
    )


def run_pipeline(
    raw_video_path: str,
    captions_path: str,
    project_root: Optional[str] = None,
    lut_path: str = DEFAULT_LUT_PATH,
    output_directory: Optional[str] = None,
    output_name: str = "",
    remove_silence: bool = False,
    silence_threshold: float = DEFAULT_SILENCE_THRESHOLD,
    face_tracking: bool = True,
    display_title: str = "",
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
    on_phase: Optional[Callable[[str], None]] = None,
    on_log: Optional[Callable[[str], None]] = None,
) -> Dict[str, Any]:
    def phase(name: str) -> None:
        if on_phase:
            on_phase(name)

    def log(message: str) -> None:
        if on_log:
            on_log(message)

    configure_logging()
    phase("A extrair áudio e metadados")
    log("A validar dependências do sistema (FFmpeg, etc.)")
    validate_dependencies()
    
    resolved_project_root = os.path.abspath(project_root or os.getcwd())
    resolved_output_directory = os.path.abspath(
        output_directory or os.path.join(resolved_project_root, "Output")
    )
    os.makedirs(resolved_output_directory, exist_ok=True)

    if not output_name.strip():
        raise InputValidationError("Informe o nome da edição.")

    log(f"A carregar vídeo: {os.path.basename(raw_video_path)}")
    resolved_video_path = validate_video_file(raw_video_path).path
    
    log("A validar legendas SRT")
    validate_captions_srt(captions_path)
    
    log("A analisar ficheiro de vídeo para metadados")
    metadata = analyze_video(resolved_video_path)
    classification = metadata.classification
    output_stem = build_output_stem(output_name)
    resolved_lut_path = lut_path
    if not os.path.isabs(resolved_lut_path):
        resolved_lut_path = os.path.join(resolved_project_root, resolved_lut_path)
    
    log(f"Metadados extraídos: {metadata.duration_seconds:.1f}s, resolução {metadata.width}x{metadata.height}")

    windows = split_at_speech_boundaries(
        metadata.duration_seconds,
        extract_caption_intervals(captions),
    )
    is_split = len(windows) > 1
    if is_split:
        LOGGER.info("Vídeo longo será dividido em %s partes antes da edição", len(windows))
        log(f"Vídeo dividido em {len(windows)} partes")

    with tempfile.TemporaryDirectory(prefix="tapa-na-lata-", dir=resolved_output_directory) as work_directory:
        if is_split:
            source_paths = split_video_with_ffmpeg(
                resolved_video_path,
                windows,
                work_directory,
            )
        else:
            source_paths = [resolved_video_path]

        formatted_outputs: list[Dict[str, str]] = []
        treated_paths: list[str] = []
        preserved_intervals: list[dict[str, float]] = []
        cut_video_metadata: list[dict[str, object]] = []
        published_captions_paths: list[str] = []
        vertical_output_metadata: list[dict[str, object]] = []
        for index, source_path in enumerate(source_paths, start=1):
            if is_split:
                log(f"A processar parte {index} de {len(source_paths)}")
            part_stem = build_output_stem(output_name, index) if is_split else output_stem
            part_captions = rebase_captions_to_window(captions, windows[index - 1]) if is_split else captions

            edit_source_path = source_path
            if remove_silence:
                phase("A analisar silêncios e disfluências")
                part_duration = windows[index - 1].duration if is_split else metadata.duration_seconds
                intervals = compute_keep_intervals(
                    compute_auto_remove_intervals(
                        source_path,
                        part_captions,
                        part_duration,
                        silence_duration=silence_threshold,
                    ),
                    part_duration,
                )
                removed_seconds = max(0.0, part_duration - sum(end - start for start, end in intervals))
                log(f"{removed_seconds:.1f} segundos de silêncio/disfluências detetados (parte {index}/{len(source_paths)})")
                part_captions = rebase_captions_to_intervals(part_captions, intervals)
                preserved_intervals.extend(
                    {"start": start, "end": end, "duration": end - start}
                    for start, end in intervals
                )
                cut_path = os.path.join(work_directory, f"{part_stem}-silencio-removido.mp4")
                log("A cortar segmentos de silêncio com FFmpeg")
                edit_source_path = cut_video_with_ffmpeg(
                    source_path,
                    intervals,
                    output_path=cut_path,
                    work_dir=work_directory,
                )
                cut_video_metadata.append(validate_output_video(edit_source_path))

            phase("A aplicar parâmetros de câmara")
            log("A aplicar LUT e tratamento de cor")
            base_treated_path = os.path.join(resolved_output_directory, f"{part_stem}-tratado.mp4")
            if os.path.exists(base_treated_path):
                raise InputValidationError(f"A saída já existe e não será sobrescrita: {base_treated_path}")
            treated_video_path = enhance_video_with_ffmpeg(
                edit_source_path,
                output_path=base_treated_path,
                    lut_path=resolved_lut_path,
            )
            phase("A renderizar vídeo final")
            log("Iniciando renderização FFmpeg")
            formatting_result = format_video_by_classification(
                treated_video_path,
                captions_path,
                classification,
                output_directory=resolved_output_directory,
                output_stem=part_stem,
                title=display_title,
                face_tracking=face_tracking,
                safe_area=safe_area,
                crop_x=crop_x,
                crop_y=crop_y,
                crop_w=crop_w,
                crop_h=crop_h,
                content_crop_x=content_crop_x,
                content_crop_y=content_crop_y,
                content_crop_w=content_crop_w,
                content_crop_h=content_crop_h,
                subtitle_font=subtitle_font,
                subtitle_color_preset=subtitle_color_preset,
                subtitle_position_y=subtitle_position_y,
                subtitle_scale=subtitle_scale,
            )
            published_captions_paths.append(os.path.basename(formatting_result["captions_json"]))
            treated_paths.append(treated_video_path)
            formatted_outputs.append(formatting_result)
            log(f"Renderização concluída (parte {index}/{len(source_paths)})")

            for output_path in formatting_result.values():
                if isinstance(output_path, str) and output_path.lower().endswith(".mp4"):
                    validate_output_video(output_path)
                    if "vertical" in os.path.basename(output_path).lower():
                        vertical_output_metadata.append(validate_vertical_output(output_path))

    LOGGER.info("Step 2/3 completed: enhancer; Step 3/3 completed: formatter")

    LOGGER.info(
        "FFmpeg exports remain NVENC 10M in enhancer and formatter encoded outputs"
    )

    return {
        "project_root": resolved_project_root,
        "output_directory": resolved_output_directory,
        "classification": classification,
        "metadata": metadata,
        "output_name": output_stem,
        "face_tracking": face_tracking,
        "base_treated_path": treated_paths[0] if len(treated_paths) == 1 else treated_paths,
        "formatted_outputs": formatted_outputs[0] if len(formatted_outputs) == 1 else formatted_outputs,
        "parts": [
            {"start": window.start, "end": window.end, "duration": window.duration}
            for window in windows
        ],
        "silence_removal": {
            "enabled": remove_silence,
            "silence_threshold": silence_threshold,
            "preserved_intervals": preserved_intervals,
            "cut_videos": cut_video_metadata,
            "published_captions_paths": published_captions_paths,
            "vertical_outputs": vertical_output_metadata,
        },
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Executa a edição Tapa na Lata.",
    )
    parser.add_argument("video", help="Path to the raw input video")
    parser.add_argument("captions", help="Path to the captions JSON file ([{word, start, end}, ...])")
    parser.add_argument("name", help="Nome da edição; palavras serão separadas por hífens")
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root folder. Defaults to current working directory.",
    )
    parser.add_argument(
        "--lut-path",
        default=DEFAULT_LUT_PATH,
        help="Placeholder LUT file path used during enhancement.",
    )
    parser.add_argument(
        "--output-dir",
        default=None,
        help="Optional output directory. Defaults to <project-root>/Output.",
    )
    parser.add_argument(
        "--remove-silence",
        action="store_true",
        help="Transcreve e remove silêncios usando tolerância padrão de 0,3 segundo.",
    )
    parser.add_argument(
        "--silence-threshold",
        type=float,
        default=DEFAULT_SILENCE_THRESHOLD,
        help="Gap máximo entre falas preservadas, em segundos.",
    )
    parser.add_argument(
        "--no-face-tracking",
        action="store_true",
        help="Desativa o posicionamento vertical baseado em rosto.",
    )
    return parser


def main() -> int:
    configure_logging()

    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        run_pipeline(
            args.video,
            args.captions,
            output_name=args.name,
            project_root=args.project_root,
            lut_path=args.lut_path,
            output_directory=args.output_dir,
            remove_silence=args.remove_silence,
            silence_threshold=args.silence_threshold,
            face_tracking=not args.no_face_tracking,
        )
    except InputValidationError as exc:
        LOGGER.error("Entrada inválida: %s", exc)
        return 2
    except Exception as exc:
        LOGGER.exception("Pipeline failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())