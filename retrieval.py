# -*- coding: utf-8 -*-
"""Retrieval v1 (Сабмит №1): BM25 + exact-ref boost (§9 упрощённо).

Канал dense и реранк в v1 отсутствуют — деградация BM25-only допустима (§8.3);
полная программа (RRF, rerank, expand) — Фаза 2/3.
"""
from __future__ import annotations

import gzip
import json
from pathlib import Path
from typing import Optional, Sequence

import bm25s
import numpy as np
from numpy import ndarray

from config import Config, first_nonempty
from ingest import QUERY_REF_RE, normalize_text

CHUNK_FIELDS = ("chunk_id", "doc_id", "short_name", "path", "text", "text_ctx")


def index_dirs(cfg: Config) -> list[Path]:
    """Где искать артефакты: cwd → DATA_DIR → script_dir → script_dir/index (§2.3)."""
    roots = [Path.cwd(), Path(cfg.data_dir),
             Path(__file__).resolve().parent,
             Path(__file__).resolve().parent / "index"]
    seen, out = set(), []
    for root in roots:
        try:
            root = root.resolve()
        except OSError:
            continue
        if root not in seen:
            out.append(root)
            seen.add(root)
    return out


def find_artifact(cfg: Config, name: str) -> Optional[Path]:
    for root in index_dirs(cfg):
        path = root / name
        if path.is_file():
            return path
    return None


def load_chunks(cfg: Config) -> list[dict]:
    path = find_artifact(cfg, "chunks.jsonl.gz")
    if path is None:
        return []
    with gzip.open(path, "rt", encoding="utf-8") as f:
        return [json.loads(line) for line in f if line.strip()]


class Retriever:
    """BM25-поиск по text_ctx + приоритет exact-ref-документов (§9.1/9.2 упрощённо)."""

    def __init__(self, chunks: Sequence[dict], bm25_npz: Path,
                 refs: Optional[dict] = None, sibling_fill: bool = False):
        self.chunks = list(chunks)
        if not self.chunks:
            raise ValueError("пустой индекс чанков")
        self.sibling_fill = bool(sibling_fill)  # добор +2 сиблинга (SIBLING_FILL=1)
        self._texts = [c["text_ctx"] for c in self.chunks]
        payload = np.load(bm25_npz, allow_pickle=False)
        self._token_pattern = str(payload["token_pattern"][0])
        ids_flat: ndarray = payload["ids_flat"].astype(np.int32)
        row_lengths = payload["row_lengths"].astype(np.int32)
        vocab = [str(v) for v in payload["vocab"]]
        cutoff = np.cumsum(row_lengths)[:-1]
        rows = list(np.split(ids_flat, cutoff)) if len(row_lengths) else []
        # Tokenized из плоских массивов; vocab — dict-token→id (требование bm25s)
        tok = bm25s.tokenization.Tokenized(
            ids=rows, vocab={t: i for i, t in enumerate(vocab)})
        self._vocab_map = {t: i for i, t in enumerate(vocab)}
        self._bm25 = bm25s.BM25()  # дефолт 'lucene' (method="bm25s" для idf невалиден)
        self._bm25.index(tok)
        # refs: doc_id → номера документов (exact-ref boost, §9.1.1)
        self._doc_numbers: dict[str, set[str]] = {}
        for entry in (refs or {}).get("doc_numbers", []):
            self._doc_numbers.setdefault(entry["doc_id"], set()).update(entry["numbers"])

    # --- запрос ---

    def _ref_doc_ids(self, query: str) -> set[str]:
        """Документы, прямо упомянутые в вопросе: «ст. 59», «№ К-12», «А-08/2024»."""
        hits: set[str] = set()
        for m in QUERY_REF_RE.finditer(query):
            token = (m.group(1) or m.group(2) or m.group(3))
            if not token:
                continue
            if token.isdigit():  # «ст. 59» — сверяем с номером статьи в имени файла
                for doc_id, numbers in self._doc_numbers.items():
                    for num in numbers:
                        head = num.rsplit("-", 1)[-1]  # «ст59» → 59, «тк_59» → 59
                        if token.lstrip("0") == head.lstrip("0"):
                            hits.add(doc_id)
            else:  # №-номера: К-12, А-08/2024, ПТ-05/2023
                for doc_id, numbers in self._doc_numbers.items():
                    if token in numbers:
                        hits.add(doc_id)
        return hits

    def ref_doc_hits(self, question: str) -> set[str]:
        """E-фикс: документы, прямо упомянутые в вопросе (анти-переотказ)."""
        return self._ref_doc_ids(normalize_text(question))

    def search(self, question: str, top_k: int) -> list[dict]:
        """→ [{...chunk}] в порядке релевантности, doc-boost вне конкуренции.

        Sibling-fill (+2, флаг SIBLING_FILL): мульти-фактные вопросы («плата
        И до какого числа») держат второй факт в соседнем пункте того же
        документа; срез топ-K его терял. Добор — только чанками документов,
        УЖЕ представленных в выдаче (новых документов не добавляем)."""
        question = normalize_text(question)
        query = bm25s.tokenize([question], lower=True, stopwords=[])
        top_k = max(1, min(top_k or 1, len(self.chunks)))
        pool = min(top_k + 4, len(self.chunks))
        result, _ = self._bm25.retrieve(query, k=pool)
        order = [int(i) for i in result[0]]
        ref_docs = self._ref_doc_ids(question)
        if ref_docs:
            in_refs = [i for i in order if self.chunks[i]["doc_id"] in ref_docs]
            rest = [i for i in order if self.chunks[i]["doc_id"] not in ref_docs]
            order = in_refs + rest
        selected = order[:top_k]
        if self.sibling_fill:
            have_docs = {self.chunks[i]["doc_id"] for i in selected}
            have = set(selected)
            extra: list[int] = []
            for i in order[top_k:]:
                if len(extra) >= 2:
                    break
                if self.chunks[i]["doc_id"] in have_docs and i not in have:
                    extra.append(i)
                    have.add(i)
            selected = selected + extra
        return [self.chunks[i] for i in selected]
