# GOLEM-HY3D Orchestrator

GOLEM-HY3D Orchestrator converts a part-segmented 3D mesh into an articulated, engine-agnostic prop: glTF 2.0 plus a JSON joint manifest. Segmentation comes from Tencent HY-3D-Component (or Hyper3D BANG). Code groups the parts by surface contact, enumerates 54 discrete joint candidates per group (24 edge hinges, 24 frame-mounted hinges, 6 slides) and sweeps each one: surface samples advance in steps no longer than 1% of the model's diagonal against a KD-tree of every other part, and the collision-free range is measured in degrees or metres. No model outputs a number. A vision model on Tencent TokenHub (DeepSeek-V4.1-Flash) may only keep a part fixed or select one of its passing candidates; any other answer fails schema and geometry checks, and every fallback is recorded. Pivots, axes and joint limits are computed in Blender from the selected candidate, then imported into Unity 6.6 in batch mode as ArticulationBody joints, driven through their measured range by PhysX drives, rendered off screen at 60 fps and self-tested. Each run writes `proof.json`: a SHA-256 manifest of every artifact in the chain, the TokenHub request ids, the sweep's full pass/fail matrix and the per-stage timings. On a five-run known-answer set, HY-3D-Component segmentation plus this pipeline recovered 4 of 4 drawers on a filing cabinet, against 3 of 4 with BANG; the vault-door and chest failures, and their causes, are documented in [`runs/README.md`](runs/README.md).

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
 parts: one glTF node per part (hy3d_component_probe.py logs files, nodes, meshes, bounds, textures),
 each simplified to at most 60,000 faces (Blender)
     │
     ▼  golem_kinematics.py (no model, ~1-10 s)
 groups (bonded parts, small attachments folded in) → 54 candidates per group → SWEEP:
 surface samples move along each candidate in steps no longer than the reach (1% of the model's
 diagonal); a sample hits when it comes within reach of another part AND half a reach closer than at
 rest; the motion stops when more than 0.2% of samples hit or go below the floor → free range.
 Pass: hinge ≥ 75°, slide ≥ half its test length, and the face rule.
     │
     ▼  golem_tiebreak.py: only for groups with passed candidates
 DeepSeek-V4.1-Flash sees the model textured + its parts flat-coloured (4 renders, Blender), three parts
 per question, and answers
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

## Results: the known-answer set

Five new Hyper3D models were planned, but Hyper3D's generation service was down for the whole session (every submit timed out inside Hyper3D's network; nothing was charged). The runs use the hackathon's Hyper3D meshes instead, which have a known answer (the artist's joints), so each one is **scored**. Three single-piece meshes went through **Tencent HY-3D-Component** (its three free calls), two through the hackathon's Hyper3D BANG splits. Details, ids and timings: [`runs/README.md`](runs/README.md).

| Run | Split by | Joints built | Matches the artist | Extra | Unity self-test |
|---|---|---|---|---|---|
| Filing cabinet | **Tencent HY-3D-Component** (11 parts) | 4 × slide front, 2 × slide back | **4 of 4** | 2 | PASS |
| Filing cabinet | Hyper3D BANG (9 parts) | 3 × slide front | **3 of 4** | 0 | PASS |
| Vault door | Tencent HY-3D-Component (26 parts) | none: every joint hits the frame | **0 of 1** | 0 | — |
| Vault door | Hyper3D BANG (4 parts) | none: every joint hits the frame | **0 of 1** | 0 | — |
| Chest | Tencent HY-3D-Component (26 parts) | 3 hinges on lid pieces | **0 of 1** | 3 | **FAIL** (caught) |

- **On the cabinet, Tencent's split beats BANG's.** HY-3D-Component separated all four drawer fronts; BANG only three (the artist carved the fourth by hand at the hackathon). The two extra joints are back panels made into drawers by hy4-preview, the text-only fallback, in the one batch DeepSeek couldn't finish; every batch DeepSeek decided from the renders was right.
- **The vault door can't open cleanly on either split.** It sits deep in its frame; every hinge, including GOLEM's own hinge-arm, hits the frame within 0–9°. In the hackathon's Unity demo it opened only because GOLEM turns off collisions between a door and its frame.
- **HY-3D-Component splits by component, not by moving part.** The chest's lid came back as several pieces; contact grouping didn't reassemble them, so the right joint was never a candidate. The tie-breaker picked among wrong options and **the Unity self-test failed the run**: the physics gate did its job.

Rules this testing added, each covered by tests or recorded in `proof.json`:
- **Geometry says how a part can move, never whether it should.** A back panel was free to swing off the back, so every part with a passing joint goes to the tie-breaker, which may keep it fixed.
- **The face rule.** DeepSeek once made a back panel a drawer sliding forward *through the inside* of the cabinet. A part may only open toward an outer face it sits on, and only slide out of that face, or along it if it is a thin panel in it (a sliding door).
- **Bonding at 0.2.** Parts move as one when at least 20% of either one's surface lies within reach of the other. Tencent's neighbouring drawer fronts touch at 13–15% and must stay separate; the vault door's three BANG pieces touch at 20–61% and must move together. Tuned on these three models.
- **Simplify Tencent's parts.** They come back at up to ~1M faces each; PhysX spent over 12 minutes on one convex hull. Every part is simplified to at most 60,000 faces in Blender first; `proof.json` keeps both hashes.
- **Three parts per question.** Asked about ten parts at once, DeepSeek reasoned past its 32,000-token cap without answering.

## Earlier paths kept here

`golem_choose.py` and `run_demo.py` (with `tencent/`) are the hy4-preview text path from the [previous repository](https://github.com/suryaprasanthcse/Golem-tencent-hy4-preview): the model read boxes and contacts and chose every joint. `golem_cutter.py` is the hackathon's lid cutter for one-piece models. Blueprint A doesn't use them.

## Run it

**You need:** Windows 11, Python 3.14 (`pip install -r requirements.txt`), Blender 5.2, Unity 6.6 (`6000.6.0f1`), FFmpeg, the Hyper3D CLI (`npm i -g @hyper3d/cli`, `hyper3d auth login`), and a Tencent TokenHub key with `hy-3d-component` and `deepseek-v4.1-flash` enabled, in `%USERPROFILE%\.golem\secrets.env` as `TENCENT_MAAS_API_KEY=...` (never in the repository).

```bash
python -m unittest tests.test_kinematics tests.test_choose          # offline
bash tools/run_known_answer.sh                                      # the five scored runs (needs the hackathon models)
python tools/score.py                                               # runs/SCORES.md
python tools/generate_unseen.py                                     # new Hyper3D models, 0.5 credits each (when Hyper3D is up)
python run_orchestrator.py microwave --split component              # Tencent HY-3D-Component (30 TokenHub credits)
python run_orchestrator.py dumpster --split bang                    # Hyper3D BANG (0.5 credits)
```

The hackathon models are in `assets_release.zip` from the [hackathon release](https://github.com/suryaprasanthcse/Golem_Arcade_Game_tech/releases/tag/v1.0-hackathon); `run_known_answer.sh` expects them in `../golem_oct3/assets/raw` (set `HACK`).

## Limits, honestly

- **Three props, five runs.** Enough to find failure modes, not to claim an accuracy rate. No new assets were run (Hyper3D was down).
- **The sweep uses distances, not inside/outside.** Parts that already overlap at rest can look blocked (the vault door). A signed test (winding numbers) is the next step.
- **No joint that spins a part about its own centre,** so wheels and knobs (the vault's locking wheel) can't move.
- **Grouping by contact can't rebuild a part that comes back in pieces** (the chest's lid from HY-3D-Component). Grouping by shared motion (pieces that pass the same hinge together) is the next step.
- **Geometry leaves ties on almost every part.** A drawer front can also swing like a flap, so the model decides on nearly every prop, not only on symmetric ones. It can only choose among joints that passed.
- **The vision model is slow and not Tencent's.** DeepSeek-V4.1-Flash takes 1–3 minutes per three-part batch. When it can't finish, hy4-preview (text only) decides, and made both of the cabinet's extra joints. No Tencent model on TokenHub reads images today; when one does, it is one setting.
- **Tencent's parts are dense and untextured** (flat segment colours), so they are simplified before use and look colour-coded in the videos. Unity took 11 minutes on the 26-part chest.
- **Sizes and densities are code constants per asset,** not measured; masses use the closed volume of each part times an effective density standing in for a hollow object.
- **The thresholds (bonding, face rule, sweep reach) were tuned on these three models.**

## AI disclosure

The code was written by Claude Code (Anthropic's Claude Opus 5.5) under the author's direction. The 3D models come from Hyper3D Rodin; parts from Tencent HY-3D-Component or Hyper3D BANG; tie-break decisions from DeepSeek-V4.1-Flash (with hy4-preview as fallback) on Tencent TokenHub. The runs use the hackathon's models and prompts; the prompts for new assets in `tools/generate_unseen.py` were written by Claude Code.
