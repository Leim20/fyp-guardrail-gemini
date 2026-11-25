# Guardrail console: design notes

Status: prototype of the main view (2026-11-17). It plays back checks recorded from the real
guard. Live data, and the other views, come after the guard is served over HTTP (FR07).
The supervisor has not seen it yet; their view as a marker decides whether the direction stays.

## What it is for

The guardrail is middleware and has no interface of its own. The console is the interface of
the guardrail, not of an agent: it is for a developer or a marker who wants to see what the
guard decided and why. IR §1.5.1 lists what such an interface shows: the input, the findings
of each layer, the verdict, the reason, the proposed tool call and the latency. Each of those
has a place below.

## The idea

A railway signalling diagram, drawn in the vocabulary of a terminal.

| In the guard | On screen |
|---|---|
| a checkpoint (input, action, output) | a track |
| a layer (rules, classifier, policy, taint, ...) | a signal beside the track |
| one check | a train: a block of inverse video carrying the run's id |
| the layer's result | the lamp: green pass, amber flag, red stop |
| BLOCK | the train stops before the red signal and goes no further |

The metaphor was chosen because it is not decoration: a railway signal's three aspects mean
proceed, caution and stop, which is what ALLOW, SANITIZE and BLOCK mean. A viewer who knows
nothing about the guard can read where a check was stopped.

The terminal vocabulary (one monospaced face, the ANSI colours of a terminal emulator, square
corners, no shadows) was chosen for two reasons. The audience is developers, and this is how
their own tools look. And an earlier version in the usual dashboard style (dark blue, glowing
accents, rounded cards) was rejected as generic; the project's own command-line output was the
better-looking artefact, so the console now extends it instead of replacing it.

One thing is taken from the browser that a terminal cannot do: a train glides along its track
instead of jumping a character at a time.

## The screen

| Area | Question it answers | IR §1.5.1 item |
|---|---|---|
| Status line | How many checks so far, how many of each verdict, how often the judge was called, how much latency the guard adds, did any attack get through | latency |
| Pipeline | Which layers did this check meet, and which one decided. The count under a signal is how often that layer acted | layer-level findings |
| Decisions | What happened, in order, one line per check, grouped by run | verdict, proposed tool call |
| Inspect | Why: every layer's result, the reason, the text with removed sentences struck through, where a tainted value came from, the judge's reason, the event record | input, reason |
| Command line | Run a chosen scenario now; the keyboard shortcuts | — |

Clicking a signal filters the decisions to those that layer acted on.

## Motion

Every animation states something about the guard. Nothing moves for its own sake.

| Motion | Meaning |
|---|---|
| A train moves along a track | a check is in progress |
| The train waits before a signal, the lamp lights | that layer is judging it |
| The lamp turns red and blinks, the train turns red and stays | the check was blocked here |
| The train turns amber | something was flagged; with SANITIZE, part of the text was removed |
| The train waits with a "?" before the end | ASK_CONFIRM: a person is being asked |
| A line appears at the bottom of the log | the guard has decided |
| A number in the status line rolls to its new value | the totals changed |

Only `transform` and `opacity` are animated, so it stays smooth. Durations are 140 to 280 ms.
With the system setting "reduce motion" on, trains jump from signal to signal and numbers
change at once.

## Themes

Dark is a terminal window, for a live demonstration. Light is the same terminal on paper, for
screenshots in the report. Both are the same page: every colour is a CSS variable
(`console/css/tokens.css`), and the theme sets the values. A verdict is never shown by colour
alone; each has a glyph and a word (✓ ALLOW, ~ SANITIZE, ? CONFIRM, ✗ BLOCK).

## Where the data comes from

```
now:    scripts/export_console_demo.py -> console/data/demo_events.json -> js/stream.js -> the page
later:  guard -> GET /events (Server-Sent Events) -> js/stream.js -> the page
```

The recording is made by replaying the 16 scenarios and the review probes through five guard
configurations. Verdicts, layers, reasons, scores and latencies are what the guard returned.
Two things are added for display:

- the checked text. The audit log keeps only a hash and a redacted preview; the recording
  keeps the words. All data is synthetic. In a deployment this would be off by default.
- a per-layer `trace`, derived from the audit record's fields. The engine does not emit one
  yet; when it does, the derivation in the export script is deleted.

Only `js/stream.js` knows where events come from, so replacing the recording with a live
stream changes that one file.

### Event format

```json
{"id": 17, "run": "IND-006", "variant": "full", "checkpoint": "action",
 "tool": "update_account", "args": {"field": "phone", "value": "+60 11-6666 6666"},
 "verdict": "BLOCK", "layer": "taint", "reasons": ["tainted_argument: value copied from ..."],
 "latency_ms": 0.9, "harm": true, "executed": null,
 "trace": [{"layer": "schema", "status": "pass", "detail": ""},
           {"layer": "taint", "status": "block", "detail": "tainted_argument: ..."}],
 "taint": {"arg": "value", "value": "+60 11-6666 6666", "source": "search_kb:forum_sms_tracking.md"}}
```

`status` is one of pass, flag, block, clear (the judge cleared a flag), fail (the judge could
not answer), skip (not needed) and off (switched off in this configuration). Input and output
events also carry `text` and `result_text`.

## Files

| File | Role |
|---|---|
| `console/index.html` | the page |
| `console/css/tokens.css`, `app.css` | the two themes; layout and components |
| `console/js/store.js` | the state, in one place; tells the other modules what changed |
| `console/js/stream.js` | where events come from |
| `console/js/pipeline.js` | tracks, signals and trains (SVG) |
| `console/js/feed.js`, `detail.js`, `kpi.js` | the decision log, the inspect pane, the status line |
| `console/js/main.js` | start-up and the keyboard |
| `scripts/export_console_demo.py` | makes the recording |
| `scripts/serve_console.py` | serves the page without caching while it is developed |

There is no build step and no framework: plain JavaScript modules, SVG, CSS. Nothing is
loaded from the network, so the console runs offline.

## Not done

- Live data over Server-Sent Events, and switching individual layers on and off (needs FR07).
- The other views: a manual inspector, side-by-side replay, policy, evaluation with export.
- The trace shows policy as one signal; which rule refused is in the detail, but rules that
  passed are not listed individually.
- Look-alike fonts on other machines: the page asks for Ubuntu Sans Mono and falls back to the
  system's monospaced face.
