# -*- coding: utf-8 -*-
"""Real-LLM eval демо-18 для lean-ветки (конвейер v1 / cfc9cad).

Отличия от HEAD-раннера (eval/real_eval.py в основном репо):
- импорты из ЭТОГО worktree (lean-pipeline: без _number_keys/parse_dialog);
- .env и демо-данные берутся из основного репо (PSB_MAIN, по умолчанию
  D:\\hackaton_PSB); индекс — родной lean (worktree/index, 255 чанков);
- score-логика самодостаточна (_number_keys скопирован из HEAD-pipeline).

Запуск:  python eval/real_eval.py            (ключи из .env основного репо)
         python eval/real_eval.py --ids 1,5  (подмножество)
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
MAIN = Path(os.environ.get("PSB_MAIN", str(ROOT)))


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


from quality import score_answer as offline_score_answer

VERDICT_RE = re.compile(r"^\s*(Да|Нет)\b", re.I)
REF_RE = re.compile(r"п\.\s*(\d+(?:\.\d+)+)")
REFUSAL_MARK = re.compile(r"нет документов|не могу|не удалось", re.I)

NUM_RE = re.compile(r"\d+(?:[.,]\d+)?")
MILLION_RE = re.compile(r"(\d+(?:[.,]\d+)?)\s*(млн|млрд|тыс\.?)", re.I)
MILLION_MULT = {"млн": 10 ** 6, "млрд": 10 ** 9, "тыс": 10 ** 3}


def _number_keys(text: str) -> set[str]:
    """Копия HEAD-pipeline (§13.1.3 v2.1): точные ключи чисел текста."""
    keys: set[str] = set()
    flat = (text or "").replace("\u00a0", " ")
    for raw in NUM_RE.findall(flat):
        if len(raw) >= 2:
            keys.add(raw.replace(",", ".").lstrip("0") or "0")
    for m in re.finditer(r"\d{1,3}(?:[ ]\d{3})+", flat):
        keys.add(m.group().replace(" ", "").lstrip("0") or "0")
    for m in MILLION_RE.finditer(flat):
        try:
            value = float(m.group(1).replace(",", ".")) * MILLION_MULT[
                m.group(2).lower().rstrip(".")]
            keys.add(str(int(value)))
        except (ValueError, KeyError):
            pass
    return keys


def load_csv(path: Path, key: str, val: str) -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get(key) and row.get(val):
                out[row[key].strip()] = row[val].strip()
    return out


def score_answer(answer: str, gold: str, question: str) -> dict:
    """Import-safe deterministic diagnostics; not the organiser's score."""
    return offline_score_answer(answer, gold, question)


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--ids", default="")
    args = ap.parse_args()

    # Only the explicitly invoked live entrypoint reads credentials or assistant modules.
    _env = MAIN / ".env"
    load_dotenv(_env if _env.is_file() else ROOT / ".env")
    os.environ.setdefault("DATA_DIR", str(MAIN / "data"))
    import config
    import solution
    import pipeline

    q_path = MAIN / "data" / "demo_questions.csv"
    g_path = MAIN / "data" / "demo_output.csv"
    questions = load_csv(q_path, "id", "question")
    gold = load_csv(g_path, "id", "answer")
    ids = [s for s in args.ids.split(",") if s] or list(questions)

    cfg = config.from_env()
    ctx = solution.load_context()
    client = ctx.get("client")
    if client is None:
        print("Нет API-клиента: заполни .env основного репо "
              "(LLM_BASE_URL/LLM_API_KEY/LLM_MODEL)", file=sys.stderr)
        return
    print(f"Флаги: top_k={cfg.top_k} max_tokens={cfg.max_tokens} "
          f"max_input_chars={cfg.max_input_chars} "
          f"citation_lint={cfg.citation_lint}")

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
    print(f"\nДИАГНОСТИКА avg={avg:.3f} по {len(results)} вопросам; не оценка платформы")
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
