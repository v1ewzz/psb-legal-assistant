/** Форматирование и безопасная разметка текста (без innerHTML — §18.2). */

const MONTHS = [
  "января", "февраля", "марта", "апреля", "мая", "июня",
  "июля", "августа", "сентября", "октября", "ноября", "декабря",
];

export function formatDate(iso) {
  if (!iso || typeof iso !== "string") return "";
  const match = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return iso;
  return `${match[3]}.${match[2]}.${match[1]}`;
}

export function formatDateLong(iso) {
  if (!iso || typeof iso !== "string") return "";
  const match = iso.match(/^(\d{4})-(\d{2})-(\d{2})$/);
  if (!match) return iso;
  const month = MONTHS[Number(match[2]) - 1] ?? "";
  return `${Number(match[3])} ${month} ${match[1]} г.`;
}

export function formatPercent(value) {
  return `${Math.round(Number(value || 0) * 100)}%`;
}

/**
 * Подсветка по индексам: бэкенд отдаёт start/end внутри текста чанка,
 * фронт режет строку и оборачивает середину в <mark>. Никакой разметки из
 * данных не исполняется — всё уходит текстовыми узлами.
 */
export function highlightParts(text, start, end) {
  const value = String(text ?? "");
  const from = Number(start);
  const to = Number(end);
  const valid =
    Number.isFinite(from) &&
    Number.isFinite(to) &&
    from >= 0 &&
    to > from &&
    to <= value.length;
  if (!valid) return [{ text: value, marked: false }];
  return [
    { text: value.slice(0, from), marked: false },
    { text: value.slice(from, to), marked: true },
    { text: value.slice(to), marked: false },
  ].filter((part) => part.text.length > 0);
}

/** Хлебные крошки: «ч. 1, абз. 3» → ["ч. 1", "абз. 3"]. */
export function splitPath(path) {
  return String(path || "")
    .split(/,\s*/)
    .map((part) => part.trim())
    .filter(Boolean);
}

/** Короткая цитата для чипа источника. */
export function shorten(text, limit = 120) {
  const value = String(text ?? "").replace(/\s+/g, " ").trim();
  return value.length > limit ? `${value.slice(0, limit - 1)}…` : value;
}
