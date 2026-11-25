// The status line: totals since this guard configuration was selected, in one row like the
// status bar of a terminal program. A number rolls to its new value, so a change is seen as
// well as read. Latency gets a sparkline made of block characters, as a terminal would draw it.

import { state, subscribe } from "./store.js";

const KEYS = ["checks", "BLOCK", "SANITIZE", "ASK_CONFIRM", "judge", "harm"];
const BLOCKS = "▁▂▃▄▅▆▇█";
const SPARK_POINTS = 18;
const displayed = {};
let bar;

function roll(key, target, decimals = 0) {
  const node = bar.querySelector(`[data-key="${key}"]`);
  const from = displayed[key] ?? 0;
  displayed[key] = target;
  if (state.reducedMotion || from === target) { node.textContent = target.toFixed(decimals); return; }
  const start = performance.now(), duration = 280;
  const tick = (now) => {
    if (displayed[key] !== target) return;                   // a newer value took over
    const k = Math.min(1, (now - start) / duration), eased = 1 - (1 - k) ** 3;
    node.textContent = (from + (target - from) * eased).toFixed(decimals);
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function median(values) {
  if (!values.length) return 0;
  const sorted = [...values].sort((a, b) => a - b), mid = sorted.length >> 1;
  return sorted.length % 2 ? sorted[mid] : (sorted[mid - 1] + sorted[mid]) / 2;
}

// A square-root scale: most checks take under a millisecond and a few take tens; on a linear
// scale the small ones would all be the lowest block.
function sparkline(values) {
  const recent = values.slice(-SPARK_POINTS);
  if (!recent.length) return "";
  const top = Math.sqrt(Math.max(...recent, 1));
  return recent.map((value) => BLOCKS[Math.min(7, Math.floor((Math.sqrt(value) / top) * 7.999))]).join("");
}

function refresh() {
  for (const key of KEYS) roll(key, state.counts[key]);
  roll("latency", median(state.latencies), 1);
  bar.querySelector("#spark").textContent = sparkline(state.latencies);
  bar.querySelector("#harm").classList.toggle("is-alert", state.counts.harm > 0);
}

export function init() {
  bar = document.getElementById("status");
  subscribe((what) => { if (what === "event" || what === "variant") refresh(); });
}
