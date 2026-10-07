"""Render a .glb in headless Blender: front, side and three-quarter views, with textures.

  python golem_preview.py assets/raw/chest.glb --marks 0.5 0.6 0.7
  python golem_preview.py assets/split/chest/chest_split.glb --open-deg 70

--marks draws labelled ticks at those fractions of the height in the front view's margins,
so a person or a vision model can say where the seam is. --open-deg swings the "lid" node
about its hinge from the matching *_joints.json, as a visual check of the joint.
Images go to assets/preview/<name>/.
"""

import json
import math
import os
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
BLENDER = os.environ.get("GOLEM_BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")


def parse_args(argv):
    import argparse

    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("input", type=Path)
    parser.add_argument("--marks", type=float, nargs="*", default=[], help="height fractions to mark (0-1)")
    parser.add_argument("--open-deg", type=float, default=0.0, help="open the lid by this many degrees")
    parser.add_argument("--size", type=int, default=900, help="image size in pixels")
    parser.add_argument("--out", type=Path, default=ROOT / "assets" / "preview")
    return parser.parse_args(argv)


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
    import bpy
    from mathutils import Quaternion, Vector

    log = lambda message: print(f"[golem] {message}", flush=True)
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=str(args.input.resolve()))
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH"]

    if args.open_deg:
        joints_path = args.input.with_name(args.input.name.replace("_split.glb", "_joints.json"))
        if not joints_path.exists():
            sys.exit(f"[golem] --open-deg needs {joints_path.name} next to the .glb")
        for joint in json.loads(joints_path.read_text())["joints"]:
            part = next((o for o in scene.objects if o.name.startswith(joint["child"])), None)
            if part is None:
                sys.exit(f"[golem] no node '{joint['child']}' in {args.input.name}")
            x, y, z = joint["axis"]
            axis = Vector((x, -z, y))  # glTF (x, y, z) -> Blender (x, -z, y)
            if joint["type"] == "prismatic":  # slides pull out to their full travel
                part.location += axis * joint["limits_deg"][1]
                log(f"{joint['child']} slid {joint['limits_deg'][1]:.3f} along {list(axis)}")
                continue
            part.rotation_mode = "QUATERNION"
            part.rotation_quaternion = Quaternion(axis, math.radians(args.open_deg)) @ part.rotation_quaternion
            log(f"{joint['child']} opened {args.open_deg} deg about {list(axis)}")
    bpy.context.view_layer.update()

    corners = [o.matrix_world @ Vector(c) for o in meshes for c in o.bound_box]
    lo = Vector([min(c[i] for c in corners) for i in range(3)])
    hi = Vector([max(c[i] for c in corners) for i in range(3)])
    center, extent = (lo + hi) / 2, hi - lo
    span = max(extent) * 1.35

    for fraction in args.marks:
        z = lo.z + fraction * extent.z
        for side in (-1, 1):  # short red ticks in both margins of the front view
            bpy.ops.mesh.primitive_cube_add(size=1, location=(center.x + side * (extent.x / 2 + span * 0.05), lo.y - 0.01, z))
            tick = bpy.context.active_object
            tick.scale = (span * 0.08, 0.001, span * 0.004)
            tick.color = (1, 0, 0, 1)
        text = bpy.data.objects.new(f"mark_{fraction}", bpy.data.curves.new(f"mark_{fraction}", type="FONT"))
        text.data.body, text.data.size, text.data.align_y = f"{fraction:.2f}", span * 0.035, "CENTER"
        text.location = (center.x - extent.x / 2 - span * 0.2, lo.y - 0.01, z)
        text.rotation_euler = (math.pi / 2, 0, 0)
        text.color = (1, 0, 0, 1)
        scene.collection.objects.link(text)

    scene.render.engine = "BLENDER_WORKBENCH"
    scene.display.shading.light = "STUDIO"
    scene.display.shading.color_type = "TEXTURE"
    scene.render.resolution_x = scene.render.resolution_y = args.size
    camera = bpy.data.objects.new("camera", bpy.data.cameras.new("camera"))
    scene.collection.objects.link(camera)
    scene.camera = camera

    out_dir = (args.out / args.input.stem.replace("_split", "")).resolve()  # Blender misreads relative render paths
    out_dir.mkdir(parents=True, exist_ok=True)
    suffix = f"_open{int(args.open_deg)}" if args.open_deg else ""
    # glTF's front (+Z) imports as Blender -Y; Blender cameras look along their local -Z.
    views = {
        "front": (center + Vector((0, -span * 2, 0)), "ORTHO"),
        "side": (center + Vector((span * 2, 0, 0)), "ORTHO"),
        "three_quarter": (center + Vector((-span * 1.1, -span * 1.6, span * 0.9)), "PERSP"),
    }
    for view, (location, kind) in views.items():
        camera.location = location
        camera.rotation_euler = (center - location).to_track_quat("-Z", "Y").to_euler()
        camera.data.type = kind
        camera.data.ortho_scale = span * (1.5 if args.marks and view == "front" else 1.15)
        camera.data.lens = 50
        path = out_dir / f"{view}{suffix}.png"
        scene.render.filepath = str(path)
        bpy.ops.render.render(write_still=True)
        log(f"wrote {path}")


if __name__ == "__main__":
    try:
        import bpy  # noqa: F401
    except ImportError:
        sys.exit(relaunch_in_blender())
    run_in_blender(parse_args(sys.argv[sys.argv.index("--") + 1 :]))
