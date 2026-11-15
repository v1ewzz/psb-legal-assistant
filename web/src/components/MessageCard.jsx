import StatusBadge from "./StatusBadge.jsx";
import ConfidenceMeter from "./ConfidenceMeter.jsx";
import { STATUS } from "../api/contract.js";
import { formatPercent, shorten } from "../lib/text.js";

/**
 * Карточка реплики по макетам 01_main_screen.html и 02_answer_states.html:
 * - Пользователь: .msg-user с меткой времени .meta
 * - Ассистент: .msg-assistant с аватаром .who, карточкой .answer-card, бейджем .badge,
 *   полоской статуса слева, чипами источников .chip, счетчиком confidence-dot и кнопкой «Как принято решение →».
 */
export default function MessageCard({
  message,
  selected = false,
  onSelect,
  onOpenSource,
  onOpenPanel,
  onRetry,
  onAsk,
}) {
  const timestamp = message.time || "10.10.2026, 14:02";

  if (message.role === "user") {
    return (
      <article className="msg-user">
        <div className="msg-user__text">{message.content}</div>
        <div className="meta">{timestamp}</div>
      </article>
    );
  }

  if (message.error) {
    return (
      <article className="msg-assistant">
        <div className="who">
          <span className="avatar">А</span> Ассистент · сбой связи
        </div>
        <div className="answer-card answer-card--error" data-selected={selected}>
          <div className="answer-head">
            <span className="badge badge--error">
              <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round">
                <circle cx="12" cy="12" r="10" />
                <line x1="12" y1="8" x2="12" y2="12" />
                <line x1="12" y1="16" x2="12.01" y2="16" />
              </svg>
              Сбой связи
            </span>
            <span className="time">{timestamp}</span>
          </div>
          <p className="answer-text" style={{ color: "var(--status-refusal-color)" }}>
            {message.error}
          </p>
          <div className="answer-foot">
            <span>ядро не ответило — сессия сохранена</span>
            {onRetry && (
              <button type="button" className="chip" style={{ marginLeft: "auto" }} onClick={() => onRetry(message)}>
                Повторить запрос
              </button>
            )}
          </div>
        </div>
      </article>
    );
  }

  const answer = message.answer;
  const rawStatus = answer?.status ?? "partial";
  const normStatus = rawStatus === "question_error" ? "error" : rawStatus;
  const meta = STATUS[rawStatus] ?? STATUS[normStatus] ?? STATUS.partial;
  const sources = answer?.sources ?? [];
  const trace = answer?.trace;
  const totalMs = Number(trace?.timings?.total_ms ?? 0);
  const citations = trace?.citations;
  const streaming = Boolean(message.streaming);

  const cardModifier =
    normStatus === "full" ? "answer-card--full"
    : normStatus === "partial" ? "answer-card--partial"
    : normStatus === "refusal" ? "answer-card--refusal"
    : "answer-card--error";

  // Статусная подпись в заголовке ассистента
  let assistantMeta = "ответил по базе знаний";
  if (streaming) assistantMeta = "answering…";
  else if (totalMs > 0) assistantMeta = `время ответа ${(totalMs / 1000).toFixed(1)} с`;
  else if (trace?.dropped?.length > 0 || trace?.gates?.some((g) => !g.passed)) {
    assistantMeta = "детектор инъекций проверил источники";
  }

  return (
    <article className="msg-assistant" onClick={() => onSelect?.(message.id)}>
      <div className="who">
        <span className="avatar">А</span> Ассистент · {assistantMeta}
      </div>

      <div className={`answer-card ${cardModifier}`} data-selected={selected}>
        <div className="answer-head">
          <StatusBadge status={rawStatus} streaming={streaming} />
          <span className="time">{timestamp}</span>
        </div>

        <p className="answer-text">
          {answer?.answer || (streaming ? "" : "Ответ пуст — ядро вернуло пустую строку.")}
          {streaming && <span className="stream-cursor" aria-hidden="true" />}
        </p>

        {/* Чипы источников под ответом */}
        {sources.length > 0 && (
          <div className="answer-chips" style={{ marginTop: 10, display: "flex", flexWrap: "wrap", gap: 6 }}>
            {sources.map((src) => (
              <button
                key={src.id}
                type="button"
                className="chip"
                onClick={(e) => {
                  e.stopPropagation();
                  onOpenSource?.(src);
                }}
                title={`${src.short_name} — ${src.path}`}
              >
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ marginRight: 2 }}>
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <path d="M14 2v6h6" />
                </svg>
                {src.short_name ? `${src.short_name.slice(0, 24)}…` : "Документ"} · {src.path || "фрагмент"}
              </button>
            ))}
          </div>
        )}

        {/* План действий (v1.1) */}
        {trace?.plan?.length > 0 && !streaming && (
          <div className="plan" style={{ marginTop: 12 }}>
            <span className="plan__title" style={{ fontWeight: 600, fontSize: "var(--fs-body-small)", color: "var(--psb-orange-500)", display: "block", marginBottom: 6 }}>
              План действий по процедуре:
            </span>
            <ol className="plan__list" style={{ listStyle: "none", padding: 0, display: "grid", gap: 6 }}>
              {trace.plan.map((step, index) => (
                <li key={index} style={{ display: "flex", gap: 8, fontSize: "var(--fs-body-small)", background: "var(--surface-panel)", padding: "6px 10px", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)" }}>
                  <span style={{ fontWeight: 700, color: "var(--psb-orange-500)" }}>{index + 1}.</span>
                  <span>
                    <strong>{step.title}</strong>
                    {step.detail && <span style={{ color: "var(--text-secondary)" }}> — {step.detail}</span>}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {/* Уточняющие вопросы (v1.1) */}
        {trace?.clarifying?.length > 0 && !streaming && (
          <div className="clarify" style={{ marginTop: 12 }}>
            <span style={{ fontSize: "var(--fs-caption)", color: "var(--text-muted)", display: "block", marginBottom: 6 }}>
              Уточните — и я отвечу по документам:
            </span>
            <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
              {trace.clarifying.map((q, index) => (
                <button
                  key={index}
                  type="button"
                  className="chip"
                  style={{ background: "var(--surface-hover)", borderColor: "var(--psb-blue-500)" }}
                  onClick={(e) => {
                    e.stopPropagation();
                    onAsk?.(q);
                  }}
                >
                  {q}
                </button>
              ))}
            </div>
          </div>
        )}

        {/* Недостающие факты при отказе (макет 02) */}
        {normStatus === "refusal" && trace?.subquestions?.some((sq) => sq.status === "unsupported") && (
          <ul className="missing" style={{ listStyle: "none", marginTop: 10, display: "grid", gap: 6, padding: 0 }}>
            {trace.subquestions.filter((sq) => sq.status === "unsupported").map((sq) => (
              <li key={sq.id} style={{ display: "flex", gap: 8, fontSize: "var(--fs-body-small)", color: "var(--text-secondary)", background: "var(--bg-danger)", border: "1px solid var(--status-refusal-border)", borderRadius: "var(--radius-md)", padding: "7px 10px" }}>
                <span style={{ color: "var(--status-refusal-color)", fontWeight: "bold" }}>✕</span>
                <span>{sq.text} {sq.reason && `— ${sq.reason}`}</span>
              </li>
            ))}
          </ul>
        )}

        {/* Подвал карточки ответа */}
        <div className="answer-foot">
          <ConfidenceMeter value={answer?.confidence ?? 0} status={normStatus} />
          {citations && (
            <>
              <span>·</span>
              <span>цитаты сверены {citations.ok}/{citations.checked} ✓</span>
            </>
          )}
          {trace?.edition?.applied && (
            <>
              <span>·</span>
              <span>редакция: {shorten(trace.edition.applied, 36)}</span>
            </>
          )}
          <span style={{ marginLeft: "auto" }}>
            <button
              type="button"
              className="link-btn"
              style={{ color: "var(--text-link)", fontWeight: 500, fontSize: "var(--fs-caption)" }}
              onClick={(e) => {
                e.stopPropagation();
                onOpenPanel?.("trace", message.id);
              }}
            >
              Как принято решение →
            </button>
          </span>
        </div>
      </div>
    </article>
  );
}
