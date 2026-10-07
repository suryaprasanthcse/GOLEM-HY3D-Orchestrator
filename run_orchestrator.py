"""GOLEM-HY3D Orchestrator: one generated model in, an articulated, physics-checked prop and its proof out.

  python run_orchestrator.py NAME --split component|bang [--glb PARTS.glb]

  1. raw        assets/raw/NAME.fbx or .glb from Hyper3D (hyper3d_client.py); an FBX becomes a GLB in Blender
  2. split      Tencent HY-3D-Component (hy3d_component_probe.py, input: Hyper3D's FBX link) or Hyper3D BANG;
                every paid job is recorded and resumed, never sent twice. --glb skips this (a model already in parts)
  3. sweep      golem_kinematics.py: candidates from geometry, the sweep test, the free range of every candidate
  4. tie-break  golem_tiebreak.py: DeepSeek-V4.1-Flash on TokenHub sees the parts and may only keep a part fixed
                or pick one of its passed candidates (fallbacks: hy4-preview, then convention)
  5. choices    code: roles, joints and limits from the sweep (hinge limit = free range, capped), size and density
                from SIZES (code constants), drawer boxes for drawers
  6. joints     golem_auto.py -> golem_assemble.py (Blender): pivots, axes, the split model and joint manifest
  7. physics    Unity 6.6 in batch mode (GolemDemo.Run): import, ArticulationBody joints, every joint driven
                across its range by PhysX drives, frames at 60 fps, hinge self-test
  8. video      FFmpeg: NAME_articulation_demo.mp4, the choices' SHA-256 burned into the caption
  9. proof      proof.json: SHA-256 of every file in the chain, every request id, the sweep's pass/fail matrix

Output: runs/NAME/. Tools: GOLEM_BLENDER, GOLEM_UNITY, GOLEM_FFMPEG.
"""

import argparse
import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

import golem_kinematics
import golem_tiebreak

ROOT = Path(__file__).resolve().parent
PY = sys.executable
BLENDER = os.environ.get("GOLEM_BLENDER", r"C:\Program Files\Blender Foundation\Blender 5.2\blender.exe")
UNITY = os.environ.get("GOLEM_UNITY", r"C:\Program Files\Unity\Hub\Editor\6000.6.0f1\Editor\Unity.exe")
FFMPEG = os.environ.get("GOLEM_FFMPEG") or shutil.which("ffmpeg") or os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe")
FONT = "C\\:/Windows/Fonts/consola.ttf"
FPS = 60
HINGE_LIMIT_CAP = 110.0
MAX_PART_FACES = 60000
# Real-world size (largest dimension, m) and effective density (kg/m3 of the closed volume, standing in for
# a hollow object) per asset: code constants, like golem_sizes.json. Mass = volume x density, in Unity.
SIZES = {"microwave": (0.5, 300), "credenza": (1.6, 120), "refrigerator": (1.8, 80), "dumpster": (1.9, 70),
         "wall_safe": (0.55, 500), "filing_cabinet": (1.3, 150)}


def say(text=""):
    print(text, flush=True)


def banner(text):
    say("\n" + "=" * 78 + f"\n  {text}\n" + "=" * 78)


def sha(path: Path) -> str | None:
    return hashlib.sha256(path.read_bytes()).hexdigest() if path and path.exists() else None


def run(command, **kw) -> tuple[int, float]:
    started = time.perf_counter()
    code = subprocess.run([str(c) for c in command], **kw).returncode
    return code, round(time.perf_counter() - started, 1)


def choices_from(name: str, sweep: dict, tiebreak: dict) -> tuple[dict, dict]:
    """golem_auto.py's choices file from the sweep and the tie-break; and what code added."""
    picks = tiebreak["decision"]["parts"]
    parts, joints, counts, added, arms = {}, {}, {}, {}, set()
    for g in sweep["groups"]:
        pick = picks.get(g["group"], {"choice": "fixed"})
        if g["anchor"] or pick["choice"] == "fixed":
            for n in g["nodes"]:
                parts[n] = "body"
            continue
        candidate = next(c for c in g["candidates"] if c["id"] == pick["choice"])
        kind = pick["role"]
        counts[kind] = counts.get(kind, 0) + 1
        role = f"{kind}_{counts[kind]}"
        for n in g["nodes"]:
            parts[n] = role
        if candidate["type"] == "hinge":
            joints[role] = {"type": "hinge", "hinge": candidate["hinge"], "opens": candidate["opens"],
                            "limit_deg": round(min(candidate["free"], HINGE_LIMIT_CAP), 1)}
            arms.add(candidate["arm"])
        else:
            joints[role] = {"type": "slide", "direction": candidate["direction"]}
            if candidate["stopped_by"]:  # blocked before the end of the test: never travel further than free
                joints[role]["travel"] = round(0.95 * candidate["free"], 4)
    choices = {"source": f"blueprint-a sweep + {tiebreak['decided_by']}", "name": name, "splitter": "parts",
               "parts": parts, "joints": joints}
    if any(r.startswith("drawer") for r in joints):
        choices["drawer_boxes"] = added["drawer_boxes"] = True
    if True in arms:
        choices["hinge_arms"] = added["hinge_arms"] = True
        if False in arms:
            added["warning"] = "hinge_arms applies to every hinge: one chose an arm, another its own edge"
    if name in SIZES:
        choices["size_m"], choices["density"] = SIZES[name]
        added["size_m_and_density"] = f"SIZES[{name!r}] (code constants)"
    return choices, added


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("name", help="the prop's name (golem_sizes.json and SIZES use it)")
    parser.add_argument("--run", help="run folder under runs/ (default: NAME), to run one prop through several splitters")
    parser.add_argument("--split", choices=["component", "bang"])
    parser.add_argument("--glb", type=Path, help="a model already in parts (skips raw and split)")
    parser.add_argument("--glb-source", default="given", help="where --glb came from, for proof.json")
    parser.add_argument("--raw", type=Path, help="the single-piece model (default: assets/raw/NAME.glb)")
    parser.add_argument("--fbx", type=Path, help="the FBX sent to HY-3D-Component (default: assets/raw/NAME.fbx)")
    parser.add_argument("--fbx-url", help="its public URL (default: Hyper3D's link in assets/raw/NAME.job.json)")
    parser.add_argument("--prompt", help="generation prompt (default: from assets/raw/NAME.job.json)")
    parser.add_argument("--redo-tiebreak", action="store_true")
    parser.add_argument("--reuse-unity", action="store_true",
                        help="keep the last Unity frames and self-test, if they were made for the same choices")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="backslashreplace")
    label = args.run or args.name
    out = ROOT / "runs" / label
    out.mkdir(parents=True, exist_ok=True)
    proof = {"name": args.name, "run": label, "started_utc": time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime()),
             "seconds": {}, "fallbacks_used": [], "sha256": {}}
    raw_dir = ROOT / "assets" / "raw"
    job_path = raw_dir / f"{args.name}.job.json"
    job = json.loads(job_path.read_text(encoding="utf-8")) if job_path.exists() else {}
    prompt = args.prompt or job.get("prompt", args.name.replace("_", " "))

    # 1-2. Raw model and parts.
    if args.glb:
        parts_glb = args.glb.resolve()
        proof["split"] = {"source": args.glb_source, "parts_glb": parts_glb.name}
        proof["sha256"]["raw_glb"] = sha(args.raw) if args.raw else None
        if "bang" in args.glb_source.lower():
            proof["fallbacks_used"].append("split by Hyper3D BANG, not Tencent HY-3D-Component (free-call budget)")
    else:
        banner(f"1/9  RAW: {'Hyper3D ' + job['generation_id'] if job else args.raw}")
        proof["hyper3d"] = {k: job.get(k) for k in ("generation_id", "prompt", "tier", "format", "submitted_at",
                                                     "downloaded_at")} if job else None
        fbx = args.fbx or raw_dir / f"{args.name}.fbx"
        raw_glb = args.raw or raw_dir / f"{args.name}.glb"
        if fbx.exists() and not raw_glb.exists():
            code, proof["seconds"]["fbx_to_glb"] = run([BLENDER, "-b", "--python", ROOT / "tools" / "blender_tools.py",
                                                        "--", "fbx2glb", fbx, raw_glb], capture_output=True)
        proof["sha256"].update(raw_fbx=sha(fbx), raw_glb=sha(raw_glb))
        banner(f"2/9  SPLIT: {'Tencent HY-3D-Component' if args.split == 'component' else 'Hyper3D BANG'}")
        if args.split == "component":
            component_job = out / "component" / "job.json"
            url = args.fbx_url or job["fbx_urls"][0]
            done = component_job.exists() and json.loads(component_job.read_text(encoding="utf-8")).get("completed_at") \
                and (out / "component" / f"{label}_parts.glb").exists()
            if done:  # the job is recorded and its parts are on disk: nothing to send or fetch again
                code = 0
                say(f"[orchestrator] reusing the completed HY-3D-Component job in {component_job}")
            else:
                command = [PY, ROOT / "hy3d_component_probe.py"] + (
                    ["resume", label] if component_job.exists() else ["submit", label, "--url", url])
                code, proof["seconds"]["split"] = run(command, cwd=ROOT)
            if not component_job.exists():
                say("[orchestrator] HY-3D-Component submit wasn't recorded")
                return code or 1
            record = json.loads(component_job.read_text(encoding="utf-8"))
            parts_glb = out / "component" / f"{label}_parts.glb"
            # Tencent's parts run to ~1M faces each; PhysX spent over 12 minutes on one convex hull. Code
            # simplifies every part to at most MAX_PART_FACES before anything else reads it.
            light = out / "component" / f"{label}_parts_light.glb"
            if parts_glb.exists() and not light.exists():
                code, proof["seconds"]["simplify"] = run([BLENDER, "-b", "--python", ROOT / "tools" / "blender_tools.py",
                                                          "--", "decimate", parts_glb, light, MAX_PART_FACES],
                                                         capture_output=True)
            proof["sha256"]["parts_glb_from_tencent"] = sha(parts_glb)
            proof["mesh_simplified"] = {"max_faces_per_part": MAX_PART_FACES, "tool": "Blender Decimate (collapse)",
                                        "why": "Tencent's parts are up to ~1M faces; PhysX convex hulls stalled"}
            parts_glb = light
            proof["split"] = {"source": "tencent hy-3d-component", "job_id": record["submit"]["body"].get("id"),
                              "submit_request_id": record["submit"]["body"].get("request_id"),
                              "submit_headers": record["submit"]["headers"], "submitted": record["submit"]["at"],
                              "completed": record.get("completed_at"), "polls": record.get("polls"),
                              "files": record.get("files"), "result_links": record.get("result_links")}
        else:
            bang_job = raw_dir / f"{args.name}_bang.job.json"
            command = [PY, ROOT / "hyper3d_client.py"] + (
                ["resume", bang_job] if bang_job.exists() else ["bang", args.name, "--name", f"{args.name}_bang"])
            code, proof["seconds"]["split"] = run(command, cwd=ROOT)
            record = json.loads(bang_job.read_text(encoding="utf-8"))
            parts_glb = raw_dir / f"{args.name}_bang.glb"
            proof["split"] = {"source": "hyper3d bang", "generation_id": record.get("generation_id"),
                              "submitted": record.get("submitted_at"), "downloaded": record.get("downloaded_at")}
            proof["fallbacks_used"].append("split by Hyper3D BANG, not Tencent HY-3D-Component (free-call budget)")
        if code or not parts_glb.exists():
            say(f"[orchestrator] split failed (exit {code})")
            return code or 1
    proof["sha256"]["parts_glb"] = sha(parts_glb)

    # 3. Sweep.
    banner("3/9  SWEEP: candidates from geometry, collision-free range of every one (no model)")
    started = time.perf_counter()
    sweep = golem_kinematics.analyse(parts_glb)
    proof["seconds"]["sweep"] = round(time.perf_counter() - started, 1)
    (out / "sweep.json").write_text(json.dumps(sweep, indent=2), encoding="utf-8")
    say("\n".join(golem_kinematics.summary(sweep)))

    # 4. Tie-break.
    banner("4/9  TIE-BREAK: DeepSeek-V4.1-Flash on TokenHub, only between passed candidates")
    decisions_path = out / "decisions.json"
    if decisions_path.exists() and not args.redo_tiebreak:
        tiebreak = json.loads(decisions_path.read_text(encoding="utf-8"))
        say(f"[orchestrator] reusing {decisions_path.name} (decided by {tiebreak['decided_by']})")
    else:
        colours, _ = golem_tiebreak.colour_map(sweep)
        (out / "colours.json").write_text(json.dumps(colours), encoding="utf-8")
        code, proof["seconds"]["views"] = run([BLENDER, "-b", "--python", ROOT / "tools" / "blender_tools.py", "--",
                                               "render", parts_glb, out / "colours.json", out / "views"],
                                              capture_output=True)
        started = time.perf_counter()
        tiebreak = golem_tiebreak.decide(sweep, out / "views", prompt, "deepseek-v4.1-flash")
        proof["seconds"]["tiebreak"] = round(time.perf_counter() - started, 1)
        decisions_path.write_text(json.dumps(tiebreak, indent=2), encoding="utf-8")
    if tiebreak["decided_by"] not in ("deepseek-v4.1-flash", "geometry"):
        proof["fallbacks_used"].append(f"tie-break decided by {tiebreak['decided_by']}")
    for part, pick in tiebreak["decision"]["parts"].items():
        say(f"  {part:<10} {pick['role']:<13} {pick['choice']:<24} {pick.get('why', '')[:90]}")

    # 5. Choices.
    banner("5/9  CHOICES: roles and joints from the sweep and the tie-break")
    choices, added = choices_from(args.name, sweep, tiebreak)
    choices_path = out / "choices.json"
    choices_path.write_text(json.dumps(choices, indent=2), encoding="utf-8")
    say(json.dumps({"parts": choices["parts"], "joints": choices["joints"]}, indent=1))
    proof["code_options_added"] = added
    proof["sha256"]["choices"] = sha(choices_path)
    if not choices["joints"]:
        say("[orchestrator] nothing moves: no joints to build")
        proof["result"] = "no moving parts"
        (out / "proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
        return 1

    # 6. Joints.
    banner("6/9  JOINTS: golem_auto.py -> golem_assemble.py (Blender)")
    split_dir = out / "split"
    code, proof["seconds"]["joints"] = run([PY, ROOT / "golem_auto.py", parts_glb, "--choices", choices_path,
                                            "--out", split_dir], cwd=ROOT)
    split_glb, joints = split_dir / args.name / f"{args.name}_split.glb", split_dir / args.name / f"{args.name}_joints.json"
    if code or not joints.exists():
        return code or 1
    proof["sha256"].update(split_glb=sha(split_glb), joints=sha(joints))

    # 7. Physics.
    banner("7/9  PHYSICS: Unity 6.6 batch mode, PhysX drives across every joint, frames at 60 fps")
    frames, unity_log = out / "frames", out / "unity.log"
    if args.reuse_unity and unity_log.exists() and any(frames.glob("frame_*.png")):
        # The frames and self-test of the last run, for this same choices file (its hash is checked).
        last = json.loads((out / "unity_inputs.json").read_text(encoding="utf-8")) if (out / "unity_inputs.json").exists() else {}
        if last.get("choices") != proof["sha256"]["choices"]:
            sys.exit("[orchestrator] --reuse-unity: the last Unity run was for different choices")
        code = 0 if "DEMO self-test PASS" in unity_log.read_text(encoding="utf-8", errors="replace") else 1
        proof["unity_reused_from_previous_run"] = True
    else:
        code, proof["seconds"]["unity"] = run([UNITY, "-batchmode", "-projectPath", ROOT / "GolemUnity",
                                               "-executeMethod", "Golem.EditorTools.GolemDemo.Run",
                                               "-golemSplit", split_dir, "-golemFrames", frames, "-logFile", unity_log])
        (out / "unity_inputs.json").write_text(json.dumps({"choices": proof["sha256"]["choices"]}), encoding="utf-8")
    golem_lines = [l.strip() for l in unity_log.read_text(encoding="utf-8", errors="replace").splitlines()
                   if l.startswith("[GOLEM]")] if unity_log.exists() else []
    for line in golem_lines:
        say("  " + line[:220])
    proof["self_test"] = [l for l in golem_lines if "SELFTEST" in l]
    proof["self_test_passed"] = code == 0 and any("DEMO self-test PASS" in l for l in golem_lines)
    if code and not any(frames.glob("frame_*.png")):
        say(f"[orchestrator] Unity exited {code} with no frames")
        (out / "proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
        return code
    if code:  # a failed self-test still gets its video: it shows what the physics gate refused
        say(f"[orchestrator] Unity exited {code}: self-test FAIL; filming it anyway")

    # 8. Video.
    banner("8/9  VIDEO: FFmpeg")
    caption = [f"GOLEM-HY3D | {args.name} | split: {proof['split']['source']}",
               f"joints: geometric sweep + {tiebreak['decided_by']} tie-break",
               f"physics: Unity PhysX self-test {'PASS' if proof['self_test_passed'] else 'FAIL'}",
               f"choices sha256 {proof['sha256']['choices'][:24]}"]
    (out / "caption.txt").write_text("\n".join(caption), encoding="utf-8", newline="\n")
    video = out / f"{args.name}_articulation_demo.mp4"
    text = (f"drawtext=fontfile='{FONT}':textfile=caption.txt:fontsize=18:fontcolor=white:line_spacing=4"
            f":box=1:boxcolor=black@0.6:boxborderw=10:x=20:y=20")
    code, proof["seconds"]["video"] = run([FFMPEG, "-y", "-loglevel", "error", "-framerate", str(FPS), "-i",
                                           "frames/frame_%05d.png", "-vf", text, "-c:v", "libx264", "-pix_fmt",
                                           "yuv420p", "-crf", "20", video.name], cwd=out)
    if code:
        return code
    proof["video_frames"] = len(list(frames.glob("frame_*.png")))
    shutil.rmtree(frames)
    proof["sha256"]["video"] = sha(video)

    # 9. Proof.
    banner("9/9  PROOF")
    proof["tiebreak"] = {"decided_by": tiebreak["decided_by"],
                         "attempts": [{k: a.get(k) for k in ("model", "http", "completion_id", "served_model", "sent_utc",
                                                             "latency_ms", "usage", "finish_reason", "response_headers",
                                                             "accepted", "problems", "images")}
                                      for a in tiebreak.get("attempts", [])],
                         "decision": tiebreak["decision"]}
    proof["sweep"] = {"constants": sweep["constants"], "reach": sweep["reach"], "anchor": sweep["anchor"],
                      "matrix": {g["group"]: {"nodes": g["nodes"], "decision": g["decision"],
                                              "candidates": {c["id"]: {"pass": c["pass"], "free": c["free"],
                                                                       "unit": c["unit"], "stopped_by": c["stopped_by"]}
                                                             for c in g["candidates"]}}
                                 for g in sweep["groups"]}}
    proof["chain"] = ("raw model -> split (parts) -> sweep (pass/fail, free range) -> tie-break (pick among passed) -> "
                      "choices -> Blender split + joints -> Unity PhysX self-test -> video")
    proof["what_this_proves"] = ("Each file's hash shows it hasn't changed since this run; the request ids identify "
                                 "the calls in Tencent's and Hyper3D's records. The hashes are written by the machine "
                                 "that ran the pipeline: they don't prove who ran it. fallbacks_used lists every step "
                                 "that didn't take the primary path.")
    (out / "proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    say(json.dumps({k: proof[k] for k in ("split", "fallbacks_used", "code_options_added", "self_test_passed",
                                          "seconds", "sha256")}, indent=2)[:4000])
    say(f"\n[orchestrator] {video}\n[orchestrator] {out / 'proof.json'}")
    return 0 if proof["self_test_passed"] else 1


if __name__ == "__main__":
    sys.exit(main())
