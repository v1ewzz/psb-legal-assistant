import { formatDate, formatDateLong } from "../lib/text.js";
import { positionPct } from "../lib/timeline.js";

/**
 * Таймлайн редакций (§18.1 п.4, макет 04_timeline.html):
 * Уникальный визуальный якорь дифференциатора D1.
 * Показывает цепочки редакций во времени:
 * 1. Регламент согласования договоров (ред. 2023 -> ред. 2025)
 * 2. Договор аренды А-08 / поставки П-17 + ДС №1
 * 3. Письмо комплаенс-службы № К-12
 */
export default function Timeline({ timeline, eventDate, activeDocId, onOpenDoc }) {
  if (!timeline) {
    return (
      <section className="timeline">
        <div className="panel" style={{ padding: "var(--sp-4)" }}>
          <div className="skeleton" style={{ width: "60%", height: 18, marginBottom: 12 }} />
          <div className="skeleton" style={{ width: "90%", height: 36 }} />
        </div>
      </section>
    );
  }

  const { from, to, lanes } = timeline;
  const todayDate = timeline.today || "2026-10-10";
  const todayPct = positionPct(todayDate, from, to);

  const eventPct =
    eventDate && Number.isFinite(new Date(`${eventDate}T00:00:00Z`).getTime())
      ? positionPct(eventDate, from, to)
      : null;

  return (
    <section className="timeline" aria-label="Таймлайн редакций документов">
      <div className="panel" style={{ padding: "var(--sp-3) var(--sp-4)" }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
          <h3 style={{ margin: 0, display: "flex", alignItems: "center", gap: 8, fontSize: "var(--fs-body-small)", textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)" }}>
            <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="var(--psb-orange-500)" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <rect x="3" y="4" width="18" height="18" rx="2" />
              <path d="M16 2v4M8 2v4M3 10h18" />
            </svg>
            Таймлайн редакций во времени (D1)
          </h3>

          <div className="legend" style={{ marginLeft: "auto", display: "flex", gap: "var(--sp-3)", fontSize: "var(--fs-caption)", color: "var(--text-muted)", flexWrap: "wrap" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <i style={{ display: "inline-block", width: 12, height: 4, borderRadius: 2, background: "var(--surface-hover)", border: "1px solid var(--border-strong)" }} />
              действовала в период
            </span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <i style={{ display: "inline-block", width: 12, height: 4, borderRadius: 2, background: "rgba(47,191,113,0.3)", border: "1px solid var(--status-full-color)" }} />
              применена в ответе
            </span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}>
              <i style={{ display: "inline-block", width: 12, height: 2, background: "var(--psb-orange-500)" }} />
              выгрузка базы (10.10.2026)
            </span>
            {eventDate && (
              <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--text-link)" }}>
                <i style={{ display: "inline-block", width: 8, height: 8, background: "var(--text-link)", borderRadius: "50%" }} />
                событие вопроса: {formatDate(eventDate)}
              </span>
            )}
          </div>
        </div>

        {/* Цепочки редакций */}
        <div style={{ marginTop: 10, display: "grid", gap: 10 }}>
          {lanes.map((lane) => (
            <div key={lane.id} className="chain" style={{ display: "grid", gridTemplateColumns: "220px 1fr", gap: 12, alignItems: "center" }}>
              <div style={{ fontSize: "var(--fs-caption)", color: "var(--text-secondary)", overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={lane.caption}>
                <b style={{ color: "var(--text-primary)" }}>{lane.title}</b>
              </div>

              <div className="track" style={{ position: "relative", height: 28, background: "var(--surface-card)", borderRadius: "var(--radius-sm)", border: "1px solid var(--border-subtle)", overflow: "hidden" }}>
                {/* Метка "сегодня" */}
                {todayPct > 0 && (
                  <div
                    className="today"
                    style={{
                      position: "absolute",
                      left: `${todayPct}%`,
                      top: 0,
                      bottom: 0,
                      width: 2,
                      background: "var(--psb-orange-500)",
                      zIndex: 3,
                    }}
                    title={`сегодня · ${todayDate}`}
                  />
                )}

                {/* Метка события вопроса */}
                {eventPct != null && (
                  <div
                    className="qmark"
                    style={{
                      position: "absolute",
                      left: `${eventPct}%`,
                      top: 0,
                      bottom: 0,
                      width: 2,
                      background: "var(--text-link)",
                      zIndex: 4,
                      boxShadow: "0 0 8px var(--text-link)",
                    }}
                    title={`Дата события: ${eventDate}`}
                  />
                )}

                {/* Сегменты редакций */}
                {lane.segments.map((seg, idx) => {
                  const left = positionPct(seg.from, from, to);
                  const right = positionPct(seg.to, from, to);
                  const width = Math.max(3, right - left);
                  const isActive = seg.tone === "active" || seg.doc_id === activeDocId;

                  return (
                    <button
                      key={idx}
                      type="button"
                      className={`seg ${isActive ? "seg--active" : ""}`}
                      style={{
                        position: "absolute",
                        left: `${left}%`,
                        width: `${width}%`,
                        top: 3,
                        bottom: 3,
                        borderRadius: "var(--radius-sm)",
                        padding: "0 8px",
                        display: "flex",
                        alignItems: "center",
                        fontSize: "var(--fs-caption)",
                        whiteSpace: "nowrap",
                        overflow: "hidden",
                        textOverflow: "ellipsis",
                        background: isActive ? "rgba(47,191,113,0.22)" : "var(--surface-card-elevated)",
                        border: `1px solid ${isActive ? "var(--status-full-color)" : "var(--border-strong)"}`,
                        color: isActive ? "var(--status-full-color)" : "var(--text-secondary)",
                        cursor: "pointer",
                        zIndex: 2,
                      }}
                      title={`${seg.title} — нажать для просмотра`}
                      onClick={() => onOpenDoc?.(seg.doc_id)}
                    >
                      <span
                        style={{
                          width: 6,
                          height: 6,
                          borderRadius: "50%",
                          background: isActive ? "var(--status-full-color)" : "var(--psb-orange-500)",
                          marginRight: 6,
                          flex: "none",
                        }}
                      />
                      <b style={{ color: isActive ? "var(--status-full-color)" : "var(--text-primary)" }}>{seg.label}</b>
                    </button>
                  );
                })}
              </div>
            </div>
          ))}
        </div>

        {eventDate && (
          <div style={{ textAlign: "right", fontSize: "var(--fs-caption)", color: "var(--text-link)", marginTop: 6 }}>
            ● дата события {formatDateLong(eventDate)} учтена при выборе действующей редакции
          </div>
        )}
      </div>
    </section>
  );
}
