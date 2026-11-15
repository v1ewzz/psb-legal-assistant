import { formatPercent } from "../lib/text.js";

/**
 * Индикатор confidence по макету Ивана:
 * круговой ring (conic-gradient) + подпись «confidence 0.XX».
 */
export default function ConfidenceMeter({ value = 0, status = "full" }) {
  const safe = Math.min(1, Math.max(0, Number(value) || 0));
  const pct = Math.round(safe * 100);

  const colorVar =
    status === "full" ? "var(--status-full-color)"
    : status === "partial" ? "var(--status-partial-color)"
    : status === "refusal" ? "var(--status-refusal-color)"
    : "var(--status-error-color)";

  const ringStyle = {
    background: `conic-gradient(${colorVar} 0 ${pct}%, var(--skeleton) ${pct}% 100%)`,
  };

  return (
    <span
      className="confidence-dot"
      title={`Уверенность ответа: ${formatPercent(safe)} (${pct}%)`}
      aria-label={`Уверенность ответа: ${formatPercent(safe)}`}
    >
      <span className="ring" style={ringStyle} aria-hidden="true" />
      <span>confidence {safe.toFixed(2)}</span>
    </span>
  );
}
