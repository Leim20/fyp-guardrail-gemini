// The console's state in one place. Modules read `state`, change it through the functions
// below, and are told what changed, so each one redraws only its own part.

export const VARIANT_LABELS = {
  none: "No guard",
  detectors: "Text detectors only",
  policy: "Policy and taint only",
  no_judge: "Full, without the judge",
  full: "Full guard",
};

const ACTED = new Set(["block", "flag", "clear", "fail"]); // a layer did something other than pass

export const state = {
  meta: null,
  runs: new Map(),      // "variant/run" -> {run, kind, label, about, outcome, set}
  events: new Map(),    // "variant/run" -> [event, ...] in the order they happened
  variant: "full",
  speed: 1,
  ambient: true,
  reducedMotion: window.matchMedia("(prefers-reduced-motion: reduce)").matches,
  feed: [],             // decided events, newest first
  selected: null,       // an event
  follow: true,         // the detail panel follows the newest notable decision
  filter: null,         // a layer name: show only decisions that layer acted on
  playing: null,        // run id being played on request
  now: null,            // the run whose check is on the tracks right now
  epoch: 0,             // raised whenever the tracks are cleared; a check from before is dropped
  counts: emptyCounts(),
  latencies: [],
  gates: {},            // layer -> how many times it acted
};

function emptyCounts() {
  return { checks: 0, ALLOW: 0, SANITIZE: 0, ASK_CONFIRM: 0, BLOCK: 0, judge: 0, harm: 0 };
}

const listeners = new Set();
export function subscribe(listener) { listeners.add(listener); }
function tell(what) { listeners.forEach((listener) => listener(what)); }

export function load(data) {
  state.meta = data.meta;
  for (const run of data.runs) state.runs.set(`${run.variant}/${run.run}`, run);
  for (const event of data.events) {
    const key = `${event.variant}/${event.run}`;
    if (!state.events.has(key)) state.events.set(key, []);
    state.events.get(key).push(event);
  }
  tell("loaded");
}

// "attack · refund fraud · instruction override", or just "benign" when there is no more to say
export const describe = (run) => (run ? (run.label && run.label !== run.kind ? `${run.kind} · ${run.label}` : run.kind) : "");
export const runOf = (event) => state.runs.get(`${event.variant}/${event.run}`);
export const eventsOf = (event) => state.events.get(`${event.variant}/${event.run}`) || [];
export const acted = (event, layer) => event.trace.some((step) => step.layer === layer && ACTED.has(step.status));

// An attack's decisive step went through: allowed, or confirmed by the attacker.
export function harmExecuted(event) {
  return event.harm && event.verdict !== "BLOCK" && runOf(event)?.outcome === "executed";
}

// The guard has decided this check: it enters the feed and the totals.
export function commit(event) {
  event.at = new Date();
  state.feed.unshift(event);
  if (state.feed.length > 200) state.feed.pop();
  const counts = state.counts;
  counts.checks += 1;
  counts[event.verdict] += 1;
  if (harmExecuted(event)) counts.harm += 1;
  for (const step of event.trace) {
    if ((step.layer === "judge" || step.layer === "alignment") && !["skip", "off"].includes(step.status)) counts.judge += 1;
    if (ACTED.has(step.status)) state.gates[step.layer] = (state.gates[step.layer] || 0) + 1;
  }
  state.latencies.push(event.latency_ms);
  if (state.latencies.length > 400) state.latencies.shift();
  const notable = event.verdict !== "ALLOW" || harmExecuted(event);
  if (state.follow && (notable || !state.selected)) state.selected = event;
  tell("event");
}

export function select(event, byUser = true) {
  state.selected = event;
  if (byUser) state.follow = false;
  tell("select");
}

export function setFollow(on) {
  state.follow = on;
  if (on && state.feed.length) state.selected = state.feed.find((e) => e.verdict !== "ALLOW") || state.feed[0];
  tell("select");
}

export function setFilter(layer) {
  state.filter = state.filter === layer ? null : layer;
  tell("filter");
}

export function setVariant(variant) {
  state.variant = variant;
  state.epoch += 1;
  state.now = null;
  state.feed = [];
  state.selected = null;
  state.follow = true;
  state.filter = null;
  state.counts = emptyCounts();
  state.latencies = [];
  state.gates = {};
  tell("variant");
}

export function set(patch, what = "settings") {
  Object.assign(state, patch);
  tell(what);
}
