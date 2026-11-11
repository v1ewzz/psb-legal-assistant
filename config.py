# -*- coding: utf-8 -*-
"""Резолв переменных окружения (§2.4), клампы лимитов, константы решения.

Правила чтения (§2.4):
- первый непустой из альтернативных имён (LLM_BASE_URL / BASE_URL / OPENAI_BASE_URL);
- ключи никогда не логируются и не попадают в сабмит-ZIP;
- лимиты клампятся: TOP_K 1–8, RERANKER_TOP_N 1–5, MAX_TOKENS 128–8192, MAX_WORKERS 1–8;
- читаем именно «MAX_WORKERS», не «WORKERS» (баг первой итерации);
- LLM_MODEL всегда из env; дефолт deepseek/deepseek-v4.1-flash — только локальная разработка.
"""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import Mapping, Optional

# --- константы решения (§2.2, §16.1) ---
MAX_ANSWER_CHARS = 10_000          # жёсткий предел ответа
RECOMMENDED_ANSWER_WORDS = 150     # рекомендуемая длина (эталон 161–444 символа)
HARD_DEADLINE_MIN = 16             # 16 мин прогона + 4 мин резерва из 20
STARTUP_PROBE_BUDGET_S = 30.0      # суммарный бюджет стартовых проб (§16.3)
REBUILD_INDEX_TIMEBOX_S = 360.0    # тайм-бокс пересборки индекса на старте (§8.3)
EMBED_BATCH_SIZE = 64              # батч эмбеддингов (§2.5)

# --- дефолты моделей (только локальная разработка; на платформе всё из env) ---
DEFAULT_LLM_MODEL = "deepseek/deepseek-v4.1-flash"
DEFAULT_EMB_MODEL = "baai/bge-m3"
DEFAULT_RERANKER_MODEL = "cohere/rerank-5-fast"


def first_nonempty(*values: Optional[str]) -> str:
    """Первое непустое значение после strip(), иначе пустая строка."""
    for v in values:
        if v and v.strip():
            return v.strip()
    return ""


def clamp_int(value, lo: int, hi: int, default: int) -> int:
    """Парсинг с клампом; мусор → дефолт."""
    try:
        n = int(float(str(value).strip()))
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, n))


def clamp_float(value, lo: float, hi: float, default: float) -> float:
    try:
        f = float(str(value).strip())
    except (TypeError, ValueError):
        return default
    return max(lo, min(hi, f))


def _bool_flag(value, default: bool) -> bool:
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да"}


@dataclass
class Config:
    """Резолвленные настройки окружения (§2.4)."""

    data_dir: str = "data"
    base_url: str = ""
    llm_api_key: str = ""
    embedder_api_key: str = ""
    reranker_api_key: str = ""
    llm_model: str = DEFAULT_LLM_MODEL
    emb_model: str = DEFAULT_EMB_MODEL
    reranker_model: str = DEFAULT_RERANKER_MODEL
    top_k: int = 4
    reranker_top_n: int = 2
    max_tokens: int = 2000
    max_workers: int = 2
    # temperature ФОРСИРУЕТСЯ в 0.0 (§12.2): фактический RAG + судья против
    # эталона — сэмплирование только добавляет разброс; env-значение игнорируем
    temperature: float = 0.0
    request_timeout_s: float = 45.0     # тайм-аут вызова API: 30–45 с (§2.5)
    model_from_env: bool = False        # модель реально задана в env (не локальный дефолт)
    t5_premise_gate: bool = False       # гейт ложных предпосылок: T5_GATE=1 (после калибровки)

    @property
    def api_ready(self) -> bool:
        """Есть ли минимальный набор для вызовов API (без мок-режима)."""
        return bool(self.base_url and self.llm_api_key and self.llm_model)

    @property
    def embedder_ready(self) -> bool:
        return bool(self.base_url and self.embedder_api_key and self.emb_model)

    @property
    def reranker_ready(self) -> bool:
        return bool(self.base_url and self.reranker_api_key and self.reranker_model)


def from_env(env: Optional[Mapping[str, str]] = None) -> Config:
    """Собрать Config из окружения по таблице §2.4."""
    env = os.environ if env is None else env
    llm_model_raw = first_nonempty(
        env.get("LLM_MODEL"), env.get("GEN_MODEL"), env.get("OPENAI_MODEL")
    )
    scan_key = first_nonempty(env.get("LLM_API_KEY"), env.get("OPENAI_API_KEY"))

    return Config(
        data_dir=first_nonempty(env.get("DATA_DIR")) or "data",
        base_url=_normalize_base_url(first_nonempty(
            env.get("LLM_BASE_URL"), env.get("BASE_URL"), env.get("OPENAI_BASE_URL")
        )),
        llm_api_key=scan_key,
        embedder_api_key=first_nonempty(env.get("EMBEDDER_API_KEY"), scan_key),
        reranker_api_key=first_nonempty(env.get("RERANKER_API_KEY"), scan_key),
        llm_model=llm_model_raw or DEFAULT_LLM_MODEL,
        emb_model=first_nonempty(env.get("EMB_MODEL")) or DEFAULT_EMB_MODEL,
        reranker_model=first_nonempty(env.get("RERANKER_MODEL")) or DEFAULT_RERANKER_MODEL,
        top_k=clamp_int(env.get("TOP_K"), 1, 8, 4),
        reranker_top_n=clamp_int(env.get("RERANKER_TOP_N"), 1, 5, 2),
        max_tokens=clamp_int(env.get("MAX_TOKENS"), 128, 8192, 2000),
        max_workers=clamp_int(env.get("MAX_WORKERS"), 1, 8, 2),
        temperature=0.0,  # форсировано: детерминизм ответа (§12.2)
        model_from_env=bool(llm_model_raw),
        t5_premise_gate=_bool_flag(env.get("T5_GATE"), False),
    )


def _normalize_base_url(url: str) -> str:
    """rstrip('/') по правилу §2.4."""
    return url.rstrip("/")


def find_input(env: Optional[Mapping[str, str]] = None) -> Path:
    """Поиск questions.csv: DATA_DIR первым, затем script_dir/DATA_DIR, затем cwd (§16.1)."""
    env = os.environ if env is None else env
    data_dir = first_nonempty(env.get("DATA_DIR")) or "data"
    script_dir = Path(__file__).resolve().parent
    candidates = [
        Path(data_dir) / "questions.csv",
        script_dir / data_dir / "questions.csv",
        Path.cwd() / "questions.csv",
    ]
    for p in candidates:
        if p.exists():
            return p
    raise FileNotFoundError("questions.csv не найден: проверь DATA_DIR и cwd")
