/* Веб-чат ассистента: чат + источники + trace + документы + таймлайн.
   Безопасность: все данные через textContent/createElement — innerHTML
   для данных не используется (§18.2). */
"use strict";

const $ = (id) => document.getElementById(id);
const session = "web-" + Math.random().toString(36).slice(2, 10);

let lastSources = [];

/* ---------- утилиты без innerHTML ---------- */

function el(tag, cls, text) {
  const node = document.createElement(tag);
  if (cls) node.className = cls;
  if (text !== undefined && text !== null) node.textContent = text;
  return node;
}

function fmtDate(iso) {
  if (!iso || iso === "None") return "";
  const [y, m, d] = String(iso).split("-");
  return d && m ? `${d}.${m}.${y}` : String(iso);
}

/* ---------- чат ---------- */

function addMessage(cls, text, badge, why) {
  const msg = el("div", "msg " + cls);
  if (badge) msg.appendChild(el("span", "badge " + badge, badgeText(badge)));
  msg.appendChild(el("span", "", text));
  if (why) msg.appendChild(el("span", "why", "Почему так: " + why));
  $("messages").appendChild(msg);
  $("messages").scrollTop = $("messages").scrollHeight;
  return msg;
}

function badgeText(status) {
  return ({ full: "полный ответ", partial: "частичный ответ",
            refusal: "отказ", premise_error: "ошибка в вопросе",
            clarify: "ошибка в вопросе" })[status] || status;
}

function setBusy(on, stage) {
  $("busy").hidden = !on;
  if (stage) $("busyStage").textContent = stage;
}

function ask(message) {
  addMessage("user", message);
  setBusy(true, "Поиск по базе…");
  $("askInput").value = "";

  const es = new EventSource(
    `/api/chat/stream?session=${encodeURIComponent(session)}&msg=${encodeURIComponent(message)}`);
  es.onmessage = (ev) => {
    const data = JSON.parse(ev.data);
    if (data.event === "stage") { setBusy(true, data.stage + "…"); return; }
    if (data.event === "final") {
      es.close();
      setBusy(false);
      renderFinal(data);
    }
  };
  es.onerror = () => {
    es.close();
    setBusy(false);
    addMessage("error", "Ошибка соединения с сервером. Попробуйте ещё раз.");
  };
}

function renderFinal(rich) {
  const badge = ({ "полный ответ": "full", "частичный ответ": "partial",
                   "отказ": "refusal", "ошибка в вопросе": "premise_error" })[rich.status] || "partial";
  addMessage("assistant", rich.answer, badge, rich.trace && rich.trace.reason);
  lastSources = rich.sources || [];
  renderSources();
  renderTrace(rich.trace || {});
  if (lastSources.length) activateTab("sources");
}

/* ---------- источники ---------- */

function renderSources() {
  const box = $("tab-sources");
  box.textContent = "";
  if (!lastSources.length) {
    box.appendChild(el("p", "hint", "Для этого ответа источников нет (отказ или точная ссылка не потребовалась)."));
    return;
  }
  lastSources.forEach((s, i) => {
    const card = el("div", "src");
    card.appendChild(el("span", "doc", s.doc || "(документ)"));
    if (s.path) card.appendChild(el("span", "path", s.path));
    card.appendChild(el("div", "text", s.text || ""));
    card.addEventListener("click", () => openDoc(s.doc_id, s.text, i));
    box.appendChild(card);
  });
}

/* ---------- trace «как принято решение» ---------- */

function kv(title, value) {
  const dt = el("dt", "", title);
  const dd = el("dd", "", value === undefined || value === null || value === "" ? "—" : value);
  const wrap = el("div");
  wrap.appendChild(dt); wrap.appendChild(dd);
  return wrap;
}

function renderTrace(t) {
  const box = $("tab-trace");
  box.textContent = "";
  const list = el("dl", "kv");

  const d = t.dialog || {};
  list.appendChild(kv("Тип запроса",
    (d.is_dialog && (d.replicas || 1) > 1)
      ? `диалог (${d.replicas} реплик)` : "одиночный вопрос"));
  if (d.injection) {
    const p = el("span", "pill", "⚠ встроенные инструкции в вопросе — обработаны как данные");
    const wrap = el("div"); wrap.appendChild(p);
    list.appendChild(kv("Безопасность", ""));
    list.lastChild.appendChild(wrap);
  }
  if (t.queries && t.queries.length) {
    list.appendChild(kv("Поисковые запросы (эвристика)", t.queries.join(" | ")));
  }
  list.appendChild(kv("Реранк", t.use_rerank ? "выполнен (API)" : "не использовался"));
  const flt = t.temporal_filters || {};
  const fltStr = Object.keys(flt).length
    ? Object.entries(flt).map(([k, v]) => `${k}: ${v}`).join(", ") : "нет";
  list.appendChild(kv("Temporal-фильтры редакций", fltStr));
  if (t.temporal_notes && t.temporal_notes.length) {
    list.appendChild(kv("Заметки о редакциях", t.temporal_notes.join("\n")));
  }
  list.appendChild(kv("Проверка чисел и ссылок (verify)",
    t.verify === "ок" ? "пройдена" : (t.verify || "—")));
  list.appendChild(kv("Итог", t.reason || "—"));

  if (lastSources.length) {
    const head = el("div");
    head.appendChild(el("dt", "", "Найденные положения (топ выдачи)"));
    lastSources.forEach((s) => {
      const p = el("span", "pill", `${s.doc || ""}${s.path ? ", " + s.path : ""}`);
      head.appendChild(p);
    });
    list.appendChild(head);
  }
  box.appendChild(list);
}

/* ---------- таймлайн редакций ---------- */

function loadTimeline() {
  fetch("/api/docs")
    .then((r) => r.json())
    .then(({ timeline }) => {
      const box = $("timeline");
      box.textContent = "";
      const events = (timeline || []).filter((e) => e.date).slice(0, 12);
      events.forEach((e, i) => {
        if (i) box.appendChild(el("span", "tl-arrow", "→"));
        const item = el("button", "tl-item",
          `${fmtDate(e.date)} · ${e.label}`);
        item.type = "button";
        item.title = e.label;
        item.addEventListener("click", () => { activateTab("docs"); openDocById(e.docLabel); });
        item.dataset.docLabel = e.label;
        box.appendChild(item);
      });
      if (!events.length) box.appendChild(el("span", "tl-item", "редакции не обнаружены"));
      window.__tlEvents = events;
    })
    .catch(() => {});
}

function openDocByLabel(label) {
  const ev = (window.__tlEvents || []).find((e) => e.label === label);
  if (ev) activateTab("docs");
  // ищем документ по short_name в списке
  fetch("/api/docs").then((r) => r.json()).then(({ docs }) => {
    const d = (docs || []).find((x) => x.short_name === label);
    if (d) openDoc(d.doc_id, null, null);
  });
}

/* ---------- документы ---------- */

function loadDocs(filter) {
  fetch("/api/docs")
    .then((r) => r.json())
    .then(({ docs }) => {
      const box = $("docList");
      box.textContent = "";
      const f = (filter || "").toLowerCase();
      (docs || [])
        .filter((d) => !f || String(d.short_name).toLowerCase().includes(f))
        .forEach((d) => {
          const row = el("div", "doc-row");
          row.appendChild(el("div", "", d.short_name || d.doc_id));
          const meta = [d.number, fmtDate(d.doc_date),
            d.valid_from ? "действует с " + fmtDate(d.valid_from) : "",
            d.edition ? "ред. " + d.edition : ""].filter(Boolean).join(" · ");
          row.appendChild(el("div", "meta", meta || d.doc_type || ""));
          row.addEventListener("click", () => openDoc(d.doc_id, null, null));
          box.appendChild(row);
        });
      if (!box.children.length) box.appendChild(el("p", "hint", "Ничего не найдено."));
    });
}

function openDocById(docId) { openDoc(docId, null, null); }

function openDoc(docId, needle, srcIndex) {
  if (!docId) return;
  fetch(`/api/doc/${encodeURIComponent(docId)}`)
    .then((r) => r.json())
    .then(({ meta, text }) => {
      const view = $("docView");
      view.textContent = "";
      view.hidden = false;
      $("docList").hidden = true;
      $("docSearch").hidden = true;

      const back = el("button", "back", "← Ко всем документам");
      back.type = "button";
      back.addEventListener("click", () => {
        view.hidden = true;
        $("docList").hidden = false;
        $("docSearch").hidden = false;
      });
      view.appendChild(back);
      view.appendChild(el("h3", "", meta.short_name || meta.doc_id));
      const metaBits = [meta.number, fmtDate(meta.doc_date),
        meta.valid_from ? "действует с " + fmtDate(meta.valid_from) : "",
        meta.valid_to ? "действует до " + fmtDate(meta.valid_to) : "",
        meta.edition ? "редакция от " + meta.edition : ""].filter(Boolean).join(" · ");
      if (metaBits) view.appendChild(el("div", "meta", metaBits));

      const pre = el("pre");
      appendHighlighted(pre, text, needle);
      view.appendChild(pre);
      if (needle) {
        const mark = pre.querySelector("mark");
        if (mark) mark.scrollIntoView({ block: "center" });
      }
    })
    .catch(() => {});
}

/* Подсветка needle внутри текстового узла (без innerHTML):
   нормализуем пробелы, ищем совпадение, оборачиваем range в <mark>. */
function appendHighlighted(container, text, needle) {
  const norm = (s) => s.replace(/\s+/g, " ");
  if (!needle) { container.textContent = text; return; }
  const target = norm(needle).slice(0, 120);
  const map = [];                       // collapsed index → original index
  let collapsed = "";
  for (let i = 0; i < text.length; i++) {
    const ch = text[i];
    if (/\s/.test(ch)) {
      if (collapsed.endsWith(" ")) continue;
      collapsed += " ";
    } else collapsed += ch;
    map.push(i);
  }
  const idx = collapsed.indexOf(target);
  if (idx < 0) { container.textContent = text; return; }
  const start = map[idx];
  const end = (idx + target.length - 1 < map.length)
    ? map[idx + target.length - 1] + 1 : text.length;
  container.appendChild(document.createTextNode(text.slice(0, start)));
  const mark = document.createElement("mark");
  mark.textContent = text.slice(start, end);
  container.appendChild(mark);
  container.appendChild(document.createTextNode(text.slice(end)));
}

/* ---------- табы и инициализация ---------- */

function activateTab(name) {
  document.querySelectorAll(".tabs button").forEach((b) =>
    b.classList.toggle("active", b.dataset.tab === name));
  ["sources", "trace", "docs"].forEach((n) => {
    $("tab-" + n).hidden = n !== name;
  });
}

document.querySelectorAll(".tabs button").forEach((b) =>
  b.addEventListener("click", () => activateTab(b.dataset.tab)));

$("askForm").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const message = $("askInput").value.trim();
  if (message) ask(message);
});

$("resetBtn").addEventListener("click", () => {
  fetch(`/api/session/${encodeURIComponent(session)}/reset`, { method: "POST" });
  $("messages").textContent = "";
  addMessage("assistant",
    "Новый диалог. Задайте вопрос по документам — отвечу со ссылками на пункты.",
    "full");
  lastSources = [];
  renderSources();
  $("tab-trace").textContent = "";
  $("tab-trace").appendChild(el("p", "hint", "Здесь будет видно, как ассистент пришёл к ответу."));
});

$("docSearch").addEventListener("input", (ev) => loadDocs(ev.target.value));

loadTimeline();
loadDocs("");
addMessage("assistant",
  "Здравствуйте! Я отвечаю по закрытой базе нормативных документов: законы и внутренние документы компании. Если данных в базе нет — так и скажу. Если вопрос не по профилю — откажусь.",
  "full");
