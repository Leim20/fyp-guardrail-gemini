"""Step 3: an independent teacher re-labels every validated text without seeing its label.

The verifier (a larger model than the generator, fresh context, text only) answers yes/no in one
token; P(yes) comes from its log-probabilities. The text alone must decide the label, because
that is all the student will see.

    verifier agrees with confidence           -> keep
    anything else (uncertain or disagreeing)  -> escalate to thinking mode; keep if it agrees
    thinking mode disagrees too               -> disputed: left out, a human audits a sample
                                                 (scripts/spot_check.py)

Disagreements are escalated rather than dropped because the first pilot showed the non-thinking
verifier sharing the student's blind spot: it confidently rejected subtle injections that
thinking mode, and a reader, label correctly. Dropping them would remove exactly the hard
examples the distillation is for. In the second pilot the items still disputed after thinking
were generator errors (a "subtle" injection with no injection in it) or genuinely ambiguous.

Resumable: results are appended to data/synthetic/<stage>/verify_cache.jsonl.

Usage:
    uv run python -m training.distill.verify --stage pilot
"""

import argparse
import json
import math
import threading
from collections import Counter
from concurrent.futures import ThreadPoolExecutor, as_completed

import pandas as pd

from training.distill.generate import stage_dir
from training.distill.prompts import ESCALATION_SYSTEM, STAGES, VERIFIER_SYSTEM, escalation_prompt, verifier_prompt
from training.distill.teacher import Teacher

UNCERTAIN_BAND = (0.2, 0.8)
WORKERS = 16


def judge(teacher: Teacher, item: dict) -> dict:
    p, usage = teacher.p_yes(VERIFIER_SYSTEM, verifier_prompt(item["text"]))
    result = {"id": item["id"], "p_teacher": None if math.isnan(p) else round(p, 6), "usage": [usage]}
    confident = not math.isnan(p) and not UNCERTAIN_BAND[0] < p < UNCERTAIN_BAND[1]
    if confident and (p >= 0.5) == bool(item["label"]):
        return result | {"path": "verifier", "keep": True}
    answer, reasoning, usage = teacher.think(ESCALATION_SYSTEM, escalation_prompt(item["text"]))
    escalated_label = int(str(answer.get("label", "")).strip().lower() == "yes")
    agrees = escalated_label == item["label"]
    return result | {"path": "escalated" if agrees else "disputed", "keep": agrees,
                     "usage": [result["usage"][0], usage],
                     "escalation": {"label": escalated_label, "reason": answer.get("reason"), "reasoning": reasoning}}


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=list(STAGES), required=True)
    stage = parser.parse_args().stage

    out = stage_dir(stage)
    items = pd.read_json(out / "validated.jsonl", lines=True)
    cache_path = out / "verify_cache.jsonl"
    done = {r["id"]: r for r in map(json.loads, cache_path.read_text().splitlines())} if cache_path.exists() else {}
    todo = [row for row in items.to_dict("records") if row["id"] not in done]
    print(f"{len(items)} items, {len(done)} cached, {len(todo)} to verify")

    teacher, lock = Teacher(), threading.Lock()
    with ThreadPoolExecutor(WORKERS) as pool, cache_path.open("a") as cache:
        futures = {pool.submit(judge, teacher, item): item for item in todo}
        for n, future in enumerate(as_completed(futures), start=1):
            try:
                result = future.result()
            except Exception as error:  # a rerun retries it
                print(f"{futures[future]['id']}: FAILED {type(error).__name__}: {error}", flush=True)
                continue
            with lock:
                cache.write(json.dumps(result, ensure_ascii=False) + "\n")
                cache.flush()
                done[result["id"]] = result
            if n % 25 == 0 or n == len(todo):
                print(f"[{n}/{len(todo)}] verified", flush=True)

    results = pd.DataFrame([done[i] for i in items["id"] if i in done])
    merged = items.merge(results.drop(columns="usage"), on="id")
    kept = merged[merged["keep"]].drop(columns="keep")
    partners = kept["pair_id"].value_counts()
    kept = kept.assign(pair_complete=kept["pair_id"].map(partners).eq(2))
    kept.to_json(out / "verified.jsonl", orient="records", lines=True, force_ascii=False)
    merged[~merged["keep"]].to_json(out / "disputed.jsonl", orient="records", lines=True, force_ascii=False)

    cost = sum(u["usd_peak"] for r in done.values() for u in r["usage"])
    report = {
        "stage": stage, "verified": len(merged), "kept": len(kept), "disputed": int((~merged["keep"]).sum()),
        "paths": dict(Counter(merged["path"])),
        "escalated_by_category": merged[merged["path"] != "verifier"]["category"].value_counts().sort_index().to_dict(),
        "disputed_by_category": merged[~merged["keep"]]["category"].value_counts().sort_index().to_dict(),
        "kept_by_label": kept["label"].value_counts().sort_index().to_dict(),
        "complete_pairs": int(kept["pair_complete"].sum() // 2),
        "usd_peak": round(cost, 4),
    }
    (out / "verify_report.json").write_text(json.dumps(report, indent=2))
    print(json.dumps(report, indent=2))


if __name__ == "__main__":
    main()
