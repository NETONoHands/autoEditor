from pathlib import Path

import pytest

import pipeline_contracts


def test_validate_captions_json_accepts_well_formed_word_list(tmp_path: Path) -> None:
    captions_path = tmp_path / "captions.json"
    captions_path.write_text(
        '[{"word": "Ola", "start": 0.0, "end": 0.523}, {"word": "mundo", "start": 0.6, "end": 1.234}]',
        encoding="utf-8",
    )

    assert pipeline_contracts.validate_captions_json(str(captions_path)) == str(captions_path.resolve())


def test_validate_captions_json_rejects_non_json_extension(tmp_path: Path) -> None:
    captions_path = tmp_path / "captions.srt"
    captions_path.write_text("1\n00:00:00,000 --> 00:00:01,000\nOla\n", encoding="utf-8")

    with pytest.raises(pipeline_contracts.InputValidationError, match="JSON"):
        pipeline_contracts.validate_captions_json(str(captions_path))


@pytest.mark.parametrize(
    "payload",
    [
        "[]",
        '[{"word": "", "start": 0.0, "end": 1.0}]',
        '[{"word": "Ola", "start": -1.0, "end": 1.0}]',
        '[{"word": "Ola", "start": 1.0, "end": 1.0}]',
        '[{"word": "Ola", "start": 2.0, "end": 3.0}, {"word": "mundo", "start": 1.0, "end": 1.5}]',
    ],
)
def test_validate_captions_json_rejects_invalid_payloads(tmp_path: Path, payload: str) -> None:
    captions_path = tmp_path / "captions.json"
    captions_path.write_text(payload, encoding="utf-8")

    with pytest.raises(pipeline_contracts.InputValidationError):
        pipeline_contracts.validate_captions_json(str(captions_path))


def test_load_captions_json_preserves_decimal_precision(tmp_path: Path) -> None:
    captions_path = tmp_path / "captions.json"
    captions_path.write_text('[{"word": "Ola", "start": 0.123, "end": 0.456}]', encoding="utf-8")

    captions = pipeline_contracts.load_captions_json(str(captions_path))

    assert captions == [{"word": "Ola", "start": 0.123, "end": 0.456}]


def test_normalize_captions_payload_maps_legendas_texto_to_word() -> None:
    payload = {"legendas": [{"texto": "Ola", "start": 0.123, "end": 0.456}]}

    assert pipeline_contracts.normalize_captions_payload(payload) == [
        {"word": "Ola", "start": 0.123, "end": 0.456}
    ]


def test_normalize_captions_payload_rejects_unknown_shape() -> None:
    with pytest.raises(pipeline_contracts.InputValidationError):
        pipeline_contracts.normalize_captions_payload({"words": []})


def test_validate_and_load_captions_json_accept_legendas_wrapper(tmp_path: Path) -> None:
    captions_path = tmp_path / "captions.json"
    captions_path.write_text(
        '{"legendas": [{"texto": "Ola", "start": 0.0, "end": 0.5}, {"texto": "mundo", "start": 0.6, "end": 1.234}]}',
        encoding="utf-8",
    )

    assert pipeline_contracts.validate_captions_json(str(captions_path)) == str(captions_path.resolve())
    assert pipeline_contracts.load_captions_json(str(captions_path)) == [
        {"word": "Ola", "start": 0.0, "end": 0.5},
        {"word": "mundo", "start": 0.6, "end": 1.234},
    ]


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
