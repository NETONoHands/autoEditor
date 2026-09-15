import pytest

from segmentation import (
    TimeWindow,
    extract_caption_intervals,
    normalize_srt_content,
    rebase_captions_to_intervals,
    rebase_captions_to_window,
    rebase_srt_to_intervals,
)


SRT = """1
00:00:00,000 --> 00:00:01,000
Antes

2
00:00:01,500 --> 00:00:02,500
Durante
Linha 2

3
00:00:03,000 --> 00:00:04,000
Depois
"""


def test_rebase_removes_silence_and_preserves_text_order() -> None:
    result = rebase_srt_to_intervals(SRT, [(0, 1), (3, 4)])

    assert "Antes" in result
    assert "Durante" not in result
    assert "Depois" in result
    assert "00:00:01,000 --> 00:00:02,000" in result
    assert result.index("Antes") < result.index("Depois")


def test_rebase_cuts_cue_at_preserved_boundaries() -> None:
    srt = "1\n00:00:00,500 --> 00:00:03,500\nMensagem\n"

    result = rebase_srt_to_intervals(srt, [(0, 1), (3, 4)])

    assert result.count("Mensagem") == 2
    assert "00:00:00,500 --> 00:00:01,000" in result
    assert "00:00:01,000 --> 00:00:01,500" in result


def test_rebase_rejects_invalid_or_out_of_order_timestamps() -> None:
    with pytest.raises(ValueError):
        rebase_srt_to_intervals("1\n00:00:02,000 --> 00:00:01,000\nInválido\n", [(0, 3)])

    with pytest.raises(ValueError):
        rebase_srt_to_intervals(
            "1\n00:00:02,000 --> 00:00:03,000\nUm\n\n2\n00:00:01,000 --> 00:00:02,000\nDois\n",
            [(0, 3)],
        )


def test_normalize_srt_limits_lines_and_preserves_all_words() -> None:
    content = "1\n00:00:00,000 --> 00:00:08,000\nUm dois tres quatro cinco seis sete oito nove\n"

    result = normalize_srt_content(content)
    blocks = result.strip().split("\n\n")

    assert len(blocks) == 2
    assert "Um dois tres quatro\ncinco seis sete oito" in blocks[0]
    assert "nove" in blocks[1]


CAPTIONS = [
    {"word": "Ola", "start": 0.0, "end": 0.5},
    {"word": "mundo", "start": 0.6, "end": 1.234},
    {"word": "tudo", "start": 4.0, "end": 4.5},
    {"word": "bem", "start": 4.6, "end": 5.0},
]


def test_extract_caption_intervals_returns_word_pairs() -> None:
    assert extract_caption_intervals(CAPTIONS) == [(0.0, 0.5), (0.6, 1.234), (4.0, 4.5), (4.6, 5.0)]


def test_rebase_captions_to_window_clips_and_shifts_words() -> None:
    window = TimeWindow(0.6, 4.5)

    result = rebase_captions_to_window(CAPTIONS, window)

    assert [item["word"] for item in result] == ["mundo", "tudo"]
    assert result[0]["start"] == pytest.approx(0.0)
    assert result[0]["end"] == pytest.approx(0.634)
    assert result[1]["start"] == pytest.approx(3.4)
    assert result[1]["end"] == pytest.approx(3.9)


def test_rebase_captions_to_intervals_preserves_decimals() -> None:
    result = rebase_captions_to_intervals(CAPTIONS, [(0.0, 1.234), (4.0, 5.0)])

    assert [item["word"] for item in result] == ["Ola", "mundo", "tudo", "bem"]
    assert [item["start"] for item in result] == pytest.approx([0.0, 0.6, 1.234, 1.834])
    assert [item["end"] for item in result] == pytest.approx([0.5, 1.234, 1.734, 2.234])
