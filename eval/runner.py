# -*- coding: utf-8 -*-
"""Eval-раннер v2 (§17.1–17.2) — режимы без живого LLM (ключ недоступен, U9).

Режимы:
  recall  — retrieval-метрика: доля ожидаемых (документ, пункт) из
            demo_gold.jsonl в топе выдачи (без реранка, эвристические запросы);
  facts   — факт-чек сгенерированного submission.csv против эталонных фактов
            demo_gold.jsonl + проверки стиля (§4.1: одна строка, без маркдауна).

Использование:
  py eval/runner.py recall
  py eval/runner.py facts --submission submission.csv
"""
from __future__ import annotations

import argparse
import csv
import json
import re
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import guard  # noqa: E402
import temporal  # noqa: E402
from config import Config  # noqa: E402
from retrieval import Retriever, find_artifact, load_chunks  # noqa: E402

ROOT = Path(__file__).resolve().parents[1]


def load_gold() -> list[dict]:
    gold_path = ROOT / "eval" / "demo_gold.jsonl"
    return [json.loads(line) for line in
            gold_path.read_text(encoding="utf-8").splitlines() if line.strip()]


def load_questions() -> dict[str, str]:
    path = ROOT / "data" / "demo_questions.csv"
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {str(r["id"]).strip(): (r.get("question") or "")
                for r in csv.DictReader(f)}


def _chunk_hits(chunks: list[dict], expected_docs: list[str],
                expected_paths: list[str]) -> list[str]:
    """Какие ожидаемые (doc[, path]) пары покрыты выдачей."""
    hits = []
    for doc in expected_docs:
        doc_chunks = [c for c in chunks if doc.lower() in c["doc_id"].lower()]
        if not doc_chunks:
            hits.append(f"{doc}:НЕТ")
            continue
        if not expected_paths:
            hits.append(f"{doc}:ок")
            continue
        for path in expected_paths:
            found = any(str(c.get("path", "")).startswith(path) for c in doc_chunks)
            hits.append(f"{doc} {path}:{'ок' if found else 'НЕТ'}")
    return hits


def mode_recall(top_n: int = 8) -> int:
    cfg = Config(data_dir=str(ROOT / "data"))
    chunks = load_chunks(cfg)
    bm25 = find_artifact(cfg, "bm25.index.npz")
    refs = json.loads(find_artifact(cfg, "refs.json").read_text(encoding="utf-8"))
    docs = json.loads(find_artifact(cfg, "docs.json").read_text(encoding="utf-8"))
    retriever = Retriever(chunks, bm25, refs)
    gold, questions = load_gold(), load_questions()

    total_pairs, hit_pairs = 0, 0
    for row in gold:
        qid = row["id"]
        raw = questions.get(qid, "")
        dialog = guard.parse_dialog(raw)
        queries = guard.heuristic_queries(dialog)
        standalone = guard.standalone_query(dialog)
        filters = temporal.search_filters(standalone, docs)
        found = retriever.search(queries, top_k=24, top_n=top_n,
                                 drop_doc_ids=filters.get("drop_doc_ids"),
                                 boost_doc_ids=filters.get("boost_doc_ids"))
        found = retriever.expand(found)
        hits = _chunk_hits(found, row["expected_docs"], row["expected_paths"])
        ok = [h for h in hits if h.endswith(":ок")]
        total_pairs += len(hits)
        hit_pairs += len(ok)
        status = "OK " if len(ok) == len(hits) else "MISS"
        print(f"[{status}] q{qid:>2} {queries[0][:60]!r} → {'; '.join(hits) or 'нет ожиданий'}")
    print(f"\nrecall@{top_n}: {hit_pairs}/{total_pairs} = "
          f"{(hit_pairs / total_pairs if total_pairs else 0):.2%}")
    return 0 if hit_pairs == total_pairs else 1


def _numbers(text: str) -> set[str]:
    flat = re.sub(r"\s+", "", text).replace(",", ".")
    return {n.lstrip("0") or "0" for n in re.findall(r"\d+(?:\.\d+)?", flat)
            if len(n) >= 2}


def mode_fact_check(submission: Path) -> int:
    gold_rows = {row["id"]: row for row in load_gold()}
    golden_path = ROOT / "data" / "demo_output.csv"
    golden = {}
    with open(golden_path, encoding="utf-8-sig", newline="") as f:
        for r in csv.DictReader(f):
            golden[str(r["id"]).strip()] = r.get("answer") or ""
    with open(submission, encoding="utf-8-sig", newline="") as f:
        ours = {str(r["id"]).strip(): (r.get("answer") or "")
                for r in csv.DictReader(f)}

    failures = 0
    for qid, row in sorted(gold_rows.items(), key=lambda kv: int(kv[0])):
        answer = ours.get(qid, "")
        problems = []
        if not answer.strip():
            problems.append("пустой ответ")
        if "\n" in answer:
            problems.append("перенос строки")
        if re.search(r"[*_#`]", answer):
            problems.append("маркдаун")
        if len(answer) > 10_000:
            problems.append("слишком длинный")
        golden_answer = golden.get(qid, "")
        if row["type"] == "refusal":
            if "нет документов" not in answer and "не могу" not in answer:
                problems.append("нет шаблона отказа")
        else:
            # число из эталонного факта должно быть в нашем ответе
            for fact in row["facts"]:
                fact_norm = re.sub(r"\s+", "", fact).replace(",", ".")
                if fact_norm and fact_norm not in re.sub(r"\s+", "", answer):
                    problems.append(f"факт «{fact}» отсутствует")
        if problems:
            failures += 1
            print(f"[FAIL] q{qid}: {'; '.join(problems)}")
        else:
            print(f"[OK  ] q{qid}: {answer[:90]}…")
    print(f"\nфакт-чек/стиль: {len(gold_rows) - failures}/{len(gold_rows)} ok")
    return 0 if failures == 0 else 1


def main() -> None:
    parser = argparse.ArgumentParser(description="Eval v2 (без LLM)")
    parser.add_argument("mode", choices=["recall", "facts"])
    parser.add_argument("--top-n", type=int, default=8)
    parser.add_argument("--submission", default="submission.csv")
    args = parser.parse_args()
    if args.mode == "recall":
        sys.exit(mode_recall(args.top_n))
    sys.exit(mode_fact_check(Path(args.submission)))


if __name__ == "__main__":
    main()
