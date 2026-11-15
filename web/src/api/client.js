/**
 * Клиент API (§18.2). Два режима:
 *  - mock: локальная эмуляция SSE, работает без Python и LLM;
 *  - sse: живой `webapp.py` (FastAPI + SSE), контракт `docs/api_contract.md`.
 *
 * Переключение: клик по бейджу в шапке, через URL (?mode=sse|mock) или VITE_API_MODE.
 */
import { readSseStream } from "./sse.js";
import { streamMock } from "../mock/engine.js";
import { LIMITS, normalizeAnswer } from "./contract.js";

export function getApiMode() {
  if (typeof window === "undefined") return "mock";
  const urlParam = new URLSearchParams(window.location.search).get("mode");
  if (urlParam === "sse" || urlParam === "mock") return urlParam;
  const stored = localStorage.getItem("psb_api_mode");
  if (stored === "sse" || stored === "mock") return stored;
  if (window.location.port === "8000") return "sse";
  return import.meta.env.VITE_API_MODE === "sse" ? "sse" : "mock";
}

export function setApiMode(mode) {
  if (typeof window !== "undefined") {
    localStorage.setItem("psb_api_mode", mode);
    window.location.reload();
  }
}

export const API_MODE = getApiMode();
export const API_BASE = import.meta.env.VITE_API_BASE || "/api";

export class ApiError extends Error {
  constructor(message, { code = "api_error", status = 0 } = {}) {
    super(message);
    this.name = "ApiError";
    this.code = code;
    this.status = status;
  }
}

/** Обрезает вопрос по лимиту ядра (MAX_INPUT_CHARS = 4000) — §18.2, DoD. */
export function clampQuestion(text) {
  const value = String(text ?? "");
  if (value.length <= LIMITS.question_chars) return { text: value, truncated: false };
  return { text: value.slice(0, LIMITS.question_chars), truncated: true };
}

/**
 * Один вопрос → поток событий контракта.
 */
export async function askQuestion({ messages, signal, onEvent }) {
  const payload = messages
    .filter((message) => message.role === "user" || message.role === "assistant")
    .slice(-LIMITS.history_turns * 2)
    .map((message) => ({
      role: message.role,
      content: clampQuestion(message.content).text,
    }));

  const currentMode = getApiMode();
  if (currentMode === "mock") {
    return streamMock(payload, { onEvent, signal });
  }
  return askViaSse(payload, { onEvent, signal });
}

async function askViaSse(messages, { onEvent, signal }) {
  let response;
  try {
    response = await fetch(`${API_BASE}/chat`, {
      method: "POST",
      headers: {
        "Content-Type": "application/json",
        Accept: "text/event-stream",
      },
      body: JSON.stringify({ messages, session_id: getSessionId() }),
      signal,
    });
  } catch (error) {
    if (error?.name === "AbortError") throw error;
    throw new ApiError("Нет связи с сервером", { code: "network" });
  }

  if (!response.ok) {
    const detail = await safeJson(response);
    throw new ApiError(detail?.message || `Сервер вернул ${response.status}`, {
      code: detail?.code || "http_error",
      status: response.status,
    });
  }

  let final = null;
  await readSseStream(
    response,
    (frame) => {
      if (frame.event === "done") {
        final = normalizeAnswer(frame.data);
      } else if (frame.event === "error") {
        onEvent?.(frame);
        throw new ApiError(frame.data?.message || "Ошибка потока", {
          code: frame.data?.code || "stream_error",
        });
      }
      onEvent?.(frame);
    },
    signal,
  );
  if (!final) throw new ApiError("Поток завершился без события done", { code: "no_done" });
  return final;
}

async function safeJson(response) {
  try {
    return await response.json();
  } catch {
    return null;
  }
}

function getSessionId() {
  const key = "psb-session-id";
  let id = sessionStorage.getItem(key);
  if (!id) {
    id = `s-${Date.now().toString(36)}-${Math.random().toString(36).slice(2, 8)}`;
    sessionStorage.setItem(key, id);
  }
  return id;
}
