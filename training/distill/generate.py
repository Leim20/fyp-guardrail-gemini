"""Step 1: the teacher writes labelled texts, one call per job in the generation grid.

Resumable: each call's reply is cached in data/synthetic/<stage>/raw/<job>.json with its job,
prompt version, model, token usage and timestamp, and finished jobs are skipped on a rerun.

Usage:
    uv run python -m training.distill.generate --stage pilot
"""

import argparse
import json
from concurrent.futures import ThreadPoolExecutor, as_completed
from datetime import datetime, timezone
from pathlib import Path

from training.distill.prompts import GENERATOR_SYSTEM, PROMPT_VERSION, STAGES, build_jobs, generation_prompt
from training.distill.teacher import GENERATOR, ROOT, Teacher

SYNTHETIC_DIR = ROOT / "data" / "synthetic"
WORKERS = 8


def stage_dir(stage: str) -> Path:
    return SYNTHETIC_DIR / stage


def run_job(teacher: Teacher, job: dict, raw_dir: Path) -> dict:
    reply, usage = teacher.generate_json(GENERATOR_SYSTEM, generation_prompt(job))
    record = {"job": job, "prompt_version": PROMPT_VERSION, "model": GENERATOR, "usage": usage,
              "created": datetime.now(timezone.utc).isoformat(timespec="seconds"), "reply": reply}
    (raw_dir / f"{job['id']}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False))
    return record


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--stage", choices=list(STAGES), required=True)
    stage = parser.parse_args().stage

    raw_dir = stage_dir(stage) / "raw"
    raw_dir.mkdir(parents=True, exist_ok=True)
    jobs = build_jobs(stage)
    (stage_dir(stage) / "jobs.jsonl").write_text("".join(json.dumps(j) + "\n" for j in jobs))
    todo = [j for j in jobs if not (raw_dir / f"{j['id']}.json").exists()]
    print(f"{len(jobs)} jobs, {len(jobs) - len(todo)} cached, {len(todo)} to run")

    teacher, failed = Teacher(), []
    with ThreadPoolExecutor(WORKERS) as pool:
        futures = {pool.submit(run_job, teacher, job, raw_dir): job for job in todo}
        for done, future in enumerate(as_completed(futures), start=1):
            job = futures[future]
            try:
                record = future.result()
                print(f"[{done}/{len(todo)}] {job['id']}: {len(record['reply'].get('items', []))} items, "
                      f"{record['usage']['completion_tokens']} tokens, {record['usage']['finish_reason']}", flush=True)
            except Exception as error:  # one bad reply must not stop the batch; a rerun retries it
                failed.append(job["id"])
                print(f"[{done}/{len(todo)}] {job['id']}: FAILED {type(error).__name__}: {error}", flush=True)

    records = [json.loads(p.read_text()) for p in sorted(raw_dir.glob("*.json"))]
    cost = sum(r["usage"]["usd_peak"] for r in records)
    print(f"\n{len(records)}/{len(jobs)} jobs done, {sum(len(r['reply'].get('items', [])) for r in records)} items, "
          f"cost US${cost:.3f} at peak rates (half off-peak)" + (f"; failed: {failed}" if failed else ""))


if __name__ == "__main__":
    main()
