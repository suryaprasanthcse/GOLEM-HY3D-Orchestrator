using System.Collections.Generic;
using System.Globalization;
using System.IO;
using System.Linq;
using System.Text;
using UnityEditor;
using UnityEngine;

namespace Golem.EditorTools
{
    /// <summary>
    /// Play-mode measurements for the physics audit. An agent driving the live Editor starts a
    /// recording, triggers O, C and ball shots through the same code the keys run, then stops the
    /// recording and gets per-prop numbers: how far each prop tilted and slid, and how far, how
    /// fast and how far past its limits each joint went. Samples are taken every Editor update.
    /// </summary>
    public static class GolemAudit
    {
        const string Suffix = " (GOLEM)";
        static readonly CultureInfo Inv = CultureInfo.InvariantCulture;

        class Prop
        {
            public string name;
            public ArticulationBody root;
            public Vector3 startPosition;
            public Quaternion startRotation;
            public ArticulationBody[] parts;
            public float maxTilt, maxShift, maxTurn;
            public float[] maxValue, minValue, maxSpeed;
        }

        static List<Prop> props = new List<Prop>();
        static StringBuilder csv = new StringBuilder();
        static float startTime;

        public static string Start()
        {
            if (!EditorApplication.isPlaying)
                return "not in play mode";
            props = Object.FindObjectsByType<Transform>(FindObjectsSortMode.None)
                .Where(t => t.parent == null && t.name.EndsWith(Suffix))
                .Select(t => (t, root: t.GetComponentsInChildren<ArticulationBody>().FirstOrDefault(b => b.isRoot)))
                .Where(p => p.root != null)
                .OrderBy(p => p.t.name)
                .Select(p =>
                {
                    var parts = p.t.GetComponentsInChildren<ArticulationBody>().Where(b => !b.isRoot).ToArray();
                    return new Prop
                    {
                        name = p.t.name.Replace(Suffix, ""), root = p.root, parts = parts,
                        startPosition = p.root.transform.position, startRotation = p.root.transform.rotation,
                        maxValue = parts.Select(_ => float.MinValue).ToArray(),
                        minValue = parts.Select(_ => float.MaxValue).ToArray(),
                        maxSpeed = new float[parts.Length],
                    };
                }).ToList();
            csv.Clear();
            csv.AppendLine("t,prop,part,value,target,speed,tilt_deg,shift_m");
            startTime = Time.time;
            EditorApplication.update -= Sample;
            EditorApplication.update += Sample;
            return $"recording {props.Count} props";
        }

        static void Sample()
        {
            if (!EditorApplication.isPlaying)
            {
                EditorApplication.update -= Sample;
                return;
            }
            var t = Time.time - startTime;
            foreach (var p in props)
            {
                if (p.root == null)
                    continue;
                var tilt = Vector3.Angle(p.root.transform.up, Vector3.up);
                var shift = Vector3.Distance(p.root.transform.position, p.startPosition);
                var turn = Quaternion.Angle(p.root.transform.rotation, p.startRotation);
                p.maxTilt = Mathf.Max(p.maxTilt, tilt);
                p.maxShift = Mathf.Max(p.maxShift, shift);
                p.maxTurn = Mathf.Max(p.maxTurn, turn);
                csv.AppendLine(string.Format(Inv, "{0:F3},{1},root,,,,{2:F2},{3:F3}", t, p.name, tilt, shift));
                for (var i = 0; i < p.parts.Length; i++)
                {
                    var (value, speed) = Joint(p.parts[i]);
                    p.maxValue[i] = Mathf.Max(p.maxValue[i], value);
                    p.minValue[i] = Mathf.Min(p.minValue[i], value);
                    p.maxSpeed[i] = Mathf.Max(p.maxSpeed[i], Mathf.Abs(speed));
                    csv.AppendLine(string.Format(Inv, "{0:F3},{1},{2},{3:F3},{4:F3},{5:F3},,", t, p.name, p.parts[i].name, value, p.parts[i].xDrive.target, speed));
                }
            }
        }

        /// <summary>Joint travel in the drive's units: degrees for a hinge, metres for a slide.</summary>
        static (float value, float speed) Joint(ArticulationBody part)
        {
            if (part.dofCount < 1)
                return (0, 0);
            var scale = part.jointType == ArticulationJointType.PrismaticJoint ? 1f : Mathf.Rad2Deg;
            return (part.jointPosition[0] * scale, part.jointVelocity[0] * scale);
        }

        /// <summary>Stop recording, write the samples to a CSV, return a per-prop summary as JSON.</summary>
        public static string Stop(string csvPath)
        {
            EditorApplication.update -= Sample;
            if (!string.IsNullOrEmpty(csvPath))
                File.WriteAllText(csvPath, csv.ToString());
            return Summary();
        }

        /// <summary>The current pose of every prop, plus the extremes since Start.</summary>
        public static string Summary()
        {
            var lines = new List<string>();
            foreach (var p in props.Where(p => p.root != null))
            {
                var tilt = Vector3.Angle(p.root.transform.up, Vector3.up);
                var shift = Vector3.Distance(p.root.transform.position, p.startPosition);
                var parts = new List<string>();
                for (var i = 0; i < p.parts.Length; i++)
                {
                    var (value, _) = Joint(p.parts[i]);
                    var d = p.parts[i].xDrive;
                    parts.Add(string.Format(Inv,
                        "\"{0}\": {{\"now\": {1:F2}, \"target\": {2:F2}, \"limits\": [{3:F2}, {4:F2}], \"min\": {5:F2}, \"max\": {6:F2}, \"max_speed\": {7:F1} }}",
                        p.parts[i].name, value, d.target, d.lowerLimit, d.upperLimit, p.minValue[i], p.maxValue[i], p.maxSpeed[i]));
                }
                // Lowest vertex of the prop: below zero means it is sinking into, or through, the floor.
                // (Not Renderer.bounds: that is the generated pose's box turned with the part, and a
                // laptop screen turned 105 deg shut reported 4.8 cm below the floor while lying flat.)
                var lowest = p.root.GetComponentsInChildren<MeshFilter>().Where(f => f.sharedMesh != null && f.sharedMesh.isReadable)
                    .Min(f => f.sharedMesh.vertices.Min(v => f.transform.TransformPoint(v).y));
                lines.Add(string.Format(Inv,
                    "\"{0}\": {{\"tilt\": {1:F2}, \"max_tilt\": {2:F2}, \"shift\": {3:F3}, \"max_shift\": {4:F3}, \"max_turn\": {5:F2}, \"lowest_y\": {6:F3}, \"parts\": {{{7}}}}}",
                    p.name, tilt, p.maxTilt, shift, p.maxShift, p.maxTurn, lowest, string.Join(", ", parts)));
            }
            return string.Format(Inv, "{{\"t\": {0:F2}, ", Time.time - startTime) + string.Join(", ", lines) + "}";
        }

        public static string Open()
        {
            GolemDragController.OpenAll();
            return "open";
        }

        public static string Close()
        {
            GolemDragController.CloseAll();
            return "close";
        }

        // A scripted drag, stepped every Editor update through the drag controller's own Grab/DragTo/Release.
        static GolemDragController dragger;
        static ArticulationBody dragPart;
        static Vector2 dragFrom, dragBy;
        static Vector3 dragLocal;       // grabbed point in the part's local space
        static float dragStart, dragSeconds;
        static List<float> dragValues = new List<float>();
        static string dragReport = "no drag yet";

        /// <summary>
        /// Grab a part at the visible point farthest from its joint and move the pointer by (dx, dy)
        /// pixels over the given seconds, then hold for a second and let go. Read the outcome with
        /// DragResult: joint travel, whether it moved one way only, and how far the grabbed point ended
        /// from the cursor on screen.
        /// </summary>
        public static string Drag(string prop, string part, float dx, float dy, float seconds = 1.5f)
        {
            if (!EditorApplication.isPlaying)
                return "not in play mode";
            var cam = Camera.main;
            dragger = cam != null ? cam.GetComponent<GolemDragController>() : null;
            var wrapper = GameObject.Find(prop + Suffix);
            dragPart = wrapper != null ? wrapper.GetComponentsInChildren<ArticulationBody>().FirstOrDefault(b => b.name == part && !b.isRoot) : null;
            if (dragger == null || dragPart == null)
                return $"no drag controller, or no moving part {prop}/{part}";

            var best = BestGrab(cam, dragPart);
            if (best.score < 0)
                return $"{prop}/{part} is not visible from the camera";
            if (!dragger.Grab(best.screen) || dragger.HeldPart != dragPart)
                return $"grab at {best.screen} did not catch {prop}/{part}";

            dragFrom = best.screen;
            dragBy = new Vector2(dx, dy);
            dragLocal = dragPart.transform.InverseTransformPoint(best.point);
            dragStart = Time.time;
            dragSeconds = Mathf.Max(0.1f, seconds);
            dragValues.Clear();
            dragReport = "dragging";
            EditorApplication.update -= StepDrag;
            EditorApplication.update += StepDrag;
            var range = dragPart.xDrive.upperLimit - dragPart.xDrive.lowerLimit;
            return string.Format(Inv, "grabbed {0}/{1} at ({2:F0}, {3:F0}) px, joint {4:F2}, full travel = {5:F0} px of drag",
                prop, part, dragFrom.x, dragFrom.y, Joint(dragPart).value, dragger.DragPerUnitOnScreen.magnitude * range);
        }

        /// <summary>The visible point of a part farthest from its joint (any visible point of a slide):
        /// where a scripted drag grabs it. Candidates are a grid over the part's bounds, kept where the
        /// camera's ray hits this part.</summary>
        static (float score, Vector2 screen, Vector3 point) BestGrab(Camera cam, ArticulationBody part)
        {
            var bounds = part.GetComponentsInChildren<Renderer>().Select(r => r.bounds).Aggregate((a, b) => { a.Encapsulate(b); return a; });
            var pivot = part.transform.TransformPoint(part.anchorPosition);
            var best = (score: -1f, screen: Vector2.zero, point: Vector3.zero);
            for (var i = 0; i <= 8; i++)
                for (var j = 0; j <= 8; j++)
                    for (var k = 0; k <= 8; k++)
                    {
                        var p = bounds.min + Vector3.Scale(bounds.size, new Vector3(i, j, k) / 8f);
                        var screen = (Vector2)cam.WorldToScreenPoint(p);
                        if (!Physics.Raycast(cam.ScreenPointToRay(screen), out var hit) || hit.collider.attachedArticulationBody != part)
                            continue;
                        var score = part.jointType == ArticulationJointType.PrismaticJoint ? 1f : Vector3.Distance(hit.point, pivot);
                        if (score > best.score)
                            best = (score, screen, hit.point);
                    }
            return best;
        }

        /// <summary>Where Drag would grab this part on screen, or null if it isn't visible.</summary>
        public static Vector2? GrabPoint(string prop, string part)
        {
            var cam = Camera.main;
            var wrapper = GameObject.Find(prop + Suffix);
            var body = wrapper != null ? wrapper.GetComponentsInChildren<ArticulationBody>().FirstOrDefault(b => b.name == part && !b.isRoot) : null;
            if (cam == null || body == null)
                return null;
            var best = BestGrab(cam, body);
            return best.score < 0 ? (Vector2?)null : best.screen;
        }

        /// <summary>Like Drag, but asks for joint travel (degrees, or metres for a slide): the pointer
        /// moves along the drag direction the grab mapped out, as far as that travel should take.</summary>
        public static string DragTravel(string prop, string part, float travel, float seconds = 1.5f)
        {
            var grabbed = Drag(prop, part, 0, 0, seconds);
            if (dragReport == "dragging")
                dragBy = dragger.DragPerUnitOnScreen * travel;
            return grabbed + string.Format(Inv, ", moving pointer by ({0:F0}, {1:F0}) px", dragBy.x, dragBy.y);
        }

        static void StepDrag()
        {
            if (!EditorApplication.isPlaying || dragPart == null || dragger == null)
            {
                EditorApplication.update -= StepDrag;
                return;
            }
            var t = (Time.time - dragStart) / dragSeconds;
            var cursor = dragFrom + dragBy * Mathf.Clamp01(t);
            dragger.DragTo(cursor);
            GolemCursorOverlay.Place(cursor, held: true);  // recordings show this cursor; scripts move no real one
            dragValues.Add(Joint(dragPart).value);
            if (t < 1f + 1f / dragSeconds)  // the drag, then a second's hold
                return;

            dragger.Release();
            GolemCursorOverlay.Place(cursor, held: false);
            EditorApplication.update -= StepDrag;
            var cam = Camera.main;
            var miss = Vector2.Distance(cam.WorldToScreenPoint(dragPart.transform.TransformPoint(dragLocal)), cursor);
            // Steps against the overall direction, ignoring sub-0.05 jitter (degrees or metres x 100).
            var unit = dragPart.jointType == ArticulationJointType.PrismaticJoint ? 0.0005f : 0.05f;
            var overall = Mathf.Sign(dragValues.Last() - dragValues.First());
            var reversals = dragValues.Zip(dragValues.Skip(1), (a, b) => b - a).Count(d => Mathf.Abs(d) > unit && Mathf.Sign(d) != overall);
            var drive = dragPart.xDrive;
            dragReport = string.Format(Inv,
                "{{\"part\": \"{0}\", \"from\": {1:F2}, \"to\": {2:F2}, \"min\": {3:F2}, \"max\": {4:F2}, \"limits\": [{5:F2}, {6:F2}], \"reversals\": {7}, \"cursor_miss_px\": {8:F0}, \"samples\": {9}}}",
                dragPart.name, dragValues.First(), dragValues.Last(), dragValues.Min(), dragValues.Max(), drive.lowerLimit, drive.upperLimit, reversals, miss, dragValues.Count);
        }

        public static string DragResult() => dragReport;

        /// <summary>Fire the ball launcher's shot bound to this key (V, T, B).</summary>
        public static string Fire(string key)
        {
            var launcher = Object.FindFirstObjectByType<GolemBallLauncher>();
            var shot = launcher != null ? launcher.shots.FirstOrDefault(s => s.key == key) : null;
            if (shot == null)
                return $"no shot bound to {key}";
            launcher.Fire(shot);
            return $"fired {key} at {shot.target.name}";
        }
    }
}
