"""Offline diagnostics; standard library only, no model, network or project imports.

These checks detect declared regressions. A positive result is not a legal verdict
and is not an estimate of the organiser's LLM-judge score.
"""
from __future__ import annotations

import csv
import re
import unicodedata
from pathlib import Path


def normalize(text: str) -> str:
    return re.sub(r"\s+", " ", unicodedata.normalize("NFKC", text).lower().replace("ё", "е")).strip()


NUMBER = r"(?:\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
WORDS = {"одного": "1", "один": "1", "одной": "1", "двух": "2", "два": "2",
         "трех": "3", "три": "3", "четырех": "4", "четыре": "4",
         "пяти": "5", "пять": "5", "шести": "6", "шесть": "6"}


def numeric_text(text: str) -> str:
    text = normalize(text)
    # Source spells numbers out in parentheses. Keep the numeric form once.
    text = re.sub(r"(?<=\d)\s*\([^)]{1,80}\)", "", text)
    for word, value in WORDS.items():
        text = re.sub(rf"\b{word}\b", value, text)
    return text


def number_keys(text: str) -> set[str]:
    text = numeric_text(text)
    result = {m.group().replace(" ", "").replace("\u00a0", "").replace(",", ".")
              for m in re.finditer(NUMBER, text)}
    for match in re.finditer(rf"({NUMBER})\s*(млн|миллион\w*|млрд|миллиард\w*|тыс\.?|тысяч\w*)", text):
        from decimal import Decimal
        multiplier = 10**9 if match[2].startswith(("млрд", "миллиард")) else 10**6 if match[2].startswith(("млн", "миллион")) else 1000
        value = format(Decimal(match[1].replace(" ", "").replace(",", ".")) * multiplier, "f")
        result.add(value.rstrip("0").rstrip(".") if "." in value else value)
    return result


UNIT = r"(?:рабоч\w*\s+д(?:ень|ня|ней)|календарн\w*\s+д(?:ень|ня|ней)|банковск\w*\s+д(?:ень|ня|ней)|день|дня|дней|месяц\w*|недел\w*|год\w*|лет|час\w*|руб\w*|₽|процент\w*|%)"


def unit_kind(value: str) -> str:
    value = normalize(value)
    for prefix, kind in [("рабоч", "business_days"), ("календар", "calendar_days"),
                         ("банков", "bank_days"), ("д", "days"), ("месяц", "months"),
                         ("недел", "weeks"), ("год", "years"), ("лет", "years"),
                         ("час", "hours"), ("руб", "rubles"), ("₽", "rubles"),
                         ("процент", "percent"), ("%", "percent")]:
        if value.startswith(prefix):
            return kind
    return value


def number_unit_pairs(text: str) -> set[tuple[str, str]]:
    pairs = set()
    for match in re.finditer(rf"(?<![\d.,])({NUMBER})\s*(?:-?го\s+)?({UNIT})(?![а-я])", numeric_text(text)):
        pairs.add((match[1].replace(" ", "").replace(",", "."), unit_kind(match[2])))
    return pairs


def read_csv(path: Path, value: str = "answer") -> dict[str, str]:
    """Reject duplicate/empty IDs rather than silently dropping rows."""
    with path.open(encoding="utf-8-sig", newline="") as stream:
        reader = csv.DictReader(stream)
        if reader.fieldnames != ["id", value]:
            raise ValueError(f"{path.name}: expected columns id,{value}")
        result = {}
        for row in reader:
            key = (row.get("id") or "").strip()
            answer = row.get(value)
            if not key or key in result or answer is None or not answer.strip() or None in row:
                raise ValueError(f"{path.name}: invalid or duplicate row {reader.line_num}")
            result[key] = answer.strip()
    return result


def score_answer(answer: str, gold: str, question: str = "") -> dict:
    """Legacy-compatible diagnostic score with pair checks and review flags.

    Additional numbers are a review flag: a short gold answer cannot enumerate all
    legitimate facts. Use explicit source-backed assertions for acceptance.
    """
    a, g = normalize(answer), normalize(gold)
    verdict_re = r"^(да|нет)\b"
    gv, av = re.match(verdict_re, g), re.match(verdict_re, a)
    verdict = None if gv is None else bool(av and av[1] == gv[1])
    missing = sorted(number_keys(g) - number_keys(a))
    extra = sorted(number_keys(a) - number_keys(g + " " + question))
    gp, ap = number_unit_pairs(g), number_unit_pairs(a)
    refs = lambda t: set(re.findall(r"\bп\.\s*(\d+(?:\.\d+)+)", t))
    gr, ar = refs(g), refs(a)
    refusal_re = r"нет документов|не могу|не удалось|недостаточно (?:данных|сведений)|нет сведений"
    refusal = bool(re.search(refusal_re, a))
    expected_refusal = bool(re.search(refusal_re, g))
    checks = {"nonempty": bool(a), "verdict": verdict,
              "numbers": None if not number_keys(g) else not missing,
              "units": None if not gp else gp <= ap,
              "refs": None if not gr else gr <= ar,
              "refusal": refusal if expected_refusal else not refusal,
              "length": len(answer) <= 10000}
    values = [v for v in checks.values() if v is not None]
    return {**checks, "missing": missing, "extra_numbers": extra,
            "words": len(a.split()), "score": sum(values) / len(values),
            "needs_review": bool(extra), "diagnostic_only": True}


def check_assertions(answer: str, record: dict) -> dict:
    """Apply explicit reviewed assertions, never treating keywords as semantic proof."""
    text = numeric_text(answer)
    failures, review = [], []
    if record.get('original_sources'):
        review.append('claim_source_binding')
    if record.get('source_gap') or record.get('type') in ('refusal','partial','not_established'):
        review.append('answer_scope')
    if not text:
        failures.append("empty_answer")
    if len(answer) > 10000:
        failures.append("answer_too_long")
    for assertion in record.get("assertions", []):
        key = assertion["id"]
        all_of, any_of = assertion.get("all_of", []), assertion.get("any_of", [])
        present = all(re.search(p, text) is not None for p in all_of)
        present &= not any_of or any(re.search(p, text) is not None for p in any_of)
        prohibited = any(re.search(p, text) is not None for p in assertion.get("forbidden", []))
        if assertion.get("required", True) and not present:
            failures.append(key + ":missing")
        if prohibited:
            failures.append(key + ":contradiction")
        if assertion.get("manual_review"):
            review.append(key)
    for rule in record.get("prohibited_patterns", []):
        if re.search(rule["pattern"], text):
            failures.append(rule["id"] + ":prohibited")
    # Documented numeric/period constraints are optional: no invented universal policy.
    for item in record.get("required_pairs", []):
        if tuple(item) not in number_unit_pairs(answer):
            failures.append("pair:" + ":".join(item))
    for item in record.get("forbidden_pairs", []):
        if tuple(item) in number_unit_pairs(answer):
            failures.append("forbidden_pair:" + ":".join(item))
    return {"id": record["id"], "rule_failures": failures, "manual_review": review,
            "rule_pass": not failures, "semantic_verdict": "not_automatically_established"}


def retained_fragments(chunks: list[dict], per_fragment: int, max_chars: int) -> dict[str, str]:
    """Keep the exact truncated, XML-escaped text and break semantics of the bundle."""
    retained, total = {}, 0
    for chunk in chunks:
        fragment = str(chunk.get("text_ctx", chunk.get("text", "")))[:per_fragment]
        fragment = fragment.replace("</", "<\u200b/")
        if total + len(fragment) > max_chars:
            break
        retained[chunk["chunk_id"]] = fragment
        total += len(fragment)
    return retained


def fact_in_evidence(pattern: re.Pattern, chunks: list[dict], per_fragment: int,
                     max_chars: int, notes: list[str] | None = None,
                     source_doc_ids: set[str] | None = None) -> bool:
    retained = retained_fragments(chunks, per_fragment, max_chars)
    allowed = {c["chunk_id"] for c in chunks
               if source_doc_ids is None or c.get("doc_id") in source_doc_ids}
    # Notes must have their own checked provenance when source restriction is requested.
    return any(pattern.search(text) for cid, text in retained.items() if cid in allowed) or (
        source_doc_ids is None and any(pattern.search(n) for n in notes or []))
