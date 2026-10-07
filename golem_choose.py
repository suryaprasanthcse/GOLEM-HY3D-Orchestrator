"""GOLEM's choice step done by Tencent Hunyuan: build a part list from a multi-part model (each
part's box and the parts its surface touches), ask a Hunyuan model on Tencent TokenHub to label the
parts, check the answer, and write a choices file for golem_auto.py.

The model only picks words from fixed lists: a role per part, and a joint type and sides per moving
role. It never sets a number; golem_assemble.py computes every pivot, axis, limit and mass from the
geometry. Code also checks that every moving group holds together and is attached: its parts must
touch one another and at least one of them must touch the body. An API error, or an answer that
fails any check, falls back to the artist's choices file (--fallback), or stops when there is none.

  python golem_choose.py model.glb --out choices.json [--fallback artist.json] [--compare artist.json]
                         [--model hy4-preview] [--name NAME]

Writes the choices file (source: the model's name, or the fallback's own source) and, next to it,
<out>.run.json with the part list, the prompt, the raw reply, the token usage, the latency and the
check results. Key: TENCENT_MAAS_API_KEY in the secrets file (see golem_secrets.py). Region:
GOLEM_TOKENHUB_BASE (default Singapore); the key is only ever sent to TokenHub hosts.
"""

import argparse
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

from golem_auto import joint_for
from golem_secrets import get_secret

BASE = os.environ.get("GOLEM_TOKENHUB_BASE", "https://tokenhub-intl.tencentcloudmaas.com").rstrip("/")
# USD per million tokens (input, output): TokenHub's price list on 2026-10-06.
PRICES = {"hy4-preview": (0.834, 2.501), "hy3": (0.132, 0.528)}
MAX_OUTPUT_TOKENS = 16000  # caps one call's cost (hy4-preview: about 0.04 USD)
# Each side and the axis it lies on, in the frame the part list uses.
SIDES = {"left": "x", "right": "x", "bottom": "y", "top": "y", "back": "z", "front": "z"}
ROLE = re.compile(r"^(body|lid|door|drawer)(_[a-z0-9]+)*$")
ANSWER_KEYS = {"splitter", "parts", "joints", "unsure", "why"}
# Two parts touch when at least CONTACT_SHARE of either part's surface samples lie within
# CONTACT_REACH (a share of the model's diagonal) of the other part; the share threshold ignores grazes.
CONTACT_REACH = 0.005
CONTACT_SHARE = 0.01
CONTACT_SAMPLES = 20000  # surface samples per part, plus its vertices; fixed seed

SYSTEM = """You label the parts of a 3D prop so a physics engine can make it move.
Choose only from the allowed values. Never output numbers, coordinates, axes, angles or distances:
code computes all geometry.
Frame: x runs left to right, y bottom to top, z back to front, as seen standing in front of the prop.
Each part's centre and size are shares of the whole model (0 to 1). "touches" lists the parts its
surface touches.
- Every part id gets exactly one role. Fixed parts get "body" (one or more parts).
- Moving roles are lid, door or drawer, with a suffix when there are several (drawer_top,
  drawer_middle). Parts that move together share one role, and must be connected through parts of
  that role that touch. Every moving role must touch the body.
- Each moving role gets one joint: {"type":"hinge","hinge":SIDE,"opens":SIDE} or
  {"type":"slide","direction":SIDE}. hinge and opens must be on different axes.
- SIDE is one of: left, right, back, front, top, bottom.
- If the boxes don't settle a choice, still give your best choice and list that role in "unsure".
- "why" gives one short reason per moving role.
Reply with one JSON object only, in this form:
{"splitter":"parts","parts":{"<id>":"<role>"},"joints":{"<role>":{...}},"unsure":[],"why":{"<role>":"..."}}"""


def contacts(meshes: dict, reach: float) -> dict[str, list[str]]:
    """For each part, the parts its surface touches (see CONTACT_REACH and CONTACT_SHARE)."""
    import numpy as np
    import trimesh
    from scipy.spatial import cKDTree

    points = {n: np.vstack([m.vertices, trimesh.sample.sample_surface(m, CONTACT_SAMPLES, seed=0)[0]])
              for n, m in meshes.items()}
    trees = {n: cKDTree(p) for n, p in points.items()}
    touches = {n: [] for n in meshes}
    names = sorted(meshes)
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            (a_lo, a_hi), (b_lo, b_hi) = meshes[a].bounds, meshes[b].bounds
            if np.any(a_lo > b_hi + reach) or np.any(b_lo > a_hi + reach):
                continue  # the boxes are too far apart for the surfaces to touch
            shares = [np.mean(trees[y].query(points[x], distance_upper_bound=reach)[0] <= reach)
                      for x, y in ((a, b), (b, a))]
            if max(shares) >= CONTACT_SHARE:
                touches[a].append(b)
                touches[b].append(a)
    return touches


def part_list(model: Path, name: str) -> dict:
    """Each mesh node's box as shares of the whole model, in the glTF frame (+Y up, front +Z), and
    the parts it touches."""
    import numpy as np
    import trimesh  # only this step needs it

    scene = trimesh.load(model, force="scene")
    lo, hi = scene.bounds
    size = hi - lo
    meshes = {}
    for node in sorted(scene.graph.nodes_geometry):
        transform, geometry = scene.graph[node]
        meshes[node] = scene.geometry[geometry].copy()
        meshes[node].apply_transform(transform)
    touches = contacts(meshes, CONTACT_REACH * float(np.linalg.norm(size)))
    parts = []
    for node, mesh in meshes.items():
        part_lo, part_hi = mesh.bounds
        parts.append({"id": node,
                      "centre": [round(float(v), 2) for v in ((part_lo + part_hi) / 2 - lo) / size],
                      "size": [round(float(v), 2) for v in (part_hi - part_lo) / size],
                      "touches": touches[node]})
    return {"prop": name, "model_size": {k: round(float(v), 2) for k, v in zip("xyz", size)}, "parts": parts}


def joint_problems(role: str, joint) -> list[str]:
    if not isinstance(joint, dict):
        return [f"{role}: the joint must be an object"]
    if joint.get("type") == "hinge":
        fields = ("hinge", "opens")
    elif joint.get("type") == "slide":
        fields = ("direction",)
    else:
        return [f"{role}: joint type must be hinge or slide, not {joint.get('type')!r}"]
    if set(joint) != {"type", *fields}:
        return [f"{role}: a {joint['type']} joint has exactly the keys type, {', '.join(fields)}"]
    if not all(isinstance(joint[f], str) and joint[f] in SIDES for f in fields):
        return [f"{role}: sides must be one of {sorted(SIDES)}"]
    if joint["type"] == "hinge" and SIDES[joint["hinge"]] == SIDES[joint["opens"]]:
        return [f"{role}: hinge side and opening side must be on different axes"]
    return []


def group_problems(parts: dict, touches: dict) -> list[str]:
    """Each moving role's parts must be connected through touching parts of that role, and touch the body."""
    groups = {}
    for node, role in parts.items():
        groups.setdefault(role, set()).add(node)
    problems = []
    for role, members in sorted(groups.items()):
        if role == "body":
            continue
        reached, frontier = set(), [min(members)]
        while frontier:
            node = frontier.pop()
            if node not in reached:
                reached.add(node)
                frontier += [n for n in touches.get(node, []) if n in members]
        if reached != members:
            problems.append(f"{role}: {sorted(members - reached)} don't touch {sorted(reached)}")
        if not any(parts.get(n) == "body" for m in members for n in touches.get(m, [])):
            problems.append(f"{role}: none of its parts touches the body")
    return problems


def check(text: str, part_list_parts: list[dict]) -> tuple[dict | None, list[str]]:
    """The model's answer as choices, or None and every reason it was refused."""
    part_ids = [p["id"] for p in part_list_parts]
    touches = {p["id"]: p.get("touches", []) for p in part_list_parts}
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text.strip(), re.S)
    try:
        answer = json.loads(fenced.group(1) if fenced else text)
    except json.JSONDecodeError as error:
        return None, [f"not JSON: {error}"]
    if not isinstance(answer, dict):
        return None, ["not a JSON object"]
    problems = []
    if set(answer) - ANSWER_KEYS:
        problems.append(f"unknown keys {sorted(set(answer) - ANSWER_KEYS)}")
    if answer.get("splitter") != "parts":
        problems.append('splitter must be "parts"')
    parts, joints = answer.get("parts"), answer.get("joints")
    if not isinstance(parts, dict) or not isinstance(joints, dict):
        return None, problems + ['"parts" and "joints" must be objects']
    if set(part_ids) - set(parts):
        problems.append(f"parts with no role: {sorted(set(part_ids) - set(parts))}")
    if set(parts) - set(part_ids):
        problems.append(f"unknown part ids: {sorted(set(parts) - set(part_ids))}")
    roles = set()
    for node, role in parts.items():
        if isinstance(role, str) and ROLE.match(role):
            roles.add(role)
        else:
            problems.append(f"{node}: role {role!r} is not body, lid*, door* or drawer*")
    moving = roles - {"body"}
    if "body" not in roles:
        problems.append("no part is the body")
    if not moving:
        problems.append("no part moves")
    if set(joints) != moving:
        problems.append(f"joints for {sorted(joints)}, but the moving roles are {sorted(moving)}")
    for role, joint in joints.items():
        problems += joint_problems(role, joint)
    unsure, why = answer.get("unsure", []), answer.get("why", {})
    if not isinstance(unsure, list) or not all(isinstance(r, str) and r in moving for r in unsure):
        problems.append('"unsure" must list moving roles')
    if not isinstance(why, dict) or not all(isinstance(v, str) for v in why.values()):
        problems.append('"why" must map roles to text')
    if not problems:
        problems += group_problems(parts, touches)
    if problems:
        return None, problems
    return {"splitter": "parts", "parts": parts, "joints": joints, "unsure": unsure,
            "why": {role: reason[:200] for role, reason in why.items()}}, []


def ask(model_id: str, manifest: dict) -> dict:
    """One chat completion on TokenHub: HTTP status, latency and the raw response text."""
    url = f"{BASE}/v1/chat/completions"
    host = urlparse(url).hostname or ""
    if not re.search(r"\.tencentcloudmaas\.(com|tech)$", host):
        sys.exit(f"[golem] refusing to send the key to {host}: only TokenHub hosts get it")
    body = {"model": model_id, "max_tokens": MAX_OUTPUT_TOKENS, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM},
                         {"role": "user", "content": json.dumps(manifest)}]}
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {get_secret('TENCENT_MAAS_API_KEY')}", "Content-Type": "application/json"})
    sent = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    started = time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=300) as response:
            status, text, headers = response.status, response.read().decode("utf-8"), response.headers
    except urllib.error.HTTPError as error:
        status, text, headers = error.code, error.read().decode("utf-8", "replace"), error.headers
    except (urllib.error.URLError, TimeoutError) as error:
        return {"error": f"request failed: {error}", "latency_ms": round((time.perf_counter() - started) * 1000)}
    # Tencent's ids for this call (they can look them up in their own logs); cookies are left out.
    traced = {k: v for k, v in headers.items() if re.search(r"request|trace|id$", k, re.I) and "cookie" not in k.lower()}
    return {"status": status, "latency_ms": round((time.perf_counter() - started) * 1000), "text": text,
            "sent_utc": sent, "response_headers": traced}


def answer_from(result: dict) -> tuple[str | None, dict, list[str]]:
    """The reply's content and its record fields, or None and why there is no usable reply."""
    if "error" in result:
        return None, {}, [result["error"]]
    if result["status"] != 200:
        return None, {}, [f"HTTP {result['status']}: {result['text'][:300]}"]
    try:
        data = json.loads(result["text"])
        choice = data["choices"][0]
        content = choice["message"].get("content") or ""
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        return None, {}, ["the response is not a chat completion"]
    fields = {"completion_id": data.get("id"), "created": data.get("created"), "served_model": data.get("model"),
              "usage": data.get("usage", {}), "finish_reason": choice.get("finish_reason"),
              "reasoning": choice["message"].get("reasoning_content"), "content": content}
    if choice.get("finish_reason") == "length":
        return None, fields, [f"the reply was cut off at {MAX_OUTPUT_TOKENS} output tokens"]
    return content, fields, []


def agreement(choices: dict, artist: dict) -> list[str]:
    """How the choices compare with the artist's: body or moving per part, the moving groups, and
    each matching group's joint type and sides (limits and travel are code defaults, not compared)."""
    def groups(c):
        found = {}
        for node, role in c["parts"].items():
            found.setdefault(role, set()).add(node)
        return found

    def sides(joint):
        return tuple(joint.get(k) for k in ("type", "hinge", "opens", "direction"))

    mine, theirs = groups(choices), groups(artist)
    nodes = sorted(artist["parts"])
    same_kind = sum((choices["parts"].get(n) == "body") == (artist["parts"][n] == "body") for n in nodes)
    lines = [f"body or moving: {same_kind} of {len(nodes)} parts agree"]
    moving = [(role, members) for role, members in theirs.items() if role != "body"]
    found = joints_agree = 0
    for role, members in sorted(moving):
        match = next((r for r, m in mine.items() if m == members and r != "body"), None)
        if match is None:
            lines.append(f"  {role} {sorted(members)}: no group with exactly these parts")
            continue
        found += 1
        ok = sides(choices["joints"][match]) == sides(joint_for(role, artist.get("joints", {})))
        joints_agree += ok
        lines.append(f"  {role} {sorted(members)} = model's {match}: joint {'agrees' if ok else 'differs'}"
                     f" ({json.dumps(choices['joints'][match])})")
    lines.insert(1, f"moving groups: {found} of {len(moving)} found exactly; joints agree on {joints_agree} of {found}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("glb", type=Path, help="multi-part model (.glb), one mesh node per part")
    parser.add_argument("--out", type=Path, required=True, help="choices file to write (for golem_auto.py)")
    parser.add_argument("--fallback", type=Path, help="artist's choices file to use if the model's answer fails")
    parser.add_argument("--compare", type=Path, help="artist's choices file to score the answer against")
    parser.add_argument("--model", dest="model_id", default="hy4-preview", help="TokenHub model id (default hy4-preview)")
    parser.add_argument("--name", help="prop name (default: the model file's name)")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="backslashreplace")  # TokenHub errors carry Chinese text; Windows consoles may not
    if not args.glb.exists():
        sys.exit(f"[golem] no such model: {args.glb}")
    name = args.name or args.glb.stem
    manifest = part_list(args.glb, name)
    print(f"[golem] {name}: {len(manifest['parts'])} parts; asking {args.model_id} on {BASE}", flush=True)

    result = ask(args.model_id, manifest)
    content, fields, problems = answer_from(result)
    choices = None
    if content is not None:
        choices, problems = check(content, manifest["parts"])

    if choices is not None:
        out, outcome = {"source": args.model_id, "name": name, **choices}, "accepted"
    elif args.fallback:
        out = json.loads(args.fallback.read_text(encoding="utf-8"))
        out["fallback_reason"], outcome = problems, "fallback"
    else:
        out, outcome = None, "refused"

    args.out.parent.mkdir(parents=True, exist_ok=True)
    record_path = args.out.with_name(args.out.stem + ".run.json")
    record = {"model": args.model_id, "glb": str(args.glb), "endpoint": f"{BASE}/v1/chat/completions", "outcome": outcome,
              "problems": problems, "latency_ms": result.get("latency_ms"), "status": result.get("status"),
              "sent_utc": result.get("sent_utc"), "response_headers": result.get("response_headers"),
              **fields, "system": SYSTEM, "part_list": manifest}
    if "content" not in fields and "text" in result:
        record["response"] = result["text"][:2000]
    record_path.write_text(json.dumps(record, indent=2), encoding="utf-8")

    usage = fields.get("usage") or {}
    tokens_in, tokens_out = usage.get("prompt_tokens"), usage.get("completion_tokens")
    print(f"[golem] TokenHub latency: {result.get('latency_ms')} ms (HTTP {result.get('status', '-')})")
    if fields.get("completion_id"):
        print(f"[golem] TokenHub completion id: {fields['completion_id']} (model served: {fields.get('served_model')},"
              f" sent {result.get('sent_utc')})")
    if tokens_in is not None and tokens_out is not None:
        price = PRICES.get(args.model_id)
        cost = f", about {(tokens_in * price[0] + tokens_out * price[1]) / 1e6:.4f} USD at list price" if price else ""
        print(f"[golem] tokens: {tokens_in} in, {tokens_out} out{cost}")
    print(f"[golem] answer {outcome}" + (": " + "; ".join(problems) if problems else ""))
    print(f"[golem] run record: {record_path}")
    if out is None:
        return 1
    args.out.write_text(json.dumps(out, indent=2), encoding="utf-8")
    print(f"[golem] wrote {args.out}")
    print(json.dumps(out, indent=2))
    if args.compare and outcome == "accepted":
        print("[golem] against " + str(args.compare) + ":")
        print("\n".join(agreement(out, json.loads(args.compare.read_text(encoding="utf-8")))))
    return 0


if __name__ == "__main__":
    sys.exit(main())
