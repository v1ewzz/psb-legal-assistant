import { useRef, useState } from "react";
import { LIMITS } from "../api/contract.js";

/**
 * Поле ввода (§18.2): лимит длины вопроса, счётчик запросов на сессию,
 * подсказки-демо и кнопка остановки стриминга.
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
    <div className="composer">
      {notice && <p className="notice" role="status">{notice}</p>}

      {hints.length > 0 && !busy && (
        <div className="composer__hints">
          {hints.map((hint) => (
            <button
              key={hint.id}
              type="button"
              className="hint"
              onClick={() => useHint(hint.question)}
              disabled={limitReached}
              title={hint.question}
            >
              {hint.label}
            </button>
          ))}
        </div>
      )}

      <div className="composer__row">
        <label className="sr-only" htmlFor="question">
          Вопрос по документам базы
        </label>
        <textarea
          id="question"
          ref={inputRef}
          className="composer__input"
          placeholder="Спросите про договор, регламент или норму — ответ будет со ссылками на пункты"
          value={value}
          rows={2}
          onChange={(event) => setValue(event.target.value)}
          onKeyDown={handleKeyDown}
          aria-describedby="composer-counter"
          disabled={limitReached}
        />
        {busy ? (
          <button type="button" className="btn" onClick={onStop}>
            Стоп
          </button>
        ) : (
          <button
            type="button"
            className="btn btn--primary"
            onClick={submit}
            disabled={!value.trim() || limitReached}
            title={over ? "Вопрос будет обрезан до 4000 символов (лимит ядра)" : undefined}
          >
            Спросить
          </button>
        )}
      </div>

      <div className="composer__foot" id="composer-counter">
        <span className="composer__count" data-over={over}>
          {value.length} / {LIMITS.question_chars}
        </span>
        <span>
          вопрос {Math.min(sessionCount + 1, LIMITS.session_questions)} из{" "}
          {LIMITS.session_questions} в сессии
        </span>
        <span>Enter — отправить, Shift+Enter — перенос строки</span>
        {over && <span>вопрос будет обрезан по лимиту ядра</span>}
      </div>
    </div>
  );
}
