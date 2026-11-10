# -*- coding: utf-8 -*-
"""Мок-API для разработки без платформенного доступа (§17.4): FastAPI на
3 эндпоинта в cohere/OpenAI-совместимом формате (§2.5).

Запуск: cd scripts && python -m uvicorn mock_api:app --port 8000
Затем в .env: LLM_BASE_URL=http://127.0.0.1:8000

Триггеры для тестов (вставляются прямо в payload, детерминированы):
- chat  (в последнем сообщении): "!!delay2" (сон 2 с), "!!500"/"!!429"
  (постоянный сбой), "!!500once" (сбой только первый вызов), "!!badjson"
  (невалидный JSON в ответе), "!!blocks" (content = список блоков), "!!empty".
- embed (в любом тексте): "!!delay2", "!!500". Векторы детерминированы
  текстом (sha256 → seed), список data идёт в РЕВЕРСЕ порядка input —
  вынуждает клиента маппить по index, а не по порядку (§2.5).
- rerank (в query): "!!delay2", "!!500", "!!noscore" (без relevance_score,
  ветка ⚠ H7), "!!datakey" (ответ под ключом data, а не results).

Настроение: chat — заглушки с опозданиями и иногда невалидным JSON (тест
repair); embeddings — детерминированные ортогональные векторы фиксированной
размерности (MOCK_EMB_DIM, дефолт 1024); rerank — детерминированные
скоринги-псевдослучайности. Тесты устойчивости гоняются против мока;
качество — против реального API (⚠ U9).
"""
from __future__ import annotations

import hashlib
import os
import time
from typing import Any, List

import numpy as np
from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

app = FastAPI(title="PSB mock API", version="0.1.0")
EMB_DIM = int(os.getenv("MOCK_EMB_DIM", "1024"))

# Состояние для триггеров "сбой один раз"
_state = {"chat_500once_seen": False}


def _seed(*parts: Any) -> int:
    """Детерминированный 32-битный seed из конкатенации частей."""
    digest = hashlib.sha256("|".join(str(p) for p in parts).encode("utf-8")).digest()
    return int.from_bytes(digest[:4], "big")


def _blocks_to_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        return "".join(
            b.get("text", "") if isinstance(b, dict) else str(b) for b in content
        )
    return ""


def _last_text(body: dict) -> str:
    messages = body.get("messages") or []
    if isinstance(messages, list) and messages:
        return _blocks_to_text(messages[-1].get("content", ""))
    return ""


def _mock_error(status: int, message: str) -> JSONResponse:
    return JSONResponse({"error": {"message": message, "type": "mock_error"}},
                        status_code=status)


# --- Генерация (§2.5) ---

@app.post("/chat/completions")
async def chat_completions(request: Request):
    body = await request.json()
    model = str(body.get("model", ""))
    text = _last_text(body)

    if "!!500once" in text:
        if not _state["chat_500once_seen"]:
            _state["chat_500once_seen"] = True
            return _mock_error(500, "mock: сбой один раз, дальше ок")
        text = text.replace("!!500once", "")  # после первого триггера — обычный текст
    if "!!500" in text or "!!500" in model:
        return _mock_error(500, "mock: принудительный сбой")
    if "!!429" in text:
        return _mock_error(429, "mock: принудительный rate limit")
    if "!!delay2" in text:
        time.sleep(2.0)

    if "!!badjson" in text:
        content: Any = '```json\n{"broken": [1, 2,], }\n```'
    elif "!!blocks" in text:
        content = [
            {"type": "text", "text": "Первая часть."},
            {"type": "text", "text": " Вторая часть."},
        ]
    elif "!!empty" in text:
        content = ""
    else:
        content = f"mock-ok: {text[-40:]}"

    return {
        "choices": [{"message": {"role": "assistant", "content": content},
                     "finish_reason": "stop"}],
        "model": model,
    }


# --- Эмбеддинги (§2.5) ---

@app.post("/embeddings")
async def embeddings(request: Request):
    body = await request.json()
    inp = body.get("input", [])
    if isinstance(inp, str):
        inp = [inp]
    texts: List[str] = [str(t) for t in inp]

    if any("!!500" in t for t in texts):
        return _mock_error(500, "mock: принудительный сбой")
    if any("!!delay2" in t for t in texts):
        time.sleep(2.0)

    vectors = []
    for text in texts:
        rng = np.random.default_rng(_seed("emb", text))
        vec = rng.standard_normal(EMB_DIM)
        vec = vec / np.linalg.norm(vec)
        vectors.append(vec)

    # Важно: data идёт в РЕВЕРСЕ порядка input с корректным index —
    # клиент обязан маппить по index (§2.5), не по порядку.
    data = [
        {"index": i, "object": "embedding", "embedding": vectors[i].tolist()}
        for i in range(len(texts))
    ][::-1]
    return {"object": "list", "model": body.get("model", ""), "data": data}


# --- Реранк (§2.5) ---

@app.post("/rerank")
async def rerank(request: Request):
    body = await request.json()
    query = str(body.get("query", ""))
    documents = [str(d) for d in body.get("documents", [])]
    top_n = body.get("top_n")

    if "!!500" in query:
        return _mock_error(500, "mock: принудительный сбой")
    if "!!delay2" in query:
        time.sleep(2.0)

    results = []
    for i, doc in enumerate(documents):
        item = {"index": i}
        if "!!noscore" not in query:
            item["relevance_score"] = round(1.0 - (_seed("rr", query, doc) % 1000) / 1000.0, 6)
        results.append(item)
    results.sort(key=lambda x: x.get("relevance_score", 0.0), reverse=True)

    if isinstance(top_n, int) and top_n >= 0:
        results = results[:top_n]

    if "!!datakey" in query:
        return {"data": results}
    return {"results": results}


@app.get("/health")
async def health():
    return {"ok": True}
