// ECharts theme (validated palette), chart component and option builders
import { store } from "./store.js";
import { TIERS, tierColor, pct, n, gbp } from "./format.js";

export const SERIES = {
  light: ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300", "#6250d6", "#e34948"],
  dark: ["#3987e5", "#d95926", "#199e70", "#c98500", "#d55181", "#008300", "#9085e9", "#e66767"],
};
const STATUS = { good: "#0ca30c", warning: "#fab219", serious: "#ec835a", critical: "#d03b3b" };
export const status = STATUS;

function theme(mode) {
  const dark = mode === "dark";
  const ink = dark ? "#c2cad6" : "#3f4a5c", muted = dark ? "#8a94a6" : "#7a8496";
  const grid = dark ? "#232c3a" : "#eceef2", base = dark ? "#38445a" : "#cfd4dc";
  const axis = { axisLine: { show: true, lineStyle: { color: base } }, axisTick: { show: false },
    axisLabel: { color: muted, fontSize: 11 }, splitLine: { show: false, lineStyle: { color: grid } }, nameTextStyle: { color: muted, fontSize: 11 } };
  return {
    color: SERIES[mode], backgroundColor: "transparent",
    textStyle: { fontFamily: "Inter, system-ui, sans-serif", color: ink },
    title: { textStyle: { color: ink, fontSize: 13, fontWeight: 600 } },
    legend: { textStyle: { color: ink, fontSize: 11.5 }, icon: "roundRect", itemWidth: 10, itemHeight: 10, itemGap: 14 },
    tooltip: { backgroundColor: dark ? "#161d2a" : "#ffffff", borderColor: dark ? "#2a3446" : "#e3e6eb", borderWidth: 1,
      textStyle: { color: dark ? "#e8ecf2" : "#0f1726", fontSize: 12 }, extraCssText: "border-radius:8px;box-shadow:0 6px 20px rgba(0,0,0,.12);",
      axisPointer: { lineStyle: { color: base }, crossStyle: { color: base }, shadowStyle: { color: dark ? "rgba(255,255,255,.04)" : "rgba(15,23,38,.04)" } } },
    categoryAxis: axis,
    valueAxis: { ...axis, axisLine: { show: false }, splitLine: { show: true, lineStyle: { color: grid } } },
    line: { symbol: "circle", symbolSize: 6, lineStyle: { width: 2 }, emphasis: { lineStyle: { width: 2.5 } } },
    bar: { itemStyle: { borderRadius: [4, 4, 0, 0] }, barMaxWidth: 34 },
  };
}
echarts.registerTheme("ews-light", theme("light"));
echarts.registerTheme("ews-dark", theme("dark"));

export const fmt = {
  pct: (v) => pct(v, Math.abs(v) < 0.1 && v !== 0 ? 1 : 0),
  pct1: (v) => pct(v, 1),
  int: (v) => n(v, 0),
  dec2: (v) => (typeof v === "number" ? v.toFixed(2) : v),
  dec3: (v) => (typeof v === "number" ? v.toFixed(3) : v),
  gbp: (v) => gbp(v),
  none: (v) => v,
};

export const EChart = {
  props: { option: { type: Object, required: true }, height: { type: [Number, String], default: 260 } },
  emits: ["click"],
  template: `<div ref="el" :style="{ height: typeof height === 'number' ? height + 'px' : height, width: '100%' }"></div>`,
  mounted() {
    this.init();
    this.ro = new ResizeObserver(() => this.chart && this.chart.resize());
    this.ro.observe(this.$refs.el);
    this.stopTheme = Vue.watch(() => store.theme, () => { this.chart.dispose(); this.init(); });
  },
  beforeUnmount() { this.ro?.disconnect(); this.stopTheme?.(); this.chart?.dispose(); },
  watch: { option(o) { this.chart && this.chart.setOption(o, true); } },
  methods: {
    init() {
      this.chart = echarts.init(this.$refs.el, store.theme === "dark" ? "ews-dark" : "ews-light", { renderer: "canvas" });
      this.chart.setOption(this.option, true);
      this.chart.on("click", (p) => this.$emit("click", p));
    },
  },
};

const gridDefault = (o = {}) => ({ left: 8, right: 16, top: 36, bottom: 6, containLabel: true, ...o });

/** Lines (one axis only). series: [{name, data, color?, area?, dashed?, step?}] */
export function lineChart({ x, series, y = "none", legend = true, markLines = [], yMin, yMax, grid, smooth = false, xName, showSymbol = true }) {
  const f = fmt[y] || fmt.none;
  return {
    grid: gridDefault(grid), legend: legend && series.length > 1 ? { top: 0, left: 0 } : { show: false },
    tooltip: { trigger: "axis", valueFormatter: (v) => (v === null || v === undefined ? "–" : f(v)) },
    xAxis: { type: "category", data: x, boundaryGap: false, name: xName },
    yAxis: { type: "value", min: yMin, max: yMax, axisLabel: { formatter: f } },
    series: series.map((s, i) => ({
      name: s.name, type: "line", data: s.data, smooth, step: s.step, showSymbol: s.showSymbol ?? showSymbol,
      lineStyle: { width: s.width || 2, type: s.dashed ? "dashed" : "solid", color: s.color },
      itemStyle: s.color ? { color: s.color } : undefined,
      areaStyle: s.area ? { opacity: 0.12 } : undefined,
      markLine: i === 0 && markLines.length ? { symbol: "none", silent: true, label: { color: "inherit", fontSize: 10.5, formatter: (p) => p.name },
        data: markLines.map((m) => ({ yAxis: m.value, name: m.label, lineStyle: { color: m.color, type: "solid", width: 1, opacity: 0.8 } })) } : undefined,
    })),
  };
}

/** Bars. stacked bars get a 2px surface gap via borderWidth. */
export function barChart({ x, series, y = "none", stacked = false, horizontal = false, legend = true, colors, grid, labels = false, barWidth, markLines = [] }) {
  const f = fmt[y] || fmt.none;
  const surface = store.theme === "dark" ? "#121823" : "#ffffff";
  const few = !horizontal && x.length <= 8;
  const cat = { type: "category", data: x, axisLabel: horizontal ? { interval: 0, width: 170, overflow: "truncate" } : few ? { interval: 0, width: 84, overflow: "break", lineHeight: 13 } : { hideOverlap: true },
    inverse: horizontal };
  const val = { type: "value", axisLabel: { formatter: f } };
  return {
    grid: gridDefault({ top: legend && series.length > 1 ? 36 : 12, ...grid }),
    legend: legend && series.length > 1 ? { top: 0, left: 0 } : { show: false },
    tooltip: { trigger: "axis", axisPointer: { type: "shadow" }, valueFormatter: (v) => f(v) },
    xAxis: horizontal ? val : cat, yAxis: horizontal ? cat : val,
    series: series.map((s, i) => ({
      name: s.name, type: "bar", data: s.data, stack: stacked ? "s" : undefined, barWidth,
      itemStyle: { color: s.color || (colors && colors[i]), borderRadius: stacked ? 0 : horizontal ? [0, 4, 4, 0] : [4, 4, 0, 0],
        borderColor: stacked ? surface : undefined, borderWidth: stacked ? 1 : 0 },
      label: labels ? { show: true, position: horizontal ? "right" : "top", fontSize: 11, color: "inherit", formatter: (p) => f(p.value) } : undefined,
      emphasis: { focus: "series" },
      markLine: i === 0 && markLines.length ? { symbol: "none", silent: true, label: { fontSize: 10.5, formatter: (p) => p.name },
        data: markLines.map((m) => ({ [horizontal ? "xAxis" : "yAxis"]: m.value, name: m.label, lineStyle: { color: m.color, type: "solid", width: 1 } })) } : undefined,
    })),
  };
}

export function tierStack({ x, rows, tiers = ["Monitor", "Watch", "Early intervention", "Collections"], valueKey = "customers", y = "int" }) {
  // rows: [{month, tier_name, customers}]
  const series = tiers.map((t) => ({ name: t, color: tierColor(t), data: x.map((m) => rows.find((r) => r.month === m && r.tier_name === t)?.[valueKey] || 0) }));
  return barChart({ x, series, y, stacked: true });
}

export const tierColors = () => TIERS.map((t) => tierColor(t));
