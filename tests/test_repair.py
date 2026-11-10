# -*- coding: utf-8 -*-
"""Repair-парсер JSON (§10.4, шаги 1–3): прямой loads → ```json → срез →
косметика хвостовых запятых. LLM-повтор (шаг 4) вне этого модуля."""
import pytest

from llm_client import parse_lenient_json, JsonParseError


def test_direct_json():
    assert parse_lenient_json('{"a": 1}') == {"a": 1}


def test_fenced_json_block():
    text = 'Вот ваш JSON:\n```json\n{"a": [1, 2]}\n```\nЧто-то ещё.'
    assert parse_lenient_json(text) == {"a": [1, 2]}


def test_leading_text_before_object():
    assert parse_lenient_json('локально: {"domain": "legal"}') == {"domain": "legal"}


def test_trailing_comma_fixed():
    assert parse_lenient_json('{"a": [1, 2,], }') == {"a": [1, 2]}


def test_nested_json_with_text_prefix():
    text = '{"subanswers": [], "answer_draft": "{"}'
    assert parse_lenient_json(text)["answer_draft"] == "{"
    assert parse_lenient_json(text)["subanswers"] == []


def test_empty_raises():
    with pytest.raises(JsonParseError):
        parse_lenient_json("   ")


def test_garbage_raises():
    with pytest.raises(JsonParseError):
        parse_lenient_json("всё сломалось, JSON не будет")

def test_list_root():
    assert parse_lenient_json('[{"claim": "x"}]') == [{"claim": "x"}]
