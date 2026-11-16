# -*- coding: utf-8 -*-
"""API-клиент (chat / embeddings / rerank) с устойчивой обработкой ошибок (п.14).

- Транспорт: httpx (keep-alive), при его отсутствии — urllib (stdlib).
- Ретраи на 429/5xx/сетевые сбои/тайм-ауты: максимум max_retries (по умолчанию 2 —
  регламент запрещает неограниченные повторы), экспоненциальная пауза с джиттером,
  уважение заголовка Retry-After (кламп), учёт дедлайна.
- Общий «кулдаун» на 429: все потоки приостанавливаются, а не штурмуют API.
- Лимит одновременных запросов (семафор) = max_workers.
- Circuit breaker на канал: после серии подряд неудач канал временно закрывается,
  вызывающий код деградирует (реранк → RRF, judge → пропуск).
- 4xx (кроме 408/429) не ретраятся; ошибки разбираются по типам.
- Тайминги каждого вызова идут в runtime_utils.STATS.
"""
from __future__ import annotations

import json
import random
import re
import threading
import time
import urllib.error
import urllib.request
from typing import Any, Iterable, List, Mapping, Optional

import numpy as np

from config import Config, EMBED_BATCH_SIZE
from runtime_utils import STATS

try:  # httpx есть на платформе; локально может отсутствовать
    import httpx  # type: ignore
except Exception:  # pragma: no cover
    httpx = None  # type: ignore

RETRYABLE_STATUS = {408, 425, 429, 500, 502, 503, 504}
MAX_RETRY_AFTER_S = 8.0


class ApiError(Exception):
    pass


class ApiStatusError(ApiError):
    def __init__(self, message: str, status: Optional[int] = None):
        super().__init__(message)
        self.status = status


class ApiTimeoutError(ApiError):
    pass


class ApiDeadlineError(ApiError):
    """Не хватает времени до дедлайна — вызов не делался."""


class CircuitOpenError(ApiError):
    pass


class JsonParseError(ApiError):
    def __init__(self, raw: str, cause: Optional[BaseException] = None):
        super().__init__(f"не удалось разобрать JSON: {str(cause)[:200]}")
        self.raw = raw


def join_content(content: Any) -> str:
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
        return "".join(parts)
    return ""


_TRAILING_COMMA = re.compile(r",\s*([}\]])")
_FENCE = re.compile(r"```(?:json)?\s*(.*?)```", re.S)


def parse_lenient_json(text: str) -> Any:
    raw = (text or "").strip()
    if not raw:
        raise JsonParseError(raw, ValueError("пустой ответ"))
    candidates = [raw]
    fence = _FENCE.search(raw)
    if fence:
        candidates.append(fence.group(1).strip())
    first = min((i for i in (raw.find("{"), raw.find("[")) if i != -1), default=-1)
    if first > 0:
        candidates.append(raw[first:])
    last = max(raw.rfind("}"), raw.rfind("]"))
    if first >= 0 and last > first:
        candidates.append(raw[first:last + 1])
    first_exc: Optional[BaseException] = None
    for cand in candidates:
        for variant in (cand, _TRAILING_COMMA.sub(lambda m: m.group(1), cand)):
            try:
                return json.loads(variant)
            except json.JSONDecodeError as e:
                first_exc = first_exc or e
    raise JsonParseError(raw, first_exc)


class _Resp:
    def __init__(self, status: int, text: str, headers: Mapping[str, str]):
        self.status_code = status
        self.text = text
        self.headers = {k.lower(): v for k, v in headers.items()}

    def json(self) -> Any:
        return json.loads(self.text)


class _Breaker:
    def __init__(self, threshold: int = 5, open_s: float = 20.0) -> None:
        self.threshold, self.open_s = threshold, open_s
        self.fails = 0
        self.open_until = 0.0
        self._lock = threading.Lock()

    def allow(self) -> bool:
        return time.time() >= self.open_until

    def ok(self) -> None:
        with self._lock:
            self.fails = 0

    def fail(self) -> None:
        with self._lock:
            self.fails += 1
            if self.fails >= self.threshold:
                self.open_until = time.time() + self.open_s
                self.fails = 0


class LlmClient:
    def __init__(self, cfg: Config, *, transport: Any = None,
                 timeout_s: Optional[float] = None, pause_s: tuple = (1.0, 2.0),
                 max_retries: int = 2, force_urllib: bool = False):
        self.cfg = cfg
        self.pause_s = pause_s
        self.max_retries = max(0, min(max_retries, 3))
        self.timeout_s = cfg.request_timeout_s if timeout_s is None else timeout_s
        self.stats = {"chat": 0, "embed_calls": 0, "rerank": 0, "retries": 0,
                      "http_429": 0, "http_5xx": 0, "timeouts": 0}
        self._slock = threading.Lock()
        self._cooldown_until = 0.0
        self._sem = threading.BoundedSemaphore(max(2, cfg.max_workers + 1))
        self._breakers = {"chat": _Breaker(6, 15.0), "embed": _Breaker(4, 30.0),
                          "rerank": _Breaker(3, 45.0)}
        self._http = None
        if httpx is not None and not force_urllib:
            try:
                self._http = httpx.Client(
                    base_url=cfg.base_url, timeout=self.timeout_s, transport=transport,
                    limits=httpx.Limits(max_connections=16, max_keepalive_connections=8))
            except Exception:
                self._http = None

    def _inc(self, key: str, n: int = 1) -> None:
        with self._slock:
            self.stats[key] = self.stats.get(key, 0) + n

    def close(self) -> None:
        if self._http is not None:
            try:
                self._http.close()
            except Exception:
                pass

    def __enter__(self):
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ---- транспорт ----
    def _send(self, path: str, payload: dict, api_key: str, timeout: float) -> _Resp:
        headers = {"Authorization": f"Bearer {api_key}", "Content-Type": "application/json"}
        if self._http is not None:
            r = self._http.post(path, json=payload, headers=headers, timeout=timeout)
            return _Resp(r.status_code, r.text, dict(r.headers))
        req = urllib.request.Request(
            self.cfg.base_url + path, data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
            headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout) as r:  # noqa: S310
                return _Resp(r.status, r.read().decode("utf-8", "replace"), dict(r.headers))
        except urllib.error.HTTPError as e:
            body = e.read().decode("utf-8", "replace") if hasattr(e, "read") else ""
            return _Resp(e.code, body, dict(e.headers or {}))

    @staticmethod
    def _is_timeout(e: BaseException) -> bool:
        if httpx is not None and isinstance(e, httpx.TimeoutException):
            return True
        return isinstance(e, (TimeoutError,)) or "timed out" in str(e).lower()

    @staticmethod
    def _is_network(e: BaseException) -> bool:
        if httpx is not None and isinstance(e, httpx.TransportError):
            return True
        return isinstance(e, (urllib.error.URLError, ConnectionError, OSError))

    def _wait_cooldown(self, deadline: Optional[float]) -> None:
        wait = self._cooldown_until - time.time()
        if wait > 0:
            if deadline is not None and time.time() + wait > deadline:
                raise ApiDeadlineError("кулдаун 429 выходит за дедлайн")
            time.sleep(min(wait, MAX_RETRY_AFTER_S))

    def _post_retry(self, path: str, payload: dict, api_key: str, channel: str,
                    retries: Optional[int] = None, deadline: Optional[float] = None,
                    timeout_s: Optional[float] = None) -> dict:
        br = self._breakers[channel]
        if not br.allow():
            raise CircuitOpenError(f"{channel}: канал временно закрыт после серии сбоев")
        retries = self.max_retries if retries is None else max(0, min(retries, 3))
        last: Optional[BaseException] = None
        t_call = time.perf_counter()
        for attempt in range(retries + 1):
            timeout = timeout_s or self.timeout_s
            if deadline is not None:
                left = deadline - time.time()
                if left < 3:
                    raise ApiDeadlineError(f"{path}: до дедлайна {left:.0f} с")
                timeout = max(3.0, min(timeout, left - 1))
            if attempt:
                self._inc("retries")
                pause = random.uniform(*self.pause_s) * (2 ** (attempt - 1))
                if isinstance(last, ApiStatusError) and getattr(last, "retry_after", None):
                    pause = max(pause, min(last.retry_after, MAX_RETRY_AFTER_S))  # type: ignore[attr-defined]
                if deadline is not None and time.time() + pause > deadline - 3:
                    break
                time.sleep(pause)
            self._wait_cooldown(deadline)
            try:
                with self._sem:
                    resp = self._send(path, payload, api_key, timeout)
                if resp.status_code in RETRYABLE_STATUS:
                    err = ApiStatusError(f"{path}: HTTP {resp.status_code}: {resp.text[:160]}",
                                         status=resp.status_code)
                    ra = resp.headers.get("retry-after", "")
                    try:
                        err.retry_after = float(ra)  # type: ignore[attr-defined]
                    except ValueError:
                        pass
                    if resp.status_code == 429:
                        self._inc("http_429")
                        cool = min(getattr(err, "retry_after", 0) or random.uniform(*self.pause_s),
                                   MAX_RETRY_AFTER_S)
                        self._cooldown_until = max(self._cooldown_until, time.time() + cool)
                    else:
                        self._inc("http_5xx")
                    last = err
                    continue
                if resp.status_code != 200:
                    br.ok()  # клиентская ошибка — не сбой канала
                    raise ApiStatusError(f"{path}: HTTP {resp.status_code}: {resp.text[:200]}",
                                         status=resp.status_code)
                try:
                    data = resp.json()
                except ValueError as e:
                    last = ApiError(f"{path}: невалидный JSON ответа")
                    continue
                br.ok()
                STATS.add(f"api.{channel}", time.perf_counter() - t_call)
                return data
            except ApiError as e:
                if isinstance(e, (ApiDeadlineError, CircuitOpenError)):
                    raise
                if isinstance(e, ApiStatusError) and e.status not in RETRYABLE_STATUS:
                    raise
                last = e
            except Exception as e:  # сетевые/тайм-ауты
                if self._is_timeout(e):
                    self._inc("timeouts")
                    last = ApiTimeoutError(f"{path}: тайм-аут {timeout:.0f} с")
                elif self._is_network(e):
                    last = ApiTimeoutError(f"{path}: сетевой сбой: {type(e).__name__}")
                else:
                    raise ApiError(f"{path}: {type(e).__name__}: {str(e)[:120]}") from e
        br.fail()
        STATS.add(f"api.{channel}.failed", time.perf_counter() - t_call)
        if isinstance(last, ApiError):
            raise last
        raise ApiError(f"{path}: исчерпаны ретраи")

    # ---- chat ----
    def chat(self, messages: Iterable[Mapping], *, max_tokens: Optional[int] = None,
             temperature: Optional[float] = None, model: Optional[str] = None,
             deadline: Optional[float] = None, retries: Optional[int] = None) -> str:
        payload = {
            "model": model or self.cfg.llm_model,
            "messages": [{"role": m.get("role", "user"), "content": m.get("content", "")}
                         for m in messages],
            "temperature": self.cfg.temperature if temperature is None else temperature,
            "max_tokens": self.cfg.max_tokens if max_tokens is None
            else min(max_tokens, self.cfg.max_tokens),
        }
        data = self._post_retry("/chat/completions", payload, self.cfg.llm_api_key, "chat",
                                retries=retries, deadline=deadline)
        self._inc("chat")
        try:
            content = data["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as e:
            raise ApiError(f"/chat/completions: неожиданный формат: {str(data)[:160]}") from e
        return join_content(content)

    def chat_json(self, messages, **kwargs):
        return parse_lenient_json(self.chat(messages, **kwargs))

    # ---- embeddings ----
    def embed(self, texts: List[str], *, model: Optional[str] = None,
              deadline: Optional[float] = None) -> np.ndarray:
        texts = [str(t) for t in texts]
        if not texts:
            raise ValueError("embed: пустой список")
        model = model or self.cfg.emb_model
        rows: List[Optional[np.ndarray]] = [None] * len(texts)
        dim: Optional[int] = None
        for start in range(0, len(texts), EMBED_BATCH_SIZE):
            chunk = texts[start:start + EMBED_BATCH_SIZE]
            data = self._post_retry("/embeddings", {"model": model, "input": chunk,
                                                    "encoding_format": "float"},
                                    self.cfg.embedder_api_key, "embed", deadline=deadline)
            self._inc("embed_calls")
            items = data.get("data") or []
            if not items:
                raise ApiError("/embeddings: пустой data")
            for pos, item in enumerate(items):
                try:
                    idx = int(item.get("index", pos))
                    vec = np.asarray(item["embedding"], dtype=np.float64)
                except (KeyError, ValueError, TypeError, AttributeError) as e:
                    raise ApiError("/embeddings: битый item") from e
                if dim is None:
                    dim = vec.shape[0]
                if vec.shape[0] != dim or not 0 <= idx < len(chunk):
                    raise ApiError("/embeddings: размерность/индекс")
                rows[start + idx] = vec
        if any(r is None for r in rows):
            raise ApiError("/embeddings: часть векторов не вернулась")
        out = np.vstack(rows)  # type: ignore[arg-type]
        norms = np.linalg.norm(out, axis=1, keepdims=True)
        norms[norms == 0] = 1.0
        return out / norms

    # ---- rerank ----
    def rerank(self, query: str, documents: List[str], top_n: int = 8,
               model: Optional[str] = None, deadline: Optional[float] = None) -> List[dict]:
        payload = {"model": model or self.cfg.reranker_model, "query": query,
                   "documents": [str(d) for d in documents], "top_n": top_n}
        data = self._post_retry("/rerank", payload, self.cfg.reranker_api_key, "rerank",
                                retries=1, deadline=deadline)
        self._inc("rerank")
        items = data.get("results") or data.get("data") or []
        out, seen = [], set()
        for item in items:
            try:
                idx = int(item["index"])
            except (KeyError, ValueError, TypeError) as e:
                raise ApiError("/rerank: битый item") from e
            if not 0 <= idx < len(documents) or idx in seen:
                continue
            seen.add(idx)
            score = item.get("relevance_score", item.get("score"))
            try:
                score = None if score is None else float(score)
            except (TypeError, ValueError):
                score = None
            out.append({"index": idx, "relevance_score": score})
        return out
