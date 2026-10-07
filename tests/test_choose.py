"""golem_choose.py's checks on the model's answer, the part list with its contacts, and the scoring
(offline; no API call).

Run from the repo root: python -m unittest -v tests.test_choose
"""

import json
import tempfile
import unittest
from pathlib import Path

import numpy as np
import trimesh

from golem_choose import agreement, check, part_list

# root.4 and root.1 each touch the body root.7, but not each other.
PARTS = [{"id": "root.1", "touches": ["root.7"]}, {"id": "root.4", "touches": ["root.7"]},
         {"id": "root.7", "touches": ["root.1", "root.4"]}]
GOOD = {"splitter": "parts",
        "parts": {"root.7": "body", "root.4": "drawer_top", "root.1": "door"},
        "joints": {"drawer_top": {"type": "slide", "direction": "front"},
                   "door": {"type": "hinge", "hinge": "left", "opens": "front"}},
        "unsure": ["door"], "why": {"door": "thin panel at the front"}}


def changed(**fields):
    answer = json.loads(json.dumps(GOOD))
    answer.update(fields)
    return json.dumps(answer)


class CheckTest(unittest.TestCase):
    def refused(self, text, reason):
        choices, problems = check(text, PARTS)
        self.assertIsNone(choices)
        self.assertTrue(any(reason in p for p in problems), problems)

    def test_good_answer_is_accepted(self):
        choices, problems = check(json.dumps(GOOD), PARTS)
        self.assertEqual(problems, [])
        self.assertEqual(choices["parts"], GOOD["parts"])

    def test_json_in_a_code_fence_is_accepted(self):
        self.assertEqual(check("```json\n" + json.dumps(GOOD) + "\n```", PARTS)[1], [])

    def test_prose_is_refused(self):
        self.refused("The top drawer slides out.", "not JSON")

    def test_invented_and_missing_parts_are_refused(self):
        self.refused(changed(parts={"root.7": "body", "root.4": "drawer_top", "root.99": "door"}), "unknown part ids")
        self.refused(changed(parts={"root.7": "body", "root.4": "drawer_top"}), "parts with no role")

    def test_a_number_in_a_joint_is_refused(self):
        self.refused(changed(joints={"drawer_top": {"type": "slide", "direction": "front", "travel": 0.4},
                                     "door": GOOD["joints"]["door"]}), "exactly the keys")

    def test_an_axis_vector_is_refused(self):
        self.refused(changed(joints={"drawer_top": {"type": "slide", "direction": [0, 0, 1]},
                                     "door": GOOD["joints"]["door"]}), "sides must be one of")

    def test_hinge_and_opening_on_one_axis_are_refused(self):
        self.refused(changed(joints={"drawer_top": GOOD["joints"]["drawer_top"],
                                     "door": {"type": "hinge", "hinge": "left", "opens": "right"}}), "different axes")

    def test_unknown_role_and_missing_joint_are_refused(self):
        self.refused(changed(parts={"root.7": "body", "root.4": "wheel", "root.1": "door"}), "is not body")
        self.refused(changed(joints={"door": GOOD["joints"]["door"]}), "moving roles are")

    def test_no_body_or_extra_keys_are_refused(self):
        self.refused(changed(parts={"root.7": "door", "root.4": "drawer_top", "root.1": "door"}), "no part is the body")
        self.refused(changed(mass_kg=12), "unknown keys")

    def test_a_group_whose_parts_do_not_touch_is_refused(self):
        self.refused(changed(parts={"root.7": "body", "root.4": "drawer_top", "root.1": "drawer_top"},
                             joints={"drawer_top": GOOD["joints"]["drawer_top"]}, unsure=[], why={}),
                     "don't touch")

    def test_a_group_that_does_not_touch_the_body_is_refused(self):
        loose = [{"id": "root.1", "touches": []}, {"id": "root.4", "touches": ["root.7"]},
                 {"id": "root.7", "touches": ["root.4"]}]
        choices, problems = check(json.dumps(GOOD), loose)
        self.assertIsNone(choices)
        self.assertIn("door: none of its parts touches the body", problems)


class PartListTest(unittest.TestCase):
    def test_boxes_are_shares_of_the_whole_model_and_contacts_are_found(self):
        scene = trimesh.Scene()
        scene.add_geometry(trimesh.creation.box(extents=[2, 1, 1]), node_name="body")
        # A panel resting on the body's front face, and a knob floating 10 cm in front of the panel.
        for name, extents, centre in (("panel", [1, 0.5, 0.2], [0.5, 0, 0.6]), ("knob", [0.1, 0.1, 0.1], [0.5, 0, 0.85])):
            scene.add_geometry(trimesh.creation.box(extents=extents, transform=trimesh.transformations.translation_matrix(centre)),
                               node_name=name)
        with tempfile.TemporaryDirectory() as folder:
            path = Path(folder) / "three.glb"
            scene.export(path)
            parts = {p["id"]: p for p in part_list(path, "three")["parts"]}
        # The model spans x -1..1, y -0.5..0.5, z -0.5..0.9.
        np.testing.assert_allclose(parts["body"]["size"], [1, 1, 0.71], atol=0.01)
        np.testing.assert_allclose(parts["panel"]["centre"], [0.75, 0.5, 0.79], atol=0.01)
        self.assertEqual(parts["body"]["touches"], ["panel"])
        self.assertEqual(parts["panel"]["touches"], ["body"])
        self.assertEqual(parts["knob"]["touches"], [])


class AgreementTest(unittest.TestCase):
    def test_same_groups_under_other_names_agree(self):
        artist = {"parts": {"root.7": "body", "root.4": "drawer_top", "root.1": "drawer_lower"},
                  "joints": {"drawer_top": {"type": "slide", "direction": "front"}}}
        model = {"parts": {"root.7": "body", "root.4": "drawer_1", "root.1": "drawer_2"},
                 "joints": {"drawer_1": {"type": "slide", "direction": "front"},
                            "drawer_2": {"type": "slide", "direction": "back"}}}
        lines = agreement(model, artist)
        self.assertIn("3 of 3 parts agree", lines[0])
        self.assertIn("2 of 2 found exactly; joints agree on 1 of 2", lines[1])


if __name__ == "__main__":
    unittest.main()
