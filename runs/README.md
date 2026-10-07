# Runs: the known-answer set (7–8 October 2026)

**Why not new assets:** the plan was five new Hyper3D models. From 20:21 IST on 7 October, Hyper3D's generation service stopped answering: every submit timed out, from the CLI, from the Hyper3D MCP server (`connect ETIMEDOUT 172.16.218.172:80`, a private address inside Hyper3D's own network) and from the hyper3d.ai website (Cloudflare 524). No credits were charged.

**What ran instead:** the hackathon's own Hyper3D meshes, which have a known answer (the artist's joints, [`reference.json`](reference.json)), so every run can be **scored**, which new assets couldn't be. Three single-piece meshes went through **Tencent HY-3D-Component** (the three free calls; the FBX inputs are public in [Golem-tencent-hy4-preview/inputs](https://github.com/suryaprasanthcse/Golem-tencent-hy4-preview/tree/main/inputs)), and two through the hackathon's **Hyper3D BANG** splits. `bash tools/run_known_answer.sh` runs all five; `python tools/score.py` writes [`SCORES.md`](SCORES.md).

## Scores

| Run | Split by | Parts → groups (with a passing joint) | Decided by | Joints built | Matches the artist | Extra joints | Unity self-test |
|---|---|---|---|---|---|---|---|
| [filing_cabinet__tencent](filing_cabinet__tencent/) | Tencent HY-3D-Component | 11 → 11 (8) | DeepSeek-V4.1-Flash, hy4-preview for one batch | 4 × slide front, 2 × slide back | **4 of 4** | 2 | PASS |
| [filing_cabinet__bang](filing_cabinet__bang/) | Hyper3D BANG | 9 → 8 (6) | DeepSeek-V4.1-Flash | 3 × slide front | **3 of 4** | 0 | PASS |
| [vault_door__tencent](vault_door__tencent/) | Tencent HY-3D-Component | 26 → 5 (0) | geometry alone | none | **0 of 1** | 0 | — |
| [vault_door__bang](vault_door__bang/) | Hyper3D BANG | 4 → 2 (0) | geometry alone | none | **0 of 1** | 0 | — |
| [chest__tencent](chest__tencent/) | Tencent HY-3D-Component | 26 → 20 (11) | DeepSeek-V4.1-Flash | 3 hinges | **0 of 1** | 3 | **FAIL** |

Each run folder has its `proof.json`, `sweep.json` (every candidate of every group: pass or fail, free range, what stopped it), `decisions.json` (every model call, its ids, latency, tokens, and the answer), `choices.json`, the four renders the model saw, and the 60 fps video (`*_articulation_demo.mp4`) where joints were built.

## What each run shows

**Filing cabinet: Tencent's split beats BANG's.** HY-3D-Component returned the cabinet as 11 closed parts, including **all four drawer fronts**; BANG separated only three, and the artist had to carve the fourth out by hand at the hackathon. With Tencent's parts the pipeline found all four drawers: **4 of 4**. The two extra joints are two back panels made into drawers sliding out of the back. Both came from the one batch where DeepSeek ran out of room and hy4-preview, which reads text only, decided instead; it described them as "on the front face". The geometry allows that motion, so only the labelling step could have caught it. Every batch DeepSeek decided from the renders was right.

**Vault door: an honest negative on both splitters.** The door (three BANG parts, or 19 of Tencent's 26 grouped together) sits deep in its frame, and every hinge and slide hits the frame within 0–9°. In the hackathon demo it opened only because GOLEM turns off collisions between a door and its frame. Two things the pipeline doesn't have yet: an inside/outside test (Tencent's parts are closed solids that may already overlap at rest) and a joint that spins a part about its own centre (the wheel). Tencent's mesh also shows a hinge knuckle on the right, where the artist chose left; with no motion passing the sweep, no model was asked.

**Chest: Tencent splits by component, not by moving part.** The lid came back in several pieces (front panel, end caps, straps), none of them "the lid", and contact grouping didn't put them back together, so the right joint (one lid on its back edge, opening up) was never a candidate. DeepSeek picked the least-bad of what was offered (a lid front panel on its top edge, a side panel as a door, another panel on its back edge), and **the Unity self-test caught one turning the wrong way: FAIL.** The video shows what the physics gate refused.

## Numbers

| | Cabinet (Tencent) | Vault (Tencent) | Chest (Tencent) | Cabinet (BANG) | Vault (BANG) |
|---|---|---|---|---|---|
| HY-3D-Component job | 1499458775675895808 | 1499471492306501632 | 1499472674684985344 | — | — |
| Tencent: submit to all parts downloaded | 136 s | 236 s | 240 s | — | — |
| Parts returned; faces before simplifying | 11; up to 985k per part | 26 | 26 | 9 (BANG) | 4 (BANG) |
| Sweep | 17 s | 9 s | 34 s | 10 s | 2 s |
| Tie-break batches (3 parts each) | 4: 19, 166 (cut off) + 244 (hy4), 127 s | none | 4: 75, 168, 167, 106 s | 2: 153, 96 s | none |
| Unity: import, joints, 480 frames at 60 fps, self-test | 102 s | — | 677 s | 40 s | — |

Tencent's parts come back with flat segment colours rather than the original texture, which is why they look colour-coded in the videos, and are dense: the orchestrator simplifies every part to at most 60,000 faces (Blender) before anything else reads it, after PhysX spent over 12 minutes on a single convex hull of the unsimplified cabinet. `proof.json` keeps the hash of both versions.

**Cost:** the three free HY-3D-Component calls (90 of the 100 free credits), no Hyper3D credits, and DeepSeek-V4.1-Flash and hy4-preview tokens on TokenHub.
