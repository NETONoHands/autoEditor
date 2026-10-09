from pathlib import Path

import pytest

import pipeline_contracts


def test_validate_captions_srt_accepts_well_formed_file(tmp_path: Path) -> None:
    captions_path = tmp_path / "captions.srt"
    captions_path.write_text(
        "1\n00:00:00,000 --> 00:00:00,523\nOla\n\n2\n00:00:00,600 --> 00:00:01,234\nmundo",
        encoding="utf-8",
    )

    assert pipeline_contracts.validate_captions_srt(str(captions_path)) == str(captions_path.resolve())


def test_validate_captions_srt_rejects_non_srt_extension(tmp_path: Path) -> None:
    captions_path = tmp_path / "captions.txt"
    captions_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nOla\n", encoding="utf-8")

    with pytest.raises(pipeline_contracts.InputValidationError, match="SRT"):
        pipeline_contracts.validate_captions_srt(str(captions_path))


@pytest.mark.parametrize(
    "payload",
    [
        "",
        "A\n00:00:00,000 --> 00:00:01,000\nOla",
        "1\n00:00:00,000 -> 00:00:01,000\nOla",
        "1\n00:00:00.000 --> 00:00:01.000\nOla",
    ],
)
def test_validate_captions_srt_rejects_invalid_payloads(tmp_path: Path, payload: str) -> None:
    captions_path = tmp_path / "captions.srt"
    captions_path.write_text(payload, encoding="utf-8")

    with pytest.raises(pipeline_contracts.InputValidationError):
        pipeline_contracts.validate_captions_srt(str(captions_path))


def test_validate_vertical_output_accepts_expected_metadata(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    output = tmp_path / "vertical.mp4"
    output.write_bytes(b"video")
    monkeypatch.setattr(
        pipeline_contracts,
        "_probe_video",
        lambda path: {
            "format": {"duration": "4.5"},
            "streams": [
                {"codec_type": "video", "width": 1080, "height": 1920, "codec_name": "h264"},
                {"codec_type": "audio", "codec_name": "aac"},
            ],
        },
    )

    result = pipeline_contracts.validate_vertical_output(str(output))

    assert result["resolution"] == {"width": 1080, "height": 1920}
    assert result["video_codec"] == "h264"
    assert result["audio_codec"] == "aac"
    assert result["duration_seconds"] == 4.5


@pytest.mark.parametrize(
    ("video_stream", "audio_stream", "message"),
    [
        ({"codec_type": "video", "width": 1920, "height": 1080, "codec_name": "h264"}, {"codec_type": "audio", "codec_name": "aac"}, "1080x1920"),
        ({"codec_type": "video", "width": 1080, "height": 1920, "codec_name": "hevc"}, {"codec_type": "audio", "codec_name": "aac"}, "H.264"),
        ({"codec_type": "video", "width": 1080, "height": 1920, "codec_name": "h264"}, None, "áudio"),
    ],
)
def test_validate_vertical_output_rejects_incompatible_streams(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    video_stream: dict[str, object],
    audio_stream: dict[str, object] | None,
    message: str,
) -> None:
    output = tmp_path / "vertical.mp4"
    output.write_bytes(b"video")
    streams = [video_stream] + ([audio_stream] if audio_stream else [])
    monkeypatch.setattr(
        pipeline_contracts,
        "_probe_video",
        lambda path: {"format": {"duration": "4.5"}, "streams": streams},
    )

    with pytest.raises(RuntimeError, match=message):
        pipeline_contracts.validate_vertical_output(str(output))
