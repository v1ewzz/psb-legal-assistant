# -*- coding: utf-8 -*-
"""Конфигурация из переменных окружения + константы решения.

Принципы:
- Модель, адрес API и ключи берутся ТОЛЬКО из окружения; в коде нет ни одного
  имени модели, URL или ключа. Нет переменной → соответствующий канал выключен
  (генерация → экстрактивный режим, эмбеддинги → только BM25, реранк → RRF).
- Платформенные лимиты клампятся: TOP_K 1–8, RERANKER_TOP_N 1–5,
  MAX_TOKENS 128–8192, MAX_WORKERS 1–8.
- Ключи никогда не логируются.
"""
from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Mapping, Optional

# --- константы решения ---
SOLUTION_VERSION = "3.0"
MAX_ANSWER_CHARS = 10_000
HARD_DEADLINE_MIN = 16             # 16 мин прогона + 4 мин резерва из 20
FINAL_WRITE_RESERVE_S = 60.0       # резерв на финальную запись CSV
STARTUP_EMBED_BUDGET_S = 90.0      # тайм-бокс эмбеддинга чанков на старте
REBUILD_INDEX_TIMEBOX_S = 360.0
EMBED_BATCH_SIZE = 64
PERIODIC_WRITE_S = 30.0
MAX_EVIDENCE_CHUNKS = 10           # потолок фрагментов в промпте (после расширений)
MAX_EVIDENCE_CHARS = 9_000
PER_FRAGMENT_CHARS = 2_000


def first_nonempty(*values: Optional[str]) -> str:
    for v in values:
        if v and str(v).strip():
            return str(v).strip()
    return ""


def clamp_int(value, lo: int, hi: int, default: int) -> int:
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


def _flag(value, default: bool) -> bool:
    if value is None or not str(value).strip():
        return default
    return str(value).strip().lower() in {"1", "true", "yes", "on", "да"}


def _normalize_base_url(url: str) -> str:
    return url.rstrip("/")


@dataclass
class Config:
    data_dir: str = "data"
    base_url: str = ""
    llm_api_key: str = ""
    embedder_api_key: str = ""
    reranker_api_key: str = ""
    llm_model: str = ""
    emb_model: str = ""
    reranker_model: str = ""
    top_k: int = 6                  # платформенный кламп 1–8; внутри ещё расширения
    reranker_top_n: int = 5
    max_tokens: int = 2000
    max_workers: int = 4
    temperature: float = 0.0
    request_timeout_s: float = 40.0
    max_input_chars: int = 4000
    # --- флаги возможностей (всё включено по умолчанию, отключаемо env) ---
    hybrid: bool = True             # dense-канал + RRF
    rerank: bool = True
    rerank_pool: int = 14           # сколько кандидатов отдаём реранкеру
    candidate_pool: int = 30        # глубина каждого канала до слияния
    citation_lint: bool = True
    claim_judge: bool = True        # LLM-проверка слабо подтверждённых утверждений
    rewrite_followups: bool = True  # LLM-переформулировка реплик-продолжений
    t5_premise_gate: bool = False
    cache_enabled: bool = True
    cache_dir: str = ""             # пусто → системный tmp
    as_of: str = ""                 # YYYY-MM-DD; пусто → сегодня
    debug: bool = False
    model_from_env: bool = False

    @property
    def api_ready(self) -> bool:
        return bool(self.base_url and self.llm_api_key and self.llm_model)

    @property
    def embedder_ready(self) -> bool:
        return bool(self.hybrid and self.base_url and self.embedder_api_key and self.emb_model)

    @property
    def reranker_ready(self) -> bool:
        return bool(self.rerank and self.base_url and self.reranker_api_key and self.reranker_model)

    @property
    def evidence_k(self) -> int:
        """Сколько «ядровых» фрагментов берём из ранжирования (до расширений)."""
        return max(5, min(8, self.top_k))


def from_env(env: Optional[Mapping[str, str]] = None) -> Config:
    env = os.environ if env is None else env
    llm_model = first_nonempty(env.get("LLM_MODEL"), env.get("GEN_MODEL"), env.get("OPENAI_MODEL"))
    key = first_nonempty(env.get("LLM_API_KEY"), env.get("OPENAI_API_KEY"), env.get("API_KEY"))
    return Config(
        data_dir=first_nonempty(env.get("DATA_DIR")) or "data",
        base_url=_normalize_base_url(first_nonempty(
            env.get("LLM_BASE_URL"), env.get("BASE_URL"), env.get("OPENAI_BASE_URL"))),
        llm_api_key=key,
        embedder_api_key=first_nonempty(env.get("EMBEDDER_API_KEY"), key),
        reranker_api_key=first_nonempty(env.get("RERANKER_API_KEY"), key),
        llm_model=llm_model,
        emb_model=first_nonempty(env.get("EMB_MODEL"), env.get("EMBEDDER_MODEL")),
        reranker_model=first_nonempty(env.get("RERANKER_MODEL")),
        top_k=clamp_int(env.get("TOP_K"), 1, 8, 6),
        reranker_top_n=clamp_int(env.get("RERANKER_TOP_N"), 1, 5, 5),
        max_tokens=clamp_int(env.get("MAX_TOKENS"), 128, 8192, 2000),
        max_workers=clamp_int(env.get("MAX_WORKERS"), 1, 8, 4),
        temperature=clamp_float(env.get("TEMPERATURE"), 0.0, 2.0, 0.0),
        request_timeout_s=clamp_float(env.get("REQUEST_TIMEOUT_S"), 5.0, 90.0, 40.0),
        max_input_chars=clamp_int(env.get("MAX_INPUT_CHARS"), 500, 16000, 4000),
        hybrid=_flag(env.get("HYBRID"), True),
        rerank=_flag(env.get("RERANK"), True),
        rerank_pool=clamp_int(env.get("RERANK_POOL"), 4, 30, 14),
        candidate_pool=clamp_int(env.get("CANDIDATE_POOL"), 10, 100, 30),
        citation_lint=_flag(env.get("CITATION_LINT"), True),
        claim_judge=_flag(env.get("CLAIM_JUDGE"), True),
        rewrite_followups=_flag(env.get("REWRITE_FOLLOWUPS"), True),
        t5_premise_gate=_flag(env.get("T5_GATE"), False),
        cache_enabled=_flag(env.get("CACHE"), True),
        cache_dir=first_nonempty(env.get("CACHE_DIR")),
        as_of=first_nonempty(env.get("AS_OF_DATE")),
        debug=_flag(env.get("DEBUG"), False),
        model_from_env=bool(llm_model),
    )


def find_input(env: Optional[Mapping[str, str]] = None) -> Path:
    """questions.csv: DATA_DIR → script_dir/DATA_DIR → cwd → script_dir."""
    env = os.environ if env is None else env
    data_dir = first_nonempty(env.get("DATA_DIR")) or "data"
    script_dir = Path(__file__).resolve().parent
    for p in (Path(data_dir) / "questions.csv", script_dir / data_dir / "questions.csv",
              Path.cwd() / "questions.csv", script_dir / "questions.csv"):
        if p.exists():
            return p
    raise FileNotFoundError("questions.csv не найден: проверь DATA_DIR и cwd")
