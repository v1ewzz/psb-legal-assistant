# -*- coding: utf-8 -*-
"""Интеграционные тесты LlmClient против мок-API (§17.4, DoD Фазы 0: ≥10 тестов).

Мок поднимается в отдельном потоке (uvicorn), клиент ходит по HTTP по-настоящему:
ретраи, паузы, тайм-ауты, маппинг по index — всё как против живого прокси.
"""
from __future__ import annotations

import socket
import sys
import time
import threading

import httpx
import numpy as np
import pytest
import uvicorn

sys.path.insert(0, "scripts")  # conftest уже добавил, но для чтения из IDE — повторим

import mock_api  # noqa: E402  (scripts/mock_api.py)
from llm_client import (  # noqa: E402
    ApiError,
    ApiStatusError,
    ApiTimeoutError,
    JsonParseError,
    LlmClient,
    parse_lenient_json,
)
from config import Config  # noqa: E402


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@pytest.fixture(scope="module")
def mock_base_url():
    port = _free_port()
    server = uvicorn.Server(
        uvicorn.Config(mock_api.app, host="127.0.0.1", port=port, log_level="critical")
    )
    thread = threading.Thread(target=server.run, daemon=True)
    thread.start()
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    while time.monotonic() < deadline:
        try:
            httpx.get(f"{base}/health", timeout=1.0)
            break
        except Exception:
            time.sleep(0.1)
    else:
        pytest.fail("мок-сервер не поднялся за 15 с")
    yield base
    server.should_exit = True
    thread.join(timeout=5)


@pytest.fixture()
def client(mock_base_url) -> LlmClient:
    cfg = mock_config(mock_base_url)
    c = LlmClient(cfg)
    yield c
    c.close()


def mock_config(base_url: str, **overrides) -> Config:
    defaults = dict(
        base_url=base_url,
        llm_api_key="test-key",
        embedder_api_key="test-key",
        reranker_api_key="test-key",
        llm_model="mock-llm",
        emb_model="mock-embedder",
        reranker_model="mock-reranker",
        request_timeout_s=5.0,
    )
    defaults.update(overrides)
    return Config(**defaults)


def test_chat_ok(client: LlmClient):
    answer = client.chat([{"role": "user", "content": "Привет"}])
    assert answer.startswith("mock-ok:")


def test_chat_joins_content_blocks(client: LlmClient):
    """content может быть списком блоков (§2.5) — клиент склеивает."""
    answer = client.chat([{"role": "user", "content": "!!blocks нужны блоки"}])
    assert answer == "Первая часть. Вторая часть."


def test_chat_json_repair_fenced_trailing_commas(client: LlmClient):
    """Repair §10.4: ```json-блок + хвостовые запятые → валидный объект."""
    obj = client.chat_json([{"role": "user", "content": "дай JSON !!badjson"}])
    assert obj == {"broken": [1, 2]}


def test_embed_norm_and_index_mapping(client: LlmClient):
    """L2-нормализация на клиенте; маппинг по index (мок отдаёт data в реверсе)."""
    texts = ["первый текст", "второй текст", "срочный трудовой договор"]
    vecs = client.embed(texts)
    assert vecs.shape == (3, 1024)
    assert np.allclose(np.linalg.norm(vecs, axis=1), 1.0)
    # пер-текстовая детерминированность: пересчитать вручную и сравнить
    for i, text in enumerate(texts):
        import hashlib
        seed = int.from_bytes(
            hashlib.sha256(f"emb|{text}".encode()).digest()[:4], "big")
        rng = np.random.default_rng(seed)
        expected = rng.standard_normal(1024)
        expected /= np.linalg.norm(expected)
        assert np.allclose(vecs[i], expected)


def test_embed_batching(client: LlmClient):
    """Батчи ≤64 (§2.5): 100 текстов → 2 батча, все векторы на местах."""
    texts = [f"текст номер {i}" for i in range(100)]
    vecs = client.embed(texts)
    assert vecs.shape == (100, 1024)
    assert client.stats["embed_calls"] == 2


def test_rerank_top_n_and_order(client: LlmClient):
    rr = client.rerank("какой portraying вопрос", [f"док{i}" for i in range(5)], top_n=3)
    assert len(rr) == 3
    scores = [r["relevance_score"] for r in rr]
    assert all(s is not None for s in scores)
    assert scores == sorted(scores, reverse=True)


def test_rerank_without_relevance_score(client: LlmClient):
    """⚠ H7: часть прокси не отдаёт relevance_score — клиент возвращает None."""
    rr = client.rerank("!!noscore проверка", ["а", "б"], top_n=2)
    assert all(r["relevance_score"] is None for r in rr)


def test_rerank_data_key_variant(client: LlmClient):
    rr = client.rerank("!!datakey тест", ["а", "б"], top_n=2)
    assert len(rr) == 2


def test_retry_then_success(client: LlmClient):
    """Ретраи: первый вызов 500, повторный — успех (§2.5: 1–2 ретрая)."""
    answer = client.chat([{"role": "user", "content": "!!500once"}])
    assert answer.startswith("mock-ok:")
    assert client.stats["retries"] == 1


def test_permanent_500_exhausts_retries(client: LlmClient):
    with pytest.raises(ApiStatusError) as exc:
        client.chat([{"role": "user", "content": "!!500"}])
    assert exc.value.status == 500
    assert client.stats["retries"] == 2  # 1–2 повтора, безлимит запрещён


def test_timeout_raises_fast(mock_base_url):
    """Тайм-аут 0.5 с при задержке мока 2 с → ApiTimeoutError быстро (без ретраев)."""
    cfg = mock_config(mock_base_url, request_timeout_s=0.5)
    slow = LlmClient(cfg, max_retries=0)
    try:
        t0 = time.monotonic()
        with pytest.raises(ApiTimeoutError):
            slow.chat([{"role": "user", "content": "!!delay2"}])
        assert time.monotonic() - t0 < 2.0
    finally:
        slow.close()


def test_probe_all_green(client: LlmClient):
    """Стартовые пробы (§16.3) на моке — все три ЭП живые, {} без релевантности."""
    report = client.probe_all()
    assert report["llm_ok"] and report["emb_ok"] and report["rerank_ok"]
    assert report["emb_dim"] == 1024
    assert not report["rerank_score"] is None
