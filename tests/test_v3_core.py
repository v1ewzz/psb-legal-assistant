# -*- coding: utf-8 -*-
"""Офлайн-тесты ядра v3 (без сети и ключей): диалог, verify, legalctx,
поиск по готовому индексу, контракт CSV. Запуск: pytest tests -q"""
from __future__ import annotations

import csv
import sys
from datetime import date
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

import config  # noqa: E402
import dialog as D  # noqa: E402
import legalctx  # noqa: E402
import pipeline  # noqa: E402
import retrieval  # noqa: E402
import solution  # noqa: E402
import verify as V  # noqa: E402


@pytest.fixture(scope="module")
def cfg():
    return config.from_env({"DATA_DIR": str(ROOT / "data")})


@pytest.fixture(scope="module")
def retriever(cfg):
    data = retrieval.load_prebuilt(cfg)
    assert data is not None and data.chunks, "индекс не собран (index/)"
    return retrieval.Retriever(data, cfg, None, None)


@pytest.fixture(scope="module")
def ev(retriever):
    return retriever.search(
        "Какая арендная плата по договору аренды А-08/2024 с Северный Склад?")


# ------------------------------------------------------------------ диалог

def test_dialog_last_replica_wins():
    cell = ("Пользователь: Какой срок поставки по договору П-17/2024?\n"
            "Ассистент: Срок поставки — 30 календарных дней (п. 4.1).\n"
            "Пользователь: А какой там гарантийный срок?")
    d = D.parse_dialog(cell)
    assert d.question == "А какой там гарантийный срок?"
    assert len(d.history) == 2 and d.prev_user


def test_dialog_clamp_keeps_tail():
    cell = "Пользователь: " + "х" * 5000 + "\nАссистент: ок\nПользователь: финальный вопрос"
    d = D.clamp_dialog(D.parse_dialog(cell), 4000)
    assert d.question == "финальный вопрос"


def test_dialog_dependent_uses_history():
    cell = ("Пользователь: Какой срок поставки по договору П-17/2024?\n"
            "Ассистент: 30 календарных дней (п. 4.1).\n"
            "Пользователь: А неустойка какая?")
    d = D.parse_dialog(cell)
    assert D.is_dependent(d)
    query, context = D.retrieval_query(d)
    assert query == "А неустойка какая?" and "П-17" in context


# ------------------------------------------------------------------ verify

def test_verify_catches_invented_number(ev):
    rep = V.verify("Арендная плата составляет 987 654 рубля в месяц.", ev, "вопрос")
    kinds = {i.kind for i in rep.issues}
    assert "number" in kinds


def test_verify_catches_daykind_swap(retriever):
    # «5 рабочих дней» — в договоре поставки П-17; подмена на «календарных» ловится
    ev = retriever.search("Какой гарантийный срок по договору П-17/2024 с Альфа-Трейд?")
    rep = V.verify("Срок приёмки — 5 календарных дней с даты поставки.", ev,
                   "какой гарантийный срок по П-17")
    assert any(i.kind == "daykind" or i.kind == "number" for i in rep.issues)


def test_verify_catches_unknown_clause(ev):
    rep = V.verify("Плата составляет 450 000 рублей в месяц (п. 9.9).", ev, "аренда")
    assert any(i.kind == "clause" for i in rep.issues)


def test_verify_passes_grounded_answer(ev):
    nums = [ln.strip() for c in ev.chunks for ln in c["text"].split("\n")
            if "руб" in ln or "%" in ln]
    assert nums, "в демо-индексе нет числовых положений"
    line = nums[0][:300]
    rep = V.verify(f"По договору: {line}", ev, "вопрос")
    assert not [i for i in rep.issues if i.kind == "number"]


def test_verify_refusal_detected(ev):
    rep = V.verify("В базе знаний нет документов об этом. По памяти отвечать не буду.", ev, "q")
    assert rep.is_refusal


# ------------------------------------------------------------------ legalctx

def test_notes_on_revoked_document(cfg, retriever):
    revoked = [d for d, m in retriever.docs.items() if m.get("status") == "revoked"]
    assert revoked, "в демо-базе должна быть утратившая силу редакция"
    data = retrieval.load_prebuilt(cfg)
    ev = retrieval.Evidence(
        items=[retrieval.EvChunk(data.chunks[retriever.by_doc[revoked[0]][0]], "поиск", 1.0, 0)],
        docs={d: m for d, m in retriever.docs.items() if d in revoked})
    notes = legalctx.build_notes(ev, date(2026, 10, 10), "какой порог согласования?")
    assert any("утратил силу" in n for n in notes)


# ------------------------------------------------------------------ retrieval

def test_retriever_finds_relevant_clause(retriever, ev):
    assert ev.items, "поиск вернул пусто"
    assert any("аренд" in c.chunk["text"].lower() for c in ev.items[:4])


def test_exact_ref_boost(retriever):
    ev = retriever.search("договор поставки П-17/2024 гарантийный срок")
    assert any("П-17" in (retriever.docs[c.chunk["doc_id"]].get("doc_numbers") and
                          " ".join(retriever.docs[c.chunk["doc_id"]]["doc_numbers"]) or "")
               for c in ev.items[:3])


def test_pipeline_never_empty(cfg, retriever):
    ctx = {"cfg": cfg, "client": None, "retriever": retriever,
           "cache": pipeline.LRU(8), "budget": None, "disk": None,
           "today": retriever.as_of}
    res = pipeline.run("Какой порог согласования договоров в 2024 году?", ctx, 1e18)
    assert res.answer.strip()
    assert res.source in ("extractive", "refusal", "gate", "llm")


def test_finalize_strips_markdown():
    assert pipeline.finalize("**да**, `500 000`") == "да, 500 000"


# ------------------------------------------------------------------ CSV

def test_submission_csv_contract(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    ids, q = ["q1", "q2", "q2"], {"q1": "вопрос", "q2": "ещё"}
    solution.write_submission(ids, {"q1": "  ответ\nс переносом  "}, "отказ")
    rows = list(csv.reader(open("submission.csv", encoding="utf-8", newline="")))
    assert rows[0] == ["id", "answer"]
    assert [r[0] for r in rows[1:]] == ids          # все id, дедуп не нужен
    assert rows[1][1] == "ответ с переносом"        # одна строка, чистые пробелы
    assert all(r[1] for r in rows[1:])
