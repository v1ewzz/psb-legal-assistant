/**
 * SSE-клиент (§18.2). Работает поверх fetch + ReadableStream: EventSource не
 * умеет POST с телом (а нам нужно отправлять историю реплик), поэтому парсим
 * поток руками.
 *
 * Формат кадра стандартный:
 *   event: delta
 *   data: {"text":"…"}
 *   <пустая строка>
 *
 * События контракта: meta | source | delta | trace | done | error.
 * Неизвестные события игнорируются (бэкенд может добавить поле раньше фронта),
 * битый JSON в кадре — тоже: обрыв одного кадра не должен ломать ответ.
 */

export function parseSseFrame(raw) {
  let event = "message";
  const data = [];
  for (const line of raw.split("\n")) {
    if (line.startsWith("event:")) event = line.slice(6).trim();
    else if (line.startsWith("data:")) data.push(line.slice(5).replace(/^ /, ""));
    // строки-комментарии (": keep-alive") и поля id/retry не нужны
  }
  const payload = data.join("\n");
  if (!payload) return null;
  try {
    return { event, data: JSON.parse(payload) };
  } catch {
    return { event: "parse_error", data: { raw: payload } };
  }
}

/**
 * Читает поток и отдаёт кадры в onEvent. Возвращает промис, который
 * завершается по концу потока или по signal.abort().
 */
export async function readSseStream(response, onEvent, signal) {
  if (!response.body) throw new Error("Поток SSE недоступен: нет body");

  const reader = response.body.getReader();
  const decoder = new TextDecoder("utf-8");
  let buffer = "";

  try {
    for (;;) {
      if (signal?.aborted) break;
      const { value, done } = await reader.read();
      if (done) break;
      buffer += decoder.decode(value, { stream: true });

      let boundary = buffer.indexOf("\n\n");
      while (boundary !== -1) {
        const raw = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        // нормализуем CRLF-поток (некоторые прокси отдают \r\n\r\n)
        const frame = parseSseFrame(raw.replace(/\r\n/g, "\n"));
        if (frame) onEvent(frame);
        boundary = buffer.indexOf("\n\n");
      }
    }
    const tail = parseSseFrame(buffer.replace(/\r\n/g, "\n"));
    if (tail) onEvent(tail);
  } finally {
    try {
      await reader.cancel();
    } catch {
      /* поток уже закрыт */
    }
  }
}
