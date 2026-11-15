import { useRef, useState } from "react";
import { LIMITS } from "../api/contract.js";

/**
 * Блок ввода вопроса с защитой от переполнения и адаптивным переносом.
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
    <div className="composer-container">
      {notice && (
        <div className="composer__notice" role="status">
          {notice}
        </div>
      )}

      {/* Быстрые подсказки-демо с плавным скроллом */}
      {hints.length > 0 && !busy && (
        <div className="composer__hints">
          {hints.map((hint) => (
            <button
              key={hint.id}
              type="button"
              className="chip chip--hint"
              onClick={() => useHint(hint.question)}
              disabled={limitReached}
              title={hint.question}
            >
              {hint.label}
            </button>
          ))}
        </div>
      )}

      {/* Поле ввода: .input-bar */}
      <div className="input-bar">
        <label className="sr-only" htmlFor="question">
          Задайте вопрос по нормативным документам
        </label>
        <textarea
          id="question"
          ref={inputRef}
          className="input-bar__textarea"
          placeholder="Задайте вопрос по нормативным документам (например: какая арендная плата по договору А-08?)"
          value={value}
          rows={2}
          onChange={(e) => setValue(e.target.value)}
          onKeyDown={handleKeyDown}
          disabled={limitReached}
        />
        {busy ? (
          <button type="button" className="send-btn send-btn--stop" onClick={onStop}>
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

      {/* Подсказка под полем ввода */}
      <div className="input-hint">
        <span className="input-hint__text">
          Лимит {LIMITS.question_chars} симв. · сессия {sessionCount}/{LIMITS.session_questions} вопр. · ответы строго по базе знаний
        </span>
        {value.length > 0 && (
          <span className={`input-hint__counter ${over ? "input-hint__counter--over" : ""}`}>
            {value.length}/{LIMITS.question_chars}
          </span>
        )}
      </div>
    </div>
  );
}
