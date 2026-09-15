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
    assert "MarginV=120" in result
    assert result.index("drawtext") < result.index("subtitles")


def test_vertical_filter_uses_bundled_title_and_subtitle_fonts() -> None:
    result = formatter.build_vertical_composite_filter("C:/legenda.srt", "Minha edição")

    assert "Anton-Regular.ttf" in result
    assert "fontsdir=" in result
    assert "FontName=Sansation" in result


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
    result = formatter.build_vertical_composite_filter("C:/legenda.srt", "Título", crop_x=321)

    assert "crop=ih*9/16:ih:321:0" in result


def test_vertical_filter_adds_safe_area_margin() -> None:
    result = formatter.build_vertical_composite_filter(
        "C:/legenda.srt",
        "Título",
        safe_area=0.1,
    )

    assert "scale=1080:1920" in result
    assert "scale=864:1536" not in result
    assert "pad=1080:1920" not in result
    assert "y=192" in result
    assert "MarginL=108,MarginR=108,MarginV=192" in result


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

