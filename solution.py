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
    dense = None
    if chunks:
        bm25_path = find_artifact(cfg, "bm25.index.npz")
        refs_path = find_artifact(cfg, "refs.json")
        if bm25_path is not None:
            refs = None
            if refs_path is not None:
                refs = json.loads(refs_path.read_text(encoding="utf-8"))
            retriever = Retriever(chunks, bm25_path, refs)
    docs: list = []
    docs_path = find_artifact(cfg, "docs.json")
    if docs_path is not None:
        try:
            docs = json.loads(docs_path.read_text(encoding="utf-8"))
        except (json.JSONDecodeError, OSError) as e:
            print(f"docs.json не читается: {type(e).__name__}", file=sys.stderr)
    client = LlmClient(cfg) if cfg.api_ready else None
    rerank_ok = False
    probe: dict = {}
    if client is not None:
        # пробы старта ≤30 с (§16.3): короткий тайм-аут, без ретраев
        probe_client = LlmClient(cfg, timeout_s=10, max_retries=0)
        try:
            probe = probe_client.probe_all()
            rerank_ok = bool(probe.get("rerank_ok"))
            print(f"Пробы: llm={'ок' if probe.get('llm_ok') else 'нет'}; "
                  f"embed={'ок' if probe.get('emb_ok') else 'нет'}; "
                  f"rerank={'ок' if rerank_ok else 'нет'}; "
                  f"{probe.get('elapsed_s', '?')} с", file=sys.stderr)
        except Exception as e:
            print(f"Пробы не удались: {type(e).__name__}", file=sys.stderr)
        finally:
            probe_client.close()
        # Dense-канал: эмбеддинги чанков (гибрид BM25+dense, §9.1.4).
        # Дефолт ВЫКЛ до проверки на реальных эмбеддингах платформы: в базе
        # две редакции Регламента, cosine тянет чанк-близнец (контаминация
        # редакций). DENSE=1 — включить для A/B; любой сбой → BM25-only (§8.3).
        if retriever is not None and probe.get("emb_ok") and _bool_flag_env(cfg, "DENSE", False):
            dense = _build_dense(cfg, retriever, client, T0)
    print(f"Контекст: чанков={len(chunks) if chunks else 0}; "
          f"bm25={'да' if retriever else 'нет'}; "
          f"dense={'да' if dense is not None else 'нет'}; "
          f"llm={'да' if client else 'нет'}; "
          f"rerank={'да' if rerank_ok else 'нет'}; "
          f"режим={cfg.llm_model}", file=sys.stderr)
    return {"cfg": cfg, "retriever": retriever, "client": client,
            "docs": docs, "rerank_ok": rerank_ok}


def _bool_flag_env(cfg: config.Config, name: str, default: bool) -> bool:
    """Мини-флаг без заезда в Config (DENSE=0 для локальных A/B)."""
    import os
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on", "да"}


DENSE_TIMEBOX_S = 90.0


def _build_dense(cfg: config.Config, retriever: Retriever,
                 client: LlmClient, t0: float):
    """Матрица эмбеддингов всех чанков: кэш index/embeddings.npz (проверяем
    имя модели и число чанков), иначе батчи по 64 в тайм-боксе; L2-нормализация."""
    import numpy as np
    cache_path = Path("index") / "embeddings.npz"
    model = cfg.emb_model
    if cache_path.is_file():
        try:
            payload = np.load(cache_path, allow_pickle=False)
            if (str(payload["model"][0]) == model
                    and payload["vecs"].shape[0] == len(retriever.chunks)):
                print("dense: кэш embeddings.npz использован", file=sys.stderr)
                return payload["vecs"]
        except Exception:
            pass
    n = len(retriever.chunks)
    vecs: list = []
    try:
        for start in range(0, n, 64):
            if time.time() - t0 > DENSE_TIMEBOX_S:
                print(f"dense: тайм-бокс исчерпан на {start}/{n} — деградация BM25",
                      file=sys.stderr)
                return None
            batch = [t[:4000] for t in retriever._texts[start:start + 64]]
            v = client.embed(batch)
            vecs.append(v)
        if not vecs:
            return None
    except Exception as e:
        print(f"dense: эмбеддинги не удались ({type(e).__name__}) — BM25-only",
              file=sys.stderr)
        return None
    try:
        mat = np.vstack(vecs)
        norms = np.linalg.norm(mat, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        mat = mat / norms
        cache_path.parent.mkdir(parents=True, exist_ok=True)
        np.savez(cache_path, vecs=mat, model=np.array([model]))
        print(f"dense: {mat.shape[0]}x{mat.shape[1]} построено", file=sys.stderr)
    except Exception as e:
        print(f"dense: сохранение не удалось: {type(e).__name__}", file=sys.stderr)
        return None
    return mat


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
            # Адаптивный бюджет (U8): проекция перебора времени → fast_mode
            # (без реранка), чтобы все вопросы успели получить ответ
            if (not ctx.get("fast_mode") and len(answers) < len(ids)
                    and time.time() - T0 > 8 * 60
                    and len(answers) < 0.5 * len(ids)):
                ctx["fast_mode"] = True
                print("Fast mode: пропускаем реранк (проекция тайм-аута)", file=sys.stderr)
    write_submission(ids, answers, questions, fallback_answer)
    ex.shutdown(wait=False, cancel_futures=True)
    os._exit(0)  # зависший вызов не удержит процесс (§16.2)


if __name__ == "__main__":
    main()
