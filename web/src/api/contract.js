/**
 * Контракт ответа API (§18.1, §18.2) — ЕДИНСТВЕННЫЙ источник правды для фронта.
 * Согласован письменно в `docs/api_contract.md` (задача «заморозить с Ильёй»).
 *
 * Любое расхождение бэкенда с этим файлом — баг контракта, а не фронта:
 * нормализация ниже приводит «почти правильные» поля к форме, но новые поля
 * фронт не выдумывает.
 *
 * @typedef {"full"|"partial"|"refusal"|"question_error"} AnswerStatus
 *
 * @typedef {Object} SourceAnchor
 * @property {string} chunk_id  чанк индекса, в котором лежит фрагмент
 * @property {number} start     смещение начала цитаты внутри `chunk.text`
 * @property {number} end       смещение конца цитаты (эксклюзивно)
 *
 * @typedef {Object} Source
 * @property {string} id             стабильный id источника в рамках ответа (s1, s2…)
 * @property {string} doc_id         id документа из индекса
 * @property {string} short_name     человекочитаемое название
 * @property {"law"|"company"} doc_type
 * @property {string} kind           contract | supplement | letter | regulation | memo | law
 * @property {string} path           реквизит чанка («п. 3.1», «ч. 1, абз. 3»)
 * @property {string[]} crumbs       хлебные крошки: [документ, …части пути]
 * @property {string} quote          цитируемый фрагмент (ровно как в документе)
 * @property {SourceAnchor} anchor
 * @property {?string} doc_date      дата документа (ISO)
 * @property {?string} valid_from    дата начала действия (ISO) — для документов компании
 * @property {?string} valid_to
 * @property {?string} edition       редакция (ISO) — для Регламента
 * @property {?string} applies_from  дата применения (ISO) — для ДС
 * @property {?string} status        "active" | "revoked" — для редакций
 * @property {number} score          релевантность 0..1 (для сортировки/подписи)
 *
 * @typedef {Object} SubQuestion
 * @property {string} id
 * @property {string} text
 * @property {"supported"|"partial"|"unsupported"|"not_established"} status
 * @property {string[]} source_ids
 * @property {string} reason
 *
 * @typedef {Object} Trace
 * @property {string} request_type          direct | dialog | edition | false_premise | not_established | unclear
 * @property {string} request_type_label    подпись для UI
 * @property {string} standalone_query      запрос после разбора диалога
 * @property {Array<{doc_id:string, short_name:string, path:string, score:number, channel:string}>} retrieved
 * @property {SubQuestion[]} subquestions
 * @property {{value:number, zone:AnswerStatus, threshold_high:number, threshold_low:number, reasons:string[]}} confidence
 * @property {{applied:?string, event_date:?string, note:?string, conflicted:boolean}} edition
 * @property {{checked:number, ok:number, failures:string[]}} citations
 * @property {Array<{name:string, passed:boolean, detail:string}>} gates
 * @property {string[]} degraded
 * @property {{total_ms:number, retrieval_ms:number, llm_ms:number}} timings
 * @property {string} model
 *
 * @typedef {Object} Answer
 * @property {string} id
 * @property {string} answer
 * @property {AnswerStatus} status
 * @property {number} confidence
 * @property {Source[]} sources
 * @property {Trace} trace
 */

/** 4 статуса ответа (§18.1 п.1) — подпись, иконка, семантика цвета. */
export const STATUS = {
  full: { label: "Полный ответ", short: "полный", icon: "✓", tone: "ok" },
  partial: { label: "Частичный ответ", short: "частичный", icon: "◐", tone: "warn" },
  refusal: { label: "Обоснованный отказ", short: "отказ", icon: "✕", tone: "stop" },
  question_error: { label: "Ошибка в вопросе", short: "ошибка в вопросе", icon: "?", tone: "muted" },
};

export const STATUS_ORDER = ["full", "partial", "refusal", "question_error"];

/** Лимиты §18.2: длина вопроса и число запросов на сессию. */
export const LIMITS = {
  question_chars: 4000, // MAX_INPUT_CHARS из config.py
  session_questions: 20,
  history_turns: 10,
};

/** Ленивая нормализация: бэкенд может прислать неполный/битый объект. */
export function normalizeStatus(value) {
  return Object.prototype.hasOwnProperty.call(STATUS, value) ? value : "partial";
}

export function clampConfidence(value) {
  const num = Number(value);
  if (!Number.isFinite(num)) return 0;
  return Math.min(1, Math.max(0, num));
}

/**
 * Приводит ответ API к контракту. Не бросает исключений: битый ответ должен
 * отрендериться как «частичный» с тем, что есть, а не уронить чат (§18.2).
 */
export function normalizeAnswer(raw, fallbackId = "a0") {
  const data = raw && typeof raw === "object" ? raw : {};
  const trace = data.trace && typeof data.trace === "object" ? data.trace : {};
  const confidence = clampConfidence(
    data.confidence ?? trace.confidence?.value ?? 0,
  );
  const sources = Array.isArray(data.sources)
    ? data.sources.filter((s) => s && typeof s === "object")
    : [];
  return {
    id: String(data.id ?? fallbackId),
    answer: typeof data.answer === "string" ? data.answer : "",
    status: normalizeStatus(data.status),
    confidence,
    sources: sources.map(normalizeSource),
    trace: normalizeTrace(trace),
  };
}

function normalizeSource(source, index) {
  const anchor = source.anchor && typeof source.anchor === "object"
    ? {
        chunk_id: String(source.anchor.chunk_id ?? ""),
        start: Number(source.anchor.start ?? 0),
        end: Number(source.anchor.end ?? 0),
      }
    : { chunk_id: "", start: 0, end: 0 };
  return {
    id: String(source.id ?? `s${index + 1}`),
    doc_id: String(source.doc_id ?? ""),
    short_name: String(source.short_name ?? "Документ"),
    doc_type: source.doc_type === "law" ? "law" : "company",
    kind: String(source.kind ?? ""),
    path: String(source.path ?? ""),
    crumbs: Array.isArray(source.crumbs) ? source.crumbs.map(String) : [],
    quote: String(source.quote ?? ""),
    anchor,
    doc_date: source.doc_date ?? null,
    valid_from: source.valid_from ?? null,
    valid_to: source.valid_to ?? null,
    edition: source.edition ?? null,
    applies_from: source.applies_from ?? null,
    status: source.status ?? null,
    score: clampConfidence(source.score),
  };
}

function normalizeTrace(trace) {
  const confidence = trace.confidence && typeof trace.confidence === "object"
    ? trace.confidence
    : {};
  return {
    request_type: String(trace.request_type ?? "direct"),
    request_type_label: String(
      trace.request_type_label ?? "Прямой вопрос по документам",
    ),
    standalone_query: String(trace.standalone_query ?? ""),
    retrieved: Array.isArray(trace.retrieved)
      ? trace.retrieved.map((item) => ({
          doc_id: String(item?.doc_id ?? ""),
          short_name: String(item?.short_name ?? "Документ"),
          path: String(item?.path ?? ""),
          score: clampConfidence(item?.score),
          channel: String(item?.channel ?? "bm25"),
        }))
      : [],
    subquestions: Array.isArray(trace.subquestions)
      ? trace.subquestions.map((sub, index) => ({
          id: String(sub?.id ?? `q${index + 1}`),
          text: String(sub?.text ?? ""),
          status: ["supported", "partial", "unsupported", "not_established"].includes(
            sub?.status,
          )
            ? sub.status
            : "unsupported",
          source_ids: Array.isArray(sub?.source_ids) ? sub.source_ids.map(String) : [],
          reason: String(sub?.reason ?? ""),
        }))
      : [],
    confidence: {
      value: clampConfidence(confidence.value ?? 0),
      zone: normalizeStatus(confidence.zone),
      threshold_high: clampConfidence(confidence.threshold_high ?? 0.72),
      threshold_low: clampConfidence(confidence.threshold_low ?? 0.35),
      reasons: Array.isArray(confidence.reasons)
        ? confidence.reasons.map(String)
        : [],
    },
    edition: {
      applied: trace.edition?.applied ?? null,
      event_date: trace.edition?.event_date ?? null,
      note: trace.edition?.note ?? null,
      conflicted: Boolean(trace.edition?.conflicted),
    },
    citations: {
      checked: Number(trace.citations?.checked ?? 0),
      ok: Number(trace.citations?.ok ?? 0),
      failures: Array.isArray(trace.citations?.failures)
        ? trace.citations.failures.map(String)
        : [],
    },
    gates: Array.isArray(trace.gates)
      ? trace.gates.map((gate) => ({
          name: String(gate?.name ?? "проверка"),
          passed: Boolean(gate?.passed),
          detail: String(gate?.detail ?? ""),
        }))
      : [],
    degraded: Array.isArray(trace.degraded) ? trace.degraded.map(String) : [],
    timings: {
      total_ms: Number(trace.timings?.total_ms ?? 0),
      retrieval_ms: Number(trace.timings?.retrieval_ms ?? 0),
      llm_ms: Number(trace.timings?.llm_ms ?? 0),
    },
    model: String(trace.model ?? ""),
  };
}
