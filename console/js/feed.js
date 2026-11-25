// The decision log: one line per check, written the way the agent's own CLI prints them.
// Like `tail -f`, new lines appear at the bottom and the view stays pinned there unless the
// reader has scrolled up to look at something.

import { acted, describe, harmExecuted, runOf, select, setFilter, state, subscribe } from "./store.js";

const MAX_LINES = 300;
const WORD = { ALLOW: "✓ ALLOW", SANITIZE: "~ SANITIZE", ASK_CONFIRM: "? CONFIRM", BLOCK: "✗ BLOCK" };
const KIND = { input: "input", action: "tool", output: "output" };
let list, filterBox;

export const esc = (text) => String(text ?? "").replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" }[c]));
export const verdict = (event) => `<span class="v ${event.verdict}">${WORD[event.verdict]}</span>`;

export function subject(event) {
  if (event.checkpoint === "input") {
    // the user's own words are the subject; for a document it is where it came from
    return event.trust === "user" ? `"${event.text}"` : `${event.source} (${event.trust})`;
  }
  if (event.checkpoint === "action") return `${event.tool}(${JSON.stringify(event.args)})`;
  return "reply to the user";
}

function why(event) {
  if (harmExecuted(event)) return "";
  if (event.verdict === "ALLOW") return event.layer === "judge" ? "cleared by the judge" : "";
  return event.reasons[0] || event.layer;
}

function makeLine(event) {
  const line = document.createElement("li");
  line.className = "line";
  line.tabIndex = -1;
  line.dataset.id = event.id;
  const reason = why(event);
  line.innerHTML = `<span class="l-time">${event.at.toLocaleTimeString("en-GB", { hour12: false })}</span>
    <span class="l-run">${esc(event.run)}</span><span class="l-kind">${KIND[event.checkpoint]}</span>
    <span class="l-what" title="${esc(subject(event))}">${esc(subject(event))}${reason ? ` <span class="why">${esc(reason)}</span>` : ""}</span>
    <span>${harmExecuted(event) ? '<span class="harm">GOT THROUGH</span>' : verdict(event)}</span>`;
  line.addEventListener("click", () => select(event));
  return line;
}

// A heading before the first line of each run: which scenario this is, so a reader who looks
// up mid-stream knows what they are watching.
function makeHeading(event) {
  const run = runOf(event), heading = document.createElement("li");
  heading.className = `run-head ${run?.kind || ""}`;
  heading.innerHTML = `<b>${esc(event.run)}</b> ${esc(describe(run))}`;
  return heading;
}

export const shown = () => state.feed.filter((event) => !state.filter || acted(event, state.filter)).slice(0, MAX_LINES);

function mark() {
  for (const line of list.children) line.classList.toggle("is-selected", !!state.selected && Number(line.dataset.id) === state.selected.id);
}

function rebuild() {
  const lines = shown().reverse();                             // state.feed is newest first; a log reads downwards
  list.replaceChildren(...lines.flatMap((event, index) =>
    !state.filter && event.run !== lines[index - 1]?.run ? [makeHeading(event), makeLine(event)] : [makeLine(event)]));
  list.scrollTop = list.scrollHeight;
  mark();
  filterBox.textContent = state.filter ? `/${state.filter}  ×` : "";
}

function append() {
  const event = state.feed[0];
  if (state.filter && !acted(event, state.filter)) return;
  const pinned = list.scrollHeight - list.scrollTop - list.clientHeight < 40;
  const line = makeLine(event);
  if (!state.filter && state.feed[1]?.run !== event.run) list.append(makeHeading(event));
  list.append(line);
  while (list.children.length > MAX_LINES) list.firstElementChild.remove();
  if (pinned) list.scrollTop = list.scrollHeight;
  mark();
  if (!state.reducedMotion) line.animate([{ opacity: 0, transform: "translateY(5px)" }, { opacity: 1, transform: "none" }],
                                         { duration: 160, easing: "cubic-bezier(0.22, 1, 0.36, 1)" });
}

function reveal() {
  mark();
  list.querySelector(".is-selected")?.scrollIntoView({ block: "nearest" });
}

export function init() {
  list = document.getElementById("log");
  filterBox = document.getElementById("filter");
  filterBox.addEventListener("click", () => setFilter(state.filter));
  subscribe((what) => {
    if (what === "event") append();
    else if (what === "select") reveal();
    else if (what === "variant" || what === "filter") rebuild();
  });
}
