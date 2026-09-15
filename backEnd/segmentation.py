import re
import subprocess
from math import isfinite
from dataclasses import dataclass
from typing import Iterable, Sequence

from pipeline_contracts import MAX_PART_DURATION_SECONDS


SpeechInterval = tuple[float, float]


@dataclass(frozen=True)
class TimeWindow:
    start: float
    end: float

    @property
    def duration(self) -> float:
        return self.end - self.start


@dataclass(frozen=True)
class SubtitleCue:
    text: str
    start: float
    end: float


def extract_caption_intervals(captions: Sequence[dict]) -> list[SpeechInterval]:
    return [(float(item["start"]), float(item["end"])) for item in captions]


def rebase_captions_to_window(captions: Sequence[dict], window: TimeWindow) -> list[dict]:
    rebased: list[dict] = []
    for item in captions:
        start = float(item["start"])
        end = float(item["end"])
        if end <= window.start or start >= window.end:
            continue
        clipped_start = max(start, window.start) - window.start
        clipped_end = min(end, window.end) - window.start
        rebased.append({"word": item["word"], "start": clipped_start, "end": clipped_end})
    return rebased


def rebase_captions_to_intervals(
    captions: Sequence[dict],
    keep_intervals: Sequence[SpeechInterval],
) -> list[dict]:
    normalized_intervals = sorted((float(start), float(end)) for start, end in keep_intervals)
    rebased: list[dict] = []
    for item in captions:
        start = float(item["start"])
        end = float(item["end"])
        for interval_start, interval_end in normalized_intervals:
            overlap_start = max(start, interval_start)
            overlap_end = min(end, interval_end)
            if overlap_end <= overlap_start:
                continue
            elapsed_before = 0.0
            previous_end = 0.0
            for previous_start, previous_interval_end in normalized_intervals:
                if previous_start > overlap_start:
                    break
                elapsed_before += max(0.0, previous_start - previous_end)
                previous_end = previous_interval_end
            rebased.append(
                {
                    "word": item["word"],
                    "start": overlap_start - elapsed_before,
                    "end": overlap_end - elapsed_before,
                }
            )
            break
    return rebased


def normalize_subtitle_cues(
    cues: Sequence[SubtitleCue],
    words_per_line: int = 4,
    lines_per_cue: int = 2,
) -> list[SubtitleCue]:
    if words_per_line <= 0 or lines_per_cue <= 0:
        raise ValueError("Os limites de palavras e linhas devem ser positivos.")
    max_words = words_per_line * lines_per_cue
    normalized: list[SubtitleCue] = []
    for cue in cues:
        words = cue.text.split()
        if not words:
            continue
        chunks = [words[index:index + max_words] for index in range(0, len(words), max_words)]
        duration = cue.end - cue.start
        for chunk_index, chunk in enumerate(chunks):
            start = cue.start + duration * chunk_index / len(chunks)
            end = cue.start + duration * (chunk_index + 1) / len(chunks)
            lines = [
                " ".join(chunk[index:index + words_per_line])
                for index in range(0, len(chunk), words_per_line)
            ]
            normalized.append(SubtitleCue("\n".join(lines), start, end))
    return normalized


def _nearest_speech_end_before(
    target: float,
    window_start: float,
    speech_intervals: Sequence[SpeechInterval],
    max_lookback: float,
) -> float | None:
    lower_bound = max(window_start, target - max_lookback)
    candidates = [
        end
        for start, end in speech_intervals
        if lower_bound <= end <= target and end > start
    ]
    return max(candidates) if candidates else None


def split_at_speech_boundaries(
    duration_seconds: float,
    speech_intervals: Iterable[SpeechInterval] = (),
    max_duration: float = MAX_PART_DURATION_SECONDS,
    max_boundary_lookback: float = 30.0,
) -> list[TimeWindow]:
    if duration_seconds <= 0:
        raise ValueError("A duração deve ser maior que zero.")
    if max_duration <= 0:
        raise ValueError("A duração máxima da parte deve ser maior que zero.")
    if max_boundary_lookback < 0:
        raise ValueError("A janela de busca do limite não pode ser negativa.")

    intervals = sorted((float(start), float(end)) for start, end in speech_intervals)
    windows: list[TimeWindow] = []
    window_start = 0.0

    while duration_seconds - window_start > max_duration:
        technical_limit = window_start + max_duration
        speech_end = _nearest_speech_end_before(
            technical_limit,
            window_start,
            intervals,
            max_boundary_lookback,
        )
        boundary = speech_end or technical_limit
        windows.append(TimeWindow(window_start, boundary))
        window_start = boundary

    windows.append(TimeWindow(window_start, float(duration_seconds)))
    return windows


_TIMESTAMP_PATTERN = re.compile(
    r"^(\d{2}):(\d{2}):(\d{2}),(\d{3})\s+-->\s+(\d{2}):(\d{2}):(\d{2}),(\d{3})(.*)$"
)


def _parse_timestamp(value: str) -> float:
    hours, minutes, seconds_milliseconds = value.split(":", 2)
    seconds, milliseconds = seconds_milliseconds.split(",", 1)
    return int(hours) * 3600 + int(minutes) * 60 + int(seconds) + int(milliseconds) / 1000


def parse_srt(srt_content: str) -> list[SubtitleCue]:
    blocks = re.split(r"\r?\n\s*\r?\n", srt_content.strip())
    cues: list[SubtitleCue] = []
    previous_start = -1.0
    for block_number, block in enumerate(blocks, start=1):
        lines = block.splitlines()
        if len(lines) < 3:
            raise ValueError(f"Bloco SRT {block_number} inválido.")
        match = _TIMESTAMP_PATTERN.match(lines[1].strip())
        if not match:
            raise ValueError(f"Timestamp SRT inválido no bloco {block_number}.")
        start = _parse_timestamp(f"{match.group(1)}:{match.group(2)}:{match.group(3)},{match.group(4)}")
        end = _parse_timestamp(f"{match.group(5)}:{match.group(6)}:{match.group(7)},{match.group(8)}")
        if not isfinite(start) or not isfinite(end) or start < 0 or end <= start:
            raise ValueError(f"Intervalo SRT inválido no bloco {block_number}.")
        if start < previous_start:
            raise ValueError("Os timestamps SRT devem estar em ordem.")
        previous_start = start
        cues.append(SubtitleCue("\n".join(lines[2:]), start, end))
    return cues


def _format_timestamp(value: float) -> str:
    total_milliseconds = max(0, round(value * 1000))
    hours, remainder = divmod(total_milliseconds, 3_600_000)
    minutes, remainder = divmod(remainder, 60_000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{hours:02d}:{minutes:02d}:{seconds:02d},{milliseconds:03d}"


def normalize_srt_content(srt_content: str) -> str:
    cues = normalize_subtitle_cues(parse_srt(srt_content))
    blocks = [
        "\n".join([str(index), f"{_format_timestamp(cue.start)} --> {_format_timestamp(cue.end)}", cue.text])
        for index, cue in enumerate(cues, start=1)
    ]
    return "\n\n".join(blocks) + ("\n" if blocks else "")


def rebase_srt(srt_content: str, window: TimeWindow) -> str:
    blocks = re.split(r"\r?\n\s*\r?\n", srt_content.strip())
    rebased_blocks: list[str] = []

    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 3:
            continue
        match = _TIMESTAMP_PATTERN.match(lines[1].strip())
        if not match:
            continue

        start = _parse_timestamp(f"{match.group(1)}:{match.group(2)}:{match.group(3)},{match.group(4)}")
        end = _parse_timestamp(f"{match.group(5)}:{match.group(6)}:{match.group(7)},{match.group(8)}")
        if end <= window.start or start >= window.end:
            continue

        clipped_start = max(start, window.start) - window.start
        clipped_end = min(end, window.end) - window.start
        timestamp_line = f"{_format_timestamp(clipped_start)} --> {_format_timestamp(clipped_end)}{match.group(9)}"
        rebased_blocks.append("\n".join([lines[0], timestamp_line, *lines[2:]]))

    return "\n\n".join(rebased_blocks) + ("\n" if rebased_blocks else "")


def rebase_srt_to_intervals(
    srt_content: str,
    intervals: Sequence[SpeechInterval],
) -> str:
    cues = parse_srt(srt_content)
    normalized_intervals = sorted((float(start), float(end)) for start, end in intervals)
    output_blocks: list[str] = []
    for cue in cues:
        for interval_start, interval_end in normalized_intervals:
            overlap_start = max(cue.start, interval_start)
            overlap_end = min(cue.end, interval_end)
            if overlap_end <= overlap_start:
                continue
            elapsed_before = 0.0
            previous_end = 0.0
            for previous_start, previous_interval_end in normalized_intervals:
                if previous_start > overlap_start:
                    break
                elapsed_before += max(0.0, previous_start - previous_end)
                previous_end = previous_interval_end
            rebased_start = overlap_start - elapsed_before
            rebased_end = overlap_end - elapsed_before
            output_blocks.append(
                "\n".join(
                    [
                        str(len(output_blocks) + 1),
                        f"{_format_timestamp(rebased_start)} --> {_format_timestamp(rebased_end)}",
                        cue.text,
                    ]
                )
            )
    return normalize_srt_content("\n\n".join(output_blocks) + ("\n" if output_blocks else ""))


def extract_srt_intervals(srt_content: str) -> list[SpeechInterval]:
    intervals: list[SpeechInterval] = []
    blocks = re.split(r"\r?\n\s*\r?\n", srt_content.strip())
    for block in blocks:
        lines = block.splitlines()
        if len(lines) < 2:
            continue
        match = _TIMESTAMP_PATTERN.match(lines[1].strip())
        if match:
            start = _parse_timestamp(f"{match.group(1)}:{match.group(2)}:{match.group(3)},{match.group(4)}")
            end = _parse_timestamp(f"{match.group(5)}:{match.group(6)}:{match.group(7)},{match.group(8)}")
            intervals.append((start, end))
    return intervals


def split_video_with_ffmpeg(
    video_path: str,
    windows: Sequence[TimeWindow],
    output_directory: str,
) -> list[str]:
    output_paths: list[str] = []
    for index, window in enumerate(windows, start=1):
        output_path = f"{output_directory}/parte-{index:02d}.mp4"
        command = [
            "ffmpeg",
            "-y",
            "-ss",
            str(window.start),
            "-i",
            video_path,
            "-t",
            str(window.duration),
            "-c:v",
            "libx264",
            "-c:a",
            "aac",
            output_path,
        ]
        subprocess.run(command, check=True)
        output_paths.append(output_path)
    return output_paths
