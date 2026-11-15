import { formatPercent } from "../lib/text.js";

/** Компактный индикатор confidence (полоса + число) для карточки ответа. */
export default function ConfidenceMeter({ value = 0, label = "confidence" }) {
  const safe = Math.min(1, Math.max(0, Number(value) || 0));
  return (
    <span
      className="conf"
      title={`${label}: ${formatPercent(safe)}`}
      aria-label={`${label}: ${formatPercent(safe)}`}
    >
      <span className="conf__bar" aria-hidden="true">
        <span className="conf__fill" style={{ width: `${safe * 100}%` }} />
      </span>
      {formatPercent(safe)}
    </span>
  );
}
