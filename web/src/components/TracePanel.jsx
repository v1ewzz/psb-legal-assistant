import StatusBadge from "./StatusBadge.jsx";
import { STATUS } from "../api/contract.js";
import { formatDate, formatPercent } from "../lib/text.js";

/**
 * Блок «Как принято решение» (§18.1 п.3, макет 03_trace_panel.html).
 * Главный продающий экран для экспертов и жюри:
 * Шаги 1-5 читаются сверху вниз как логика рождения ответа.
 */
export default function TracePanel({ answer }) {
  if (!answer?.trace) {
    return (
      <div className="panel__hint" style={{ padding: "var(--sp-4)", color: "var(--text-muted)", fontSize: "var(--fs-body-small)" }}>
        Trace появится вместе с ответом: тип запроса, найденные положения, применённая редакция,
        confidence с причинами, гейты безопасности и проверка цитат.
      </div>
    );
  }

  const trace = answer.trace;
  const confidence = trace.confidence || { value: answer.confidence || 0, reasons: [] };
  const confVal = Number(confidence.value ?? answer.confidence ?? 0);
  const confPct = Math.round(confVal * 100);
  const rawStatus = answer.status || "partial";
  const normStatus = rawStatus === "question_error" ? "error" : rawStatus;

  const totalMs = trace.timings?.total_ms ? (trace.timings.total_ms / 1000).toFixed(1) : null;
  const edition = trace.edition;
  const citations = trace.citations;
  const gates = trace.gates || [];
  const dropped = trace.dropped || [];

  const gaugeColor =
    normStatus === "full" ? "var(--status-full-color)"
    : normStatus === "partial" ? "var(--status-partial-color)"
    : normStatus === "refusal" ? "var(--status-refusal-color)"
    : "var(--status-error-color)";

  return (
    <div className="trace-panel-wrap" style={{ display: "grid", gap: "var(--sp-4)" }}>
      <div className="trace" style={{ background: "var(--surface-panel)", border: "1px solid var(--border-subtle)", borderRadius: "var(--radius-lg)", overflow: "hidden" }}>
        {/* Заголовок панели */}
        <div
          className="trace-header"
          style={{
            display: "flex",
            alignItems: "center",
            gap: 12,
            padding: "12px 16px",
            background: "linear-gradient(90deg, rgba(255,92,43,0.12), transparent 65%)",
            borderBottom: "1px solid var(--border-subtle)",
          }}
        >
          <h2 style={{ fontSize: "var(--fs-h3)", fontWeight: "var(--fw-bold)", margin: 0 }}>
            Как принято решение
          </h2>
          <span style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
            <StatusBadge status={rawStatus} compact />
            {totalMs && (
              <span style={{ fontSize: "var(--fs-caption)", color: "var(--text-muted)" }}>
                {totalMs} с
              </span>
            )}
          </span>
        </div>

        {/* Тело трейса с 5 шагами */}
        <div className="trace-body" style={{ padding: "var(--sp-4)", display: "grid", gap: "var(--sp-4)" }}>

          {/* Шаг 1: Тип запроса */}
          <div className="step step--ok">
            <div className="num">1</div>
            <div className="content">
              <h4>Тип запроса</h4>
              <div className="tagline" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                <span className="tagpill tagpill--ok">
                  {trace.request_type_label || "Прямой запрос по нормам"}
                </span>
                <span className="tagpill">
                  <code>{trace.request_type || "direct"}</code>
                </span>
              </div>
              {trace.standalone_query && (
                <div className="kv" style={{ fontSize: "var(--fs-body-small)", color: "var(--text-secondary)", marginTop: 4 }}>
                  Поисковый запрос: <b style={{ color: "var(--text-primary)" }}>«{trace.standalone_query}»</b>
                </div>
              )}
            </div>
          </div>

          {/* Шаг 2: Найденные положения */}
          <div className="step step--ok">
            <div className="num">2</div>
            <div className="content">
              <h4>Найденные положения ({trace.retrieved?.length || 0})</h4>
              {dropped.length > 0 && (
                <div style={{ marginBottom: 6 }}>
                  <span className="tagpill tagpill--bad">
                    ⚠ Отброшено инъекций / инструкций: {dropped.length}
                  </span>
                </div>
              )}
              {trace.retrieved?.length > 0 ? (
                <div style={{ display: "grid", gap: 4 }}>
                  {trace.retrieved.slice(0, 5).map((item, idx) => (
                    <div key={idx} className="ev" style={{ display: "flex", alignItems: "baseline", gap: 8, fontSize: "var(--fs-body-small)" }}>
                      <span className="ref" style={{ color: "var(--text-link)", fontWeight: 600 }}>
                        {item.path || "фрагмент"}
                      </span>
                      <span style={{ color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                        {item.short_name || item.doc_id}
                      </span>
                      <span className="why" style={{ marginLeft: "auto", color: "var(--text-muted)", fontSize: "var(--fs-caption)" }}>
                        {item.channel} ({item.score ? Number(item.score).toFixed(2) : ""})
                      </span>
                    </div>
                  ))}
                </div>
              ) : (
                <p style={{ color: "var(--text-muted)", fontSize: "var(--fs-body-small)" }}>
                  Положений выше порога в базе не найдено.
                </p>
              )}
            </div>
          </div>

          {/* Шаг 3: Применённая редакция */}
          <div className="step step--ok">
            <div className="num">3</div>
            <div className="content">
              <h4>Применённая редакция</h4>
              {edition?.applied ? (
                <div>
                  <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
                    <span className="tagpill tagpill--ok">✓ {edition.applied}</span>
                    {edition.conflicted && (
                      <span className="tagpill tagpill--warn">конфликт редакций разрешён</span>
                    )}
                  </div>
                  {edition.event_date && (
                    <div className="kv" style={{ marginTop: 6, fontSize: "var(--fs-body-small)" }}>
                      Дата события: <b>{formatDate(edition.event_date)}</b>
                    </div>
                  )}
                  {edition.note && (
                    <div style={{ color: "var(--text-muted)", fontSize: "var(--fs-caption)", marginTop: 4 }}>
                      {edition.note}
                    </div>
                  )}
                </div>
              ) : (
                <div style={{ color: "var(--text-secondary)", fontSize: "var(--fs-body-small)" }}>
                  Документы действуют в единой текущей редакции (без темпоральных конфликтов).
                </div>
              )}
            </div>
          </div>

          {/* Шаг 4: Уровень уверенности (Confidence) */}
          <div className="step step--ok">
            <div className="num">4</div>
            <div className="content">
              <h4>Уровень уверенности</h4>
              <div
                className="conf"
                style={{
                  display: "grid",
                  gridTemplateColumns: "84px 1fr",
                  gap: "var(--sp-4)",
                  background: "var(--surface-card)",
                  border: "1px solid var(--border-subtle)",
                  borderRadius: "var(--radius-md)",
                  padding: "var(--sp-3) var(--sp-4)",
                }}
              >
                <div
                  className="gauge"
                  style={{
                    width: 80,
                    height: 80,
                    borderRadius: "50%",
                    background: `conic-gradient(${gaugeColor} 0 ${confPct}%, var(--skeleton) ${confPct}% 100%)`,
                    position: "relative",
                    alignSelf: "center",
                  }}
                  aria-hidden="true"
                >
                  <div
                    style={{
                      position: "absolute",
                      inset: 8,
                      borderRadius: "50%",
                      background: "var(--surface-card)",
                      display: "grid",
                      placeItems: "center",
                      fontWeight: "bold",
                      fontSize: "var(--fs-h3)",
                    }}
                  >
                    {confVal.toFixed(2)}
                  </div>
                </div>

                <div style={{ display: "grid", alignContent: "center" }}>
                  <div style={{ fontSize: "var(--fs-caption)", color: "var(--text-muted)", marginBottom: 4 }}>
                    Зона: <b style={{ color: gaugeColor }}>{normStatus}</b> (порог полного {formatPercent(confidence.threshold_high || 0.72)})
                  </div>
                  <ul style={{ listStyle: "none", padding: 0, margin: 0, display: "grid", gap: 4 }}>
                    {(confidence.reasons || []).slice(0, 4).map((r, idx) => (
                      <li key={idx} style={{ fontSize: "var(--fs-caption)", color: "var(--text-secondary)", display: "flex", gap: 6 }}>
                        <span style={{ color: "var(--psb-orange-500)", fontWeight: "bold" }}>→</span>
                        <span>{r}</span>
                      </li>
                    ))}
                  </ul>
                </div>
              </div>
            </div>
          </div>

          {/* Шаг 5: Проверка цитат и гейты */}
          <div className="step step--ok">
            <div className="num">5</div>
            <div className="content">
              <h4>Проверка цитат и гейты</h4>
              <div style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
                {citations && (
                  <span className={`tagpill ${citations.ok === citations.checked ? "tagpill--ok" : "tagpill--warn"}`}>
                    Цитаты: {citations.ok}/{citations.checked} подтверждены
                  </span>
                )}
                {gates.map((gate, idx) => (
                  <span key={idx} className={`tagpill ${gate.passed ? "tagpill--ok" : "tagpill--bad"}`}>
                    {gate.passed ? "✓" : "✕"} {gate.name}
                  </span>
                ))}
              </div>
            </div>
          </div>

          {/* Вердикт */}
          <div
            className="verdict"
            style={{
              display: "flex",
              alignItems: "center",
              gap: 12,
              background: normStatus === "full" ? "var(--status-full-bg)" : normStatus === "partial" ? "var(--status-partial-bg)" : "var(--status-refusal-bg)",
              border: `1px solid ${gaugeColor}`,
              borderRadius: "var(--radius-md)",
              padding: "10px 14px",
              fontSize: "var(--fs-body-small)",
            }}
          >
            <span style={{ fontWeight: 600, color: gaugeColor }}>
              Вердикт ядра: {normStatus === "full" ? "Полный ответ сформирован" : normStatus === "partial" ? "Частичный ответ (недостаточно данных)" : "Обоснованный отказ"}
            </span>
          </div>

        </div>
      </div>
    </div>
  );
}
