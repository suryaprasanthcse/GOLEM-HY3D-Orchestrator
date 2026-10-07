"""Tier-2 splitter: cut a boxy generated mesh into body + lid with a horizontal plane.

Run with normal Python; it relaunches itself inside headless Blender:
  python golem_cutter.py assets/raw/chest.glb
  python golem_cutter.py assets/raw/chest.glb --cut 0.72 --hinge back --open-deg 110

Writes assets/split/<name>/<name>_split.glb (root "body", child "lid" whose origin
sits on the hinge, so Unity rotates it about the right line) and <name>_joints.json
(the joint in glTF coordinates).

How the cut height is chosen (--cut auto, the default): slice the mesh at many
heights, measure the exact area of each cross-section, and look for a groove (an
area dip, lower than both sides) or a lip (a sudden step). Grooves rank first; if
neither is clear, cut at --fallback of the height. All candidates are written to
the JSON, so a model can choose among them instead.

The hinge sits on the seam itself: on the cut's outline, at its extreme on the
--hinge side (back = the asset's rear, glTF -Z), centered along the seam.
Generated meshes are solid, so the cut is capped flat: there is no hollow inside.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BLENDER = os.environ.get("GOLEM_BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
# Hinge side -> outward direction in Blender's frame (Z up; glTF's front +Z imports as Blender -Y).
HINGE_SIDES = {"back": (0.0, 1.0), "front": (0.0, -1.0), "right": (1.0, 0.0), "left": (-1.0, 0.0)}
STRAY_GAP = 0.03  # --open-part: loose pieces farther than this (fraction of the model's size) from the part go back to the body


def parse_args(argv):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path, help="input .glb")
    parser.add_argument("--cut", default="auto", help="'auto', or a fraction of the height (0-1) to cut at")
    parser.add_argument("--fallback", type=float, default=0.70, help="cut fraction when auto finds no seam")
    parser.add_argument("--band", type=float, nargs=2, default=(0.45, 0.92), help="height range searched for a seam")
    parser.add_argument("--hinge", choices=sorted(HINGE_SIDES), default="back")
    parser.add_argument("--open-deg", type=float, default=110.0, help="opening limit in degrees")
    parser.add_argument(
        "--open-part",
        action="store_true",
        help="the part above the cut is already open (e.g. a laptop screen): cut where the section "
        "area drops sharply, and let the joint close it as well as open it wider",
    )
    parser.add_argument("--out", type=Path, default=ROOT / "assets" / "split")
    return parser.parse_args(argv)


# --------------------------------------------------------------------------- outside Blender


def relaunch_in_blender() -> int:
    if not Path(BLENDER).exists():
        sys.exit(f"Blender not found at {BLENDER}. Set GOLEM_BLENDER to blender.exe.")
    args = parse_args(sys.argv[1:])  # validate before paying Blender's start-up time
    if not args.input.exists():
        sys.exit(f"No such file: {args.input}")
    command = [BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "--python", __file__, "--", *sys.argv[1:]]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    # Blender is chatty; show our lines, and everything if it failed.
    lines = (result.stdout + result.stderr).splitlines()
    shown = lines if result.returncode else [line for line in lines if line.startswith("[golem]")]
    print("\n".join(shown))
    return result.returncode


# --------------------------------------------------------------------------- inside Blender


def section_area(tri, h):
    """Exact area of the solid's horizontal cross-section at height h.

    Each triangle crossing h contributes one segment of the section outline. Orienting it
    by the triangle's outward normal makes the shoelace sum give the enclosed area, so a
    shallow gap running all round the asset shows up even when brackets or a hasp stick
    out further than the gap is deep (they would hide it from a bounding box).
    """
    import numpy as np

    z = tri[:, :, 2]
    tri = tri[(z.min(axis=1) < h) & (z.max(axis=1) > h)]
    if len(tri) == 0:
        return np.nan
    a, b = tri, tri[:, [1, 2, 0]]
    za, zb = a[:, :, 2], b[:, :, 2]
    crosses = (za - h) * (zb - h) < 0
    t = np.where(crosses, (h - za) / np.where(crosses, zb - za, 1), 0)
    points = a[:, :, :2] + t[..., None] * (b[:, :, :2] - a[:, :, :2])
    keep = crosses.sum(axis=1) == 2
    first_two = np.argsort(~crosses, axis=1, kind="stable")[:, :2]
    rows = np.arange(len(tri))
    p, q = points[rows, first_two[:, 0]][keep], points[rows, first_two[:, 1]][keep]
    normal = np.cross(tri[:, 1] - tri[:, 0], tri[:, 2] - tri[:, 0])[keep]
    # Counter-clockwise outline (seen from above) runs along (-n_y, n_x) for outward normal n.
    backwards = ((q - p) * np.stack([-normal[:, 1], normal[:, 0]], axis=1)).sum(axis=1) < 0
    p[backwards], q[backwards] = q[backwards], p[backwards].copy()
    return 0.5 * float((p[:, 0] * q[:, 1] - p[:, 1] * q[:, 0]).sum())


def seam_candidates(tri, z0, z1, band, samples=120):
    """Cross-section area profile over the height band, and the heights that look like a seam."""
    import numpy as np

    heights = z0 + (z1 - z0) * np.linspace(band[0], band[1], samples)
    areas = np.array([section_area(tri, h) for h in heights])
    areas[areas <= 0] = np.nan

    valid = np.where(np.isnan(areas), np.nanmedian(areas), areas)
    smooth = np.convolve(np.pad(valid, 1, mode="edge"), np.ones(3) / 3, mode="valid")
    window, candidates = max(2, samples // 12), []
    # A lid seam has real geometry on both sides. The legs of a carrying handle also dip,
    # but above them there is only a thin bar: require both sides to be at least half the
    # typical section.
    substantial = 0.5 * float(np.median(valid))
    for i in range(1, samples - 1):
        # A groove: a local dip in the section area.
        if smooth[i] <= smooth[i - 1] and smooth[i] <= smooth[i + 1]:
            # Lower than the sections on BOTH sides; a flat stretch next to a step is not a groove.
            left, right = smooth[max(0, i - window) : i].max(), smooth[i + 1 : i + window + 1].max()
            rim = min(left, right)
            depth = 1 - smooth[i] / rim if rim > 0 else 0
            if depth >= 0.03 and rim >= substantial:
                # A groove has a flat bottom: cut at the middle of it, not at its first sample.
                lo = hi = i
                while lo > 0 and smooth[lo - 1] <= smooth[i] * 1.005:
                    lo -= 1
                while hi < samples - 1 and smooth[hi + 1] <= smooth[i] * 1.005:
                    hi += 1
                middle = (heights[lo] + heights[hi]) / 2
                candidates.append({"kind": "groove", "fraction": float(np.interp(middle, [z0, z1], [0, 1])), "score": float(depth)})
        # A lip: the area jumps between the sections just below and just above.
        if 2 <= i < samples - 2 and smooth[i - 2] > 0 and smooth[i + 2] > 0:
            step = abs(np.log(smooth[i + 2] / smooth[i - 2]))
            neighbours = [abs(np.log(smooth[j + 2] / smooth[j - 2])) for j in (i - 1, i + 1) if 2 <= j < samples - 2]
            both_sides = min(smooth[i - 2], smooth[i + 2]) >= substantial
            if step >= np.log(1.05) and both_sides and all(step >= s for s in neighbours):
                candidates.append({"kind": "lip", "fraction": float(np.interp(heights[i], [z0, z1], [0, 1])), "score": float(step)})

    # Grooves rank first: they are the clearest seam signal, and the steps at a groove's
    # edges are side effects of the groove. Then merge near-duplicates (within 2% of the height).
    candidates.sort(key=lambda c: (c["kind"] != "groove", -c["score"]))
    merged = []
    for c in candidates:
        if all(abs(c["fraction"] - m["fraction"]) > 0.02 for m in merged):
            merged.append(c)
    profile = {"fractions": np.linspace(band[0], band[1], samples).round(4).tolist(), "section_area": np.round(valid, 6).tolist()}
    return merged[:5], profile


def run_in_blender(args) -> None:
    import bmesh
    import bpy
    import numpy as np
    from mathutils import Matrix, Vector

    log = lambda message: print(f"[golem] {message}", flush=True)
    name = args.input.stem

    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    meshes = [o for o in bpy.context.scene.objects if o.type == "MESH"]
    if not meshes:
        sys.exit(f"[golem] no meshes in {args.input}")

    # Bake every node transform into its mesh, then join everything into one object.
    for obj in meshes:
        world = obj.matrix_world.copy()
        obj.parent = None
        obj.data = obj.data.copy()
        obj.data.transform(world)
        obj.matrix_world = Matrix.Identity(4)
    for obj in [o for o in bpy.context.scene.objects if o.type != "MESH"]:
        bpy.data.objects.remove(obj)
    if len(meshes) > 1:
        with bpy.context.temp_override(active_object=meshes[0], selected_editable_objects=meshes, selected_objects=meshes):
            bpy.ops.object.join()
    source = meshes[0]
    mesh = source.data

    # glTF import splits vertices along UV and normal seams, which would leave the cut outline
    # in open pieces that can't be capped. Weld them: Blender keeps UVs per face corner, so
    # the texture survives.
    bm = bmesh.new()
    bm.from_mesh(mesh)
    size = max(source.dimensions)
    welded = len(bm.verts)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5 * size)
    welded -= len(bm.verts)
    bm.to_mesh(mesh)
    bm.free()

    co = np.empty(len(mesh.vertices) * 3)
    mesh.vertices.foreach_get("co", co)
    co = co.reshape(-1, 3)
    mesh.calc_loop_triangles()
    corners = np.empty(len(mesh.loop_triangles) * 3, dtype=np.int64)
    mesh.loop_triangles.foreach_get("vertices", corners)
    tri = co[corners.reshape(-1, 3)]
    z0, z1 = float(co[:, 2].min()), float(co[:, 2].max())
    log(f"input {args.input.name}: {len(mesh.vertices)} vertices ({welded} welded), {len(mesh.polygons)} faces, height {z1 - z0:.4f}")

    candidates, profile = seam_candidates(tri, z0, z1, args.band)
    if args.cut == "auto" and args.open_part:
        # Above a laptop's deck only the thin standing screen remains. The section first drops
        # below a fifth of the deck's, then keeps shrinking through the tapering keycaps, then
        # levels off at the screen alone: cut where it levels off, so no keycaps go with it.
        fractions = np.linspace(0.02, 0.6, 117)
        areas = np.array([section_area(tri, z0 + f * (z1 - z0)) for f in fractions])
        deck = np.nanmedian(areas[:15])
        small = np.flatnonzero(areas < 0.2 * deck)
        if len(small) == 0:
            sys.exit("[golem] --open-part: no sharp drop in section area between 2% and 60% of the height")
        i = small[0]
        while i + 1 < len(areas) and areas[i + 1] < 0.97 * areas[i]:
            i += 1
        fraction, method = float(fractions[i]), "auto: open part, where the section levels off above the base"
    elif args.cut == "auto":
        if candidates:
            fraction, method = candidates[0]["fraction"], f"auto: {candidates[0]['kind']} (score {candidates[0]['score']:.3f})"
        else:
            fraction, method = args.fallback, "auto: no clear seam, used --fallback"
    else:
        fraction, method = float(args.cut), "manual --cut"
    cut_z = z0 + fraction * (z1 - z0)
    log(f"cut at {fraction:.3f} of the height ({method}); {len(candidates)} seam candidates")

    # The caps have no UVs, so the asset's texture would smear across them. Give them a
    # plain dark interior material instead; it reads as the inside of the asset.
    interior = bpy.data.materials.new("golem_interior")
    interior.diffuse_color = (0.16, 0.09, 0.05, 1.0)
    if interior.node_tree and (bsdf := interior.node_tree.nodes.get("Principled BSDF")):
        bsdf.inputs["Base Color"].default_value = (0.16, 0.09, 0.05, 1.0)
        bsdf.inputs["Roughness"].default_value = 0.9
    mesh.materials.append(interior)
    interior_index = len(mesh.materials) - 1

    def return_strays(lid_mesh, body_mesh, seam, gap):
        """Move loose pieces of an open part that sit apart from it back to the body. A cut just above a
        laptop's deck also slices off whatever pokes above it (raised keys, bumps): those pieces would
        swing with the screen, and its convex collider, spanning them, hit the floor as it closed.
        The part is grown from its largest piece by every piece within `gap` of it; the rest goes back.
        Returns the seam without the strays' cut points (they'd skew where the hinge is centered)."""
        bm = bmesh.new()
        bm.from_mesh(lid_mesh)
        bm.verts.ensure_lookup_table()
        pieces, seen = [], set()
        for start in bm.verts:
            if start.index in seen:
                continue
            seen.add(start.index)
            stack, piece = [start], []
            while stack:
                v = stack.pop()
                piece.append(v.index)
                for e in v.link_edges:
                    w = e.other_vert(v)
                    if w.index not in seen:
                        seen.add(w.index)
                        stack.append(w)
            pieces.append(piece)
        coords = np.array([v.co[:] for v in bm.verts])
        boxes = [(coords[p].min(axis=0), coords[p].max(axis=0)) for p in pieces]
        main = {max(range(len(pieces)), key=lambda i: len(pieces[i]))}
        grown = True
        while grown:
            lo = np.min([boxes[i][0] for i in main], axis=0) - gap
            hi = np.max([boxes[i][1] for i in main], axis=0) + gap
            near = {i for i, (a, b) in enumerate(boxes) if np.all(b >= lo) and np.all(a <= hi)}
            grown = not near <= main
            main |= near
        strays = [i for i in range(len(pieces)) if i not in main]
        if not strays:
            bm.free()
            return seam
        stray_verts = {v for i in strays for v in pieces[i]}
        moved = bm.copy()
        moved.verts.ensure_lookup_table()
        bmesh.ops.delete(moved, geom=[v for v in moved.verts if v.index not in stray_verts], context="VERTS")
        stray_mesh = lid_mesh.copy()
        moved.to_mesh(stray_mesh)
        moved.free()
        bmesh.ops.delete(bm, geom=[bm.verts[i] for i in stray_verts], context="VERTS")
        bm.to_mesh(lid_mesh)
        bm.free()
        merged = bmesh.new()
        merged.from_mesh(body_mesh)
        merged.from_mesh(stray_mesh)  # appends: both halves share the original's material slots
        merged.to_mesh(body_mesh)
        merged.free()
        bpy.data.meshes.remove(stray_mesh)
        log(f"open part: {len(strays)} loose piece(s), {len(stray_verts)} vertices, sat apart from it and went back to the body")
        # Keep the cut points around the part itself (horizontally): the strays' outlines are elsewhere.
        inside = np.all((seam[:, :2] >= lo[:2]) & (seam[:, :2] <= hi[:2]), axis=1)
        return seam[inside] if inside.sum() >= 3 else seam

    def half(keep_above: bool):
        """One side of the plane, with the cut capped flat. Returns (mesh, seam vertices)."""
        bm = bmesh.new()
        bm.from_mesh(mesh)
        result = bmesh.ops.bisect_plane(
            bm,
            geom=bm.verts[:] + bm.edges[:] + bm.faces[:],
            plane_co=(0, 0, cut_z),
            plane_no=(0, 0, 1),
            clear_inner=keep_above,
            clear_outer=not keep_above,
        )
        cut_edges = [e for e in result["geom_cut"] if isinstance(e, bmesh.types.BMEdge) and e.is_valid]
        seam = np.array([v.co[:] for v in result["geom_cut"] if isinstance(v, bmesh.types.BMVert) and v.is_valid])
        filled = bmesh.ops.holes_fill(bm, edges=cut_edges, sides=0)["faces"]
        for face in filled:
            face.material_index = interior_index
        part = mesh.copy()
        bm.to_mesh(part)
        bm.free()
        return part, seam, len(filled)

    body_mesh, seam, body_caps = half(keep_above=False)
    lid_mesh, _, lid_caps = half(keep_above=True)
    if len(seam) < 3 or len(lid_mesh.polygons) == 0 or len(body_mesh.polygons) == 0:
        sys.exit(f"[golem] the cut at {fraction:.3f} missed the mesh (seam points: {len(seam)})")
    if args.open_part:
        seam = return_strays(lid_mesh, body_mesh, seam, STRAY_GAP * float(np.ptp(co, axis=0).max()))

    # Hinge on the seam: its extreme on the hinge side, centered along the seam. An open part (a
    # laptop screen) instead turns about the other extreme of its cut, its front face: it closes face
    # down onto the deck, so that edge is the one that stays put. Turning about the back of a 2 cm
    # screen swung its front edge 2 cm below the hinge, through a thin deck into the floor (and a
    # closed screen would sink its own thickness into the deck).
    out_dir = np.array([*HINGE_SIDES[args.hinge], 0.0])
    along = np.array([-out_dir[1], out_dir[0], 0.0])
    reach, spread = seam @ out_dir, seam @ along
    pivot = out_dir * (reach.min() if args.open_part else reach.max()) + along * (spread.min() + spread.max()) / 2
    pivot[2] = cut_z
    axis = np.cross([0.0, 0.0, 1.0], out_dir)  # positive rotation lifts the lid's far side

    limits, rest, open_angle = [0.0, args.open_deg], 0.0, None
    if args.open_part:
        # How far the part already stands open: the main direction of its cross-section
        # (across the hinge), measured up from "lying flat toward the front".
        part = np.array([v.co[:] for v in lid_mesh.vertices]) - pivot
        across = np.stack([part @ out_dir, part[:, 2]], axis=1)
        across -= across.mean(axis=0)
        direction = np.linalg.svd(across, full_matrices=False)[2][0]
        if direction[1] < 0:
            direction = -direction
        open_angle = float(np.degrees(np.arctan2(direction[1], -direction[0])))
        # Rest as generated (0); close down to almost flat, or open up to 135 degrees in total.
        limits = [-(open_angle - 2.0), max(0.0, 135.0 - open_angle)]
        log(f"open part stands at {open_angle:.1f} deg; joint limits {limits[0]:.1f} to {limits[1]:.1f}")

    base = np.array([(co[:, 0].min() + co[:, 0].max()) / 2, (co[:, 1].min() + co[:, 1].max()) / 2, z0])
    body_mesh.transform(Matrix.Translation(Vector(-base)))
    lid_mesh.transform(Matrix.Translation(Vector(-pivot)))
    body = bpy.data.objects.new("body", body_mesh)
    lid = bpy.data.objects.new("lid", lid_mesh)
    for obj in (body, lid):
        bpy.context.scene.collection.objects.link(obj)
    body.location, lid.location = Vector(base), Vector(pivot)
    bpy.context.view_layer.update()
    lid.parent = body
    lid.matrix_parent_inverse = body.matrix_world.inverted()
    bpy.data.objects.remove(source)

    def to_gltf(v):
        """Blender (x, y, z), Z up -> glTF (x, z, -y), Y up. A proper rotation, so axes map the same way."""
        return [round(float(v[0]), 6), round(float(v[2]), 6), round(float(-v[1]) + 0.0, 6)]

    joint = {
        "type": "revolute",
        "parent": "body",
        "child": "lid",
        "pivot": to_gltf(pivot),
        "axis": to_gltf(axis),
        "limits_deg": [round(limits[0], 2), round(limits[1], 2)],
        "rest_deg": rest,
        "hinge_side": args.hinge,
        "outward": to_gltf(out_dir),  # positive rotation moves the part toward this side
        "open_angle_deg": None if open_angle is None else round(open_angle, 2),
        "hinge_length": round(float(spread.max() - spread.min()), 6),
    }
    lid["golem_joint"] = json.dumps(joint)  # also travels in the glTF node's extras

    out_dir_path = args.out / name
    out_dir_path.mkdir(parents=True, exist_ok=True)
    glb_path, json_path = out_dir_path / f"{name}_split.glb", out_dir_path / f"{name}_joints.json"
    for obj in bpy.context.scene.objects:
        obj.select_set(obj in (body, lid))
    bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format="GLB", use_selection=True, export_yup=True, export_extras=True)

    record = {
        "asset": name,
        "input": str(args.input),
        "splitter": "golem_cutter (tier 2, horizontal plane)",
        "frame": "glTF (right-handed, +Y up, front +Z)",
        "cut": {"fraction": round(fraction, 4), "height": round(cut_z, 6), "method": method},
        "seam_candidates": candidates,
        "parts": [
            {"node": "body", "role": "root", "faces": len(body_mesh.polygons), "cap_faces": body_caps},
            {"node": "lid", "role": "moving", "faces": len(lid_mesh.polygons), "cap_faces": lid_caps},
        ],
        "joints": [joint],
        "warnings": ["solid interior: the cut is capped flat, there is no hollow inside"]
        + ([] if body_caps and lid_caps else ["the cut outline was not closed; a cap may be missing"]),
        "seam_profile": profile,
    }
    json_path.write_text(json.dumps(record, indent=2), encoding="utf-8")
    log(f"body {len(body_mesh.polygons)} faces, lid {len(lid_mesh.polygons)} faces, caps {body_caps}/{lid_caps}")
    log(f"hinge ({args.hinge}) pivot {joint['pivot']} axis {joint['axis']} (glTF)")
    log(f"wrote {glb_path}")
    log(f"wrote {json_path}")


if __name__ == "__main__":
    try:
        import bpy  # noqa: F401  (only importable inside Blender)
    except ImportError:
        sys.exit(relaunch_in_blender())
    run_in_blender(parse_args(sys.argv[sys.argv.index("--") + 1 :]))
