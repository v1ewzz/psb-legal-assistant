# -*- coding: utf-8 -*-
"""Офлайн-прокси формулы судьи: детерминированные фичи -> калибровка баллов.

Работает БЕЗ API: использует сохранённые сабмиты eval/history/*.submission.csv
и эталон data/demo_output.csv. Цель — понять, какая комбинация фич объясняет
6 известных платформенных баллов (шкала 0..1) и ранжировать кандидатов.

Фичи по вопросу (real_eval.score_answer + доп.):
- score      hits/total по применимым проверкам (verdict/numbers/units/refs/refusal/words)
- extra_refs / extra_numbers  «выдумка»-прокси: реквизиты/числа в ответе, которых нет в эталоне
- over_refusal  отказ, когда эталон содержательный (жёсткий вред)
- words_ratio  длина ответа / длина эталона

Агрегаты по сабмиту: mean(score), mean(extra_refs+extra_numbers), over_refusal_rate,
WinRate-прокси против бейзлайна (детерминированный, по score vs golden).

Формулы: S = 0.7*M + 0.3*W и линейная регрессия на [M, halluc, over_refusal, W].
"""
from __future__ import annotations

import csv
import re
import sys
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parent.parent.parent
sys.path.insert(0, str(ROOT))

from eval.real_eval import score_answer  # noqa: E402
import pipeline  # noqa: E402  (нужен _number_keys внутри score_answer)

HIST = ROOT / "eval" / "history"

PLATFORM_SCORES = {
    "n1": 0.274,
    "rules": 0.208,
    "v2": 0.23688,
    "v21": 0.27615,
    "v22": 0.25032,
    "dense": 0.26199,
}

STATES = ["n1", "rules", "v2", "v21", "v22", "dense", "head",
          "cfc9cad", "cfc9cad_sibling2"]


def load_csv(path: Path, key: str, val: str) -> dict[str, str]:
    out = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get(key) and row.get(val):
                out[row[key].strip()] = row[val].strip()
    return out


def norm(text: str) -> str:
    return re.sub(r"\s+", " ", text).strip()


REF_RE = re.compile(r"п\.\s*(\d+(?:\.\d+)+)")

def extras(answer: str, gold: str) -> tuple[int, int]:
    """(extra_refs, extra_numbers) — прокси «выдумки»."""
    a, g = norm(answer), norm(gold)
    a_refs, g_refs = set(REF_RE.findall(a)), set(REF_RE.findall(g))
    a_nums = pipeline._number_keys(a)
    g_nums = pipeline._number_keys(g)
    return len(a_refs - g_refs), len(a_nums - g_nums)


def per_question(answers: dict[str, str], gold: dict[str, str],
                 questions: dict[str, str]) -> dict[str, dict]:
    out = {}
    for qid, q in questions.items():
        a, g = answers.get(qid, ""), gold.get(qid, "")
        s = score_answer(a, g, q)
        er, en = extras(a, g)
        g_refusal = bool(re.search(r"нет документов|не могу|не удалось", norm(g), re.I))
        a_refusal = bool(re.search(r"нет документов|не могу|не удалось", norm(a), re.I))
        g_words = max(len(norm(g).split()), 1)
        out[qid] = {
            "score": s["score"],
            "extra_refs": er,
            "extra_numbers": en,
            "over_refusal": int(a_refusal and not g_refusal),
            "words_ratio": (s["words"] / g_words) if not a_refusal else None,
        }
    return out


def aggregates(pq: dict[str, dict]) -> dict:
    n = len(pq)
    scores = [v["score"] for v in pq.values()]
    halluc = [(v["extra_refs"] > 0) + (v["extra_numbers"] > 0) for v in pq.values()]
    return {
        "M": sum(scores) / n,
        "halluc_rate": sum(1 for h in halluc if h) / n,
        "extra_refs_rate": sum(1 for v in pq.values() if v["extra_refs"] > 0) / n,
        "extra_numbers_rate": sum(1 for v in pq.values() if v["extra_numbers"] > 0) / n,
        "over_refusal_rate": sum(v["over_refusal"] for v in pq.values()) / n,
        "words_ratio_med": float(np.median([v["words_ratio"] for v in pq.values()
                                            if v["words_ratio"]] or [0])),
    }


def winrate_proxy(pq_a: dict, pq_b: dict) -> float:
    """Детерминированный WinRate-прокси: A против B по score-vs-эталон."""
    wins = losses = 0
    for qid in pq_a:
        sa, sb = pq_a[qid]["score"], pq_b[qid]["score"]
        if sa > sb + 1e-9:
            wins += 1
        elif sb > sa + 1e-9:
            losses += 1
    return wins / (wins + losses) if wins + losses else 0.5


def main() -> None:
    data = ROOT / "data"
    questions = load_csv(data / "demo_questions.csv", "id", "question")
    gold = load_csv(data / "demo_output.csv", "id", "answer")
    base = load_csv(HIST / "baseline.submission.csv", "id", "answer")
    pq_base = per_question(base, gold, questions)
    agg_base = aggregates(pq_base)

    rows = []
    for name in STATES:
        f = HIST / f"{name}.submission.csv"
        if not f.exists():
            continue
        pq = per_question(load_csv(f, "id", "answer"), gold, questions)
        agg = aggregates(pq)
        agg["W_proxy"] = winrate_proxy(pq, pq_base)
        agg["W_baseline"] = winrate_proxy(pq_base, pq)  # симметрия/санити
        rows.append((name, agg, pq))

    print("Бейзлайн организаторов (детерминированно vs эталон): "
          f"M={agg_base['M']:.3f}")
    print()
    hdr = (f"{'сабмит':20} {'платф':>7} {'M':>6} {'W~':>5} {'hall':>5} "
           f"{'e.ref':>5} {'e.num':>5} {'отказ↑':>6} {'слова':>5}")
    print(hdr)
    for name, agg, _ in rows:
        plat = PLATFORM_SCORES.get(name)
        plat_s = f"{plat:.3f}" if plat is not None else "  ?"
        print(f"{name:20} {plat_s:>7} {agg['M']:>6.3f} {agg['W_proxy']:>5.2f} "
              f"{agg['halluc_rate']:>5.2f} {agg['extra_refs_rate']:>5.2f} "
              f"{agg['extra_numbers_rate']:>5.2f} {agg['over_refusal_rate']:>6.2f} "
              f"{agg['words_ratio_med']:>5.1f}")

    known = [(name, agg) for name, agg, _ in rows if name in PLATFORM_SCORES]
    if len(known) >= 4:
        print("\n=== Формулы (калибровка по известным баллам) ===")
        y = np.array([PLATFORM_SCORES[n] for n, _ in known])

        # 1) S = 0.7*M + 0.3*W (спека, без подгонки)
        S1 = np.array([0.7 * agg["M"] + 0.3 * agg["W_proxy"] for _, agg in known])
        # 2) линейная регрессия: M, halluc, over_refusal, W
        X = np.array([[agg["M"], agg["halluc_rate"], agg["over_refusal_rate"],
                       agg["W_proxy"]] for _, agg in known])
        coef, res, *_ = np.linalg.lstsq(X, y, rcond=None)
        S2 = X @ coef
        # 3) регрессия: M, e.ref, e.num, over_refusal, W
        X3 = np.array([[agg["M"], agg["extra_refs_rate"], agg["extra_numbers_rate"],
                        agg["over_refusal_rate"], agg["W_proxy"]] for _, agg in known])
        coef3, *_ = np.linalg.lstsq(X3, y, rcond=None)
        S3 = X3 @ coef3

        def report(label, S, coefs=None):
            err = S - y
            r2 = 1 - (err @ err) / ((y - y.mean()) @ (y - y.mean()))
            print(f"\n{label}: R2={r2:.3f}  макс|err|={np.abs(err).max():.4f}")
            if coefs is not None:
                print("  коэф:", " ".join(f"{c:+.3f}" for c in coefs))
            for (n, _), s, e in zip(known, S, err):
                print(f"  {n:8} платф={PLATFORM_SCORES[n]:.4f} модель={s:.4f} err={e:+.4f}")

        report("① Спека: S=0.7M+0.3W", S1)
        report("② Регрессия [M, halluc, отказ↑, W]", S2, coef)
        report("③ Регрессия [M, e.ref, e.num, отказ↑, W]", S3, coef3)

        # прогноз кандидатов лучшей моделью
        print("\n=== Прогноз кандидатов (модели ①②③) ===")
        for name, agg, _ in rows:
            if name in PLATFORM_SCORES:
                continue
            s1 = 0.7 * agg["M"] + 0.3 * agg["W_proxy"]
            x2 = np.array([agg["M"], agg["halluc_rate"], agg["over_refusal_rate"],
                           agg["W_proxy"]])
            x3 = np.array([agg["M"], agg["extra_refs_rate"], agg["extra_numbers_rate"],
                           agg["over_refusal_rate"], agg["W_proxy"]])
            print(f"  {name:20} ①={s1:.4f}  ②={x2 @ coef:+.4f}  ③={x3 @ coef3:+.4f}")


if __name__ == "__main__":
    main()
