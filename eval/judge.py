# -*- coding: utf-8 -*-
"""Воссоздание формулы платформенного судьи (спека §1.2) на живом LLM.

Протокол судьи: по каждому вопросу два сравнения — с эталоном (golden) и с
бейзлайном организаторов, в двух раундах (A/B перестановка), шкала раунда
1.00/0.75/0.50/0.25/0.00, s = (r1+r2)/2; выдумка обнуляет вопрос.
Score = 0.7·MatchRate + 0.3·WinRate; WinRate = W/(W+L), ничьи исключены.

Два варианта агрегации (калибруются по 6 известным баллам платформы):
- mode M: MatchRate = mean(s_match), т.е. только сравнение с эталоном;
- mode S: s_i = (s_match_i + s_pair_i)/2, т.е. оба сравнения усредняются.

Бейзлайн (по фактуре из §1.2): пересечение слов → топ-4 документа →
реранк начальных фрагментов (≤1200 симв. от начала файла) → топ-2 →
один LLM-вызов. Без гейтов/диалог-парсинга/temporal.

CLI:  python eval/judge.py baseline
      python eval/judge.py score FILE.csv [FILE.csv ...]
"""
from __future__ import annotations

import csv
import json
import os
import re
import sys
import time
from pathlib import Path
from concurrent.futures import ThreadPoolExecutor

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

# --- .env (только отсутствующие) ---
for line in (ROOT / ".env").read_text(encoding="utf-8-sig").splitlines():
    line = line.strip()
    if line and not line.startswith("#") and "=" in line:
        k, v = line.split("=", 1)
        os.environ.setdefault(k.strip(), v.strip().strip('"'))
os.environ.setdefault("DATA_DIR", "data")

import config as _cfg
import ingest
from llm_client import LlmClient, JsonParseError
from retrieval import Retriever, find_artifact, load_chunks  # noqa: F401

HISTORY = ROOT / "eval" / "history"

# Известные баллы платформы: имя сабмита → score (шкала 0..1).
# n1/rules/v2/v21 — подтверждённые; v22/dense — атрибуция по коммит-сообщениям.
PLATFORM_SCORES = {
    "n1": 0.274,
    "rules": 0.208,
    "v2": 0.23688,
    "v21": 0.27615,
    "v22": 0.25032,
    "dense": 0.26199,
}

# ---------------------------------------------------------------- базовый IO

def load_csv(path: Path, key: str = "id", val: str = "answer") -> dict[str, str]:
    out: dict[str, str] = {}
    with open(path, encoding="utf-8-sig", newline="") as f:
        for row in csv.DictReader(f):
            if row.get(key) and row.get(val):
                out[row[key].strip()] = row[val].strip()
    return out


def load_questions(path: Path) -> dict[str, str]:
    return load_csv(path, "id", "question")


# ---------------------------------------------------------------- бейзлайн

_STOP = set("какой какая какие каков сколько за для по при от до из-за это и или в на "
            "с со о об у к не ли то есть был была были быть наш нашему мы вас вам".split())

def _stem(w: str) -> str:
    """Грубый русский стемм для пересечения слов (как у бейзлайна)."""
    for suf in ("ами", "ями", "ого", "его", "ому", "ему", "ыми", "ими", "ая", "ое",
                "ые", "ий", "ый", "ой", "ая", "ов", "ев", "ам", "ям", "ах", "ях",
                "ть", "ла", "ло", "ли", "ей", "ой", "ом", "ём", "ы", "и", "а", "я",
                "у", "ю", "е", "о"):
        if len(w) > 4 and w.endswith(suf):
            return w[: -len(suf)]
    return w


def build_baseline(llm: LlmClient, questions: dict[str, str], kb_zip: Path,
                   out_csv: Path) -> dict[str, str]:
    """Бейзлайн организаторов на заданных вопросах (с кэшем в out_csv)."""
    if out_csv.exists():
        return load_csv(out_csv)

    docs = ingest.load_documents(kb_zip)          # [(file_name, text)]
    doc_texts = [re.sub(r"\s+", " ", t).strip() for _, t in docs]
    doc_stems = [{_stem(w) for w in re.findall(r"[а-яёa-z0-9]{3,}", t.lower())}
                 for t in doc_texts]
    frag_lens = [1200, 1200]

    def one(qid: str, q: str) -> str:
        q_stems = {_stem(w) for w in re.findall(r"[а-яёa-z0-9]{3,}", q.lower())
                   if w not in _STOP}
        scored = sorted(
            range(len(docs)),
            key=lambda i: (-len(q_stems & doc_stems[i]), i),
        )[:4]
        frags = [doc_texts[i][: frag_lens[min(k, len(frag_lens) - 1)]]
                 for k, i in enumerate(scored)]
        # реранк начальных фрагментов, топ-2
        try:
            rr = llm.rerank(q, frags, top_n=2)
            order = [r["index"] for r in rr]
        except Exception:
            order = [0, 1]
        top = [frags[i] for i in order[:2]]
        ctx = "\n\n".join(f"[Фрагмент {k+1}] {t}" for k, t in enumerate(top))
        ans = llm.chat(
            [
                {"role": "system",
                 "content": "Ты — ассистент по внутренним документам компании. "
                            "Отвечай на русском, кратко, только на основе "
                            "переданных фрагментов. Если ответа во фрагментах "
                            "нет — так и скажи."},
                {"role": "user",
                 "content": f"Вопрос: {q}\n\nФрагменты:\n{ctx}"},
            ],
            max_tokens=1000, temperature=0.0,
        ).strip()
        return re.sub(r"\s+", " ", ans)

    out: dict[str, str] = {}
    with ThreadPoolExecutor(max_workers=4) as ex:
        futs = {ex.submit(one, qid, q): qid for qid, q in questions.items()}
        for fut in futs:
            qid = futs[fut]
            try:
                out[qid] = fut.result()
            except Exception as e:                       # noqa: BLE001
                out[qid] = f"Ошибка бейзлайна: {e}"
                print(f"baseline Q{qid}: {e}", file=sys.stderr)

    with open(out_csv, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f, quoting=csv.QUOTE_ALL)
        w.writerow(["id", "answer"])
        for qid in sorted(out, key=lambda x: int(x) if x.isdigit() else 10**9):
            w.writerow([qid, out[qid]])
    return out

# ---------------------------------------------------------------- судья

MATCH_SYSTEM = """Ты — автоматический судья системы ответов по документам.
Тебе даны: ВОПРОС, ЭТАЛОН (правильный ответ) и ОТВЕТ (ответ испытуемой системы).
Оцени ОТВЕТ по соответствию ЭТАЛОНУ по шкале:
- 1.00 — по смыслу и фактам совпадает с эталоном: все ключевые факты эталона названы, числа и реквизиты совпадают; формулировки могут отличаться;
- 0.75 — основные факты верны, но есть незначительные пропуски или лишние уточнения, не меняющие суть ответа;
- 0.50 — частично: прямой ответ на вопрос дан верно, но существенно неполный (например, назван только один из двух фактов), либо обоснование или ссылки слабые;
- 0.25 — лишь косвенно относится к делу: есть отдельные верные детали, но прямого ответа на вопрос нет, либо ответ очень неполный;
- 0.00 — ответ неверный, отвечает не на тот вопрос, противоречит эталону, либо это отказ, тогда как эталон содержит содержательный ответ.
ЖЁСТКОЕ ПРАВИЛО: выдуманный факт — конкретное число, дата, реквизит документа или норма, которых нет в эталоне и которые не даны в самом вопросе, — полностью обнуляет оценку: ставь 0.00 и hallucination=true.
Если ЭТАЛОН — обоснованный отказ («в базе нет данных о X»), то корректный отказ испытуемой системы по той же теме X = 1.00; отказ по другой теме или содержательный ответ = 0.00.
Стиль и длина не важны, только фактическое соответствие.
Верни СТРОГО один JSON-объект без пояснений: {"score": 0.0, "hallucination": false, "reason": "кратко"}"""

PAIR_SYSTEM = """Ты — автоматический судья. Тебе даны ВОПРОС, ЭТАЛОН (правильный ответ, для справки) и два ответа на вопрос: A и B.
Оцени, какой из двух ответов лучше отвечает на вопрос, используя эталон как опору для проверки фактов. Критерии: правильность фактов относительно эталона, полнота ответа на вопрос, корректные реквизиты ссылок, честный отказ, если данных для ответа нет.
Шкала с точки зрения ответа A:
- 1.00 — A явно лучше B;
- 0.75 — A лучше B;
- 0.50 — примерно одинаково;
- 0.25 — B лучше A;
- 0.00 — B явно лучше A.
ЖЁСТКОЕ ПРАВИЛО: выдуманный факт — конкретное число, дата, реквизит или норма, которых НЕТ ни в эталоне, ни в самом вопросе, ни в другом ответе, — обнуляет оценку такого ответа: если выдумки в A — ставь 0.00; если в B — ставь 1.00. Если эталон — отказ, а один из ответов содержит содержательный ответ с фактами не из эталона — это тоже выдумка.
Верни СТРОГО один JSON-объект без пояснений: {"score": 0.0, "reason": "кратко"}"""

_SCALE = (0.0, 0.25, 0.5, 0.75, 1.0)


def _snap(x) -> float:
    try:
        x = float(x)
    except (TypeError, ValueError):
        return 0.5
    return min(_SCALE, key=lambda s: abs(s - x))


def judge_match(llm: LlmClient, q: str, gold: str, cand: str, swap: bool) -> dict:
    blocks = [
        f"ЭТАЛОН:\n{gold}",
        f"ОТВЕТ ИСПЫТУЕМОЙ СИСТЕМЫ:\n{cand}",
    ]
    if swap:
        blocks.reverse()
    user = f"ВОПРОС:\n{q}\n\n" + "\n\n".join(blocks)
    for attempt in range(2):
        try:
            js = llm.chat_json(
                [{"role": "system", "content": MATCH_SYSTEM},
                 {"role": "user", "content": user}],
                max_tokens=1000, temperature=0.0,
            )
            return {"score": _snap(js.get("score")),
                    "hallucination": bool(js.get("hallucination")),
                    "reason": str(js.get("reason", ""))[:200]}
        except JsonParseError:
            if attempt == 1:
                return {"score": 0.5, "hallucination": False,
                        "reason": "judge parse error"}
            user = user + "\n\n(Верни только JSON.)"
        except Exception as e:                            # noqa: BLE001
            return {"score": 0.5, "hallucination": False, "error": True,
                    "reason": f"ERROR {type(e).__name__}: {str(e)[:140]}"}
    return {"score": 0.5, "hallucination": False, "reason": "?"}


def judge_pair(llm: LlmClient, q: str, cand: str, base: str, gold: str,
               swap: bool) -> dict:
    """score — с точки зрения кандидата; swap меняет A/B местами."""
    a, b = (cand, base) if not swap else (base, cand)
    user = (f"ВОПРОС:\n{q}\n\nЭТАЛОН (для справки):\n{gold}\n\n"
            f"ОТВЕТ A:\n{a}\n\nОТВЕТ B:\n{b}")
    for attempt in range(2):
        try:
            js = llm.chat_json(
                [{"role": "system", "content": PAIR_SYSTEM},
                 {"role": "user", "content": user}],
                max_tokens=1000, temperature=0.0,
            )
            s = _snap(js.get("score"))
            if swap:
                s = 1.0 - s
            return {"score": s, "reason": str(js.get("reason", ""))[:200]}
        except JsonParseError:
            if attempt == 1:
                return {"score": 0.5, "reason": "judge parse error"}
            user = user + "\n\n(Верни только JSON.)"
        except Exception as e:                            # noqa: BLE001
            return {"score": 0.5, "error": True,
                    "reason": f"ERROR {type(e).__name__}: {str(e)[:140]}"}
    return {"score": 0.5, "reason": "?"}

# ---------------------------------------------------------------- агрегаты

def score_submission(llm: LlmClient, name: str, answers: dict[str, str],
                     questions: dict[str, str], golden: dict[str, str],
                     baseline: dict[str, str], workers: int = 4) -> dict:
    ids = sorted(questions, key=lambda x: int(x) if x.isdigit() else 10**9)
    jobs = []
    for qid in ids:
        jobs.append(("match", qid, False))
        jobs.append(("match", qid, True))
        jobs.append(("pair", qid, False))
        jobs.append(("pair", qid, True))

    results: dict[tuple, dict] = {}

    def run(job):
        kind, qid, swap = job
        q = questions[qid]
        if kind == "match":
            return judge_match(llm, q, golden.get(qid, ""), answers.get(qid, ""), swap)
        return judge_pair(llm, q, answers.get(qid, ""), baseline.get(qid, ""),
                          golden.get(qid, ""), swap)

    t0 = time.monotonic()
    with ThreadPoolExecutor(max_workers=workers) as ex:
        for fut, res in zip(jobs, ex.map(run, jobs)):
            results[fut] = res
    el = time.monotonic() - t0

    per_q = {}
    for qid in ids:
        m = [results[("match", qid, s)]["score"] for s in (False, True)]
        p = [results[("pair", qid, s)]["score"] for s in (False, True)]
        per_q[qid] = {
            "s_match": round(sum(m) / 2, 3),
            "s_pair": round(sum(p) / 2, 3),
            "halluc": any(results[("match", qid, s)]["hallucination"]
                          for s in (False, True)),
        }

    s_match = [per_q[qid]["s_match"] for qid in ids]
    s_pair = [per_q[qid]["s_pair"] for qid in ids]
    errors = sum(1 for v in results.values() if v.get("error"))
    match_rate = sum(s_match) / len(s_match)

    wins = sum(1 for s in s_pair if s > 0.5)
    losses = sum(1 for s in s_pair if s < 0.5)
    win_rate = wins / (wins + losses) if wins + losses else 0.5

    s_combo = [(a + b) / 2 for a, b in zip(s_match, s_pair)]
    match_rate_S = sum(s_combo) / len(s_combo)

    out = {
        "name": name,
        "n": len(ids),
        "elapsed_s": round(el, 1),
        "errors": errors,
        "modeM": {"MatchRate": round(match_rate, 4),
                  "WinRate": round(win_rate, 4),
                  "Score": round(0.7 * match_rate + 0.3 * win_rate, 4)},
        "modeS": {"MatchRate": round(match_rate_S, 4),
                  "WinRate": round(win_rate, 4),
                  "Score": round(0.7 * match_rate_S + 0.3 * win_rate, 4)},
        "per_q": per_q,
        "details": {str(k): v for k, v in results.items()},
    }
    return out

# ---------------------------------------------------------------- CLI

def main() -> None:
    cfg = _cfg.from_env()
    llm = LlmClient(cfg)
    data_dir = ROOT / os.environ.get("DATA_DIR", "data")

    questions = load_questions(data_dir / "demo_questions.csv")
    golden = load_csv(data_dir / "demo_output.csv")

    cmd = sys.argv[1] if len(sys.argv) > 1 else "score"
    HISTORY.mkdir(exist_ok=True)

    if cmd == "baseline":
        kb = data_dir / "kb.zip"
        if not kb.exists():
            kb = data_dir / "knowledge-base.zip"
        base = build_baseline(llm, questions, kb, HISTORY / "baseline.submission.csv")
        print(f"baseline: {len(base)} ответов -> {HISTORY/'baseline.submission.csv'}")
        return

    files = sys.argv[2:]
    if not files:
        print("usage: judge.py score FILE.csv ...", file=sys.stderr)
        return

    base_path = HISTORY / "baseline.submission.csv"
    if not base_path.exists():
        kb = data_dir / "kb.zip" if (data_dir / "kb.zip").exists() else data_dir / "knowledge-base.zip"
        print("строим бейзлайн...", file=sys.stderr)
        build_baseline(llm, questions, kb, base_path)
    baseline = load_csv(base_path)

    rows = []
    for f in files:
        p = Path(f)
        name = p.stem.replace(".submission", "")
        answers = load_csv(p)
        res = score_submission(llm, name, answers, questions, golden, baseline)
        out = HISTORY / f"{name}.judge.json"
        out.write_text(json.dumps(res, ensure_ascii=False, indent=1),
                       encoding="utf-8")
        rows.append((name, res))
        print(f"[{name}] modeM Score={res['modeM']['Score']} "
              f"(M={res['modeM']['MatchRate']} W={res['modeM']['WinRate']}) "
              f"modeS Score={res['modeS']['Score']} "
              f"(M={res['modeS']['MatchRate']}) errors={res['errors']}"
              f"  -> {out.name}")
        if res["errors"] > res["n"] * 2:
            print("\nAPI недоступен (лимит/сеть) — останавливаюсь, "
                  "частичные результаты сохранены.", file=sys.stderr)
            return 2

    print("\n=== Калибровка vs платформа ===")
    print(f"{'сабмит':8} {'платформа':>9} {'modeM':>7} {'modeS':>7} {'errM':>6} {'errS':>6}")
    for name, res in rows:
        plat = PLATFORM_SCORES.get(name)
        if plat is None:
            print(f"{name:8} {'?':>9} {res['modeM']['Score']:>7.4f} "
                  f"{res['modeS']['Score']:>7.4f}")
            continue
        errM = res["modeM"]["Score"] - plat
        errS = res["modeS"]["Score"] - plat
        print(f"{name:8} {plat:>9.5f} {res['modeM']['Score']:>7.4f} "
              f"{res['modeS']['Score']:>7.4f} {errM:>+6.3f} {errS:>+6.3f}")


if __name__ == "__main__":
    sys.exit(main())
