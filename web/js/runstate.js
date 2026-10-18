// Folds kernel events into a task's cells. Mirrors apply_event in server/jobs.py.
const MAX_STREAM = 30000;

export function newCell(c) {
  return { index: c.index, type: c.type, title: c.title, start_line: c.start_line,
    status: c.type === "code" ? "pending" : null, duration: null, outputs: [] };
}

export function applyEvent(task, ev) {
  const e = ev.event;
  if (e === "notebook") { task.cells = ev.cells.map(newCell); return; }
  const cells = task.cells || [];
  const cell = ev.cell !== null && ev.cell !== undefined ? cells[ev.cell] : null;
  if (e === "cell_start" && cell) { cell.status = "running"; cell.started_at = ev.ts; }
  else if (e === "cell_end" && cell) {
    cell.status = ev.status; cell.duration = ev.duration;
    if (ev.error) {
      cell.outputs.push({ kind: "error", ...ev.error });
      task.error = `${ev.error.ename}: ${ev.error.evalue}`.slice(0, 500);
      cells.slice(cell.index + 1).forEach((c) => { if (c.type === "code") c.status = "skipped"; });
    }
  } else if (e === "stream" && cell) {
    const outs = cell.outputs, last = outs[outs.length - 1];
    if (last && last.kind === "stream" && last.name === ev.name) { if (last.text.length < MAX_STREAM) last.text += ev.text; }
    else outs.push({ kind: "stream", name: ev.name, text: ev.text });
  } else if (e === "display" && cell) {
    cell.outputs.push({ kind: ev.kind, title: ev.title, data: ev.data });
  } else if (e === "progress" && cell) {
    const p = cell.outputs.find((o) => o.kind === "progress" && o.label === ev.label);
    if (p) { p.done = ev.done; p.total = ev.total; } else cell.outputs.push({ kind: "progress", label: ev.label, done: ev.done, total: ev.total });
  } else if (e === "table_written") {
    task.rows_written = (task.rows_written || 0) + ev.rows;
    task.tables_written = (task.tables_written || 0) + 1;
    (task.tables ||= []).push({ table: ev.table, version: ev.version, rows: ev.rows });
    if (cell) cell.outputs.push({ kind: "table_written", table: ev.table, version: ev.version, rows: ev.rows });
  } else if (e === "llm_call") {
    task.llm_calls = (task.llm_calls || 0) + 1;
    if (cell) cell.outputs.push({ kind: "llm", purpose: ev.purpose, model: ev.served_by || ev.model, status: ev.status,
      latency_ms: ev.latency_ms, output_tokens: ev.output_tokens, input_tokens: ev.input_tokens });
  } else if (e === "model_registered" && cell) {
    cell.outputs.push({ kind: "model", name: ev.name, version: ev.version, stage: ev.stage });
  }
}

export function cellProgress(task) {
  const code = (task?.cells || []).filter((c) => c.type === "code");
  if (!code.length) return 0;
  return code.filter((c) => ["ok", "error", "skipped"].includes(c.status)).length / code.length;
}
