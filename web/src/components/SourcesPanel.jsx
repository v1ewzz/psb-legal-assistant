import { formatDate, formatPercent, shorten } from "../lib/text.js";

/**
 * Панель источников (§18.1 п.2, макет 01_main_screen.html):
 * Карточки положений .source-row: иконка, название, реквизиты, скор.
 * Поддерживает витрину дифференциатора D2: отброшенные инъекции (.source-row.dropped).
 */
export default function SourcesPanel({ answer, onOpenSource }) {
  const sources = answer?.sources ?? [];
  const dropped = answer?.trace?.dropped ?? [];

  if (!answer) {
    return (
      <div className="panel__hint" style={{ padding: "var(--sp-4)", color: "var(--text-muted)", fontSize: "var(--fs-body-small)" }}>
        Задайте вопрос — здесь появятся найденные положения базы знаний с реквизитами и оценкой релевантности.
      </div>
    );
  }

  if (sources.length === 0 && dropped.length === 0) {
    return (
      <div className="panel__hint" style={{ padding: "var(--sp-4)", color: "var(--text-muted)", fontSize: "var(--fs-body-small)" }}>
        У этого ответа источников нет: вопрос ушёл за пределы базы знаний, отказ обоснован результатами поиска (см. вкладку «Как принято решение»).
      </div>
    );
  }

  return (
    <div className="sources-container" style={{ display: "grid", gap: 6 }}>
      {/* Активные источники */}
      {sources.map((source) => {
        let sub = "";
        if (source.crumbs?.length > 1) {
          sub = source.crumbs.slice(1).join(" · ");
        }
        if (source.doc_date) sub += (sub ? " · " : "") + `от ${formatDate(source.doc_date)}`;
        if (source.valid_from) sub += (sub ? " · " : "") + `с ${formatDate(source.valid_from)}`;
        if (source.status === "revoked") sub += (sub ? " · " : "") + "утратил силу";

        return (
          <div
            key={source.id}
            className="source-row"
            onClick={() => onOpenSource(source)}
            title="Открыть документ с подсвеченным фрагментом"
            role="button"
            tabIndex={0}
            onKeyDown={(e) => e.key === "Enter" && onOpenSource(source)}
          >
            <span className="ico">
              <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                <path d="M14 2v6h6" />
              </svg>
            </span>
            <div style={{ minWidth: 0, flex: 1 }}>
              <div className="name" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {source.short_name} {source.path ? `· ${source.path}` : ""}
              </div>
              <div className="sub" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                {sub || shorten(source.quote, 60)}
              </div>
            </div>
            <span className="score">{source.score ? Number(source.score).toFixed(2) : "—"}</span>
          </div>
        );
      })}

      {/* Отброшенные чанки (детектор инъекций D2) */}
      {dropped.map((item, idx) => (
        <div key={`dropped-${idx}`} className="source-row dropped" title="Чанк отброшен детектором compromised-инструкций">
          <span className="ico" style={{ color: "var(--status-refusal-color)" }}>
            <svg width="16" height="16" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
              <path d="M10.29 3.86L1.82 18a2 2 0 0 0 1.71 3h16.94a2 2 0 0 0 1.71-3L13.71 3.86a2 2 0 0 0-3.42 0z" />
              <line x1="12" y1="9" x2="12" y2="13" />
              <line x1="12" y1="17" x2="12.01" y2="17" />
            </svg>
          </span>
          <div style={{ minWidth: 0, flex: 1 }}>
            <div className="name" style={{ color: "var(--status-refusal-color)" }}>
              ⚠ Чанк отброшен: {item.reason || "инструкция / инъекция"}
            </div>
            <div className="sub">{item.doc_id || item.short_name || "детектор безопасности"} · в промпт не попал</div>
          </div>
          <span className="score">—</span>
        </div>
      ))}
    </div>
  );
}
