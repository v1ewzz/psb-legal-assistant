# -*- coding: utf-8 -*-
"""Веб-интерфейс бэкенда: FastAPI + SSE (§18).

Функции: чат со статусами (полный/частичный/отказ/ошибка в вопросе), панель
источников с подсветкой, блок trace «как принято решение», таймлайн редакций,
просмотр документов. Ядро то же: pipeline.answer_one со списком реплик —
история сессии собирается в диалоговую ячейку «Пользователь:/Ассистент:».

Безопасность: без innerHTML с данными (textContent на фронте), CORS с
ограниченным origin, лимит длины вопроса и частоты на сессию. Секреты только
через окружение. Режим «без LLM» (нет ключей в env) — экстрактивные ответы,
интерфейс полностью проверяется офлайн.

Запуск: uvicorn webapp:app  (или docker compose up)
"""
from __future__ import annotations

import json
import os
import re
import time
from collections import defaultdict, deque
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import config
import pipeline
import solution
from guard import parse_dialog

WEB_DIR = Path(__file__).resolve().parent / "web"
ROOT = Path(__file__).resolve().parent

MAX_QUESTION_CHARS = 8000          # серверный лимит сверх клампа answer_one
RATE_LIMIT_MSGS = 20               # сообщений на сессию
RATE_LIMIT_WINDOW_S = 5 * 60

app = FastAPI(title="Юридический ассистент — ПСБ", version="2.1")
app.add_middleware(
    CORSMiddleware,
    allow_origins=[o for o in (
        os.environ.get("WEB_ORIGIN", "").split(",")
        if os.environ.get("WEB_ORIGIN") else []
    ) if o.strip()] or ["http://127.0.0.1:8000", "http://localhost:8000"],
    allow_methods=["GET", "POST"],
    allow_headers=[],
)

_CTX: Optional[dict] = None
_sessions: dict[str, list[tuple[str, str]]] = defaultdict(list)
_rate: dict[str, deque] = defaultdict(deque)


def get_ctx() -> dict:
    """Ленивая загрузка индекса/клиента (общий контекст с batch-режимом)."""
    global _CTX
    if _CTX is None:
        _CTX = solution.load_context()
    return _CTX


# ---------------------------------------------------------------- helpers

def dialog_cell(history: list[tuple[str, str]], message: str) -> str:
    """История сессии + новое сообщение → ячейка «Пользователь:/Ассистент:»."""
    lines = [f"{'Пользователь' if role == 'user' else 'Ассистент'}: {text}"
             for role, text in history]
    lines.append(f"Пользователь: {message}")
    return "\n".join(lines)


STATUS_RU = {
    "full": "полный",
    "partial": "частичный",
    "refusal": "отказ",
    "premise_error": "ошибка в вопросе",
    "clarify": "ошибка в вопросе",
}


def answer_rich(question: str, ctx: dict, deadline: float) -> dict:
    """answer_one + трассировка (§18.1.3) → {answer, status, sources, trace}."""
    collect: dict = {}
    ctx["collect"] = collect
    try:
        answer = pipeline.answer_one(question, ctx, deadline)
    finally:
        ctx["collect"] = None
    status = collect.get("status", "partial")
    sources = collect.get("chunks") or []
    dialog = parse_dialog(question[: ctx["cfg"].max_input_chars])
    trace = {
        "dialog": collect.get("dialog") or {
            "is_dialog": dialog.is_dialog, "replicas": 1,
            "last_user": dialog.last_user[:300],
            "injection": bool(dialog.fake_system)},
        "queries": collect.get("queries") or [],
        "use_rerank": collect.get("use_rerank"),
        "temporal_filters": collect.get("temporal_filters") or {},
        "temporal_notes": collect.get("notes") or [],
        "prompt_chars": collect.get("prompt_chars"),
        "verify": collect.get("verify"),
        "reason": collect.get("reason", ""),
    }
    return {"answer": answer, "status": STATUS_RU.get(status, "частичный"),
            "sources": sources, "trace": trace}


def check_rate(sid: str) -> None:
    now = time.monotonic()
    q = _rate[sid]
    while q and now - q[0] > RATE_LIMIT_WINDOW_S:
        q.popleft()
    if len(q) >= RATE_LIMIT_MSGS:
        raise HTTPException(429, "Слишком много запросов — подождите немного")
    q.append(now)


def timeline_events(docs: list[dict]) -> list[dict]:
    """События таймлайна редакций из метаданных базы (без зашитых имён).

    Показываем документы компании (даты и редакции) — статьи законов
    только захламляют шкалу."""
    events = []
    for d in docs:
        if d.get("doc_type") != "company":
            continue
        edition = d.get("edition")
        vf = (d.get("valid_from") or "").strip()
        date = vf or (d.get("doc_date") or "").strip()
        if not date or date == "None":
            continue
        events.append({
            "date": date,
            "label": str(d.get("short_name") or d.get("file_name")),
            "kind": d.get("doc_type", ""),
            "note": f"ред. от {edition}" if edition else "",
        })
    return sorted(events, key=lambda e: e["date"])


# ---------------------------------------------------------------- routes

@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


# статика фронта (style.css, app.js) — только из каталога web/
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/api/docs")
def api_docs():
    ctx = get_ctx()
    out = []
    for d in ctx.get("docs") or []:
        out.append({k: d.get(k) for k in
                    ("doc_id", "short_name", "doc_type", "kind", "number",
                     "doc_date", "valid_from", "valid_to", "edition")})
    return {"docs": out, "timeline": timeline_events(ctx.get("docs") or [])}


@app.get("/api/doc/{doc_id:path}")
def api_doc(doc_id: str):
    """Полный текст документа = склейка чанков по порядку (§18.1.5)."""
    ctx = get_ctx()
    retriever = ctx.get("retriever")
    if retriever is None:
        raise HTTPException(503, "Индекс недоступен")
    parts = [c for c in retriever.chunks if c.get("doc_id") == doc_id]
    if not parts:
        raise HTTPException(404, "Документ не найден")
    meta = next((d for d in (ctx.get("docs") or [])
                 if d.get("doc_id") == doc_id), {})
    text = "\n\n".join(str(c.get("text") or "") for c in parts)
    return {"meta": {k: meta.get(k) for k in
                     ("doc_id", "short_name", "doc_type", "number", "doc_date",
                      "valid_from", "valid_to", "edition")},
            "text": text}


CLARIFY_ANSWER = ("Похоже, вопрос неполный: по одной букве или символу я не могу "
                  "определить тему. Напишите вопрос целиком — например, "
                  "«Какой гарантийный срок по договору с Альфа-Трейд?» — и я отвечу "
                  "по документам со ссылками на пункты.")


def _is_meaningful(message: str) -> bool:
    """Вопрос содержит хотя бы один осмысленный токен (буква/цифра, ≥2 знака).

    Веб-оболочка: вырожденный ввод («п», «?») не пускаем в конвейер — BM25
    найдёт «п. 3.2» и экстрактив выдаст мусор. Батч-режим это не касается."""
    tokens = re.findall(r"[а-яёa-z0-9]{2,}", message.lower())
    return any(any(c.isalpha() for c in t) for t in tokens)


async def _chat_flow(sid: str, message: str) -> dict:
    """Общая ветка чата: лимиты → история → answer_rich → сохранить реплики.

    answer_one блокирующий — выполняется в threadpool, event loop свободен."""
    sid = (sid or "default")[:64]
    message = (message or "").strip()
    if not message:
        raise HTTPException(400, "Пустой вопрос")
    if len(message) > MAX_QUESTION_CHARS:
        raise HTTPException(400, f"Вопрос длиннее {MAX_QUESTION_CHARS} символов")
    check_rate(sid)
    if not _is_meaningful(message):
        # вырожденный вопрос: не запускаем конвейер и не портим историю диалога
        return {"answer": CLARIFY_ANSWER, "status": STATUS_RU["clarify"],
                "sources": [], "trace": {"reason": "вопрос слишком короткий"},
                "session": sid}
    history = _sessions[sid][-10:]          # последние 5 пар реплик
    cell = dialog_cell(history, message)
    ctx = get_ctx()
    rich = await run_in_threadpool(answer_rich, cell, dict(ctx),
                                   time.time() + 15 * 60)
    _sessions[sid].append(("user", message))
    _sessions[sid].append(("assistant", rich["answer"]))
    rich["session"] = sid
    return rich


@app.post("/api/chat")
async def api_chat_post(req: Request):
    try:
        body = await req.json()
    except Exception:
        raise HTTPException(400, "Ожидался JSON {session, message}")
    return await _chat_flow(str(body.get("session", "default")),
                            str(body.get("message", "")))


def _sse(data: dict) -> str:
    return f"data: {json.dumps(data, ensure_ascii=False)}\n\n"


@app.get("/api/chat/stream")
async def api_chat_stream(session: str = "default", msg: str = ""):
    """SSE: события start → stage → final (§18.2). Параметры в query."""
    sid = (session or "default")[:64]
    message = (msg or "").strip()
    if not message:
        raise HTTPException(400, "Пустой вопрос")
    if len(message) > MAX_QUESTION_CHARS:
        raise HTTPException(400, f"Вопрос длиннее {MAX_QUESTION_CHARS} символов")
    check_rate(sid)

    async def gen():
        yield _sse({"event": "start"})
        yield _sse({"event": "stage", "stage": "поиск по базе"})
        rich = await _chat_flow(sid, message)
        yield _sse({"event": "stage", "stage": "проверка чисел и ссылок"})
        yield _sse({"event": "final", **rich})

    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache",
                                      "X-Accel-Buffering": "no"})


@app.post("/api/session/{sid}/reset")
async def api_reset(sid: str):
    _sessions.pop(sid[:64], None)
    _rate.pop(sid[:64], None)
    return {"ok": True}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8000)
