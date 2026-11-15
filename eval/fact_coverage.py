# -*- coding: utf-8 -*-
"""Fact-coverage eval: дошла ли норма до evidence (детерминированно, без API).

Для каждого демо-вопроса:
1. из эталонного ответа (data/demo_output.csv) извлекаются числовые факты
   (ключи pipeline._number_keys: суммы, сроки, даты, проценты);
2. gold-чанки — чанки индекса, содержащие число факта (по тем же ключам);
3. повторяется retrieval-путь answer_one (guard.heuristic_queries →
   temporal.search_filters → retriever.search(top_n=8) → expand);
4. проверяется судьба каждого gold-чанка:
   retrieved  — вошёл в топ-8 выдачи (модель видела бы его вообще);
   evidence   — вошёл в финальный evidence-бандл (в промпте);
   budget_cut — был в selection, но вытеснен бюджетом символов.

Метрики: fact_retrieval (факт найден в выдаче), fact_coverage (факт дошёл до
evidence), budget_loss (дошёл до selection, но срезан бюджетом). Это то место,
где промпт бессилен: модель не может назвать число, которого не видела.

Запуск:
  python eval/fact_coverage.py                          # дефолтный бюджет
  EVIDENCE_PER_FRAGMENT_CHARS=1300 EVIDENCE_MAX_CHARS=9000 python eval/fact_coverage.py
  python eval/fact_coverage.py --questions data/demo_questions.csv
"""
from __future__ import annotations

import argparse
import csv
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import config
import guard
import pipeline
import temporal
from retrieval import Retriever, find_artifact, load_chunks

UNIT_RE = re.compile(
    r"(\d{1,3}(?:[ \u00a0]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)[-—–\s]*"
    r"(руб\w*|₽|%|процент\w*|млн|млрд|тыс\.?|"
    r"мес\w*|недел\w*|дн\w*|день|числ\w*|год\w*|календарн\w*|рабоч\w*)",
    re.I)


def load_questions(path: Path) -> dict[str, str]:
    questions: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            qid = (row.get("id") or "").strip()
            q = (row.get("question") or "").strip()
            if qid and q:
                questions[qid] = q
    return questions


def load_gold(path: Path) -> dict[str, str]:
    gold: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            qid = (row.get("id") or "").strip()
            ans = (row.get("answer") or "").strip()
            if qid and ans:
                gold[qid] = ans
    return gold


def facts_of(answer: str) -> list[dict]:
    """Числовые факты эталона: число (ключ) + единица рядом + готовый паттерн."""
    facts: list[dict] = []
    seen: set[str] = set()
    for m in UNIT_RE.finditer(answer):
        raw, unit = m.group(1), m.group(2).lower()
        key = raw.replace("\u00a0", " ").replace(" ", "").replace(",", ".").lstrip("0") or "0"
        if key in seen:
            continue
        seen.add(key)
        facts.append({"key": key, "raw": raw, "unit": unit,
                      "pat": _num_pattern(raw, unit),
                      "snippet": answer[max(0, m.start() - 12):m.end() + 12]})
    # даты-ключи без единицы (01.03, 2.4 — номер пункта тоже факт ссылки)
    for key in sorted(pipeline._number_keys(answer)):
        if key not in seen and "." in key:
            seen.add(key)
            facts.append({"key": key, "raw": key, "unit": "date/ref",
                          "pat": _num_pattern(key, "date/ref"),
                          "snippet": key})
    return facts


def fact_matches(fact: dict, chunk_text: str) -> bool:
    """Факт найден в чанке по готовому паттерну fact["pat"].
    Терпит пробелы/NBSP в числах, слова в скобках между числом и единицей,
    дефисы («5-го числа») и ведущие нули в датах/пунктах («1.03» ↔ «01.03»)."""
    return fact["pat"].search(chunk_text) is not None


def _num_pattern(raw: str, unit: str) -> re.Pattern:
    """«450 000» + руб → число (с \\s между группами), затем ≤50 не-точечных
    символов до головы единицы (внутри предложения). date/ref — точное число
    с границами цифр, в двух вариантах нулей («1.03» и «01.03»)."""
    num = raw.replace("\u00a0", " ").replace(",", ".")
    if unit == "date/ref":
        parts = num.split(".")
        alts = {num}
        if "." in num:
            padded = ".".join(p.zfill(2) for p in parts)
            alts.add(padded)
            unpadded = ".".join(p.lstrip("0") or "0" for p in parts)
            alts.add(unpadded)
        body = "|".join(re.escape(a) for a in sorted(alts, key=len, reverse=True))
        return re.compile(rf"(?<![\d.,])(?:{body})(?![\d])", re.I)
    parts = [re.escape(p) for p in num.split(" ") if p]
    body = r"[\s\u00a0]?".join(parts)
    unit_head = re.escape(unit[:4])
    return re.compile(rf"(?<![\d.,]){body}[^\n.]{{0,50}}?{unit_head}", re.I)





def run(questions_path: Path, gold_path: Path,
        per_fragment: int | None, max_chars: int | None,
        top_n: int = 8, sibling_fill: bool = False,
        verbose: bool = True) -> dict:
    cfg = config.from_env({})
    chunks = load_chunks(cfg)
    npz = find_artifact(cfg, "bm25.index.npz")
    refs_path = find_artifact(cfg, "refs.json")
    import json
    refs = json.loads(refs_path.read_text(encoding="utf-8")) if refs_path else None
    retriever = Retriever(chunks, npz, refs)
    docs = retriever.docs if hasattr(retriever, "docs") else None
    if docs is None:
        import gzip
        docs_path = find_artifact(cfg, "docs.json")
        docs = json.loads(docs_path.read_text(encoding="utf-8")) if docs_path else []
    fact_cache = [str(c.get("text", "")) + "\n" + str(c.get("text_ctx", ""))
                  for c in chunks]

    questions = load_questions(questions_path)
    gold = load_gold(gold_path)

    rows = []
    tot_facts = tot_retr = tot_evid = tot_budget_cut = 0
    per_frag = per_fragment if per_fragment is not None else cfg.evidence_per_fragment_chars
    max_ch = max_chars if max_chars is not None else cfg.evidence_max_chars

    for qid, question in questions.items():
        ans = gold.get(qid)
        if not ans:
            continue
        facts = facts_of(ans)
        # факты, уже присутствующие в вопросе («август 2024», «500 000») —
        # исходные данные, evidence содержать их не обязан
        facts = [f for f in facts
                 if not (question and f["pat"].search(question))]
        dialog = guard.parse_dialog(question)
        queries = guard.heuristic_queries(dialog)
        standalone = guard.standalone_query(dialog)
        t_text = pipeline._temporal_text(dialog, queries)
        filters = temporal.search_filters(t_text, docs)
        t0 = time.time()
        topN = retriever.search(
            queries, top_k=24, rerank_query=standalone, client=None,
            top_n=top_n,
            drop_doc_ids=filters.get("drop_doc_ids"),
            boost_doc_ids=filters.get("boost_doc_ids"),
            deadline=time.time() + 30,
        )
        if sibling_fill and topN:
            # симуляция sibling-fill: добор чанками документов из топ-8,
            # по полному RRF-порядку (топ-24), без новых документов
            full = retriever.search(
                queries, top_k=24, rerank_query=standalone, client=None,
                top_n=24, drop_doc_ids=filters.get("drop_doc_ids"),
                boost_doc_ids=filters.get("boost_doc_ids"),
                deadline=time.time() + 30)
            core_docs = {c["doc_id"] for c in topN}
            for c in full:
                if len(topN) >= top_n + 4:
                    break
                if c["doc_id"] in core_docs and \
                        c["chunk_id"] not in {x["chunk_id"] for x in topN}:
                    topN.append(c)
        sel_ms = int((time.time() - t0) * 1000)
        expanded = retriever.expand(topN) if topN else []
        topN_ids = {c["chunk_id"] for c in topN}
        notes = temporal.reason_notes(t_text, docs, expanded)
        note_text = " ".join(notes)
        bundle, _ = pipeline._evidence_bundle(
            expanded, notes, per_fragment=per_frag, max_chars=max_ch)
        # надёжнее: пересобрать по счётчику символов
        evidence_ids = _bundle_ids(expanded, per_frag, max_ch)

        fact_rows = []
        for fact in facts:
            gold_ids = [chunks[i]["chunk_id"] for i, text
                        in enumerate(fact_cache) if fact_matches(fact, text)]
            retr = any(g in topN_ids or g in {c["chunk_id"] for c in expanded}
                       for g in gold_ids)
            evid = bool(
                any(g in evidence_ids for g in gold_ids)
                or (note_text and fact_matches(fact, note_text)))
            cut = retr and not evid
            tot_facts += 1
            tot_retr += int(retr)
            tot_evid += int(evid)
            tot_budget_cut += int(cut)
            fact_rows.append({
                "key": fact["key"], "unit": fact["unit"],
                "gold": "; ".join(gold_ids[-2:]) if gold_ids else "—",
                "retrieved": "да" if retr else "НЕТ",
                "evidence": "да" if evid else "НЕТ",
                "budget_cut": "✂" if cut else "",
            })

        rows.append({"qid": qid, "question": question[:70],
                     "sel_ms": sel_ms, "facts": fact_rows,
                     "n_gold_chunks": len(topN_ids)})

    n = max(tot_facts, 1)
    summary = {
        "facts": tot_facts,
        "fact_retrieval": round(100 * tot_retr / n, 1),
        "fact_coverage": round(100 * tot_evid / n, 1),
        "budget_loss": round(100 * tot_budget_cut / n, 1),
        "per_fragment": per_frag, "max_chars": max_ch,
    }
    if verbose:
        print(f"\n=== FACT-COVERAGE  (per_fragment={per_frag}, "
              f"max_chars={max_ch}) ===")
        print(f"фактов: {tot_facts} | retrieval: {summary['fact_retrieval']}% "
              f"| evidence: {summary['fact_coverage']}% | "
              f"срезано бюджетом: {summary['budget_loss']}%")
        print()
        for row in rows:
            marks = []
            for f in row["facts"]:
                flag = ""
                if f["retrieved"] == "НЕТ":
                    flag = "RETR-FAIL"
                elif f["evidence"] == "НЕТ":
                    flag = "BUDGET-CUT"
                if flag:
                    marks.append(f"{f['key']}({f['unit']})={flag}")
            status = "; ".join(marks) if marks else "ok"
            print(f"Q{row['qid']}: {status}")
    return summary


def _bundle_ids(expanded: list[dict], per_frag: int, max_chars: int) -> set[str]:
    """Какие чанки реально вошли бы в бандл (та же логика, что _evidence_bundle)."""
    ids, total = set(), 0
    for chunk in expanded:
        frag = str(chunk.get("text_ctx", chunk.get("text", "")))[:per_frag]
        if total + len(frag) > max_chars:
            break
        ids.add(chunk["chunk_id"])
        total += len(frag)
    return ids


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--questions", default="data/demo_questions.csv")
    ap.add_argument("--gold", default="data/demo_output.csv")
    ap.add_argument("--per-fragment", type=int, default=None)
    ap.add_argument("--max-chars", type=int, default=None)
    ap.add_argument("--top-n", type=int, default=8)
    ap.add_argument("--sibling-fill", action="store_true")
    args = ap.parse_args()
    cfg = config.from_env({})
    pf = args.per_fragment if args.per_fragment is not None else cfg.evidence_per_fragment_chars
    mc = args.max_chars if args.max_chars is not None else cfg.evidence_max_chars
    run(Path(args.questions), Path(args.gold), pf, mc, top_n=args.top_n,
        sibling_fill=args.sibling_fill)


if __name__ == "__main__":
    main()
