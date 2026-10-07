"""Tier-1 adapter: turn a multi-part model (e.g. Hyper3D BANG output) into a GOLEM split.

The model chooses, the geometry computes: the caller names which parts stay fixed and
which group moves, on which side its hinge is and which way it opens (discrete choices,
made by a person or a vision model); this script computes the pivot and axis and writes
the same files as golem_cutter.py, so the Unity side treats both tiers alike.

  python golem_assemble.py assets/raw/vault_door_bang.glb --root root.5 \
      --move door=root.12,root.1,root.2:left:front:100

--move NAME=NODES:HINGE:OPENS[:LIMIT] (repeatable): a hinged part
  NODES   comma-separated node names that move together
  HINGE   side of the group the hinge is on: left, right, back, front, top, bottom
  OPENS   direction the free edge swings toward: front, back, up, down, left, right
  LIMIT   opening limit in degrees (default 100)
The pivot sits on the group's box at its extreme toward HINGE and toward OPENS (so the part
swings clear of what it closes against), centered along the hinge line.

--slide NAME=NODES:DIRECTION[:TRAVEL] (repeatable): a sliding part, e.g. a drawer
  DIRECTION  which way it pulls out: front, back, left, right, up, down
  TRAVEL     how far, in scene units (default: half the fixed body's depth along DIRECTION,
             since a drawer front is thin but the drawer runs back into the body)

--carve NEW=SOURCE:X0,X1,Y0,Y1,Z0,Z1 (repeatable, applied first): a new part made of SOURCE's
  loose pieces that lie wholly inside this box (glTF frame: +Y up, front +Z, scene units), cut
  out of SOURCE. For a part the multi-part model left fused into another: the filing cabinet has
  four drawer fronts, BANG split out three, and the fourth is loose pieces of the carcass.
  NEW can then be used in --root, --move or --slide like any other part.

--drawer-boxes: give every --slide part an open-top drawer box behind its front. Generated
  models draw drawers as front panels only, which float in mid-air when pulled out. The box is
  added after the joint is computed, so pivot, axis and travel still come from the front alone.
  It runs from the front's back face into the body, 1.25x the travel deep (so it stays in its
  runners when fully out; capped at 95% of the space behind the front), a little narrower and
  lower than the front, with walls 2% of its width thick. Its plain material takes the front's
  colour: the area-weighted median of the front's texture under its faces, so the metal
  handle doesn't tint it. In Unity the box joins the drawer's mesh, so its convex collider and
  its mass (mesh volume x density) cover the whole drawer.

--hinge-arms: for every --move part, set the hinge axis beside the frame instead of on the part,
  and build the hardware that hangs the part from it, as on a round bank-vault door. Pivoting on
  the part's own box corner fails for a thick door seated in its frame: the corner is where the
  front-most point (a central locking hub) meets the side, so it floats in front of the door's
  rim, and turning about it sweeps the door's back through the frame. The axis goes to the root
  body's extreme toward HINGE and toward OPENS (its outer front edge), centered on the part along
  the hinge line, so the door backs out through its own opening before it swings clear. A hinge
  barrel on that axis joins the body, and an arm (a bar in front of the frame's face and a foot down
  to the door's face) joins the part. Both are separate objects named golem_hinge_*, so Unity gives
  them colliders and mass with their body and the stage can tell them from the door.

Writes assets/split/<name>/<name>_split.glb and <name>_joints.json. Runs in headless Blender.
"""

import json
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BLENDER = os.environ.get("GOLEM_BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
# Directions in Blender's frame (Z up; glTF's front +Z imports as Blender -Y).
SIDES = {
    "left": (-1, 0, 0), "right": (1, 0, 0), "back": (0, 1, 0), "front": (0, -1, 0),
    "top": (0, 0, 1), "up": (0, 0, 1), "bottom": (0, 0, -1), "down": (0, 0, -1),
}
# Drawer boxes (--drawer-boxes), as fractions of the front panel unless noted.
DRAWER_WIDTH = 0.92            # box width
DRAWER_HEIGHT = 0.80           # box height
DRAWER_BOTTOM_GAP = 0.06       # gap under the box
DRAWER_WALL = 0.02             # wall thickness, as a fraction of the box width
DRAWER_DEPTH_PER_TRAVEL = 1.25  # box depth, as a multiple of the slide travel
DRAWER_DEPTH_FILL = 0.95       # at most this fraction of the space behind the front
# Hinge hardware (--hinge-arms), as fractions of the moving part's height along the hinge line unless noted.
HINGE_BARREL = 0.035           # barrel radius
HINGE_SPAN = 0.6               # barrel length
HINGE_ARM = 0.16               # arm height
HINGE_REACH = 0.25             # how far the arm reaches over the part, as a fraction of its width
HINGE_SEGMENTS = 24


def parse_args(argv):
    import argparse

    import numpy as np

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path)
    parser.add_argument("--root", required=True, help="comma-separated node names that stay fixed")
    parser.add_argument("--move", action="append", default=[], help="NAME=NODES:HINGE:OPENS[:LIMIT]")
    parser.add_argument("--slide", action="append", default=[], help="NAME=NODES:DIRECTION[:TRAVEL]")
    parser.add_argument("--carve", action="append", default=[], help="NEW=SOURCE:X0,X1,Y0,Y1,Z0,Z1")
    parser.add_argument("--drawer-boxes", action="store_true", help="build a drawer box behind every --slide part")
    parser.add_argument("--hinge-arms", action="store_true", help="hang every --move part from an axis beside the frame, with a barrel and an arm")
    parser.add_argument("--name", help="output name (default: input file name)")
    parser.add_argument("--out", type=Path, default=ROOT / "assets" / "split")
    args = parser.parse_args(argv)
    groups = []
    for spec in args.move:
        name, rest = spec.split("=", 1)
        fields = rest.split(":")
        if len(fields) not in (3, 4) or fields[1] not in SIDES or fields[2] not in SIDES:
            parser.error(f"bad --move {spec!r}; expected NAME=NODES:HINGE:OPENS[:LIMIT]")
        groups.append({"kind": "hinge", "name": name, "nodes": fields[0].split(","), "hinge": fields[1],
                       "opens": fields[2], "limit": float(fields[3]) if len(fields) == 4 else 100.0})
    for spec in args.slide:
        name, rest = spec.split("=", 1)
        fields = rest.split(":")
        if len(fields) not in (2, 3) or fields[1] not in SIDES:
            parser.error(f"bad --slide {spec!r}; expected NAME=NODES:DIRECTION[:TRAVEL]")
        groups.append({"kind": "slide", "name": name, "nodes": fields[0].split(","), "direction": fields[1],
                       "travel": float(fields[2]) if len(fields) == 3 else None})
    if not groups:
        parser.error("give at least one --move or --slide")
    args.groups = groups
    carves = []
    for spec in args.carve:
        try:
            name, rest = spec.split("=", 1)
            source, box = rest.split(":", 1)
            bounds = [float(v) for v in box.split(",")]
        except ValueError:
            bounds = []
        if len(bounds) != 6 or bounds[0] >= bounds[1] or bounds[2] >= bounds[3] or bounds[4] >= bounds[5]:
            parser.error(f"bad --carve {spec!r}; expected NEW=SOURCE:X0,X1,Y0,Y1,Z0,Z1 with each min below its max")
        carves.append({"name": name, "source": source, "box": np.array(bounds).reshape(3, 2)})
    args.carves = carves
    return args


def relaunch_in_blender() -> int:
    if not Path(BLENDER).exists():
        sys.exit(f"Blender not found at {BLENDER}. Set GOLEM_BLENDER to blender.exe.")
    args = parse_args(sys.argv[1:])
    if not args.input.exists():
        sys.exit(f"No such file: {args.input}")
    command = [BLENDER, "-b", "--factory-startup", "--python-exit-code", "1", "--python", __file__, "--", *sys.argv[1:]]
    result = subprocess.run(command, capture_output=True, text=True, encoding="utf-8", errors="replace")
    lines = (result.stdout + result.stderr).splitlines()
    print("\n".join(lines if result.returncode else [line for line in lines if line.startswith("[golem]")]))
    return result.returncode


def run_in_blender(args) -> None:
    import bmesh
    import bpy
    import numpy as np
    from mathutils import Matrix, Vector

    log = lambda message: print(f"[golem] {message}", flush=True)
    name = args.name or args.input.stem
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    meshes = {o.name: o for o in bpy.context.scene.objects if o.type == "MESH"}
    log(f"parts in {args.input.name}: {sorted(meshes)}")

    # Bake node transforms into the meshes so every part shares the scene frame.
    for obj in meshes.values():
        world = obj.matrix_world.copy()
        obj.parent = None
        obj.data = obj.data.copy()
        obj.data.transform(world)
        obj.matrix_world = Matrix.Identity(4)
    for obj in [o for o in bpy.context.scene.objects if o.type != "MESH"]:
        bpy.data.objects.remove(obj)

    def delete_vertices(obj, indices):
        bm = bmesh.new()
        bm.from_mesh(obj.data)
        bm.verts.ensure_lookup_table()
        bmesh.ops.delete(bm, geom=[bm.verts[i] for i in indices], context="VERTS")
        bm.to_mesh(obj.data)
        bm.free()

    for carve in args.carves:
        source = meshes.get(carve["source"])
        if source is None:
            sys.exit(f"[golem] --carve: unknown part {carve['source']!r}; available: {sorted(meshes)}")
        mesh = source.data
        co = np.empty(len(mesh.vertices) * 3)
        mesh.vertices.foreach_get("co", co)
        co = co.reshape(-1, 3)
        gltf = np.stack([co[:, 0], co[:, 2], -co[:, 1]], axis=1)  # Blender Z up -> glTF Y up
        box = carve["box"]
        inside = np.all((gltf >= box[:, 0]) & (gltf <= box[:, 1]), axis=1)
        # Loose pieces: vertices joined by edges (union-find). Take whole pieces only, so nothing is torn.
        edges = np.empty(len(mesh.edges) * 2, dtype=np.int64)
        mesh.edges.foreach_get("vertices", edges)
        parent = list(range(len(co)))

        def find(i):
            while parent[i] != i:
                parent[i] = parent[parent[i]]
                i = parent[i]
            return i

        for a, b in edges.reshape(-1, 2):
            ra, rb = find(a), find(b)
            if ra != rb:
                parent[ra] = rb
        piece = np.array([find(i) for i in range(len(co))])
        outside_pieces = set(piece[~inside])
        take = np.array([inside[i] and piece[i] not in outside_pieces for i in range(len(co))])
        if not take.any():
            sys.exit(f"[golem] --carve {carve['name']}: no loose piece of {carve['source']} lies wholly inside the box")
        carved = source.copy()
        carved.data = source.data.copy()
        carved.name = carved.data.name = carve["name"]
        for collection in source.users_collection:
            collection.objects.link(carved)
        delete_vertices(carved, np.flatnonzero(~take))
        delete_vertices(source, np.flatnonzero(take))
        meshes[carve["name"]] = carved
        log(f"{carve['name']}: carved {int(take.sum())} of {len(co)} vertices "
            f"({len(set(piece[take]))} loose pieces) out of {carve['source']}")

    def join(node_names, new_name):
        missing = [n for n in node_names if n not in meshes]
        if missing:
            sys.exit(f"[golem] unknown parts {missing}; available: {sorted(meshes)}")
        objects = [meshes[n] for n in node_names]
        if len(objects) > 1:
            with bpy.context.temp_override(active_object=objects[0], selected_editable_objects=objects, selected_objects=objects):
                bpy.ops.object.join()
        objects[0].name = objects[0].data.name = new_name
        return objects[0]

    def vertices(obj):
        co = np.empty(len(obj.data.vertices) * 3)
        obj.data.vertices.foreach_get("co", co)
        return co.reshape(-1, 3)

    def set_origin(obj, point):
        obj.data.transform(Matrix.Translation(Vector(-np.asarray(point))))
        obj.location = Vector(point)

    def to_gltf(v):
        """Blender (x, y, z), Z up -> glTF (x, z, -y), Y up (a proper rotation)."""
        return [round(float(v[0]), 6), round(float(v[2]), 6), round(float(-v[1]) + 0.0, 6)]

    def front_colour(part):
        """Linear RGB of a part's front: the area-weighted median of its base-colour texture
        sampled at each face's UV centre (a plain base colour if it has no texture)."""
        mesh, sources = part.data, {}

        def source(material):
            # The image feeding Base Color, searched upstream through any mix or multiply nodes.
            bsdf = next((n for n in material.node_tree.nodes if n.type == "BSDF_PRINCIPLED"), None) \
                if material.use_nodes and material.node_tree else None
            if bsdf is None:
                return None, np.array(material.diffuse_color[:3])
            nodes = [link.from_node for link in bsdf.inputs["Base Color"].links]
            while nodes:
                node = nodes.pop()
                if node.type == "TEX_IMAGE" and node.image and node.image.size[0] > 0:
                    image = node.image
                    width, height = image.size
                    pixels = np.empty(width * height * image.channels, np.float32)
                    image.pixels.foreach_get(pixels)
                    srgb = image.colorspace_settings.name == "sRGB"
                    return (pixels.reshape(height, width, image.channels), srgb), None
                nodes.extend(link.from_node for socket in node.inputs for link in socket.links)
            return None, np.array(bsdf.inputs["Base Color"].default_value[:3])

        uv = mesh.uv_layers.active
        colours, weights = [], []
        for poly in mesh.polygons:
            material = mesh.materials[poly.material_index] if mesh.materials else None
            if material is None:
                continue
            if material.name not in sources:
                sources[material.name] = source(material)
            image, plain = sources[material.name]
            if image is None or uv is None:
                colour = plain
            else:
                pixels, srgb = image
                centre = np.mean([uv.data[i].uv[:] for i in poly.loop_indices], axis=0) % 1.0
                row = min(int(centre[1] * pixels.shape[0]), pixels.shape[0] - 1)
                col = min(int(centre[0] * pixels.shape[1]), pixels.shape[1] - 1)
                colour = pixels[row, col, :3].astype(float)
                if srgb:  # Blender reports 8-bit textures as stored (sRGB); materials want linear
                    colour = np.where(colour <= 0.04045, colour / 12.92, ((colour + 0.055) / 1.055) ** 2.4)
            colours.append(colour)
            weights.append(poly.area)
        if not colours:
            return np.array([0.16, 0.09, 0.05])  # the cutter's interior brown
        colours, weights = np.array(colours), np.array(weights)
        median = []
        for channel in colours.T:
            order = np.argsort(channel)
            cumulative = np.cumsum(weights[order])
            median.append(channel[order][np.searchsorted(cumulative, cumulative[-1] / 2)])
        return np.array(median)

    def add_drawer_box(part, lo, hi, direction, travel, name):
        """An open-top box (bottom, two sides, back) behind a sliding front, added to the part's
        own mesh in a material of the front's colour. lo/hi: the front's bounds (scene frame).
        Returns the box's dimensions for the joints JSON, or None for a vertical slide."""
        axis = int(np.argmax(np.abs(direction)))
        if axis == 2:
            log(f"{name}: slides vertically, so no drawer box")
            return None
        lateral = 1 - axis  # the other horizontal axis
        into = -np.sign(direction[axis])  # from the front into the body
        back_face = hi[axis] if into > 0 else lo[axis]
        body_far = body_hi[axis] if into > 0 else body_lo[axis]
        size = hi - lo
        width, height = DRAWER_WIDTH * size[lateral], DRAWER_HEIGHT * size[2]
        wall = DRAWER_WALL * width
        depth = min(DRAWER_DEPTH_PER_TRAVEL * travel, DRAWER_DEPTH_FILL * abs(body_far - back_face))
        bottom = lo[2] + DRAWER_BOTTOM_GAP * size[2]
        start = back_face - into * wall / 2  # half a wall inside the front, so no seam shows
        middle = (lo[lateral] + hi[lateral]) / 2
        # Slabs as (lateral, vertical, depth-into-the-body) extents; they don't overlap, so the
        # mesh volume Unity sums for the mass counts each wall once.
        half = width / 2
        slabs = [
            ((-half, half), (bottom, bottom + wall), (0, depth)),                          # bottom
            ((-half, -half + wall), (bottom + wall, bottom + height), (0, depth - wall)),  # side
            ((half - wall, half), (bottom + wall, bottom + height), (0, depth - wall)),    # side
            ((-half, half), (bottom + wall, bottom + height), (depth - wall, depth)),      # back
        ]
        colour = front_colour(part)
        material = bpy.data.materials.new(f"golem_drawer_{name}")
        material.diffuse_color = (*colour, 1.0)
        if material.node_tree and (bsdf := material.node_tree.nodes.get("Principled BSDF")):
            bsdf.inputs["Base Color"].default_value = (*colour, 1.0)
            bsdf.inputs["Roughness"].default_value = 0.8
        part.data.materials.append(material)
        index = len(part.data.materials) - 1
        to_local = part.matrix_world.inverted()
        bm = bmesh.new()
        bm.from_mesh(part.data)
        for (u0, u1), (z0, z1), (w0, w1) in slabs:
            centre, extent = np.zeros(3), np.zeros(3)
            centre[lateral], extent[lateral] = middle + (u0 + u1) / 2, u1 - u0
            centre[2], extent[2] = (z0 + z1) / 2, z1 - z0
            centre[axis], extent[axis] = start + into * (w0 + w1) / 2, w1 - w0
            matrix = to_local @ Matrix.Translation(Vector(centre)) @ Matrix.Diagonal((*extent, 1.0))
            cube = bmesh.ops.create_cube(bm, size=1.0, matrix=matrix)["verts"]
            for face in {f for v in cube for f in v.link_faces}:
                face.material_index = index
        bm.to_mesh(part.data)
        bm.free()
        part.data.update()
        volume = sum((u1 - u0) * (z1 - z0) * (w1 - w0) for (u0, u1), (z0, z1), (w0, w1) in slabs)
        return {"depth": round(depth, 6), "width": round(width, 6), "height": round(height, 6),
                "wall": round(wall, 6), "volume": round(volume, 6), "colour": [round(float(c), 4) for c in colour]}

    def add_hinge_arm(part, co, lo, hi, pivot, hinge, opens, line, name):
        """A hinge barrel (on the body) and an arm (on the part) for an axis beside the frame. co/lo/hi:
        the part's vertices and bounds in the scene frame, as before its origin moved to the pivot.
        Returns the hardware's dimensions for the joints JSON."""
        h_ax, o_ax, l_ax = (int(np.argmax(np.abs(v))) for v in (hinge, opens, line))
        s_h, s_o = np.sign(hinge[h_ax]), np.sign(opens[o_ax])
        size = hi - lo
        height, width = size[l_ax], size[h_ax]
        radius, arm, reach = HINGE_BARREL * height, HINGE_ARM * height, HINGE_REACH * width
        edge = hi[h_ax] if s_h > 0 else lo[h_ax]  # the part's edge on the hinge side
        frame_front = pivot[o_ax]  # the axis sits on the frame's front face
        # The part's face under the arm: its front-most point in the arm's band, near the hinge edge.
        band = (np.abs(co[:, l_ax] - pivot[l_ax]) < arm / 2) & (s_h * (edge - co[:, h_ax]) < reach)
        face = s_o * (s_o * co[band, o_ax]).max() if band.any() else frame_front

        colour = front_colour(part)
        metal = bpy.data.materials.new(f"golem_hinge_{name}")
        metal.diffuse_color = (*colour, 1.0)
        if metal.node_tree and (bsdf := metal.node_tree.nodes.get("Principled BSDF")):
            bsdf.inputs["Base Color"].default_value = (*colour, 1.0)
            bsdf.inputs["Metallic"].default_value = 0.85
            bsdf.inputs["Roughness"].default_value = 0.35

        def extent(h0, h1, o0, o1):
            """A box from extents along the hinge and opening axes, the arm's height along the line."""
            a, b = np.zeros(3), np.zeros(3)
            a[h_ax], b[h_ax] = sorted((h0, h1))
            a[o_ax], b[o_ax] = sorted((o0, o1))
            a[l_ax], b[l_ax] = pivot[l_ax] - arm / 2, pivot[l_ax] + arm / 2
            return a, b

        def solid(object_name, boxes, barrel_length=0.0, owner=None):
            mesh = bpy.data.meshes.new(object_name)
            obj = bpy.data.objects.new(object_name, mesh)
            bpy.context.scene.collection.objects.link(obj)
            bm = bmesh.new()
            for a, b in boxes:
                if np.all(b - a > 0):
                    bmesh.ops.create_cube(bm, size=1.0, matrix=Matrix.Translation(Vector((a + b) / 2)) @ Matrix.Diagonal((*(b - a), 1.0)))
            if barrel_length > 0:
                turn = Vector((0, 0, 1)).rotation_difference(Vector(line)).to_matrix().to_4x4()
                bmesh.ops.create_cone(bm, cap_ends=True, segments=HINGE_SEGMENTS, radius1=radius, radius2=radius,
                                      depth=barrel_length, matrix=Matrix.Translation(Vector(pivot)) @ turn)
            bm.to_mesh(mesh)
            bm.free()
            mesh.materials.append(metal)
            set_origin(obj, pivot)
            bpy.context.view_layer.update()
            obj.parent = owner
            obj.matrix_parent_inverse = owner.matrix_world.inverted()
            return obj

        # The arm turns with the part: a bar in front of the frame's face from over the part to the
        # axis, and a foot from the bar down to the part's face (recessed doors sit behind the frame).
        bar = extent(edge - s_h * reach, pivot[h_ax], frame_front, frame_front + s_o * 2 * radius)
        foot = extent(edge - s_h * reach, edge - s_h * 0.4 * reach, face - s_o * 0.005 * height, frame_front)
        boxes = [bar] + ([foot] if s_o * (frame_front - face) > 0 else [])
        solid(f"golem_hinge_arm_{name}", boxes, owner=part)
        solid(f"golem_hinge_barrel_{name}", [], barrel_length=HINGE_SPAN * height, owner=body)
        return {"barrel_radius": round(float(radius), 6), "barrel_length": round(float(HINGE_SPAN * height), 6),
                "arm_height": round(float(arm), 6), "arm_reach": round(float(reach), 6),
                "door_face_behind_frame": round(float(s_o * (frame_front - face)), 6),
                "colour": [round(float(c), 4) for c in colour]}

    body = join(args.root.split(","), "body")
    co = vertices(body)
    body_size = co.max(axis=0) - co.min(axis=0)
    body_lo, body_hi = co.min(axis=0), co.max(axis=0)  # scene frame, for drawer boxes
    base = np.array([(co[:, 0].min() + co[:, 0].max()) / 2, (co[:, 1].min() + co[:, 1].max()) / 2, co[:, 2].min()])
    set_origin(body, base)
    bpy.context.view_layer.update()

    joints = []
    for group in args.groups:
        part = join(group["nodes"], group["name"])
        co = vertices(part)
        if group["kind"] == "slide":
            direction = np.array(SIDES[group["direction"]], float)
            lo, hi = co.min(axis=0), co.max(axis=0)
            travel = group["travel"] if group["travel"] is not None else 0.5 * float(body_size @ np.abs(direction))
            set_origin(part, (lo + hi) / 2)  # a slide has no pivot; anchor it at its middle
            bpy.context.view_layer.update()
            part.parent = body
            part.matrix_parent_inverse = body.matrix_world.inverted()
            joints.append({
                "type": "prismatic",
                "parent": "body",
                "child": group["name"],
                "pivot": to_gltf((lo + hi) / 2),
                "axis": to_gltf(direction),
                "limits_deg": [0.0, round(travel, 6)],  # a slide's limits are in scene units, not degrees
                "rest_deg": 0.0,
                "slides_toward": group["direction"],
                "outward": to_gltf(direction),  # positive travel moves the part this way
            })
            log(f"{group['name']}: nodes {group['nodes']}, slides {group['direction']} up to {travel:.3f}")
            if args.drawer_boxes and (box := add_drawer_box(part, lo, hi, direction, travel, group["name"])):
                joints[-1]["drawer_box"] = box  # scene units; built after the joint, which it doesn't change
                log(f"{group['name']}: drawer box {box['width']:.3f} x {box['height']:.3f} x {box['depth']:.3f} deep, "
                    f"wall {box['wall']:.4f}, colour {box['colour']}")
            continue
        hinge, opens = np.array(SIDES[group["hinge"]], float), np.array(SIDES[group["opens"]], float)
        if abs(hinge @ opens) > 0.5:
            sys.exit(f"[golem] {group['name']}: hinge side and opening direction must be perpendicular")
        line = np.cross(hinge, opens)  # direction of the hinge line
        lo, hi = co.min(axis=0), co.max(axis=0)
        # Extreme toward the hinge side and toward the opening side; centered along the hinge line.
        # With --hinge-arms, the extreme of the fixed body instead: an axis beside the frame (see above).
        side_lo, side_hi = (body_lo, body_hi) if args.hinge_arms else (lo, hi)
        pivot = np.where(hinge > 0, side_hi, side_lo) * np.abs(hinge) + np.where(opens > 0, side_hi, side_lo) * np.abs(opens) \
            + (lo + hi) / 2 * np.abs(line)
        axis = np.cross(-hinge, opens)  # positive rotation swings the free edge toward OPENS
        set_origin(part, pivot)
        bpy.context.view_layer.update()
        part.parent = body
        part.matrix_parent_inverse = body.matrix_world.inverted()
        joints.append({
            "type": "revolute",
            "parent": "body",
            "child": group["name"],
            "pivot": to_gltf(pivot),
            "axis": to_gltf(axis),
            "limits_deg": [0.0, group["limit"]],
            "rest_deg": 0.0,
            "hinge_side": group["hinge"],
            "opens_toward": group["opens"],
            "outward": to_gltf(hinge),
            "hinge_length": round(float((hi - lo) @ np.abs(line)), 6),
        })
        log(f"{group['name']}: nodes {group['nodes']}, hinge {group['hinge']}, opens {group['opens']}, "
            f"pivot {joints[-1]['pivot']} axis {joints[-1]['axis']} (glTF)")
        if args.hinge_arms:
            joints[-1]["hinge_arm"] = add_hinge_arm(part, co, lo, hi, pivot, hinge, opens, line, group["name"])
            log(f"{group['name']}: hinge arm {joints[-1]['hinge_arm']}")

    used = set(args.root.split(",")) | {n for g in args.groups for n in g["nodes"]}
    unused = sorted(set(meshes) - used)
    if unused:
        log(f"warning: parts not assigned (left out of the export): {unused}")

    out_dir = (args.out / name).resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    glb_path, json_path = out_dir / f"{name}_split.glb", out_dir / f"{name}_joints.json"
    keep = {body} | {bpy.data.objects[g["name"]] for g in args.groups} \
        | {o for o in bpy.context.scene.objects if o.name.startswith("golem_hinge_")}
    for obj in bpy.context.scene.objects:
        obj.select_set(obj in keep)
    bpy.ops.export_scene.gltf(filepath=str(glb_path), export_format="GLB", use_selection=True, export_yup=True)
    json_path.write_text(json.dumps({
        "asset": name,
        "input": str(args.input),
        "splitter": "golem_assemble (multi-part model, e.g. Hyper3D BANG)",
        "frame": "glTF (right-handed, +Y up, front +Z)",
        "parts": [{"node": "body", "role": "root", "from": args.root.split(",")}]
        + [{"node": g["name"], "role": "moving", "from": g["nodes"]} for g in args.groups],
        "joints": joints,
        "unassigned_parts": unused,
    }, indent=2), encoding="utf-8")
    log(f"wrote {glb_path}")
    log(f"wrote {json_path}")


if __name__ == "__main__":
    try:
        import bpy  # noqa: F401
    except ImportError:
        sys.exit(relaunch_in_blender())
    run_in_blender(parse_args(sys.argv[sys.argv.index("--") + 1 :]))
