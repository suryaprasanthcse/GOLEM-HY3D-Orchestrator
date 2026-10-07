using System;
using System.IO;
using System.Linq;
using UnityEditor;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Golem.EditorTools
{
    /// <summary>
    /// Headless demo for run_demo.py: import one split-props folder into a new scene, run the hinge
    /// self-test, then film every moving part opening one after another and closing together, rendered
    /// off screen with physics stepped by script (game time advances one frame per video frame).
    ///   Unity.exe -batchmode -projectPath GolemUnity -executeMethod Golem.EditorTools.GolemDemo.Run
    ///             -golemSplit &lt;split folder&gt; -golemFrames &lt;folder for PNG frames&gt;
    /// Exits 0 when the self-test passes and frames were written, 1 otherwise, 2 on an exception.
    /// </summary>
    public static class GolemDemo
    {
        const int Width = 1280, Height = 720;
        const float Fps = 60f;
        const int Substeps = 2;
        const float Settle = 0.6f, Stagger = 0.9f, HoldOpen = 2.2f, HoldClosed = 1.5f;

        public static void Run()
        {
            try
            {
                var split = Arg("-golemSplit");
                var frames = Arg("-golemFrames");
                EditorSceneManager.NewScene(NewSceneSetup.DefaultGameObjects, NewSceneMode.Single);
                Debug.Log("[GOLEM] DEMO import: " + GolemMenu.ImportFolder(split));
                // Film before the self-test: the self-test destroys its articulated copies, and stepping
                // PhysX by script after that crashed in the solver as soon as a part moved.
                var written = Film(frames);
                Debug.Log($"[GOLEM] DEMO wrote {written} frames to {frames}");
                var passed = GolemMenu.SelfTest();
                Debug.Log($"[GOLEM] DEMO self-test {(passed ? "PASS" : "FAIL")}");
                EditorApplication.Exit(passed && written > 0 ? 0 : 1);
            }
            catch (Exception e)
            {
                Debug.LogException(e);
                EditorApplication.Exit(2);
            }
        }

        static string Arg(string name)
        {
            var args = Environment.GetCommandLineArgs();
            var i = Array.IndexOf(args, name);
            if (i < 0 || i + 1 >= args.Length)
                throw new ArgumentException($"missing {name} <path>");
            return Path.GetFullPath(args[i + 1]);
        }

        static int Film(string folder)
        {
            Directory.CreateDirectory(folder);
            foreach (var old in Directory.GetFiles(folder, "frame_*.png"))
                File.Delete(old);
            var cam = Camera.main;
            if (cam == null)
                throw new InvalidOperationException("no main camera");
            cam.fieldOfView *= 1.2f;  // ImportFolder frames the closed prop tightly; leave room for open parts
            var parts = UnityEngine.Object.FindObjectsByType<ArticulationBody>()
                .Where(b => !b.isRoot && b.transform.root.name.EndsWith(" (GOLEM)"))
                .OrderByDescending(b => b.transform.position.y).ToArray();  // top first
            foreach (var part in parts)
                if (part.TryGetComponent(out GolemJointLink _))
                    GolemJointLink.Apply(part.transform);  // Awake doesn't run in edit mode

            // Rest = where the part starts; moved = whichever limit has more room (as in the self-test).
            var rest = parts.Select(p => p.xDrive.target).ToArray();
            var moved = parts.Select(p => p.xDrive.upperLimit - p.xDrive.target >= p.xDrive.target - p.xDrive.lowerLimit
                ? p.xDrive.upperLimit : p.xDrive.lowerLimit).ToArray();
            var closeAt = Settle + Stagger * parts.Length + HoldOpen;
            var end = closeAt + HoldClosed + 1f;

            var rt = new RenderTexture(Width, Height, 24) { antiAliasing = 4 };
            var image = new Texture2D(Width, Height, TextureFormat.RGB24, false);
            var previousTarget = cam.targetTexture;
            var previousMode = Physics.simulationMode;
            Physics.simulationMode = SimulationMode.Script;
            cam.targetTexture = rt;
            var count = 0;
            try
            {
                for (var t = 0f; t < end; t += 1f / Fps)
                {
                    for (var i = 0; i < parts.Length; i++)
                    {
                        var open = t >= Settle + Stagger * i && t < closeAt;
                        var goal = open ? moved[i] : rest[i];
                        var drive = parts[i].xDrive;
                        drive.target = Mathf.MoveTowards(drive.target, goal, GolemDragController.TopSpeed(parts[i]) / Fps);
                        parts[i].xDrive = drive;
                    }
                    for (var s = 0; s < Substeps; s++)
                        Physics.Simulate(1f / (Fps * Substeps));
                    cam.Render();
                    RenderTexture.active = rt;
                    image.ReadPixels(new Rect(0, 0, Width, Height), 0, 0);
                    RenderTexture.active = null;
                    File.WriteAllBytes(Path.Combine(folder, $"frame_{count++:D5}.png"), image.EncodeToPNG());
                }
            }
            finally
            {
                cam.targetTexture = previousTarget;
                Physics.simulationMode = previousMode;
                UnityEngine.Object.DestroyImmediate(image);
                rt.Release();
            }
            return count;
        }
    }
}
