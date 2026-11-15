import { useEffect, useMemo, useState } from "react";
import { loadDoc } from "../api/docs.js";
import { formatDate, shorten } from "../lib/text.js";

const KIND_LABELS = {
  law: "Закон",
  contract: "Договор",
  supplement: "Доп. соглашение",
  letter: "Письмо",
  regulation: "Регламент",
  memo: "Служебная записка",
};

/**
 * Список документов базы с поиском (§18.1 п.5): по названию — мгновенно,
 * по полному тексту — по чанкам (в SSE-режиме тот же контракт отдаёт бэкенд).
 */
export default function DocsDrawer({ docs, onOpenDoc, onClose }) {
  const [query, setQuery] = useState("");
  const [hits, setHits] = useState([]);
  const [searching, setSearching] = useState(false);

  const byTitle = useMemo(() => {
    const needle = query.trim().toLowerCase();
    if (!needle) return docs;
    return docs.filter((doc) =>
      `${doc.short_name} ${doc.number ?? ""} ${doc.file_name ?? ""}`
        .toLowerCase()
        .includes(needle),
    );
  }, [docs, query]);

  useEffect(() => {
    const needle = query.trim().toLowerCase();
    if (needle.length < 3) {
      setHits([]);
      return undefined;
    }
    let alive = true;
    setSearching(true);
    const timer = setTimeout(async () => {
      const found = [];
      for (const doc of docs) {
        try {
          const full = await loadDoc(doc.doc_id);
          const matches = (full?.chunks ?? []).filter((chunk) =>
            chunk.text.toLowerCase().includes(needle),
          );
          if (matches.length > 0) {
            found.push({
              doc,
              hits: matches.slice(0, 2).map((chunk) => ({ path: chunk.path, text: chunk.text })),
            });
          }
        } catch {
          /* документ недоступен — пропускаем */
        }
      }
      if (alive) {
        setHits(found);
        setSearching(false);
      }
    }, 220);

    return () => {
      alive = false;
      clearTimeout(timer);
      setSearching(false);
    };
  }, [docs, query]);

  const hitIds = new Set(hits.map((hit) => hit.doc.doc_id));

  return (
    <div
      className="modal"
      role="dialog"
      aria-modal="true"
      aria-labelledby="docs-title"
      onClick={(event) => {
        if (event.target === event.currentTarget) onClose();
      }}
    >
      <div className="modal__card" style={{ width: "min(760px, 100%)" }}>
        <header className="modal__head">
          <div style={{ flex: 1 }}>
            <h2 className="modal__title" id="docs-title">
              Документы базы знаний
            </h2>
            <p className="msg__meta">
              {docs.length} документов · поиск по названию и полному тексту
            </p>
          </div>
          <button type="button" className="btn" onClick={onClose}>
            Закрыть ✕
          </button>
        </header>

        <div className="modal__body">
          <label className="sr-only" htmlFor="docs-search">
            Поиск по документам
          </label>
          <input
            id="docs-search"
            className="docs__search"
            placeholder="Например: аренда, 450 000, санкционная проверка"
            value={query}
            onChange={(event) => setQuery(event.target.value)}
            autoFocus
          />

          {searching && <p className="msg__meta">Ищем по полному тексту…</p>}

          {hits.length > 0 && (
            <>
              <p className="trace__label" style={{ marginTop: 12 }}>
                Найдено в тексте ({hits.length})
              </p>
              <ul className="docs__list">
                {hits.map(({ doc, hits: docHits }) => (
                  <li key={`hit-${doc.doc_id}`}>
                    <button type="button" className="docs__item" onClick={() => onOpenDoc(doc.doc_id)}>
                      <span className="docs__itemTitle">{doc.short_name}</span>
                      {docHits.map((hit, index) => (
                        <span className="docs__hit" key={index}>
                          {hit.path}: {shorten(hit.text, 150)}
                        </span>
                      ))}
                    </button>
                  </li>
                ))}
              </ul>
            </>
          )}

          <p className="trace__label" style={{ marginTop: 12 }}>
            {query.trim() ? `По названию (${byTitle.length})` : `Все документы (${byTitle.length})`}
          </p>
          {query.trim() && byTitle.length === 0 && hits.length > 0 && (
            <p className="panel__hint">
              В названиях совпадений нет, но текст найден выше — откройте документ из блока
              «Найдено в тексте».
            </p>
          )}
          <ul className="docs__list">
            {byTitle.map((doc) => (
              <li key={doc.doc_id}>
                <button type="button" className="docs__item" onClick={() => onOpenDoc(doc.doc_id)}>
                  <span className="docs__itemTitle">{doc.short_name}</span>
                  <span className="docs__itemMeta">
                    <span className="tag">{KIND_LABELS[doc.kind] ?? doc.kind ?? "документ"}</span>
                    {doc.doc_type === "law" ? (
                      <span className="tag">закон</span>
                    ) : (
                      <span className="tag">компания</span>
                    )}
                    {doc.doc_date && <span>от {formatDate(doc.doc_date)}</span>}
                    {doc.valid_from && <span>с {formatDate(doc.valid_from)}</span>}
                    {doc.valid_to && <span>до {formatDate(doc.valid_to)}</span>}
                    {doc.edition && <span>ред. {formatDate(doc.edition)}</span>}
                    {doc.applies_from && <span>применяется с {formatDate(doc.applies_from)}</span>}
                    {hitIds.has(doc.doc_id) && <span className="tag tag--active">есть в тексте</span>}
                  </span>
                </button>
              </li>
            ))}
          </ul>

          {byTitle.length === 0 && !searching && hits.length === 0 && (
            <p className="panel__hint">Ничего не найдено: попробуйте другое слово или реквизит.</p>
          )}
        </div>
      </div>
    </div>
  );
}
