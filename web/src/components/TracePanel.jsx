import StatusBadge from "./StatusBadge.jsx";
import { STATUS } from "../api/contract.js";
import { formatDate, formatPercent } from "../lib/text.js";

const SUB_LABELS = {
  supported: { icon: "✓", text: "поддержан", tone: "ok" },
  partial: { icon: "◐", text: "частично", tone: "warn" },
  unsupported: { icon: "✕", text: "не поддержан", tone: "stop" },
  not_established: { icon: "?", text: "не установлен", tone: "muted" },
};

/**
 * Блок «Как принято решение» (§18.1 п.3) — главный экран для экспертов.
 * Порядок разделов = порядок логики ядра: тип запроса → найденные положения →
 * подвопросы → confidence и причины → редакция → проверка цитат.
 */
export default function TracePanel({ answer }) {
  if (!answer?.trace) {
    return <p className="panel__hint">Trace появится вместе с ответом: тип запроса, найденные положения, confidence с причинами, редакция и проверка цитат.</p>;
  }

  const trace = answer.trace;
  const confidence = trace.confidence;
  const zone = STATUS[confidence.zone] ?? STATUS.partial;

  return (
    <>
      <section className="trace__section">
        <h3 className="trace__label">Тип запроса</h3>
        <p className="trace__value">{trace.request_type_label}</p>
        <p className="trace__note">
          <code>request_type: {trace.request_type}</code>
        </p>
        {trace.standalone_query && (
          <p className="trace__note">Запрос к поиску: «{trace.standalone_query}»</p>
        )}
      </section>

      <section className="trace__section">
        <h3 className="trace__label">Найденные положения ({trace.retrieved.length})</h3>
        {trace.retrieved.length === 0 ? (
          <p className="trace__note">Поиск не дал положений выше порога.</p>
        ) : (
          <ul className="retrieved">
            {trace.retrieved.map((item, index) => (
              <li className="retrieved__row" key={`${item.doc_id}-${item.path}-${index}`}>
                <span className="retrieved__name" title={`${item.short_name} — ${item.path}`}>
                  {item.path || "фрагмент"} · {item.short_name}
                  <span className="retrieved__score"> [{item.channel}]</span>
                </span>
                <span className="retrieved__score">{formatPercent(item.score)}</span>
              </li>
            ))}
          </ul>
        )}
      </section>

      <section className="trace__section">
        <h3 className="trace__label">Статусы подвопросов ({trace.subquestions.length})</h3>
        {trace.subquestions.length === 0 ? (
          <p className="trace__note">Вопрос не разложен на подвопросы.</p>
        ) : (
          <ul className="trace__list">
            {trace.subquestions.map((sub) => {
              const label = SUB_LABELS[sub.status] ?? SUB_LABELS.unsupported;
              const tone = label.tone === "ok" ? "ok" : label.tone === "stop" ? "fail" : "wait";
              return (
                <li className={`trace__item trace__item--${tone}`} key={sub.id}>
                  <span>
                    <strong>{label.text}:</strong> {sub.text}
                    {sub.reason && <span className="trace__note"> — {sub.reason}</span>}
                    {sub.source_ids.length > 0 && (
                      <span className="trace__note"> (источники: {sub.source_ids.join(", ")})</span>
                    )}
                  </span>
                </li>
              );
            })}
          </ul>
        )}
      </section>

      <section className="trace__section">
        <h3 className="trace__label">Confidence и причины</h3>
        <div className="confidence">
          <span className="confidence__value">{formatPercent(confidence.value)}</span>
          <span className="confidence__zone">
            <StatusBadge status={confidence.zone} compact />
            <br />
            порог полного {formatPercent(confidence.threshold_high)}, отказ ниже{" "}
            {formatPercent(confidence.threshold_low)}
          </span>
        </div>
        <div className="confidence__scale" style={{ marginTop: 10 }} aria-hidden="true">
          <div className="confidence__fill" style={{ width: `${confidence.value * 100}%` }} />
          <span className="confidence__mark" style={{ left: `${confidence.threshold_low * 100}%` }} />
          <span className="confidence__mark" style={{ left: `${confidence.threshold_high * 100}%` }} />
        </div>
        <ul className="trace__list" style={{ marginTop: 10 }}>
          {confidence.reasons.map((reason, index) => (
            <li className="trace__item trace__item--wait" key={index}>
              <span>{reason}</span>
            </li>
          ))}
        </ul>
        <p className="trace__note">Зона ответа: {zone.label}</p>
      </section>

      <section className="trace__section">
        <h3 className="trace__label">Применённая редакция</h3>
        {trace.edition.applied || trace.edition.note ? (
          <>
            <p className="trace__value">{trace.edition.applied ?? "не применялась"}</p>
            {trace.edition.event_date && (
              <p className="trace__note">Дата события: {formatDate(trace.edition.event_date)}</p>
            )}
            {trace.edition.note && <p className="trace__note">{trace.edition.note}</p>}
            {trace.edition.conflicted && (
              <p className="trace__note">
                <span className="tag tag--revoked">конфликт редакций разрешён кодом (D1)</span>
              </p>
            )}
          </>
        ) : (
          <p className="trace__note">Вопрос не про редакции: дата события не указана, документ не редакционный.</p>
        )}
      </section>

      <section className="trace__section">
        <h3 className="trace__label">Проверка цитат</h3>
        <p className="trace__value">
          {trace.citations.ok} из {trace.citations.checked} подтверждены
        </p>
        {trace.citations.failures.length > 0 ? (
          <ul className="trace__list" style={{ marginTop: 6 }}>
            {trace.citations.failures.map((failure, index) => (
              <li className="trace__item trace__item--fail" key={index}>
                <span>{failure}</span>
              </li>
            ))}
          </ul>
        ) : (
          <p className="trace__note">Числа и реквизиты ответа найдены в evidence (verify-lite).</p>
        )}
      </section>

      {trace.gates.length > 0 && (
        <section className="trace__section">
          <h3 className="trace__label">Гейты и проверки</h3>
          <ul className="trace__list">
            {trace.gates.map((gate, index) => (
              <li
                className={`trace__item ${gate.passed ? "trace__item--ok" : "trace__item--fail"}`}
                key={index}
              >
                <span>
                  <strong>{gate.name}</strong>
                  {gate.detail && <span className="trace__note"> — {gate.detail}</span>}
                </span>
              </li>
            ))}
          </ul>
        </section>
      )}

      <section className="trace__section">
        <h3 className="trace__label">Служебное</h3>
        <p className="trace__note">
          retrieval {trace.timings.retrieval_ms} мс · LLM {trace.timings.llm_ms} мс · всего{" "}
          {trace.timings.total_ms} мс
        </p>
        {trace.model && <p className="trace__note">модель: {trace.model}</p>}
        {trace.degraded.length > 0 && (
          <p className="trace__note">деградации: {trace.degraded.join("; ")}</p>
        )}
      </section>
    </>
  );
}
