# -*- coding: utf-8 -*-
"""Точка входа платформы: пакетный прогон questions.csv → submission.csv.

Гарантии:
- submission.csv ровно с колонками id,answer; все id из входа; ответы непустые;
  каркас пишется сразу, затем периодически и финально (атомарно);
- HARD_DEADLINE = старт + 16 мин (из 20); адаптивная деградация по оставшемуся времени;
- любой сбой на вопросе → экстрактивный fallback, прогон не падает;
- ключи/тексты вопросов не логируются; сводка времени по этапам — в stderr.
"""
from __future__ import annotations

import csv
import os
import sys
import time
from concurrent.futures import FIRST_COMPLETED, ThreadPoolExecutor, wait
from pathlib import Path

T0 = time.time()
sys.path.insert(0, str(Path(__file__).resolve().parent))

import config  # noqa: E402
import pipeline  # noqa: E402
from config import find_input  # noqa: E402
from runtime_utils import STATS, Budget  # noqa: E402

HARD_DEADLINE = T0 + config.HARD_DEADLINE_MIN * 60
OUT = "submission.csv"


def write_submission(ids: list[str], answers: dict[str, str], fallback: str) -> None:
    tmp = OUT + ".tmp"
    with open(tmp, "w", encoding="utf-8", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL, lineterminator="\n")
        w.writerow(["id", "answer"])
        for qid in ids:
            answer = " ".join((answers.get(qid) or "").split()) or fallback
            w.writerow([qid, answer[:config.MAX_ANSWER_CHARS]])
    os.replace(tmp, OUT)


def read_questions(path: Path) -> tuple[list[str], dict[str, str]]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        rows = list(csv.reader(f))
    if not rows:
        return [], {}
    header = [h.strip().lower() for h in rows[0]]
    id_col = header.index("id") if "id" in header else 0
    q_col = next((header.index(n) for n in ("question", "query", "text", "вопрос") if n in header),
                 1 if len(header) > 1 else 0)
    ids, questions = [], {}
    for row in rows[1:]:
        if len(row) <= max(id_col, 0):
            continue
        qid = str(row[id_col]).strip()
        if qid and qid not in questions:
            ids.append(qid)
            questions[qid] = str(row[q_col]).strip() if len(row) > q_col else ""
    return ids, questions


def safe_answer(qid: str, question: str, ctx: dict, deadline: float, budget: Budget) -> str:
    t = time.time()
    try:
        answer = pipeline.answer_one(question, ctx, deadline)
        return answer or pipeline.grounded_refusal(question, ctx)
    except Exception as e:  # noqa: BLE001
        print(f"id={qid}: {type(e).__name__}", file=sys.stderr)
        STATS.count("question_exception")
        try:
            return pipeline.extractive_fallback(question, ctx)
        except Exception:  # noqa: BLE001
            return pipeline.grounded_refusal(question, ctx)
    finally:
        budget.mark_done()
        STATS.add("question.total", time.time() - t)


def main() -> None:
    path = find_input()
    ids, questions = read_questions(path)
    cfg = config.from_env()
    try:
        ctx = pipeline.build_context(cfg, t0=T0)
    except Exception as e:  # noqa: BLE001
        print(f"Контекст не собран ({type(e).__name__}) — режим отказов", file=sys.stderr)
        ctx = {"cfg": cfg, "client": None, "retriever": None, "cache": pipeline.LRU(8),
               "budget": None, "today": None}
    fallback = pipeline.grounded_refusal("", ctx)
    write_submission(ids, {}, fallback)
    print(f"Вопросов: {len(ids)}; старт за {time.time() - T0:.1f} с", file=sys.stderr)

    deadline = HARD_DEADLINE - config.FINAL_WRITE_RESERVE_S
    workers = max(1, cfg.max_workers)
    budget = Budget(deadline, len(ids), workers)
    ctx["budget"] = budget
    answers: dict[str, str] = {}
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        futures = {ex.submit(safe_answer, qid, questions[qid], ctx, deadline, budget): qid for qid in ids}
        pending = set(futures)
        last_write = time.time()
        while pending and time.time() < HARD_DEADLINE:
            done, pending = wait(pending, timeout=5, return_when=FIRST_COMPLETED)
            for fut in done:
                try:
                    answers[futures[fut]] = fut.result()
                except Exception:  # noqa: BLE001
                    pass
            if time.time() - last_write > config.PERIODIC_WRITE_S:
                write_submission(ids, answers, fallback)
                last_write = time.time()
                print(f"Промежуточная запись: {len(answers)}/{len(ids)}", file=sys.stderr)
        for fut in pending:  # не успели к дедлайну → экстрактивный ответ
            qid = futures[fut]
            try:
                answers[qid] = pipeline.extractive_fallback(questions[qid], ctx)
            except Exception:  # noqa: BLE001
                pass
        write_submission(ids, answers, fallback)
    finally:
        print(f"Готово: {len(answers)}/{len(ids)} за {time.time() - T0:.1f} с", file=sys.stderr)
        print(STATS.summary(), file=sys.stderr)
        client = ctx.get("api_client")
        if client is not None:
            print(f"API: {client.stats}", file=sys.stderr)
        sys.stderr.flush()
        ex.shutdown(wait=False, cancel_futures=True)
        os._exit(0)


if __name__ == "__main__":
    main()
