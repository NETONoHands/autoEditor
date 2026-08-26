import pytest

from segmentation import rebase_srt_to_intervals


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
