from pathlib import Path

import pytest

from cutter import extract_speech_intervals, serialize_intervals
from pipeline_contracts import validate_output_video


def test_speech_gap_at_threshold_is_preserved() -> None:
    data = {"segments": [{"start": 0, "end": 1}, {"start": 1.3, "end": 2}]}

    assert extract_speech_intervals(data) == [(0.0, 2.0)]


def test_speech_gap_above_threshold_is_removed() -> None:
    data = {"segments": [{"start": 0, "end": 1}, {"start": 1.31, "end": 2}]}

    assert extract_speech_intervals(data) == [(0.0, 1.0), (1.31, 2.0)]


def test_word_fallback_uses_three_tenths_threshold() -> None:
    data = {"words": [{"start": 0, "end": 1}, {"start": 1.3, "end": 2}]}

    assert extract_speech_intervals(data) == [(0.0, 2.0)]


def test_intervals_are_serialized_with_duration() -> None:
    assert serialize_intervals([(1, 2.5)]) == [
        {"start": 1.0, "end": 2.5, "duration": 1.5}
    ]


def test_final_video_validation_rejects_missing_file(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="não existe ou está vazio"):
        validate_output_video(str(tmp_path / "missing.mp4"))
