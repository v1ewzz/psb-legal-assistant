# -*- coding: utf-8 -*-
"""Doc2query (G-фикс): синтетические вопросы в BM25-поле; промпт не трогаем.

Для каждого чанка index/chunks.jsonl.gz — один LLM-вызов, генерирующий 3
коротких пользовательских вопроса. Они добавляются ТОЛЬКО в текст BM25-поля
(индекс перестраивается); text_ctx остаётся байт-в-байт — в LLM-промпт
синтетика не попадает никогда. Лечит лексический разрыв BM25 («ночной
пропуск» vs «круглосуточный доступ») без runtime-вызовов.

Выход: outdir (build/d2q_index): chunks.jsonl.gz (+поле doc2q),
bm25.index.npz (перестроен по text_ctx+doc2q), docs.json/refs.json — копии,
manifest.json с пометкой doc2q. Для A/B: DATA_DIR=build/d2q_index eval-раннера.

Запуск: py scripts/doc2query.py [--index index] [--outdir build/d2q_index]
"""
from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import shutil
import sys
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def load_dotenv(path: Path) -> None:
    if not path.is_file():
        return
    for line in path.read_text(encoding="utf-8-sig").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k, v = k.strip(), v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


_env = ROOT / ".env"
if not _env.is_file():
    _env = Path(os.environ.get("PSB_MAIN", r"D:\hackaton_PSB")) / ".env"
load_dotenv(_env)
os.environ.setdefault("DATA_DIR", str(Path(os.environ.get("PSB_MAIN", r"D:\hackaton_PSB")) / "data"))

import bm25s  # noqa: E402
import numpy as np  # noqa: E402

from config import from_env  # noqa: E402
from ingest import is_compromised  # noqa: E402
from llm_client import ApiError, LlmClient  # noqa: E402

GEN_SYSTEM = (
    "Ты помогаешь поисковой системе: генерируешь короткие вопросы, на которые "
    "отвечает фрагмент документа."
)
GEN_USER = (
    "Фрагмент:\n{text}\n\n"
    "Сгенерируй 3 разных коротких вопроса пользователя (5–12 слов), на которые "
    "этот фрагмент даёт ответ. Пиши как пользователь: обычные слова, не юридические "
    "термины из фрагмента. По одному вопросу на строку, без номеров и пояснений."
)
_NUM_BULLET_RE = re.compile(r"^\s*(?:\d+[.)]\s*|[-•*]\s*)")


def gen_questions(client: LlmClient, text: str) -> list[str]:
    """→ до 3 коротких вопросов; пусто — если модель не дала годного."""
    raw = client.chat(
        [{"role": "system", "content": GEN_SYSTEM},
         {"role": "user", "content": GEN_USER.format(text=text[:1200])}],
        max_tokens=1200, temperature=0.3)
    # 1200: deepseek-v4.1 — reasoning-модель; при 700 часть вызовов уходила
    # в пустой content (все токены съедали «размышления»)
    out, seen = [], set()
    for line in raw.splitlines():
        q = _NUM_BULLET_RE.sub("", line).strip()
        q = re.sub(r"\s+", " ", q)
        if not 8 <= len(q) <= 200 or is_compromised(q) or q.lower() in seen:
            continue
        seen.add(q.lower())
        out.append(q)
        if len(out) == 3:
            break
    return out


def rebuild_bm25(chunks: list[dict], outdir: Path) -> None:
    """BM25 по text_ctx + doc2q (зеркало ingest.build_index, те же параметры)."""
    texts = [c["text_ctx"] + ("\n" + c["doc2q"] if c.get("doc2q") else "")
             for c in chunks]
    tokenized = bm25s.tokenize(texts, lower=True, stopwords=[])
    ids_list = [np.asarray(idx, dtype=np.int32) for idx in tokenized.ids]
    ids_flat = (np.concatenate(ids_list) if ids_list
                else np.zeros(0, dtype=np.int32))
    row_lengths = np.asarray([len(a) for a in ids_list], dtype=np.int32)
    vocab = list(tokenized.vocab)
    np.savez_compressed(outdir / "bm25.index.npz",
                        ids_flat=ids_flat,
                        row_lengths=row_lengths,
                        vocab=np.asarray(vocab, dtype=np.str_),
                        token_pattern=np.asarray(["(?u)\\b\\w\\w+\\b"]))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--index", default="index")
    ap.add_argument("--outdir", default="build/d2q_index")
    args = ap.parse_args()

    src = ROOT / args.index
    outdir = ROOT / args.outdir
    src_chunks = [json.loads(line) for line in
                  gzip.open(src / "chunks.jsonl.gz", "rt", encoding="utf-8")
                  if line.strip()]
    if not src_chunks:
        sys.exit(f"Пустой индекс: {src / 'chunks.jsonl.gz'}")

    # инкрементально: уже сгенерированные doc2q сохраняем, догоняем только пробелы
    chunks = src_chunks
    prev = outdir / "chunks.jsonl.gz"
    if prev.is_file():
        old = [json.loads(line) for line in
               gzip.open(prev, "rt", encoding="utf-8") if line.strip()]
        if len(old) == len(src_chunks):
            chunks = old

    cfg = from_env()
    if not cfg.api_ready:
        sys.exit("API недоступен: заполни .env (LLM_BASE_URL/LLM_API_KEY/LLM_MODEL)")
    client = LlmClient(cfg, timeout_s=60, max_retries=2)

    t0 = time.time()
    questions: dict[int, list[str]] = {}
    failures: list[int] = []
    for i, c in enumerate(chunks):
        if c.get("doc2q"):
            questions[i] = c["doc2q"].split("\n")
    todo = [i for i in range(len(chunks)) if i not in questions]
    print(f"К генерации: {len(todo)}/{len(chunks)} "
          f"(готовых из прошлого прогона: {len(questions)})", file=sys.stderr)
    with ThreadPoolExecutor(max_workers=6) as ex:
        futures = {ex.submit(gen_questions, client, chunks[i]["text"]): i
                   for i in todo}
        for n, fut in enumerate(as_completed(futures), 1):
            i = futures[fut]
            try:
                questions[i] = fut.result()
            except ApiError as e:
                failures.append(i)
                print(f"чанк {i}: {type(e).__name__}: {str(e)[:120]}", file=sys.stderr)
            if n % 50 == 0:
                print(f"  {n}/{len(chunks)}; сбоев={len(failures)}; "
                      f"{round(time.time() - t0)}с", file=sys.stderr)

    for i, chunk in enumerate(chunks):
        if questions.get(i):
            chunk["doc2q"] = "\n".join(questions[i])

    outdir = ROOT / args.outdir
    outdir.mkdir(parents=True, exist_ok=True)
    with gzip.open(outdir / "chunks.jsonl.gz", "wt", encoding="utf-8") as f:
        for chunk in chunks:
            f.write(json.dumps(chunk, ensure_ascii=False) + "\n")
    for name in ("docs.json", "refs.json"):
        shutil.copyfile(src / name, outdir / name)
    rebuild_bm25(chunks, outdir)
    (outdir / "manifest.json").write_text(json.dumps({
        "mode": "bm25_only",
        "n_chunks": len(chunks),
        "schema_version": 2,
        "doc2q": True,
        "doc2q_failures": len(failures),
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "build_s": round(time.time() - t0, 1),
    }, ensure_ascii=False, indent=1), encoding="utf-8")

    covered = sum(1 for c in chunks if c.get("doc2q"))
    print(f"Готово: {covered}/{len(chunks)} чанков с doc2q; сбоев={len(failures)}; "
          f"вызовов={client.stats['chat']}; {round(time.time() - t0)}с → {outdir}",
          file=sys.stderr)


if __name__ == "__main__":
    main()
