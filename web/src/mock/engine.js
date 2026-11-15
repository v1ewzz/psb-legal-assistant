/**
 * Мок-режим (§18.2): работает без LLM и без webapp.py, эмулируя ровно те же
 * SSE-события, что отдаёт живой бэкенд. Задача — витрина и стыковка контракта
 * до готовности ядра: фронт, написанный под мок, не меняется при переходе на SSE.
 *
 * Источник текстов и метаданных документов — `kb.json`, сгенерированный из
 * index/ скриптом `scripts/gen_web_mock.py`. Анкоры (start/end) считаются
 * здесь: цитата ищется в теле чанка, как это сделает бэкенд.
 */
import { DEMO_ANSWERS } from "./examples.js";
import { clampConfidence, LIMITS } from "../api/contract.js";

let kbPromise = null;

/** Ленивая загрузка мок-базы: в SSE-режиме чанк не грузится вовсе. */
function loadKb() {
  if (!kbPromise) {
    kbPromise = import("./kb.json").then((module) => module.default);
  }
  return kbPromise;
}

const sleep = (ms, signal) =>
  new Promise((resolve, reject) => {
    if (signal?.aborted) {
      reject(new DOMException("Прервано", "AbortError"));
      return;
    }
    const timer = setTimeout(() => {
      signal?.removeEventListener("abort", onAbort);
      resolve();
    }, ms);
    function onAbort() {
      clearTimeout(timer);
      reject(new DOMException("Прервано", "AbortError"));
    }
    signal?.addEventListener("abort", onAbort, { once: true });
  });

function docsById(kb) {
  return new Map(kb.docs.map((doc) => [doc.doc_id, doc]));
}

/** Хлебные крошки: документ → части пути («ч. 1, абз. 3» → 2 крошки). */
function crumbsFor(doc, path) {
  const parts = String(path || "")
    .split(/,\s*/)
    .filter(Boolean);
  return [doc.short_name, ...parts];
}

/** Досчитывает анкор цитаты внутри чанка (индексы, не поиск по regex при рендере). */
function anchorFor(doc, path, quote) {
  const chunk = (doc.chunks || []).find((item) => item.path === path);
  if (!chunk) return { chunk_id: "", start: 0, end: 0 };
  const start = quote ? chunk.text.indexOf(quote) : -1;
  return {
    chunk_id: chunk.chunk_id,
    start: start >= 0 ? start : 0,
    end: start >= 0 ? start + quote.length : 0,
  };
}

function resolveSources(demo, docs) {
  return demo.sources.map((source, index) => {
    const doc = docs.get(source.doc_id);
    const anchor = doc ? anchorFor(doc, source.path, source.quote) : { chunk_id: "", start: 0, end: 0 };
    return {
      id: `s${index + 1}`,
      doc_id: source.doc_id,
      short_name: doc?.short_name ?? "Документ",
      doc_type: doc?.doc_type === "law" ? "law" : "company",
      kind: doc?.kind ?? "",
      path: source.path,
      crumbs: doc ? crumbsFor(doc, source.path) : [source.path].filter(Boolean),
      quote: source.quote,
      anchor,
      doc_date: doc?.doc_date ?? null,
      valid_from: doc?.valid_from ?? null,
      valid_to: doc?.valid_to ?? null,
      edition: doc?.edition ?? null,
      applies_from: doc?.applies_from ?? null,
      status: doc?.status ?? null,
      score: clampConfidence(source.score),
    };
  });
}

function resolveRetrieved(trace, docs) {
  return (trace.retrieved || []).map((item) => ({
    doc_id: item.doc_id,
    short_name: docs.get(item.doc_id)?.short_name ?? "Документ",
    path: item.path,
    score: clampConfidence(item.score),
    channel: item.channel,
  }));
}

/** Тема вопроса для честного отказа (§4.2): «пособие по беременности…». */
function topicOf(text) {
  const cleaned = String(text || "")
    .replace(/[?!.]+$/g, "")
    .replace(/\s+/g, " ")
    .trim();
  if (!cleaned) return "запрошенной теме";
  const words = cleaned.split(" ").slice(0, 7).join(" ");
  return words.length > 90 ? `${words.slice(0, 90)}…` : words;
}

function buildRefusal(text) {
  return {
    id: "mock-refusal",
    answer:
      `В базе знаний нет документов о ${topicOf(text)}, поэтому ответить по документам я не могу. ` +
      "По памяти отвечать не буду, так как нормы могли измениться.",
    status: "refusal",
    confidence: 0.1,
    sources: [],
    trace: {
      request_type: "direct",
      request_type_label: "Прямой вопрос вне базы знаний (мок-режим)",
      retrieved: [],
      subquestions: [
        {
          id: "q1",
          text: String(text || "").slice(0, 200),
          status: "unsupported",
          source_ids: [],
          reason: "мок-режим: демо-ответа для этого вопроса нет, поиск не выполнялся",
        },
      ],
      confidence: {
        threshold_high: 0.72,
        threshold_low: 0.35,
        reasons: [
          "мок-режим: демо-ответа нет — отдаётся честный отказ по шаблону §4.2",
          "живой режим (VITE_API_MODE=sse) отвечает через pipeline.answer_one",
        ],
      },
      edition: { applied: null, event_date: null, note: null, conflicted: false },
      citations: { checked: 0, ok: 0, failures: [] },
      gates: [{ name: "мок-режим", passed: false, detail: "ядро не подключено" }],
      degraded: ["мок-режим: без LLM и retrieval"],
      timings: { retrieval_ms: 0, llm_ms: 0 },
      model: "mok-режим (без LLM)",
    },
  };
}

function pickDemo(text) {
  const normalized = String(text || "").toLowerCase();
  if (!normalized.trim()) return null;
  let best = null;
  let bestScore = 0;
  for (const demo of DEMO_ANSWERS) {
    const score = demo.keywords.reduce(
      (acc, keyword) => (normalized.includes(keyword.toLowerCase()) ? acc + 1 : acc),
      0,
    );
    if (score > bestScore) {
      best = demo;
      bestScore = score;
    }
  }
  return best;
}

/** Собирает полный ответ мока (без стриминга) — используется и в тестах. */
export async function buildMockAnswer(messages) {
  const kb = await loadKb();
  const docs = docsById(kb);
  const userMessages = (messages || []).filter((m) => m.role === "user");
  const lastUser = userMessages.at(-1)?.content ?? "";
  const demo = pickDemo(lastUser);

  if (!String(lastUser).trim()) {
    return {
      ...buildRefusal(lastUser),
      answer:
        "Не удалось определить вопрос: пустая реплика. Напишите, какой документ и какое событие вас интересуют.",
      status: "question_error",
      trace: {
        ...buildRefusal(lastUser).trace,
        request_type: "unclear",
        request_type_label: "Пустой вопрос",
        timings: { retrieval_ms: 0, llm_ms: 0 },
      },
    };
  }
  if (!demo) return buildRefusal(lastUser);

  const sources = resolveSources(demo, docs);
  const trace = {
    ...demo.trace,
    standalone_query: lastUser.slice(0, LIMITS.question_chars),
    retrieved: resolveRetrieved(demo.trace, docs),
    confidence: {
      ...demo.trace.confidence,
      value: demo.confidence,
      zone: demo.status,
    },
    timings: {
      ...demo.trace.timings,
      total_ms:
        Number(demo.trace.timings?.retrieval_ms ?? 0) +
        Number(demo.trace.timings?.llm_ms ?? 0),
    },
  };
  return {
    id: demo.id,
    answer: demo.answer,
    status: demo.status,
    confidence: demo.confidence,
    sources,
    trace,
  };
}

/**
 * Эмулирует SSE-поток живого бэкенда: meta → source* → delta* → trace → done.
 * Скорость подобрана так, чтобы видеть стриминг и подсветку статуса.
 */
export async function streamMock(messages, { onEvent, signal } = {}) {
  const answer = await buildMockAnswer(messages);
  const emit = (event, data) => onEvent?.({ event, data });

  emit("meta", {
    request_id: `mock-${Date.now()}`,
    status: answer.status,
    confidence: answer.confidence,
  });
  await sleep(120, signal);

  for (const source of answer.sources) {
    emit("source", source);
    await sleep(90, signal);
  }

  // Стриминг текста: по 2–3 слова, чтобы UI успевал перерисовать.
  const tokens = answer.answer.split(/(\s+)/);
  let buffer = "";
  for (let i = 0; i < tokens.length; i += 1) {
    buffer += tokens[i];
    if (i % 3 === 2 || i === tokens.length - 1) {
      emit("delta", { text: buffer });
      buffer = "";
      await sleep(22, signal);
    }
  }

  emit("trace", answer.trace);
  emit("done", answer);
  return answer;
}
