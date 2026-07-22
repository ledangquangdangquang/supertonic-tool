"""Unit tests for video_pipeline subtitle and translate modules."""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

sys.path.insert(0, str(Path(__file__).parent))

from video_pipeline.subtitle import (
    SubtitleBlock,
    parse_srt,
    seconds_to_srt_time,
    srt_time_to_seconds,
    wrap_subtitle_text,
    write_srt,
)
from video_pipeline.translate import (
    CerebrasTranslator,
    OllamaTranslator,
    TranslationError,
    _translate_blocks,
)


# ── subtitle.py ──────────────────────────────────────────────────────

SAMPLE_SRT = """\
1
00:00:01,000 --> 00:00:04,000
Hello world

2
00:00:05,500 --> 00:00:08,200
This is a test
with two lines

3
00:00:10,000 --> 00:00:12,000
Xin chào Việt Nam
"""


def test_parse_srt_basic():
    blocks = parse_srt(SAMPLE_SRT)
    assert len(blocks) == 3
    assert blocks[0].index == 1
    assert blocks[0].start == "00:00:01,000"
    assert blocks[0].end == "00:00:04,000"
    assert blocks[0].text == "Hello world"


def test_parse_srt_multiline():
    blocks = parse_srt(SAMPLE_SRT)
    assert blocks[1].text == "This is a test\nwith two lines"


def test_parse_srt_bom():
    content = "\ufeff" + SAMPLE_SRT
    blocks = parse_srt(content)
    assert len(blocks) == 3


def test_parse_srt_crlf():
    content = SAMPLE_SRT.replace("\n", "\r\n")
    blocks = parse_srt(content)
    assert len(blocks) == 3


def test_parse_srt_skips_invalid():
    bad = "1\nNOT_A_TIMECODE\nSome text\n\n2\n00:00:01,000 --> 00:00:02,000\nValid"
    blocks = parse_srt(bad)
    assert len(blocks) == 1
    assert blocks[0].text == "Valid"


def test_parse_srt_empty():
    assert parse_srt("") == []
    assert parse_srt("   ") == []


def test_write_srt_roundtrip():
    blocks = parse_srt(SAMPLE_SRT)
    output = write_srt(blocks)
    reparsed = parse_srt(output)
    assert len(reparsed) == 3
    for orig, new in zip(blocks, reparsed):
        assert orig.start == new.start
        assert orig.end == new.end
        assert orig.text == new.text


def test_write_srt_index_fallback():
    block = SubtitleBlock(index=0, start="00:00:01,000", end="00:00:02,000", text="zero index")
    output = write_srt([block])
    assert output.startswith("1\n")


def test_wrap_subtitle_text():
    long = "This is a very long subtitle line that should be wrapped properly"
    wrapped = wrap_subtitle_text(long, max_chars=20)
    assert all(len(line) <= 20 for line in wrapped.split("\n"))


def test_wrap_subtitle_text_preserves_newlines():
    text = "Short\nThis is a very long subtitle line that should be wrapped properly"
    wrapped = wrap_subtitle_text(text, max_chars=20)
    lines = wrapped.split("\n")
    assert lines[0] == "Short"
    assert all(len(l) <= 20 for l in lines[1:])


def test_seconds_to_srt_time():
    assert seconds_to_srt_time(0.0) == "00:00:00,000"
    assert seconds_to_srt_time(61.5) == "00:01:01,500"
    assert seconds_to_srt_time(3661.123) == "01:01:01,123"


def test_srt_time_to_seconds():
    assert srt_time_to_seconds("00:00:00,000") == 0.0
    assert srt_time_to_seconds("00:01:01,500") == 61.5
    assert srt_time_to_seconds("01:01:01,123") == pytest.approx(3661.123)


def test_timecode_roundtrip():
    for seconds in [0.0, 0.001, 1.5, 59.999, 60.0, 3600.0]:
        srt_time = seconds_to_srt_time(seconds)
        back = srt_time_to_seconds(srt_time)
        assert back == pytest.approx(seconds, abs=0.001)


# ── translate.py: _parse_json_array ──────────────────────────────────


def test_parse_json_array_plain():
    result = CerebrasTranslator._parse_json_array('["Hello", "World"]')
    assert result == ["Hello", "World"]


def test_parse_json_array_markdown():
    content = '```json\n["Hello", "World"]\n```'
    result = CerebrasTranslator._parse_json_array(content)
    assert result == ["Hello", "World"]


def test_parse_json_array_dict():
    content = '{"0": "Hello", "1": "World"}'
    result = CerebrasTranslator._parse_json_array(content)
    assert result == ["Hello", "World"]


def test_parse_json_array_dict_with_list():
    content = '{"translations": ["Hello", "World"]}'
    result = CerebrasTranslator._parse_json_array(content)
    assert result == ["Hello", "World"]


def test_parse_json_array_invalid():
    with pytest.raises(TranslationError):
        CerebrasTranslator._parse_json_array("not json at all")


def test_parse_json_array_non_string_list():
    with pytest.raises(TranslationError):
        CerebrasTranslator._parse_json_array("[1, 2, 3]")


# ── translate.py: _parse_lines (Ollama) ──────────────────────────────


def test_parse_lines_numbered():
    content = "1. Xin chào\n2. Thế giới\n3. Việt Nam"
    result = OllamaTranslator._parse_lines(content, expected=3)
    assert result == ["Xin chào", "Thế giới", "Việt Nam"]


def test_parse_lines_with_paren():
    content = "1) Hello\n2) World"
    result = OllamaTranslator._parse_lines(content, expected=2)
    assert result == ["Hello", "World"]


def test_parse_lines_padded():
    content = "1. Hello\n2. World"
    result = OllamaTranslator._parse_lines(content, expected=3)
    assert result == ["Hello", "World", ""]


def test_parse_lines_trimmed():
    content = "1. Hello\n2. World\n3. Extra"
    result = OllamaTranslator._parse_lines(content, expected=2)
    assert result == ["Hello", "World"]


def test_parse_lines_no_numbers():
    content = "Hello\nWorld"
    result = OllamaTranslator._parse_lines(content, expected=2)
    assert result == ["Hello", "World"]


# ── translate.py: _translate_blocks fallback ──────────────────────────


def test_translate_blocks_batch_fallback():
    class MockTranslator:
        call_count = 0

        def _translate_texts(self, texts, source_lang, target_lang):
            self.call_count += 1
            if len(texts) > 1 and self.call_count == 1:
                return []  # force batch failure
            return [f"translated-{t}" for t in texts]

    translator = MockTranslator()
    blocks = [
        SubtitleBlock(index=i, start=f"00:00:0{i},000", end=f"00:00:0{i},500", text=f"line{i}")
        for i in range(1, 4)
    ]
    result = _translate_blocks(translator, blocks, "en", "vi", batch_size=3)
    assert len(result) == 3
    assert all("translated-" in b.text for b in result)
    # batch failed -> 3 individual calls
    assert translator.call_count == 4  # 1 batch + 3 individual
