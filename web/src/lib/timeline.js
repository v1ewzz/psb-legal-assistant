/**
 * Таймлайн редакций (§18.1 п.4) — визуальный якорь дифференциатора D1.
 *
 * Три цепочки строятся из метаданных документов (`/api/docs`), а не из
 * захардкоженных дат: если бэкенд добавит документ с valid_from/valid_to,
 * шкала подхватит его без правок фронта.
 */

const DAY = 24 * 60 * 60 * 1000;

export const TIMELINE_FROM = "2023-01-01";

function toTime(iso) {
  if (!iso) return null;
  const time = new Date(`${iso}T00:00:00Z`).getTime();
  return Number.isFinite(time) ? time : null;
}

function findDoc(docs, needles) {
  return docs.find((doc) => needles.every((needle) => doc.doc_id.includes(needle))) ?? null;
}

/** Процент позиции даты на шкале [from, to]. */
export function positionPct(iso, from = TIMELINE_FROM, to) {
  const start = toTime(from);
  const end = toTime(to);
  const point = toTime(iso);
  if (start == null || end == null || point == null || end <= start) return 0;
  return Math.min(100, Math.max(0, ((point - start) / (end - start)) * 100));
}

/**
 * @param {Array<Object>} docs метаданные документов из /api/docs
 * @param {string} today дата выгрузки базы (§11.1), по умолчанию 2026-10-10
 */
export function buildTimeline(docs = [], today = "2026-10-10") {
  const reg2023 = findDoc(docs, ["Регламент", "2023"]);
  const reg2025 = findDoc(docs, ["Регламент", "2025"]);
  const supply = findDoc(docs, ["Договор_поставки_П-17-2024"]) ?? findDoc(docs, ["П-17-2024"]);
  const supplement = findDoc(docs, ["Доп_соглашение"]);
  const letter = findDoc(docs, ["Письмо_комплаенс"]);

  const lanes = [
    {
      id: "regulation",
      title: "Регламент согласования договоров",
      caption: "порог одобрения ген. директора: 10 → 15 млн ₽",
      segments: [
        reg2023 && {
          doc_id: reg2023.doc_id,
          label: "ред. 10.01.2023",
          from: reg2023.valid_from ?? reg2023.edition,
          to: reg2023.valid_to ?? today,
          tone: "revoked",
          title: "Действовала до 01.03.2025, порог 10 000 000 ₽",
        },
        reg2025 && {
          doc_id: reg2025.doc_id,
          label: "ред. 01.03.2025",
          from: reg2025.valid_from ?? reg2025.edition,
          to: today,
          tone: "active",
          title: "Действует с 01.03.2025, порог 15 000 000 ₽",
        },
      ].filter(Boolean),
    },
    {
      id: "supply",
      title: "Договор поставки № П-17/2024",
      caption: "договор от 12.02.2024 → ДС № 1 с 01.10.2024",
      segments: [
        supply && {
          doc_id: supply.doc_id,
          label: "договор",
          from: supply.doc_date,
          to: today,
          tone: "base",
          title: "Договор поставки № П-17/2024 с ООО «Альфа-Трейд»",
        },
        supplement && {
          doc_id: supplement.doc_id,
          label: "ДС № 1",
          from: supplement.applies_from ?? supplement.doc_date,
          to: today,
          tone: "active",
          title: "Дополнительное соглашение № 1, применяется с 01.10.2024",
        },
      ].filter(Boolean),
    },
    {
      id: "letter",
      title: "Письмо комплаенс № К-12",
      caption: "издано 15.04.2025 → действует с 21.04.2025",
      segments: [
        letter && {
          doc_id: letter.doc_id,
          label: "издано",
          from: letter.doc_date,
          to: letter.valid_from ?? today,
          tone: "pending",
          title: "Издано 15.04.2025, до 21.04.2025 не применялось",
        },
        letter && {
          doc_id: letter.doc_id,
          label: "действует",
          from: letter.valid_from ?? letter.doc_date,
          to: today,
          tone: "active",
          title: "Действует с 21.04.2025: санкционная проверка иностранных контрагентов",
        },
      ].filter(Boolean),
    },
  ];

  // Горизонтальные метки: переходы редакций и дата выгрузки базы.
  const marks = [
    reg2023?.valid_from && { date: reg2023.valid_from, label: "10.01.2023", kind: "edition" },
    supplement?.applies_from && { date: supplement.applies_from, label: "01.10.2024", kind: "supplement" },
    reg2025?.valid_from && { date: reg2025.valid_from, label: "01.03.2025", kind: "edition" },
    letter?.valid_from && { date: letter.valid_from, label: "21.04.2025", kind: "letter" },
    today && { date: today, label: "база выгружена", kind: "today" },
  ].filter(Boolean);

  return { from: TIMELINE_FROM, to: today, marks, lanes };
}

/** Документы, относящиеся к таймлайну (для подсветки выбранной цепочки). */
export function timelineDocIds(timeline) {
  const ids = new Set();
  for (const lane of timeline?.lanes ?? []) {
    for (const segment of lane.segments) ids.add(segment.doc_id);
  }
  return ids;
}

export { DAY };
