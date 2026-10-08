import argparse
import json
import logging
import os
from typing import Dict, List

LOGGER = logging.getLogger(__name__)

MAX_WORDS_PER_LINE = 4
MAX_PAUSE_SECONDS = 0.5
PLAY_RES_X = 1080
PLAY_RES_Y = 1920

# ASS colors are BGR: white and yellow.
WHITE = "&HFFFFFF&"
YELLOW = "&H00FFFF&"

BASE_FONT_SIZE = 90
DEFAULT_FONT = "Arial"
SUBTITLE_FONTS = ("Arial", "Roboto", "Anton", "Sansation", "DejaVu Sans")
MIN_SUBTITLE_SCALE = 0.5
MAX_SUBTITLE_SCALE = 2.0

# Cores ASS em BGR. "highlight" é a palavra ativa.
COLOR_PRESETS = {
    "white_black_outline": {
        "primary": WHITE, "highlight": YELLOW, "outline_color": "&H00000000&",
        "back_color": "&H80000000&", "border_style": 1, "outline": 6, "shadow": 2,
    },
    "yellow_shadow": {
        "primary": YELLOW, "highlight": WHITE, "outline_color": "&H00000000&",
        "back_color": "&H00000000&", "border_style": 1, "outline": 2, "shadow": 6,
    },
    "white_black_box": {
        "primary": WHITE, "highlight": YELLOW, "outline_color": "&H00000000&",
        "back_color": "&H00000000&", "border_style": 3, "outline": 10, "shadow": 0,
    },
    "cyan_black_outline": {
        "primary": "&HFFFF00&", "highlight": WHITE, "outline_color": "&H00000000&",
        "back_color": "&H80000000&", "border_style": 1, "outline": 6, "shadow": 2,
    },
}
DEFAULT_COLOR_PRESET = "white_black_outline"

# Alinhamento ASS: 8 = topo central, 5 = meio central, 2 = fundo central.
POSITION_ALIGNMENTS = {"top": 8, "center": 5, "bottom": 2}
DEFAULT_POSITION = "bottom"
TOP_MARGIN_V = 400

ASS_HEADER = """[Script Info]
ScriptType: v4.00+
PlayResX: {play_res_x}
PlayResY: {play_res_y}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,{font},{font_size},{primary},{primary},{outline_color},{back_color},-1,0,0,0,100,100,0,0,{border_style},{outline},{shadow},{alignment},{margin_h},{margin_h},{margin_v},1

[Events]
Format: Layer, Start, End, Name, MarginL, MarginR, MarginV, Effect, Text
"""


def load_words(json_path: str) -> List[Dict]:
    with open(json_path, "r", encoding="utf-8") as file:
        data = json.load(file)

    if not isinstance(data, list):
        raise ValueError("JSON root must be a list of word dictionaries.")

    words = []
    for index, item in enumerate(data):
        if not isinstance(item, dict) or not {"word", "start", "end"} <= item.keys():
            raise ValueError(f"Item {index} must contain 'word', 'start' and 'end'.")
        text = str(item["word"]).strip()
        if not text:
            continue
        start, end = float(item["start"]), float(item["end"])
        words.append({"word": text, "start": start, "end": max(end, start)})

    return words


def group_words(
    words: List[Dict],
    max_words: int = MAX_WORDS_PER_LINE,
    max_pause: float = MAX_PAUSE_SECONDS,
) -> List[List[Dict]]:
    groups: List[List[Dict]] = []
    current: List[Dict] = []

    for word in words:
        if current and (
            len(current) >= max_words or word["start"] - current[-1]["end"] > max_pause
        ):
            groups.append(current)
            current = []
        current.append(word)

    if current:
        groups.append(current)

    return groups


def format_ass_time(seconds: float) -> str:
    total_cs = int(round(max(seconds, 0.0) * 100))
    hours, rem = divmod(total_cs, 360000)
    minutes, rem = divmod(rem, 6000)
    secs, cs = divmod(rem, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{cs:02d}"


def escape_ass_text(text: str) -> str:
    return text.replace("\\", "\\\\").replace("{", "(").replace("}", ")")


def build_group_events(
    group: List[Dict],
    base_color: str = WHITE,
    highlight_color: str = YELLOW,
    alignment: int = 2,
) -> List[str]:
    """One dialogue per spoken word: the active word is yellow, others white.

    Each event spans from the word's start until the next word starts (or the
    word's end for the last one), so the line stays on screen without flicker.
    """
    events = []
    for active_index, active in enumerate(group):
        start = active["start"]
        if active_index + 1 < len(group):
            end = group[active_index + 1]["start"]
        else:
            end = active["end"]
        end = max(end, start + 0.02)

        parts = []
        for index, word in enumerate(group):
            color = highlight_color if index == active_index else base_color
            parts.append(f"{{\\c{color}}}{escape_ass_text(word['word'])}")
        text = " ".join(parts)

        events.append(
            f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},"
            f"Default,,0,0,0,,{{\\an{alignment}}}{text}"
        )
    return events


def convert_json_to_ass(
    json_path: str,
    ass_path: str,
    margin_h: int = 60,
    margin_v: int = 300,
    font: str = DEFAULT_FONT,
    color_preset: str = DEFAULT_COLOR_PRESET,
    position_y: str = DEFAULT_POSITION,
    scale: float = 1.0,
) -> str:
    if margin_h < 0 or margin_v < 0:
        raise ValueError("Subtitle margins must be non-negative")
    if font not in SUBTITLE_FONTS:
        raise ValueError(f"Unsupported subtitle font: {font}")
    if color_preset not in COLOR_PRESETS:
        raise ValueError(f"Unsupported subtitle color preset: {color_preset}")
    if position_y not in POSITION_ALIGNMENTS:
        raise ValueError(f"Unsupported subtitle position: {position_y}")
    if not MIN_SUBTITLE_SCALE <= scale <= MAX_SUBTITLE_SCALE:
        raise ValueError(
            f"subtitle scale must be between {MIN_SUBTITLE_SCALE} and {MAX_SUBTITLE_SCALE}"
        )

    preset = COLOR_PRESETS[color_preset]
    alignment = POSITION_ALIGNMENTS[position_y]
    style_margin_v = {"top": max(margin_v, TOP_MARGIN_V), "center": 0, "bottom": margin_v}[position_y]

    words = load_words(json_path)
    groups = group_words(words)

    lines = [
        ASS_HEADER.format(
            play_res_x=PLAY_RES_X,
            play_res_y=PLAY_RES_Y,
            font=font,
            font_size=int(round(BASE_FONT_SIZE * scale)),
            primary=preset["primary"],
            outline_color=preset["outline_color"],
            back_color=preset["back_color"],
            border_style=preset["border_style"],
            outline=preset["outline"],
            shadow=preset["shadow"],
            alignment=alignment,
            margin_h=margin_h,
            margin_v=style_margin_v,
        ).rstrip("\n")
    ]
    for group in groups:
        lines.extend(
            build_group_events(group, preset["primary"], preset["highlight"], alignment)
        )

    os.makedirs(os.path.dirname(os.path.abspath(ass_path)), exist_ok=True)
    with open(ass_path, "w", encoding="utf-8-sig") as file:
        file.write("\n".join(lines) + "\n")

    LOGGER.info("ASS written: %s (%d lines)", ass_path, len(groups))
    return ass_path


def main() -> None:
    logging.basicConfig(level=logging.INFO)
    parser = argparse.ArgumentParser(description="Convert Whisper word JSON to ASS.")
    parser.add_argument("json_path")
    parser.add_argument("ass_path", nargs="?")
    args = parser.parse_args()

    ass_path = args.ass_path or os.path.splitext(args.json_path)[0] + ".ass"
    convert_json_to_ass(args.json_path, ass_path)


if __name__ == "__main__":
    main()
