/**
 * Документы базы (§18.1 п.2, п.5): список с поиском и полный текст для
 * просмотра с подсветкой фрагмента.
 *
 * mock: данные из `src/mock/kb.json` (сгенерирован из index/).
 * sse:  GET /api/docs            → {docs: [...]}          (метаданные)
 *       GET /api/docs/{doc_id}   → {doc: {...}, chunks: [...]}
 */
import { API_BASE, getApiMode } from "./client.js";

let kbPromise = null;

function mockKb() {
  if (!kbPromise) kbPromise = import("../mock/kb.json").then((m) => m.default);
  return kbPromise;
}

const docCache = new Map();

/** Список документов (без тел чанков — их грузим по требованию). */
export async function loadDocsIndex() {
  if (getApiMode() === "mock") {
    const kb = await mockKb();
    return kb.docs.map(({ chunks, ...meta }) => meta);
  }
  const response = await fetch(`${API_BASE}/docs`);
  if (!response.ok) throw new Error(`Не удалось загрузить список документов: ${response.status}`);
  const data = await response.json();
  return Array.isArray(data?.docs) ? data.docs : [];
}

/** Документ целиком: метаданные + чанки (path, text, chunk_id). */
export async function loadDoc(docId) {
  if (docCache.has(docId)) return docCache.get(docId);
  let doc;
  if (getApiMode() === "mock") {
    const kb = await mockKb();
    const found = kb.docs.find((item) => item.doc_id === docId);
    doc = found ?? null;
  } else {
    const response = await fetch(`${API_BASE}/docs/${encodeURIComponent(docId)}`);
    if (!response.ok) throw new Error(`Документ недоступен: ${response.status}`);
    const data = await response.json();
    doc = data?.doc ? { ...data.doc, chunks: data.chunks ?? [] } : null;
  }
  if (doc) docCache.set(docId, doc);
  return doc;
}

/** Дата выгрузки базы = «сегодня» системы (§11.1). */
export async function loadToday() {
  return "2026-10-10";
}
