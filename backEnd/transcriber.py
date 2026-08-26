import argparse
import json
import logging
import os
from typing import Any, Dict, List, Optional

import whisper


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


def detect_device() -> str:
    try:
        import torch
    except ImportError:
        return "cpu"

    return "cuda" if torch.cuda.is_available() else "cpu"


def ensure_output_directory(project_root: str) -> str:
    output_directory = os.path.join(project_root, "Output")
    os.makedirs(output_directory, exist_ok=True)
    return output_directory


def load_transcription_model(model_name: str = "base") -> Any:
    device = detect_device()
    LOGGER.info("Loading Whisper model '%s' on device '%s'", model_name, device)
    return whisper.load_model(model_name, device=device)


def normalize_words(segment_id: int, words: Optional[List[Dict[str, Any]]]) -> List[Dict[str, Any]]:
    normalized_words: List[Dict[str, Any]] = []

    for position, word in enumerate(words or []):
        normalized_words.append(
            {
                "segment_id": segment_id,
                "index": position,
                "word": word.get("word"),
                "start": word.get("start"),
                "end": word.get("end"),
                "probability": word.get("probability"),
            }
        )

    return normalized_words


def build_transcript_payload(
    video_path: str,
    subtitle_path: str,
    model_name: str,
    transcription_result: Dict[str, Any],
) -> Dict[str, Any]:
    segments_payload: List[Dict[str, Any]] = []
    words_payload: List[Dict[str, Any]] = []

    for segment_id, segment in enumerate(transcription_result.get("segments", [])):
        segment_words = normalize_words(segment_id, segment.get("words"))
        words_payload.extend(segment_words)

        segments_payload.append(
            {
                "id": segment_id,
                "start": segment.get("start"),
                "end": segment.get("end"),
                "text": segment.get("text"),
                "words": segment_words,
            }
        )

    return {
        "model_name": model_name,
        "video_path": video_path,
        "subtitle_path": subtitle_path,
        "language": transcription_result.get("language"),
        "text": transcription_result.get("text"),
        "segments": segments_payload,
        "words": words_payload,
    }


def transcribe_video_to_json(
    video_path: str,
    subtitle_path: str,
    project_root: Optional[str] = None,
    model_name: str = "base",
    output_json_path: Optional[str] = None,
) -> Dict[str, Any]:
    configure_logging()

    resolved_project_root = os.path.abspath(project_root or os.getcwd())
    resolved_video_path = validate_input_file(video_path, "Video")
    resolved_subtitle_path = validate_input_file(subtitle_path, "Subtitle SRT")
    output_directory = ensure_output_directory(resolved_project_root)

    model = load_transcription_model(model_name)
    use_fp16 = detect_device() == "cuda"

    LOGGER.info("Starting transcription for %s", resolved_video_path)
    transcription_result = model.transcribe(
        resolved_video_path,
        word_timestamps=True,
        fp16=use_fp16,
        verbose=False,
    )

    payload = build_transcript_payload(
        resolved_video_path,
        resolved_subtitle_path,
        model_name,
        transcription_result,
    )

    if output_json_path is None:
        output_filename = f"{os.path.splitext(os.path.basename(resolved_subtitle_path))[0]}.json"
        resolved_output_json_path = os.path.join(output_directory, output_filename)
    else:
        resolved_output_json_path = os.path.abspath(output_json_path)
        os.makedirs(os.path.dirname(resolved_output_json_path) or resolved_project_root, exist_ok=True)

    with open(resolved_output_json_path, "w", encoding="utf-8") as output_file:
        json.dump(payload, output_file, ensure_ascii=False, indent=2)

    LOGGER.info("Transcription JSON saved to %s", resolved_output_json_path)

    return {
        "output_json_path": resolved_output_json_path,
        "payload": payload,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Transcribe a video from A-Roll with Whisper base and export timestamps to JSON.",
    )
    parser.add_argument("video", help="Path to the video file from A-Roll")
    parser.add_argument("srt", help="Path to the respective SRT file")
    parser.add_argument(
        "--project-root",
        default=None,
        help="Project root where Output will be created. Defaults to the current working directory.",
    )
    parser.add_argument(
        "--model",
        default="base",
        help="Whisper model name. Defaults to base.",
    )
    parser.add_argument(
        "--output-json",
        default=None,
        help="Optional custom JSON output path.",
    )
    return parser


def main() -> int:
    configure_logging()

    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        transcribe_video_to_json(
            args.video,
            args.srt,
            project_root=args.project_root,
            model_name=args.model,
            output_json_path=args.output_json,
        )
    except Exception as exc:
        LOGGER.exception("Transcription failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())