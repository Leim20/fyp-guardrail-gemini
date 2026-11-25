// The pipeline, drawn as a railway signalling diagram in a terminal's vocabulary.
//
//   a checkpoint  is a track        (input, action, output)
//   a layer       is a signal       (a lamp on a post beside the track)
//   a check       is a train        (a block of inverse video carrying the run's id)
//
// A train runs along its track. Before each signal it waits for the lamp: green, it passes;
// amber, it passes marked; red, it stops there and goes no further. Which lamp shows what is
// the event's own trace, so the picture is the guard's decision and nothing else.
//
// Everything is SVG: a handful of elements, moved once per frame with a transform. This is
// the one thing a real terminal cannot do, a block that glides instead of jumping a column.

import { harmExecuted, setFilter, state, subscribe } from "./store.js";

const X0 = 214, X1 = 1066;                     // where a track starts and ends
const SIGNAL_X = [372, 544, 716, 888];
const HOLD_X = 980;                            // where a train waits for a person's confirmation
const TRACKS = {
  input: { y: 54, from: "content", to: "agent", gates: ["trust", "rules", "classifier", "judge"] },
  action: { y: 130, from: "agent", to: "tools", gates: ["schema", "policy", "taint", "alignment"] },
  output: { y: 206, from: "agent", to: "user", gates: ["leak", "links", "pii"] },
};
const ORIGIN = { user: "user message", internal_data: "internal data", external_untrusted: "untrusted content" };
// which switch of the guard configuration turns a signal on
const NEEDS = { trust: ["rules", "classifier"], rules: ["rules"], classifier: ["classifier"], judge: ["judge"],
                schema: ["policy"], policy: ["policy"], taint: ["policy", "taint"], alignment: ["policy", "judge"],
                leak: ["output"], links: ["output"], pii: ["output"] };

const SPEED = 520;      // units per second at 1x
const DWELL = 0.16;     // seconds a train waits at a signal that judges it
const NS = "http://www.w3.org/2000/svg";

let svg, trainLayer;
let trains = [];
let frameId = null, lastTime = 0;
const timers = {};

function el(name, attrs = {}, text) {
  const node = document.createElementNS(NS, name);
  for (const [key, value] of Object.entries(attrs)) node.setAttribute(key, value);
  if (text !== undefined) node.textContent = text;
  return node;
}

function build() {
  for (const [name, track] of Object.entries(TRACKS)) {
    const y = track.y;
    svg.append(
      el("text", { class: "t-name", x: 22, y: y + 4 }, name.toUpperCase()),
      el("text", { class: "t-end", x: X0 - 12, y: y + 4, "text-anchor": "end", "data-from": name }, track.from),
      el("path", { class: "rail", d: `M${X0} ${y} H${X1}` }),
      el("path", { class: "rail-tip", d: `M${X1 + 9} ${y} l-10 -4.5 v9 Z` }),
      el("text", { class: "t-end", x: X1 + 18, y: y + 4, "data-to": name }, track.to));
    track.gates.forEach((gate, index) => {
      const x = SIGNAL_X[index];
      const signal = el("g", { class: "signal", "data-gate": gate, tabindex: 0, role: "button",
                               "aria-label": `${gate}: show only the decisions this layer acted on` });
      signal.append(el("rect", { x: x - 44, y: y - 40, width: 88, height: 78, fill: "transparent" }),   // hit area
                    el("path", { class: "post", d: `M${x} ${y} V${y - 18}` }),
                    el("circle", { class: "halo", cx: x, cy: y - 26, r: 15 }),
                    el("circle", { class: "lamp", cx: x, cy: y - 26, r: 7 }),
                    el("text", { class: "s-name", x, y: y + 19 }, gate),
                    el("text", { class: "s-count", x, y: y + 33 }, ""));
      signal.addEventListener("click", () => setFilter(gate));
      signal.addEventListener("keydown", (e) => { if (e.key === "Enter" || e.key === " ") { e.preventDefault(); setFilter(gate); } });
      svg.append(signal);
    });
  }
  trainLayer = el("g");
  svg.append(trainLayer);
}

const signalOf = (gate) => svg.querySelector(`[data-gate="${gate}"]`);

function show(gate, status) {
  const signal = signalOf(gate);
  signal.classList.remove("is-pass", "is-flag", "is-block", "is-clear");
  signal.classList.add(`is-${status}`);
  clearTimeout(timers[gate]);
  const lit = { pass: 650, clear: 1300, flag: 1300, block: 1800 }[status];
  timers[gate] = setTimeout(() => signal.classList.remove(`is-${status}`), lit / state.speed);
}

function refresh() {
  const flags = state.meta?.variants[state.variant] || {};
  for (const gate of Object.keys(NEEDS)) {
    const signal = signalOf(gate);
    const on = gate === "trust" ? NEEDS.trust.some((f) => flags[f]) : NEEDS[gate].every((f) => flags[f]);
    signal.classList.toggle("is-off", !on);
    signal.classList.toggle("is-filter", state.filter === gate);
    signal.querySelector(".s-count").textContent = state.gates[gate] ? `×${state.gates[gate]}` : "";
  }
}

// Where a blocked train stops. A layer that blocks outright stops it there: a train does not
// run past a red signal. If no single layer blocked (the detectors only flagged, and the
// decision was taken on their combined result), it stops at the last one that flagged.
function stopGate(event) {
  if (event.verdict !== "BLOCK") return null;
  const blocked = event.trace.find((s) => s.status === "block");
  const raised = event.trace.filter((s) => ["flag", "fail"].includes(s.status));
  return (blocked || raised.at(-1) || event.trace.filter((s) => s.status !== "off").at(-1))?.layer || null;
}

export function spawn(event) {
  return new Promise((resolve) => {
    const track = TRACKS[event.checkpoint];
    const label = event.run;
    const width = label.length * 7.6 + 16, half = width / 2;
    const stops = track.gates.map((gate, index) => ({ x: SIGNAL_X[index] - half - 14, gate }));   // held just before the signal
    if (event.verdict === "ASK_CONFIRM") stops.push({ x: HOLD_X, wait: true });
    stops.push({ x: X1 - half - 2, end: true });

    const node = el("g", { class: "train" });
    node.append(el("rect", { x: -half, y: -10, width, height: 20 }), el("text", { x: 0, y: 4.2 }, label));
    trainLayer.append(node);
    if (event.checkpoint === "input") {
      const from = svg.querySelector('[data-from="input"]');
      from.textContent = ORIGIN[event.trust] || ORIGIN.external_untrusted;
      from.classList.toggle("is-untrusted", !(event.trust in ORIGIN) || event.trust === "external_untrusted");
    }
    trains.push({ event, node, track, stops, next: 0, x: X0 + half + 2, hold: 0, label,
                  trace: Object.fromEntries(event.trace.map((s) => [s.layer, s])), stop: stopGate(event),
                  lastGate: track.gates.at(-1), resolve, resolved: false });
    if (!frameId) { lastTime = performance.now(); frameId = requestAnimationFrame(frame); }
  });
}

function paint(train, status) {
  train.node.setAttribute("class", `train${status ? " st-" + status : ""}`);
}

function settle(train) {
  if (!train.resolved) { train.resolved = true; train.resolve(); }
}

function atSignal(train, gate) {
  const status = train.trace[gate]?.status || "off";
  if (status !== "off" && status !== "skip") {
    show(gate, status === "fail" ? "flag" : status);
    if (["flag", "fail", "block"].includes(status)) paint(train, "warn");
    if (status === "clear") paint(train, "");
    train.hold = state.reducedMotion ? 0 : DWELL;
  }
  if (gate === train.stop) {                     // red: the check goes no further
    paint(train, "block");
    // every other layer that also objected shows red too (two rules can both refuse one call)
    for (const step of train.event.trace) if (step.layer !== gate && step.status === "block") show(step.layer, "block");
    train.hold = state.reducedMotion ? 0.2 : 1.1;  // stand at the signal long enough to be read
    train.leaving = true;
    settle(train);
    return;
  }
  if (gate === train.lastGate && status !== "off") {
    const verdict = train.event.verdict;
    paint(train, { ALLOW: "allow", SANITIZE: "warn", ASK_CONFIRM: "confirm" }[verdict] || "");
  }
}

function arrive(train) {
  if (harmExecuted(train.event)) {
    paint(train, "block");
    const to = svg.querySelector(`[data-to="${train.event.checkpoint}"]`);
    to.textContent = `${train.track.to} ✗ harm`;
    to.classList.add("is-harm");
    clearTimeout(timers[`to-${train.event.checkpoint}`]);
    timers[`to-${train.event.checkpoint}`] = setTimeout(() => { to.textContent = train.track.to; to.classList.remove("is-harm"); }, 1800 / state.speed);
  }
  train.fade = 1;
  settle(train);
}

function advance(train, dt) {
  if (train.fade !== undefined) {
    train.fade -= dt / 0.28;
    train.node.style.opacity = Math.max(0, train.fade);
    if (train.fade <= 0) { train.node.remove(); train.gone = true; }
    return;
  }
  if (train.hold > 0) {
    train.hold -= dt * state.speed;
    if (train.hold > 0) return;
    if (train.leaving) { train.fade = 1; return; }
    if (train.waiting) {
      train.waiting = false;
      train.node.querySelector("text").textContent = train.label;
      if (train.event.executed === false) { train.fade = 1; settle(train); return; }   // the person said no
    }
  }
  let distance = SPEED * state.speed * dt * (state.reducedMotion ? 8 : 1);
  while (distance > 0 && train.next < train.stops.length) {
    const stop = train.stops[train.next], left = stop.x - train.x;
    if (distance < left) { train.x += distance; break; }
    distance -= Math.max(0, left);
    train.x = Math.max(train.x, stop.x);
    train.next += 1;
    if (stop.gate) atSignal(train, stop.gate);
    if (stop.wait) {
      train.waiting = true;
      train.hold = state.reducedMotion ? 0.1 : 0.9;
      train.node.querySelector("text").textContent = `${train.label} ?`;
    }
    if (stop.end) { arrive(train); break; }
    if (train.hold > 0) break;
  }
  train.node.setAttribute("transform", `translate(${train.x.toFixed(1)} ${train.track.y})`);
}

function frame(now) {
  const dt = Math.min(0.05, (now - lastTime) / 1000);
  lastTime = now;
  trains.forEach((train) => advance(train, dt));
  trains = trains.filter((train) => !train.gone);
  frameId = trains.length ? requestAnimationFrame(frame) : null;
}

// Clear the tracks (the guard configuration changed) without leaving anyone waiting.
export function clear() {
  trains.forEach((train) => { settle(train); train.node.remove(); });
  trains = [];
}

export function init() {
  svg = document.getElementById("tracks");
  build();
  subscribe((what) => { if (["loaded", "variant", "event", "filter"].includes(what)) refresh(); });
}
