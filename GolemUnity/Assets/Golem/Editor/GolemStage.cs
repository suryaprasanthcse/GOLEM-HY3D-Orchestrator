using System.Collections.Generic;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Golem.EditorTools
{
    /// <summary>
    /// GOLEM > Build Demo Stage: lays the imported props out in a presentation line (smallest to
    /// largest, left to right on screen), turns them into free physics bodies standing on a floor,
    /// adds a backdrop, key/fill lighting and a framed camera, and sets up the physics-proof ball.
    /// Run after Import Split Props.
    /// </summary>
    public static class GolemStage
    {
        const string Suffix = " (GOLEM)";
        static readonly string[] Order = { "laptop", "toolbox", "chest", "filing_cabinet", "vault_door" };
        const float Gap = 0.6f;          // metres between props
        const float VerticalFov = 30f;   // a longer lens flattens perspective and reads as cinematic
        const float Aspect = 16f / 9f;
        const float CloseUpPitch = 20f;  // degrees the close-ups look down
        const float CloseUpYaw = 30f;    // degrees the close-ups turn to one side, so slides and swings read in
                                         // depth; each takes the side where its neighbours block less of it
        const float CloseUpFill = 0.8f;  // fraction of the frame the prop's range of motion fills
        // Walls behind anchored props (FixtureWalls), in metres unless noted.
        const string WallPrefix = "GOLEM Wall ";
        const float WallThickness = 0.3f;
        const float WallBeyond = 0.6f;     // past the frame's sides
        const float WallAbove = 0.9f;      // above the frame's top
        const float WallClearance = 0.3f;  // gap left to a neighbouring prop
        const float WallEmbed = 0.02f;     // the wall's face sits this far inside the frame's back, so no seam shows
        const float DoorwayFill = 0.9f;    // rectangular doorway size, as a fraction of the moving parts' outline
        const float RoundTolerance = 0.15f; // a door whose width and height differ less than this is round
        const int RoundStrips = 96;        // horizontal strips that step a round doorway: at 24 the steps showed as stairs through the frame
        const float RecessWall = 0.1f;

        [MenuItem("GOLEM/Build Demo Stage")]
        public static void BuildMenu() => Build();

        /// <summary>For an agent driving the live Editor: build the stage and save the scene.</summary>
        public static string Build()
        {
            var props = Object.FindObjectsByType<Transform>(FindObjectsSortMode.None)
                .Where(t => t.parent == null && t.name.EndsWith(Suffix))
                .OrderBy(t => Rank(t.name.Replace(Suffix, "")))
                .ToList();
            if (props.Count == 0)
                return "no GOLEM props in the scene: run Import Split Props first";

            LayOut(props);
            // Free bodies, so an impact can move them by their real mass; fixtures (a vault door is
            // set in a wall) stay put. The vault's 2.8 t door outweighs its frame, so free-standing it topples.
            foreach (var prop in props)
                if (prop.GetComponentInChildren<ArticulationBody>() is { isRoot: true } body)
                    body.immovable = GolemMenu.SizeFor(prop.name.Replace(Suffix, ""))?.anchored == true;

            var all = BoundsOf(props);
            var surface = Material(new Color(0.16f, 0.16f, 0.17f), 0.25f);
            Floor(surface);
            Backdrop(Material(new Color(0.11f, 0.11f, 0.12f), 0.1f), all);
            FixtureWalls(props);
            Lights();
            Frame(all);
            Ball(props);
            Views(props);

            EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene());
            return $"stage built: {props.Count} props, {all.size.x:F2} m wide";
        }

        static int Rank(string name)
        {
            var index = System.Array.IndexOf(Order, name);
            return index >= 0 ? index : Order.Length;  // props not in the list go at the end
        }

        // TryGetComponent, not "??": the Editor's fake-null objects would fool the ?? operator.
        static T Ensure<T>(GameObject owner) where T : Component =>
            owner.TryGetComponent(out T existing) ? existing : owner.AddComponent<T>();

        /// <summary>One line along X, centred on the origin. The camera looks along -Z, so +X is screen left.</summary>
        static void LayOut(List<Transform> props)
        {
            var cursor = 0f;
            var centres = new List<float>();
            foreach (var prop in props)
            {
                var b = BoundsOf(new[] { prop });
                var width = b.size.x;
                var centre = cursor - width / 2;
                prop.position += new Vector3(centre - b.center.x, 0, -b.center.z);
                centres.Add(centre);
                cursor -= width + Gap;
            }
            var shift = -(centres.First() + centres.Last()) / 2;
            foreach (var prop in props)
                prop.position += new Vector3(shift, 0, 0);
        }

        static void Floor(Material material)
        {
            var floor = GameObject.Find("GOLEM Ground") ?? GameObject.CreatePrimitive(PrimitiveType.Plane);
            floor.name = "GOLEM Ground";
            floor.transform.position = Vector3.zero;
            floor.transform.localScale = new Vector3(10, 1, 10);
            floor.GetComponent<Renderer>().sharedMaterial = material;
        }

        static void Backdrop(Material material, Bounds all)
        {
            var wall = GameObject.Find("GOLEM Backdrop") ?? GameObject.CreatePrimitive(PrimitiveType.Quad);
            wall.name = "GOLEM Backdrop";
            wall.transform.position = new Vector3(all.center.x, 6f, all.min.z - 3f);
            // A Quad is visible from -Z only; the camera looks along -Z from +Z, so turn it around.
            wall.transform.rotation = Quaternion.Euler(0f, 180f, 0f);
            wall.transform.localScale = new Vector3(40, 12, 1);
            Object.DestroyImmediate(wall.GetComponent<Collider>());
            wall.GetComponent<Renderer>().sharedMaterial = material;
        }

        /// <summary>
        /// A concrete wall behind every anchored prop (golem_sizes.json "anchored"). The prop's body is
        /// immovable either way; the wall shows why, so a 4.5 t vault that never falls reads as built in
        /// rather than as a statue ignoring physics. The doorway follows the moving parts' outline (round
        /// for a round door) and hides behind the frame's rim, with a dark recess behind it, so the opened
        /// door shows a vault rather than bare wall. The wall stops WallClearance short of neighbouring props.
        /// Static box colliders; rebuilt on every stage build.
        /// </summary>
        static void FixtureWalls(List<Transform> props)
        {
            foreach (var old in Object.FindObjectsByType<Transform>(FindObjectsSortMode.None).Where(t => t.parent == null && t.name.StartsWith(WallPrefix)).ToArray())
                Object.DestroyImmediate(old.gameObject);
            var concrete = Material(new Color(0.34f, 0.34f, 0.35f), 0.08f);
            var dark = Material(new Color(0.03f, 0.03f, 0.035f), 0.05f);
            foreach (var prop in props)
            {
                var name = prop.name.Replace(Suffix, "");
                if (GolemMenu.SizeFor(name)?.anchored != true)
                    continue;
                var frame = PartBounds(prop, root: true);
                var moving = PartBounds(prop, root: false);
                if (frame.size == Vector3.zero || moving.size == Vector3.zero)
                    continue;

                // Props face +Z, so the wall goes behind the frame's back face.
                var front = frame.min.z + WallEmbed;
                var back = front - WallThickness;
                var left = frame.min.x - WallBeyond;
                var right = frame.max.x + WallBeyond;
                foreach (var other in props.Where(p => p != prop).Select(p => BoundsOf(new[] { p })))
                {
                    if (other.max.z < back || other.min.z > front)
                        continue;  // not level with the wall
                    if (other.min.x >= frame.max.x)
                        right = Mathf.Min(right, other.min.x - WallClearance);
                    else if (other.max.x <= frame.min.x)
                        left = Mathf.Max(left, other.max.x + WallClearance);
                }
                var top = frame.max.y + WallAbove;
                var c = moving.center;
                // A round door (a vault's) gets a round doorway; a square one would poke its corners out
                // past the frame's rim. Otherwise a rectangle at DoorwayFill of the door's outline.
                var round = Mathf.Abs(moving.extents.x - moving.extents.y) < RoundTolerance * Mathf.Max(moving.extents.x, moving.extents.y);
                var radius = Mathf.Max(moving.extents.x, moving.extents.y);
                var hx = round ? radius : DoorwayFill * moving.extents.x;
                var hy = round ? radius : DoorwayFill * moving.extents.y;

                var wall = new GameObject(WallPrefix + name).transform;
                void Slab(string part, Vector3 min, Vector3 max, Material material)
                {
                    if (max.x - min.x <= 0f || max.y - min.y <= 0f || max.z - min.z <= 0f)
                        return;
                    var cube = GameObject.CreatePrimitive(PrimitiveType.Cube);
                    cube.name = part;
                    cube.transform.SetParent(wall, false);
                    cube.transform.position = (min + max) / 2;
                    cube.transform.localScale = max - min;
                    cube.GetComponent<Renderer>().sharedMaterial = material;
                }
                // The wall, around the doorway.
                if (round)
                {
                    Slab("below", new Vector3(left, 0, back), new Vector3(right, c.y - radius, front), concrete);
                    Slab("above", new Vector3(left, c.y + radius, back), new Vector3(right, top, front), concrete);
                    // Strip by strip, each strip open as wide as the circle gets within it: the stepped edge
                    // never narrows the doorway, and stays behind the frame's rim (for the vault, within
                    // 0.86 m of the centre against a 0.75 m opening and a 1.1 m rim).
                    var strip = 2 * radius / RoundStrips;
                    for (var i = 0; i < RoundStrips; i++)
                    {
                        var y0 = c.y - radius + i * strip;
                        var y1 = y0 + strip;
                        var nearest = y0 <= c.y && c.y <= y1 ? 0f : Mathf.Min(Mathf.Abs(y0 - c.y), Mathf.Abs(y1 - c.y));
                        var half = Mathf.Sqrt(radius * radius - nearest * nearest);
                        Slab($"left {i}", new Vector3(left, y0, back), new Vector3(c.x - half, y1, front), concrete);
                        Slab($"right {i}", new Vector3(c.x + half, y0, back), new Vector3(right, y1, front), concrete);
                    }
                }
                else
                {
                    Slab("left", new Vector3(left, 0, back), new Vector3(c.x - hx, top, front), concrete);
                    Slab("right", new Vector3(c.x + hx, 0, back), new Vector3(right, top, front), concrete);
                    Slab("below", new Vector3(c.x - hx, 0, back), new Vector3(c.x + hx, c.y - hy, front), concrete);
                    Slab("above", new Vector3(c.x - hx, c.y + hy, back), new Vector3(c.x + hx, top, front), concrete);
                }
                // The recess behind the doorway, as deep as the doorway is tall.
                var depth = 2 * hy;
                var t = RecessWall;
                Slab("recess back", new Vector3(c.x - hx - t, c.y - hy - t, back - depth - t), new Vector3(c.x + hx + t, c.y + hy + t, back - depth), dark);
                Slab("recess left", new Vector3(c.x - hx - t, c.y - hy, back - depth), new Vector3(c.x - hx, c.y + hy, back), dark);
                Slab("recess right", new Vector3(c.x + hx, c.y - hy, back - depth), new Vector3(c.x + hx + t, c.y + hy, back), dark);
                Slab("recess floor", new Vector3(c.x - hx - t, c.y - hy - t, back - depth), new Vector3(c.x + hx + t, c.y - hy, back), dark);
                Slab("recess ceiling", new Vector3(c.x - hx - t, c.y + hy, back - depth), new Vector3(c.x + hx + t, c.y + hy + t, back), dark);
            }
        }

        /// <summary>Renderer bounds of a prop's root body, or of its moving parts, without hinge hardware
        /// (golem_assemble.py --hinge-arms): an arm reaching sideways would make a round door look oblong.</summary>
        static Bounds PartBounds(Transform prop, bool root)
        {
            var renderers = prop.GetComponentsInChildren<Renderer>()
                .Where(r => !r.name.StartsWith("golem_hinge") && r.GetComponentInParent<ArticulationBody>() is { } body && body.isRoot == root).ToArray();
            if (renderers.Length == 0)
                return new Bounds();
            var b = renderers[0].bounds;
            foreach (var r in renderers)
                b.Encapsulate(r.bounds);
            return b;
        }

        static void Lights()
        {
            var key = Object.FindObjectsByType<Light>(FindObjectsSortMode.None).FirstOrDefault(l => l.type == LightType.Directional && l.name != "GOLEM Fill");
            if (key == null)
                key = new GameObject("Directional Light").AddComponent<Light>();
            key.type = LightType.Directional;
            key.transform.rotation = Quaternion.Euler(42f, 150f, 0f);  // from front-right, above
            key.intensity = 1.3f;
            key.shadows = LightShadows.Soft;
            key.shadowStrength = 0.75f;
            RenderSettings.sun = key;

            var fill = Ensure<Light>(GameObject.Find("GOLEM Fill") ?? new GameObject("GOLEM Fill"));
            fill.type = LightType.Directional;
            fill.transform.rotation = Quaternion.Euler(20f, -140f, 0f);  // softer, from front-left
            fill.intensity = 0.35f;
            fill.shadows = LightShadows.None;
        }

        /// <summary>Fit the whole line in a 16:9 frame with a little margin, from slightly above.</summary>
        static void Frame(Bounds all)
        {
            var cam = Camera.main;
            if (cam == null)
                return;
            cam.fieldOfView = VerticalFov;
            cam.nearClipPlane = 0.05f;
            var halfV = VerticalFov * 0.5f * Mathf.Deg2Rad;
            var halfH = Mathf.Atan(Mathf.Tan(halfV) * 16f / 9f);
            var distance = Mathf.Max(all.size.x * 0.56f / Mathf.Tan(halfH), all.size.y * 0.7f / Mathf.Tan(halfV)) + all.extents.z;
            var target = all.center + Vector3.down * 0.08f * all.size.y;
            cam.transform.position = target + new Vector3(0, 0.25f * distance, distance);
            cam.transform.LookAt(target);
        }

        static void Ball(List<Transform> props)
        {
            var ball = GameObject.Find("GOLEM Ball") ?? GameObject.CreatePrimitive(PrimitiveType.Sphere);
            ball.name = "GOLEM Ball";
            const float radius = 0.2f;
            ball.transform.localScale = Vector3.one * radius * 2;
            ball.GetComponent<Renderer>().sharedMaterial = Material(new Color(0.75f, 0.12f, 0.08f), 0.7f);
            var rb = Ensure<Rigidbody>(ball);
            rb.mass = 30f;  // a heavy ball, so the difference between light and heavy props shows
            rb.collisionDetectionMode = CollisionDetectionMode.ContinuousDynamic;

            // Same ball, same speed: it shakes the heavy filing cabinet and knocks the 17 kg toolbox onto its
            // side. The vault door is anchored (a fixture), so its shot only shows the door holding.
            // The first shot (the cabinet) fires by itself shortly after Play; each can be repeated with its key.
            var launcherObject = GameObject.Find("GOLEM Ball Launcher") ?? new GameObject("GOLEM Ball Launcher");
            var launcher = Ensure<GolemBallLauncher>(launcherObject);
            launcher.ball = rb;
            var shots = new List<GolemBallLauncher.Shot>();
            foreach (var (name, key, side) in new[] { ("filing_cabinet", "B", -1.4f), ("toolbox", "T", -1.2f), ("vault_door", "V", 1.4f) })
            {
                var target = props.FirstOrDefault(p => p.name == name + Suffix);
                if (target == null)
                    continue;
                var c = BoundsOf(new[] { target }).center;
                shots.Add(new GolemBallLauncher.Shot { key = key, target = target, spawn = new Vector3(c.x + side, radius, 3f), speed = 7f });
            }
            launcher.shots = shots.ToArray();
            if (shots.Count > 0)
                ball.transform.position = shots[0].spawn;
        }

        /// <summary>Camera presets on the main camera: view 0 is the wide shot just framed, then one
        /// three-quarter close-up per prop in line order, fitted to everything the prop's parts sweep
        /// through between their joint limits (a door swung open, drawers pulled out).</summary>
        static void Views(List<Transform> props)
        {
            var cam = Camera.main;
            if (cam == null)
                return;
            var rig = Ensure<GolemCameraRig>(cam.gameObject);
            var views = new List<GolemCameraRig.View>
            {
                new GolemCameraRig.View { name = "wide", position = cam.transform.position, rotation = cam.transform.rotation },
            };
            var swept = props.Select(SweptCorners).ToList();
            var boxes = swept.Select(corners =>
            {
                var box = new Bounds(corners[0], Vector3.zero);
                foreach (var p in corners)
                    box.Encapsulate(p);
                return box;
            }).ToList();
            for (var i = 0; i < props.Count; i++)
            {
                var target = boxes[i];
                var hingeSide = HingeSide(props[i]);
                // A door hung from hinge hardware (golem_assemble.py --hinge-arms) is shown from its hinge
                // side, or the open door hides the arm that holds it; other side hinges from the far side.
                var hardware = props[i].GetComponentsInChildren<Transform>().Any(t => t.name.StartsWith("golem_hinge"));
                var preferred = hardware ? hingeSide : -hingeSide;
                // Showing the hardware outweighs one sight line grazing a neighbour's swept box (the vault's
                // hinge side grazed the cabinet's pulled-out drawers), not two.
                var sideWeight = hardware ? 15 : 1;
                var best = (blocked: int.MaxValue, position: Vector3.zero, rotation: Quaternion.identity);
                var scores = new List<string>();
                foreach (var yaw in new[] { CloseUpYaw, -CloseUpYaw })  // ties go to the first
                {
                    var rotation = Quaternion.Euler(CloseUpPitch, 180f + yaw, 0f);  // yaw 180 looks along -Z, at the fronts
                    var position = target.center - rotation * Vector3.forward * FitDistance(swept[i], target.center, rotation);
                    // Neighbours in the way count most. Then a side hinge's preferred side: the far side, as
                    // from the hinge side an open door stands across its own doorway, unless it has hinge hardware.
                    var blocked = 10 * SightLinesBlocked(position, target, boxes.Where((_, j) => j != i))
                                  + (hingeSide != 0 && Mathf.Sign(position.x - target.center.x) != preferred ? sideWeight : 0);
                    scores.Add($"{(yaw > 0 ? "left" : "right")} {blocked}");
                    if (blocked < best.blocked)
                        best = (blocked, position, rotation);
                }
                Debug.Log($"[GOLEM] close-up {props[i].name.Replace(Suffix, "")}: {string.Join(", ", scores)} (10 per sight line through a neighbour; a side hinge's less useful side 1, or 15 with hinge hardware)");
                views.Add(new GolemCameraRig.View { name = props[i].name.Replace(Suffix, ""), position = best.position, rotation = best.rotation });
            }
            rig.views = views.ToArray();
        }

        /// <summary>World X side (+1 or -1) of a hinge set off to one side of the prop's body, such as a
        /// door's; 0 for hinges near the middle (lids hinge along the back) and for slides.</summary>
        static float HingeSide(Transform prop)
        {
            var renderers = prop.GetComponentsInChildren<Renderer>()
                .Where(r => r.GetComponentInParent<ArticulationBody>() is { isRoot: true }).ToArray();
            if (renderers.Length == 0)
                return 0;
            var body = renderers[0].bounds;
            foreach (var r in renderers)
                body.Encapsulate(r.bounds);
            foreach (var part in prop.GetComponentsInChildren<ArticulationBody>().Where(b => !b.isRoot && b.jointType == ArticulationJointType.RevoluteJoint))
            {
                var offset = part.transform.TransformPoint(part.anchorPosition).x - body.center.x;
                if (Mathf.Abs(offset) > 0.5f * body.extents.x)
                    return Mathf.Sign(offset);
            }
            return 0;
        }

        /// <summary>How many sight lines from the camera to the target's centre and corners pass
        /// through another prop's swept bounds before reaching the target.</summary>
        static int SightLinesBlocked(Vector3 camera, Bounds target, IEnumerable<Bounds> others)
        {
            var points = new List<Vector3> { target.center };
            for (var i = 0; i < 8; i++)
                points.Add(new Vector3((i & 1) == 0 ? target.min.x : target.max.x, (i & 2) == 0 ? target.min.y : target.max.y, (i & 4) == 0 ? target.min.z : target.max.z));
            var otherList = others.ToList();
            return points.Count(p =>
            {
                var ray = new Ray(camera, p - camera);
                var reach = Vector3.Distance(camera, p);
                return otherList.Any(b => b.IntersectRay(ray, out var hit) && hit < reach);
            });
        }

        /// <summary>Corners of every part's bounds at several points across its joint's travel.</summary>
        static List<Vector3> SweptCorners(Transform prop)
        {
            var corners = new List<Vector3>();
            foreach (var renderer in prop.GetComponentsInChildren<Renderer>())
            {
                var body = renderer.GetComponentInParent<ArticulationBody>();
                var b = renderer.bounds;
                var box = new List<Vector3>();
                for (var i = 0; i < 8; i++)
                    box.Add(new Vector3((i & 1) == 0 ? b.min.x : b.max.x, (i & 2) == 0 ? b.min.y : b.max.y, (i & 4) == 0 ? b.min.z : b.max.z));
                if (body == null || body.isRoot)
                {
                    corners.AddRange(box);
                    continue;
                }
                // In the Editor every part sits at its generated pose, joint travel 0.
                var drive = body.xDrive;
                for (var k = 0; k <= 6; k++)
                {
                    var travel = Mathf.Lerp(drive.lowerLimit, drive.upperLimit, k / 6f);
                    corners.AddRange(box.Select(p => GolemDragController.Moved(body, p, travel)));
                }
            }
            return corners;
        }

        /// <summary>How far back along the view a camera must stand to fit every point in CloseUpFill of a 16:9 frame.</summary>
        static float FitDistance(List<Vector3> points, Vector3 centre, Quaternion rotation)
        {
            var forward = rotation * Vector3.forward;
            var right = rotation * Vector3.right;
            var up = rotation * Vector3.up;
            var tanV = Mathf.Tan(VerticalFov * 0.5f * Mathf.Deg2Rad) * CloseUpFill;
            var tanH = Mathf.Tan(VerticalFov * 0.5f * Mathf.Deg2Rad) * Aspect * CloseUpFill;
            var distance = 0f;
            foreach (var p in points)
            {
                var q = p - centre;
                var depth = Vector3.Dot(q, forward);  // a point beyond the centre is farther from the camera
                distance = Mathf.Max(distance, Mathf.Abs(Vector3.Dot(q, right)) / tanH - depth, Mathf.Abs(Vector3.Dot(q, up)) / tanV - depth);
            }
            return distance;
        }

        /// <summary>For an agent: jump the main camera to preset view (0 = wide) without Play mode.</summary>
        public static string ShowView(int index)
        {
            var cam = Camera.main;
            if (cam == null || !cam.TryGetComponent(out GolemCameraRig rig) || index < 0 || index >= rig.views.Length)
                return "no such view";
            rig.Show(index, true);
            return $"view {index}: {rig.views[index].name}";
        }

        /// <summary>Render the main camera straight into a PNG at the given size, with the camera's
        /// aspect matched to it (the Editor's tiled screenshot misaligns when the Game view's aspect differs).</summary>
        public static string Capture(string path, int width = 1920, int height = 1080)
        {
            var cam = Camera.main;
            if (cam == null)
                return "no main camera";
            var target = new RenderTexture(width, height, 24, RenderTextureFormat.ARGB32) { antiAliasing = 4 };
            var previousTarget = cam.targetTexture;
            var previousAspect = cam.aspect;
            cam.targetTexture = target;
            cam.aspect = (float)width / height;
            cam.Render();
            var active = RenderTexture.active;
            RenderTexture.active = target;
            var image = new Texture2D(width, height, TextureFormat.RGB24, false);
            image.ReadPixels(new Rect(0, 0, width, height), 0, 0);
            image.Apply();
            RenderTexture.active = active;
            cam.targetTexture = previousTarget;
            cam.aspect = previousAspect;
            cam.ResetAspect();
            System.IO.File.WriteAllBytes(path, image.EncodeToPNG());
            Object.DestroyImmediate(image);
            target.Release();
            Object.DestroyImmediate(target);
            return $"captured {width}x{height} to {path}";
        }

        static Material Material(Color colour, float smoothness)
        {
            var material = new Material(Shader.Find("Universal Render Pipeline/Lit"));
            material.SetColor("_BaseColor", colour);
            material.SetFloat("_Smoothness", smoothness);
            return material;
        }

        static Bounds BoundsOf(IEnumerable<Transform> roots)
        {
            var renderers = roots.SelectMany(r => r.GetComponentsInChildren<Renderer>()).ToArray();
            if (renderers.Length == 0)
                return new Bounds();
            var b = renderers[0].bounds;
            foreach (var r in renderers)
                b.Encapsulate(r.bounds);
            return b;
        }
    }
}
