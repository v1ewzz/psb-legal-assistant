import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Composer from "./components/Composer.jsx";
import MessageCard from "./components/MessageCard.jsx";
import SourcesPanel from "./components/SourcesPanel.jsx";
import TracePanel from "./components/TracePanel.jsx";
import DocsPanel from "./components/DocsPanel.jsx";
import Timeline from "./components/Timeline.jsx";
import DocumentModal from "./components/DocumentModal.jsx";
import DocsDrawer from "./components/DocsDrawer.jsx";
import { API_MODE, askQuestion, clampQuestion, getApiMode, setApiMode } from "./api/client.js";
import { loadDoc, loadDocsIndex, loadToday } from "./api/docs.js";
import { LIMITS, normalizeAnswer } from "./api/contract.js";
import { buildTimeline } from "./lib/timeline.js";
import { DEMO_HINTS } from "./mock/examples.js";

/** Пустой ответ-заготовка, пока идёт стриминг статуса и текста. */
function emptyAnswer(id) {
  return {
    id,
    answer: "",
    status: "partial",
    confidence: 0,
    sources: [],
    trace: null,
  };
}

export default function App() {
  const [messages, setMessages] = useState([]);
  const [selectedId, setSelectedId] = useState(null);
  const [tab, setTab] = useState("sources"); // "sources" | "trace" | "docs"
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [sessionCount, setSessionCount] = useState(0);
  const [docs, setDocs] = useState([]);
  const [today, setToday] = useState("2026-10-10");
  const [modalSource, setModalSource] = useState(null);
  const [docsOpen, setDocsOpen] = useState(false);

  const seq = useRef(0);
  const aborter = useRef(null);
  const scrollRef = useRef(null);

  const uid = () => {
    seq.current += 1;
    return `m${seq.current}`;
  };

  useEffect(() => {
    loadDocsIndex()
      .then(setDocs)
      .catch(() => setNotice("Список документов недоступен — интерфейс работает без него"));
    loadToday().then((value) => value && setToday(value));
  }, []);

  const timeline = useMemo(() => (docs.length ? buildTimeline(docs, today) : null), [docs, today]);

  const assistantMessages = messages.filter((m) => m.role === "assistant");
  const lastAssistant = assistantMessages.at(-1);
  const selected = assistantMessages.find((m) => m.id === selectedId) ?? lastAssistant;
  const activeAnswer = selected?.answer ?? null;

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages]);

  const patchAnswer = useCallback((id, updater) => {
    setMessages((prev) =>
      prev.map((msg) =>
        msg.id === id && msg.answer ? { ...msg, answer: updater(msg.answer) } : msg,
      ),
    );
  }, []);

  const ask = useCallback(
    async (rawText) => {
      const { text, truncated } = clampQuestion(rawText);
      if (!text.trim()) return;
      if (sessionCount >= LIMITS.session_questions) {
        setNotice(`Лимит ${LIMITS.session_questions} вопросов на сессию — начните новый диалог`);
        return;
      }

      const now = new Date();
      const timeStr = `${today}, ${String(now.getHours()).padStart(2, "0")}:${String(now.getMinutes()).padStart(2, "0")}`;

      const userMessage = { id: uid(), role: "user", content: text, time: timeStr };
      const assistantId = uid();
      const history = [...messages, userMessage];
      const controller = new AbortController();
      aborter.current = controller;

      setMessages((prev) => [
        ...prev,
        userMessage,
        {
          id: assistantId,
          role: "assistant",
          question: text,
          time: timeStr,
          streaming: true,
          answer: emptyAnswer(assistantId),
        },
      ]);
      setSelectedId(assistantId);
      setBusy(true);
      setNotice(truncated ? "Вопрос обрезан по лимиту 4000 символов (MAX_INPUT_CHARS)" : "");

      try {
        await askQuestion({
          messages: history,
          signal: controller.signal,
          onEvent: ({ event, data }) => {
            if (event === "meta") {
              patchAnswer(assistantId, (answer) => ({
                ...answer,
                status: data?.status ?? answer.status,
                confidence: Number(data?.confidence ?? answer.confidence),
              }));
            } else if (event === "source") {
              patchAnswer(assistantId, (answer) => ({
                ...answer,
                sources: [...answer.sources, data],
              }));
            } else if (event === "delta") {
              patchAnswer(assistantId, (answer) => ({
                ...answer,
                answer: answer.answer + (data?.text ?? ""),
              }));
            } else if (event === "trace") {
              patchAnswer(assistantId, (answer) => ({
                ...answer,
                trace: data,
                confidence: Number(data?.confidence?.value ?? answer.confidence),
              }));
            }
          },
        });

        setSessionCount((count) => count + 1);
        setMessages((prev) =>
          prev.map((msg) =>
            msg.id === assistantId
              ? { ...msg, streaming: false, answer: normalizeAnswer(msg.answer, assistantId) }
              : msg,
          ),
        );
      } catch (error) {
        const aborted = error?.name === "AbortError";
        const errorMsg = aborted ? "Запрос остановлен пользователем." : error?.message || "Ошибка связи с ядром";
        setMessages((prev) =>
          prev.map((item) => {
            if (item.id !== assistantId) return item;
            if (Boolean(item.answer?.answer) && aborted) return { ...item, streaming: false };
            return { ...item, streaming: false, error: errorMsg };
          }),
        );
      } finally {
        aborter.current = null;
        setBusy(false);
      }
    },
    [messages, patchAnswer, sessionCount, today],
  );

  const stop = () => aborter.current?.abort();

  const reset = () => {
    aborter.current?.abort();
    setMessages([]);
    setSelectedId(null);
    setSessionCount(0);
    setNotice("");
    setTab("sources");
  };

  const openSource = (source) => setModalSource(source);

  const openPanel = (nextTab, messageId) => {
    if (messageId) setSelectedId(messageId);
    setTab(nextTab);
  };

  const openDocById = async (docId) => {
    if (!docId) return;
    try {
      const found = docs.find((d) => d.doc_id === docId);
      setModalSource({
        id: "from-timeline",
        doc_id: docId,
        short_name: found?.short_name ?? "Документ",
        path: "",
        crumbs: [],
        quote: "",
        anchor: { chunk_id: "", start: 0, end: 0 },
      });
    } catch {
      setNotice("Не удалось открыть документ");
    }
  };

  const retry = (msg) => {
    if (msg.question) ask(msg.question);
  };

  return (
    <div className="page-container">
      {/* Топбар по макету 01_main_screen.html */}
      <header className="topbar">
        <span className="logo">
          <span className="logo-mark">П</span>
          ПСБ · Юридический ассистент
        </span>
        <span className="tag">
          ООО «Меридиан» · {docs.length || 21} документов · база выгружена {today.split("-").reverse().join(".")}
        </span>
        <div style={{ marginLeft: "auto", display: "flex", alignItems: "center", gap: 8 }}>
          <button
            type="button"
            className="tag"
            onClick={() => setApiMode(getApiMode() === "mock" ? "sse" : "mock")}
            title="Нажмите для переключения режима"
            style={{
              cursor: "pointer",
              color: getApiMode() === "mock" ? "var(--status-partial-color)" : "var(--status-full-color)",
            }}
          >
            {getApiMode() === "mock" ? "● мок-режим (клик: SSE ядро)" : "● SSE · живое ядро (клик: мок)"}
          </button>
          <button type="button" className="btn" onClick={() => setDocsOpen(true)}>
            Документы базы
          </button>
          <button
            type="button"
            className="btn"
            onClick={reset}
            disabled={messages.length === 0}
          >
            Новый диалог
          </button>
        </div>
      </header>

      {/* Основная рабочая область: сетка чат + боковая панель */}
      <div className="layout">
        {/* ============ ЗОНА 1: ЧАТ ============ */}
        <section className="chat">
          <div className="chat__scroll" ref={scrollRef}>
            {messages.length === 0 && (
              <div className="chat__empty" style={{ padding: "var(--sp-6) var(--sp-4)", textAlign: "center" }}>
                <h2 style={{ fontSize: "var(--fs-h2)", color: "var(--text-primary)", marginBottom: 8 }}>
                  Юридический ассистент с подтверждением источников
                </h2>
                <p style={{ color: "var(--text-secondary)", maxWidth: 600, margin: "0 auto", lineHeight: "var(--lh-normal)" }}>
                  21 документ в базе (законы и документы компании). Все ответы подтверждаются ссылками на пункты,
                  проходят детектор инъекций (D2), проверку цитат и разбор решений (D1–D4).
                </p>
                <div style={{ marginTop: 24, fontSize: "var(--fs-caption)", color: "var(--text-muted)" }}>
                  Выберите подсказку ниже или введите свой вопрос:
                </div>
              </div>
            )}

            {messages.map((msg) => (
              <MessageCard
                key={msg.id}
                message={msg}
                selected={msg.id === selected?.id}
                onSelect={setSelectedId}
                onOpenSource={openSource}
                onOpenPanel={openPanel}
                onRetry={retry}
                onAsk={ask}
              />
            ))}
          </div>

          <Composer
            onSend={ask}
            onStop={stop}
            busy={busy}
            hints={DEMO_HINTS}
            sessionCount={sessionCount}
            notice={notice}
          />
        </section>

        {/* ============ ЗОНА 2 & 3: ПРАВАЯ ПАНЕЛЬ ============ */}
        <aside className="side">
          <div className="tabs">
            <div
              className={`tab ${tab === "sources" ? "tab--active" : ""}`}
              onClick={() => setTab("sources")}
              role="button"
              tabIndex={0}
            >
              Источники {activeAnswer?.sources?.length ? `(${activeAnswer.sources.length})` : ""}
            </div>
            <div
              className={`tab ${tab === "trace" ? "tab--active" : ""}`}
              onClick={() => setTab("trace")}
              role="button"
              tabIndex={0}
            >
              Как принято решение
            </div>
            <div
              className={`tab ${tab === "docs" ? "tab--active" : ""}`}
              onClick={() => setTab("docs")}
              role="button"
              tabIndex={0}
            >
              Документы ({docs.length || 21})
            </div>
          </div>

          <div className="panel" style={{ minHeight: 380 }}>
            {tab === "sources" && (
              <>
                <h3 style={{ fontSize: "var(--fs-body-small)", textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)", marginBottom: 12, display: "flex", alignItems: "center", gap: 6 }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--psb-orange-500)" strokeWidth="2" strokeLinecap="round">
                    <path d="M4 19.5A2.5 2.5 0 0 1 6.5 17H20V2H6.5A2.5 2.5 0 0 0 4 4.5v15z" />
                  </svg>
                  Найденные положения
                </h3>
                <SourcesPanel answer={activeAnswer} onOpenSource={openSource} />
              </>
            )}

            {tab === "trace" && (
              <TracePanel answer={activeAnswer} />
            )}

            {tab === "docs" && (
              <>
                <h3 style={{ fontSize: "var(--fs-body-small)", textTransform: "uppercase", letterSpacing: "0.06em", color: "var(--text-muted)", marginBottom: 12, display: "flex", alignItems: "center", gap: 6 }}>
                  <svg width="14" height="14" viewBox="0 0 24 24" fill="none" stroke="var(--psb-orange-500)" strokeWidth="2" strokeLinecap="round">
                    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z" />
                    <path d="M14 2v6h6" />
                  </svg>
                  База знаний компании
                </h3>
                <DocsPanel docs={docs} onOpenDoc={openDocById} />
              </>
            )}
          </div>
        </aside>
      </div>

      {/* ============ ЗОНА 4: ТАЙМЛАЙН РЕДАКЦИЙ (СНИЗУ) ============ */}
      <div style={{ maxWidth: 1180, margin: "0 auto var(--sp-5)", padding: "0 var(--sp-4)" }}>
        <Timeline
          timeline={timeline}
          eventDate={activeAnswer?.trace?.edition?.event_date ?? null}
          activeDocId={activeAnswer?.sources?.[0]?.doc_id}
          onOpenDoc={openDocById}
        />
      </div>

      {/* Модалка просмотра документа с подсветкой */}
      {modalSource && (
        <DocumentModal source={modalSource} onClose={() => setModalSource(null)} />
      )}

      {/* Дровер полного каталога документов */}
      {docsOpen && (
        <DocsDrawer
          docs={docs}
          onOpenDoc={async (docId) => {
            setDocsOpen(false);
            await openDocById(docId);
            loadDoc(docId).catch(() => {});
          }}
          onClose={() => setDocsOpen(false)}
        />
      )}
    </div>
  );
}
