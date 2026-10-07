"""The Blender cutter on synthetic chests with known seams (needs Blender; about 10 s).

Run from the repo root: python -m unittest -v
"""

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

ROOT = Path(__file__).resolve().parents[1]


def box(lo, hi):
    lo, hi = np.asarray(lo, float), np.asarray(hi, float)
    return trimesh.creation.box(extents=hi - lo, transform=trimesh.transformations.translation_matrix((lo + hi) / 2))


# glTF frame: +Y up, front +Z, so the back of the chest is at -Z.
CHESTS = {
    # A 4 cm groove between body and lid, inset 2 cm: cut mid-groove, hinge on the groove's back edge.
    "groove": ([box([-.5, 0, -.3], [.5, .38, .3]), box([-.48, .38, -.28], [.48, .42, .28]), box([-.5, .42, -.3], [.5, .55, .3])], 0.40, -0.28),
    # A lid that overhangs the body by 2 cm: cut at the step.
    "lip": ([box([-.5, 0, -.3], [.5, .40, .3]), box([-.52, .40, -.32], [.52, .55, .32])], 0.40, -0.30),
    # No seam at all: fall back to 70% of the height.
    "plain": ([box([-.5, 0, -.3], [.5, .55, .3])], 0.385, -0.30),
}


class CutterTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.tmp = Path(tempfile.mkdtemp())
        cls.results = {}
        for name, (parts, _, _) in CHESTS.items():
            trimesh.Scene([trimesh.util.concatenate(parts)]).export(cls.tmp / f"{name}.glb")
            run = subprocess.run(
                [sys.executable, str(ROOT / "golem_cutter.py"), str(cls.tmp / f"{name}.glb"), "--out", str(cls.tmp / "out")],
                capture_output=True,
                text=True,
            )
            cls.results[name] = run

    def check(self, name):
        _, cut_y, back_z = CHESTS[name]
        self.assertEqual(self.results[name].returncode, 0, self.results[name].stdout)
        out = self.tmp / "out" / name
        joint = json.loads((out / f"{name}_joints.json").read_text())["joints"][0]
        scene = trimesh.load(out / f"{name}_split.glb", force="scene")

        self.assertEqual(scene.graph.transforms.parents.get("lid"), "body")
        np.testing.assert_allclose(scene.graph.get("lid")[0][:3, 3], joint["pivot"], atol=1e-4)  # lid origin = hinge
        self.assertAlmostEqual(joint["pivot"][1], cut_y, delta=0.005)
        self.assertAlmostEqual(joint["pivot"][2], back_z, places=4)
        np.testing.assert_allclose(joint["axis"], [-1, 0, 0])  # positive rotation lifts the front
        for node in ("body", "lid"):
            part = scene.geometry[scene.graph[node][1]].copy()
            part.merge_vertices(merge_tex=True, merge_norm=True)
            self.assertTrue(part.is_watertight, f"{node} cap missing")

    def test_groove_is_cut_in_the_middle(self):
        self.check("groove")

    def test_overhanging_lid_is_cut_at_the_step(self):
        self.check("lip")

    def test_no_seam_falls_back_to_70_percent(self):
        self.check("plain")


if __name__ == "__main__":
    unittest.main()
