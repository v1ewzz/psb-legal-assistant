import StatusBadge from "./StatusBadge.jsx";
import ConfidenceMeter from "./ConfidenceMeter.jsx";
import { STATUS } from "../api/contract.js";
import { shorten } from "../lib/text.js";

/**
 * Карточка реплики с адаптивным рендером (без переполнения кнопок и чипов).
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
          <span className="avatar">А</span>
          <span className="who__text">Ассистент · сбой связи</span>
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
            <span className="answer-foot__note">ядро не ответило — сессия сохранена</span>
            {onRetry && (
              <button type="button" className="btn btn--sm" onClick={() => onRetry(message)}>
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

  let assistantMeta = "ответил по базе знаний";
  if (streaming) assistantMeta = "формирует ответ…";
  else if (totalMs > 0) assistantMeta = `время ${(totalMs / 1000).toFixed(1)} с`;
  else if (trace?.dropped?.length > 0 || trace?.gates?.some((g) => !g.passed)) {
    assistantMeta = "проверено детектором D2";
  }

  return (
    <article className="msg-assistant" onClick={() => onSelect?.(message.id)}>
      <div className="who">
        <span className="avatar">А</span>
        <span className="who__text">Ассистент · {assistantMeta}</span>
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

        {/* Чипы источников под ответом с защитой от переполнения */}
        {sources.length > 0 && (
          <div className="answer-chips">
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
                <svg width="12" height="12" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" style={{ flex: "none" }}>
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <path d="M14 2v6h6" />
                </svg>
                <span className="chip__text">
                  {src.short_name ? `${src.short_name.slice(0, 26)}…` : "Документ"} · {src.path || "фрагмент"}
                </span>
              </button>
            ))}
          </div>
        )}

        {/* План действий (v1.1) */}
        {trace?.plan?.length > 0 && !streaming && (
          <div className="plan">
            <span className="plan__title">План действий по процедуре:</span>
            <ol className="plan__list">
              {trace.plan.map((step, index) => (
                <li key={index} className="plan__item">
                  <span className="plan__num">{index + 1}.</span>
                  <span>
                    <strong>{step.title}</strong>
                    {step.detail && <span className="plan__detail"> — {step.detail}</span>}
                  </span>
                </li>
              ))}
            </ol>
          </div>
        )}

        {/* Уточняющие вопросы (v1.1) */}
        {trace?.clarifying?.length > 0 && !streaming && (
          <div className="clarify">
            <span className="clarify__title">Уточните вопрос:</span>
            <div className="clarify__chips">
              {trace.clarifying.map((q, index) => (
                <button
                  key={index}
                  type="button"
                  className="chip chip--clarify"
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

        {/* Недостающие факты при отказе */}
        {normStatus === "refusal" && trace?.subquestions?.some((sq) => sq.status === "unsupported") && (
          <ul className="missing">
            {trace.subquestions.filter((sq) => sq.status === "unsupported").map((sq) => (
              <li key={sq.id} className="missing__item">
                <span className="missing__icon">✕</span>
                <span>{sq.text} {sq.reason && `— ${sq.reason}`}</span>
              </li>
            ))}
          </ul>
        )}

        {/* Подвал карточки ответа с надежным адаптивным переносом */}
        <div className="answer-foot">
          <div className="answer-foot__metrics">
            <ConfidenceMeter value={answer?.confidence ?? 0} status={normStatus} />
            {citations && (
              <>
                <span className="answer-foot__sep">·</span>
                <span className="answer-foot__item">цитаты {citations.ok}/{citations.checked} ✓</span>
              </>
            )}
            {trace?.edition?.applied && (
              <>
                <span className="answer-foot__sep">·</span>
                <span className="answer-foot__item">ред: {shorten(trace.edition.applied, 28)}</span>
              </>
            )}
          </div>

          <div className="answer-foot__actions">
            <button
              type="button"
              className="btn btn--sm answer-foot__btn"
              onClick={(e) => {
                e.stopPropagation();
                onOpenPanel?.("trace", message.id);
              }}
            >
              Как принято решение →
            </button>
          </div>
        </div>
      </div>
    </article>
  );
}
