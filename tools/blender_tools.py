"""Headless Blender helpers for the orchestrator (run by golem_tiebreak.py and run_orchestrator.py):

  blender -b --python tools/blender_tools.py -- fbx2glb IN.fbx OUT.glb
  blender -b --python tools/blender_tools.py -- render PARTS.glb COLOURS.json OUT_DIR

render writes four views: textured.png (the model as generated, from the front-left), and ids.png,
ids_front.png and ids_back.png (front-left, front and back-right, every part flat-coloured by its group
from COLOURS.json: {node: [r, g, b]}), so a vision model can say which part is which by colour.
"""

import json
import sys
from pathlib import Path

import bpy
from mathutils import Vector

SIZE = 768


def clear():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def fbx2glb(source: str, target: str):
    clear()
    bpy.ops.import_scene.fbx(filepath=source)
    bpy.ops.export_scene.gltf(filepath=target, export_format="GLB")
    print(f"[blender] {source} -> {target}")


def bounds(objects):
    corners = [o.matrix_world @ Vector(c) for o in objects for c in o.bound_box]
    lo = Vector((min(c.x for c in corners), min(c.y for c in corners), min(c.z for c in corners)))
    hi = Vector((max(c.x for c in corners), max(c.y for c in corners), max(c.z for c in corners)))
    return lo, hi


def camera_at(scene, position: Vector, target: Vector):
    data = bpy.data.cameras.new("view")
    data.lens = 45
    camera = bpy.data.objects.new("view", data)
    scene.collection.objects.link(camera)
    camera.location = position
    camera.rotation_euler = (target - position).to_track_quat("-Z", "Y").to_euler()
    scene.camera = camera
    return camera


def render(parts: str, colours_path: str, out_dir: str):
    clear()
    bpy.ops.import_scene.gltf(filepath=parts)
    scene = bpy.context.scene
    meshes = [o for o in scene.objects if o.type == "MESH"]
    lo, hi = bounds(meshes)
    centre, size = (lo + hi) / 2, max((hi - lo).x, (hi - lo).y, (hi - lo).z)
    scene.render.engine = "BLENDER_WORKBENCH"
    scene.render.resolution_x = scene.render.resolution_y = SIZE
    scene.render.film_transparent = False
    scene.view_settings.view_transform = "Standard"  # flat ID colours come out as given
    scene.world = bpy.data.worlds.new("plain")
    shading = scene.display.shading
    out = Path(out_dir).resolve()  # Blender doesn't resolve relative output paths from the working folder
    out.mkdir(parents=True, exist_ok=True)
    # glTF's front (+Z) imports as Blender -Y; up is +Z.
    three_quarter = centre + Vector((-0.9, -1.7, 0.8)) * size
    front = centre + Vector((0.0, -2.2, 0.15)) * size
    back = centre + Vector((1.2, 1.6, 0.8)) * size

    shading.light, shading.color_type = "STUDIO", "TEXTURE"
    camera_at(scene, three_quarter, centre)
    scene.render.filepath = str(out / "textured.png")
    bpy.ops.render.render(write_still=True)

    colours = json.loads(Path(colours_path).read_text(encoding="utf-8"))
    for o in meshes:
        node = o.name if o.name in colours else (o.parent.name if o.parent and o.parent.name in colours else None)
        o.color = (*colours[node], 1.0) if node else (0.5, 0.5, 0.5, 1.0)
    shading.light, shading.color_type = "FLAT", "OBJECT"
    scene.render.filepath = str(out / "ids.png")
    bpy.ops.render.render(write_still=True)
    for name, position in (("ids_front", front), ("ids_back", back)):
        camera_at(scene, position, centre)
        scene.render.filepath = str(out / f"{name}.png")
        bpy.ops.render.render(write_still=True)
    print(f"[blender] rendered {len(meshes)} meshes to {out}")


if __name__ == "__main__":
    args = sys.argv[sys.argv.index("--") + 1:]
    {"fbx2glb": fbx2glb, "render": render}[args[0]](*args[1:])
