# -*- coding: utf-8 -*-
"""Retrieval: exact-ref ∪ BM25(bm25s) ∪ dense(faiss/IP) → RRF(k=60) → rerank
→ expand (parent-раздел, refs, edges) (§9).

Правила: реранк по «standalone-вопрос + text_ctx», top_n=8 независимо от
RERANKER_TOP_N; compromised-чанки никогда не входят в пул; лимит Reason
6–8 чанков ≤6000 символов; дизамбигуация компаний rapidfuzz (T7).
"""
# TODO(Фаза 2): каналы + RRF + реранк + expand.
