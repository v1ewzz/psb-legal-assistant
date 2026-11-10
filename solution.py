# -*- coding: utf-8 -*-
"""Точка входа платформы: пакетный прогон (§16.1).

find_input → questions.csv (utf-8-sig, дедуп id, первый побеждает) →
контекст (артефакты индекса; если нет — пересборка из kb.zip, тайм-бокс 6 мин) →
каркас submission.csv сразу со всеми id → ThreadPoolExecutor(MAX_WORKERS) →
safe_answer (никогда не пусто; экстрактивный fallback при сбоях) →
периодическая запись каждые 30 с → финальная запись → os._exit(0).

Правила: HARD_DEADLINE = старт + 16 мин; логи краткие, в stderr, без текстов
вопросов и ключей (§5.3).
"""
from __future__ import annotations

import csv
import json
import os
import sys
import time
from pathlib import Path
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait

import config
import ingest
import pipeline
from config import find_input
from llm_client import LlmClient
from retrieval import Retriever, find_artifact, load_chunks

T0 = time.time()
HARD_DEADLINE = T0 + config.HARD_DEADLINE_MIN * 60
OUT = "submission.csv"
PERIODIC_S = 30.0


def write_submission(ids: list[str], answers: dict[str, str],
                     questions: dict[str, str], fallback_answer: str) -> None:
    """Атомарная запись CSV (§16.1): QUOTE_ALL, одна строка на ответ, обрезка."""
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
        w.writerow(["id", "answer"])
        for qid in ids:
            answer = (answers.get(qid) or "").strip() or fallback_answer
            w.writerow([qid, answer[:config.MAX_ANSWER_CHARS]])
    os.replace(tmp, OUT)


def safe_answer(qid: str, question: str, ctx: dict, deadline: float) -> str:
    """любой сбой → fallback, никогда не пусто (§16.1)."""
    try:
        answer = pipeline.answer_one(question, ctx, deadline)
        return answer or pipeline.grounded_refusal(question, ctx)
    except Exception as e:
        print(f"id={qid}: {type(e).__name__}", file=sys.stderr)
        try:
            return pipeline.extractive_fallback(question, ctx)
        except Exception:
            return pipeline.grounded_refusal(question, ctx)


def load_context() -> dict:
    """Артефакты → BM25-ретривер; если нет — пересборка из kb.zip (§8.3)."""
    cfg = config.from_env()
    chunks = load_chunks(cfg)
    if not chunks:
        kb = ingest.find_kb_zip()
        if kb is not None and (T0 + config.REBUILD_INDEX_TIMEBOX_S - time.time()) > 60:
            try:
                ingest.build_index(kb, Path("index"))
                chunks = load_chunks(cfg)
            except Exception as e:
                print(f"Пересборка индекса не удалась: {type(e).__name__}", file=sys.stderr)
    retriever = None
    if chunks:
        bm25_path = find_artifact(cfg, "bm25.index.npz")
        refs_path = find_artifact(cfg, "refs.json")
        if bm25_path is not None:
            refs = None
            if refs_path is not None:
                refs = json.loads(refs_path.read_text(encoding="utf-8"))
            retriever = Retriever(chunks, bm25_path, refs)
    client = LlmClient(cfg) if cfg.api_ready else None
    print(f"Контекст: чанков={len(chunks) if chunks else 0}; "
          f"bm25={'да' if retriever else 'нет'}; "
          f"llm={'да' if client else 'нет'}; "
          f"режим={cfg.llm_model}", file=sys.stderr)
    return {"cfg": cfg, "retriever": retriever, "client": client}


def main() -> None:
    path = find_input()
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.DictReader(f))
    ids, seen, questions = [], set(), {}
    for row in rows:
        qid = str(row.get("id") or "").strip()
        if qid and qid not in seen:
            seen.add(qid)
            ids.append(qid)
            questions[qid] = str(row.get("question") or "").strip()

    ctx = load_context()
    fallback_answer = pipeline.grounded_refusal("", ctx)
    write_submission(ids, {}, questions, fallback_answer)  # каркас сразу (§16.2)
    print(f"Вопросов: {len(ids)}", file=sys.stderr)

    answers: dict[str, str] = {}
    deadline = HARD_DEADLINE - 60  # минута резерва на финальную запись
    workers = ctx["cfg"].max_workers
    with ThreadPoolExecutor(max_workers=max(1, workers)) as ex:
        futures = {ex.submit(safe_answer, qid, questions[qid], ctx, deadline): qid
                   for qid in ids}
        pending = set(futures)
        last_write = time.time()
        while pending and time.time() < HARD_DEADLINE:
            done, pending = wait(pending, timeout=5, return_when=FIRST_COMPLETED)
            for fut in done:
                answers[futures[fut]] = fut.result()
            if time.time() - last_write > PERIODIC_S:
                write_submission(ids, answers, questions, fallback_answer)
                last_write = time.time()
                print(f"Промежуточная запись: {len(answers)}/{len(ids)}", file=sys.stderr)
    write_submission(ids, answers, questions, fallback_answer)
    ex.shutdown(wait=False, cancel_futures=True)
    os._exit(0)  # зависший вызов не удержит процесс (§16.2)


if __name__ == "__main__":
    main()
