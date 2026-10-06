import re
from typing import Any, Dict, List

_NON_WORD_RE = re.compile(r"[^\w]+", re.UNICODE)


def _normalize(word: Any) -> str:
    return _NON_WORD_RE.sub("", str(word).lower())


def detect_disfluencies(
    captions: List[Dict[str, Any]],
    max_gap: float = 0.4,
    max_sequence_len: int = 3,
) -> List[Dict[str, float]]:
    """Detect immediate word/phrase repetitions (stutters) in timed captions.

    A repetition is a sequence of 1..``max_sequence_len`` words that is
    immediately followed by an identical sequence, where the gap between the
    ``end`` of the first occurrence and the ``start`` of the second is smaller
    than ``max_gap`` seconds. Comparison ignores case and punctuation.

    The earlier occurrence(s) are reported as removable, so the last (cleanest)
    occurrence is kept. Chains such as "eu eu eu" yield a single merged interval
    covering all but the last word.

    Args:
        captions: Items with ``word``, ``start`` and ``end`` keys, ordered by time.
        max_gap: Maximum gap, in seconds, between consecutive repetitions.
        max_sequence_len: Longest repeated word sequence to look for.

    Returns:
        A list of ``{"start": float, "end": float}`` intervals to cut, ordered by
        time, with cuts of consecutive words merged.
    """
    words = [_normalize(c.get("word", "")) for c in captions]
    total = len(captions)
    intervals: List[Dict[str, float]] = []

    last_cut_idx = -2
    i = 0
    while i < total:
        matched = 0
        for n in range(min(max_sequence_len, (total - i) // 2), 0, -1):
            first = words[i:i + n]
            second = words[i + n:i + 2 * n]
            if not all(first) or first != second:
                continue
            gap = float(captions[i + n]["start"]) - float(captions[i + n - 1]["end"])
            if gap < max_gap:
                matched = n
                break

        if matched:
            start = float(captions[i]["start"])
            end = float(captions[i + matched - 1]["end"])
            if intervals and last_cut_idx == i - 1:
                intervals[-1]["end"] = max(intervals[-1]["end"], end)
            else:
                intervals.append({"start": start, "end": end})
            last_cut_idx = i + matched - 1
            i += matched
        else:
            i += 1

    return intervals
