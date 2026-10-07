"""Generate the five unseen assets with Hyper3D, retrying submits that time out, without ever paying twice.

A submit that times out is retried (at most ATTEMPTS times, WAIT seconds apart) only if the credit balance
is unchanged since before that submit; if credits dropped but no job was recorded, everything stops, so
the lost job can be found by hand instead of being paid for again.

The three assets for Tencent's HY-3D-Component are generated as FBX (its only input format; Hyper3D's
download link is passed to Tencent as is). The two for Hyper3D BANG are generated as GLB.

  python tools/generate_unseen.py
"""

import json
import subprocess
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ATTEMPTS, WAIT = 6, 240
ASSETS = [
    ("microwave", "fbx", "countertop microwave oven with a side-hinged glass door on the front, door closed, "
                         "door handle on the right edge, control panel on the right, game prop"),
    ("credenza", "fbx", "low wooden credenza sideboard with two sliding doors on the front, doors closed, game prop"),
    ("refrigerator", "fbx", "single-door kitchen refrigerator, door closed, vertical handle on the left edge "
                            "of the door, game prop"),
    ("dumpster", "glb", "green steel dumpster with one hinged top lid, lid closed, game prop"),
    ("wall_safe", "glb", "steel wall safe with a hinged front door, door closed, round dial and handle on the door, "
                         "game prop"),
]


def credits() -> float:
    out = subprocess.run(["hyper3d", "--output", "json", "auth", "status"], capture_output=True, text=True,
                         shell=True).stdout
    wallet = json.loads(out)["wallet"]
    return float(wallet["balance"]) + float(wallet["subscription_balance"])


def main() -> int:
    for name, fmt, prompt in ASSETS:
        if (ROOT / "assets" / "raw" / f"{name}.job.json").exists():
            print(f"[{name}] already recorded, skipping", flush=True)
            continue
        for attempt in range(1, ATTEMPTS + 1):
            before = credits()
            print(f"[{name}] attempt {attempt}, credits {before}", flush=True)
            result = subprocess.run([sys.executable, str(ROOT / "hyper3d_client.py"), "generate", prompt,
                                     "--name", name, "--format", fmt, "--timeout", "15"],
                                    capture_output=True, text=True, cwd=ROOT)
            print(result.stdout[-600:] + result.stderr[-600:], flush=True)
            if (ROOT / "assets" / "raw" / f"{name}.job.json").exists():
                break
            after = credits()
            if after < before:
                print(f"[{name}] STOP: credits fell {before} -> {after} with no job recorded", flush=True)
                return 2
            if attempt < ATTEMPTS:
                time.sleep(WAIT)
        else:
            print(f"[{name}] gave up after {ATTEMPTS} attempts", flush=True)
            return 1
    print("[all] done", flush=True)
    return 0


if __name__ == "__main__":
    sys.exit(main())
