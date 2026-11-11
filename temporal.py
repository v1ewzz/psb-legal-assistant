# -*- coding: utf-8 -*-
"""Temporal-слой v2 (D1, §11): даты события в вопросе, окна редакций документов,
заметки для Reason. Даты и выбор редакции считает код, LLM только вставляет.

Правила §4.3: редакционные оговорки в обычных ответах НЕ добавляются —
заметки строятся только при явной дате события в вопросе (T2/T3/T4/T11).
"""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

TODAY = date(2026, 10, 10)   # дата выгрузки базы = «сегодня» системы (§11.1)

MONTH_PREFIXES = [
    ("январ", 1), ("феврал", 2), ("март", 3), ("апрел", 4), ("ма[йея]", 5),
    ("июн", 6), ("июл", 7), ("август", 8), ("сентябр", 9), ("октябр", 10),
    ("ноябр", 11), ("декабр", 12),
]
MONTH_YEAR_RE = re.compile(
    r"\b(?:(\d{1,2})\s+)?(" + "|".join(p for p, _ in MONTH_PREFIXES) + r")\w*\s+(\d{4})\b",
    re.I)
NUM_DATE_RE = re.compile(r"\b(\d{1,2})[.\-/](\d{1,2})[.\-/](\d{4})\b")
DOC_NUM_STRIP_RE = re.compile(r"\b[А-ЯЁ]{1,2}-\d+(?:/\d{4})?\b")
YEAR_RE = re.compile(r"\b(19|20)(\d{2})\b")
OLD_EDITION_MARKERS_RE = re.compile(
    r"ред\.?\s*2023|утратил\w* силу|ранее\s+действов|прежде|до\s*01\.03\.2025", re.I)


def _month_no(text: str) -> Optional[int]:
    for prefix, num in MONTH_PREFIXES:
        if re.match(prefix, text, re.I):
            return num
    return None


def event_date(query: str) -> tuple[Optional[date], str]:
    """Дата события из вопроса → (date|None, точность 'day'|'month'|'year').

    Приоритет (§10.3): dd.mm.yyyy → «N месяца YYYY» / «месяц YYYY» → год.
    Номера документов («А-08/2024») годом события не считаются.
    """
    q = query or ""
    m = NUM_DATE_RE.search(q)
    if m:
        try:
            return date(int(m.group(3)), int(m.group(2)), int(m.group(1))), "day"
        except ValueError:
            pass
    m = MONTH_YEAR_RE.search(q)
    if m:
        month = _month_no(m.group(2))
        if month:
            day = int(m.group(1)) if m.group(1) else 1
            try:
                return date(int(m.group(3)), month, day), ("day" if m.group(1) else "month")
            except ValueError:
                pass
    q_no_nums = DOC_NUM_STRIP_RE.sub(" ", q)
    m = YEAR_RE.search(q_no_nums)
    if m:
        year = int(m.group(1) + m.group(2))
        if 1990 <= year <= 2100:
            return date(year, 1, 1), "year"
    return None, ""


def _fmt(d: Optional[str]) -> str:
    """ISO-строка → «01.03.2025»; None → «?»."""
    if not d:
        return "?"
    return d[8:10] + "." + d[5:7] + "." + d[0:4]


def _docs_by_kind(docs: list, kind: str) -> list[dict]:
    return [d for d in docs if d.get("kind") == kind]


def search_filters(query: str, docs: list) -> dict:
    """Фильтры retrieval по дате события (§11.2): какие редакции тащить.

    - дата события в прошлом → бустить отозванную редакцию Регламента,
      действующую не выбрасывать (для сравнения порогов, как в эталоне Q5);
    - без даты → отозванную редакцию не тащим (оговорки не нужны, §4.3),
      кроме явных маркеров вопроса про старую редакцию.
    """
    revoked = [d for d in _docs_by_kind(docs, "regulation") if d.get("status") == "revoked"]
    active = [d for d in _docs_by_kind(docs, "regulation") if d.get("status") == "active"]
    ev, _ = event_date(query)
    drop, boost = set(), set()
    marker = bool(OLD_EDITION_MARKERS_RE.search(query or ""))
    cutoff = None
    if revoked and revoked[0].get("valid_to"):
        try:
            cutoff = date.fromisoformat(revoked[0]["valid_to"])
        except ValueError:
            cutoff = None
    if ev and cutoff and ev < cutoff:
        boost |= {d["doc_id"] for d in revoked}
    elif ev and cutoff and ev >= cutoff:
        if not marker:
            drop |= {d["doc_id"] for d in revoked}
    elif not ev and not marker:
        drop |= {d["doc_id"] for d in revoked}
    return {"drop_doc_ids": drop, "boost_doc_ids": boost, "event_date": ev}


def reason_notes(query: str, docs: list, chunks: list) -> list[str]:
    """Заметки к Reason: какая редакция действует на дату события и т.п.

    Заметки — данные для модели (и для verify: их текст входит в evidence).
    Пусто, если в вопросе нет даты события и нет версионных конфликтов.
    """
    ev, _ = event_date(query or "")
    notes: list[str] = []
    chunk_docs = {c["doc_id"] for c in chunks or []}

    if ev:
        notes.append(f"Дата события из вопроса: {ev.strftime('%d.%m.%Y')}. "
                     f"Сопоставляй её с датами действия документов во фрагментах.")

    # T2: две редакции Регламента
    revoked = [d for d in _docs_by_kind(docs, "regulation") if d.get("status") == "revoked"]
    active = [d for d in _docs_by_kind(docs, "regulation") if d.get("status") == "active"]
    if revoked and active:
        try:
            rev_from = date.fromisoformat(revoked[0]["valid_from"]) if revoked[0].get("valid_from") else None
            rev_to = date.fromisoformat(revoked[0]["valid_to"]) if revoked[0].get("valid_to") else None
            act_from = date.fromisoformat(active[0]["valid_from"]) if active[0].get("valid_from") else None
        except ValueError:
            rev_from = rev_to = act_from = None
        if ev and rev_from and rev_to and rev_from <= ev < rev_to:
            notes.append(
                f"На {ev.strftime('%d.%m.%Y')} действовал Регламент согласования договоров "
                f"в редакции от {_fmt(rev_from.isoformat())} — используй значения ИМЕННО этой "
                f"редакции из фрагментов. Редакция от {_fmt((act_from or rev_to).isoformat())} "
                f"на эту дату не распространялась; её значения можно упомянуть одной фразой "
                f"только как справку, с оговоркой «к событию не применяется».")
        elif ev and act_from and ev >= act_from:
            notes.append(f"На {ev.strftime('%d.%m.%Y')} действует редакция Регламента "
                         f"от {_fmt(act_from.isoformat())}.")

    # T3: письмо издано раньше, чем вступило в силу
    for d in _docs_by_kind(docs, "letter"):
        if d["doc_id"] not in chunk_docs:
            continue
        vf = date.fromisoformat(d["valid_from"]) if d.get("valid_from") else None
        dd = date.fromisoformat(d["doc_date"]) if d.get("doc_date") else None
        if ev and dd and vf and dd <= ev < vf:
            notes.append(f"Письмо № {d.get('number')} издано {_fmt(d['doc_date'])}, но действует "
                         f"с {_fmt(d['valid_from'])} — на {ev.strftime('%d.%m.%Y')} оно ещё "
                         f"не действовало и требования по нему не предъявлялись.")
        elif ev and vf and ev >= vf:
            notes.append(f"Письмо № {d.get('number')} действует с {_fmt(d['valid_from'])} — "
                         f"на {ev.strftime('%d.%m.%Y')} оно уже действовало.")

    # T4: ДС с собственной датой применения
    for d in _docs_by_kind(docs, "supplement"):
        if d["doc_id"] not in chunk_docs:
            continue
        applies = d.get("applies_from")
        signed = d.get("signed") or d.get("doc_date")
        if applies and signed:
            notes.append(
                f"Дополнительное соглашение № 1 от {_fmt(signed)} изложило п. 3.2 договора "
                f"№ {d.get('amends_doc')} в новой редакции; она применяется к спецификациям, "
                f"подписанным после {_fmt(applies)}. До этой даты действует исходная "
                f"редакция пункта из договора.")

    # T11: будущие изменения законов (текста в базе нет)
    for d in docs:
        if d["doc_id"] not in chunk_docs or d.get("doc_type") != "law":
            continue
        for fut in d.get("future_editions") or []:
            try:
                fd = date.fromisoformat(fut)
            except (ValueError, TypeError):
                continue
            if ev and ev >= fd and fd > TODAY:
                notes.append(f"С {_fmt(fut)} статья изменяется, но текст изменений в базе "
                             f"отсутствует — вывод о содержании будущей редакции невозможен.")
    return notes
