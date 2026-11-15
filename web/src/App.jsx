import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import Composer from "./components/Composer.jsx";
import MessageCard from "./components/MessageCard.jsx";
import SourcesPanel from "./components/SourcesPanel.jsx";
import TracePanel from "./components/TracePanel.jsx";
import Timeline from "./components/Timeline.jsx";
import DocumentModal from "./components/DocumentModal.jsx";
import DocsDrawer from "./components/DocsDrawer.jsx";
import { API_MODE, askQuestion, clampQuestion } from "./api/client.js";
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
  const [tab, setTab] = useState("sources");
  const [busy, setBusy] = useState(false);
  const [notice, setNotice] = useState("");
  const [sessionCount, setSessionCount] = useState(0);
  const [docs, setDocs] = useState([]);
  const [today, setToday] = useState("2026-10-10");
  const [modalSource, setModalSource] = useState(null);
  const [docsOpen, setDocsOpen] = useState(false);
  const [menuOpen, setMenuOpen] = useState(false);

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

  const assistantMessages = messages.filter((message) => message.role === "assistant");
  const lastAssistant = assistantMessages.at(-1);
  const selected =
    assistantMessages.find((message) => message.id === selectedId) ?? lastAssistant;
  const activeAnswer = selected?.answer ?? null;

  useEffect(() => {
    const node = scrollRef.current;
    if (node) node.scrollTop = node.scrollHeight;
  }, [messages]);

  const patchAnswer = useCallback((id, updater) => {
    setMessages((prev) =>
      prev.map((message) =>
        message.id === id && message.answer
          ? { ...message, answer: updater(message.answer) }
          : message,
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

      const userMessage = { id: uid(), role: "user", content: text };
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
          prev.map((message) =>
            message.id === assistantId ? { ...message, streaming: false, answer: normalizeAnswer(message.answer, assistantId) } : message,
          ),
        );
      } catch (error) {
        const aborted = error?.name === "AbortError";
        const message = aborted
          ? "Запрос остановлен пользователем."
          : error?.message || "Неизвестная ошибка ядра";
        setMessages((prev) =>
          prev.map((item) => {
            if (item.id !== assistantId) return item;
            const hasText = Boolean(item.answer?.answer);
            if (hasText && aborted) return { ...item, streaming: false };
            return { ...item, streaming: false, error: message };
          }),
        );
      } finally {
        aborter.current = null;
        setBusy(false);
      }
    },
    [messages, patchAnswer, sessionCount],
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
      const found = docs.find((doc) => doc.doc_id === docId);
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

  const retry = (message) => {
    if (message.question) ask(message.question);
  };

  return (
    <div className="app">
      <header className="header">
        <div className="header__brand">
          <span className="header__mark" aria-hidden="true">ПСБ</span>
          <span className="header__name">Юридический ассистент</span>
        </div>
        <span className="header__sub">
          ООО «Меридиан» · {docs.length || 21} документов · база выгружена {today.split("-").reverse().join(".")}
        </span>
        <div className="header__spacer" />
        <div className="header__actions">
          <span className="chip-mode" data-mode={API_MODE}>
            {API_MODE === "mock" ? "мок-режим · без LLM" : "SSE · живое ядро"}
          </span>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setMenuOpen(false);
              setDocsOpen(true);
            }}
          >
            Документы базы
          </button>
          <button
            type="button"
            className="btn"
            onClick={() => {
              setMenuOpen(false);
              reset();
            }}
            disabled={messages.length === 0}
          >
            Новый диалог
          </button>
          <button
            type="button"
            className="header__burger"
            aria-label="Меню"
            aria-expanded={menuOpen}
            onClick={() => setMenuOpen((open) => !open)}
          >
            <span aria-hidden="true" />
            <span aria-hidden="true" />
            <span aria-hidden="true" />
          </button>
        </div>

        {menuOpen && (
          <>
            <div className="header__backdrop" onClick={() => setMenuOpen(false)} />
            <nav className="header__menu" aria-label="Меню интерфейса">
              <span className="chip-mode" data-mode={API_MODE}>
                {API_MODE === "mock" ? "мок-режим · без LLM" : "SSE · живое ядро"}
              </span>
              <button
                type="button"
                className="btn"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  setDocsOpen(true);
                }}
              >
                Документы базы
              </button>
              <button
                type="button"
                className="btn"
                role="menuitem"
                onClick={() => {
                  setMenuOpen(false);
                  reset();
                }}
                disabled={messages.length === 0}
              >
                Новый диалог
              </button>
            </nav>
          </>
        )}
      </header>

      <main className="chat">
        <div className="chat__scroll" ref={scrollRef}>
          {messages.length === 0 && (
            <div className="chat__empty">
              <h2>Спросите по документам — ответ будет со ссылками на пункты</h2>
              <p>
                21 документ в базе: 13 законов и 8 документов компании. Каждый ответ
                показывает статус, уверенность, источники с подсветкой фрагмента и
                разбор «как принято решение».
              </p>
              <p style={{ marginTop: 12 }}>
                Попробуйте подсказку под полем ввода — они покрывают все четыре статуса
                ответа и конфликт редакций Регламента.
              </p>
            </div>
          )}

          {messages.map((message) => (
            <MessageCard
              key={message.id}
              message={message}
              selected={message.id === selected?.id}
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
          hints={API_MODE === "mock" ? DEMO_HINTS : []}
          sessionCount={sessionCount}
          notice={notice}
        />
      </main>

      <aside className="panel">
        <div className="panel__tabs" role="tablist" aria-label="Источники и разбор ответа">
          <button
            type="button"
            role="tab"
            className="tab"
            aria-selected={tab === "sources"}
            data-active={tab === "sources"}
            onClick={() => setTab("sources")}
          >
            Источники {activeAnswer?.sources?.length ? `· ${activeAnswer.sources.length}` : ""}
          </button>
          <button
            type="button"
            role="tab"
            className="tab"
            aria-selected={tab === "trace"}
            data-active={tab === "trace"}
            onClick={() => setTab("trace")}
          >
            Как принято решение
          </button>
        </div>

        <div className="panel__body">
          {tab === "sources" ? (
            <SourcesPanel answer={activeAnswer} onOpenSource={openSource} />
          ) : (
            <TracePanel answer={activeAnswer} />
          )}
        </div>
      </aside>

      <Timeline
        timeline={timeline}
        eventDate={activeAnswer?.trace?.edition?.event_date ?? null}
        onOpenDoc={openDocById}
      />

      {modalSource && (
        <DocumentModal source={modalSource} onClose={() => setModalSource(null)} />
      )}
      {docsOpen && (
        <DocsDrawer
          docs={docs}
          onOpenDoc={async (docId) => {
            setDocsOpen(false);
            await openDocById(docId);
            // прогреваем кеш: модалка откроется мгновенно
            loadDoc(docId).catch(() => {});
          }}
          onClose={() => setDocsOpen(false)}
        />
      )}
    </div>
  );
}
