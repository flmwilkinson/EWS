// Hash router shared by the shell and pages
const { reactive } = Vue;

export const route = reactive({ name: "workflows", params: [], query: {} });
export const KNOWN = new Set();

export function parse() {
  const h = decodeURIComponent(location.hash.replace(/^#\/?/, ""));
  const [path, qs] = h.split("?");
  const parts = path.split("/").filter(Boolean);
  route.name = KNOWN.has(parts[0]) ? parts[0] : "workflows";
  route.params = parts.slice(1);
  route.query = Object.fromEntries(new URLSearchParams(qs || ""));
}
window.addEventListener("hashchange", parse);
export const go = (p) => { location.hash = "#/" + p.replace(/^#?\/?/, ""); };
