from pathlib import Path

import formatter


def test_vertical_filter_uses_central_crop_and_reserved_title_subtitle_areas() -> None:
    result = formatter.build_vertical_composite_filter("C:/legenda.srt", "Minha edição")

    assert "crop=ih*9/16:ih:(iw-ow)/2:0" in result
    assert "scale=1080:1920" in result
    assert "drawtext" in result
    assert "fontfile=" in result
    assert "fontcolor=yellow" in result
    assert "x=(w-text_w)/2:y=80" in result
    assert "enable='between(t,0,5)'" in result
    assert "alpha='if(lt(t,4),1,5-t)'" in result
    assert "subtitles='C\\:/legenda.srt':force_style=" in result
    assert "MarginV=450" in result
    assert result.index("drawtext") < result.index("subtitles=")


def test_vertical_filter_uses_bundled_title_and_subtitle_fonts() -> None:
    result = formatter.build_vertical_composite_filter("C:/legenda.srt", "Minha edição")

    assert "Anton-Regular.ttf" in result
    assert "fontsdir=" in result


def test_resolve_title_fontfile_prefers_env_override(monkeypatch, tmp_path: Path) -> None:
    custom_font = tmp_path / "custom.ttf"
    custom_font.write_bytes(b"fake")
    monkeypatch.setenv("TAPA_NA_LATA_TITLE_FONT", str(custom_font))

    assert formatter.resolve_title_fontfile() == str(custom_font)


def test_resolve_subtitle_font_falls_back_through_bundled_fonts(monkeypatch) -> None:
    monkeypatch.setattr(formatter, "FONTS_DIR", "nonexistent-fonts-dir")

    family, fonts_dir = formatter.resolve_subtitle_font()

    assert family == "Arial"
    assert fonts_dir is None


def test_vertical_filter_escapes_title_text() -> None:
    result = formatter.build_vertical_composite_filter("C:/legenda.srt", "Título: 100%")

    assert "T\u00edtulo\\: 100%%" in result


def test_vertical_filter_can_omit_visual_title() -> None:
    result = formatter.build_vertical_composite_filter("C:/legenda.srt", "")

    assert "drawtext" not in result


def test_vertical_crop_can_use_face_position() -> None:
    result = formatter.build_vertical_composite_filter("C:/legenda.srt", "Título", face_crop_x=321)

    assert "crop=min(ih*9/16\\,iw):ih:max(0\\,min(iw-ow\\,321)):0" in result
    assert "scale=1080:1920:force_original_aspect_ratio=decrease" in result
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2" in result


def test_manual_crop_skips_face_tracker(monkeypatch, tmp_path) -> None:
    def fail(*args, **kwargs):
        raise AssertionError("face_tracker must not be called")

    monkeypatch.setattr(formatter, "detect_face_crop_x", fail)
    result = formatter.build_vertical_composite_filter(
        "C:/legenda.srt", "", crop_x=10, crop_y=20, crop_w=300, crop_h=400
    )

    assert (
        "crop=300:400:10:20,"
        "scale=1080:1920:force_original_aspect_ratio=decrease,"
        "pad=1080:1920:(ow-iw)/2:(oh-ih)/2"
    ) in result
    assert formatter.has_manual_crop(10, 20, 300, 400)
    assert not formatter.has_manual_crop(10, None, 300, 400)


def test_manual_crop_is_applied_absolutely() -> None:
    first = formatter.build_vertical_crop_filter(100, 20, 300, 400)
    second = formatter.build_vertical_crop_filter(100, 300, 300, 100)

    assert first.startswith("crop=300:400:100:20,")
    assert second.startswith("crop=300:100:100:300,")


def test_manual_crop_clamps_negative_origin() -> None:
    result = formatter.build_vertical_crop_filter(-5, -7, 300, 400)

    assert result.startswith("crop=300:400:0:0,")


def test_vertical_filter_adds_safe_area_margin() -> None:
    result = formatter.build_vertical_composite_filter(
        "C:/legenda.srt",
        "Título",
        safe_area=0.1,
    )

    assert "scale=1080:1920" in result
    assert "scale=864:1536" not in result
    assert "pad=1080:1920:(ow-iw)/2:(oh-ih)/2" in result
    assert "y=192" in result
    assert "subtitles='C\\:/legenda.srt':force_style=" in result


def test_vertical_filter_rejects_invalid_safe_area() -> None:
    try:
        formatter.build_vertical_composite_filter("C:/legenda.srt", safe_area=0.25)
    except ValueError as exc:
        assert "safe_area" in str(exc)
    else:
        raise AssertionError("Expected an invalid safe area to be rejected")


def test_face_tracking_is_optional_and_falls_back_to_center(monkeypatch) -> None:
    video_path = str(Path("video.mp4"))
    called = []

    def fake_detector(path: str) -> int:
        called.append(path)
        return 321

    monkeypatch.setattr(formatter, "detect_face_crop_x", fake_detector)
    assert formatter.resolve_vertical_crop_x(video_path, False) is None
    assert called == []
    assert formatter.resolve_vertical_crop_x(video_path, True) == 321
    assert called == [video_path]


def test_face_tracking_failure_uses_center_crop(monkeypatch) -> None:
    def failing_detector(path: str) -> int:
        raise RuntimeError("sem rosto")

    monkeypatch.setattr(formatter, "detect_face_crop_x", failing_detector)

    assert formatter.resolve_vertical_crop_x("video.mp4", True) is None


def test_build_srt_from_captions_preserves_millisecond_precision() -> None:
    captions = [
        {"word": "Ola", "start": 0.0, "end": 0.5},
        {"word": "mundo", "start": 0.523, "end": 1.234},
    ]

    result = formatter.build_srt_from_captions(captions)

    assert "00:00:00,000 --> 00:00:01,234" in result
    assert "Ola mundo" in result


def test_formatter_renders_vertical_outputs_from_generated_ass(monkeypatch, tmp_path: Path) -> None:
    video_path = tmp_path / "treated.mp4"
    video_path.write_bytes(b"video")
    srt_path = tmp_path / "captions.srt"
    srt_path.write_text("1\n00:00:00,000 --> 00:00:00,500\nOla", encoding="utf-8")
    commands: list[list[str]] = []
    monkeypatch.setattr(formatter, "_run_ffmpeg_with_fallback", commands.append)

    result = formatter.format_video_by_classification(
        str(video_path),
        str(srt_path),
        "short",
        output_directory=str(tmp_path / "output"),
        output_stem="edicao",
        face_tracking=False,
        safe_area=0.1,
    )

    final_srt_path = Path(result["captions_ass"])
    assert final_srt_path.is_file()
    assert final_srt_path.suffix == ".srt"
    srt_content = final_srt_path.read_text(encoding="utf-8")
    assert "Ola" in srt_content
    assert len(commands) == 2
    video_filter = commands[0][commands[0].index("-vf") + 1]
    assert "subtitles='" in video_filter
    assert "force_style=" in video_filter
    assert formatter.escape_path_for_ffmpeg_filter(str(final_srt_path)) in video_filter
