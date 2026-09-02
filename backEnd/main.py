import argparse
import logging
import os
import tempfile
from typing import Any, Dict, Optional

from analyzer import analyze_video
from cutter import DEFAULT_SILENCE_THRESHOLD, cut_video_with_ffmpeg, extract_speech_intervals
from enhancer import enhance_video_with_ffmpeg
from formatter import format_video_by_classification
from pipeline_contracts import (
    InputValidationError,
    build_output_stem,
    validate_dependencies,
    validate_srt_file,
    validate_output_video,
    validate_video_file,
    validate_vertical_output,
)
from segmentation import (
    extract_srt_intervals,
    rebase_srt,
    rebase_srt_to_intervals,
    split_at_speech_boundaries,
    split_video_with_ffmpeg,
)
from transcriber import transcribe_video_to_json


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
    subtitle_path: str,
    project_root: Optional[str] = None,
    lut_path: str = DEFAULT_LUT_PATH,
    output_directory: Optional[str] = None,
    output_name: str = "",
    remove_silence: bool = False,
    silence_threshold: float = DEFAULT_SILENCE_THRESHOLD,
    face_tracking: bool = True,
    display_title: str = "",
    safe_area: float = 0.0,
) -> Dict[str, Any]:
    configure_logging()

    resolved_project_root = os.path.abspath(project_root or os.getcwd())
    resolved_output_directory = os.path.abspath(
        output_directory or os.path.join(resolved_project_root, "Output")
    )
    os.makedirs(resolved_output_directory, exist_ok=True)

    if not output_name.strip():
        raise InputValidationError("Informe o nome da edição.")

    validate_dependencies()
    resolved_video_path = validate_video_file(raw_video_path).path
    resolved_subtitle_path = validate_srt_file(subtitle_path)
    metadata = analyze_video(resolved_video_path)
    classification = metadata.classification
    output_stem = build_output_stem(output_name)
    resolved_lut_path = lut_path
    if not os.path.isabs(resolved_lut_path):
        resolved_lut_path = os.path.join(resolved_project_root, resolved_lut_path)
    LOGGER.info("Step 1/3 completed: analyzer (%s)", classification)

    subtitle_content = open(resolved_subtitle_path, encoding="utf-8-sig").read()
    windows = split_at_speech_boundaries(
        metadata.duration_seconds,
        extract_srt_intervals(subtitle_content),
    )
    is_split = len(windows) > 1
    if is_split:
        LOGGER.info("Vídeo longo será dividido em %s partes antes da edição", len(windows))

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
        published_srt_paths: list[str] = []
        vertical_output_metadata: list[dict[str, object]] = []
        for index, source_path in enumerate(source_paths, start=1):
            part_stem = build_output_stem(output_name, index) if is_split else output_stem
            part_srt_path = resolved_subtitle_path
            part_srt_content = subtitle_content
            if is_split:
                part_srt_path = os.path.join(work_directory, f"{part_stem}.srt")
                part_srt_content = rebase_srt(subtitle_content, windows[index - 1])
                with open(part_srt_path, "w", encoding="utf-8") as part_file:
                    part_file.write(part_srt_content)

            edit_source_path = source_path
            if remove_silence:
                transcript_path = os.path.join(work_directory, f"{part_stem}-transcript.json")
                transcript = transcribe_video_to_json(
                    source_path,
                    part_srt_path,
                    project_root=work_directory,
                    output_json_path=transcript_path,
                )
                intervals = extract_speech_intervals(
                    transcript["payload"],
                    silence_threshold=silence_threshold,
                )
                final_srt_path = os.path.join(
                    resolved_output_directory,
                    f"{part_stem}.srt",
                )
                with open(final_srt_path, "w", encoding="utf-8") as final_srt_file:
                    final_srt_file.write(rebase_srt_to_intervals(part_srt_content, intervals))
                validate_srt_file(final_srt_path)
                published_srt_paths.append(os.path.basename(final_srt_path))
                preserved_intervals.extend(
                    {"start": start, "end": end, "duration": end - start}
                    for start, end in intervals
                )
                cut_path = os.path.join(work_directory, f"{part_stem}-silencio-removido.mp4")
                edit_source_path = cut_video_with_ffmpeg(
                    source_path,
                    intervals,
                    output_path=cut_path,
                    work_dir=work_directory,
                )
                cut_video_metadata.append(validate_output_video(edit_source_path))
                part_srt_path = final_srt_path

            base_treated_path = os.path.join(resolved_output_directory, f"{part_stem}-tratado.mp4")
            if os.path.exists(base_treated_path):
                raise InputValidationError(f"A saída já existe e não será sobrescrita: {base_treated_path}")
            treated_video_path = enhance_video_with_ffmpeg(
                edit_source_path,
                output_path=base_treated_path,
                    lut_path=resolved_lut_path,
            )
            formatting_result = format_video_by_classification(
                treated_video_path,
                part_srt_path,
                classification,
                output_directory=resolved_output_directory,
                output_stem=part_stem,
                title=display_title,
                face_tracking=face_tracking,
                safe_area=safe_area,
            )
            if remove_silence and not os.path.isfile(os.path.join(resolved_output_directory, f"{part_stem}.srt")):
                published_srt_path = os.path.join(resolved_output_directory, f"{part_stem}.srt")
                with open(published_srt_path, "w", encoding="utf-8") as published_srt_file:
                    with open(part_srt_path, encoding="utf-8-sig") as source_srt_file:
                        published_srt_file.write(source_srt_file.read())
                validate_srt_file(published_srt_path)
                published_srt_paths.append(os.path.basename(published_srt_path))
            treated_paths.append(treated_video_path)
            formatted_outputs.append(formatting_result)

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
            "published_srt_paths": published_srt_paths,
            "vertical_outputs": vertical_output_metadata,
        },
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Executa a edição Tapa na Lata.",
    )
    parser.add_argument("video", help="Path to the raw input video")
    parser.add_argument("srt", help="Path to the respective SRT subtitle file")
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
            args.srt,
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