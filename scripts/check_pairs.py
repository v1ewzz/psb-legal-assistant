# -*- coding: utf-8 -*-
"""Офлайн-проверка рычага №2 (без LLM): проходят ли эталоны demo-18 и реплеи
реальных сабмитов через pair-проверку _unit_pair_problem.

Retrieval и evidence — ровно как в answer_one (lean-индекс, sibling_fill ON).
Падение эталона = потенциальный ложный топит вживую; падение реплея n1/v21
(реальные ответы, 0.274/0.276 на платформе) = сигнал ослабить проверку."""
import csv
import json
import sys
from pathlib import Path

LEAN = Path(__file__).resolve().parent.parent
MAIN = Path(r"D:\hackaton_PSB")
sys.path.insert(0, str(LEAN))

from config import from_env
from pipeline import _evidence_bundle, _unit_pair_problem, _verify_lite
from retrieval import Retriever, find_artifact, load_chunks


def load_csv(path: Path, key: str, val: str) -> dict[str, str]:
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r[key].strip(): (r[val] or "").strip() for r in csv.DictReader(f)}


cfg = from_env()
chunks = load_chunks(cfg)
retr = Retriever(chunks, find_artifact(cfg, "bm25.index.npz"),
                 json.loads(find_artifact(cfg, "refs.json").read_text(encoding="utf-8")),
                 sibling_fill=True)
questions = load_csv(MAIN / "data" / "demo_questions.csv", "id", "question")
print(f"чанков: {len(chunks)}, вопросов: {len(questions)}")


def check(name: str, answers: dict[str, str]) -> int:
    bad = []
    n = 0
    for qid, ans in answers.items():
        q = questions.get(qid)
        if not q or not ans:
            continue
        n += 1
        frags = _evidence_bundle(retr.search(q, top_k=cfg.top_k))[2]
        problem = _unit_pair_problem(ans, frags)
        if problem:
            bad.append((qid, problem))
    print(f"{name}: {n - len(bad)}/{n} чисто")
    for qid, p in bad:
        print(f"  {qid}: {p}")
    return len(bad)


total = 0
total += check("GOLDEN demo_output", load_csv(MAIN / "data" / "demo_output.csv", "id", "answer"))
for state in ("n1", "v21", "head", "v22"):
    p = MAIN / "eval" / "history" / f"{state}.submission.csv"
    if p.exists():
        total += check(state, load_csv(p, "id", "answer"))
print(f"\nИТОГО проблемных ответов: {total}")
