from pathlib import Path
import subprocess

import pytest

import cutter
from cutter import extract_speech_intervals, serialize_intervals
from pipeline_contracts import validate_output_video


def test_speech_gap_at_threshold_is_preserved() -> None:
    data = {"segments": [{"start": 0, "end": 1}, {"start": 1.1, "end": 2}]}

    assert extract_speech_intervals(data) == [(0.0, 2.0)]


def test_speech_gap_above_threshold_is_removed() -> None:
    data = {"segments": [{"start": 0, "end": 1}, {"start": 1.31, "end": 2}]}

    assert extract_speech_intervals(data) == [(0.0, 1.0), (1.31, 2.0)]


def test_word_fallback_uses_three_tenths_threshold() -> None:
    data = {"words": [{"start": 0, "end": 1}, {"start": 1.1, "end": 2}]}

    assert extract_speech_intervals(data) == [(0.0, 2.0)]


def test_intervals_are_serialized_with_duration() -> None:
    assert serialize_intervals([(1, 2.5)]) == [
        {"start": 1.0, "end": 2.5, "duration": 1.5}
    ]


def test_final_video_validation_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="não existe ou está vazio"):
        validate_output_video(str(tmp_path / "missing.mp4"))


def test_cut_recodes_segments_and_concatenates_them(tmp_path: Path, monkeypatch) -> None:
    video_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    work_dir = tmp_path / "work"
    video_path.write_bytes(b"input")
    commands: list[list[str]] = []

    def fake_run_ffmpeg(command: list[str]) -> None:
        commands.append(command)
        Path(command[-1]).write_bytes(b"output")

    monkeypatch.setattr(cutter, "run_segment_ffmpeg_with_fallback", fake_run_ffmpeg)
    monkeypatch.setattr(cutter, "run_ffmpeg", fake_run_ffmpeg)

    result = cutter.cut_video_with_ffmpeg(
        str(video_path),
        [(0.0, 1.0)],
        output_path=str(output_path),
        work_dir=str(work_dir),
    )

    assert result == str(output_path)
    assert commands[0][commands[0].index("-t") + 1] == "1.0"
    assert commands[0][commands[0].index("-c:v") + 1] == "h264_nvenc"
    assert commands[0][commands[0].index("-c:a") + 1] == "aac"
    assert commands[0][commands[0].index("-pix_fmt") + 1] == "yuv420p"
    assert commands[1][commands[1].index("-c") + 1] == "copy"


def test_segment_cut_retries_with_libx264_when_nvenc_fails(monkeypatch) -> None:
    commands: list[list[str]] = []

    def fake_run_ffmpeg(command: list[str]) -> None:
        commands.append(command)
        if command[command.index("-c:v") + 1] == "h264_nvenc":
            raise subprocess.CalledProcessError(1, command, stderr="h264_nvenc unavailable")

    monkeypatch.setattr(cutter, "run_ffmpeg", fake_run_ffmpeg)
    command = ["ffmpeg", "-c:v", "h264_nvenc", "-pix_fmt", "yuv420p", "segment.mp4"]

    cutter.run_segment_ffmpeg_with_fallback(command)

    assert len(commands) == 2
    fallback = commands[1]
    assert fallback[fallback.index("-c:v") + 1] == "libx264"
    assert fallback[fallback.index("-crf") + 1] == "23"
    assert fallback[fallback.index("-preset") + 1] == "fast"


def test_compute_keep_intervals_returns_complement_of_removed_ranges() -> None:
    result = cutter.compute_keep_intervals([(1.0, 3.0), (5.0, 6.0)], total_duration=10.0)

    assert result == [(0.0, 1.0), (3.0, 5.0), (6.0, 10.0)]


def test_compute_keep_intervals_merges_overlapping_removals() -> None:
    result = cutter.compute_keep_intervals([(2.0, 4.0), (3.5, 5.0)], total_duration=10.0)

    assert result == [(0.0, 2.0), (5.0, 10.0)]


def test_compute_keep_intervals_rejects_invalid_range() -> None:
    with pytest.raises(ValueError):
        cutter.compute_keep_intervals([(5.0, 3.0)], total_duration=10.0)

    with pytest.raises(ValueError):
        cutter.compute_keep_intervals([(0.0, 11.0)], total_duration=10.0)


def test_compute_keep_intervals_rejects_removing_everything() -> None:
    with pytest.raises(ValueError):
        cutter.compute_keep_intervals([(0.0, 10.0)], total_duration=10.0)


def test_cut_by_removed_intervals_cuts_the_complement(tmp_path: Path, monkeypatch) -> None:
    video_path = tmp_path / "input.mp4"
    output_path = tmp_path / "output.mp4"
    video_path.write_bytes(b"input")
    captured_intervals: list = []

    def fake_cut_video_with_ffmpeg(video, intervals, output_path=None, work_dir=None):
        captured_intervals.append(intervals)
        Path(output_path).write_bytes(b"output")
        return output_path

    monkeypatch.setattr(cutter, "cut_video_with_ffmpeg", fake_cut_video_with_ffmpeg)

    result = cutter.cut_by_removed_intervals(
        str(video_path),
        [(2.0, 4.0)],
        total_duration=10.0,
        output_path=str(output_path),
    )

    assert captured_intervals == [[(0.0, 2.0), (4.0, 10.0)]]
    assert result["keep_intervals"] == [(0.0, 2.0), (4.0, 10.0)]
    assert result["output_path"] == str(output_path)
