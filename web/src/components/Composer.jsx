import { useRef, useState } from "react";
import { LIMITS } from "../api/contract.js";

/**
 * Блок ввода вопроса по макету 01_main_screen.html:
 * .input-bar, textarea, .send-btn (оранжевый градиент), .input-hint, демо-подсказки.
 */
export default function Composer({
  onSend,
  onStop,
  busy,
  hints = [],
  sessionCount = 0,
  notice,
}) {
  const [value, setValue] = useState("");
  const inputRef = useRef(null);
  const over = value.length > LIMITS.question_chars;
  const limitReached = sessionCount >= LIMITS.session_questions;

  function submit() {
    const text = value.trim();
    if (!text || busy || limitReached) return;
    onSend(text);
    setValue("");
    inputRef.current?.focus();
  }

  function handleKeyDown(event) {
    if (event.key === "Enter" && !event.shiftKey) {
      event.preventDefault();
      submit();
    }
  }

  function useHint(question) {
    if (!question || busy || limitReached) return;
    onSend(question);
    setValue("");
  }

  return (
    <div className="composer-area" style={{ display: "grid", gap: 8 }}>
      {notice && (
        <div className="notice" role="status" style={{ fontSize: "var(--fs-caption)", color: "var(--status-partial-color)", background: "var(--status-partial-bg)", border: "1px solid var(--status-partial-border)", padding: "6px 12px", borderRadius: "var(--radius-sm)" }}>
          {notice}
        </div>
      )}

      {/* Быстрые подсказки-демо (макет 01/02) */}
      {hints.length > 0 && !busy && (
        <div className="composer__hints" style={{ display: "flex", flexWrap: "wrap", gap: 6 }}>
          {hints.map((hint) => (
            <button
              key={hint.id}
              type="button"
              className="chip"
              onClick={() => useHint(hint.question)}
              disabled={limitReached}
              title={hint.question}
            >
              {hint.label}
            </button>
          ))}
        </div>
      )}

      {/* Поле ввода вопроса: .input-bar */}
      <div className="input-bar">
        <label className="sr-only" htmlFor="question">
          Задайте вопрос по нормативным документам
        </label>
        <textarea
          id="question"
          ref={inputRef}
          placeholder="Задайте вопрос по нормативным документам (например: какая арендная плата по договору А-08?)"
          value={value}
          rows={2}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={limitReached}
        />
        {busy ? (
          <button type="button" className="send-btn" style={{ background: "var(--status-refusal-color)" }} onClick={onStop}>
            Стоп
          </button>
        ) : (
          <button
            type="button"
            className="send-btn"
            onClick={submit}
            disabled={!value.trim() || limitReached}
          >
            Спросить
          </button>
        )}
      </div>

      {/* Подсказка под полем ввода: .input-hint */}
      <div className="input-hint" style={{ display: "flex", justifyContent: "space-between", alignItems: "center" }}>
        <span>
          Лимит {LIMITS.question_chars} симв. · сессия {sessionCount}/{LIMITS.session_questions} вопр. · ответы строго по базе знаний
        </span>
        {value.length > 0 && (
          <span style={{ color: over ? "var(--status-refusal-color)" : "var(--text-muted)", fontFamily: "var(--font-mono)", fontSize: "var(--fs-caption)" }}>
            {value.length}/{LIMITS.question_chars}
          </span>
        )}
      </div>
    </div>
  );
}
