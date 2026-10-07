using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;
using UnityEngine.SceneManagement;

namespace Golem.EditorTools
{
    /// <summary>
    /// GOLEM > Import Split Props: brings every prop from ../assets/split/&lt;name&gt;/ (the cutter's
    /// &lt;name&gt;_split.glb + &lt;name&gt;_joints.json) into the open scene as an articulated object.
    ///
    /// Command line (builds SampleScene, runs the hinge self-test, saves, exits 0 on success):
    ///   Unity.exe -batchmode -projectPath GolemUnity -executeMethod Golem.EditorTools.GolemMenu.BuildFromCommandLine
    /// </summary>
    public static class GolemMenu
    {
        const string ScenePath = "Assets/Scenes/SampleScene.unity";
        const string PropsFolder = "Assets/GolemProps";
        const string Suffix = " (GOLEM)";
        const float Spacing = 3.5f;

        static string SplitRoot => Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "assets", "split"));
        static string SizesPath => Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "golem_sizes.json"));

        [System.Serializable] internal class PropSize { public string name; public float size_m; public float density; public bool anchored; }
        [System.Serializable] class SizeTable { public PropSize[] props; }

        /// <param name="choicesPath">golem_auto.py's &lt;name&gt;_choices.json: its size_m and density are used
        /// when golem_sizes.json has no entry for this prop.</param>
        internal static PropSize SizeFor(string name, string choicesPath = null)
        {
            var table = File.Exists(SizesPath) ? JsonUtility.FromJson<SizeTable>(File.ReadAllText(SizesPath)) : null;
            var entry = table?.props?.FirstOrDefault(p => p.name == name);
            if (entry == null && choicesPath != null && File.Exists(choicesPath))
            {
                var chosen = JsonUtility.FromJson<PropSize>(File.ReadAllText(choicesPath));
                if (chosen.size_m > 0f && chosen.density > 0f)
                {
                    Debug.Log($"[GOLEM] {name}: size {chosen.size_m} m and density {chosen.density} kg/m3 from {Path.GetFileName(choicesPath)}");
                    return chosen;
                }
            }
            if (entry == null)
                Debug.LogWarning($"[GOLEM] {name}: no entry in golem_sizes.json, keeping generated size");
            return entry;
        }

        [MenuItem("GOLEM/Import Split Props")]
        public static void ImportSplitPropsMenu()
        {
            var count = ImportAll();
            EditorSceneManager.MarkSceneDirty(SceneManager.GetActiveScene());
            EditorUtility.DisplayDialog("GOLEM", $"Imported {count} prop(s) from\n{SplitRoot}", "OK");
        }

        [MenuItem("GOLEM/Run Hinge Self-Test")]
        public static void SelfTestMenu()
        {
            var passed = SelfTest();
            EditorUtility.DisplayDialog("GOLEM", passed ? "All joints open the right way." : "A joint failed: see the Console.", "OK");
        }

        /// <summary>For an agent driving the live Editor: import, self-test and save the open scene,
        /// with no dialogs and without quitting Unity.</summary>
        public static string ImportAndTest()
        {
            var count = ImportAll();
            var passed = count > 0 && SelfTest();
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene());
            return $"{count} prop(s), self-test {(passed ? "PASS" : "FAIL")}";
        }

        /// <summary>For an agent: import every prop, self-test, then build the demo stage.</summary>
        public static string RebuildAll() => ImportAndTest() + "; " + GolemStage.Build();

        /// <summary>
        /// For metrics: rebuild everything and time each stage. With fresh, each prop's imported
        /// model is deleted first, so its time includes the full glTFast import a new asset needs.
        /// Returns JSON: per-prop import+articulation ms, then self-test and stage ms.
        /// </summary>
        public static string TimedRebuild(bool fresh)
        {
            var timings = new System.Collections.Generic.List<string>();
            var total = System.Diagnostics.Stopwatch.StartNew();
            var index = 0;
            foreach (var dir in Directory.GetDirectories(SplitRoot).OrderBy(d => d))
            {
                var name = Path.GetFileName(dir);
                var glb = Path.Combine(dir, $"{name}_split.glb");
                var json = Path.Combine(dir, $"{name}_joints.json");
                if (!File.Exists(glb) || !File.Exists(json))
                    continue;
                var old = GameObject.Find(name + Suffix);
                if (old != null)
                    Object.DestroyImmediate(old);  // before any asset deletion, see ImportProp
                if (fresh)
                    AssetDatabase.DeleteAsset($"{PropsFolder}/{name}");
                var watch = System.Diagnostics.Stopwatch.StartNew();
                ImportProp(name, glb, json, new Vector3(index++ * Spacing, 0, 0));
                timings.Add($"\"{name}\": {watch.ElapsedMilliseconds}");
            }
            EnsureStage();
            var test = System.Diagnostics.Stopwatch.StartNew();
            var passed = SelfTest();
            var testMs = test.ElapsedMilliseconds;
            var stage = System.Diagnostics.Stopwatch.StartNew();
            GolemStage.Build();
            var report = "{\"import_and_articulate_ms\": {" + string.Join(", ", timings) + "}, " +
                         $"\"self_test_ms\": {testMs}, \"self_test_passed\": {(passed ? "true" : "false")}, " +
                         $"\"stage_ms\": {stage.ElapsedMilliseconds}, \"total_ms\": {total.ElapsedMilliseconds}, \"fresh_import\": {(fresh ? "true" : "false")}}}";
            // Also written to a file: fresh imports outlast the Pipeline server's 5 s main-thread wait,
            // so a caller may never receive the return value.
            File.WriteAllText(TimingPath, report);
            Debug.Log($"[GOLEM] TIMING {report}");
            return report;
        }

        public static string TimingPath => Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "assets", "unity_timing.json"));

        public static void BuildFromCommandLine()
        {
            EditorSceneManager.OpenScene(ScenePath);
            var count = ImportAll();
            var passed = count > 0 && SelfTest();
            EditorSceneManager.SaveScene(SceneManager.GetActiveScene());
            Debug.Log($"[GOLEM] BUILD {(passed ? "PASS" : "FAIL")}: {count} prop(s)");
            EditorApplication.Exit(passed ? 0 : 1);
        }

        [MenuItem("GOLEM/Import Folder...")]
        public static void ImportFolderMenu()
        {
            var folder = EditorUtility.OpenFolderPanel("GOLEM: folder of split props (golem_auto.py --out)", SplitRoot, "");
            if (string.IsNullOrEmpty(folder))
                return;
            var report = ImportFolder(folder);
            Debug.Log($"[GOLEM] {report}");
            EditorUtility.DisplayDialog("GOLEM", report.Replace("; ", "\n"), "OK");
        }

        /// <summary>For an agent: import every prop under a split folder other than ../assets/split (for
        /// example golem_auto.py's output for a newly generated model) into the open scene, without saving.
        /// Returns each prop's parts with their masses.</summary>
        public static string ImportFolder(string splitRoot)
        {
            var count = ImportAll(splitRoot);
            var props = Directory.GetDirectories(splitRoot).Select(Path.GetFileName)
                .Select(name => GameObject.Find(name + Suffix))
                .Where(prop => prop != null).ToList();
            // Frame what was imported, three-quarter view with a 30 degree lens: EnsureStage frames
            // for a whole stage (2 m at least), where a lone laptop is a speck.
            var renderers = props.SelectMany(p => p.GetComponentsInChildren<Renderer>()).ToArray();
            if (Camera.main != null && renderers.Length > 0)
            {
                var b = renderers[0].bounds;
                foreach (var r in renderers)
                    b.Encapsulate(r.bounds);
                var size = Mathf.Max(b.size.x, b.size.y, b.size.z);
                Camera.main.fieldOfView = 30f;
                Camera.main.nearClipPlane = 0.01f;
                Camera.main.transform.position = b.center + new Vector3(-1.1f, 1.0f, 2.0f) * size;
                Camera.main.transform.LookAt(b.center);
            }
            var lines = props
                .Select(prop => prop.name + ": " + string.Join(", ", prop.GetComponentsInChildren<ArticulationBody>()
                    .Select(b => $"{b.name} {b.mass:F2} kg{(b.isRoot ? "" : $" ({b.jointType}, limits {b.xDrive.lowerLimit:F1}..{b.xDrive.upperLimit:F1})")}")));
            return $"{count} prop(s) from {splitRoot}: " + string.Join("; ", lines);
        }

        static int ImportAll(string splitRoot = null)
        {
            splitRoot ??= SplitRoot;
            if (!Directory.Exists(splitRoot))
            {
                Debug.LogError($"[GOLEM] no split props folder: {splitRoot}");
                return 0;
            }
            var count = 0;
            foreach (var dir in Directory.GetDirectories(splitRoot).OrderBy(d => d))
            {
                var name = Path.GetFileName(dir);
                var glb = Path.Combine(dir, $"{name}_split.glb");
                var json = Path.Combine(dir, $"{name}_joints.json");
                if (!File.Exists(glb) || !File.Exists(json))
                    continue;
                if (ImportProp(name, glb, json, new Vector3(count * Spacing, 0, 0)))
                    count++;
            }
            EnsureStage();
            return count;
        }

        static bool ImportProp(string name, string glb, string json, Vector3 position)
        {
            // Remove the previous instance BEFORE reimporting: reimporting swaps the meshes under its
            // MeshColliders, and PhysX crashes the Editor detaching them from articulation links.
            var old = GameObject.Find(name + Suffix);
            if (old != null)
                Object.DestroyImmediate(old);

            var folder = $"{PropsFolder}/{name}";
            Directory.CreateDirectory(folder);
            var glbAsset = $"{folder}/{name}_split.glb";
            File.Copy(json, $"{folder}/{name}_joints.json", true);
            // Only reimport a model that actually changed: faster, and nothing else in the scene can
            // be holding meshes that are about to be replaced.
            if (!File.Exists(glbAsset) || !File.ReadAllBytes(glbAsset).SequenceEqual(File.ReadAllBytes(glb)))
            {
                File.Copy(glb, glbAsset, true);
                AssetDatabase.ImportAsset(glbAsset, ImportAssetOptions.ForceSynchronousImport | ImportAssetOptions.ForceUpdate);
            }

            var model = AssetDatabase.LoadAssetAtPath<GameObject>(glbAsset);
            if (model == null)
            {
                Debug.LogError($"[GOLEM] {name}: glTFast did not import {glbAsset} (is com.unity.cloud.gltfast installed?)");
                return false;
            }

            // The wrapper stands in for the glTF scene root, so the spec's coordinates mean the same
            // thing here. (glTFast folds a glTF's single root node into the imported object and
            // renames it after the file, so that object is the root part, keeping its own offset.)
            var wrapper = new GameObject(name + Suffix).transform;
            wrapper.position = position;
            var instance = (GameObject)PrefabUtility.InstantiatePrefab(model);
            PrefabUtility.UnpackPrefabInstance(instance, PrefabUnpackMode.Completely, InteractionMode.AutomatedAction);
            instance.transform.SetParent(wrapper, false);

            // Real-world size: generated meshes arrive ~2 units across; scale uniformly to size_m.
            var size = SizeFor(name, Path.Combine(Path.GetDirectoryName(json), $"{name}_choices.json"));
            var renderers = instance.GetComponentsInChildren<Renderer>();
            if (size != null && renderers.Length > 0)
            {
                var b = renderers[0].bounds;
                foreach (var r in renderers)
                    b.Encapsulate(r.bounds);
                wrapper.localScale = Vector3.one * (size.size_m / Mathf.Max(b.size.x, b.size.y, b.size.z));
            }
            if (renderers.Length > 0)  // stand the prop on the ground
                wrapper.position += Vector3.up * -renderers.Min(r => r.bounds.min.y);

            var spec = JsonUtility.FromJson<GolemSpec>(File.ReadAllText(json));
            GolemArticulator.Build(wrapper, spec, size != null ? size.density : 500f);
            Debug.Log($"[GOLEM] {name}: {spec.joints.Length} joint(s) built");
            return true;
        }

        /// <summary>Ground, drag controller, and a camera looking at the props' fronts (+Z in Unity, as in glTF).</summary>
        static void EnsureStage()
        {
            if (GameObject.Find("GOLEM Ground") == null)
            {
                var ground = GameObject.CreatePrimitive(PrimitiveType.Plane);
                ground.name = "GOLEM Ground";
                ground.transform.localScale = new Vector3(5, 1, 5);
            }
            var cam = Camera.main;
            if (cam == null)
                return;
            if (!cam.TryGetComponent(out GolemDragController _))
                cam.gameObject.AddComponent<GolemDragController>();

            var renderers = Object.FindObjectsByType<Renderer>(FindObjectsSortMode.None)
                .Where(r => r.transform.root.name.EndsWith(Suffix)).ToArray();
            if (renderers.Length == 0)
                return;
            var bounds = renderers[0].bounds;
            foreach (var r in renderers)
                bounds.Encapsulate(r.bounds);
            var size = Mathf.Max(bounds.size.x, bounds.size.y, 2f);
            cam.transform.position = bounds.center + new Vector3(-0.35f * size, 0.55f * size, 1.35f * size);
            cam.transform.LookAt(bounds.center);
        }

        /// <summary>
        /// Simulates each prop headlessly: drive every joint 70 degrees (toward whichever limit has
        /// room: open for a lid, closed for a standing laptop screen) and check that it turned that
        /// far and moved the right way: positive rotation must move the part toward its hinge side
        /// (a lid opening swings back over its hinge; a screen closing tips forward). That catches a
        /// wrong axis sign after the glTF -> Unity conversion. Runs on copies, so the scene keeps its pose.
        /// </summary>
        public static bool SelfTest()
        {
            var passed = true;
            var tested = 0;
            var previous = Physics.simulationMode;
            Physics.simulationMode = SimulationMode.Script;
            foreach (var original in Object.FindObjectsByType<Transform>(FindObjectsSortMode.None).Where(t => t.parent == null && t.name.EndsWith(Suffix)))
            {
                var copy = Object.Instantiate(original.gameObject, original.position + new Vector3(0, 0, -50), original.rotation);
                foreach (var root in copy.GetComponentsInChildren<ArticulationBody>().Where(b => b.isRoot))
                    root.immovable = true;  // the copy floats away from the floor; pin it while testing joints
                var parts = copy.GetComponentsInChildren<ArticulationBody>().Where(b => !b.isRoot).ToArray();
                foreach (var link in copy.GetComponentsInChildren<GolemJointLink>())
                    GolemJointLink.Apply(link.transform);
                var before = parts.Select(p => (rotation: p.transform.rotation, center: Center(p),
                    pivot: p.transform.TransformPoint(p.anchorPosition),
                    axis: p.transform.rotation * p.anchorRotation * Vector3.right)).ToArray();
                var moves = new float[parts.Length];
                for (var i = 0; i < parts.Length; i++)
                {
                    var drive = parts[i].xDrive;
                    var slide = parts[i].jointType == ArticulationJointType.PrismaticJoint;
                    var upRoom = drive.upperLimit - drive.target;
                    var downRoom = drive.target - drive.lowerLimit;
                    // Hinges move 70 degrees, slides 70% of their travel, toward whichever limit has room.
                    moves[i] = slide
                        ? (upRoom >= downRoom ? 0.7f * upRoom : -0.7f * downRoom)
                        : (upRoom >= 45f ? Mathf.Min(70f, upRoom) : -Mathf.Min(70f, downRoom));
                    drive.target += moves[i];
                    parts[i].xDrive = drive;
                }
                for (var step = 0; step < 200; step++)
                    Physics.Simulate(0.02f);

                for (var i = 0; i < parts.Length; i++)
                {
                    var b = before[i];
                    var slide = parts[i].jointType == ArticulationJointType.PrismaticJoint;
                    var shift = Center(parts[i]) - b.center;
                    var outward = parts[i].TryGetComponent(out GolemJointLink link) ? link.outward : Vector3.zero;
                    // Positive travel moves the part toward its hinge side (or slide direction); without that data, a lid must rise.
                    var rightWay = outward != Vector3.zero ? Mathf.Sign(Vector3.Dot(shift, outward)) == Mathf.Sign(moves[i]) : shift.y > 0;
                    // Where the drag controller assumes the part goes for this travel: along the anchor's X axis,
                    // or around it by Quaternion.AngleAxis. If physics disagrees, dragging would feel inverted.
                    var predicted = slide
                        ? b.axis * moves[i]
                        : b.pivot + Quaternion.AngleAxis(moves[i], b.axis) * (b.center - b.pivot) - b.center;
                    var dragAgrees = Vector3.Angle(predicted, shift) < 30f;
                    float amount = slide ? shift.magnitude : Quaternion.Angle(b.rotation, parts[i].transform.rotation);
                    var reached = slide ? Mathf.Abs(amount - Mathf.Abs(moves[i])) < 0.25f * Mathf.Abs(moves[i])
                                        : Mathf.Abs(amount - Mathf.Abs(moves[i])) < 10f;
                    var ok = rightWay && dragAgrees && reached;
                    passed &= ok;
                    tested++;
                    var unit = slide ? "" : " deg";
                    Debug.Log($"[GOLEM] SELFTEST {original.name}/{parts[i].name}: {(slide ? "slid" : "turned")} {amount:F2}{unit} " +
                              $"of {moves[i]:+0.00;-0.00}{unit}, {(rightWay ? "right way" : "WRONG WAY")}, " +
                              $"drag {(dragAgrees ? "agrees" : "DISAGREES")} -> {(ok ? "PASS" : "FAIL")}");
                }
                Object.DestroyImmediate(copy);
            }
            Physics.simulationMode = previous;
            if (tested == 0)
                Debug.LogError("[GOLEM] SELFTEST found no moving parts to test");
            return passed && tested > 0;
        }

        static Vector3 Center(ArticulationBody part)
        {
            var renderers = part.GetComponentsInChildren<Renderer>();
            if (renderers.Length == 0)
                return part.transform.position;
            var bounds = renderers[0].bounds;
            foreach (var r in renderers)
                bounds.Encapsulate(r.bounds);
            return bounds.center;
        }
    }
}
