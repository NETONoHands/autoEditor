import json

import pytest

from json_to_ass import convert_json_to_ass


def _convert(tmp_path, **kwargs):
    src = tmp_path / "c.json"
    src.write_text(json.dumps([{"word": "ola", "start": 0.0, "end": 0.5}]), encoding="utf-8")
    out = tmp_path / "c.ass"
    convert_json_to_ass(str(src), str(out), **kwargs)
    return out.read_text(encoding="utf-8")


def test_defaults_keep_previous_style(tmp_path):
    text = _convert(tmp_path)
    assert "Style: Default,Arial,90," in text
    assert "\\an2" in text


def test_custom_style(tmp_path):
    text = _convert(
        tmp_path, font="Roboto", color_preset="yellow_shadow", position_y="top", scale=1.5
    )
    assert "Style: Default,Roboto,135," in text
    assert "\\an8" in text


def test_invalid_values(tmp_path):
    with pytest.raises(ValueError):
        _convert(tmp_path, font="Comic")
    with pytest.raises(ValueError):
        _convert(tmp_path, scale=5)
