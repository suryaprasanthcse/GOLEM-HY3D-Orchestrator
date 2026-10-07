"""GOLEM Blueprint A: joint candidates from geometry and a geometric sweep test, with no model in the loop.

For a multi-part model (one mesh node per part, glTF frame: +Y up, front +Z, left -X):

  1. Groups. The largest part (by box volume) is the anchor. Parts other than the anchor that are bonded
     (BOND_SHARE of either one's surface within contact reach of the other) move together, like a door
     and its wheel. A small part (box diagonal under ATTACH_SHARE of a part it touches) is folded into the
     part it touches most, so handles and knobs move with their door.
  2. Candidates. Each other group gets GOLEM's whole vocabulary: 24 hinges (hinge side x opening side,
     perpendicular: an edge of the group's box, turning so the part first moves toward the opening side),
     the same 24 hung from the anchor's edge instead (golem_assemble.py --hinge-arms, as on a vault door
     seated in its frame), and 6 slides.
  3. Sweep. The group's surface samples move along each candidate in steps no longer than the reach
     (SWEEP_REACH of the model's diagonal), against every other group at rest and the floor. A sample hits
     when it comes within reach of another part AND at least half a reach closer than it was at rest, so
     seams and parts sliding alongside a neighbour don't count. The motion stops at the first step where
     more than HIT_SHARE of the samples hit or go below the floor; the range covered so far is the
     candidate's free range, in degrees or scene units.
  4. Feasible. A hinge must turn at least HINGE_MIN_DEG; a slide must move at least SLIDE_MIN_SHARE of
     its test length. A part can also only open toward an outer face of the anchor's box that it lies on
     (within FACE_SHARE), and only slide out of that face, or along it if it is a thin panel in it
     (THIN_SHARE, a sliding door): a panel at the back can't slide forward through the inside of a
     cabinet. A group with no feasible candidate is fixed. Geometry says how a group CAN move,
     never whether it SHOULD (a back panel may be free to fall off the back): every group with a feasible
     candidate goes to golem_tiebreak.py, which may only keep it fixed or pick one of its candidates.

  python golem_kinematics.py model.glb --out sweep.json
"""

import argparse
import hashlib
import json
import sys
import time
from pathlib import Path

import numpy as np

SIDES = {"left": (-1, 0, 0), "right": (1, 0, 0), "bottom": (0, -1, 0), "top": (0, 1, 0),
         "back": (0, 0, -1), "front": (0, 0, 1)}
CONTACT_REACH = 0.005   # touching at rest: within this share of the model's diagonal...
CONTACT_SHARE = 0.01    # ...for at least this share of either part's samples
BOND_SHARE = 0.1        # parts (not the anchor) this strongly in contact move together
ATTACH_SHARE = 0.35     # a part this much smaller than a part it touches moves with it
SWEEP_REACH = 0.01      # collision distance and largest step, as a share of the model's diagonal
HIT_SHARE = 0.002       # share of a group's samples that must hit for the motion to stop
HINGE_TEST_DEG = 120.0
HINGE_MIN_DEG = 75.0
SLIDE_TEST_SHARE = 0.6  # slides are tested over this share of the anchor's size along the direction...
SLIDE_MIN_SHARE = 0.5   # ...and must cover at least this share of that length
FACE_SHARE = 0.12       # a group lies on an outer face of the anchor's box within this share of its size
THIN_SHARE = 0.2        # a panel in a face is at most this share of the anchor's size thick
MOVING_SAMPLES = 3000
SEED = 0


def unit(side: str) -> np.ndarray:
    return np.array(SIDES[side], float)


def surface_points(mesh, count: int) -> np.ndarray:
    import trimesh
    points, _ = trimesh.sample.sample_surface(mesh, count, seed=SEED)
    return np.vstack([points, mesh.vertices[:: max(1, len(mesh.vertices) // count)]])


def static_density(mesh, reach: float) -> int:
    """Enough samples that every point of the surface is within about half the reach of one."""
    return int(np.clip(mesh.area / (reach / 2) ** 2, 5000, 150000))


def load_parts(model: Path) -> tuple[dict, np.ndarray, np.ndarray]:
    import trimesh
    scene = trimesh.load(model, force="scene")
    meshes = {}
    for node in sorted(scene.graph.nodes_geometry):
        transform, geometry = scene.graph[node]
        mesh = scene.geometry[geometry].copy()
        mesh.apply_transform(transform)
        meshes[node] = mesh
    lo, hi = scene.bounds
    return meshes, lo, hi


def contact_shares(meshes: dict, reach: float) -> dict[tuple, float]:
    """For each pair of parts that touch at rest, the larger of the two shares of samples within reach."""
    from scipy.spatial import cKDTree
    points = {n: surface_points(m, 20000) for n, m in meshes.items()}
    trees = {n: cKDTree(p) for n, p in points.items()}
    names, found = sorted(meshes), {}
    for i, a in enumerate(names):
        for b in names[i + 1:]:
            (a_lo, a_hi), (b_lo, b_hi) = meshes[a].bounds, meshes[b].bounds
            if np.any(a_lo > b_hi + reach) or np.any(b_lo > a_hi + reach):
                continue
            share = max(float(np.mean(trees[y].query(points[x], distance_upper_bound=reach)[0] <= reach))
                        for x, y in ((a, b), (b, a)))
            if share >= CONTACT_SHARE:
                found[(a, b)] = found[(b, a)] = round(share, 4)
    return found


def group_parts(meshes: dict, touching: dict) -> tuple[str, dict[str, str]]:
    """The anchor, and each part's group (named after the group's biggest part)."""
    volume = {n: float(np.prod(m.bounds[1] - m.bounds[0])) for n, m in meshes.items()}
    diagonal = {n: float(np.linalg.norm(m.bounds[1] - m.bounds[0])) for n, m in meshes.items()}
    anchor = max(meshes, key=lambda n: volume[n])
    owner = {n: n for n in meshes}

    def root(n):
        while owner[n] != n:
            n = owner[n]
        return n

    def join(a, b):
        a, b = root(a), root(b)
        if a != b:
            # The anchor always stays a root; otherwise the bigger part names the group.
            if a == anchor or (b != anchor and volume[a] > volume[b]):
                a, b = b, a
            owner[a] = b

    for (a, b), share in sorted(touching.items()):
        if anchor not in (a, b) and share >= BOND_SHARE:
            join(a, b)
    # Smallest first, so a knob on a handle on a door ends up with the door.
    for part in sorted(meshes, key=lambda n: diagonal[n]):
        if part == anchor or root(part) != part:
            continue
        bigger = [(touching[(part, other)], other) for other in meshes
                  if (part, other) in touching and diagonal[part] < ATTACH_SHARE * diagonal[other]]
        if bigger:
            join(part, max(bigger)[1])
    return anchor, {n: root(n) for n in meshes}


def candidates() -> list[dict]:
    found = []
    for arm in (False, True):
        for hinge, h in SIDES.items():
            for opens, o in SIDES.items():
                if abs(np.dot(h, o)) < 0.5:
                    found.append({"id": f"hinge{'-arm' if arm else ''}:{hinge}:{opens}", "type": "hinge",
                                  "hinge": hinge, "opens": opens, "arm": arm})
    found += [{"id": f"slide:{d}", "type": "slide", "direction": d} for d in SIDES]
    return found


def rotate(points: np.ndarray, pivot: np.ndarray, axis: np.ndarray, angle: float) -> np.ndarray:
    """Rodrigues' rotation of points about the line through pivot along axis (unit), by angle (rad)."""
    v = points - pivot
    cos, sin = np.cos(angle), np.sin(angle)
    return pivot + v * cos + np.cross(axis, v) * sin + np.outer(v @ axis, axis) * (1 - cos)


def hinge_line(lo: np.ndarray, hi: np.ndarray, hinge: str, opens: str) -> tuple[np.ndarray, np.ndarray]:
    """GOLEM's hinge: the edge of the box toward the hinge side and the opening side, centred along the
    third axis; it turns so the part first moves toward the opening side (axis = opens x hinge)."""
    h, o = unit(hinge), unit(opens)
    pivot = (lo + hi) / 2
    for direction in (h, o):
        k = int(np.argmax(np.abs(direction)))
        pivot[k] = hi[k] if direction[k] > 0 else lo[k]
    return pivot, np.cross(o, h)


def sweep(moving: np.ndarray, tree, owners: np.ndarray, names: list, floor: float, reach: float,
          candidate: dict, lo: np.ndarray, hi: np.ndarray, test_length: float,
          anchor_lo: np.ndarray, anchor_hi: np.ndarray) -> dict:
    rest, _ = tree.query(moving, distance_upper_bound=2 * reach)
    limit = np.minimum(reach, rest - reach / 2)  # a hit: within reach AND half a reach closer than at rest
    keep = limit > 0                              # samples pressed against a neighbour at rest can't hit
    points, limit = moving[keep], limit[keep]
    if candidate["type"] == "hinge":
        if candidate["arm"]:
            # golem_assemble.py --hinge-arms: the axis on the anchor's edge, centred on the part.
            pivot, axis = hinge_line(anchor_lo, anchor_hi, candidate["hinge"], candidate["opens"])
            third = [k for k in range(3) if not (unit(candidate["hinge"])[k] or unit(candidate["opens"])[k])][0]
            pivot[third] = (lo[third] + hi[third]) / 2
        else:
            pivot, axis = hinge_line(lo, hi, candidate["hinge"], candidate["opens"])
        radius = np.linalg.norm(np.cross(points - pivot, axis), axis=1).max(initial=reach)
        total, step = np.radians(HINGE_TEST_DEG), reach / radius
        place = lambda amount: rotate(points, pivot, axis, amount)
    else:
        direction = unit(candidate["direction"])
        total, step = test_length, reach
        place = lambda amount: points + direction * amount
    steps = max(1, int(np.ceil(total / step)))
    free, stopped_by = total, None
    for i in range(1, steps + 1):
        moved = place(total * i / steps)
        hit_distance, hit_index = tree.query(moved, distance_upper_bound=reach)
        hits = hit_distance < limit
        below = moved[:, 1] < floor - reach
        if hits.sum() + below.sum() > HIT_SHARE * len(moving):
            free = total * (i - 1) / steps
            if below.sum() > hits.sum():
                stopped_by = "floor"
            else:
                labels, counts = np.unique(owners[hit_index[hits]], return_counts=True)
                stopped_by = names[labels[np.argmax(counts)]]
            break
    if candidate["type"] == "hinge":
        free_value, tested, minimum, unit_name = np.degrees(free), HINGE_TEST_DEG, HINGE_MIN_DEG, "deg"
    else:
        free_value, tested, minimum, unit_name = free, test_length, SLIDE_MIN_SHARE * test_length, "scene units"
    return {**candidate, "free": round(float(free_value), 4), "tested_to": round(float(tested), 4),
            "unit": unit_name, "pass": bool(free_value >= minimum - 1e-9), "stopped_by": stopped_by,
            "samples": int(len(points))}


def analyse(model: Path) -> dict:
    import trimesh
    from scipy.spatial import cKDTree
    started = time.perf_counter()
    meshes, lo, hi = load_parts(model)
    diagonal = float(np.linalg.norm(hi - lo))
    touching = contact_shares(meshes, CONTACT_REACH * diagonal)
    anchor, group_of = group_parts(meshes, touching)
    groups = sorted(set(group_of.values()), key=lambda g: (g != anchor, g))
    reach = SWEEP_REACH * diagonal
    merged = {g: trimesh.util.concatenate([meshes[n] for n in meshes if group_of[n] == g]) for g in groups}
    samples = {g: surface_points(merged[g], static_density(merged[g], reach)) for g in groups}
    anchor_lo, anchor_hi = merged[anchor].bounds
    anchor_size = anchor_hi - anchor_lo

    results = []
    for g in groups:
        g_lo, g_hi = merged[g].bounds
        entry = {"group": g, "nodes": sorted(n for n in meshes if group_of[n] == g), "anchor": g == anchor,
                 "centre": [round(float(v), 4) for v in ((g_lo + g_hi) / 2 - lo) / (hi - lo)],
                 "size": [round(float(v), 4) for v in (g_hi - g_lo) / (hi - lo)],
                 "touches": sorted({group_of[b] for (a, b) in touching if group_of[a] == g and group_of[b] != g})}
        if g == anchor:
            entry.update(decision="fixed", reason="largest part: the anchor", candidates=[], feasible=[])
            results.append(entry)
            continue
        others = [o for o in groups if o != g]
        tree = cKDTree(np.vstack([samples[o] for o in others]))
        owners = np.concatenate([np.full(len(samples[o]), i) for i, o in enumerate(others)])
        moving = surface_points(merged[g], MOVING_SAMPLES)
        tested = []
        for candidate in candidates():
            along = np.abs(unit(candidate.get("direction", "front")))
            length = max(SLIDE_TEST_SHARE * float(along @ anchor_size), float(along @ (g_hi - g_lo)))
            tested.append(sweep(moving, tree, owners, others, float(lo[1]), reach, candidate, g_lo, g_hi,
                                length, anchor_lo, anchor_hi))
        faces = [s for s, d in SIDES.items()
                 if abs(float(unit(s) @ ((g_hi if sum(d) > 0 else g_lo) - (anchor_hi if sum(d) > 0 else anchor_lo))))
                 <= FACE_SHARE * float(np.abs(unit(s)) @ anchor_size)]
        # Sliding along a face (a sliding door) only for a thin panel in that face.
        panels = [f for f in faces if float(np.abs(unit(f)) @ (g_hi - g_lo)) <= THIN_SHARE * float(np.abs(unit(f)) @ anchor_size)]
        for c in tested:
            toward = c["opens"] if c["type"] == "hinge" else c["direction"]
            along = c["type"] == "slide" and any(abs(unit(f) @ unit(toward)) < 0.5 for f in panels)
            if c["pass"] and toward not in faces and not along:
                c["pass"], c["stopped_by"] = False, f"not on the {toward} face (on {faces or 'no outer face'})"
        entry["faces"] = faces
        feasible = [c["id"] for c in tested if c["pass"]]
        entry.update(candidates=tested, feasible=feasible, decision="choose" if feasible else "fixed",
                     reason=f"{len(feasible)} feasible candidate(s): fixed, or one of them" if feasible
                     else "no candidate moves far enough")
        results.append(entry)
    return {"model": str(model), "sha256": hashlib.sha256(model.read_bytes()).hexdigest(),
            "frame": "glTF: +Y up, front +Z, left -X",
            "constants": {"contact_reach": CONTACT_REACH, "contact_share": CONTACT_SHARE, "bond_share": BOND_SHARE,
                          "attach_share": ATTACH_SHARE, "sweep_reach": SWEEP_REACH, "hit_share": HIT_SHARE,
                          "hinge_test_deg": HINGE_TEST_DEG, "hinge_min_deg": HINGE_MIN_DEG,
                          "slide_test_share": SLIDE_TEST_SHARE, "slide_min_share": SLIDE_MIN_SHARE,
                          "face_share": FACE_SHARE, "thin_share": THIN_SHARE,
                          "moving_samples": MOVING_SAMPLES, "seed": SEED},
            "model_size": [round(float(v), 4) for v in hi - lo], "reach": round(reach, 5),
            "anchor": anchor, "groups": results, "seconds": round(time.perf_counter() - started, 2)}


def summary(result: dict) -> list[str]:
    lines = [f"{Path(result['model']).name}: {len(result['groups'])} groups, anchor {result['anchor']}, "
             f"{result['seconds']} s"]
    for g in result["groups"]:
        if g["anchor"]:
            lines.append(f"  {g['group']:<12} anchor {g['nodes']}")
            continue
        passed = [c for c in g["candidates"] if c["pass"]]
        shown = ", ".join(f"{c['id']} {c['free']:.2f}" for c in passed[:6])
        lines.append(f"  {g['group']:<12} {g['decision']:<6} {len(passed)}/{len(g['candidates'])}"
                     f"{': ' + shown if shown else ''}{' ...' if len(passed) > 6 else ''}  nodes {g['nodes']}")
    return lines


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", type=Path)
    parser.add_argument("--out", type=Path)
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="backslashreplace")
    result = analyse(args.model)
    if args.out:
        args.out.parent.mkdir(parents=True, exist_ok=True)
        args.out.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print("\n".join(summary(result)))
    return 0


if __name__ == "__main__":
    sys.exit(main())
