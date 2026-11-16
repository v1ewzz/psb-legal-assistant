# -*- coding: utf-8 -*-
"""Веб-интерфейс бэкенда: FastAPI + SSE (§18) на ядре v3.

Функции: чат со статусами (полный/частичный/отказ/ошибка в вопросе), панель
источников с подсветкой, блок trace «как принято решение», таймлайн редакций,
просмотр документов. Ядро то же: pipeline.answer_one — история сессии
собирается в диалоговую ячейку «Пользователь:/Ассистент:» (dialog.py её
разбирает, отвечает только на последнюю реплику).

Адаптация под v3: статусы выводятся из Result (source/issues), источники —
из Result.evidence (фрагменты с реквизитами), трасса — из Result.trace и
каналов поиска (BM25/doc2q/dense/rerank), заметки о редакциях —
legalctx.build_notes (те же, что видит модель).

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

try:  # локальный запуск: ключи из .env (в ZIP-сабмит не входит)
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from starlette.concurrency import run_in_threadpool

import config
import dialog as D
import legalctx
import pipeline
import verify as V

WEB_DIR = Path(__file__).resolve().parent / "web"
ROOT = Path(__file__).resolve().parent

MAX_QUESTION_CHARS = 8000          # серверный лимит сверх клампа answer_one
RATE_LIMIT_MSGS = 20               # сообщений на сессию
RATE_LIMIT_WINDOW_S = 5 * 60
WEB_DEADLINE_S = 15 * 60

app = FastAPI(title="Юридический ассистент — ПСБ", version="3.0")
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
        _CTX = pipeline.build_context(config.from_env())
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

SOURCE_REASONS_RU = {
    "поиск": "релевантность",
    "соседний фрагмент": "соседний пункт",
    "начало статьи": "начало статьи",
}


def status_of(res: "pipeline.Result") -> str:
    """Статус ответа из фактов о прогоне (не по эвристикам «на глаз»)."""
    if res.source == "gate":
        return "premise_error"
    if res.source == "refusal":
        return "refusal"
    if res.source == "extractive":
        return "partial"          # цитаты найдены, полный вывод не сформирован
    # llm / cache: отказ распознаём только по фразе отказа в самом ответе
    if len(res.answer) < 700 and V.REFUSAL_RE.search(res.answer or ""):
        return "refusal"
    if res.issues or res.supported_ratio < 0.999:
        return "partial"
    return "full"


def sources_of(res: "pipeline.Result", limit: int = 8) -> list[dict]:
    """Панель источников: фрагменты из Evidence с реквизитами и текстом."""
    if res.evidence is None:
        return []
    out = []
    for it in res.evidence.items[:limit]:
        m = res.evidence.docs[it.chunk["doc_id"]]
        out.append({
            "doc": m.get("short_name") or m.get("label") or it.chunk["doc_id"],
            "doc_id": it.chunk["doc_id"],
            "path": it.chunk.get("path", ""),
            "reason": SOURCE_REASONS_RU.get(it.reason, it.reason),
            "text": it.chunk.get("body") or it.chunk.get("text", ""),
        })
    return out


def trace_of(res: "pipeline.Result", question: str, ctx: dict) -> dict:
    """Трасса «как принято решение» (§18.1.3) — из фактов прогона v3."""
    dlg = D.parse_dialog(question[: ctx["cfg"].max_input_chars])
    ev = res.evidence
    channels = list(ev.channels) if ev else []
    notes = []
    if ev is not None:
        try:
            notes = legalctx.build_notes(ev, ctx["today"], dlg.question)
        except Exception:
            notes = []
    verify_line = "пройдена" if not res.issues else "; ".join(res.issues[:4])
    reason = {
        "llm": "ответ сгенерирован и подтверждён проверкой",
        "cache": "ответ из кэша (тот же вопрос уже отвечен)",
        "extractive": "LLM недоступна — показаны найденные положения дословно",
        "gate": "в вопросе неверная предпосылка — поправлено кодом",
        "refusal": "данных для ответа в базе нет — отказ",
    }.get(res.source, res.source)
    if res.attempts > 1:
        reason += f" (попыток: {res.attempts})"
    return {
        "dialog": {
            "is_dialog": bool(dlg.history),
            "replicas": len(dlg.history) + 1,
            "last_user": dlg.question[:300],
            "injection": False,   # инъекции в документах отсекаются на ingest (D2)
        },
        "queries": [f"каналы: {', '.join(channels) if channels else 'BM25'}"],
        "use_rerank": "rerank" in channels,
        "temporal_filters": {},
        "temporal_notes": notes,
        "prompt_chars": None,
        "verify": verify_line,
        "reason": reason,
    }


def answer_rich(question: str, ctx: dict, deadline: float) -> dict:
    """Прогон ядра v3 → {answer, status, sources, trace}."""
    res = pipeline.run(question, ctx, deadline)
    return {"answer": res.answer, "status": status_of(res),
            "sources": sources_of(res), "trace": trace_of(res, question, ctx)}


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
        if d.get("doc_type") not in ("contract", "policy", "letter", "note"):
            continue
        edition = d.get("edition")
        vf = (d.get("valid_from") or "").strip()
        date = vf or (d.get("doc_date") or "").strip()
        if not date or date == "None":
            continue
        events.append({
            "date": date,
            "label": str(d.get("short_name") or d.get("doc_id")),
            "kind": d.get("doc_type", ""),
            "note": f"ред. от {edition}" if edition else "",
        })
    return sorted(events, key=lambda e: e["date"])


def doc_public(d: dict) -> dict:
    return {
        "doc_id": d.get("doc_id"),
        "short_name": d.get("short_name") or d.get("label") or d.get("doc_id"),
        "doc_type": d.get("doc_type"),
        "number": ", ".join(d.get("doc_numbers") or []) or None,
        "doc_date": d.get("doc_date"),
        "valid_from": d.get("valid_from"),
        "valid_to": d.get("valid_to"),
        "edition": d.get("edition_date"),
    }


# ---------------------------------------------------------------- routes

@app.get("/")
def index() -> FileResponse:
    return FileResponse(WEB_DIR / "index.html")


# статика фронта (style.css, app.js) — только из каталога web/
app.mount("/static", StaticFiles(directory=WEB_DIR), name="static")


@app.get("/api/docs")
def api_docs():
    ctx = get_ctx()
    retriever = ctx.get("retriever")
    docs = list(retriever.docs.values()) if retriever else []
    out = [doc_public(d) for d in docs]
    return {"docs": out, "timeline": timeline_events(docs)}


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
    meta = retriever.docs.get(doc_id, {})
    text = "\n\n".join(str(c.get("text") or "") for c in parts)
    return {"meta": doc_public(meta), "text": text}


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
    rich = await run_in_threadpool(answer_rich, cell, ctx, time.time() + WEB_DEADLINE_S)
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
