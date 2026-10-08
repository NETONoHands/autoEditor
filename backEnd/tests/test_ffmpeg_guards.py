import subprocess

import pytest

import formatter
import silence_detector


def _completed(stdout: str = "", stderr: str = "", returncode: int = 0):
    return subprocess.CompletedProcess([], returncode, stdout=stdout, stderr=stderr)


def test_detect_silences_returns_empty_without_audio_stream(monkeypatch, caplog):
    calls = []

    def fake_run(command, **kwargs):
        calls.append(command[0])
        return _completed(stdout='{"streams": []}')

    monkeypatch.setattr(silence_detector.subprocess, "run", fake_run)

    with caplog.at_level("WARNING"):
        assert silence_detector.detect_silences("video.mp4") == []

    assert calls == ["ffprobe"]
    assert "sem faixa de áudio" in caplog.text


def test_detect_silences_runs_ffmpeg_when_audio_exists(monkeypatch):
    def fake_run(command, **kwargs):
        if command[0] == "ffprobe":
            return _completed(stdout='{"streams": [{"index": 1}]}')
        return _completed(stderr="silence_start: 1.0\nsilence_end: 2.5 | silence_duration: 1.5")

    monkeypatch.setattr(silence_detector.subprocess, "run", fake_run)

    assert silence_detector.detect_silences("video.mp4") == [{"start": 1.0, "end": 2.5}]


@pytest.mark.parametrize(
    "args",
    [
        (None, None, None, None),
        (0, 0, None, 100),
        (0, 0, 0, 100),
        (0, 0, "abc", 100),
        (0, 0, float("nan"), 100),
    ],
)
def test_vertical_crop_filter_falls_back_on_invalid_manual_crop(args):
    assert not formatter.has_manual_crop(*args)
    assert formatter.build_vertical_crop_filter(*args).startswith("crop=ih*9/16")


def test_vertical_crop_filter_clamps_negative_origin():
    assert formatter.build_vertical_crop_filter(-5, -2, 300.0, "400").startswith("crop=300:400:0:0,")


def test_split_screen_filter_rejects_invalid_crops():
    with pytest.raises(ValueError):
        formatter.build_split_screen_filter(0, 0, None, 10, 0, 0, 10, 10, "s.ass")
