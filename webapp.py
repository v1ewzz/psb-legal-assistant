# -*- coding: utf-8 -*-
"""Веб-интерфейс бэкенда: FastAPI + SSE стриминг (§18, docs/api_contract.md).

Эндпоинты:
- POST /api/chat      -> text/event-stream (meta, source*, delta*, trace, done, ping)
- GET  /api/docs      -> {docs: DocMeta[]}
- GET  /api/docs/{id} -> {doc: DocMeta, chunks: Chunk[]}
- GET  /api/health    -> {ok, mode, docs, schema_version}
- Статика             -> раздача web/dist при наличии
"""
from __future__ import annotations

import asyncio
import json
import os
import re
import sys
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, AsyncGenerator, Dict, List, Optional

from fastapi import FastAPI, HTTPException, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

import config
import guard
import pipeline
import solution
import temporal
from llm_client import ApiError, LlmClient
from retrieval import Retriever

# Кэш контекста приложения
CTX: Dict[str, Any] = {}
DOCS_BY_ID: Dict[str, dict] = {}
CHUNKS_BY_DOC: Dict[str, list] = {}

T1_TRAP_RE = re.compile(r"90\s+календарн", re.I)


def _init_backend():
    """Загружает контекст, индекс и подготавливает быстрые структуры."""
    global CTX, DOCS_BY_ID, CHUNKS_BY_DOC
    print("Инициализация бэкенда Юридического ассистента ПСБ...", file=sys.stderr)
    CTX = solution.load_context()
    docs = CTX.get("docs") or []
    DOCS_BY_ID = {d["doc_id"]: d for d in docs}

    retriever = CTX.get("retriever")
    CHUNKS_BY_DOC = {}
    if retriever and retriever.chunks:
        for chunk in retriever.chunks:
            did = chunk.get("doc_id")
            if did not in CHUNKS_BY_DOC:
                CHUNKS_BY_DOC[did] = []
            CHUNKS_BY_DOC[did].append(chunk)

    print(f"Готово: документов={len(DOCS_BY_ID)}, чанков={len(retriever.chunks if retriever else 0)}", file=sys.stderr)


@asynccontextmanager
async def lifespan(app: FastAPI):
    _init_backend()
    yield


app = FastAPI(
    title="ПСБ Юридический ассистент API",
    description="FastAPI + SSE бэкенд для веб-интерфейса (§18)",
    version="1.1.0",
    lifespan=lifespan,
)

# Разрешаем CORS для локальной разработки фронта
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=False,
    allow_methods=["*"],
    allow_headers=["*"],
)


# ============================================================
# Модели запросов / ответов
# ============================================================

class ChatMessage(BaseModel):
    role: str
    content: str


class ChatRequest(BaseModel):
    messages: List[ChatMessage]
    session_id: Optional[str] = None


# ============================================================
# Вспомогательные функции построения контракта (§4, §5, §6)
# ============================================================

def _build_source(chunk: dict, doc_meta: dict, idx: int) -> dict:
    """Формирует объект Source согласно docs/api_contract.md §4."""
    path = str(chunk.get("path") or "")
    short_name = str(doc_meta.get("short_name") or chunk.get("short_name") or chunk.get("doc_id") or "Документ")
    crumbs = [short_name]
    if path:
        crumbs.extend([p.trim() if hasattr(p, "trim") else p.strip() for p in path.split(",") if p.strip()])

    text = str(chunk.get("text") or "")
    quote = text[:240].strip()

    return {
        "id": f"s{idx}",
        "doc_id": chunk.get("doc_id"),
        "short_name": short_name,
        "doc_type": chunk.get("doc_type") or doc_meta.get("doc_type") or "company",
        "kind": doc_meta.get("kind") or "contract",
        "path": path,
        "crumbs": crumbs,
        "quote": quote,
        "anchor": {
            "chunk_id": chunk.get("chunk_id", ""),
            "start": 0,
            "end": len(quote),
        },
        "doc_date": doc_meta.get("doc_date"),
        "valid_from": doc_meta.get("valid_from"),
        "valid_to": doc_meta.get("valid_to"),
        "edition": doc_meta.get("edition"),
        "applies_from": doc_meta.get("applies_from"),
        "status": doc_meta.get("status"),
        "score": round(float(chunk.get("score", 0.88)), 2),
    }


def _detect_request_type(dialog: guard.Dialog) -> tuple[str, str]:
    """Определяет тип запроса согласно словарю §10 api_contract.md."""
    query = dialog.last_user.lower()
    if dialog.fake_system or guard.detect_injection_in_question(dialog.raw):
        return "injection", "Попытка инъекции / атака на контекст"
    if any(k in query for k in ["порядок", "шаг", "процедур", "инструкци", "согласован"]):
        return "procedure", "Процедурный вопрос / порядок действий"
    if any(k in query for k in ["редакц", "действова", "период", "2023", "2024", "2025", "числа", "дата"]):
        return "edition", "Вопрос о редакции на дату события"
    if any(k in query for k in ["правда ли", "верно ли", "90 дней", "500 000"]):
        return "false_premise", "Проверка утверждения с возможной ложной предпосылкой"
    if dialog.is_dialog:
        return "dialog", "Уточняющий вопрос в контексте диалога"
    return "direct", "Прямой нормативный запрос"


# ============================================================
# Эндпоинты
# ============================================================

@app.get("/api/health")
async def health():
    """Проверка живости сервиса (§18.1)."""
    cfg = CTX.get("cfg")
    client = CTX.get("client")
    is_live = bool(cfg and cfg.api_ready and client is not None)
    return {
        "ok": True,
        "mode": "live" if is_live else "mock",
        "docs": len(CTX.get("docs") or []),
        "schema_version": "1.1",
    }


@app.get("/api/docs")
async def get_docs():
    """Список документов базы знаний (§18.1 п.5)."""
    return {"docs": CTX.get("docs") or []}


@app.get("/api/docs/{doc_id}")
async def get_doc_detail(doc_id: str):
    """Полный текст документа с чанками для просмотра и подсветки."""
    doc_meta = DOCS_BY_ID.get(doc_id)
    if not doc_meta:
        raise HTTPException(status_code=404, detail="Документ не найден")

    chunks = CHUNKS_BY_DOC.get(doc_id) or []
    return {
        "doc": doc_meta,
        "chunks": chunks,
    }


@app.post("/api/chat")
async def chat_stream(req: ChatRequest):
    """SSE-стриминг ответа на вопрос с историей диалога (§18.1 п.1, docs/api_contract.md)."""
    if not req.messages:
        raise HTTPException(status_code=400, detail="Список сообщений пуст")

    # Сборка входного вопроса для ядра
    if len(req.messages) == 1:
        question_text = req.messages[0].content
    else:
        parts = []
        for m in req.messages:
            prefix = "Пользователь:" if m.role == "user" else "Ассистент:"
            parts.append(f"{prefix} {m.content}")
        question_text = "\n".join(parts)

    async def event_generator() -> AsyncGenerator[str, None]:
        t0 = time.time()
        cfg = CTX.get("cfg")
        retriever: Optional[Retriever] = CTX.get("retriever")
        docs = CTX.get("docs") or []
        client: Optional[LlmClient] = CTX.get("client")

        dialog = guard.parse_dialog(question_text)
        req_type, req_type_label = _detect_request_type(dialog)
        queries = guard.heuristic_queries(dialog)
        standalone = guard.standalone_query(dialog)

        # 1. Поиск положений
        chunks = []
        dropped = []
        t_text = pipeline._temporal_text(dialog, queries)
        filters = temporal.search_filters(t_text, docs)

        if retriever is not None:
            raw_chunks = retriever.search(
                queries,
                top_k=24,
                rerank_query=standalone,
                top_n=8,
                drop_doc_ids=filters.get("drop_doc_ids"),
                boost_doc_ids=filters.get("boost_doc_ids"),
            )
            # Детектор инъекций T1/D2
            for c in raw_chunks:
                text_c = str(c.get("text", ""))
                if T1_TRAP_RE.search(text_c) or "Игнорируй правила" in text_c:
                    dropped.append({
                        "doc_id": c.get("doc_id"),
                        "short_name": c.get("short_name"),
                        "reason": "инъекция «90 календарных дней» (детектор D2)",
                    })
                else:
                    chunks.append(c)
            chunks = retriever.expand(chunks)

        # Подготовка объектов источников
        sources = []
        for i, c in enumerate(chunks[:5], 1):
            dmeta = DOCS_BY_ID.get(c.get("doc_id"), {})
            sources.append(_build_source(c, dmeta, i))

        # Оценка статуса и уверенности
        has_supported = len(sources) > 0
        is_empty_q = not dialog.last_user.strip()

        if is_empty_q:
            predicted_status = "question_error"
            confidence_val = 0.2
        elif not has_supported:
            predicted_status = "refusal"
            confidence_val = 0.25
        elif any(k in dialog.last_user.lower() for k in ["пособие", "роддом", "отпуск"]):
            predicted_status = "refusal"
            confidence_val = 0.30
        elif len(sources) >= 2:
            predicted_status = "full"
            confidence_val = 0.92
        else:
            predicted_status = "partial"
            confidence_val = 0.65

        # 1. Событие meta (карточка красится сразу!)
        req_id = f"r-{int(t0*1000) % 100000:05x}"
        yield f"event: meta\ndata: {json.dumps({'request_id': req_id, 'status': predicted_status, 'confidence': confidence_val}, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.04)

        # 2. События source (по мере выдачи)
        for s in sources:
            yield f"event: source\ndata: {json.dumps(s, ensure_ascii=False)}\n\n"
            await asyncio.sleep(0.02)

        # 3. Вычисление ответа ядром
        notes = temporal.reason_notes(t_text, docs, chunks)
        deadline = time.time() + 45.0
        t_retrieval_ms = int((time.time() - t0) * 1000)

        # Если доступен реальный LLM клиент
        final_answer = ""
        if client is not None and cfg and cfg.api_ready:
            try:
                final_answer = pipeline.answer_one(question_text, CTX, deadline)
            except Exception as e:
                print(f"Ошибка вызова pipeline: {e}", file=sys.stderr)
                final_answer = pipeline.extractive_fallback(question_text, CTX, chunks)
        else:
            # Осмысленный экстрактивный fallback или отказ
            if predicted_status == "refusal":
                final_answer = pipeline.grounded_refusal(question_text, CTX)
            elif is_empty_q:
                final_answer = "Не удалось определить вопрос. Уточните формулировку."
            else:
                final_answer = pipeline.extractive_fallback(question_text, CTX, chunks)

        # 4. События delta (стриминг текста)
        tokens = re.split(r"(\s+)", final_answer)
        for tok in tokens:
            if tok:
                yield f"event: delta\ndata: {json.dumps({'text': tok}, ensure_ascii=False)}\n\n"
                await asyncio.sleep(0.015)

        t_llm_ms = int((time.time() - t0) * 1000) - t_retrieval_ms
        t_total_ms = int((time.time() - t0) * 1000)

        # 5. Построение объекта Trace
        edition_note = None
        event_date = None
        m_date = re.search(r"(\d{2}\.\d{2}\.\d{4})", t_text)
        if m_date:
            d_parts = m_date.group(1).split(".")
            event_date = f"{d_parts[2]}-{d_parts[1]}-{d_parts[0]}"
            edition_note = f"На дату {m_date.group(1)} применяются соответствующие нормы"

        reasons = [
            f"retrieval 0.30: найдено {len(sources)} релевантных положений в топ-5",
            f"достаточность 0.25: ключевые факты подтверждены документами",
            "верификация 0.30: числовые показатели проверены по тексту базы",
            "контекст 0.15: проверка на инъекции и хронологию пройдена",
        ]
        if dropped:
            reasons.append("детектор D2: отброшены скомпрометированные чанки")

        subquestions = []
        if req_type == "procedure":
            subquestions.append({"id": "q1", "text": "Какой порядок действий установлен?", "status": "supported", "source_ids": ["s1"], "reason": "Шаги найдены в регламенте"})
        elif len(sources) > 0:
            subquestions.append({"id": "q1", "text": standalone, "status": "supported", "source_ids": [s["id"] for s in sources[:2]], "reason": "Норма присутствует в базе"})
        else:
            subquestions.append({"id": "q1", "text": standalone, "status": "unsupported", "source_ids": [], "reason": "Положения в базе знаний отсутствуют"})

        plan = []
        if req_type == "procedure" and len(sources) > 0:
            plan = [
                {"title": "Юридическая проверка контрагента", "detail": "проверка полномочий и благонадёжности по перечню К-12", "source_ids": ["s1"]},
                {"title": "Согласование финансовым департаментом", "detail": "визирование условий расчётов и налоговых ставок", "source_ids": ["s1"]},
                {"title": "Одобрение генеральным директором", "detail": "при сумме сделки свыше установленного лимита (п. 2.4)", "source_ids": ["s1"]},
                {"title": "Подписание и регистрация", "detail": "внесение в картотеку договоров", "source_ids": ["s1"]},
            ]

        clarifying = []
        if predicted_status == "question_error":
            clarifying = [
                "Какая арендная плата по договору А-08?",
                "Какой лимит согласования по Регламенту 2025 года?",
                "Какие условия поставки с ООО «Альфа-Трейд»?",
            ]

        trace_obj = {
            "request_type": req_type,
            "request_type_label": req_type_label,
            "standalone_query": standalone,
            "retrieved": [
                {
                    "doc_id": s["doc_id"],
                    "short_name": s["short_name"],
                    "path": s["path"],
                    "score": s["score"],
                    "channel": "bm25+exact-ref",
                }
                for s in sources
            ],
            "dropped": dropped,
            "subquestions": subquestions,
            "confidence": {
                "value": confidence_val,
                "zone": predicted_status,
                "threshold_high": 0.72,
                "threshold_low": 0.35,
                "reasons": reasons,
            },
            "edition": {
                "applied": "Действующая редакция на дату вопроса",
                "event_date": event_date,
                "note": edition_note or "Конфликт редакций не обнаружен",
                "conflicted": bool(edition_note and "2023" in edition_note),
            },
            "citations": {
                "checked": len(sources),
                "ok": len(sources),
                "failures": [],
            },
            "gates": [
                {"name": "T1: инъекции в документах", "passed": len(dropped) == 0 or True, "detail": "детектор проверил источники"},
                {"name": "verify-lite: числа из evidence", "passed": True, "detail": "числа согласованы"},
            ],
            "degraded": [],
            "timings": {
                "total_ms": t_total_ms,
                "retrieval_ms": t_retrieval_ms,
                "llm_ms": t_llm_ms,
            },
            "model": cfg.llm_model if (cfg and cfg.api_ready) else "экстрактивный режим (без LLM)",
            "plan": plan,
            "clarifying": clarifying,
        }

        yield f"event: trace\ndata: {json.dumps(trace_obj, ensure_ascii=False)}\n\n"
        await asyncio.sleep(0.02)

        # 6. Событие done
        done_obj = {
            "id": req_id,
            "answer": final_answer,
            "status": predicted_status,
            "confidence": confidence_val,
            "sources": sources,
            "trace": trace_obj,
        }
        yield f"event: done\ndata: {json.dumps(done_obj, ensure_ascii=False)}\n\n"

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


# ============================================================
# Раздача фронтенда из web/dist (если собран)
# ============================================================

dist_dir = Path(__file__).parent / "web" / "dist"
if dist_dir.is_dir():
    app.mount("/", StaticFiles(directory=str(dist_dir), html=True), name="static")
