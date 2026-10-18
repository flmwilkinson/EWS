// Formatting and domain constants

export const TIERS = ["Performing", "Monitor", "Watch", "Early intervention", "Collections", "Recoveries"];
export const TIER_NUM = Object.fromEntries(TIERS.map((t, i) => [t, i]));
export const USE_CASES = [
  ["limit_and_activation", "Approval, activation and limits", "credit-card"],
  ["pricing", "Pricing", "banknote"],
  ["exposure", "Exposure management", "shield-alert"],
  ["offers_engagement", "Offers and engagement", "megaphone"],
  ["support", "Support and early intervention", "headset"],
  ["collections", "Collections treatment", "hand-coins"],
];

const css = (v) => getComputedStyle(document.documentElement).getPropertyValue(v).trim();
export const tierColor = (t) => css(`--tier-${typeof t === "number" ? t : TIER_NUM[t] ?? 0}`);

export const isNum = (v) => typeof v === "number" && isFinite(v);
export function n(v, d = 0) { return isNum(v) ? v.toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d }) : "–"; }
export function pct(v, d = 1) { return isNum(v) ? (v * 100).toLocaleString("en-GB", { minimumFractionDigits: d, maximumFractionDigits: d }) + "%" : "–"; }
export function pctAuto(v) { if (!isNum(v)) return "–"; const a = Math.abs(v); return pct(v, a < 0.01 ? 2 : a < 0.1 ? 1 : 0); }
export function gbp(v, compact = true) {
  if (!isNum(v)) return "–";
  if (compact && Math.abs(v) >= 1e6) return "£" + (v / 1e6).toLocaleString("en-GB", { maximumFractionDigits: 1 }) + "m";
  if (compact && Math.abs(v) >= 1e4) return "£" + (v / 1e3).toLocaleString("en-GB", { maximumFractionDigits: 0 }) + "k";
  return "£" + v.toLocaleString("en-GB", { maximumFractionDigits: 0 });
}
export function dec(v, d = 3) { return isNum(v) ? v.toFixed(d) : "–"; }
export function compact(v) {
  if (!isNum(v)) return "–";
  if (Math.abs(v) >= 1e6) return (v / 1e6).toFixed(1) + "M";
  if (Math.abs(v) >= 1e3) return (v / 1e3).toFixed(v >= 1e4 ? 0 : 1) + "k";
  return String(Math.round(v));
}
export function bytes(b) {
  if (!isNum(b)) return "–";
  if (b > 1e9) return (b / 1e9).toFixed(1) + " GB";
  if (b > 1e6) return (b / 1e6).toFixed(1) + " MB";
  if (b > 1e3) return (b / 1e3).toFixed(0) + " KB";
  return b + " B";
}
export function dur(s) {
  if (!isNum(s)) return "–";
  if (s < 1) return (s * 1000).toFixed(0) + " ms";
  if (s < 60) return s.toFixed(s < 10 ? 1 : 0) + "s";
  const m = Math.floor(s / 60), r = Math.round(s % 60);
  return `${m}m ${String(r).padStart(2, "0")}s`;
}
export function ago(ts) {
  if (!ts) return "–";
  const t = typeof ts === "number" ? ts * 1000 : Date.parse(ts);
  const s = (Date.now() - t) / 1000;
  if (s < 60) return "just now";
  if (s < 3600) return Math.floor(s / 60) + " min ago";
  if (s < 86400) return Math.floor(s / 3600) + " h ago";
  return Math.floor(s / 86400) + " d ago";
}
export function dt(ts, opts) {
  if (!ts) return "–";
  const d = typeof ts === "number" ? new Date(ts * 1000) : new Date(ts);
  return d.toLocaleString("en-GB", opts || { day: "2-digit", month: "short", hour: "2-digit", minute: "2-digit" });
}
export function simClock(iso) {
  if (!iso) return "–";
  const d = new Date(iso);
  return d.toLocaleString("en-GB", { weekday: "short", day: "2-digit", month: "short", year: "numeric" }) + " · " +
    d.toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" });
}
export function simTime(iso) { return iso ? new Date(iso).toLocaleTimeString("en-GB", { hour: "2-digit", minute: "2-digit" }) : "–"; }

// Feature values in the units people use
const PCT_F = new Set(["util", "util_max_6m", "pay_ratio", "dsr", "gambling_share", "bnpl_share", "od_peak_util", "ext_debt_to_income",
  "cash_adv_to_limit_3m", "pd_6m", "p_arrears_3m", "p_forbearance_6m", "propensity_to_pay", "p_cant_pay", "refix_shock_fwd", "cost_to_pay_ratio_6m"]);
const PP_F = new Set(["util_chg_3m", "pay_ratio_chg_3m", "dsr_chg_3m", "gambling_chg_3m"]);
const SIGNED_PCT = new Set(["income_chg_vs_6m", "ext_debt_chg_6m"]);
export function fval(feature, v) {
  if (v === null || v === undefined || (typeof v === "number" && !isFinite(v))) return "–";
  if (typeof v !== "number") return String(v);
  if (PCT_F.has(feature)) return pct(v, Math.abs(v) < 0.1 ? 1 : 0);
  if (PP_F.has(feature)) return (v >= 0 ? "+" : "") + (v * 100).toFixed(1) + "pp";
  if (SIGNED_PCT.has(feature)) return (v >= 0 ? "+" : "") + (v * 100).toFixed(0) + "%";
  if (feature === "headroom_gbp" || feature === "credit_limit") return gbp(v, false);
  if (Math.abs(v) >= 100) return n(v, 0);
  if (Number.isInteger(v)) return String(v);
  return v.toFixed(2);
}
export const initials = (s) => (s || "?").split(/\s+/).map((w) => w[0]).join("").slice(0, 2).toUpperCase();
export const AVATAR = ["#2a78d6", "#6250d6", "#1baf7a", "#eb6834", "#e87ba4", "#0e7490"];
export const avatarColor = (s) => AVATAR[[...(s || "x")].reduce((a, c) => a + c.charCodeAt(0), 0) % AVATAR.length];
export const srcClass = (s) => (s || "").replace(/\s+/g, "-");
export function esc(s) { return String(s ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c])); }
const mdCache = new Map();
export function md(text) {
  const key = text || "";
  let html = mdCache.get(key);
  if (html === undefined) {
    html = DOMPurify.sanitize(marked.parse(key, { breaks: false }));
    if (mdCache.size > 400) mdCache.delete(mdCache.keys().next().value);
    mdCache.set(key, html);
  }
  return html;
}
export function jsonHtml(obj) {
  const s = JSON.stringify(obj, null, 2) ?? "";
  return esc(s).replace(/(&quot;(?:\\.|[^&]|&(?!quot;))*?&quot;)(\s*:)?|\b(true|false|null)\b|-?\d+(?:\.\d+)?(?:e[+-]?\d+)?/gi, (m, str, colon, b) => {
    if (str) return colon ? `<span class="k">${str}</span>${colon}` : `<span class="s">${str}</span>`;
    if (b) return `<span class="b">${m}</span>`;
    return `<span class="n">${m}</span>`;
  });
}
