# -*- coding: utf-8 -*-
"""API-клиент прокси (§2.5): chat / embed / rerank + repair JSON + пробы старта.

Правила:
- ретраи: 1–2 повтора на 429/5xx/сетевые сбои, пауза 1–2 с (безлимитные
  повторные запросы запрещены регламентом);
- тайм-аут вызова 30–45 с (90 с стартера — много при дедлайне 16 мин);
- токен: Authorization: Bearer <ключ>; ключи никогда не логируются;
- эмбеддинги: маппить по item["index"], а не по порядку; L2-нормализация
  на клиенте; батчи ≤ 64 текстов;
- реранк: results или data; relevance_score может отсутствовать (⚠ H7) —
  в этом случае score = None, confidence живёт без rerank-фичи;
- content у chat может быть строкой или списком блоков — склеиваем.
"""
from __future__ import annotations

import json
import random
import re
import time
from typing import Any, Iterable, List, Mapping, Optional

import httpx
import numpy as np

from config import Config, EMBED_BATCH_SIZE

RETRYABLE_STATUS = {429, 500, 502, 503, 504}

# Замыкание: JSON-реплей с предохранителями §17.4-Мок-мода на пинги. Замыкание: json


class ApiError(Exception):
    """Базовая ошибка API-клиента."""


class ApiStatusError(ApiError):
    """HTTP-статус вне ретраемого/удачного множества или исчерпаны ретраи."""

    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class ApiTimeoutError(ApiError):
    """Тайм-аут вызова (включая исчерпание ретраев на сетевых сбоях)."""


class JsonParseError(ApiError):
    """Ответ не разбирается как JSON даже после repair (§10.4, шаги 1–3)."""

    def __init__(self, raw: str, cause: Optional[BaseException] = None):
        super().__init__(f"не удалось разобрать JSON: {str(cause)[:200]}")
        self.raw = raw


def join_content(content: Any) -> str:
    """content = строка ИЛИ список блоков (§2.5) → плоская строка."""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: List[str] = []
        for block in content:
            if isinstance(block, str):
                parts.append(block)
            elif isinstance(block, Mapping):
                text = block.get("text", block.get("content", ""))
                if isinstance(text, str) and text:
                    parts.append(text)
        return "".join(parts)  # блоки — сплошной текст, без разделителя
    return ""


_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_lenient_json(text: str) -> Any:
    """Repair-парсер §10.4 (шаги 1–3). Шаг 4 (повтор LLM) делает вызывающая сторона.

    Попытки: прямой json.loads → ```json-блок → срез от первой {/[ →
    косметика хвостовых запятых на каждом кандидате.
    """
    raw = (text or "").strip()
    if not raw:
        raise JsonParseError(raw, ValueError("пустой ответ"))

    candidates: List[str] = [raw]
    fence = _FENCE.search(raw)
    if fence:
        candidates.append(fence.group(1).strip())
    first = min((i for i in (raw.find("{"), raw.find("[")) if i != -1), default=-1)
    if first > 0:
        candidates.append(raw[first:])

    first_exc: Optional[BaseException] = None
    for cand in candidates:
        variants = [cand, re.sub(_TRAILING_COMMA, lambda m: m.group(1), cand)]
        for variant in variants:
            try:
                return json.loads(variant)
            except json.JSONDecodeError as e:
                first_exc = first_exc or e
    raise JsonParseError(raw, first_exc)


class LlmClient:
    """HTTP-клиент трёх API (§2.5). transport — для тестов с httpx.MockTransport."""

    def __init__(
        self,
        cfg: Config,
        *,
        transport: Optional[httpx.BaseTransport] = None,
        timeout_s: Optional[float] = None,
        pause_s: tuple = (1.0, 2.0),
        max_retries: int = 2,
    ):
        self.cfg = cfg
        self.pause_s = pause_s
        self.max_retries = max_retries  # 1–2 ретрая §2.5; 0 — для тайм-аут-тестов
        self.stats = {"chat": 0, "embed_calls": 0, "rerank": 0, "retries": 0}
        self._http = httpx.Client(
            base_url=cfg.base_url,
            timeout=cfg.request_timeout_s if timeout_s is None else timeout_s,
            transport=transport,
        )

    def close(self) -> None:
        self._http.close()

    def __enter__(self) -> "LlmClient":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # --- низкоуровневый POST с ретраями ---

    def _post_retry(self, path: str, payload: dict, api_key: str,
                    retries: Optional[int] = None) -> dict:
        retries = self.max_retries if retries is None else max(0, retries)
        last_exc: Optional[BaseException] = None
        for attempt in range(retries + 1):
            if attempt:
                self.stats["retries"] += 1
                time.sleep(random.uniform(*self.pause_s))
            try:
                resp = self._http.post(
                    path,
                    json=payload,
                    headers={
                        "Authorization": f"Bearer {api_key}",
                        "Content-Type": "application/json",
                    },
                )
                if resp.status_code in RETRYABLE_STATUS:
                    last_exc = ApiStatusError(
                        f"{path}: HTTP {resp.status_code}: {resp.text[:200]}",
                        status=resp.status_code,
                    )
                    continue  # ретрай
                if resp.status_code != 200:
                    raise ApiStatusError(
                        f"{path}: HTTP {resp.status_code}: {resp.text[:200]}",
                        status=resp.status_code,
                    )
                return resp.json()
            except (httpx.TimeoutException, httpx.NetworkError) as e:
                last_exc = e
                continue  # ретрай на сетевых сбоях
        if isinstance(last_exc, httpx.TimeoutException) or (
            isinstance(last_exc, httpx.NetworkError)
        ):
            raise ApiTimeoutError(f"{path}: сетевой тайм-аут/сбой: {last_exc}") from last_exc
        if isinstance(last_exc, ApiError):
            raise last_exc
        raise ApiError(f"{path}: исчерпаны ретраи: {last_exc}")

    # --- Генерация (§2.5) ---

    def chat(self, messages: Iterable[Mapping], *, max_tokens: Optional[int] = None,
             temperature: Optional[float] = None,
             model: Optional[str] = None) -> str:
        payload = {
            "model": model or self.cfg.llm_model,
            "messages": [
                {"role": m.get("role", "user"), "content": m.get("content", "")}
                for m in messages
            ],
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": self.cfg.max_tokens if max_tokens is None
            else min(max_tokens, self.cfg.max_tokens),
        }
        data = self._post_retry("/chat/completions", payload, self.cfg.llm_api_key)
        self.stats["chat"] += 1
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ApiError(
                f"/chat/completions: неожиданный формат ответа: {str(data)[:200]}"
            ) from e
        return join_content(content)

    def chat_json(self, messages, **kwargs):
        """chat() + repair-парсинг JSON (§10.4). Throws JsonParseError."""
        raw = self.chat(messages, **kwargs)
        return parse_lenient_json(raw)

    # --- Эмбеддинги (§2.5) ---

    def embed(self, texts: List[str], *, model: Optional[str] = None) -> np.ndarray:
        """Батчи ≤64, маппинг по index, L2-нормализация. → (n, dim) float64."""
        texts = [str(t) for t in texts]
        if not texts:
            raise ValueError("embed: пустой список текстов")
        out = np.zeros((len(texts), 0), dtype=np.float64)
        dim: Optional[int] = None
        model = model or self.cfg.emb_model
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            chunk = texts[start:start + EMBED_BATCH_SIZE]
            payload = {"model": model, "input": chunk, "encoding_format": "float"}
            data = self._post_retry("/embeddings", payload, self.cfg.embedder_api_key)
            self.stats["embed_calls"] += 1
            items = data.get("data") or []
            if not items:
                raise ApiError("/embeddings: пустой ответ data")
            batch = np.zeros((len(chunk), dim if dim is not None else 0),
                             dtype=np.float64)
            for item in items:
                try:
                    idx = int(item["index"])
                    vec = np.asarray(item["embedding"], dtype=np.float64)
                except (KeyError, ValueError, TypeError) as e:
                    raise ApiError(f"/embeddings: битый item: {str(item)[:120]}") from e
                if dim is None:
                    dim = vec.shape[0]
                    out = np.zeros((len(texts), dim), dtype=np.float64)
                    batch = np.zeros((len(chunk), dim), dtype=np.float64)
                elif vec.shape[0] != dim:
                    raise ApiError(f"/embeddings: размерность {vec.shape[0]} != {dim}")
                if not 0 <= idx < len(chunk):
                    raise ApiError(f"/embeddings: index {idx} вне батча")
                batch[idx] = vec
            out[start:start + len(chunk)] = batch
        # L2-нормализация на клиенте (как стартер)
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms

    # --- Реранк (§2.5) ---

    def rerank(self, query: str, documents: List[str], top_n: int = 8,
               model: Optional[str] = None) -> List[dict]:
        """→ [{"index": int, "relevance_score": float|None}] в порядке релевантности."""
        payload = {
            "model": model or self.cfg.reranker_model,
            "query": query,
            "documents": [str(d) for d in documents],
            "top_n": top_n,
        }
        data = self._post_retry("/rerank", payload, self.cfg.reranker_api_key)
        self.stats["rerank"] += 1
        items = data.get("results") or data.get("data") or []
        out = []
        for item in items:
            try:
                idx = int(item["index"])
            except (KeyError, ValueError, TypeError) as e:
                raise ApiError(f"/rerank: битый item: {str(item)[:120]}") from e
            score = item.get("relevance_score")
            out.append({
                "index": idx,
                "relevance_score": None if score is None else float(score),
            })
        return out

    # --- Стартовые пробы ≤30 с (§16.3) ---

    def probe_all(self) -> dict:
        result: dict = {
            "llm_ok": False,
            "emb_ok": False,
            "emb_dim": None,
            "rerank_ok": False,
            "rerank_score": False,
            "rerank_top_n_unrestricted": False,
            "warnings": [],
        }
        t0 = time.monotonic()
        try:
            result["llm_ok"] = bool(self.chat(
                [{"role": "user", "content": "ping"}], max_tokens=5, temperature=0.0
            ).strip())
        except ApiError as e:
            result["warnings"].append(f"llm: {e}")
        try:
            vecs = self.embed([
                "срочный трудовой договор по соглашению сторон",
                "договор аренды нежилого помещения",
            ])
            result["emb_ok"] = vecs.shape[0] == 2
            result["emb_dim"] = int(vecs.shape[1])
        except ApiError as e:
            result["warnings"].append(f"embed: {e}")
        try:
            rr = self.rerank("проверка формата реранка", ["документ А", "документ Б"], top_n=8)
            result["rerank_ok"] = bool(rr)
            result["rerank_score"] = all(r["relevance_score"] is not None for r in rr)
            result["rerank_top_n_unrestricted"] = len(rr) == 2
        except ApiError as e:
            result["warnings"].append(f"rerank: {e}")
        result["elapsed_s"] = round(time.monotonic() - t0, 1)
        return result
