import { useEffect, useMemo, useRef, useState } from "react";
import { loadDoc } from "../api/docs.js";
import { formatDate, highlightParts, splitPath } from "../lib/text.js";

/**
 * Просмотр документа с подсветкой фрагмента (§18.1 п.2, §18 DoD).
 *
 * Подсветка — по индексам из контракта (`anchor.start/end` внутри чанка).
 * Если бэкенд не дал индексы, берём смещение цитаты в тексте чанка; и в том,
 * и в другом случае в DOM попадают только текстовые узлы — innerHTML нет.
 */
export default function DocumentModal({ source, onClose }) {
  const [doc, setDoc] = useState(null);
  const [error, setError] = useState("");
  const chunkRef = useRef(null);

  useEffect(() => {
    if (!source?.doc_id) return;
    let alive = true;
    setDoc(null);
    setError("");
    loadDoc(source.doc_id)
      .then((loaded) => {
        if (!alive) return;
        if (!loaded) setError("Документ не найден в индексе.");
        else setDoc(loaded);
      })
      .catch((err) => alive && setError(err.message || "Не удалось открыть документ"));
    return () => {
      alive = false;
    };
  }, [source?.doc_id]);

  useEffect(() => {
    if (doc && chunkRef.current) {
      chunkRef.current.scrollIntoView({ block: "center", behavior: "smooth" });
    }
  }, [doc, source?.anchor?.chunk_id]);

  useEffect(() => {
    function onKey(event) {
      if (event.key === "Escape") onClose();
    }
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [onClose]);

  const visibleChunks = useMemo(() => {
    const chunks = doc?.chunks ?? [];
    const anchorId = source?.anchor?.chunk_id;
    // Чанк «документ целиком» дублирует абзацы — показываем его только если
    // именно в него указывает анкор.
    return chunks.filter(
      (chunk) => chunk.path !== "документ целиком" || chunk.chunk_id === anchorId,
    );
  }, [doc, source?.anchor?.chunk_id]);

  const crumbs = source?.crumbs?.length ? source.crumbs : [doc?.short_name ?? "Документ"];

  return (
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="doc-title"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal__card">
        <header className="modal__head">
          <div style={{ flex: 1 }}>
            <h2 className="modal__title" id="doc-title">
              {doc?.short_name ?? source?.short_name ?? "Документ"}
            </h2>
            <div className="crumbs">
              {crumbs.map((crumb, index) => (
                <span key={`${crumb}-${index}`}>
                  {index > 0 && <span className="crumbs__sep"> › </span>}
                  <span className={index === crumbs.length - 1 ? "crumbs__last" : undefined}>
                    {crumb}
                  </span>
                </span>
              ))}
              {source?.path && crumbs.length === 1 && <span className="crumbs__last"> · {source.path}</span>}
            </div>
          </div>
          <button type="button" className="btn" onClick={onClose} aria-label="Закрыть документ">
            Закрыть ✕
          </button>
        </header>

        <div className="modal__body">
          {error && <p className="answer__text answer__text--error">{error}</p>}
          {!doc && !error && (
            <>
              <div className="skeleton" style={{ width: "80%", marginBottom: 10 }} />
              <div className="skeleton" style={{ width: "65%", marginBottom: 10 }} />
              <div className="skeleton" style={{ width: "72%" }} />
            </>
          )}
          {doc &&
            visibleChunks.map((chunk) => {
              const isAnchor = chunk.chunk_id === source?.anchor?.chunk_id;
              let start = isAnchor ? source?.anchor?.start : 0;
              let end = isAnchor ? source?.anchor?.end : 0;
              if (isAnchor && !(end > start) && source?.quote) {
                const found = chunk.text.indexOf(source.quote);
                if (found >= 0) {
                  start = found;
                  end = found + source.quote.length;
                }
              }
              const parts = highlightParts(chunk.text, start, end);
              return (
                <p
                  className="doc__chunk"
                  data-chunk={isAnchor}
                  key={chunk.chunk_id}
                  ref={isAnchor ? chunkRef : undefined}
                >
                  {parts.map((part, index) =>
                    part.marked ? (
                      <mark key={index}>{part.text}</mark>
                    ) : (
                      <span key={index}>{part.text}</span>
                    ),
                  )}
                </p>
              );
            })}
          {doc && visibleChunks.length === 0 && (
            <p className="answer__text">Текст документа недоступен в индексе.</p>
          )}
        </div>

        <footer className="modal__foot">
          <span className="tag">{source?.path || splitPath(source?.path).join(", ") || "фрагмент"}</span>
          {doc?.doc_date && <span>дата документа: {formatDate(doc.doc_date)}</span>}
          {doc?.valid_from && <span>действует с {formatDate(doc.valid_from)}</span>}
          {doc?.valid_to && <span>действовал до {formatDate(doc.valid_to)}</span>}
          {doc?.edition && <span>редакция от {formatDate(doc.edition)}</span>}
          {doc?.applies_from && <span>применяется с {formatDate(doc.applies_from)}</span>}
          {doc?.status === "active" && <span className="tag tag--active">действует</span>}
          {doc?.status === "revoked" && <span className="tag tag--revoked">утратил силу</span>}
          <span style={{ marginLeft: "auto" }}>фрагмент подсвечен по индексам чанка</span>
        </footer>
      </div>
    </div>
  );
}
