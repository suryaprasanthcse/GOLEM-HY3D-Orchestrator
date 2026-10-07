# GOLEM pipeline metrics

Measured 2026-10-04T07:28:48+05:30 on Intel64 Family 6 Model 140 Stepping 2, GenuineIntel, Windows 11 (laptop: i5-11320H, 24 GB RAM, GTX 1650).

| Prop | Hyper3D cloud (generate + BANG) | GOLEM split | GOLEM Unity (import + joints) | GOLEM local total | End to end | Credits |
|---|---|---|---|---|---|---|
| chest | 111 s | 4.3 s | 0.56 s | **4.9 s** | 116 s | 0.5 |
| toolbox | 87 s | 4.1 s | 0.53 s | **4.6 s** | 92 s | 0.5 |
| laptop | 125 s | 4.3 s | 0.54 s | **4.8 s** | 130 s | 0.5 |
| vault_door | 217 s + 311 s | 3.4 s | 0.69 s | **4.1 s** | 532 s | 1.0 |
| filing_cabinet | 85 s + 283 s | 4.0 s | 0.64 s | **4.6 s** | 373 s | 1.0 |

- **GOLEM's own work per prop: 4.1-4.9 s** (average 4.6 s), of which Blender start-up is 2.1 s; without it, 2.0-2.8 s.
- Hyper3D generation and BANG run in the cloud, about 1.5-5 min per job, 0.5 credits each.
- Unity, all props: self-test 0.03 s (passed), demo stage 0.12 s.
- Cloud times come from each job's record (submitted -> downloaded); polling backs off to 30 s, so they can overstate by up to ~30 s.
- Unity imports were fresh (cached models deleted first), as for a newly generated asset.

Regenerate: `python golem_metrics.py` with the Unity project open.
