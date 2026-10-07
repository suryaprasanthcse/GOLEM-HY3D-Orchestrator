"""Measure how fast the pipeline turns a prompt into an interactive prop, for the pitch.

  python golem_metrics.py             # writes METRICS.md and assets/metrics.json
  python golem_metrics.py --no-unity  # skip the Unity stage (no Editor open)

- Hyper3D (cloud): from each job's own record, submitted -> downloaded, so it includes the
  queue, generation and download. Polling backs off to 30 s, so these can overstate by up to ~30 s.
- GOLEM split (local): each prop's split step from golem_props.json is replayed and timed.
  Blender's start-up is measured on its own too, since a batch run would pay it only once.
- GOLEM Unity (local): the open Editor (through the Unity CLI) rebuilds every prop with a
  fresh glTFast import, timing import + joints per prop, then the self-test and the stage.
"""

import argparse
import json
import os
import platform
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent
RAW = ROOT / "assets" / "raw"
BLENDER = os.environ.get("GOLEM_BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
CREDITS_PER_JOB = 0.5  # measured from the wallet before/after each job (Medium, High and BANG alike)


def hyper3d_seconds(job_name: str) -> float | None:
    record = json.loads((RAW / f"{job_name}.job.json").read_text(encoding="utf-8"))
    if "downloaded_at" not in record:
        return None
    start = datetime.fromisoformat(record["submitted_at"])
    end = datetime.fromisoformat(record["downloaded_at"])
    return (end - start).total_seconds()


def timed(command: list[str]) -> float:
    start = time.perf_counter()
    result = subprocess.run(command, cwd=ROOT, capture_output=True, text=True, encoding="utf-8", errors="replace")
    if result.returncode != 0:
        raise SystemExit(f"{' '.join(command)} failed:\n{result.stdout}\n{result.stderr}")
    return time.perf_counter() - start


def unity_rebuild(wait_minutes: float = 10) -> dict | None:
    """Ask the open Editor for a timed rebuild. Fresh imports outlast the Pipeline server's 5 s
    main-thread wait, so the Editor writes its timings to a file, which we wait for instead."""
    timing = ROOT / "assets" / "unity_timing.json"
    timing.unlink(missing_ok=True)
    env = {**os.environ, "UNITY_NO_BANNER": "1", "UNITY_NO_PAGER": "1", "UNITY_NO_CONSENT_PROMPT": "1"}
    command = ["unity", "command", "eval", "return Golem.EditorTools.GolemMenu.TimedRebuild(true);",
               "--timeout", "900", "--format", "json"]
    result = subprocess.run(command, cwd=ROOT / "GolemUnity", capture_output=True, text=True, env=env,
                            encoding="utf-8", errors="replace", shell=sys.platform == "win32")
    if '"success": true' not in result.stdout and "timed out" not in result.stdout:
        print(f"Unity stage skipped (is the Editor open?): {result.stdout[:300]} {result.stderr[:300]}")
        return None
    deadline = time.monotonic() + wait_minutes * 60
    while time.monotonic() < deadline:
        if timing.exists():
            return json.loads(timing.read_text(encoding="utf-8"))
        time.sleep(2)
    print("Unity stage: no timing file after waiting")
    return None


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--no-unity", action="store_true")
    args = parser.parse_args()
    props = json.loads((ROOT / "golem_props.json").read_text(encoding="utf-8"))["props"]

    blender_startup = timed([BLENDER, "-b", "--factory-startup", "--python-expr", "pass"])
    print(f"Blender start-up: {blender_startup:.1f} s")
    rows = []
    for prop in props:
        cloud = [hyper3d_seconds(job) for job in prop["hyper3d_jobs"]]
        split = timed([sys.executable, *prop["split"]])
        rows.append({"name": prop["name"], "hyper3d_jobs": prop["hyper3d_jobs"], "hyper3d_s": cloud,
                     "credits": CREDITS_PER_JOB * len(prop["hyper3d_jobs"]), "split_s": round(split, 2)})
        print(f"{prop['name']}: Hyper3D {cloud} s, split {split:.1f} s")

    unity = None if args.no_unity else unity_rebuild()
    for row in rows:
        ms = (unity or {}).get("import_and_articulate_ms", {}).get(row["name"])
        row["unity_s"] = None if ms is None else round(ms / 1000, 2)
        local = row["split_s"] + (row["unity_s"] or 0)
        row["golem_local_s"] = round(local, 2)
        row["golem_local_without_blender_startup_s"] = round(local - blender_startup, 2)
        row["total_s"] = round(sum(s for s in row["hyper3d_s"] if s) + local, 1)

    machine = f"{platform.processor() or platform.machine()}, {platform.system()} {platform.release()}"
    result = {"measured_at": datetime.now().astimezone().isoformat(timespec="seconds"), "machine": machine,
              "blender_startup_s": round(blender_startup, 2), "unity": unity, "props": rows}
    (ROOT / "assets").mkdir(exist_ok=True)
    (ROOT / "assets" / "metrics.json").write_text(json.dumps(result, indent=2), encoding="utf-8")
    write_report(result)
    print("wrote METRICS.md and assets/metrics.json")
    return 0


def write_report(result: dict) -> None:
    fmt = lambda v: "n/a" if v is None else f"{v:.0f} s" if v >= 10 else f"{v:.1f} s" if v >= 1 else f"{v:.2f} s"
    lines = [
        "# GOLEM pipeline metrics",
        "",
        f"Measured {result['measured_at']} on {result['machine']} (laptop: i5-11320H, 24 GB RAM, GTX 1650).",
        "",
        "| Prop | Hyper3D cloud (generate + BANG) | GOLEM split | GOLEM Unity (import + joints) | GOLEM local total | End to end | Credits |",
        "|---|---|---|---|---|---|---|",
    ]
    for row in result["props"]:
        cloud = " + ".join(fmt(s) for s in row["hyper3d_s"])
        lines.append(f"| {row['name']} | {cloud} | {fmt(row['split_s'])} | {fmt(row['unity_s'])} | "
                     f"**{fmt(row['golem_local_s'])}** | {fmt(row['total_s'])} | {row['credits']} |")
    local = [r["golem_local_s"] for r in result["props"]]
    warm = [r["golem_local_without_blender_startup_s"] for r in result["props"]]
    unity = result["unity"] or {}
    lines += [
        "",
        f"- **GOLEM's own work per prop: {min(local):.1f}-{max(local):.1f} s** (average {sum(local) / len(local):.1f} s), "
        f"of which Blender start-up is {result['blender_startup_s']:.1f} s; without it, {min(warm):.1f}-{max(warm):.1f} s.",
        f"- Hyper3D generation and BANG run in the cloud, about 1.5-5 min per job, {result['props'][0]['credits'] / len(result['props'][0]['hyper3d_jobs'])} credits each.",
        f"- Unity, all props: self-test {unity.get("self_test_ms", 0) / 1000:.2f} s "
        f"({'passed' if unity.get('self_test_passed') else 'not run or failed'}), demo stage {unity.get("stage_ms", 0) / 1000:.2f} s.",
        "- Cloud times come from each job's record (submitted -> downloaded); polling backs off to 30 s, "
        "so they can overstate by up to ~30 s.",
        "- Unity imports were fresh (cached models deleted first), as for a newly generated asset.",
        "",
        "Regenerate: `python golem_metrics.py` with the Unity project open.",
    ]
    (ROOT / "METRICS.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


if __name__ == "__main__":
    sys.exit(main())
