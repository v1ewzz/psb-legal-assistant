import { STATUS } from "../api/contract.js";

function StatusIcon({ status }) {
  if (status === "full") {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        <path d="m9 12 2 2 4-4" />
      </svg>
    );
  }
  if (status === "partial") {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" aria-hidden="true">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        <path d="M12 2v20" />
      </svg>
    );
  }
  if (status === "refusal") {
    return (
      <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
        <path d="M12 22s8-4 8-10V5l-8-3-8 3v7c0 6 8 10 8 10z" />
        <path d="m9 9 6 6M15 9l-6 6" />
      </svg>
    );
  }
  // error / question_error
  return (
    <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="currentColor" strokeWidth="2.2" strokeLinecap="round" strokeLinejoin="round" aria-hidden="true">
      <circle cx="12" cy="12" r="10" />
      <path d="M9.09 9a3 3 0 0 1 5.83 1c0 2-3 3-3 3" />
      <line x1="12" y1="17" x2="12.01" y2="17" />
    </svg>
  );
}

/**
 * Бейдж статуса ответа (§18.1 п.1, дизайн-система Ивана):
 * различим за 1 секунду без чтения текста — иконка + цвет + подпись.
 * Классы: .badge.badge--full, .badge.badge--partial, .badge.badge--refusal, .badge.badge--error
 */
export default function StatusBadge({ status, streaming = false, compact = false }) {
  const normStatus = status === "question_error" ? "error" : (status || "partial");
  const meta = STATUS[status] ?? STATUS[normStatus] ?? STATUS.partial;
  const modifier = normStatus === "full" ? "badge--full"
    : normStatus === "partial" ? "badge--partial"
    : normStatus === "refusal" ? "badge--refusal"
    : "badge--error";

  return (
    <span className={`badge ${modifier}`} title={meta.label}>
      <StatusIcon status={normStatus} />
      <span>{compact ? meta.short : meta.label}</span>
      {streaming && <span className="sr-only">(формируется)</span>}
    </span>
  );
}
