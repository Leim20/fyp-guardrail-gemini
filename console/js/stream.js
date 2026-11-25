// Where events come from. The rest of the console only knows `start(onEvent)` and `play(run)`.
//
// Prototype: events were recorded from the real guard (scripts/export_console_demo.py) and
// are played back here, one run after another, as ambient traffic.
// Later: the guard pushes its decisions over Server-Sent Events, and this file becomes
//   new EventSource("/events").onmessage = (m) => onEvent(JSON.parse(m.data))
// with nothing else in the console changing.

import { load, set, state } from "./store.js";

let onEvent = null;      // (event) => Promise that resolves when the check has been decided
let generation = 0;      // raised to cancel whatever sequence is running

const wait = (ms) => new Promise((resolve) => setTimeout(resolve, ms / state.speed));

export async function start(handler) {
  onEvent = handler;
  const response = await fetch("data/demo_events.json");
  if (!response.ok) throw new Error(`could not load the recorded events (${response.status})`);
  load(await response.json());
  ambient();
}

export function runIds() {
  const ids = [...state.runs.values()].filter((r) => r.variant === state.variant);
  return { scenarios: ids.filter((r) => r.set === "scenario"), probes: ids.filter((r) => r.set === "probe") };
}

async function playRun(run, mine) {
  for (const event of state.events.get(`${state.variant}/${run}`) || []) {
    if (mine !== generation) return false;
    await onEvent(event);
    await wait(260);
  }
  return mine === generation;
}

// Ambient traffic: every run of the current configuration in a fixed shuffled order, forever.
async function ambient() {
  const mine = ++generation;
  const { scenarios, probes } = runIds();
  const order = [...scenarios, ...probes].map((r) => r.run);
  for (let i = order.length - 1; i > 0; i--) {           // deterministic shuffle: same order each load
    const j = (i * 7 + 3) % (i + 1);
    [order[i], order[j]] = [order[j], order[i]];
  }
  for (let i = 0; mine === generation; i = (i + 1) % order.length) {
    while (!state.ambient && mine === generation) await new Promise((r) => setTimeout(r, 150));
    if (!(await playRun(order[i], mine))) return;
    await wait(700);
  }
}

// Play one run now (a scenario chip), then go back to ambient traffic.
export async function play(run) {
  const mine = ++generation;
  set({ playing: run }, "playing");
  const finished = await playRun(run, mine);
  if (!finished) return;
  set({ playing: null }, "playing");
  await wait(2500);
  if (mine === generation) ambient();
}

export function restart() { ambient(); }
