"""One command for the whole GOLEM loop, with Tencent Hunyuan making the part choices:

  1. golem_choose.py   the part list (boxes and contacts) goes to hy4-preview on Tencent TokenHub; the
                       answer is checked by code, or the artist's choices are used (the output says which)
  2. golem_auto.py     the choices go to GOLEM's splitter (Blender), which computes every joint
  3. Unity, headless   import, hinge self-test, and frames of each part moving (GolemDemo.Run)
  4. FFmpeg            the frames become an MP4 with the call's details burned in
  5. proof.json        SHA-256 of every file in the chain, TokenHub's completion id, and the timings

  python run_demo.py --glb MODEL.glb [--name filing_cabinet] [--model hy4-preview] [--replay RUN.json]

--replay reuses a stored golem_choose run (its .run.json) instead of calling TokenHub, for a meeting
without a network; the console, the video and proof.json then say REPLAY.
Output: tencent/demo/<name>_<UTC time>/. Unity: GOLEM_UNITY; FFmpeg: GOLEM_FFMPEG (or on PATH).
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

ROOT = Path(__file__).resolve().parent
UNITY = os.environ.get("GOLEM_UNITY", r"C:\Program Files\Unity\Hub\Editor\6000.6.0f1\Editor\Unity.exe")
FFMPEG = os.environ.get("GOLEM_FFMPEG") or shutil.which("ffmpeg") or os.path.expandvars(
    r"%LOCALAPPDATA%\Microsoft\WinGet\Packages\Gyan.FFmpeg_Microsoft.Winget.Source_8wekyb3d8bbwe"
    r"\ffmpeg-9.0.2-full_build\bin\ffmpeg.exe")
FONT = "C\\:/Windows/Fonts/consola.ttf"  # escaped for FFmpeg's filter syntax


def say(text: str = ""):
    print(text, flush=True)


def banner(text: str):
    say("\n" + "=" * 78 + f"\n  {text}\n" + "=" * 78)


def sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def run(command: list, **kwargs) -> tuple[int, float]:
    """Run a step with its output streamed to the console; its exit code and seconds."""
    started = time.perf_counter()
    code = subprocess.run([str(c) for c in command], **kwargs).returncode
    return code, round(time.perf_counter() - started, 1)


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--glb", type=Path, required=True, help="multi-part model, e.g. assets/raw/filing_cabinet_bang.glb")
    parser.add_argument("--name", default="filing_cabinet")
    parser.add_argument("--model", default="hy4-preview", help="TokenHub model id")
    parser.add_argument("--artist", type=Path, help="artist's choices (fallback and score); default tencent/artist_choices/<name>.json")
    parser.add_argument("--replay", type=Path, help="a stored golem_choose .run.json to reuse instead of calling TokenHub")
    args = parser.parse_args()
    sys.stdout.reconfigure(errors="backslashreplace")
    if not args.glb.exists():
        sys.exit(f"[demo] no such model: {args.glb}")
    artist = args.artist or ROOT / "tencent" / "artist_choices" / f"{args.name}.json"
    stamp = time.strftime("%Y%m%dT%H%M%SZ", time.gmtime())
    out = ROOT / "tencent" / "demo" / f"{args.name}_{stamp}"
    out.mkdir(parents=True)
    choices, record_path = out / "choices.json", out / "choices.run.json"
    timings, codes = {}, {}

    # 1. The part choices.
    if args.replay:
        banner(f"1/5  REPLAY of a stored {args.model} run: {args.replay.name} (no TokenHub call now)")
        shutil.copyfile(args.replay, record_path)
        shutil.copyfile(args.replay.with_name(args.replay.name.replace(".run.json", ".json")), choices)
        codes["choose"], timings["choose_s"] = 0, 0.0
    else:
        banner(f"1/5  PART CHOICES: {args.model} on Tencent TokenHub")
        command = [sys.executable, ROOT / "golem_choose.py", args.glb, "--name", args.name, "--model", args.model,
                   "--out", choices]
        if artist.exists():
            command += ["--fallback", artist, "--compare", artist]
        codes["choose"], timings["choose_s"] = run(command, cwd=ROOT)
        if codes["choose"]:
            return codes["choose"]
    record = json.loads(record_path.read_text(encoding="utf-8"))
    chosen = json.loads(choices.read_text(encoding="utf-8"))
    by_model = chosen.get("source") == args.model
    usage = record.get("usage") or {}
    who = (f"{args.model} (TokenHub completion id {record.get('completion_id') or 'not recorded'})" if by_model
           else f"ARTIST FALLBACK: {'; '.join(record.get('problems') or [])}")
    say(f"\n[demo] part choices by: {who}")
    if by_model:
        say(f"[demo] latency {record.get('latency_ms')} ms, tokens {usage.get('prompt_tokens')} in /"
            f" {usage.get('completion_tokens')} out")

    # 2. GOLEM's splitter computes the joints. Drawer boxes are a code option, not a model choice.
    banner("2/5  GEOMETRY: golem_auto.py -> golem_assemble.py (Blender) computes every joint")
    split_input, added = dict(chosen), {}
    if any(role.startswith("drawer") for role in chosen.get("parts", {}).values()):
        split_input["drawer_boxes"] = added["drawer_boxes"] = True
        say("[demo] code option added for the split: drawer_boxes (GOLEM builds a box behind each drawer front)")
    (out / "split_input.json").write_text(json.dumps(split_input, indent=2), encoding="utf-8")
    codes["split"], timings["split_s"] = run([sys.executable, ROOT / "golem_auto.py", args.glb,
                                             "--choices", out / "split_input.json", "--out", out / "split"], cwd=ROOT)
    if codes["split"]:
        return codes["split"]
    joints = out / "split" / args.name / f"{args.name}_joints.json"

    # 3. Unity, headless: import, self-test, frames.
    banner("3/5  PHYSICS: Unity 6.6 headless (PhysX articulations), self-test and frames")
    unity_log = out / "unity.log"
    codes["unity"], timings["unity_s"] = run([UNITY, "-batchmode", "-projectPath", ROOT / "GolemUnity",
                                             "-executeMethod", "Golem.EditorTools.GolemDemo.Run",
                                             "-golemSplit", out / "split", "-golemFrames", out / "frames",
                                             "-logFile", unity_log])
    log = unity_log.read_text(encoding="utf-8", errors="replace") if unity_log.exists() else ""
    golem_lines = [line.strip() for line in log.splitlines() if line.startswith("[GOLEM]")]
    for line in golem_lines:
        say("  " + line)
    self_test = [line for line in golem_lines if "SELFTEST" in line]
    passed = codes["unity"] == 0 and any("DEMO self-test PASS" in line for line in golem_lines)
    if codes["unity"]:
        say(f"[demo] Unity exited with {codes['unity']}; see {unity_log}")
        return codes["unity"]

    # 4. FFmpeg: frames to MP4, with what made the choices burned in.
    banner("4/5  VIDEO: FFmpeg")
    choices_hash = sha256(choices)
    caption = [f"Part choices: {args.model} via Tencent TokenHub" + (" (REPLAY)" if args.replay else "")
               if by_model else "Part choices: ARTIST FALLBACK (model answer refused)",
               f"completion {record.get('completion_id') or 'id not recorded'}, {record.get('latency_ms')} ms",
               "Joints and physics: GOLEM code (Blender split, Unity PhysX)",
               f"self-test {'PASS' if passed else 'FAIL'} | choices sha256 {choices_hash[:16]}"]
    # LF only: FFmpeg draws a CRLF as an extra blank line.
    (out / "caption.txt").write_text("\n".join(caption), encoding="utf-8", newline="\n")
    video = out / f"{args.name}_{args.model}{'_replay' if args.replay else ''}.mp4"
    text = (f"drawtext=fontfile='{FONT}':textfile=caption.txt:fontsize=18:fontcolor=white:line_spacing=4"
            f":box=1:boxcolor=black@0.6:boxborderw=10:x=20:y=20")
    codes["encode"], timings["encode_s"] = run([FFMPEG, "-y", "-loglevel", "error", "-framerate", "30",
                                               "-i", "frames/frame_%05d.png", "-vf", text, "-c:v", "libx264",
                                               "-pix_fmt", "yuv420p", "-crf", "20", video.name], cwd=out)
    if codes["encode"]:
        return codes["encode"]
    frames = sorted((out / "frames").glob("frame_*.png"))
    say(f"[demo] {video} ({video.stat().st_size // 1024} KB, {len(frames)} frames)")
    shutil.rmtree(out / "frames")

    # 5. The chain of evidence.
    banner("5/5  PROOF: proof.json")
    proof = {
        "created_utc": stamp, "replay": bool(args.replay), "model": args.model,
        "choices_by": args.model if by_model else "artist (fallback)",
        "tokenhub": {k: record.get(k) for k in ("endpoint", "completion_id", "served_model", "sent_utc",
                                                 "latency_ms", "usage", "finish_reason", "response_headers")},
        "refused_because": record.get("problems") or [],
        "code_options_added": added,
        "self_test": self_test, "self_test_passed": passed,
        "seconds": timings,
        "sha256": {"model_glb": sha256(args.glb), "part_list_and_reply": sha256(record_path),
                   "choices": choices_hash, "split_glb": sha256(joints.with_name(f"{args.name}_split.glb")),
                   "joints": sha256(joints), "video": sha256(video)},
        "chain": "model_glb -> part list -> TokenHub reply -> checks -> choices -> Blender split + joints -> "
                 "Unity PhysX self-test -> video",
    }
    (out / "proof.json").write_text(json.dumps(proof, indent=2), encoding="utf-8")
    say(json.dumps({k: proof[k] for k in ("choices_by", "tokenhub", "self_test_passed", "seconds")}, indent=2))
    say(f"\n[demo] proof: {out / 'proof.json'}")
    return 0 if passed else 1


if __name__ == "__main__":
    sys.exit(main())
