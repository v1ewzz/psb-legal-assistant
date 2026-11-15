/**
 * Демо-ответы мок-режима (§18.2, «мок-файл с 3 примерами»).
 *
 * Стиль ответа — §4.2 (вывод первым предложением, затем обоснование;
 * отказ называет тему и не предлагает «уточните вопрос»), тексты цитат —
 * дословно из `index/chunks.jsonl.gz` (мок не выдумывает норм).
 *
 * Структура повторяет контракт `docs/api_contract.md` байт в байт:
 * {answer, status, confidence, sources[], trace{}}. Анкоры (start/end)
 * считает `mock/engine.js` — здесь только doc_id + path + цитата.
 */

export const DOC = {
  rent: "документы_компании__Договор_аренды_А-08-2024_Северный_Склад",
  supply: "документы_компании__Договор_поставки_П-17-2024_Альфа-Трейд",
  supplyOld: "документы_компании__Договор_поставки_ПТ-05-2023_Альфа-Трейдинг",
  reg2023: "документы_компании__Регламент_согласования_договоров_ред_2023_утратил_силу",
  reg2025: "документы_компании__Регламент_согласования_договоров_ред_2025",
  letter: "документы_компании__Письмо_комплаенс-службы_К-12_от_15.04.2025",
  pd: "законы__152ФЗ_ст9_действующая",
  tk70: "законы__ТК_ст70_действующая",
};

/** @type {Array<Object>} */
export const DEMO_ANSWERS = [
  {
    id: "demo-full-rent",
    hint: "Полный ответ: арендная плата",
    keywords: ["аренд", "а-08", "склад"],
    answer:
      "Арендная плата по договору № А-08/2024 составляет 450 000 рублей в месяц, включая НДС (п. 3.1), и вносится ежемесячно не позднее 5-го числа текущего месяца (п. 3.2).",
    status: "full",
    confidence: 0.93,
    sources: [
      {
        doc_id: DOC.rent,
        path: "п. 3.1",
        quote:
          "Арендная плата составляет 450 000 (четыреста пятьдесят тысяч) рублей в месяц, включая НДС.",
        score: 0.94,
      },
      {
        doc_id: DOC.rent,
        path: "п. 3.2",
        quote:
          "Арендная плата вносится ежемесячно, не позднее 5-го числа текущего месяца.",
        score: 0.88,
      },
    ],
    trace: {
      request_type: "direct",
      request_type_label: "Прямой вопрос по документу компании",
      retrieved: [
        { doc_id: DOC.rent, path: "п. 3.1", score: 0.94, channel: "bm25+rerank" },
        { doc_id: DOC.rent, path: "п. 3.2", score: 0.88, channel: "bm25+rerank" },
        { doc_id: DOC.rent, path: "п. 2.1", score: 0.41, channel: "bm25" },
      ],
      subquestions: [
        {
          id: "q1",
          text: "Какая арендная плата по договору № А-08/2024?",
          status: "supported",
          source_ids: ["s1"],
          reason: "Сумма и валюта есть в п. 3.1 дословно",
        },
        {
          id: "q2",
          text: "В какой срок вносится арендная плата?",
          status: "supported",
          source_ids: ["s2"],
          reason: "Срок платежа есть в п. 3.2",
        },
      ],
      confidence: {
        threshold_high: 0.72,
        threshold_low: 0.35,
        reasons: [
          "retrieval 0.30: оба целевых пункта в топ-2 (exact-ref boost)",
          "достаточность 0.25: все подвопросы поддержаны",
          "верификация 0.30: 3 числа ответа найдены в evidence",
          "контекст 0.15: договор назван в вопросе, редакций нет",
        ],
      },
      edition: {
        applied: null,
        event_date: null,
        note: "Договор аренды — не редакционный документ, дата события не указана",
        conflicted: false,
      },
      citations: { checked: 3, ok: 3, failures: [] },
      gates: [
        { name: "T1: инъекции в документах", passed: true, detail: "compromised-строк нет" },
        { name: "verify-lite: числа из evidence", passed: true, detail: "450 000, 3.1, 5" },
        { name: "единица при числе", passed: true, detail: "«в месяц» совпадает с evidence" },
      ],
      degraded: [],
      timings: { retrieval_ms: 210, llm_ms: 1840 },
      model: "mok-режим (без LLM)",
    },
  },

  {
    id: "demo-partial-pd",
    hint: "Частичный: отзыв согласия ПДн",
    keywords: ["отзыв", "согласи", "персональн", "пдн"],
    answer:
      "Согласие на обработку персональных данных может быть отозвано субъектом персональных данных (152-ФЗ, ст. 9, п. 2). Внутреннего документа ООО «Меридиан» о порядке отзыва согласия в базе знаний нет, поэтому порядок подачи заявления по документам компании указать не могу.",
    status: "partial",
    confidence: 0.54,
    sources: [
      {
        doc_id: DOC.pd,
        path: "п. 2",
        quote:
          "Согласие на обработку персональных данных может быть отозвано субъектом персональных данных.",
        score: 0.83,
      },
    ],
    trace: {
      request_type: "direct",
      request_type_label: "Прямой вопрос, два подвопроса",
      retrieved: [
        { doc_id: DOC.pd, path: "п. 2", score: 0.83, channel: "bm25+rerank" },
        { doc_id: DOC.pd, path: "п. 1", score: 0.62, channel: "bm25" },
        { doc_id: DOC.pd, path: "п. 4", score: 0.57, channel: "bm25" },
      ],
      subquestions: [
        {
          id: "q1",
          text: "Можно ли отозвать согласие на обработку ПДн?",
          status: "supported",
          source_ids: ["s1"],
          reason: "152-ФЗ, ст. 9, п. 2 — прямой ответ",
        },
        {
          id: "q2",
          text: "Какой в компании порядок отзыва согласия?",
          status: "unsupported",
          source_ids: [],
          reason: "в базе нет локального акта о порядке отзыва",
        },
      ],
      confidence: {
        threshold_high: 0.72,
        threshold_low: 0.35,
        reasons: [
          "retrieval 0.30: поддержан только закон, внутренний акт не найден",
          "достаточность 0.25: 1 из 2 подвопросов поддержан (правило §14.3 — не голый отказ)",
          "верификация 0.30: ссылка «ст. 9, п. 2» подтверждена",
          "контекст 0.15: вопрос составной, часть уходит за пределы базы",
        ],
      },
      edition: {
        applied: "152-ФЗ, ст. 9 (ред., действующая с 01.03.2021)",
        event_date: null,
        note: "Дата события не указана — применена действующая редакция",
        conflicted: false,
      },
      citations: { checked: 2, ok: 2, failures: [] },
      gates: [
        { name: "T1: инъекции в документах", passed: true, detail: "compromised-строк нет" },
        { name: "verify-lite: числа из evidence", passed: true, detail: "чисел в ответе нет" },
        {
          name: "полнота покрытия подвопросов",
          passed: false,
          detail: "q2 без источника → статус «частичный»",
        },
      ],
      degraded: ["внутренний документ компании отсутствует в базе"],
      timings: { retrieval_ms: 240, llm_ms: 2110 },
      model: "mok-режим (без LLM)",
    },
  },

  {
    id: "demo-refusal-benefit",
    hint: "Отказ: пособие по БиР",
    keywords: ["пособи", "беременност", "бир", "декрет"],
    answer:
      "В базе знаний нет документов о размере пособия по беременности и родам, поэтому ответить по документам я не могу. По памяти отвечать не буду, так как нормы могли измениться.",
    status: "refusal",
    confidence: 0.08,
    sources: [],
    trace: {
      request_type: "direct",
      request_type_label: "Прямой вопрос вне базы знаний",
      retrieved: [
        { doc_id: DOC.tk70, path: "ч. 1, абз. 2", score: 0.19, channel: "bm25" },
        { doc_id: DOC.tk70, path: "абз. 9", score: 0.14, channel: "bm25" },
      ],
      subquestions: [
        {
          id: "q1",
          text: "Какой размер пособия по беременности и родам в 2026 году?",
          status: "unsupported",
          source_ids: [],
          reason: "в базе 13 законов и 8 документов компании, норм о пособиях нет",
        },
      ],
      confidence: {
        threshold_high: 0.72,
        threshold_low: 0.35,
        reasons: [
          "retrieval 0.30: лучший скор 0.19 — ниже порога «есть основания»",
          "достаточность 0.25: ни один подвопрос не поддержан",
          "верификация 0.30: нечего проверять — ответ не генерировался",
          "контекст 0.15: тема «пособие по БиР» отсутствует в реестре документов",
        ],
      },
      edition: {
        applied: null,
        event_date: "2026",
        note: "Дата в вопросе — год; редакции не применялись, документов нет",
        conflicted: false,
      },
      citations: { checked: 0, ok: 0, failures: [] },
      gates: [
        { name: "T1: инъекции в документах", passed: true, detail: "compromised-строк нет" },
        {
          name: "grounded refusal (D4)",
          passed: true,
          detail: "тема названа, «по памяти не отвечаю» добавлено",
        },
        {
          name: "запрет голого отказа при supported",
          passed: true,
          detail: "supported подвопросов нет — отказ допустим",
        },
      ],
      degraded: [],
      timings: { retrieval_ms: 180, llm_ms: 0 },
      model: "mok-режим (без LLM)",
    },
  },

  {
    id: "demo-question-error",
    hint: "Ошибка в вопросе",
    keywords: ["сколько дней", "какой срок?", "а если он"],
    answer:
      "Не удалось определить вопрос: не указано, о каком документе и событии речь. Уточните формулировку — например: «в какой срок передаётся оригинал договора в юридический отдел».",
    status: "question_error",
    confidence: 0.0,
    sources: [],
    trace: {
      request_type: "unclear",
      request_type_label: "Вопрос не распознан (нет предмета запроса)",
      retrieved: [],
      subquestions: [
        {
          id: "q1",
          text: "(не выделен) — «сколько дней?»",
          status: "not_established",
          source_ids: [],
          reason: "нет документа, срока и события — retrieval невозможен",
        },
      ],
      confidence: {
        threshold_high: 0.72,
        threshold_low: 0.35,
        reasons: [
          "guard: последняя реплика не содержит предмета запроса",
          "эвристический standalone-запрос пуст → поиск не запускался",
        ],
      },
      edition: { applied: null, event_date: null, note: null, conflicted: false },
      citations: { checked: 0, ok: 0, failures: [] },
      gates: [
        {
          name: "guard.parse_dialog",
          passed: false,
          detail: "last_user без сущностей: ни реквизита, ни темы",
        },
      ],
      degraded: [],
      timings: { retrieval_ms: 0, llm_ms: 0 },
      model: "mok-режим (без LLM)",
    },
  },

  {
    id: "demo-edition-reg2023",
    hint: "Таймлайн: редакция Регламента на дату",
    keywords: ["редакци", "регламент", "порог"],
    answer:
      "На 15.06.2024 действовал Регламент согласования договоров в редакции от 10.01.2023: договоры на сумму свыше 10 000 000 рублей подлежали одобрению генеральным директором до подписания (п. 2.4). Редакция от 01.03.2025 с порогом 15 000 000 рублей на эту дату не применяется.",
    status: "full",
    confidence: 0.9,
    sources: [
      {
        doc_id: DOC.reg2023,
        path: "п. 2.4",
        quote:
          "Договоры на сумму свыше 10 000 000 (десяти миллионов) рублей подлежат одобрению генеральным директором до подписания.",
        score: 0.91,
      },
      {
        doc_id: DOC.reg2025,
        path: "п. 2.4",
        quote:
          "Договоры на сумму свыше 15 000 000 (пятнадцати миллионов) рублей подлежат одобрению генеральным директором до подписания.",
        score: 0.55,
      },
    ],
    trace: {
      request_type: "edition",
      request_type_label: "Вопрос о редакции на дату события",
      retrieved: [
        { doc_id: DOC.reg2023, path: "п. 2.4", score: 0.91, channel: "exact-ref+bm25" },
        { doc_id: DOC.reg2025, path: "п. 2.4", score: 0.55, channel: "bm25" },
      ],
      subquestions: [
        {
          id: "q1",
          text: "Какая редакция Регламента действовала 15.06.2024?",
          status: "supported",
          source_ids: ["s1"],
          reason: "ред. 10.01.2023 действовала до 01.03.2025 (valid_to)",
        },
        {
          id: "q2",
          text: "Какой порог одобрения был на эту дату?",
          status: "supported",
          source_ids: ["s1"],
          reason: "10 000 000 рублей в п. 2.4 редакции 2023 года",
        },
      ],
      confidence: {
        threshold_high: 0.72,
        threshold_low: 0.35,
        reasons: [
          "temporal 0.30: дата события 15.06.2024 попадает в окно ред. 2023",
          "достаточность 0.25: оба подвопроса поддержаны одной редакцией",
          "верификация 0.30: оба числа (10 000 000, 15 000 000) из evidence",
          "контекст 0.15: обе редакции Регламента в выдаче — конфликт разрешён кодом",
        ],
      },
      edition: {
        applied: "Регламент, ред. от 10.01.2023",
        event_date: "2024-06-15",
        note: "На 15.06.2024 действовала редакция от 10.01.2023; редакция от 01.03.2025 к событию не применяется",
        conflicted: true,
      },
      citations: { checked: 3, ok: 3, failures: [] },
      gates: [
        { name: "T1: инъекции в документах", passed: true, detail: "compromised-строк нет" },
        {
          name: "temporal: выбор редакции по дате (D1)",
          passed: true,
          detail: "valid_to ред. 2023 = 01.03.2025 > 15.06.2024",
        },
        {
          name: "verify-lite: числа из evidence",
          passed: true,
          detail: "10 000 000 и 15 000 000 найдены в соответствующих редакциях",
        },
      ],
      degraded: [],
      timings: { retrieval_ms: 260, llm_ms: 1990 },
      model: "mok-режим (без LLM)",
    },
  },
];

/** Вопросы-подсказки под полем ввода: показывают все 4 статуса. */
export const DEMO_HINTS = DEMO_ANSWERS.map((item) => ({
  id: item.id,
  label: item.hint,
  question: {
    "demo-full-rent": "Какая арендная плата по договору аренды № А-08/2024 и в какой срок она вносится?",
    "demo-partial-pd": "Можно ли отозвать согласие на обработку персональных данных и что для этого нужно в нашей компании?",
    "demo-refusal-benefit": "Какой размер пособия по беременности и родам в 2026 году?",
    "demo-question-error": "Сколько дней?",
    "demo-edition-reg2023": "Какая редакция Регламента согласования договоров действовала 15.06.2024 и какой порог одобрения был?",
  }[item.id],
}));
