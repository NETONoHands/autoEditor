import argparse
import json
import logging
import os
import subprocess
import tempfile
from typing import Any, Dict, List, Optional, Sequence, Tuple


LOGGER = logging.getLogger(__name__)


SpeechInterval = Tuple[float, float]


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


def load_whisper_json(json_path: str) -> Dict[str, Any]:
    resolved_json_path = validate_input_file(json_path, "Whisper JSON")

    with open(resolved_json_path, "r", encoding="utf-8") as json_file:
        return json.load(json_file)


def extract_speech_intervals(transcription_data: Dict[str, Any], silence_threshold: float = 0.5) -> List[SpeechInterval]:
    segments = transcription_data.get("segments", [])
    if not segments:
        words = transcription_data.get("words", [])
        if not words:
            return []

        segments = []
        current_start = None
        current_end = None

        for word in words:
            start = word.get("start")
            end = word.get("end")
            if start is None or end is None:
                continue

            if current_start is None:
                current_start = float(start)
                current_end = float(end)
                continue

            if float(start) - float(current_end) <= silence_threshold:
                current_end = max(float(current_end), float(end))
            else:
                segments.append({"start": current_start, "end": current_end})
                current_start = float(start)
                current_end = float(end)

        if current_start is not None and current_end is not None:
            segments.append({"start": current_start, "end": current_end})

    sorted_segments = sorted(
        (
            segment
            for segment in segments
            if segment.get("start") is not None and segment.get("end") is not None
        ),
        key=lambda segment: float(segment["start"]),
    )

    intervals: List[SpeechInterval] = []
    current_start: Optional[float] = None
    current_end: Optional[float] = None

    for segment in sorted_segments:
        start = float(segment["start"])
        end = float(segment["end"])

        if current_start is None:
            current_start = start
            current_end = end
            continue

        if start - float(current_end) <= silence_threshold:
            current_end = max(float(current_end), end)
        else:
            intervals.append((current_start, float(current_end)))
            current_start = start
            current_end = end

    if current_start is not None and current_end is not None:
        intervals.append((current_start, float(current_end)))

    return intervals


def write_concat_list_file(segment_paths: Sequence[str], work_dir: str) -> str:
    os.makedirs(work_dir, exist_ok=True)
    concat_list_path = os.path.join(work_dir, "concat_list.txt")

    with open(concat_list_path, "w", encoding="utf-8") as list_file:
        for segment_path in segment_paths:
            safe_path = segment_path.replace("\\", "/").replace("'", "'\\''")
            list_file.write(f"file '{safe_path}'\n")

    return concat_list_path


def run_ffmpeg(command: List[str]) -> None:
    LOGGER.info("Running FFmpeg command: %s", " ".join(command))
    subprocess.run(command, check=True)


def cut_video_with_ffmpeg(
    video_path: str,
    intervals: Sequence[SpeechInterval],
    output_path: Optional[str] = None,
    work_dir: Optional[str] = None,
) -> str:
    configure_logging()

    resolved_video_path = validate_input_file(video_path, "Video")
    resolved_output_path = os.path.abspath(output_path or os.path.join(os.getcwd(), "Output", "cortes_brutos.mp4"))
    resolved_work_dir = os.path.abspath(work_dir or os.path.join(os.getcwd(), "Output", "cutter_work"))

    os.makedirs(os.path.dirname(resolved_output_path), exist_ok=True)
    os.makedirs(resolved_work_dir, exist_ok=True)

    if not intervals:
        raise ValueError("No speech intervals were found in the Whisper JSON.")

    segment_paths: List[str] = []

    with tempfile.TemporaryDirectory(dir=resolved_work_dir) as temp_dir:
        for index, (start, end) in enumerate(intervals):
            segment_path = os.path.join(temp_dir, f"segment_{index:04d}.mp4")
            command = [
                "ffmpeg",
                "-y",
                "-i",
                resolved_video_path,
                "-ss",
                str(start),
                "-to",
                str(end),
                "-c",
                "copy",
                segment_path,
            ]
            run_ffmpeg(command)
            segment_paths.append(segment_path)

        concat_list_path = write_concat_list_file(segment_paths, temp_dir)

        concat_command = [
            "ffmpeg",
            "-y",
            "-f",
            "concat",
            "-safe",
            "0",
            "-i",
            concat_list_path,
            "-c",
            "copy",
            resolved_output_path,
        ]
        run_ffmpeg(concat_command)

    if not os.path.isfile(resolved_output_path):
        raise RuntimeError(f"Cropped video was not created: {resolved_output_path}")

    LOGGER.info("Cropped video saved to %s", resolved_output_path)
    return resolved_output_path


def cut_from_whisper_json(
    json_path: str,
    video_path: str,
    output_path: Optional[str] = None,
    silence_threshold: float = 0.5,
) -> Dict[str, Any]:
    configure_logging()

    transcription_data = load_whisper_json(json_path)
    intervals = extract_speech_intervals(transcription_data, silence_threshold=silence_threshold)
    resolved_output_path = cut_video_with_ffmpeg(video_path, intervals, output_path=output_path)

    return {
        "intervals": intervals,
        "output_path": resolved_output_path,
    }


def build_argument_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Cut a video using speech intervals from a Whisper JSON and build cortes_brutos.mp4.",
    )
    parser.add_argument("json", help="Path to the JSON generated by Whisper")
    parser.add_argument("video", help="Path to the original video file")
    parser.add_argument(
        "--output",
        default=None,
        help="Optional output path. Defaults to Output/cortes_brutos.mp4.",
    )
    parser.add_argument(
        "--silence-threshold",
        type=float,
        default=0.5,
        help="Maximum silence gap in seconds allowed inside a speech block.",
    )
    return parser


def main() -> int:
    configure_logging()

    parser = build_argument_parser()
    args = parser.parse_args()

    try:
        cut_from_whisper_json(
            args.json,
            args.video,
            output_path=args.output,
            silence_threshold=args.silence_threshold,
        )
    except Exception as exc:
        LOGGER.exception("Video cutting failed: %s", exc)
        return 1

    return 0


if __name__ == "__main__":
    raise SystemExit(main())