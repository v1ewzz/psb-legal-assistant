import { useEffect, useMemo, useRef, useState } from "react";
import { loadDoc } from "../api/docs.js";
import { formatDate, highlightParts } from "../lib/text.js";

/**
 * Просмотр документа с подсветкой фрагмента (§18.1 п.2, макет 05_source_viewer.html).
 * Подсветка — строго по индексам символов через текстовые узлы (без innerHTML!).
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
    return chunks.filter(
      (chunk) => chunk.path !== "документ целиком" || chunk.chunk_id === anchorId,
    );
  }, [doc, source?.anchor?.chunk_id]);

  const crumbs = source?.crumbs?.length ? source.crumbs : [doc?.short_name ?? "Документ"];

  return (
    <div
      className="modal-backdrop"
      role="dialog"
      aria-modal="true"
      onClick={(e) => {
        if (e.target === e.currentTarget) onClose();
      }}
    >
      <div className="modal" style={{ width: "90%", maxWidth: 860, margin: "auto" }}>
        {/* Шапка модалки */}
        <div className="modal-head">
          <div style={{ flex: 1, minWidth: 0 }}>
            {/* Хлебные крошки */}
            <div className="crumbs">
              {crumbs.map((crumb, idx) => (
                <span key={idx}>
                  {idx > 0 && <span className="sep"> › </span>}
                  <span className={idx === crumbs.length - 1 ? "here" : undefined}>{crumb}</span>
                </span>
              ))}
              {source?.path && crumbs.length === 1 && <span className="here"> · {source.path}</span>}
            </div>

            {/* Название документа */}
            <h2 className="title" style={{ marginTop: 6, fontSize: "var(--fs-h3)", fontWeight: "bold" }}>
              {doc?.short_name ?? source?.short_name ?? "Документ"}
            </h2>

            {/* Мета-теги */}
            <div className="meta" style={{ display: "flex", flexWrap: "wrap", gap: 6, marginTop: 8 }}>
              {source?.doc_date && (
                <span className="tagpill">от {formatDate(source.doc_date)}</span>
              )}
              {source?.valid_from && (
                <span className="tagpill tagpill--ok">действует с {formatDate(source.valid_from)}</span>
              )}
              {source?.valid_to && (
                <span className="tagpill tagpill--bad">утратил силу {formatDate(source.valid_to)}</span>
              )}
              {source?.doc_type && (
                <span className="tagpill">{source.doc_type === "law" ? "Федеральный закон" : "Документ компании"}</span>
              )}
              {source?.score && (
                <span className="tagpill">релевантность: {(Number(source.score) * 100).toFixed(0)}%</span>
              )}
            </div>
          </div>

          <button type="button" className="close" onClick={onClose} aria-label="Закрыть">
            ✕
          </button>
        </div>

        {/* Тело документа */}
        <div className="modal-body" style={{ maxHeight: "60vh", overflowY: "auto", padding: "var(--sp-4) var(--sp-5)" }}>
          {error && <p style={{ color: "var(--status-refusal-color)" }}>{error}</p>}
          {!doc && !error && (
            <div style={{ display: "grid", gap: 10 }}>
              <div className="skeleton" style={{ width: "80%", height: 16 }} />
              <div className="skeleton" style={{ width: "65%", height: 16 }} />
              <div className="skeleton" style={{ width: "72%", height: 16 }} />
            </div>
          )}

          {/* Цитата в рамочке */}
          {source?.quote && (
            <div className="quote-anchor">
              <span style={{ fontSize: "var(--fs-caption)", color: "var(--psb-orange-500)", fontWeight: "bold", display: "block", marginBottom: 4 }}>
                Фрагмент, подтверждающий ответ:
              </span>
              <b>«{source.quote}»</b>
            </div>
          )}

          {/* Чанки документа с подсветкой */}
          {visibleChunks.map((chunk) => {
            const isAnchorChunk = chunk.chunk_id === source?.anchor?.chunk_id;
            const start = isAnchorChunk ? source?.anchor?.start : undefined;
            const end = isAnchorChunk ? source?.anchor?.end : undefined;
            const parts = highlightParts(chunk.text, start, end);

            return (
              <div
                key={chunk.chunk_id}
                ref={isAnchorChunk ? chunkRef : null}
                className="doc-p"
                style={{
                  background: isAnchorChunk ? "rgba(255,92,43,0.06)" : undefined,
                  borderLeft: isAnchorChunk ? "3px solid var(--psb-orange-500)" : undefined,
                  paddingLeft: isAnchorChunk ? 10 : 0,
                  borderRadius: 4,
                  marginBottom: 14,
                }}
              >
                {chunk.path && (
                  <span className="ref-in-text" style={{ fontWeight: 600, color: "var(--text-muted)", fontSize: "var(--fs-caption)" }}>
                    {chunk.path}
                  </span>
                )}
                <span>
                  {parts.map((part, index) =>
                    part.marked ? (
                      <mark key={index} className="hl">
                        {part.text}
                      </mark>
                    ) : (
                      <span key={index}>{part.text}</span>
                    ),
                  )}
                </span>
              </div>
            );
          })}
        </div>

        {/* Подвал модалки */}
        <div className="modal-foot">
          <span>ID чанка: {source?.anchor?.chunk_id || "весь документ"}</span>
          <button type="button" className="btn" style={{ marginLeft: "auto" }} onClick={onClose}>
            Закрыть
          </button>
        </div>
      </div>
    </div>
  );
}
