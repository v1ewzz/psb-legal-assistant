# -*- coding: utf-8 -*-
"""Юридический контекст для промпта (п.5, 8, 10): статус и редакция каждого
документа, вступившие/будущие изменения закона, конфликты между документами.
Всё считается кодом по метаданным индекса — это доверенные заметки для модели."""
from __future__ import annotations

import re
from datetime import date
from typing import Optional

from retrieval import Evidence
from textutil import content_stems, normalize_text, tokenize, truncate

_FACT_RE = re.compile(
    r"(\d{1,3}(?:[ \u00a0]\d{3})+|\d+(?:[.,]\d+)?)\s*(?:\([^)]{0,80}\)\s*)?"
    r"(календарн\w*\s+дн\w*|рабоч\w*\s+дн\w*|дн\w*|месяц\w*|лет|год\w*|час\w*|%|процент\w*|"
    r"руб\w*|₽|млн|тыс\w*)", re.I)


def _unit(u: str) -> str:
    u = u.lower()
    if u.startswith("календ"):
        return "кал.дн"
    if u.startswith("рабоч"):
        return "раб.дн"
    for k, v in (("дн", "дн"), ("месяц", "мес"), ("год", "год"), ("лет", "год"), ("час", "час"),
                 ("%", "%"), ("процент", "%"), ("руб", "руб"), ("₽", "руб"), ("млн", "руб"),
                 ("тыс", "руб")):
        if u.startswith(k):
            return v
    return u


def extract_facts(text: str) -> list[tuple[str, str, frozenset]]:
    """(unit, number, ключевые основы вокруг) — для сопоставления условий разных документов."""
    out = []
    t = normalize_text(text)
    for m in _FACT_RE.finditer(t):
        num = re.sub(r"[ \u00a0]", "", m.group(1)).replace(",", ".")
        before = t[max(0, m.start() - 90):m.start()]
        after = t[m.end():m.end() + 40]
        keys = frozenset(s for s in content_stems(before + " " + after) if len(s) > 3)
        out.append((_unit(m.group(2)), num, keys))
    return out


def find_conflicts(ev: Evidence, docs: dict[str, dict]) -> list[dict]:
    """Пары фактов из разных документов: одна единица измерения, ≥2 общих ключа, разные числа."""
    facts = []
    for it in ev.items[:6]:
        c = it.chunk
        for unit, num, keys in extract_facts(c["body"]):
            facts.append((c["doc_id"], c["chunk_id"], unit, num, keys))
    seen = set()
    out = []
    for i, a in enumerate(facts):
        for b in facts[i + 1:]:
            if a[0] == b[0] or a[2] != b[2] or a[3] == b[3]:
                continue
            if docs[a[0]]["doc_type"] != docs[b[0]]["doc_type"] and not _relation(docs, a[0], b[0]):
                continue
            shared = a[4] & b[4]
            if len(shared) < 3:
                continue
            pair = tuple(sorted((a[0], b[0]))) + (a[2],) + tuple(sorted((a[3], b[3])))
            if pair in seen:
                continue
            seen.add(pair)
            out.append({"a": a[0], "b": b[0], "unit": a[2], "na": a[3], "nb": b[3],
                        "shared": sorted(shared)[:4]})
    return out[:6]


def _relation(docs: dict[str, dict], a: str, b: str) -> Optional[str]:
    ma, mb = docs[a], docs[b]
    if ma.get("superseded_by") == b or mb.get("superseded_by") == a:
        return "edition"
    for r in ma.get("relations", []):
        if r["target"] == b:
            return r["type"]
    return None


def as_of_date(ev_docs: dict[str, dict], today: date) -> date:
    return today


def doc_status_line(m: dict, today: date) -> str:
    parts = []
    if m["doc_type"] == "law":
        parts.append("закон/кодекс")
    elif m.get("edition_date"):
        parts.append(f"редакция от {m['edition_date']}")
    if m.get("status") == "revoked":
        vt = m.get("valid_to")
        parts.append("УТРАТИЛ СИЛУ" + (f" с {vt}" if vt else "") + " — не применять к текущим ситуациям")
    else:
        vf = m.get("valid_from")
        parts.append("действует" + (f" с {vf}" if vf else ""))
    return "; ".join(parts)


def pending_notes(m: dict, today: date, limit: int = 3) -> list[str]:
    """Изменения нормы, которые вступают в силу позже `today` (в тексте может быть старая норма)."""
    out = []
    for a in m.get("amendments", []):
        d = a.get("date")
        try:
            if d and date.fromisoformat(d) > today and a.get("ntype") in ("changed", "added", "revoked"):
                out.append(f"{a['what']}: изменение вступает в силу {d} (ещё не действует)")
        except ValueError:
            continue
    return out[:limit]


def build_notes(ev: Evidence, today: date, question: str) -> list[str]:
    """Заметки для промпта: статусы, свежие/будущие изменения, конфликты, отсутствующие ссылки."""
    notes: list[str] = []
    docs = ev.docs
    for ref in ev.missing_refs:
        notes.append(f"В базе знаний НЕТ документа, соответствующего ссылке «{ref}» из вопроса — "
                     f"не отвечай по памяти; прямо скажи, что такого документа в базе нет.")
    if ev.coverage < 0.4 and not ev.missing_refs:
        notes.append("Найденные фрагменты слабо связаны с вопросом. Если среди них нет прямого ответа, "
                     "откажись по шаблону из правила 9, ничего не додумывай.")
    revoked = [m for m in docs.values() if m.get("status") == "revoked"]
    for m in revoked:
        cur = m.get("superseded_by")
        tail = f" Действует: {docs[cur]['label']}." if cur and cur in docs else ""
        notes.append(f"«{m['label']}» утратил силу — применять нельзя, упоминай только как прежнюю редакцию.{tail}")
    for m in docs.values():
        for p in pending_notes(m, today):
            notes.append(f"{m['label']}: {p}. Текст во фрагменте может отражать прежнюю норму — укажи это.")
        recent = [a for a in m.get("amendments", []) if a.get("date")
                  and today.replace(year=today.year - 1) <= date.fromisoformat(a["date"]) <= today]
        if recent and m["doc_type"] == "law":
            recent.sort(key=lambda a: a["date"], reverse=True)
            notes.append(f"{m['label']}: норма менялась с {recent[0]['date']} (уже действует).")
    for it in ev.items:
        if it.reason.startswith("изменено допсоглашением"):
            notes.append("Допсоглашение изменило условие базового договора — применяется редакция из допсоглашения "
                         "(с учётом срока его применения).")
            break
    for it in ev.items:
        if it.reason.startswith("связанный документ") and any(
                r["type"] == "supplements" for m in docs.values() for r in m.get("relations", [])):
            notes.append("Письмо/документ дополняет другой документ: условия применяются совместно, "
                         "укажи оба источника.")
            break
    conflicts = find_conflicts(ev, docs)
    for c in conflicts[:3]:
        a, b = docs[c["a"]]["label"], docs[c["b"]]["label"]
        rel = _relation(docs, c["a"], c["b"])
        if rel == "edition":
            old, new = (c["a"], c["b"]) if docs[c["a"]].get("superseded_by") == c["b"] else (c["b"], c["a"])
            notes.append(f"Расхождение редакций: в «{docs[old]['label']}» (утратила силу) — "
                         f"{c['na' if old == c['a'] else 'nb']} {c['unit']}, в действующей «{docs[new]['label']}» — "
                         f"{c['nb' if old == c['a'] else 'na']} {c['unit']}. Применяй действующую.")
        elif rel in ("amends", "supplements", "refers"):
            notes.append(f"Расхождение между связанными документами «{a}» ({c['na']} {c['unit']}) и «{b}» "
                         f"({c['nb']} {c['unit']}): изменяющий/дополняющий документ имеет приоритет — объясни.")
        else:
            notes.append(f"Условия в «{a}» ({c['na']} {c['unit']}) и «{b}» ({c['nb']} {c['unit']}) различаются: "
                         f"это разные документы. Если вопрос не называет документ — приведи оба варианта "
                         f"с указанием документа, не смешивай их.")
    contracts = {d for d, m in docs.items() if m["doc_type"] == "contract"}
    if len(contracts) >= 2 and not any(
            n.startswith("Условия в") for n in notes) and not _names_contract(question, docs, contracts):
        notes.append("В найденных материалах несколько договоров — если вопрос не называет конкретный, "
                     "укажи, из какого договора условие, либо приведи условия по каждому.")
    return notes[:8]


def _names_contract(question: str, docs: dict[str, dict], contracts: set[str]) -> bool:
    q = normalize_text(question)
    for d in contracts:
        for n in docs[d].get("doc_numbers", []):
            if n in q:
                return True
    return False


def fragment_header(i: int, chunk: dict, m: dict, reason: str, today: date) -> str:
    attrs = [f'id="c{i}"', f'документ="{m["label"]}"', f'раздел="{truncate(chunk["path"], 80)}"',
             f'статус="{"утратил силу" if m.get("status") == "revoked" else "действует"}"']
    if m.get("edition_date"):
        attrs.append(f'редакция="{m["edition_date"]}"')
    if reason and reason != "поиск":
        attrs.append(f'связь="{truncate(reason, 70)}"')
    return "<fragment " + " ".join(attrs) + ">"
