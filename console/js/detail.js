// The inspect pane: why one check was decided the way it was, laid out like the output of a
// `describe` command.
//   layers   the result of every layer, in the order the check met them
//   text     what was checked; sentences the guard removed are struck through
//   origin   for a tainted argument, the place in untrusted content where the value appeared
//   record   the event itself, to copy

import { esc, subject, verdict } from "./feed.js";
import { describe, eventsOf, harmExecuted, runOf, setFollow, state, subscribe } from "./store.js";

const GLYPH = { pass: "✓", clear: "✓", flag: "!", fail: "!", block: "✗", skip: "–", off: "·" };
const SAID = { pass: "passed", clear: "cleared the flag", flag: "flagged", fail: "could not answer", block: "blocked",
               skip: "not needed", off: "off in this configuration" };
let pane, showing = null;

const digits = (text) => text.replace(/\D/g, "");
function sameNumber(a, b) {                   // mirrors guardrail/trust.py: a country code or trunk zero may differ
  if (a === b) return true;
  const [short, long] = [a.replace(/^0+/, ""), b.replace(/^0+/, "")].sort((x, y) => x.length - y.length);
  return short.length >= 7 && long.endsWith(short);
}

// Where a value sits in a text: the exact words, or the same number written differently.
function locate(text, value) {
  const at = text.toLowerCase().indexOf(String(value).toLowerCase());
  if (at >= 0) return [at, at + String(value).length];
  const wanted = digits(String(value));
  if (wanted.length < 3) return null;
  for (const match of text.matchAll(/\+?\d[\d\s\-().]*\d/g)) {
    if (sameNumber(digits(match[0]), wanted)) return [match.index, match.index + match[0].length];
  }
  return null;
}

function struck(text, removed) {
  let html = "", from = 0;
  for (const sentence of removed) {
    const at = text.indexOf(sentence, from);
    if (at < 0) continue;
    html += esc(text.slice(from, at)) + `<del>${esc(sentence)}</del>`;
    from = at + sentence.length;
  }
  return html + esc(text.slice(from));
}

const section = (title) => `<h3 data-rule="${"─".repeat(Math.max(4, 46 - title.length))}">${title}</h3>`;

function layers(event) {
  return `${section("layers")}<ol class="steps">${event.trace.map((step) => `
    <li class="s-${step.status}"><i>${GLYPH[step.status]}</i><b>${esc(step.layer)}</b><span class="d">${esc(step.status === "skip" && step.detail ? step.detail : SAID[step.status] + (step.detail ? "  " + step.detail : ""))}</span></li>`).join("")}</ol>`;
}

function input(event) {
  const note = event.verdict === "BLOCK" ? "withheld: the agent never saw this text"
    : event.removed.length ? "struck-through sentences were removed; the agent received the rest" : "";
  const body = event.verdict === "BLOCK" ? esc(event.text) : struck(event.text, event.removed);
  return `${section("text")}<pre class="ind">${body}</pre>${note ? `<p class="ind c-dim">└─ ${note}</p>` : ""}`;
}

function action(event) {
  const args = Object.entries(event.args).map(([name, value]) => {
    const text = esc(typeof value === "string" ? `"${value}"` : String(value));
    return event.taint?.arg === name ? `  ${esc(name)}: <mark>${text}</mark>  <span class="c-yellow">◀── copied from untrusted content</span>` : `  ${esc(name)}: ${text}`;
  }).join("\n");
  let html = `${section("call")}<pre class="ind">${esc(event.tool)}(\n${args}\n)</pre>`;
  if (event.taint) {
    const source = eventsOf(event).find((e) => e.checkpoint === "input" && e.source === event.taint.source);
    const span = source && locate(source.text, event.taint.value);
    if (span) {
      const text = source.text, [from, to] = span, start = Math.max(0, from - 96), end = Math.min(text.length, to + 64);
      html += `${section("origin")}<p class="ind c-dim">${esc(event.taint.source)}, read earlier in this session</p>
        <pre class="ind">${start ? "…" : ""}${esc(text.slice(start, from))}<mark>${esc(text.slice(from, to))}</mark>${esc(text.slice(to, end))}${end < text.length ? "…" : ""}</pre>
        <p class="ind c-dim">└─ the user never typed this value</p>`;
    }
  }
  if (event.judge) html += `${section("alignment judge")}<pre class="ind">${esc(event.judge.reason)}</pre>`;
  if (event.verdict === "ASK_CONFIRM") {
    const answer = event.executed === true ? "the person asked said yes, and the call ran"
      : event.executed === false ? "the person asked did not confirm, and the call did not run" : "waiting for the user's answer";
    html += `<p class="ind c-dim">└─ ${answer}</p>`;
  }
  return html;
}

function output(event) {
  let html = `${section("reply as written")}<pre class="ind">${esc(event.text)}</pre>`;
  if (event.verdict === "BLOCK") html += `<p class="ind c-dim">└─ not sent; the user received a refusal instead</p>`;
  else if (event.result_text !== event.text) {
    html += `${section("reply as sent")}<pre class="ind">${esc(event.result_text).replace(/\[(link removed|address removed|redacted)\]/g, "<ins>[$1]</ins>")}</pre>`;
  }
  return html;
}

function render() {
  const event = state.selected;
  if (!event) {
    showing = null;
    pane.innerHTML = `<p class="empty">No decision selected.<br>This pane follows the newest decision that was not a plain ALLOW. Click a line, or use ↑ ↓, to look at one.</p>`;
    return;
  }
  if (showing === event.id) { pane.querySelector(".follow")?.classList.toggle("is-on", state.follow); return; }
  showing = event.id;
  const run = runOf(event);
  const by = harmExecuted(event) ? `<span class="harm">GOT THROUGH</span> <span class="c-dim">no layer stopped it</span>`
    : `${verdict(event)}${event.layer && event.layer !== "none" ? ` <span class="c-dim">by</span> <b>${esc(event.layer.replace("_", " "))}</b>` : ""}`;
  const record = { ...event };
  delete record.at;
  pane.innerHTML = `
    <p class="head">${by} <span class="c-dim">${event.latency_ms.toFixed(1)} ms</span>
      <button type="button" class="act follow push${state.follow ? " is-on" : ""}" title="Keep showing the newest notable decision (f)">follow</button></p>
    <p class="sub">${esc(event.run)}  ${esc(describe(run))}</p>
    <p class="sub">${esc(event.checkpoint)}  ${esc(subject(event))}</p>
    ${layers(event)}
    ${event.reasons.length ? `${section("reason")}<pre class="ind">${event.reasons.map(esc).join("\n")}</pre>` : ""}
    ${{ input, action, output }[event.checkpoint](event)}
    <details><summary>record <button type="button" class="act copy">copy json</button></summary><pre>${esc(JSON.stringify(record, null, 2))}</pre></details>`;
  pane.querySelector(".follow").addEventListener("click", () => setFollow(!state.follow));
  pane.querySelector(".copy").addEventListener("click", async (click) => {
    click.preventDefault();
    await navigator.clipboard.writeText(JSON.stringify(record, null, 2));
    click.target.textContent = "copied";
    setTimeout(() => { click.target.textContent = "copy json"; }, 1200);
  });
  pane.scrollTop = 0;
  if (state.reducedMotion) return;
  pane.animate([{ opacity: 0.35 }, { opacity: 1 }], { duration: 160, easing: "ease-out" });
  pane.querySelectorAll(".steps li").forEach((item, index) => {
    item.animate([{ opacity: 0, transform: "translateX(-6px)" }, { opacity: 1, transform: "none" }],
                 { duration: 200, delay: 35 * index, easing: "cubic-bezier(0.22, 1, 0.36, 1)", fill: "backwards" });
  });
}

export function init() {
  pane = document.getElementById("inspect");
  subscribe((what) => { if (["event", "select", "variant", "loaded"].includes(what)) render(); });
  render();
}
