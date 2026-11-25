// Starts the console and connects its parts:
//   stream    hands over one event at a time
//   pipeline  runs it down its track and says when the guard has decided
//   store     then records the decision, and the log, status line and inspect pane redraw
// It also owns the keyboard, because a terminal program is driven from it.

import * as detail from "./detail.js";
import * as feed from "./feed.js";
import * as kpi from "./kpi.js";
import * as pipeline from "./pipeline.js";
import { VARIANT_LABELS, commit, describe, select, set, setFilter, setFollow, setVariant, state, subscribe } from "./store.js";
import * as stream from "./stream.js";
import * as theme from "./theme.js";

const variantSelect = document.getElementById("variant");
const runs = document.getElementById("runs");
const live = document.getElementById("live");

function buildControls() {
  document.getElementById("domain").innerHTML = `<span class="k">domain</span><b>${state.meta.domain}</b>`;
  variantSelect.innerHTML = Object.keys(state.meta.variants)
    .map((v) => `<option value="${v}"${v === state.variant ? " selected" : ""}>${(VARIANT_LABELS[v] || v).toLowerCase()}</option>`).join("");
  buildRuns();
}

function buildRuns() {
  const { scenarios, probes } = stream.runIds();
  const item = (run) => `<button type="button" class="run ${run.kind}" role="listitem" data-run="${run.run}" title="${run.kind}: ${run.label}">${run.run}</button>`;
  runs.innerHTML = scenarios.map(item).join("") + `<span class="gap">│</span>` + probes.map(item).join("");
}

function changeVariant(variant) {
  variantSelect.value = variant;
  pipeline.clear();
  setVariant(variant);
  buildRuns();
  stream.restart();
}

function setSpeed(speed) { set({ speed }); }

// Move the selection one line up or down the log (older is up, as in a terminal).
function step(direction) {
  const lines = feed.shown();                    // newest first
  if (!lines.length) return;
  const at = state.selected ? lines.indexOf(state.selected) : -1;
  const next = at < 0 ? 0 : Math.min(lines.length - 1, Math.max(0, at - direction));
  select(lines[next]);
}

runs.addEventListener("click", (click) => {
  const run = click.target.closest(".run")?.dataset.run;
  if (!run) return;
  pipeline.clear();                              // whatever was on the tracks makes way
  set({ follow: true, epoch: state.epoch + 1 }, "select");   // and the inspect pane follows this run
  stream.play(run);
});
// the list of runs is one long row: let an ordinary mouse wheel move along it
runs.addEventListener("wheel", (wheel) => {
  if (!wheel.deltaY) return;
  wheel.preventDefault();
  runs.scrollLeft += wheel.deltaY;
}, { passive: false });
variantSelect.addEventListener("change", () => changeVariant(variantSelect.value));
live.addEventListener("click", () => set({ ambient: !state.ambient }));
document.getElementById("theme-key")?.addEventListener("click", theme.toggle);

document.addEventListener("keydown", (key) => {
  if (key.target.matches("select, input, textarea") || key.metaKey || key.ctrlKey || key.altKey) return;
  const variants = Object.keys(state.meta?.variants || {});
  const actions = {
    " ": () => set({ ambient: !state.ambient }),
    "1": () => setSpeed(0.5), "2": () => setSpeed(1), "3": () => setSpeed(2),
    "t": theme.toggle,
    "g": () => changeVariant(variants[(variants.indexOf(state.variant) + 1) % variants.length]),
    "f": () => setFollow(!state.follow),
    "ArrowUp": () => step(-1), "k": () => step(-1),
    "ArrowDown": () => step(1), "j": () => step(1),
    "Escape": () => state.filter && setFilter(state.filter),
  };
  const action = actions[key.key];
  if (!action) return;
  key.preventDefault();
  action();
});

subscribe((what) => {
  if (what === "loaded") buildControls();
  if (what === "settings" || what === "loaded") {
    live.classList.toggle("is-paused", !state.ambient);
    document.getElementById("live-label").textContent = `${state.ambient ? "replay" : "paused"} ${state.speed}×`;
  }
  if (what === "now" || what === "variant") {
    const run = state.now && state.runs.get(`${state.variant}/${state.now}`);
    document.getElementById("now").innerHTML = run
      ? `<b class="${run.kind}">${run.run}</b> ${describe(run)}` : "";
  }
  if (what === "playing") {
    for (const item of runs.querySelectorAll(".run")) item.classList.toggle("is-playing", item.dataset.run === state.playing);
  }
});

window.matchMedia("(prefers-reduced-motion: reduce)").addEventListener("change", (query) => set({ reducedMotion: query.matches }));

pipeline.init();
kpi.init();
feed.init();
detail.init();

// One event: run the check down its track, and when the guard has decided, record the
// decision. A check that was still on the track when the tracks were cleared (another run was
// asked for, or the configuration changed) is dropped: it never reached a decision on screen.
stream.start(async (event) => {
  const epoch = state.epoch;
  if (state.now !== event.run) set({ now: event.run }, "now");
  await pipeline.spawn(event);
  if (epoch === state.epoch) commit(event);
}).catch((error) => {
  document.getElementById("inspect").innerHTML = `<p class="empty">${error.message}.<br>Run: uv run python scripts/export_console_demo.py<br>and serve this folder over HTTP.</p>`;
});

// for the browser console while developing
window.consoleState = state;
window.selectEvent = select;
