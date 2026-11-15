import { useMemo, useState } from "react";
import { formatDate } from "../lib/text.js";

const KIND_LABELS = {
  law: "Закон",
  contract: "Договор",
  supplement: "ДС",
  letter: "Письмо",
  regulation: "Регламент",
  memo: "Служебка",
};

/**
 * Вкладка «Документы» в правой панели по макету 01_main_screen.html:
 * Поиск по названию и номерам, быстрый просмотр всех 21 документов базы.
 */
export default function DocsPanel({ docs = [], onOpenDoc }) {
  const [query, setQuery] = useState("");

  const filtered = useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return docs;
    return docs.filter((d) =>
      `${d.short_name} ${d.number || ""} ${d.doc_id}`.toLowerCase().includes(q)
    );
  }, [docs, query]);

  return (
    <div className="docs-panel" style={{ display: "grid", gap: 8 }}>
      <div style={{ padding: "0 2px" }}>
        <input
          type="search"
          placeholder="Поиск по документам базы…"
          value={query}
          onChange={(e) => setQuery(e.target.value)}
          style={{
            width: "100%",
            boxSizing: "border-box",
            padding: "8px 12px",
            background: "var(--surface-card)",
            border: "1px solid var(--border-subtle)",
            borderRadius: "var(--radius-md)",
            color: "var(--text-primary)",
            fontSize: "var(--fs-body-small)",
            outline: "none",
          }}
        />
      </div>

      <div style={{ display: "grid", gap: 6, maxHeight: "calc(100vh - 380px)", overflowY: "auto", paddingRight: 4 }}>
        {filtered.map((doc) => {
          const kindLabel = KIND_LABELS[doc.kind] || (doc.doc_type === "law" ? "Закон" : "Документ");
          return (
            <div
              key={doc.doc_id}
              className="source-row"
              onClick={() => onOpenDoc?.(doc.doc_id)}
              role="button"
              tabIndex={0}
              onKeyDown={(e) => e.key === "Enter" && onOpenDoc?.(doc.doc_id)}
              title={doc.short_name}
            >
              <span className="ico">
                <svg width="15" height="15" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2" strokeLinecap="round">
                  <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                  <path d="M14 2v6h6" />
                </svg>
              </span>
              <div style={{ minWidth: 0, flex: 1 }}>
                <div className="name" style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>
                  {doc.short_name}
                </div>
                <div className="sub">
                  <span className="tagpill" style={{ padding: "1px 6px", fontSize: 10, marginRight: 6 }}>
                    {kindLabel}
                  </span>
                  {doc.doc_date && `от ${formatDate(doc.doc_date)} `}
                  {doc.valid_from && `(с ${formatDate(doc.valid_from)})`}
                  {doc.status === "revoked" && " · утратил силу"}
                </div>
              </div>
            </div>
          );
        })}
        {filtered.length === 0 && (
          <p style={{ color: "var(--text-muted)", fontSize: "var(--fs-caption)", textAlign: "center", padding: 12 }}>
            Ничего не найдено по запросу «{query}»
          </p>
        )}
      </div>
    </div>
  );
}
