# -*- coding: utf-8 -*-
"""Разбор флагов pair-проверки: падает ли там же старая глобальная проверка
чисел (_verify_lite) и что реально лежит в evidence."""
import csv
import json
import sys
from pathlib import Path

LEAN = Path(__file__).resolve().parent.parent
MAIN = LEAN  # материалы demo-18 лежат в этом же репозитории
sys.path.insert(0, str(LEAN))

from config import from_env
from pipeline import _evidence_bundle, _unit_pair_problem, _verify_lite, NUM_RE
from retrieval import Retriever, find_artifact, load_chunks


def load_csv(path, key, val):
    with open(path, encoding="utf-8-sig", newline="") as f:
        return {r[key].strip(): (r[val] or "").strip() for r in csv.DictReader(f)}


cfg = from_env()
chunks = load_chunks(cfg)
retr = Retriever(chunks, find_artifact(cfg, "bm25.index.npz"),
                 json.loads(find_artifact(cfg, "refs.json").read_text(encoding="utf-8")),
                 sibling_fill=True)
questions = load_csv(MAIN / "data" / "demo_questions.csv", "id", "question")
goldens = load_csv(MAIN / "data" / "demo_output.csv", "id", "answer")
v21 = load_csv(MAIN / "eval" / "history" / "v21.submission.csv", "id", "answer")

for qid in sys.argv[1:]:
    q = questions[qid]
    frags = _evidence_bundle(retr.search(q, top_k=cfg.top_k))[2]
    joined = "".join(frags)
    for name, ans in (("GOLDEN", goldens.get(qid, "")), ("v21", v21.get(qid, ""))):
        if not ans:
            continue
        pair = _unit_pair_problem(ans, frags)
        glob = _verify_lite(ans, joined)
        print(f"\n=== Q{qid} [{name}] pair={'OK' if pair is None else pair}")
        print(f"    global _verify_lite: {'OK' if glob is None else glob}")
        print(f"    ответ: {ans[:220]}")
        nums = sorted({n for n in NUM_RE.findall(ans) if len(n) >= 2})
        for num in nums:
            for frag in frags:
                if num.replace(",", ".") in frag.replace(",", ".").replace(" ", "") or num in frag:
                    idx = frag.find(num)
                    print(f"    ev[{num}]: …{frag[max(0, idx - 60):idx + 80]}…")
                    break
            else:
                print(f"    ev[{num}]: НЕ НАЙДЕНО во фрагментах")
