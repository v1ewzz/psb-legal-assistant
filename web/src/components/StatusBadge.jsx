import { STATUS } from "../api/contract.js";

/**
 * Бейдж статуса ответа (§18.1 п.1, DoD): различим без чтения текста —
 * иконка + цвет + подпись. Семантика цветов зафиксирована в токенах.
 */
export default function StatusBadge({ status, streaming = false, compact = false }) {
  const meta = STATUS[status] ?? STATUS.partial;
  return (
    <span className="badge" data-tone={meta.tone} title={meta.label}>
      <span className="badge__icon" aria-hidden="true">
        {meta.icon}
      </span>
      {compact ? meta.short : meta.label}
      {streaming && <span className="sr-only">(формируется)</span>}
    </span>
  );
}
