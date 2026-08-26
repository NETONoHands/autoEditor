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


def test_vertical_filter_escapes_title_text() -> None:
    result = formatter.build_vertical_composite_filter("C:/legenda.srt", "Título: 100%")

    assert "T\u00edtulo\\: 100%%" in result


def test_vertical_filter_can_omit_visual_title() -> None:
    result = formatter.build_vertical_composite_filter("C:/legenda.srt", "")

    assert "drawtext" not in result


def test_vertical_crop_can_use_face_position() -> None:
    result = formatter.build_vertical_composite_filter("C:/legenda.srt", "Título", crop_x=321)

    assert "crop=ih*9/16:ih:321:0" in result


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
