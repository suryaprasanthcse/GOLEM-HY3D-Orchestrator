"""Hyper3D client: text prompt -> Rodin Gen-2.5 model -> .glb in assets/raw/, plus BANG splits.

Drives the official Hyper3D CLI (npm: @hyper3d/cli) with JSON output. The CLI signs in
through the browser (`hyper3d auth login`), so no static API key is needed; raw REST
keys are locked to Hyper3D's Business tier.

Every billable job is recorded in assets/raw/<name>.job.json the moment the CLI accepts
it, so an interrupted run resumes without paying twice. The CLI never retries billable
requests itself, and neither does this script.

Usage:
  python hyper3d_client.py auth
  python hyper3d_client.py generate "wooden treasure chest with a hinged lid, closed" --name chest
  python hyper3d_client.py bang chest --instruction "separate the lid from the body"
  python hyper3d_client.py resume assets/raw/chest.job.json
"""

import argparse
import json
import os
import re
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

import requests

RAW_DIR = Path(__file__).resolve().parent / "assets" / "raw"
TIERS = ["Gen-2.5-Extreme-Low", "Gen-2.5-Medium", "Gen-2.5-High"]
POLL_FIRST, POLL_MAX = 5.0, 30.0
DONE = {"done", "completed", "complete", "succeeded", "success", "finished"}
FAILED = {"failed", "failure", "error", "cancelled", "canceled"}


def _cli() -> list[str]:
    """Run the CLI's JavaScript entry with Node directly: going through the Windows .cmd
    shim would let cmd.exe reinterpret quotes and '&' inside prompts."""
    script = os.environ.get("HYPER3D_CLI_JS")
    if not script:
        shim = shutil.which("hyper3d")
        if not shim:
            raise SystemExit("Hyper3D CLI not found. Run: npm install --global @hyper3d/cli@latest")
        script = str(Path(shim).parent / "node_modules" / "@hyper3d" / "cli" / "dist" / "index.js")
    node = shutil.which("node")
    if not node or not Path(script).exists():
        raise SystemExit(f"Cannot run the Hyper3D CLI (node={node}, script={script}). Set HYPER3D_CLI_JS.")
    return [node, script]


def run(*args: str) -> dict:
    """One CLI call with JSON output. Exits with the CLI's own error message on failure."""
    env = {**os.environ, "HYPER3D_UPDATE_CHECK": "0"}  # no update prompts mid-pipeline
    result = subprocess.run(
        [*_cli(), *args, "--output", "json"], capture_output=True, text=True, encoding="utf-8", env=env
    )
    if result.returncode != 0:
        message = (result.stderr or result.stdout).strip()
        if "auth" in message.lower() or "sign" in message.lower():
            message += "\nSign in again with: hyper3d auth login --no-browser"
        raise SystemExit(f"hyper3d {args[0]} failed: {message}")
    try:
        return json.loads(result.stdout)
    except json.JSONDecodeError:
        raise SystemExit(f"hyper3d {args[0]} returned non-JSON output: {result.stdout[:500]}")


def _find(data, keys):
    """First value under any of `keys`, searching nested dicts and lists."""
    if isinstance(data, dict):
        for key in keys:
            if data.get(key) not in (None, ""):
                return data[key]
        values = data.values()
    elif isinstance(data, list):
        values = data
    else:
        return None
    for value in values:
        found = _find(value, keys)
        if found is not None:
            return found
    return None


def _urls(data, found=None) -> list[str]:
    found = [] if found is None else found
    if isinstance(data, str) and data.startswith("http"):
        found.append(data)
    elif isinstance(data, dict):
        for value in data.values():
            _urls(value, found)
    elif isinstance(data, list):
        for value in data:
            _urls(value, found)
    return found


def _now() -> str:
    return datetime.now().astimezone().isoformat(timespec="seconds")


def _save(job: dict) -> None:
    Path(job["manifest"]).write_text(json.dumps(job, indent=2), encoding="utf-8")


def _start(name: str, args: list[str], record: dict) -> dict:
    """Submit a billable CLI command and record it before anything else can go wrong."""
    RAW_DIR.mkdir(parents=True, exist_ok=True)
    manifest = RAW_DIR / f"{name}.job.json"
    if manifest.exists():
        raise SystemExit(f"{manifest} already exists: pick another --name, or resume that job.")
    response = run(*args)
    generation_id = _find(response, ["generation_id", "generationId", "id", "uuid", "task_uuid"])
    if not generation_id:
        raise SystemExit(f"No generation id in the CLI response: {json.dumps(response)[:500]}")
    job = {
        "name": name,
        **record,
        "generation_id": generation_id,
        "submitted_at": _now(),
        "submit_response": response,
        "manifest": str(manifest),
    }
    _save(job)
    print(f"[submit] {name}: generation {generation_id}")
    return job


def generate(prompt, name, tier, quality, mesh_mode, fmt="glb") -> dict:
    args = ["generate", "--prompt", prompt, "--format", fmt]
    if tier:
        args += ["--tier", tier]
    if quality:
        args += ["--quality", str(quality)]
    if mesh_mode:
        args += ["--mesh-mode", mesh_mode]
    return _start(name, args, {"kind": "generate", "prompt": prompt, "tier": tier or "server default", "format": fmt})


def bang(source: str, instruction, strength, name) -> dict:
    """Split a finished generation into parts. `source` is a job name or a generation id."""
    manifest = RAW_DIR / f"{source}.job.json"
    source_id = json.loads(manifest.read_text())["generation_id"] if manifest.exists() else source
    args = ["bang", source_id, "--format", "glb"]
    if instruction:
        args += ["--instruction", instruction]
    if strength:
        args += ["--strength", str(strength)]
    record = {"kind": "bang", "source_generation_id": source_id, "instruction": instruction, "strength": strength}
    return _start(name or f"{source}_bang", args, record)


def wait(job: dict, timeout_minutes: float) -> None:
    """Check status (5 s, backing off to 30 s) until done; exit on failure or timeout."""
    start, interval, last, failures = time.monotonic(), POLL_FIRST, None, 0
    while True:
        time.sleep(interval)
        try:
            response = run("status", job["generation_id"])
            failures = 0
        except SystemExit as error:
            # A status check timing out says nothing about the job, which keeps running
            # server-side; only give up after several failures in a row.
            failures += 1
            if failures >= 5:
                raise
            print(f"[status] check failed ({failures}/5), retrying: {error}")
            interval = min(interval * 1.5, POLL_MAX)
            continue
        status = str(_find(response, ["status", "state"]) or "unknown")
        elapsed = time.monotonic() - start
        if status != last:
            print(f"[status] {elapsed:5.0f} s  {status}")
            last = status
        if status.lower() in FAILED:
            job["failed_at"], job["status_response"] = _now(), response
            _save(job)
            raise SystemExit(f"Hyper3D reports '{status}' after {elapsed:.0f} s. Record: {job['manifest']}")
        if status.lower() in DONE:
            job["generation_seconds"], job["status_response"] = round(elapsed), response
            _save(job)
            return
        if elapsed > timeout_minutes * 60:
            raise SystemExit(f"Still '{status}' after {timeout_minutes} min. Resume later: {job['manifest']}")
        interval = min(interval * 1.5, POLL_MAX)


def download(job: dict) -> list[Path]:
    """Fetch the result files at once (their URLs can expire) and keep the .glb or .fbx ones. For an FBX
    the download URL is kept too: Tencent's HY-3D-Component reads its input from a URL."""
    response = run("result", job["generation_id"])
    # Rodin returns e.g. base_basic_pbr.glb and base_basic_shaded.glb: the PBR one becomes
    # <name>.glb, the others <name>_<variant>.glb.
    files = response.get("files") or [{"name": "", "url": url} for url in _urls(response)]
    files = sorted(files, key=lambda f: "pbr" not in f.get("name", ""))
    urls = [f["url"] for f in files]
    saved = []
    for item in files:
        content = requests.get(item["url"], timeout=300).content
        if content[:18] == b"Kaydara FBX Binary":
            target = RAW_DIR / f"{job['name']}{'' if not saved else '_' + str(len(saved))}.fbx"
            target.write_bytes(content)
            saved.append(target)
            job.setdefault("fbx_urls", []).append(item["url"])
            print(f"[download] {target.name}: {len(content) / 1e6:.1f} MB")
            continue
        if content[:4] != b"glTF":
            continue  # previews, textures and other files
        variant = Path(item.get("name", "")).stem.replace("base_basic_", "") or str(len(saved))
        target = RAW_DIR / (f"{job['name']}.glb" if not saved else f"{job['name']}_{variant}.glb")
        target.write_bytes(content)
        saved.append(target)
        print(f"[download] {target.name}: {len(content) / 1e6:.1f} MB")
    if not saved:
        raise SystemExit(f"No binary glTF or FBX among {len(urls)} result URLs: {json.dumps(response)[:500]}")
    job["result_response"], job["files"], job["downloaded_at"] = response, [str(p) for p in saved], _now()
    _save(job)
    return saved


def _slug(text: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", text.lower()).strip("_")[:40] or "asset"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    commands = parser.add_subparsers(dest="command", required=True)
    commands.add_parser("auth", help="show the signed-in account and credit balances (free)")
    gen = commands.add_parser("generate", help="text prompt -> .glb in assets/raw/")
    gen.add_argument("prompt")
    gen.add_argument("--name", help="file name (default: from the prompt)")
    gen.add_argument("--tier", choices=TIERS, help="default: the server's (Gen-2.5-Medium)")
    gen.add_argument("--quality", type=int, help="target polygon count (Raw 500-1,000,000)")
    gen.add_argument("--mesh-mode", choices=["Raw", "Quad"])
    gen.add_argument("--format", dest="fmt", choices=["glb", "fbx"], default="glb")
    split = commands.add_parser("bang", help="split a finished model into parts")
    split.add_argument("source", help="job name (e.g. chest) or generation id")
    split.add_argument("--instruction", help='e.g. "separate the lid from the body"; omit for automatic')
    split.add_argument("--strength", type=int, help="soft target part count, 1-12")
    split.add_argument("--name", help="file name (default: <source>_bang)")
    resume = commands.add_parser("resume", help="continue a recorded job")
    resume.add_argument("manifest", type=Path)
    for sub in (gen, split, resume):
        sub.add_argument("--timeout", type=float, default=15, help="minutes to wait (default 15)")
    args = parser.parse_args()

    if args.command == "auth":
        print(json.dumps(run("auth", "status"), indent=2))
        return 0
    if args.command == "generate":
        job = generate(args.prompt, args.name or _slug(args.prompt), args.tier, args.quality, args.mesh_mode, args.fmt)
    elif args.command == "bang":
        job = bang(args.source, args.instruction, args.strength, args.name)
    else:
        job = json.loads(args.manifest.read_text(encoding="utf-8"))
    wait(job, args.timeout)
    for path in download(job):
        print(f"[done] {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
