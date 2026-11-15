import { formatDate, formatDateLong } from "../lib/text.js";
import { positionPct } from "../lib/timeline.js";

/**
 * Таймлайн редакций (§18.1 п.4) — визуальный якорь D1.
 * Три цепочки: Регламент (2023 → 01.03.2025), ДС № 1 к договору П-17/2024
 * (с 01.10.2024), письмо К-12 (издано 15.04.2025 → действует 21.04.2025).
 * Если в ответе есть дата события — на шкале появляется вертикальная метка.
 */
export default function Timeline({ timeline, eventDate, onOpenDoc }) {
  if (!timeline) {
    return (
      <section className="timeline">
        <div className="timeline__head">
          <h2 className="timeline__title">Таймлайн редакций</h2>
        </div>
        <div className="skeleton" style={{ width: "60%" }} />
        <div className="skeleton" style={{ width: "40%" }} />
      </section>
    );
  }

  const { from, to, lanes, marks } = timeline;
  const eventPct =
    eventDate && Number.isFinite(new Date(`${eventDate}T00:00:00Z`).getTime())
      ? positionPct(eventDate, from, to)
      : null;

  return (
    <section className="timeline" aria-label="Таймлайн редакций документов">
      <div className="timeline__head">
        <h2 className="timeline__title">Таймлайн редакций</h2>
        {eventDate && (
          <span className="msg__meta">
            дата события из вопроса: {formatDateLong(eventDate)}
          </span>
        )}
        <div className="timeline__legend">
          <span className="legend">
            <span className="legend__swatch" data-tone="active" /> действует
          </span>
          <span className="legend">
            <span className="legend__swatch" data-tone="revoked" /> утратил силу
          </span>
          <span className="legend">
            <span className="legend__swatch" data-tone="pending" /> издано, ещё не действует
          </span>
        </div>
      </div>

      <div className="timeline__body">
        <div className="timeline__overlay" aria-hidden={eventPct == null}>
          {eventPct != null && (
            <div className="timeline__event" style={{ left: `${eventPct}%` }}>
              <span>{formatDate(eventDate)}</span>
            </div>
          )}
        </div>

        {lanes.map((lane) => (
          <div className="timeline__lane" key={lane.id}>
            <span className="timeline__laneTitle" title={lane.caption}>
              {lane.title}
            </span>
            {lane.segments.map((segment, index) => {
              const left = positionPct(segment.from, from, to);
              const right = positionPct(segment.to, from, to);
              const width = Math.max(2, right - left);
              return (
                <button
                  key={`${lane.id}-${index}`}
                  type="button"
                  className="timeline__seg"
                  data-tone={segment.tone}
                  style={{ left: `${left}%`, width: `${width}%` }}
                  title={`${segment.title} — открыть документ`}
                  onClick={() => onOpenDoc?.(segment.doc_id)}
                >
                  {segment.label}
                </button>
              );
            })}
          </div>
        ))}
      </div>

      <div className="timeline__marks" aria-hidden="true">
        {marks.map((mark, index) => (
          <span
            key={`${mark.date}-${mark.label}`}
            className="timeline__mark"
            data-kind={mark.kind}
            style={{
              left: `${positionPct(mark.date, from, to)}%`,
              top: index % 2 === 1 ? 11 : 0,
            }}
          >
            {mark.label}
          </span>
        ))}
      </div>
    </section>
  );
}
