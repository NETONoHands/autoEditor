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

ASS_HEADER = f"""[Script Info]
ScriptType: v4.00+
PlayResX: {PLAY_RES_X}
PlayResY: {PLAY_RES_Y}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
Style: Default,Arial,90,{WHITE},{WHITE},&H00000000&,&H80000000&,-1,0,0,0,100,100,0,0,1,6,2,2,60,60,300,1

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


def build_group_events(group: List[Dict]) -> List[str]:
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
            color = YELLOW if index == active_index else WHITE
            parts.append(f"{{\\c{color}}}{escape_ass_text(word['word'])}")
        text = " ".join(parts)

        events.append(
            f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},"
            f"Default,,0,0,0,,{{\\an2}}{text}"
        )
    return events


def convert_json_to_ass(json_path: str, ass_path: str) -> str:
    words = load_words(json_path)
    groups = group_words(words)

    lines = [ASS_HEADER.rstrip("\n")]
    for group in groups:
        lines.extend(build_group_events(group))

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
