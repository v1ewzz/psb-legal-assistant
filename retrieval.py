# -*- coding: utf-8 -*-
"""Retrieval v2 (§9): мульти-запросы → BM25 → RRF → exact-ref boost →
temporal-фильтры → реранк (API) → расширение (сосед, full-doc).

v2 после Сабмита №1:
- чанки по пунктам (ingest v2) — точность выдачи выросла;
- несколько запросов на вопрос (standalone + контекст диалога) объединяются RRF
  (§9.1.4 «подвопросы ищутся независимо»);
- реранк по text_ctx (§9.1.6), при сбое — деградация к RRF-порядку (§8.3);
- temporal-фильтры из temporal.search_filters: какую редакцию тащить (D1);
- compromised-чанков в индексе нет вовсе (отсекаются при ingest).
"""
from __future__ import annotations

import gzip
import json
import re
import sys
from pathlib import Path
from typing import Optional, Sequence, Union

import bm25s
import numpy as np
from numpy import ndarray

from config import Config
from ingest import QUERY_REF_RE, normalize_text, tokenize_ru

CHUNK_FIELDS = ("chunk_id", "doc_id", "short_name", "path", "text", "text_ctx")

RRF_K = 60
DENSE_WEIGHT = 1.0           # вес dense-голоса в RRF (BM25: по 1.0 на запрос)
BOOST_REF_SCORE = 0.05       # doc-boost: выше любого одиночного RRF-ранга
RERANK_DOC_CHARS = 2200      # обрезка text_ctx для вызова реранка
PUNKT_QUERY_RE = re.compile(r"\bп\.\s*(\d+(?:\.\d+)?)", re.I)


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
    """BM25 по text_ctx + RRF по нескольким запросам + exact-ref boost (§9)."""

    def __init__(self, chunks: Sequence[dict], bm25_npz: Path, refs: Optional[dict] = None,
                 dense: Optional[ndarray] = None):
        self.chunks = list(chunks)
        if not self.chunks:
            raise ValueError("пустой индекс чанков")
        self._texts = [c["text_ctx"] for c in self.chunks]
        # dense-канал: (n_chunks, dim), L2-нормализовано; рассинхрон → деградация BM25
        self.dense = dense
        if dense is not None and (getattr(dense, "shape", (0,))[0] != len(self.chunks)):
            self.dense = None
        payload = np.load(bm25_npz, allow_pickle=False)
        token_pattern = str(payload["token_pattern"][0])
        if token_pattern != "custom_ru_v2":
            # старый индекс (bm25s.tokenize) несовместим с токенизатором запросов
            raise ValueError(f"индекс собран старым токенизатором: {token_pattern}")
        ids_flat: ndarray = payload["ids_flat"].astype(np.int32)
        row_lengths = payload["row_lengths"].astype(np.int32)
        vocab = [str(v) for v in payload["vocab"]]
        cutoff = np.cumsum(row_lengths)[:-1]
        rows = list(np.split(ids_flat, cutoff)) if len(row_lengths) else []
        tok = bm25s.tokenization.Tokenized(
            ids=rows, vocab={t: i for i, t in enumerate(vocab)})
        self._vocab_map = {t: i for i, t in enumerate(vocab)}
        self._bm25 = bm25s.BM25()
        self._bm25.index(tok)

        refs = refs or {}
        self._doc_numbers: dict[str, set[str]] = {}
        for entry in refs.get("doc_numbers", []):
            self._doc_numbers.setdefault(entry["doc_id"], set()).update(entry["numbers"])
        self._doc_aliases: dict[str, list[str]] = {}
        for entry in refs.get("doc_aliases", []):
            self._doc_aliases.setdefault(entry["doc_id"], []).extend(entry["aliases"])
        # позиции чанков по документам — для соседей (expand)
        self._by_doc: dict[str, list[int]] = {}
        self._pos_by_id: dict[str, int] = {}
        for i, c in enumerate(self.chunks):
            self._by_doc.setdefault(c["doc_id"], []).append(i)
            self._pos_by_id[c["chunk_id"]] = i

    # --- запрос ---

    def _bm25_ranked(self, query: str, k: int) -> list[int]:
        query = normalize_text(query)
        toks = [self._vocab_map[t] for t in tokenize_ru(query) if t in self._vocab_map]
        if not toks:
            return []
        q = bm25s.tokenization.Tokenized(
            ids=[np.asarray(toks, dtype=np.int32)],
            vocab=self._vocab_map)
        k = max(1, min(k, len(self.chunks)))
        result, scores = self._bm25.retrieve(q, k=k)
        out: list[int] = []
        for pos, value in enumerate(result[0]):
            idx = int(value)
            if idx < 0 or idx >= len(self.chunks):
                continue  # добивка bm25s
            if pos < len(scores[0]) and float(scores[0][pos]) <= 0:
                continue  # нулевая релевантность
            out.append(idx)
        return out

    def _match_alias(self, query_low: str, alias: str) -> bool:
        """Слово-граничное совпадение: «Альфа-Трейд» не матчится внутри
        «Альфа-Трейдинг» (T7, §9.4)."""
        pattern = r"(?<![0-9а-яёa-z-])" + re.escape(alias.lower()) + r"(?![0-9а-яёa-z-])"
        return re.search(pattern, query_low) is not None

    def _ref_doc_ids(self, queries: Sequence[str]) -> set[str]:
        hits: set[str] = set()
        for query in queries:
            qlow = normalize_text(query.lower())
            for doc_id, aliases in self._doc_aliases.items():
                if any(self._match_alias(qlow, a) for a in aliases):
                    hits.add(doc_id)
            for m in QUERY_REF_RE.finditer(query):
                token = m.group(2) or m.group(3)
                if token:
                    for doc_id, numbers in self._doc_numbers.items():
                        if token in numbers or norm_num(token) in numbers:
                            hits.add(doc_id)
                    continue
                token = m.group(1)
                if token and token.isdigit():
                    for doc_id, numbers in self._doc_numbers.items():
                        for num in numbers:
                            head = num.rsplit("-", 1)[-1].split(".")[0]
                            if token.lstrip("0") == head.lstrip("0"):
                                hits.add(doc_id)
        return hits

    def _punkt_wanted(self, queries: Sequence[str]) -> Optional[str]:
        """«п. 5.4» в вопросе → точечный поиск чанка этого пункта."""
        for query in queries:
            m = PUNKT_QUERY_RE.search(query)
            if m:
                return m.group(1)
        return None

    # --- основной поиск ---

    def search(self, queries: Union[str, Sequence[str]], top_k: int = 24,
               rerank_query: Optional[str] = None, client=None,
               top_n: int = 8, drop_doc_ids: Optional[set] = None,
               boost_doc_ids: Optional[set] = None,
               deadline: Optional[float] = None,
               query_vec: Optional[ndarray] = None) -> list[dict]:
        """Мульти-запрос → RRF → ref-boost → фильтры → реранк → топ-N (§9)."""
        if isinstance(queries, str):
            queries = [queries]
        queries = [q for q in queries if q and q.strip()] or [" "]

        rrf: dict[int, float] = {}
        for query in queries:
            for rank, idx in enumerate(self._bm25_ranked(query, top_k)):
                rrf[idx] = rrf.get(idx, 0.0) + 1.0 / (RRF_K + rank + 1)

        # Dense-голос: cosine по эмбеддингам чанков — синонимы и перефразы,
        # которых нет у BM25 (§9.1.4 гибрид). Сбой → тихая деградация BM25.
        if self.dense is not None and query_vec is not None:
            try:
                sims = self.dense @ query_vec
                order_d = np.argsort(-sims)[:top_k]
                for rank, idx in enumerate(order_d):
                    if float(sims[idx]) > 0.15:  # нули/мусор не голосуют
                        rrf[idx] = rrf.get(idx, 0.0) + DENSE_WEIGHT / (RRF_K + rank + 1)
            except Exception as e:
                print(f"dense-фьюжн недоступен: {type(e).__name__}", file=sys.stderr)

        drop_doc_ids = drop_doc_ids or set()
        boost_doc_ids = boost_doc_ids or set()

        # exact-ref: документы по номерам/алиасам — вне конкуренции (§9.2)
        ref_docs = self._ref_doc_ids(queries)
        punkt = self._punkt_wanted(queries)
        for doc_id in ref_docs:
            for idx in self._by_doc.get(doc_id, []):
                path = str(self.chunks[idx].get("path", ""))
                if punkt and path.startswith(f"п. {punkt}"):
                    rrf[idx] = rrf.get(idx, 0.0) + BOOST_REF_SCORE * 4
                else:
                    rrf[idx] = rrf.get(idx, 0.0) + BOOST_REF_SCORE
        if punkt:  # пункт могли искать и без номера документа
            for idx, c in enumerate(self.chunks):
                if str(c.get("path", "")).startswith(f"п. {punkt}"):
                    rrf[idx] = rrf.get(idx, 0.0) + BOOST_REF_SCORE * 2

        candidates = [(score, idx) for idx, score in rrf.items()
                      if self.chunks[idx]["doc_id"] not in drop_doc_ids]
        candidates.sort(key=lambda p: (-p[0], p[1]))
        # doc-boost: поднять редакцию под дату события (D1)
        if boost_doc_ids:
            boosted = [(s + BOOST_REF_SCORE, i) for s, i in candidates
                       if self.chunks[i]["doc_id"] in boost_doc_ids]
            rest = [(s, i) for s, i in candidates
                    if self.chunks[i]["doc_id"] not in boost_doc_ids]
            candidates = sorted(boosted + rest, key=lambda p: (-p[0], p[1]))

        order = [idx for _, idx in candidates[:max(top_n * 3, 24)]]
        if not order:
            return []

        # реранк (§9.1.6): query = standalone, документы = text_ctx.
        # Страховка от слабого реранкера/клампа прокси: BM25-топ-2 остаётся
        # в финальной выдаче, даже если реранк его не выбрал
        if client is not None and rerank_query and len(order) > 1:
            try:
                docs = [self._texts[i][:RERANK_DOC_CHARS] for i in order]
                res = client.rerank(rerank_query, docs, top_n=min(top_n, len(order)))
                picked = [order[int(item["index"])] for item in res
                          if 0 <= int(item["index"]) < len(order)]
                if picked:
                    bm25_head = [i for i in order[:2] if i not in picked]
                    merged = picked + bm25_head
                    order = merged + [i for i in order if i not in merged]
            except Exception as e:  # деградация к RRF-порядку (§8.3)
                print(f"rerank недоступен: {type(e).__name__}", file=sys.stderr)
        selected = [self.chunks[i] for i in order[:top_n]]
        # Sibling-fill: добор до 4 чанков документов, УЖЕ представленных в
        # топ-N (по RRF-порядку за срезом). Мульти-фактные вопросы («плата
        # и до какого числа») держат оба факта в соседних пунктах одного
        # договора; срез топ-N систематически терял второй факт (ранги 10-12).
        # SIBLING_FILL=0 — выключить (бисекция; факт-покрытие падает до 93%).
        sibling_extra = 4
        if (_sibling_fill_enabled() and len(selected) >= top_n
                and len(order) > top_n):
            core_docs = {c["doc_id"] for c in selected}
            have = {c["chunk_id"] for c in selected}
            for i in order[top_n:]:
                if len(selected) >= top_n + sibling_extra:
                    break
                chunk = self.chunks[i]
                if chunk["doc_id"] in core_docs and chunk["chunk_id"] not in have:
                    selected.append(chunk)
                    have.add(chunk["chunk_id"])
        # Шапки и полнотекстовые чанки — в конец выдачи (§9.2): «договор» есть
        # в каждой шапке, без реранка они вытесняли пункты с нормой
        core = [c for c in selected
                if str(c.get("path")) not in {"шапка", "документ целиком"}]
        aux = [c for c in selected
               if str(c.get("path")) in {"шапка", "документ целиком"}]
        return core + aux

    def expand(self, selected: list[dict], max_extra: int = 3) -> list[dict]:
        """Расширение (§9.1.7): предыдущий чанк того же документа (контекст
        раздела) для топ-2 + full-doc чанк документа топ-1, если есть."""
        out = list(selected)
        seen = {c["chunk_id"] for c in out}
        for chunk in selected[:2]:
            gid = self._pos_by_id.get(chunk["chunk_id"])
            if gid is None:
                continue
            positions = self._by_doc.get(chunk["doc_id"], [])
            try:
                j = positions.index(gid)
            except ValueError:
                continue
            if j > 0:
                prev = self.chunks[positions[j - 1]]
                if prev["chunk_id"] not in seen:
                    out.append(prev)
                    seen.add(prev["chunk_id"])
        if selected:
            for idx in self._by_doc.get(selected[0]["doc_id"], []):
                full = self.chunks[idx]
                if full.get("path") == "документ целиком" and full["chunk_id"] not in seen:
                    out.append(full)
                    seen.add(full["chunk_id"])
                    break
        return out[: len(selected) + max_extra]


def _sibling_fill_enabled() -> bool:
    """SIBLING_FILL env-флаг (бисекция регресса 26.199); дефолт — включён."""
    import os
    raw = os.environ.get("SIBLING_FILL")
    if raw is None or not raw.strip():
        return True
    return raw.strip().lower() not in {"0", "false", "no", "off", "нет"}


def norm_num(token: str) -> str:
    """«П-17-2024» → «П-17/2024» (сравнение реквизитов)."""
    return re.sub(r"^([А-ЯЁ]{1,2}-\d+)-(\d{4})$", r"\1/\2", token.strip())
