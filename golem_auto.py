"""GOLEM's choice step as a file: read the discrete choices for a generated model from a JSON file
and run the matching splitter (golem_cutter.py or golem_assemble.py) with them.

Today an artist writes the choices file (Artist-in-the-Loop). It is also the slot a vision model
fills: the choices are discrete on purpose, so swapping the artist for a model changes who writes
this file and nothing downstream.

  python golem_auto.py model.glb --choices choices.json [--out DIR]

Choices file:
  source        who made the choices: "artist", or the vision model's name
  name          output name (default: the model file's name)
  size_m, density   optional real-world size (largest dimension, metres) and effective density
                (kg/m3); Unity uses them when golem_sizes.json has no entry for this name
  splitter      "cut": a one-piece mesh with a lid (golem_cutter.py)
                "parts": a multi-part model, such as Hyper3D BANG output (golem_assemble.py)
  lid           for "cut": {"hinge": "back", "generated_open": false, "limit_deg": 110, "cut": "auto"}
  parts         for "parts": {node: role}; role "body" stays fixed, every other role moves as a group
  joints        for "parts": {role: {"type": "hinge", "hinge": SIDE, "opens": SIDE, "limit_deg": N}
                              or {"type": "slide", "direction": SIDE, "travel": N}};
                roles named lid*, door* and drawer* default to a back-hinged lid opening up 110 deg,
                a left-hinged door opening to the front 100 deg, and a drawer sliding out the front
  drawer_boxes, hinge_arms   optional golem_assemble.py options

The choices file is copied next to the split output as <name>_choices.json, with the model's path.
"""

import argparse
import json
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
SIDES = {"left", "right", "back", "front", "top", "bottom", "up", "down"}
ROLE_DEFAULTS = {
    "lid": {"type": "hinge", "hinge": "back", "opens": "up", "limit_deg": 110},
    "door": {"type": "hinge", "hinge": "left", "opens": "front", "limit_deg": 100},
    "drawer": {"type": "slide", "direction": "front"},
}


def fail(message: str):
    sys.exit(f"[golem] choices: {message}")


def side(value, what: str) -> str:
    if value not in SIDES:
        fail(f"{what} must be one of {sorted(SIDES)}, not {value!r}")
    return value


def joint_for(role: str, joints: dict) -> dict:
    if role in joints:
        return joints[role]
    default = next((d for prefix, d in ROLE_DEFAULTS.items() if role.startswith(prefix)), None)
    if default is None:
        fail(f"no joint given for role {role!r}, and it doesn't start with {sorted(ROLE_DEFAULTS)}")
    return default


def cutter_command(model: Path, choices: dict, out: Path) -> list[str]:
    lid = choices.get("lid", {})
    command = [str(ROOT / "golem_cutter.py"), str(model), "--out", str(out),
               "--hinge", side(lid.get("hinge", "back"), "lid.hinge"),
               "--open-deg", str(float(lid.get("limit_deg", 110))),
               "--cut", str(lid.get("cut", "auto"))]
    if lid.get("generated_open"):
        command.append("--open-part")
    return command


def assembler_command(model: Path, choices: dict, out: Path, name: str) -> list[str]:
    parts = choices.get("parts") or fail('"parts" is required for splitter "parts"')
    groups: dict[str, list[str]] = {}
    for node, role in parts.items():
        groups.setdefault(role, []).append(node)
    if "body" not in groups:
        fail('one or more parts must have the role "body" (the part that stays fixed)')
    command = [str(ROOT / "golem_assemble.py"), str(model), "--out", str(out), "--name", name,
               "--root", ",".join(groups.pop("body"))]
    if not groups:
        fail("no moving parts: give at least one role other than body")
    for role, nodes in groups.items():
        joint = joint_for(role, choices.get("joints", {}))
        if joint.get("type") == "hinge":
            spec = f"{role}={','.join(nodes)}:{side(joint['hinge'], f'{role}.hinge')}:{side(joint['opens'], f'{role}.opens')}"
            command += ["--move", spec + (f":{float(joint['limit_deg'])}" if "limit_deg" in joint else "")]
        elif joint.get("type") == "slide":
            spec = f"{role}={','.join(nodes)}:{side(joint['direction'], f'{role}.direction')}"
            command += ["--slide", spec + (f":{float(joint['travel'])}" if "travel" in joint else "")]
        else:
            fail(f"joint type for {role!r} must be 'hinge' or 'slide'")
    if choices.get("drawer_boxes"):
        command.append("--drawer-boxes")
    if choices.get("hinge_arms"):
        command.append("--hinge-arms")
    return command


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("model", type=Path, help="generated model (.glb)")
    parser.add_argument("--choices", type=Path, required=True, help="choices JSON (see above)")
    parser.add_argument("--out", type=Path, default=ROOT / "assets" / "split")
    args = parser.parse_args()
    if not args.model.exists():
        fail(f"no such model: {args.model}")
    choices = json.loads(args.choices.read_text(encoding="utf-8"))
    name = choices.get("name") or args.model.stem
    print(f"[golem] choices from {choices.get('source', 'unknown')}: {args.choices}", flush=True)

    with tempfile.TemporaryDirectory() as staging:
        model = args.model
        splitter = choices.get("splitter")
        if splitter == "cut":
            # The cutter names its output after the model file, so stage it under the chosen name.
            model = Path(staging) / f"{name}.glb"
            shutil.copyfile(args.model, model)
            command = cutter_command(model, choices, args.out)
        elif splitter == "parts":
            command = assembler_command(model, choices, args.out, name)
        else:
            fail('"splitter" must be "cut" or "parts"')
        print("[golem] running: " + " ".join(f'"{c}"' if " " in c else c for c in ["python", *command]), flush=True)
        result = subprocess.run([sys.executable, *command], cwd=ROOT)
    if result.returncode:
        return result.returncode
    record = dict(choices, name=name, model=str(args.model))
    (args.out / name / f"{name}_choices.json").write_text(json.dumps(record, indent=2), encoding="utf-8")
    print(f"[golem] wrote {args.out / name / f'{name}_choices.json'}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
