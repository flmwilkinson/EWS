// HTTP and server-sent event helpers

async function request(method, path, body) {
  const res = await fetch(path, {
    method,
    headers: body !== undefined ? { "Content-Type": "application/json" } : {},
    body: body !== undefined ? JSON.stringify(body) : undefined,
  });
  if (!res.ok) {
    let msg = res.statusText;
    try { const j = await res.json(); msg = j.detail || msg; } catch (e) {}
    const err = new Error(typeof msg === "string" ? msg : JSON.stringify(msg));
    err.status = res.status;
    throw err;
  }
  const ct = res.headers.get("content-type") || "";
  return ct.includes("json") ? res.json() : res.text();
}

export const api = {
  get: (p) => request("GET", p),
  post: (p, b = {}) => request("POST", p, b),
  put: (p, b) => request("PUT", p, b),
};

// A shared EventSource per channel, fanned out to any number of handlers
const channels = {};
export function subscribe(channel, handler) {
  if (!channels[channel]) {
    const es = new EventSource(`/api/stream/${channel}`);
    const handlers = new Set();
    es.onmessage = (m) => {
      let ev;
      try { ev = JSON.parse(m.data); } catch (e) { return; }
      handlers.forEach((h) => { try { h(ev); } catch (e) { console.error(e); } });
    };
    es.onerror = () => {};
    channels[channel] = { es, handlers };
  }
  channels[channel].handlers.add(handler);
  return () => channels[channel].handlers.delete(handler);
}

// One-off streams (AI text): GET via EventSource
export function streamGet(url, onEvent) {
  const es = new EventSource(url);
  let closed = false;
  es.onmessage = (m) => {
    const ev = JSON.parse(m.data);
    onEvent(ev);
    if (ev.type === "done" || ev.type === "error") { closed = true; es.close(); }
  };
  es.onerror = () => { if (!closed) { onEvent({ type: "error", message: "Connection lost" }); es.close(); } };
  return () => { closed = true; es.close(); };
}

// POST streams (Ask AI): fetch + ReadableStream, parsing "data: ..." frames
export async function streamPost(url, body, onEvent, signal) {
  const res = await fetch(url, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body), signal });
  if (!res.ok) { onEvent({ type: "error", message: res.statusText }); return; }
  const reader = res.body.getReader();
  const dec = new TextDecoder();
  let buf = "";
  for (;;) {
    const { value, done } = await reader.read();
    if (done) break;
    buf += dec.decode(value, { stream: true });
    let i;
    while ((i = buf.indexOf("\n\n")) >= 0) {
      const frame = buf.slice(0, i);
      buf = buf.slice(i + 2);
      const line = frame.split("\n").find((l) => l.startsWith("data: "));
      if (line) { try { onEvent(JSON.parse(line.slice(6))); } catch (e) {} }
    }
  }
}
