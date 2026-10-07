"""The only model step in Blueprint A: for each part the sweep left open, keep it fixed or pick ONE of the
candidates the sweep passed, and tag its role and material. Nothing else: no numbers, no new candidates.

  python golem_tiebreak.py SWEEP.json --views DIR --prompt "<generation prompt>" --out decisions.json
                           [--model deepseek-v4.1-flash]

A vision model on Tencent TokenHub sees the model textured, and its parts flat-coloured by group, with
the list of parts and their passed candidates, at most CHUNK parts per question (asked about ten parts at
once, DeepSeek reasoned past its 32,000-token cap). Code checks the answer: every asked part answered exactly
once; role and material from fixed lists; the choice is "fixed" or one of that part's passed candidates;
the role agrees with the motion (a drawer slides, a door hinges). On an API error or a refused answer it
falls back to hy4-preview with the same text but no images, then to a written convention that reads the
generation prompt. The output says which one decided, and why the others were refused.
"""

import argparse
import base64
import json
import os
import re
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path
from urllib.parse import urlparse

import numpy as np

from golem_secrets import get_secret

BASE = os.environ.get("GOLEM_TOKENHUB_BASE", "https://tokenhub-intl.tencentcloudmaas.com").rstrip("/")
ROLES = {"door": "hinge", "lid": "hinge", "flap": "hinge", "drawer": "slide", "sliding_door": "slide",
         "fixed_panel": None, "handle": None, "decoration": None, "other": None}
MATERIALS = ["wood", "metal", "plastic", "glass", "stone", "fabric", "other"]
COLOURS = [("red", [0.9, 0.1, 0.1]), ("green", [0.1, 0.7, 0.1]), ("blue", [0.1, 0.3, 0.95]),
           ("yellow", [0.95, 0.85, 0.1]), ("magenta", [0.85, 0.1, 0.85]), ("cyan", [0.1, 0.85, 0.85]),
           ("orange", [1.0, 0.5, 0.0]), ("purple", [0.5, 0.2, 0.8]), ("brown", [0.55, 0.3, 0.1]),
           ("pink", [1.0, 0.6, 0.7]), ("lime", [0.6, 1.0, 0.2]), ("navy", [0.1, 0.1, 0.45])]
GREY = [0.55, 0.55, 0.55]
MAX_TOKENS = 32000  # both models reason first; 8,000 ran out before any answer
CHUNK = 3           # parts per question

SYSTEM = """You decide which parts of a 3D game prop move, for a physics engine. Code has already tested
every possible motion of every part for collisions; for each part listed you may only answer "fixed" or
ONE of the candidate ids listed for that part. Never invent a candidate, a number or an axis.
Images: (1) the prop as generated, front-left view; (2) the same view with each listed part in a flat
colour (grey is the main body, which never moves); (3) the coloured parts from the front; (4) from the
back-right. Directions are as seen standing in front of the prop.
Candidate ids: "hinge:<side>:<toward>" turns about the part's own edge on <side>, opening toward
<toward>; "hinge-arm:<side>:<toward>" is the same with the axis on the body's outer edge (for a thick
door set inside a frame); "slide:<direction>" slides that way. Each candidate shows how far it moves
freely (degrees, or scene units for slides).
For each listed part give: role (door, lid, flap, drawer, sliding_door, fixed_panel, handle, decoration,
other); material (wood, metal, plastic, glass, stone, fabric, other); choice ("fixed" or one candidate id;
doors, lids and flaps must hinge, drawers and sliding doors must slide, every other role is fixed); why
(what you see that decides it: handle position, hinges, seams, gaps). Also give the body's material.
Reply with one JSON object only:
{"body_material": "...", "parts": {"<part>": {"role": "...", "material": "...", "choice": "...", "why": "..."}}}"""


def colour_map(sweep: dict) -> tuple[dict, dict]:
    """{node: rgb} for the renders, and {group: colour name} for the prompt."""
    nodes, names, i = {}, {}, 0
    for g in sweep["groups"]:
        if g["anchor"] or g["decision"] == "fixed":
            rgb, name = GREY, "grey"
        else:
            name, rgb = COLOURS[i % len(COLOURS)]
            i += 1
        names[g["group"]] = name
        for n in g["nodes"]:
            nodes[n] = rgb
    return nodes, names


def open_parts(sweep: dict) -> list[dict]:
    return [g for g in sweep["groups"] if g["decision"] == "choose"]


def offered(group: dict) -> dict:
    """The passed candidates a part is offered: a hinge hung from the body's edge only when the same hinge on
    the part's own edge didn't pass (that is what the arm is for), so the model reads fewer near-duplicates."""
    passed = {c["id"]: c for c in group["candidates"] if c["pass"]}
    return {i: c for i, c in passed.items() if not (c.get("arm") and i.replace("hinge-arm:", "hinge:") in passed)}


def question(sweep: dict, names: dict, prompt: str, only: set | None = None) -> str:
    return json.dumps({
        "generation_prompt": prompt,
        "parts": [{"part": g["group"], "colour": names[g["group"]], "centre": g["centre"], "size": g["size"],
                   "touches": g["touches"],
                   "candidates": [{"id": c["id"], "free": c["free"], "unit": c["unit"]} for c in offered(g).values()]}
                  for g in open_parts(sweep) if only is None or g["group"] in only]})


def ask(model: str, text: str, images: list[Path]) -> dict:
    url = f"{BASE}/v1/chat/completions"
    if not re.search(r"\.tencentcloudmaas\.(com|tech)$", urlparse(url).hostname or ""):
        sys.exit(f"[tiebreak] refusing to send the key to {url}")
    content = [{"type": "text", "text": text}] + [
        {"type": "image_url", "image_url": {"url": "data:image/png;base64," + base64.b64encode(p.read_bytes()).decode()}}
        for p in images]
    body = {"model": model, "max_tokens": MAX_TOKENS, "response_format": {"type": "json_object"},
            "messages": [{"role": "system", "content": SYSTEM}, {"role": "user", "content": content if images else text}]}
    request = urllib.request.Request(url, data=json.dumps(body).encode("utf-8"), method="POST", headers={
        "Authorization": f"Bearer {get_secret('TENCENT_MAAS_API_KEY')}", "Content-Type": "application/json"})
    sent, started = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()), time.perf_counter()
    try:
        with urllib.request.urlopen(request, timeout=600) as response:
            status, raw, headers = response.status, response.read().decode("utf-8"), response.headers
    except urllib.error.HTTPError as error:
        status, raw, headers = error.code, error.read().decode("utf-8", "replace"), error.headers
    except (urllib.error.URLError, TimeoutError) as error:
        return {"model": model, "error": f"request failed: {error}", "sent_utc": sent}
    record = {"model": model, "http": status, "sent_utc": sent, "latency_ms": round((time.perf_counter() - started) * 1000),
              "images": [p.name for p in images],
              "response_headers": {k: v for k, v in headers.items() if re.search(r"request|trace", k, re.I)}}
    try:
        data = json.loads(raw)
        message = data["choices"][0]["message"]
        record.update(completion_id=data.get("id"), served_model=data.get("model"), usage=data.get("usage"),
                      finish_reason=data["choices"][0].get("finish_reason"), content=message.get("content") or "",
                      reasoning=message.get("reasoning_content"))
    except (json.JSONDecodeError, KeyError, IndexError, TypeError):
        record["error"] = f"HTTP {status}: {raw[:400]}"
    return record


def check(text: str, sweep: dict, only: set | None = None) -> tuple[dict | None, list[str]]:
    fenced = re.fullmatch(r"```(?:json)?\s*(.*?)\s*```", text.strip(), re.S)
    try:
        answer = json.loads(fenced.group(1) if fenced else text)
    except json.JSONDecodeError as error:
        return None, [f"not JSON: {error}"]
    if not isinstance(answer, dict) or not isinstance(answer.get("parts"), dict):
        return None, ['no "parts" object']
    problems, wanted = [], {g["group"]: g for g in open_parts(sweep) if only is None or g["group"] in only}
    if set(answer) - {"body_material", "parts"}:
        problems.append(f"unknown keys {sorted(set(answer) - {'body_material', 'parts'})}")
    if answer.get("body_material") not in MATERIALS:
        problems.append(f"body_material {answer.get('body_material')!r} is not one of {MATERIALS}")
    parts = answer["parts"]
    if set(parts) != set(wanted):
        problems.append(f"answered {sorted(parts)}, asked {sorted(wanted)}")
    for name, item in parts.items():
        if name not in wanted or not isinstance(item, dict):
            continue
        role, material, choice = item.get("role"), item.get("material"), item.get("choice")
        passed = offered(wanted[name])
        if role not in ROLES:
            problems.append(f"{name}: role {role!r} is not one of {sorted(ROLES)}")
            continue
        if material not in MATERIALS:
            problems.append(f"{name}: material {material!r} is not one of {MATERIALS}")
        if choice != "fixed" and choice not in passed:
            problems.append(f"{name}: choice {choice!r} is not 'fixed' or a passed candidate")
            continue
        motion = None if choice == "fixed" else passed[choice]["type"]
        if motion != ROLES[role]:
            problems.append(f"{name}: a {role} can't be {'fixed' if motion is None else 'a ' + motion}")
    if problems:
        return None, problems
    return {"body_material": answer["body_material"],
            "parts": {n: {k: (str(v)[:200] if k == "why" else v) for k, v in item.items()
                          if k in ("role", "material", "choice", "why")} for n, item in parts.items()}}, []


def convention(sweep: dict, prompt: str) -> dict:
    """The last resort, from the generation prompt's words; every decision is marked as convention."""
    words = prompt.lower()
    side = "left" if "handle on the right" in words else "right" if "handle on the left" in words else "left"
    parts = {g["group"]: {"role": "fixed_panel", "material": "other", "choice": "fixed",
                          "why": "convention: no rule moves it"} for g in open_parts(sweep)}
    passed = {g["group"]: [c["id"] for c in g["candidates"] if c["pass"]] for g in open_parts(sweep)}
    by_size = sorted(open_parts(sweep), key=lambda g: -np.prod(g["size"]))
    front = [g for g in by_size if g["centre"][2] > 0.75]  # on the front face (centre z, as a share of depth)
    top = [g for g in by_size if g["centre"][1] > 0.75]

    def take(group, choice, role):
        parts[group] = {"role": role, "material": "other", "choice": choice, "why": "convention from the prompt's words"}

    if "sliding door" in words:  # every front part that can slide sideways: toward the middle
        for g in front:
            option = "slide:right" if g["centre"][0] < 0.5 else "slide:left"
            if option in passed[g["group"]]:
                take(g["group"], option, "sliding_door")
    elif "drawer" in words:      # every front part that can slide out of the front
        for g in front:
            if "slide:front" in passed[g["group"]]:
                take(g["group"], "slide:front", "drawer")
    elif "lid" in words or "door" in words:  # the biggest top (lid) or front (door) part that can, only
        options = ["hinge:back:top", "hinge-arm:back:top"] if "lid" in words else \
                  [f"hinge:{side}:front", f"hinge-arm:{side}:front"]
        for g in (top if "lid" in words else front):
            option = next((o for o in options if o in passed[g["group"]]), None)
            if option:
                take(g["group"], option, "lid" if "lid" in words else "door")
                break
    return {"body_material": "other", "parts": parts}


def decide(sweep: dict, views: Path, prompt: str, model: str) -> dict:
    nodes, names = colour_map(sweep)
    if not open_parts(sweep):
        return {"decided_by": "geometry", "attempts": [], "decision": {"body_material": "other", "parts": {}}}
    images = [views / f for f in ("textured.png", "ids.png", "ids_front.png", "ids_back.png") if (views / f).exists()]
    groups = [g["group"] for g in open_parts(sweep)]
    # A few parts per question: one question about ten parts made DeepSeek reason past 32,000 tokens.
    chunks = [set(groups[i:i + CHUNK]) for i in range(0, len(groups), CHUNK)]
    attempts, parts, materials, deciders = [], {}, [], set()
    for number, chunk in enumerate(chunks, 1):
        text = question(sweep, names, prompt, chunk)
        decision, decider = None, "convention"
        for name, imgs in ((model, images), ("hy4-preview", [])):
            record = ask(name, text, imgs)
            if "error" in record:
                decision, problems = None, [record["error"]]
            elif record.get("finish_reason") == "length":
                decision, problems = None, [f"cut off at {MAX_TOKENS} output tokens before answering"]
            else:
                decision, problems = check(record.get("content", ""), sweep, chunk)
            record.update(chunk=sorted(chunk), problems=problems, accepted=decision is not None)
            attempts.append(record)
            print(f"[tiebreak] {name}, parts {sorted(chunk)} ({number}/{len(chunks)}): "
                  f"{'accepted' if decision else 'refused: ' + '; '.join(problems)} ({record.get('latency_ms', '-')} ms)",
                  flush=True)
            if decision:
                decider = name
                break
        if decision is None:
            decision = {"body_material": None, "parts": {g: p for g, p in convention(sweep, prompt)["parts"].items()
                                                            if g in chunk}}
        parts.update({g: {**p, "by": decider} for g, p in decision["parts"].items()})
        materials.append(decision["body_material"])
        deciders.add(decider)
    return {"decided_by": "+".join(sorted(deciders)), "chunk_size": CHUNK, "colours": names, "attempts": attempts,
            "decision": {"body_material": next((m for m in materials if m), "other"), "parts": parts}}


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("sweep", type=Path)
    parser.add_argument("--views", type=Path, required=True)
    parser.add_argument("--prompt", required=True)
    parser.add_argument("--out", type=Path, required=True)
    parser.add_argument("--model", default="deepseek-v4.1-flash")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="backslashreplace")
    result = decide(json.loads(args.sweep.read_text(encoding="utf-8")), args.views, args.prompt, args.model)
    args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result["decision"], indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
