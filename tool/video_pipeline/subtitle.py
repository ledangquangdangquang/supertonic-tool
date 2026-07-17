from __future__ import annotations

from dataclasses import dataclass
import re
import textwrap


TIMECODE_RE = re.compile(
    r"(?P<start>\d{2}:\d{2}:\d{2},\d{3})\s*-->\s*"
    r"(?P<end>\d{2}:\d{2}:\d{2},\d{3})(?:\s+.*)?"
)


@dataclass(slots=True)
class SubtitleBlock:
    index: int
    start: str
    end: str
    text: str


def parse_srt(content: str) -> list[SubtitleBlock]:
    """Parse SRT content while preserving text and timing."""
    content = content.replace("\ufeff", "").replace("\r\n", "\n").replace("\r", "\n")
    blocks: list[SubtitleBlock] = []

    for raw_block in re.split(r"\n\s*\n", content.strip()):
        lines = [line.strip() for line in raw_block.split("\n") if line.strip()]
        if not lines:
            continue

        index = len(blocks) + 1
        if re.fullmatch(r"\d+", lines[0]):
            index = int(lines.pop(0))
        if not lines:
            continue

        time_match = TIMECODE_RE.match(lines[0])
        if not time_match:
            continue

        text = "\n".join(lines[1:]).strip()
        if not text:
            continue

        blocks.append(
            SubtitleBlock(
                index=index,
                start=time_match.group("start"),
                end=time_match.group("end"),
                text=text,
            )
        )

    return blocks


def write_srt(blocks: list[SubtitleBlock], wrap_chars: int = 42) -> str:
    parts = []
    for fallback_index, block in enumerate(blocks, 1):
        index = block.index or fallback_index
        text = wrap_subtitle_text(block.text.strip(), max_chars=wrap_chars)
        parts.append(f"{index}\n{block.start} --> {block.end}\n{text}")
    return "\n\n".join(parts) + "\n"


def wrap_subtitle_text(text: str, max_chars: int = 42) -> str:
    lines = text.split("\n")
    wrapped: list[str] = []
    for line in lines:
        if len(line) <= max_chars:
            wrapped.append(line)
        else:
            wrapped.extend(textwrap.fill(line, width=max_chars, break_long_words=False).split("\n"))
    return "\n".join(wrapped)


def seconds_to_srt_time(seconds: float) -> str:
    milliseconds = max(0, int(round(seconds * 1000)))
    hours, rem = divmod(milliseconds, 3_600_000)
    minutes, rem = divmod(rem, 60_000)
    secs, millis = divmod(rem, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def srt_time_to_seconds(value: str) -> float:
    hours, minutes, rest = value.split(":")
    seconds, millis = rest.split(",")
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis) / 1000
    )
