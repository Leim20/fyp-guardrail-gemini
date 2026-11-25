// Dark and light are the same page with different token values (css/tokens.css). Where the
// browser supports the View Transitions API, the switch cross-fades instead of snapping.

import { set, state } from "./store.js";

export function toggle() {
  const next = document.documentElement.dataset.theme === "dark" ? "light" : "dark";
  const apply = () => {
    document.documentElement.dataset.theme = next;
    try { localStorage.setItem("console-theme", next); } catch (e) { /* private window: not remembered */ }
    set({}, "theme");
  };
  if (document.startViewTransition && !state.reducedMotion) document.startViewTransition(apply);
  else apply();
}
