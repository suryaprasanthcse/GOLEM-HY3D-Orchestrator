# GOLEM-HY3D Orchestrator

**AI-generated 3D props come out as statues. Tencent's HY-3D-Component splits them into parts; GOLEM works out, from geometry, how each part can move, and makes it move in a physics engine.**

This is "Blueprint A": geometry proposes every possible joint and measures how far each one can move without hitting anything; a vision model on Tencent TokenHub only picks among the joints that passed; deterministic code builds the joints, Unity's PhysX checks them, and every run leaves a `proof.json` with the hash of every file and every request id.

It continues [GOLEM](https://github.com/suryaprasanthcse/Golem_Arcade_Game_tech) (1st place, Game Tech track, Cambridge × Arcade AI Hackathon, October 2026) and its [hy4-preview integration](https://github.com/suryaprasanthcse/Golem-tencent-hy4-preview), where a text model read a part list. Here the model is out of the geometry entirely.

---

## Who decides what

| Decision | Made by |
|---|---|
| Splitting the generated mesh into parts | **Tencent HY-3D-Component** on TokenHub (or Hyper3D BANG) |
| Which parts move together (a door and its knob) | code: contact shares between parts |
| Every possible joint for every part: 24 hinges on the part's own edges, 24 hung from the body's edges, 6 slides | code (`golem_kinematics.py`) |
| How far each joint can move before it hits something (degrees or metres), and whether that is far enough | code: the sweep test |
| Which outer face a part may open toward | code: the face rule |
| For parts with joints that passed: fixed, or which one of those joints; role and material | **DeepSeek-V4.1-Flash** on TokenHub, seeing renders; checked by code |
| Pivots, axes, joint limits (from the measured free range), drawer boxes | code (`golem_assemble.py`, in Blender) |
| Real-world size and mass | code constants per asset (`SIZES` in `run_orchestrator.py`) |
| Pass or fail | Unity 6.6 PhysX self-test, headless |

## The pipeline

```
 Hyper3D Rodin (text prompt) ──► .fbx (and its download link)
     │
     ▼  Tencent HY-3D-Component (TokenHub /v1/api/3d/submit, FBX by URL) ── or Hyper3D BANG
 parts: one glTF node per part (hy3d_component_probe.py logs files, nodes, meshes, bounds, textures)
     │
     ▼  golem_kinematics.py (no model, ~1-10 s)
 groups (bonded parts, small attachments folded in) → 54 candidates per group → SWEEP:
 surface samples move along each candidate in steps no longer than the reach (1% of the model's
 diagonal); a sample hits when it comes within reach of another part AND half a reach closer than at
 rest; the motion stops when more than 0.2% of samples hit or go below the floor → free range.
 Pass: hinge ≥ 75°, slide ≥ half its test length, and the face rule.
     │
     ▼  golem_tiebreak.py: only for groups with passed candidates
 DeepSeek-V4.1-Flash sees the model textured + its parts flat-coloured (4 renders, Blender) and answers
 {role, material, choice} per part, choice = "fixed" or ONE passed candidate. Code refuses anything
 else (invented candidate, wrong role for the motion, missing part). Fallbacks: hy4-preview (text),
 then a written convention; the output says which decided.
     │
     ▼  golem_auto.py → golem_assemble.py (Blender): pivots, axes, limits → <name>_split.glb + <name>_joints.json
     ▼  Unity 6.6 batch mode (GolemDemo.Run): ArticulationBody joints, every joint driven across its range by
        PhysX drives, frames rendered off screen at 60 fps, hinge self-test (exit code)
     ▼  FFmpeg: <name>_articulation_demo.mp4, the choices' SHA-256 in the caption
     ▼  proof.json
```

**Unity runs with `-batchmode`, not `-nographics`:** with no graphics device it can't render frames. Batch mode still opens no window.

## Proof of each run

`runs/<name>/proof.json` records:
- the Hyper3D generation id, prompt and times; the SHA-256 of the raw `.fbx` and `.glb`;
- Tencent HY-3D-Component's job id, request ids and headers, submit and completion times, and the structure of what it returned; the SHA-256 of the parts model;
- the sweep's full pass/fail matrix (every candidate of every group, its free range and what stopped it) and its constants;
- the tie-break calls (model, completion id, request headers, latency, tokens, accepted or refused and why) and the decision;
- the SHA-256 of the choices, the split model, the joint manifest and the video; the Unity self-test lines; the seconds per stage;
- `fallbacks_used`: every step that didn't take the primary path, and `code_options_added`: everything code added that no model chose.

**What it proves and what it doesn't:** each hash shows that file hasn't changed since the run, and the request ids identify the calls in Tencent's and Hyper3D's records. The hashes are written by the machine that ran the pipeline, so they don't prove who ran it or that nobody intervened; `fallbacks_used` is the honest record of where the primary path wasn't taken.

## Validation on a prop with a known answer

The hackathon's filing cabinet (Hyper3D BANG, 9 parts), where an artist's choices are the reference ([`runs/filing_cabinet/`](runs/filing_cabinet/)):

| Step | Result |
|---|---|
| Sweep | 7 groups (the three back panels that overlap became one). The three drawer fronts: 9 of 54 candidates pass each (slide out the front, and flaps on each front edge). The big back-panel group: none pass, fixed by geometry. The two other back panels: a few hinges pass. |
| Tie-break | DeepSeek-V4.1-Flash, 116 s: the three fronts are drawers sliding out the front, both back panels fixed. **9 of 9 parts agree with the artist.** |
| Physics | Each drawer slid 0.34 m of 0.34 m, the right way: self-test PASS. 480 frames at 60 fps. |
| Whole run | about 3 minutes, no fallbacks. |

Two things this validation found in the first version of the rules, both now covered by tests:
- **Geometry says how a part can move, never whether it should.** A back panel was free to swing off the back. So every part with a passing joint goes to the tie-breaker, which may keep it fixed.
- **The face rule.** DeepSeek once made a back panel a drawer sliding forward *through the inside* of the cabinet. A part may now only open toward an outer face it sits on, and only slide out of that face, or along it if it is a thin panel in it (a sliding door).

**The hackathon vault door can't open cleanly.** Its door (three parts, which the sweep groups together) sits deep in its frame: every hinge, including GOLEM's own hinge-arm, hits the frame within about 1°. In the hackathon's Unity demo it opened because GOLEM turns off collisions between a door and its frame, so it passed through. Without an inside/outside test, the sweep can't yet rule out that the parts already overlap at rest.

## Earlier paths kept here

`golem_choose.py` and `run_demo.py` (with `tencent/`) are the hy4-preview text path from the [previous repository](https://github.com/suryaprasanthcse/Golem-tencent-hy4-preview): the model read boxes and contacts and chose every joint. `golem_cutter.py` is the hackathon's lid cutter for one-piece models. Blueprint A doesn't use them.

## Unseen assets

See [`runs/README.md`](runs/README.md) for the assets generated for this repository and their results.

## Run it

**You need:** Windows 11, Python 3.14 (`pip install -r requirements.txt`), Blender 5.2, Unity 6.6 (`6000.6.0f1`), FFmpeg, the Hyper3D CLI (`npm i -g @hyper3d/cli`, `hyper3d auth login`), and a Tencent TokenHub key with `hy-3d-component` and `deepseek-v4.1-flash` enabled, in `%USERPROFILE%\.golem\secrets.env` as `TENCENT_MAAS_API_KEY=...` (never in the repository).

```bash
python -m unittest tests.test_kinematics tests.test_choose          # offline
python tools/generate_unseen.py                                     # Hyper3D, 0.5 credits per asset
python run_orchestrator.py microwave --split component              # Tencent HY-3D-Component (30 TokenHub credits)
python run_orchestrator.py dumpster --split bang                    # Hyper3D BANG (0.5 credits)
python run_orchestrator.py filing_cabinet --glb assets/raw/filing_cabinet_bang.glb   # a model already in parts
```

## Limits, honestly

- **The sweep uses distances, not inside/outside.** Parts that already overlap at rest can look blocked (the vault door). A signed test (winding numbers) is the next step.
- **Geometry leaves ties on almost every part.** A drawer front can also swing like a flap, so the vision model decides on nearly every prop, not only on symmetric ones. It can only choose among joints that passed.
- **The vision model is slow and not Tencent's.** DeepSeek-V4.1-Flash reasons for 2–3 minutes per prop. No Tencent model on TokenHub reads images today; when one does, it is one setting.
- **Sizes and densities are code constants per asset,** not measured; masses use the closed volume of each part times an effective density standing in for a hollow object.
- **The tie-breaker can still be wrong among valid joints,** for example which side a door hinges on when nothing in the image says so.

## AI disclosure

The code was written by Claude Code (Anthropic's Claude Opus 5.5) under the author's direction. The 3D models come from Hyper3D Rodin; parts from Tencent HY-3D-Component or Hyper3D BANG; tie-break decisions from DeepSeek-V4.1-Flash (with hy4-preview as fallback) on Tencent TokenHub. The asset prompts were written by Claude Code.
