"""Blueprint A on synthetic props with known answers: the sweep, the face rule, grouping, the tie-break
checks and the choices built from them (offline; no API call).

Run from the repo root: python -m unittest -v tests.test_kinematics
"""

import json
import tempfile
import unittest
from pathlib import Path

import trimesh

import golem_kinematics as kin
import golem_tiebreak as tie
from run_orchestrator import choices_from


def box(extents, centre):
    return trimesh.creation.box(extents=extents, transform=trimesh.transformations.translation_matrix(centre))


def analyse(parts: dict) -> dict:
    scene = trimesh.Scene()
    for name, mesh in parts.items():
        scene.add_geometry(mesh, node_name=name, geom_name=name)
    with tempfile.TemporaryDirectory() as folder:
        path = Path(folder) / "prop.glb"
        scene.export(path)
        result = kin.analyse(path)
    return {g["group"]: g for g in result["groups"]}


def verdict(group, candidate):
    return next(c for c in group["candidates"] if c["id"] == candidate)["pass"]


class SweepTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        # A solid 1 m cube with a drawer front on its front face and a lid on its top.
        cls.groups = analyse({"body": box([1, 1, 1], [0, 0, 0]),
                              "front": box([0.6, 0.3, 0.05], [0, -0.1, 0.525]),
                              "lid": box([1, 0.05, 1], [0, 0.525, 0])})

    def test_the_largest_part_is_the_fixed_anchor(self):
        self.assertTrue(self.groups["body"]["anchor"])

    def test_a_front_panel_slides_out_but_not_into_the_body(self):
        front = self.groups["front"]
        self.assertTrue(verdict(front, "slide:front"))
        self.assertFalse(verdict(front, "slide:back"))

    def test_a_front_panel_swings_out_on_a_front_edge(self):
        self.assertTrue(verdict(self.groups["front"], "hinge:left:front"))

    def test_a_lid_opens_upward_on_its_back_edge(self):
        lid = self.groups["lid"]
        self.assertTrue(verdict(lid, "hinge:back:top"))
        self.assertFalse(verdict(lid, "slide:bottom"))

    def test_free_range_is_measured(self):
        swing = next(c for c in self.groups["lid"]["candidates"] if c["id"] == "hinge:back:top")
        self.assertEqual(swing["unit"], "deg")
        self.assertGreaterEqual(swing["free"], kin.HINGE_MIN_DEG)


class FaceRuleTest(unittest.TestCase):
    def test_a_back_panel_cannot_slide_forward_through_an_open_cabinet(self):
        # Walls left, right and bottom (one part), and a loose panel at the back: physically free to
        # slide forward through the empty inside, but it isn't on the front face.
        walls = trimesh.util.concatenate([box([0.05, 1, 1], [-0.5, 0, 0]), box([0.05, 1, 1], [0.5, 0, 0]),
                                          box([1, 0.05, 1], [0, -0.5, 0])])
        groups = analyse({"walls": walls, "back": box([0.9, 0.9, 0.04], [0, 0, -0.48])})
        slide = next(c for c in groups["back"]["candidates"] if c["id"] == "slide:front")
        self.assertFalse(slide["pass"])
        self.assertIn("not on the front face", slide["stopped_by"])


class GroupingTest(unittest.TestCase):
    def test_overlapping_pieces_and_a_small_knob_move_as_one_group(self):
        groups = analyse({"body": box([1, 1, 1], [0, 0, 0]),
                          "door_a": box([0.6, 0.6, 0.05], [0, 0, 0.525]),
                          "door_b": box([0.6, 0.6, 0.05], [0, 0, 0.54]),   # overlaps door_a
                          "knob": box([0.06, 0.06, 0.06], [0.2, 0, 0.59])})  # on door_b's face
        moving = [g for g in groups.values() if not g["anchor"]]
        self.assertEqual(len(moving), 1)
        self.assertEqual(moving[0]["nodes"], ["door_a", "door_b", "knob"])


class TieBreakCheckTest(unittest.TestCase):
    SWEEP = {"groups": [
        {"group": "body", "anchor": True, "decision": "fixed", "nodes": ["body"], "candidates": []},
        {"group": "front", "anchor": False, "decision": "choose", "nodes": ["front"], "candidates": [
            {"id": "slide:front", "type": "slide", "direction": "front", "pass": True, "free": 0.6,
             "unit": "scene units", "stopped_by": None, "arm": False},
            {"id": "hinge:left:front", "type": "hinge", "hinge": "left", "opens": "front", "arm": False,
             "pass": True, "free": 120.0, "unit": "deg", "stopped_by": None},
            {"id": "slide:back", "type": "slide", "direction": "back", "pass": False, "free": 0.0,
             "unit": "scene units", "stopped_by": "body", "arm": False}]}]}

    def answer(self, **part):
        item = {"role": "drawer", "material": "wood", "choice": "slide:front", "why": "handle"}
        item.update(part)
        return json.dumps({"body_material": "wood", "parts": {"front": item}})

    def test_a_valid_pick_is_accepted(self):
        decision, problems = tie.check(self.answer(), self.SWEEP)
        self.assertEqual(problems, [])
        self.assertEqual(decision["parts"]["front"]["choice"], "slide:front")

    def test_a_failed_or_invented_candidate_is_refused(self):
        self.assertIsNone(tie.check(self.answer(choice="slide:back"), self.SWEEP)[0])
        self.assertIsNone(tie.check(self.answer(choice="hinge:top:front"), self.SWEEP)[0])

    def test_role_and_motion_must_agree(self):
        decision, problems = tie.check(self.answer(role="drawer", choice="hinge:left:front"), self.SWEEP)
        self.assertIsNone(decision)
        self.assertIn("a drawer can't be a hinge", problems[0])

    def test_unknown_material_and_missing_parts_are_refused(self):
        self.assertIsNone(tie.check(self.answer(material="titanium"), self.SWEEP)[0])
        self.assertIsNone(tie.check(json.dumps({"body_material": "wood", "parts": {}}), self.SWEEP)[0])


class ChoicesTest(unittest.TestCase):
    def test_choices_carry_the_measured_limit_and_mark_code_options(self):
        sweep = {"groups": [
            {"group": "body", "anchor": True, "nodes": ["body", "trim"], "candidates": []},
            {"group": "door", "anchor": False, "nodes": ["door", "knob"], "candidates": [
                {"id": "hinge:left:front", "type": "hinge", "hinge": "left", "opens": "front", "arm": False,
                 "free": 118.0, "stopped_by": None}]},
            {"group": "drawer", "anchor": False, "nodes": ["drawer"], "candidates": [
                {"id": "slide:front", "type": "slide", "direction": "front", "arm": False, "free": 0.4,
                 "stopped_by": "body"}]}]}
        tiebreak = {"decided_by": "deepseek-v4.1-flash", "decision": {"parts": {
            "door": {"role": "door", "choice": "hinge:left:front"}, "drawer": {"role": "drawer", "choice": "slide:front"}}}}
        choices, added = choices_from("microwave", sweep, tiebreak)
        self.assertEqual(choices["parts"], {"body": "body", "trim": "body", "door": "door_1", "knob": "door_1",
                                            "drawer": "drawer_1"})
        self.assertEqual(choices["joints"]["door_1"]["limit_deg"], 110.0)  # 118 free, capped
        self.assertEqual(choices["joints"]["drawer_1"]["travel"], 0.38)     # blocked: 95% of the free range
        self.assertTrue(added["drawer_boxes"])
        self.assertEqual(choices["size_m"], 0.5)


if __name__ == "__main__":
    unittest.main()
