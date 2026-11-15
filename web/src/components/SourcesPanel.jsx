import { formatDate, formatPercent, shorten } from "../lib/text.js";

/**
 * Панель источников (§18.1 п.2): клик → документ с подсвеченным фрагментом,
 * хлебные крошки, дата/редакция, для документов компании — doc_date и valid_from.
 */
export default function SourcesPanel({ answer, onOpenSource }) {
  const sources = answer?.sources ?? [];

  if (!answer) {
    return <p className="panel__hint">Задайте вопрос — здесь появятся найденные положения с цитатами и реквизитами.</p>;
  }

  if (sources.length === 0) {
    return (
      <p className="panel__hint">
        У этого ответа источников нет: вопрос ушёл за пределы базы знаний, отказ обоснован
        результатами поиска (см. вкладку «Как принято решение»).
      </p>
    );
  }

  return (
    <>
      {sources.map((source) => (
        <button
          key={source.id}
          type="button"
          className="src-card"
          onClick={() => onOpenSource(source)}
          title="Открыть документ с подсвеченным фрагментом"
        >
          <span className="src-card__top">
            <span className="src-card__ref">{source.path || "фрагмент"}</span>
            <span className="src-card__name">{source.short_name}</span>
          </span>

          {source.quote && <span className="src-card__quote">«{shorten(source.quote, 220)}»</span>}

          <span className="src-card__meta">
            {source.crumbs.length > 1 && (
              <span className="tag">{source.crumbs.slice(1).join(" → ")}</span>
            )}
            {source.doc_date && <span className="tag">от {formatDate(source.doc_date)}</span>}
            {source.valid_from && <span className="tag">действует с {formatDate(source.valid_from)}</span>}
            {source.valid_to && <span className="tag">до {formatDate(source.valid_to)}</span>}
            {source.edition && <span className="tag">ред. {formatDate(source.edition)}</span>}
            {source.applies_from && (
              <span className="tag">применяется с {formatDate(source.applies_from)}</span>
            )}
            {source.status === "active" && <span className="tag tag--active">действует</span>}
            {source.status === "revoked" && <span className="tag tag--revoked">утратил силу</span>}
            <span className="tag">релевантность {formatPercent(source.score)}</span>
          </span>
        </button>
      ))}
    </>
  );
}
