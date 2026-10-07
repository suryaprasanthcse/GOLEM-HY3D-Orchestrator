"""Tencent HY-3D-Component on TokenHub: split a generated model into parts, and log exactly what comes back.

  python hy3d_component_probe.py submit <name> --url <public FBX URL>   (billed: 30 credits per call)
  python hy3d_component_probe.py resume <name>                          (poll a recorded job; free)

The model's only input is an FBX at a public URL (Hyper3D's own download link works). The job is
recorded in runs/<name>/component/job.json the moment TokenHub accepts it, so a crash never pays twice;
`submit` refuses to run again for a name that already has a job. The script then polls
/v1/api/3d/query until the job completes or fails, downloads every result file, and logs its
structure: files, nodes, meshes, vertex and face counts, bounds, materials and textures. All the parts
are written into one glTF with one node per part (<name>_parts.glb), which is what golem_kinematics.py
reads. Signed result links are kept without their query strings in the logs.

Key: TENCENT_MAAS_API_KEY in the secrets file (golem_secrets.py); only TokenHub hosts get it.
"""

import argparse
import hashlib
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from golem_secrets import get_secret

ROOT = Path(__file__).resolve().parent
BASE = os.environ.get("GOLEM_TOKENHUB_BASE", "https://tokenhub-intl.tencentcloudmaas.com").rstrip("/")
MODEL = "hy-3d-component"
POLL_SECONDS, TIMEOUT_MINUTES = 10, 45


def now() -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())


def unsigned(url: str) -> str:
    parts = urlparse(url)
    return f"{parts.scheme}://{parts.netloc}{parts.path}" + (" (+signed query)" if parts.query else "")


def call(path: str, body: dict) -> dict:
    url = BASE + path
    if not re.search(r"\.tencentcloudmaas\.(com|tech)$", urlparse(url).hostname or ""):
        sys.exit(f"[component] refusing to send the key to {url}")
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {get_secret('TENCENT_MAAS_API_KEY')}", "Content-Type": "application/json"})
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=120) as response:
            status, text, headers = response.status, response.read().decode("utf-8"), response.headers
    except urllib.error.HTTPError as error:
        status, text, headers = error.code, error.read().decode("utf-8", "replace"), error.headers
    traced = {k: v for k, v in headers.items() if re.search(r"request|trace", k, re.I)}
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        data = {"raw": text[:1000]}
    return {"http": status, "ms": round((time.perf_counter() - started) * 1000), "headers": traced, "body": data,
            "at": now()}


def describe(path: Path) -> dict:
    """What a result file holds: nodes, meshes, sizes, materials and textures."""
    import trimesh
    info = {"file": path.name, "bytes": path.stat().st_size, "sha256": hashlib.sha256(path.read_bytes()).hexdigest()}
    try:
        scene = trimesh.load(path, force="scene")
    except Exception as error:  # an unreadable file is a finding, not a crash
        return {**info, "error": f"{type(error).__name__}: {error}"}
    meshes = []
    for node in sorted(scene.graph.nodes_geometry):
        transform, geometry = scene.graph[node]
        mesh = scene.geometry[geometry]
        material = getattr(mesh.visual, "material", None)
        meshes.append({"node": node, "vertices": len(mesh.vertices), "faces": len(mesh.faces),
                       "watertight": bool(mesh.is_watertight),
                       "bounds": [[round(float(v), 4) for v in row] for row in mesh.bounds],
                       "visual": type(mesh.visual).__name__,
                       "texture": bool(material is not None and (getattr(material, "baseColorTexture", None) is not None
                                                                 or getattr(material, "image", None) is not None))})
    lo, hi = scene.bounds
    return {**info, "nodes": len(meshes), "bounds": [[round(float(v), 4) for v in lo], [round(float(v), 4) for v in hi]],
            "meshes": meshes}


def combine(files: list[Path], target: Path) -> list[str]:
    """Every mesh of every result file as its own node in one glTF; returns the node names."""
    import trimesh
    out, names = trimesh.Scene(), []
    for index, path in enumerate(files):
        scene = trimesh.load(path, force="scene")
        for node in sorted(scene.graph.nodes_geometry):
            transform, geometry = scene.graph[node]
            mesh = scene.geometry[geometry].copy()
            mesh.apply_transform(transform)
            name = f"part_{len(names):02d}"
            out.add_geometry(mesh, node_name=name, geom_name=name)
            names.append(name)
    out.export(target)
    return names


def poll(name: str) -> int:
    folder = ROOT / "runs" / name / "component"
    job_path = folder / "job.json"
    job = json.loads(job_path.read_text(encoding="utf-8"))
    job_id = job["submit"]["body"].get("id")
    deadline = time.monotonic() + TIMEOUT_MINUTES * 60
    while True:
        result = call("/v1/api/3d/query", {"model": MODEL, "id": job_id})
        status = result["body"].get("status")
        job.setdefault("polls", 0)
        job["polls"] += 1
        job["last_query"] = result
        job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
        print(f"[component] {name}: {status} ({result['ms']} ms)", flush=True)
        if status in ("completed", "failed") or time.monotonic() > deadline:
            break
        time.sleep(POLL_SECONDS)
    if status != "completed":
        print(f"[component] {name}: {status}: {json.dumps(result['body'].get('error'))}")
        return 1
    files = []
    for index, item in enumerate(result["body"].get("data", [])):
        url = item.get("url", "")
        extension = item.get("type") or Path(urlparse(url).path).suffix.lstrip(".") or "bin"
        target = folder / f"{name}_result_{index:02d}.{extension}"
        target.write_bytes(urllib.request.urlopen(url, timeout=300).read())
        files.append(target)
    structure = [describe(f) for f in files]
    parts_path = folder / f"{name}_parts.glb"
    nodes = combine([f for f in files if f.suffix.lower() in (".glb", ".gltf")], parts_path) if files else []
    job.update(completed_at=now(), files=structure, parts_glb=parts_path.name, part_nodes=nodes,
               parts_sha256=hashlib.sha256(parts_path.read_bytes()).hexdigest() if nodes else None,
               result_links=[unsigned(i.get("url", "")) for i in result["body"].get("data", [])],
               seconds=round(time.time() - job["submitted_epoch"], 1))
    job["last_query"]["body"]["data"] = [{**i, "url": unsigned(i.get("url", ""))} for i in result["body"].get("data", [])]
    job_path.write_text(json.dumps(job, indent=2), encoding="utf-8")
    print(f"[component] {name}: {len(files)} file(s) in {job['seconds']} s -> {len(nodes)} part node(s)")
    for f in structure:
        print(f"  {f['file']}: {f.get('nodes', '?')} node(s), {f['bytes'] // 1024} KB"
              f"{', ERROR ' + f['error'] if 'error' in f else ''}")
        for m in f.get("meshes", [])[:40]:
            print(f"    {m['node']}: {m['vertices']} v, {m['faces']} f, watertight {m['watertight']}, "
                  f"texture {m['texture']}, bounds {m['bounds']}")
    return 0


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("command", choices=["submit", "resume"])
    parser.add_argument("name")
    parser.add_argument("--url", help="public FBX URL (submit)")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="backslashreplace")
    folder = ROOT / "runs" / args.name / "component"
    if args.command == "submit":
        if (folder / "job.json").exists():
            sys.exit(f"[component] {args.name} already has a job ({folder / 'job.json'}): resume it instead")
        if not args.url:
            sys.exit("[component] submit needs --url")
        folder.mkdir(parents=True, exist_ok=True)
        submitted = time.time()
        result = call("/v1/api/3d/submit", {"model": MODEL, "file": {"url": args.url}})
        job = {"name": args.name, "model": MODEL, "endpoint": BASE + "/v1/api/3d/submit", "input": unsigned(args.url),
               "submit": result, "submitted_epoch": submitted}
        (folder / "job.json").write_text(json.dumps(job, indent=2), encoding="utf-8")
        print(f"[component] {args.name}: HTTP {result['http']} in {result['ms']} ms: {json.dumps(result['body'])[:300]}")
        if result["http"] != 200 or not result["body"].get("id"):
            return 1
    return poll(args.name)


if __name__ == "__main__":
    sys.exit(main())
