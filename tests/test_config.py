# -*- coding: utf-8 -*-
"""Тесты config.py: резолв env-имён, клампы, find_input (§2.4, §16.1)."""
import pytest

import config
from config import Config, find_input, from_env


def test_env_name_fallbacks():
    env = {"BASE_URL": "http://proxy ", "OPENAI_API_KEY": "k1"}
    cfg = from_env(env)
    assert cfg.base_url == "http://proxy"  # rstrip("/")
    assert cfg.llm_api_key == "k1"
    assert cfg.embedder_api_key == "k1" and cfg.reranker_api_key == "k1"  # fallback на LLM-ключ


def test_base_url_variants():
    cfg = from_env({"LLM_BASE_URL": " http://a/"})
    assert cfg.base_url == "http://a"
    cfg = from_env({"OPENAI_BASE_URL": "http://b"})
    assert cfg.base_url == "http://b"
    assert from_env({}).base_url == ""


def test_model_env_wins_over_default():
    cfg = from_env({"GEN_MODEL": "cohere/x"})
    assert cfg.llm_model == "cohere/x" and cfg.model_from_env
    cfg = from_env({})
    assert cfg.model_from_env is False  # дефолт только для локальной разработки


def test_clamps():
    cfg = from_env({"TOP_K": "100", "RERANKER_TOP_N": "0", "MAX_TOKENS": "99999",
                    "MAX_WORKERS": "-3", "TEMPERATURE": "5"})
    assert (cfg.top_k, cfg.reranker_top_n, cfg.max_tokens, cfg.max_workers,
            cfg.temperature) == (8, 1, 8192, 1, 2.0)


def test_max_workers_name_not_workers():
    """Читаем именно MAX_WORKERS, не WORKERS (баг первой итерации)."""
    assert from_env({"WORKERS": "8"}).max_workers == 2
    assert from_env({"MAX_WORKERS": "8"}).max_workers == 8


def test_garbage_lims_fall_to_defaults():
    cfg = from_env({"TOP_K": "abc", "MAX_WORKERS": "??? ", "MAX_TOKENS": ""})
    assert cfg.top_k == 4 and cfg.max_workers == 2 and cfg.max_tokens == 2000


def test_api_ready_flag():
    assert not from_env({}).api_ready
    assert from_env({"LLM_BASE_URL": "http://x", "LLM_API_KEY": "k"}).api_ready


def test_find_input_cwd(tmp_path, monkeypatch):
    (tmp_path / "questions.csv").write_text("id,question\n1,q\n", encoding="utf-8-sig")
    monkeypatch.chdir(tmp_path)
    assert find_input({}) == tmp_path / "questions.csv"


def test_find_input_data_dir(tmp_path, monkeypatch):
    data = tmp_path / "data"
    data.mkdir()
    (data / "questions.csv").write_text("id,question\n", encoding="utf-8")
    monkeypatch.chdir(tmp_path)
    found = find_input({"DATA_DIR": "data"})
    assert found.resolve() == (data / "questions.csv").resolve()


def test_find_input_missing(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    with pytest.raises(FileNotFoundError):
        find_input({})
