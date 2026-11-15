import StatusBadge from "./StatusBadge.jsx";
import ConfidenceMeter from "./ConfidenceMeter.jsx";
import { STATUS } from "../api/contract.js";
import { formatPercent, shorten } from "../lib/text.js";

/**
 * Карточка реплики: вопрос пользователя или ответ ассистента со статусом,
 * confidence и чипами источников (§18.1 п.1–2).
 */
export default function MessageCard({
  message,
  selected = false,
  onSelect,
  onOpenSource,
  onOpenPanel,
  onRetry,
}) {
  if (message.role === "user") {
    return (
      <article className="msg msg--user">
        <div className="msg__bubble">{message.content}</div>
      </article>
    );
  }

  if (message.error) {
    return (
      <article className="msg">
        <div className="answer" data-tone="stop" data-selected={selected}>
          <div className="answer__head">
            <span className="badge" data-tone="stop">
              <span className="badge__icon" aria-hidden="true">!</span>
              Сбой связи
            </span>
          </div>
          <p className="answer__text answer__text--error">{message.error}</p>
          <div className="answer__footer">
            <span>ядро не ответило — сессия сохранена, можно повторить</span>
            <div className="answer__actions">
              {onRetry && (
                <button type="button" className="link-btn" onClick={() => onRetry(message)}>
                  Повторить
                </button>
              )}
            </div>
          </div>
        </div>
      </article>
    );
  }

  const answer = message.answer;
  const status = answer?.status ?? "partial";
  const meta = STATUS[status] ?? STATUS.partial;
  const sources = answer?.sources ?? [];
  const trace = answer?.trace;
  const totalMs = Number(trace?.timings?.total_ms ?? 0);
  const citations = trace?.citations;
  const streaming = Boolean(message.streaming);

  return (
    <article className="msg">
      <div
        className="answer"
        data-tone={meta.tone}
        data-selected={selected}
      >
        <div className="answer__head">
          <StatusBadge status={status} streaming={streaming} />
          <ConfidenceMeter value={answer?.confidence ?? 0} />
          {streaming && <span className="msg__meta">формируется…</span>}
        </div>

        <p className="answer__text">
          {answer?.answer || (streaming ? "" : "Ответ пуст — ядро вернуло пустую строку.")}
          {streaming && <span className="answer__caret" aria-hidden="true" />}
        </p>

        {sources.length > 0 && (
          <div className="sources" aria-label="Источники ответа">
            {sources.map((source) => (
              <button
                key={source.id}
                type="button"
                className="source-chip"
                onClick={() => onOpenSource(source)}
                title={`${source.short_name} — ${source.path}`}
              >
                <span className="source-chip__ref">{source.path || "фрагмент"}</span>
                <span className="source-chip__name">{source.short_name}</span>
                <span className="source-chip__score">{formatPercent(source.score)}</span>
              </button>
            ))}
          </div>
        )}

        <div className="answer__footer">
          <span>{sources.length} ист.</span>
          {citations && (
            <span>
              цитаты: {citations.ok}/{citations.checked}
            </span>
          )}
          {totalMs > 0 && <span>{(totalMs / 1000).toFixed(1)} с</span>}
          {trace?.edition?.applied && (
            <span>редакция: {shorten(trace.edition.applied, 42)}</span>
          )}
          {trace?.model && <span>{trace.model}</span>}
          <div className="answer__actions">
            <button type="button" className="link-btn" onClick={() => onSelect?.(message.id)}>
              {selected ? "выбрано" : "выбрать"}
            </button>
            <button
              type="button"
              className="link-btn"
              onClick={() => onOpenPanel?.("sources", message.id)}
              disabled={sources.length === 0}
            >
              Источники
            </button>
            <button
              type="button"
              className="link-btn"
              onClick={() => onOpenPanel?.("trace", message.id)}
              disabled={!trace}
            >
              Как принято решение
            </button>
          </div>
        </div>
      </div>
    </article>
  );
}
