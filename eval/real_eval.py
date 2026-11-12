# -*- coding: utf-8 -*-
"""Real-LLM eval демо-18 против эталонов (нужен API-ключ в .env).

Прогоняет data/demo_questions.csv через ПОЛНЫЙ конвейер answer_one (с реранком,
dense-флагом из env) и детерминированно сравнивает ответы с эталонами
(data/demo_output.csv):

- verdict   — первое слово (Да/Нет) совпало с эталоном;
- numbers   — все числовые ключи эталона есть в ответе (по ключам _number_keys);
- units     — пары число+период совпадают (unit-ловушка в обе стороны);
- refs      — реквизиты «п. X» эталона воспроизведены;
- refusal   — если эталон — отказ, ответ тоже отказ с темой;
- words     — длина ответа в коридоре эталона (±50%).

Запуск:  python eval/real_eval.py            (ключи из .env / окружения)
         python eval/real_eval.py --ids 1,5,6   (подмножество)
"""
from __future__ import annotations

import argparse
import csv
import os
import re
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_dotenv(path: Path) -> None:
    """KEY=VALUE в os.environ (только отсутствующие; кавычки срезаются)."""
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


load_dotenv(ROOT / ".env")
os.environ.setdefault("DATA_DIR", "data")

import config
import pipeline
import solution
from llm_client import LlmClient
from retrieval import Retriever, find_artifact, load_chunks

VERDICT_RE = re.compile(r"^\s*(Да|Нет)\b", re.I)
REF_RE = re.compile(r"п\.\s*(\d+(?:\.\d+)+)")
REFUSAL_MARK = re.compile(r"нет документов|не могу|не удалось", re.I)


def load_csv(path: Path, key: str, val: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get(key) and row.get(val):
                out[row[key].strip()] = row[val].strip()
    return out


def score_answer(answer: str, gold: str, question: str) -> dict:
    a_norm = re.sub(r"\s+", " ", answer).strip()
    g_norm = re.sub(r"\s+", " ", gold).strip()

    g_verdict = VERDICT_RE.match(g_norm)
    a_verdict = VERDICT_RE.match(a_norm)
    verdict_ok = None
    if g_verdict:
        verdict_ok = bool(a_verdict and
                          a_verdict.group(1).lower() == g_verdict.group(1).lower())

    g_keys = pipeline._number_keys(g_norm)
    a_keys = pipeline._number_keys(a_norm)
    missing = sorted(k for k in g_keys if k not in a_keys)
    numbers_ok = None if not g_keys else not missing

    def units_of(text: str) -> set[str]:
        return {m.group(2).lower()[:4] for m in re.finditer(
            r"(\d+(?:[.,]\d+)?)[^\n.]{0,20}?(?:в |за |)?"
            r"(?<![а-яёА-ЯЁ])(недел\w+|месяц\w+|год\w+|дн\w*|календарн\w+|"
            r"рабоч\w+|час(?!т)\w*)\b", text, re.I)}

    g_units, a_units = units_of(g_norm), units_of(a_norm)
    units_ok = None if not g_units else g_units <= a_units

    g_refs = set(REF_RE.findall(g_norm))
    a_refs = set(REF_RE.findall(a_norm))
    refs_ok = None if not g_refs else g_refs <= a_refs

    g_refusal = bool(REFUSAL_MARK.search(g_norm))
    a_refusal = bool(REFUSAL_MARK.search(a_norm))
    refusal_ok = None if not g_refusal else a_refusal

    g_words, a_words = len(g_norm.split()), len(a_norm.split())
    words_ok = a_words <= max(int(g_words * 1.5), 61)

    hits = sum(1 for v in (verdict_ok, numbers_ok, units_ok, refs_ok,
                           refusal_ok, words_ok) if v is True)
    total = sum(1 for v in (verdict_ok, numbers_ok, units_ok, refs_ok,
                            refusal_ok, words_ok) if v is not None)
    return {"verdict": verdict_ok, "numbers": numbers_ok,
            "missing": missing, "units": units_ok, "refs": refs_ok,
            "refusal": refusal_ok, "words": a_words,
            "score": hits / total if total else 0.0}


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    ap.add_argument("--no-dense", action="store_true")
    args = ap.parse_args()

    questions = load_csv(ROOT / "data/demo_questions.csv", "id", "question")
    gold = load_csv(ROOT / "data/demo_output.csv", "id", "answer")
    ids = [s for s in args.ids.split(",") if s] or list(questions)

    ctx = solution.load_context()
    if args.no_dense and ctx.get("retriever") is not None:
        ctx["retriever"].dense = None
    client = ctx.get("client")
    if client is None:
        print("Нет API-клиента: заполни .env (LLM_BASE_URL/LLM_API_KEY/LLM_MODEL)",
              file=sys.stderr)
        return

    deadline = time.time() + 15 * 60
    results = []
    for qid in ids:
        q = questions[qid]
        t0 = time.time()
        ans = pipeline.answer_one(q, ctx, deadline)
        dt = round(time.time() - t0, 1)
        s = score_answer(ans, gold.get(qid, ""), q)
        results.append((qid, s, ans, dt, gold.get(qid, "")))
        flags = " ".join(
            f"{k}={'+' if v else '✗'}" for k, v in s.items()
            if k in ("verdict", "numbers", "units", "refs", "refusal") and v is not None)
        print(f"Q{qid}: {s['score']:.2f} {flags} слова={s['words']} {dt}s "
              f"{('не хватает: ' + ','.join(s['missing'])) if s.get('missing') else ''}")

    avg = sum(r[1]["score"] for r in results) / max(len(results), 1)
    print(f"\nИТОГО avg={avg:.3f} по {len(results)} вопросам")
    out = ROOT / "build" / "real_eval_answers.txt"
    out.parent.mkdir(exist_ok=True)
    with open(out, "w", encoding="utf-8") as f:
        for qid, s, ans, dt, g in results:
            f.write(f"=== Q{qid} (score {s['score']:.2f}, {dt}s)\n"
                    f"ВОПРОС: {questions[qid][:200]}\n"
                    f"ЭТАЛОН: {g}\nОТВЕТ:  {ans}\n\n")
    print(f"Ответы сохранены: {out}")


if __name__ == "__main__":
    main()
