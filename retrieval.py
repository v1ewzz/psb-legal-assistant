# -*- coding: utf-8 -*-
"""Гибридный поиск (п.1–5, 8, 10, 11): BM25(текст) + BM25(doc2q) + dense → RRF →
метаданные/точные ссылки → реранкер (с деградацией) → расширение контекста
(перекрёстные ссылки, связанные документы/редакции, соседние чанки).

Все внешние каналы опциональны: нет эмбеддера → BM25+doc2q; нет реранкера → RRF.
"""
from __future__ import annotations

import gzip
import json
import math
import os
import re
import sys
import tempfile
import threading
import time
from dataclasses import dataclass, field
from datetime import date
from pathlib import Path
from typing import Optional

import numpy as np

import ingest
from config import (Config, EMBED_BATCH_SIZE, MAX_EVIDENCE_CHARS, MAX_EVIDENCE_CHUNKS,
                    STARTUP_EMBED_BUDGET_S)
from llm_client import ApiError, LlmClient
from runtime_utils import LRU, STATS, Trace
from textutil import (normalize_text, parse_dates, sha1, stem, tokenize, years_in)

PAST_WORDS = ("ранее", "прежн", "старой", "старая", "предыдущ", "был ", "была ", "было ",
              "утратил", "отмен", "до внесения", "до 01.", "действовал", "действовала",
              "в 2023", "в 2024", "раньше", "прошл")
GENERIC_LABEL = {"договор", "соглашен", "письмо", "служебн", "записк", "регламент", "доп", "ред",
                 "к", "от", "ст"}


def embed_text(c: dict) -> str:
    t = f"{c.get('label', '')}. {c.get('path', '')}. {c['text'][:1300]}"
    if c.get("doc2q"):
        t += "\nВопросы: " + c["doc2q"].replace("\n", " ")
    return t[:2000]


# ------------------------------------------------------------------ BM25

class BM25:
    def __init__(self, docs_tokens: list[list[str]], k1: float = 1.4, b: float = 0.75):
        self.n = len(docs_tokens)
        self.k1, self.b = k1, b
        self.dl = np.asarray([len(t) for t in docs_tokens], dtype=np.float64)
        self.avgdl = float(self.dl.mean()) if self.n and self.dl.sum() else 1.0
        post: dict[str, dict[int, int]] = {}
        for i, toks in enumerate(docs_tokens):
            for t in toks:
                d = post.setdefault(t, {})
                d[i] = d.get(i, 0) + 1
        self.idf: dict[str, float] = {}
        self.post: dict[str, tuple[np.ndarray, np.ndarray]] = {}
        for t, d in post.items():
            df = len(d)
            self.idf[t] = math.log(1 + (self.n - df + 0.5) / (df + 0.5))
            idx = np.fromiter(d.keys(), dtype=np.int64, count=df)
            tf = np.fromiter(d.values(), dtype=np.float64, count=df)
            self.post[t] = (idx, tf)

    def score(self, q_tokens: list[str]) -> np.ndarray:
        out = np.zeros(self.n, dtype=np.float64)
        if not self.n:
            return out
        seen: dict[str, int] = {}
        for t in q_tokens:
            seen[t] = seen.get(t, 0) + 1
        for t, qtf in seen.items():
            p = self.post.get(t)
            if p is None:
                continue
            idx, tf = p
            norm = self.k1 * (1 - self.b + self.b * self.dl[idx] / self.avgdl)
            out[idx] += self.idf[t] * (tf * (self.k1 + 1) / (tf + norm)) * (1 + 0.15 * math.log(qtf))
        return out


# ---------------------------------------------------------------- время

def valid_on(meta: dict, d: date) -> bool:
    try:
        vf = meta.get("valid_from") or meta.get("edition_date") or meta.get("doc_date")
        if vf and d < date.fromisoformat(vf):
            return False
        vt = meta.get("valid_to")
        if vt and d >= date.fromisoformat(vt):
            return False
        if meta.get("status") == "revoked" and not vt:
            return False
    except ValueError:
        pass
    return True


def query_time_intent(query: str) -> dict:
    low = normalize_text(query).lower()
    past = any(w in low for w in PAST_WORDS)
    return {"past": past, "dates": parse_dates(query), "years": years_in(query)}


# ------------------------------------------------------------ результаты

@dataclass
class EvChunk:
    chunk: dict
    reason: str = "поиск"
    score: float = 0.0
    rank: int = 0


@dataclass
class Evidence:
    items: list[EvChunk] = field(default_factory=list)
    docs: dict[str, dict] = field(default_factory=dict)
    confidence: float = 0.0
    top_bm25: float = 0.0
    coverage: float = 0.0
    channels: list[str] = field(default_factory=list)
    missing_refs: list[str] = field(default_factory=list)
    rerank_scores: bool = False

    @property
    def chunks(self) -> list[dict]:
        return [e.chunk for e in self.items]


# -------------------------------------------------------------- Retriever

class Retriever:
    def __init__(self, data: ingest.IndexData, cfg: Config,
                 client: Optional[LlmClient] = None, embeddings: Optional[np.ndarray] = None):
        self.data, self.cfg, self.client = data, cfg, client
        self.chunks = data.chunks
        self.docs = data.docs
        self.n = len(self.chunks)
        if not self.n:
            raise ValueError("пустой индекс")
        self.index_hash = data.manifest.get("index_hash", "")
        self.bm_text = BM25([tokenize(c["text_ctx"]) for c in self.chunks])
        self.bm_d2q = BM25([tokenize(c.get("doc2q", "")) for c in self.chunks])
        self.emb = embeddings
        self.by_doc: dict[str, list[int]] = {}
        for i, c in enumerate(self.chunks):
            self.by_doc.setdefault(c["doc_id"], []).append(i)
        for ids in self.by_doc.values():
            ids.sort(key=lambda i: self.chunks[i]["ord"])
        self.clause_map: dict[tuple[str, str], int] = {}
        for i, c in enumerate(self.chunks):
            for cl in c.get("clauses", []):
                self.clause_map.setdefault((c["doc_id"], cl), i)
        self.label_tokens = {d: {t for t in tokenize(m["label"].replace("(", " ").replace(")", " "))
                                 if not t[0].isdigit() and t not in GENERIC_LABEL and len(t) > 2}
                             for d, m in self.docs.items()}
        self.num_to_docs: dict[str, set[str]] = {}
        self.art_to_docs: dict[str, set[str]] = {}
        for d, m in self.docs.items():
            for nn in m.get("doc_numbers", []):
                self.num_to_docs.setdefault(nn, set()).add(d)
            if m.get("article"):
                self.art_to_docs.setdefault(m["article"], set()).add(d)
        all_dates = [m["export_date"] for m in self.docs.values() if m.get("export_date")]
        today = date.today()
        if cfg.as_of:
            try:
                today = date.fromisoformat(cfg.as_of)
            except ValueError:
                pass
        elif all_dates:
            today = max(today, date.fromisoformat(max(all_dates)))
        self.as_of = today
        self._qemb = LRU(4096)
        self._cache = LRU(4096)
        self._rerank_fail = 0
        self._lock = threading.Lock()

    # ---- точные ссылки ----
    def _acts_in_query(self, query: str) -> set[str]:
        low = _alias_key(query)
        found = set()
        for d, m in self.docs.items():
            if m["doc_type"] != "law":
                continue
            for a in m["act_aliases"]:
                ak = _alias_key(a)
                if len(ak) >= 2 and re.search(rf"(?<![а-яa-z0-9]){re.escape(ak)}(?![а-яa-z0-9])", low):
                    found.add(m["act"])
        return found

    def ref_docs(self, query: str) -> tuple[set[str], list[str]]:
        """Документы, прямо названные в вопросе, и ссылки, для которых документа нет."""
        hits: set[str] = set()
        missing: list[str] = []
        acts = self._acts_in_query(query)
        for m in ingest.QUERY_REF_RE.finditer(normalize_text(query)):
            art, num1, num2 = m.group(1), m.group(2), m.group(3)
            if art:
                art = art.replace(",", ".")
                cands = {d for d in self.art_to_docs.get(art, set())
                         if not acts or self.docs[d]["act"] in acts}
                if cands:
                    hits |= cands
                elif self.art_to_docs.get(art) or art not in self.art_to_docs:
                    act_txt = f" {'/'.join(sorted(acts))}" if acts else ""
                    missing.append(f"ст. {art}{act_txt}")
            else:
                token = (num1 or num2)
                base = token.split("/")[0]
                cands = self.num_to_docs.get(token, set()) | self.num_to_docs.get(base, set())
                if cands:
                    hits |= cands
                else:
                    missing.append(f"№ {token}")
        return hits, missing

    # ---- каналы ----
    def _dense_scores(self, query: str, deadline: Optional[float]) -> Optional[np.ndarray]:
        if self.emb is None or self.client is None or not self.cfg.embedder_ready:
            return None
        key = normalize_text(query).lower()[:1500]
        vec = self._qemb.get(key)
        if vec is None:
            try:
                vec = self.client.embed([query[:1500]], deadline=deadline)[0]
            except ApiError:
                STATS.count("dense_fail")
                return None
            self._qemb.put(key, vec)
        return self.emb @ vec

    @staticmethod
    def _ranks(scores: np.ndarray, pool: int) -> dict[int, int]:
        pos = np.where(scores > 0)[0]
        if not len(pos):
            return {}
        order = pos[np.argsort(-scores[pos], kind="stable")][:pool]
        return {int(i): r for r, i in enumerate(order)}

    # ---- основной поиск ----
    def search(self, query: str, *, context: str = "", level: int = 0,
               deadline: Optional[float] = None, trace: Optional[Trace] = None) -> Evidence:
        trace = trace or Trace()
        key = f"{normalize_text(query).lower()}|{normalize_text(context).lower()[:300]}|{min(level, 2)}"
        cached = self._cache.get(key)
        if cached is not None:
            STATS.count("search_cache_hit")
            return cached
        ev = self._search(query, context, level, deadline, trace)
        self._cache.put(key, ev)
        return ev

    def _search(self, query: str, context: str, level: int, deadline: Optional[float],
                trace: Trace) -> Evidence:
        cfg = self.cfg
        pool = cfg.candidate_pool
        q_all = f"{query}\n{context}".strip() if context else query
        with trace.stage("retrieve.lexical"):
            qt = tokenize(q_all)
            main_qt = tokenize(query)
            s_text = self.bm_text.score(qt if context else main_qt)
            s_d2q = self.bm_d2q.score(main_qt)
        with trace.stage("retrieve.dense"):
            s_dense = self._dense_scores(q_all if context else query, deadline) if level < 3 else None
        channels = ["bm25", "doc2q"] + (["dense"] if s_dense is not None else [])
        fused = np.zeros(self.n, dtype=np.float64)
        for scores, w in ((s_text, 1.0), (s_d2q, 0.8), (s_dense, 1.0)):
            if scores is None:
                continue
            if w == 1.0 and scores is s_dense:
                ranks = self._ranks(np.where(scores > 0.12, scores, 0), pool)
            else:
                ranks = self._ranks(scores, pool)
            for i, r in ranks.items():
                fused[i] += w / (60 + r)
        top_bm25 = float(s_text.max()) if self.n else 0.0

        # --- метаданные ---
        ti = query_time_intent(query)
        ref_docs, missing = self.ref_docs(q_all if context else query)
        qset = {t for t in main_qt if not t[0].isdigit()}
        asof_dates = ti["dates"]
        for i in np.where(fused > 0)[0]:
            c = self.chunks[i]
            m = self.docs[c["doc_id"]]
            mult = 1.0
            if c["doc_id"] in ref_docs:
                mult *= 2.2
            lt = self.label_tokens.get(c["doc_id"], set())
            if lt:
                ov = len(qset & lt) / len(lt)
                if ov >= 0.25:
                    mult *= 1 + 0.8 * ov
            if m.get("status") == "revoked" and not ti["past"]:
                matches_year = any(str(y) in (m.get("edition_date") or "") + (m.get("valid_to") or "")
                                   + m["label"] for y in ti["years"])
                mult *= 1.0 if matches_year else 0.6
            if asof_dates:
                d0 = asof_dates[0]
                mult *= 1.3 if valid_on(m, d0) else 0.7
            fused[i] *= mult

        order = [int(i) for i in np.argsort(-fused, kind="stable") if fused[i] > 0]
        if not order:
            order = [int(i) for i in np.argsort(-s_text, kind="stable")[:cfg.evidence_k]]
        base_order = order[:max(cfg.rerank_pool, cfg.evidence_k + 2)]

        # --- реранк ---
        rr_scores_used = False
        conf = 0.0
        final = list(base_order)
        if (level < 2 and cfg.reranker_ready and self.client is not None
                and self._rerank_fail < 4 and len(base_order) > 1):
            with trace.stage("retrieve.rerank"):
                try:
                    final, conf = self._rerank(query, base_order, deadline)
                    rr_scores_used = conf > 0
                    channels.append("rerank")
                except ApiError:
                    with self._lock:
                        self._rerank_fail += 1
                    STATS.count("rerank_fail")
        qstems = {t for t in main_qt if not t[0].isdigit() and len(t) > 2}
        cov = 0.0
        if qstems and final:
            have_tok: set[str] = set()
            for i in final[:3]:
                have_tok |= set(tokenize(self.chunks[i]["text_ctx"]))
            cov = len(qstems & have_tok) / len(qstems)
        if not rr_scores_used:
            conf = cov

        # --- выбор ядра ---
        k = cfg.evidence_k
        core = final[:k]
        items = [EvChunk(self.chunks[i], "поиск", float(fused[i]), r) for r, i in enumerate(core)]
        with trace.stage("retrieve.expand"):
            items = self._expand(items, query, s_text, fused, ti, set(ref_docs))
        items = self._order(items)
        docs = {it.chunk["doc_id"]: self.docs[it.chunk["doc_id"]] for it in items}
        return Evidence(items=items, docs=docs, confidence=conf, top_bm25=top_bm25, coverage=cov,
                        channels=channels, missing_refs=missing, rerank_scores=rr_scores_used)

    def _rerank(self, query: str, cand: list[int], deadline: Optional[float]) -> tuple[list[int], float]:
        docs = [f"{self.chunks[i]['label']} — {self.chunks[i]['path']}\n{self.chunks[i]['text'][:1100]}"
                for i in cand]
        try:
            res = self.client.rerank(query[:1500], docs, top_n=len(docs), deadline=deadline)
        except ApiError as e:
            if getattr(e, "status", None) in (400, 413, 422) and len(docs) > 5:
                n = max(self.cfg.reranker_top_n, 5)
                docs, cand = docs[:n + 3], cand[:n + 3]
                res = self.client.rerank(query[:1500], docs, top_n=min(n, len(docs)), deadline=deadline)
            else:
                raise
        if not res:
            raise ApiError("пустой ответ реранка")
        rr_rank = {r["index"]: p for p, r in enumerate(res)}
        miss = len(res)
        scored = [r["relevance_score"] for r in res if r["relevance_score"] is not None]
        fuse = {}
        for pos, i in enumerate(cand):
            fuse[i] = 1.0 / (60 + pos) + 1.8 / (60 + rr_rank.get(pos, miss + pos))
        final = sorted(cand, key=lambda i: -fuse[i])
        conf = float(max(scored)) if scored else 0.0
        return final, max(0.0, min(1.0, conf))

    # ---- расширение контекста ----
    def _best_in_doc(self, doc_id: str, s_text: np.ndarray, fused: np.ndarray,
                     prefer_clauses: set[str], exclude: set[int]) -> Optional[int]:
        best, best_v = None, -1.0
        for i in self.by_doc.get(doc_id, []):
            if i in exclude:
                continue
            v = s_text[i] + 40 * fused[i]
            if prefer_clauses & set(self.chunks[i].get("clauses", [])):
                v += 3.0
            if v > best_v:
                best, best_v = i, v
        return best

    def _expand(self, items: list[EvChunk], query: str, s_text: np.ndarray, fused: np.ndarray,
                ti: dict, ref_docs: set[str]) -> list[EvChunk]:
        have = {self._idx(it.chunk) for it in items}
        chars = sum(len(it.chunk["text"]) for it in items)
        cap_n, cap_c = MAX_EVIDENCE_CHUNKS, MAX_EVIDENCE_CHARS

        def add(i: Optional[int], reason: str) -> None:
            nonlocal chars
            if i is None or i in have or len(items) >= cap_n:
                return
            c = self.chunks[i]
            if chars + len(c["text"]) > cap_c:
                return
            have.add(i)
            chars += len(c["text"])
            items.append(EvChunk(c, reason, float(fused[i]), len(items)))

        core = list(items)
        # 1) перекрёстные ссылки внутри документа
        for it in core[:4]:
            if self.docs[it.chunk["doc_id"]]["doc_type"] == "law":
                continue
            for x in it.chunk.get("xrefs", []):
                add(self.clause_map.get((it.chunk["doc_id"], x)), f"ссылка на п. {x}")
        # 2) связанные документы и редакции
        seen_docs: list[str] = []
        for it in core:
            if it.chunk["doc_id"] not in seen_docs:
                seen_docs.append(it.chunk["doc_id"])
        for did in seen_docs[:2]:
            m = self.docs[did]
            own = {cl for it in core if it.chunk["doc_id"] == did for cl in it.chunk.get("clauses", [])}
            for rel in m.get("relations", []):
                tgt = rel["target"]
                if tgt not in self.by_doc:
                    continue
                if rel["type"] == "amends" and rel["dir"] == "in":
                    add(self._best_in_doc(tgt, s_text, fused, set(), have), f"изменено допсоглашением ({self.docs[tgt]['label']})")
                elif rel["type"] == "amends" and rel["dir"] == "out":
                    add(self._best_in_doc(tgt, s_text, fused, set(), have), f"изменяемый документ ({self.docs[tgt]['label']})")
                elif rel["type"] == "supplements":
                    add(self._best_in_doc(tgt, s_text, fused, set(), have), f"связанный документ ({self.docs[tgt]['label']})")
            if m.get("superseded_by"):
                tgt = m["superseded_by"]
                add(self._best_in_doc(tgt, s_text, fused, own, have), f"актуальная редакция ({self.docs[tgt]['label']})")
            if m.get("supersedes") and ti["past"]:
                tgt = m["supersedes"]
                add(self._best_in_doc(tgt, s_text, fused, own, have), f"прежняя редакция ({self.docs[tgt]['label']})")
        # 2b) начало статьи закона: общее правило/заголовок обычно в первом фрагменте
        for did in seen_docs[:1]:
            if self.docs[did]["doc_type"] == "law":
                add(self.by_doc[did][0], "начало статьи")
        # 3) соседние чанки для двух лучших (вторая половина мульти-фактных пунктов)
        for it in core[:2]:
            did, o = it.chunk["doc_id"], it.chunk["ord"]
            ids = self.by_doc[did]
            pos = next((p for p, i in enumerate(ids) if self.chunks[i]["ord"] == o), None)
            if pos is None:
                continue
            nb = [ids[p] for p in (pos + 1, pos - 1) if 0 <= p < len(ids)]
            nb.sort(key=lambda i: -(s_text[i] + 40 * fused[i]))
            if nb and (s_text[nb[0]] > 0 or self.docs[did]["doc_type"] != "law"):
                add(nb[0], "соседний фрагмент")
        return items

    def _idx(self, chunk: dict) -> int:
        return self._id_to_idx[chunk["chunk_id"]]

    @property
    def _id_to_idx(self) -> dict[str, int]:
        m = self.__dict__.get("_idmap")
        if m is None:
            m = self.__dict__["_idmap"] = {c["chunk_id"]: i for i, c in enumerate(self.chunks)}
        return m

    def _order(self, items: list[EvChunk]) -> list[EvChunk]:
        first_rank: dict[str, int] = {}
        for p, it in enumerate(items):
            first_rank.setdefault(it.chunk["doc_id"], p)
        return sorted(items, key=lambda it: (first_rank[it.chunk["doc_id"]], it.chunk["ord"]))


def _alias_key(s: str) -> str:
    return normalize_text(s).lower()


# ------------------------------------------------------------- загрузка

def index_dirs(cfg: Config) -> list[Path]:
    script = Path(__file__).resolve().parent
    roots = [Path.cwd(), Path(cfg.data_dir), script, script / "index"]
    out, seen = [], set()
    for r in roots:
        try:
            r = r.resolve()
        except OSError:
            continue
        if r not in seen:
            seen.add(r)
            out.append(r)
    return out


def load_prebuilt(cfg: Config) -> Optional[ingest.IndexData]:
    for root in index_dirs(cfg):
        d = ingest.load_index_dir(root)
        if d is not None and d.chunks:
            return d
    return None


def get_index_data(cfg: Config, t0: float) -> Optional[ingest.IndexData]:
    """Предсобранный индекс → (если есть kb.zip) инкрементальная синхронизация с ним."""
    prebuilt = load_prebuilt(cfg)
    kb = ingest.find_kb_zip()
    if kb is None:
        return prebuilt
    try:
        documents = ingest.load_documents(kb)
        data, stats = ingest.build_data(documents, prebuilt)
        print(f"Индекс: kb.zip синхронизирован (переиспользовано {len(stats['reused'])}, "
              f"пересобрано {len(stats['rebuilt'])}, удалено {len(stats['removed'])})",
              file=sys.stderr)
        return data
    except Exception as e:  # битый kb.zip не должен ронять прогон
        print(f"kb.zip не обработан: {type(e).__name__}", file=sys.stderr)
        return prebuilt


def load_embeddings(data: ingest.IndexData, cfg: Config, client: Optional[LlmClient],
                    budget_s: float = STARTUP_EMBED_BUDGET_S) -> Optional[np.ndarray]:
    """Эмбеддинги чанков: дисковый кэш → иначе батчами через API в тайм-боксе."""
    if client is None or not cfg.embedder_ready:
        return None
    texts = [embed_text(c) for c in data.chunks]
    key = sha1(cfg.emb_model + "|" + "\n".join(texts))[:16]
    base = cfg.cache_dir or os.path.join(tempfile.gettempdir(), "meridian_cache")
    path = os.path.join(base, f"emb_{key}.npy")
    try:
        if os.path.isfile(path):
            arr = np.load(path)
            if arr.shape[0] == len(texts):
                return arr
    except Exception:
        pass
    t0 = time.time()
    deadline = t0 + budget_s
    arr = None
    for attempt in range(2):  # один повтор: на старте возможен кратковременный 429/5xx
        try:
            arr = client.embed(texts, deadline=deadline)
            break
        except ApiError as e:
            if attempt == 0 and time.time() + 8 < deadline:
                time.sleep(4)
                continue
            print(f"Эмбеддинги недоступны ({type(e).__name__}) — поиск без dense", file=sys.stderr)
            return None
    try:
        os.makedirs(base, exist_ok=True)
        np.save(path, arr)
    except OSError:
        pass
    print(f"Эмбеддинги чанков: {arr.shape} за {time.time() - t0:.1f} с", file=sys.stderr)
    return arr
