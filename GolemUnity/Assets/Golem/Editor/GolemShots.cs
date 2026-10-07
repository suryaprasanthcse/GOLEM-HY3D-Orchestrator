using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Recorder;
using UnityEditor.Recorder.Encoder;
using UnityEditor.Recorder.Input;
using UnityEngine;

namespace Golem.EditorTools
{
    /// <summary>
    /// Recorded demo shots. Each shot is a timed script of camera views, key presses (O, C, ball
    /// shots) and drags shown with the drawn cursor (GolemCursorOverlay), run in game time while the
    /// Unity Recorder writes the Game view to MP4 at a constant frame rate: game time advances one
    /// frame per video frame, so the footage is smooth however slowly the Editor runs.
    /// An agent enters Play mode (with the auto ball shot off: shots fire it themselves), calls
    /// Record, and polls Status until it reports the file.
    /// </summary>
    public static class GolemShots
    {
        const int Width = 1920, Height = 1080;
        const float Fps = 30f;
        const float Approach = 0.6f;  // seconds the cursor glides to a grab point before pressing

        class Step
        {
            public float At;
            public Action Do;
        }

        static List<Step> steps = new List<Step>();
        static int next;
        static float start;
        static RecorderController recorder;
        static string output;

        public static string Status { get; private set; } = "idle";

        public static string[] Shots => new[] { "test", "stage", "cabinet", "laptop", "toolbox", "vault", "r2" };

        public static string Record(string shot, string folder)
        {
            if (!EditorApplication.isPlaying)
                return "enter Play mode first";
            if (recorder != null && recorder.IsRecording())
                return "already recording";
            steps = Script(shot);
            if (steps == null)
                return $"no shot named {shot}; shots: {string.Join(", ", Shots)}";
            steps.Sort((a, b) => a.At.CompareTo(b.At));

            Directory.CreateDirectory(folder);
            output = Path.Combine(folder, shot);
            File.Delete(output + ".status");
            var settings = ScriptableObject.CreateInstance<RecorderControllerSettings>();
            var movie = ScriptableObject.CreateInstance<MovieRecorderSettings>();
            movie.name = "GOLEM " + shot;
            movie.Enabled = true;
            movie.EncoderSettings = new CoreEncoderSettings
            {
                Codec = CoreEncoderSettings.OutputCodec.MP4,
                EncodingQuality = CoreEncoderSettings.VideoEncodingQuality.High,
            };
            movie.ImageInputSettings = new GameViewInputSettings { OutputWidth = Width, OutputHeight = Height };
            movie.AudioInputSettings.PreserveAudio = false;
            movie.OutputFile = output;  // the Recorder adds .mp4
            settings.AddRecorderSettings(movie);
            settings.SetRecordModeToManual();
            settings.FrameRate = Fps;
            settings.FrameRatePlayback = FrameRatePlayback.Constant;
            settings.CapFrameRate = true;
            recorder = new RecorderController(settings);
            recorder.PrepareRecording();
            if (!recorder.StartRecording())
                return Status = "the Recorder did not start (is a Game view open?)";

            GolemCursorOverlay.Hide();
            next = 0;
            start = Time.time;
            Status = "recording " + shot;
            EditorApplication.update -= Tick;
            EditorApplication.update += Tick;
            return $"recording {shot} ({steps[steps.Count - 1].At:F1} s) to {output}.mp4";
        }

        static void Tick()
        {
            if (!EditorApplication.isPlaying)
            {
                Finish("stopped early: Play mode ended");
                return;
            }
            var t = Time.time - start;
            while (next < steps.Count && steps[next].At <= t)
                steps[next++].Do();
            if (next >= steps.Count)
                Finish($"done: {output}.mp4");
        }

        static void Finish(string status)
        {
            EditorApplication.update -= Tick;
            if (recorder != null && recorder.IsRecording())
                recorder.StopRecording();
            GolemCursorOverlay.Hide();
            Status = status;
            // Also on disk, so a caller can wait without querying the Editor: each query compiles C#
            // on the main thread, which slowed recording to about one and a half frames a second.
            if (output != null)
                File.WriteAllText(output + ".status", status);
        }

        // ---- the shots -------------------------------------------------------------------------

        static List<Step> Script(string shot)
        {
            var s = new List<Step>();
            void At(float t, Action act) => s.Add(new Step { At = t, Do = act });
            void View(float t, int index, bool glide = false) => At(t, () =>
            {
                if (Camera.main != null && Camera.main.TryGetComponent(out GolemCameraRig rig))
                    rig.Show(index, instant: !glide);
            });
            void Key(float t, string key, string label) => At(t, () =>
            {
                GolemCursorOverlay.Key($"{key}   {label}");
                if (key == "O") GolemAudit.Open();
                else if (key == "C") GolemAudit.Close();
                else GolemAudit.Fire(key);
            });
            // The cursor glides to the grab point, then drags; the drag ends with a second's hold.
            void Drag(float t, string prop, string part, float travel, float seconds)
            {
                At(t - Approach, () =>
                {
                    if (GolemAudit.GrabPoint(prop, part) is Vector2 point)
                        GolemCursorOverlay.GlideTo(point, Approach * 0.9f);
                });
                At(t, () => GolemAudit.DragTravel(prop, part, travel, seconds));
            }
            void End(float t) => At(t, () => { });
            // A PNG of the final Game view frame, overlay included, next to the video: a way to check
            // a shot without a video decoder.
            void Still(float t, string name) => At(t, () => ScreenCapture.CaptureScreenshot(output + "_" + name + ".png"));

            switch (shot)
            {
                case "test":  // a short check of the Recorder, the cursor and a drag
                    View(0f, 1);
                    Drag(1.0f, "laptop", "lid", -60f, 1.2f);
                    Still(1.6f, "mid_drag");
                    End(4.0f);
                    break;
                case "stage":  // the five statues; the heavy cabinet takes the ball; everything opens and closes
                    View(0f, 0);
                    Key(1.5f, "B", "a 30 kg ball at the 66 kg filing cabinet");
                    Key(5.0f, "O", "open every part");
                    Key(11.0f, "C", "close every part");
                    End(16.0f);
                    break;
                case "cabinet":  // drawers pulled out by hand, then all four
                    View(0f, 4);
                    Drag(1.2f, "filing_cabinet", "drawer_top", 0.42f, 1.4f);
                    Drag(4.6f, "filing_cabinet", "drawer_bottom", 0.42f, 1.4f);
                    Key(8.2f, "O", "open every part");
                    End(11.0f);
                    break;
                case "laptop":  // the screen dragged shut, then open again
                    View(0f, 1);
                    Drag(1.2f, "laptop", "lid", -100f, 2.0f);
                    Drag(5.6f, "laptop", "lid", 100f, 2.0f);
                    End(9.6f);
                    break;
                case "toolbox":  // lid opened by hand, then the ball knocks the 17 kg toolbox over
                    View(0f, 2);
                    Drag(1.2f, "toolbox", "lid", 100f, 1.6f);
                    Key(5.0f, "T", "the same 30 kg ball at the 17 kg toolbox");
                    End(10.0f);
                    break;
                case "vault":  // the 2.7 t door swings on its hinge arm, then is dragged most of the way back
                    View(0f, 5);
                    Key(1.0f, "O", "open: the 2.7 t vault door");
                    Drag(7.0f, "vault_door", "door", -70f, 3.0f);
                    End(12.0f);
                    break;
                case "r2":  // R2: the laptop the Hyper3D bridge imported, after GOLEM (GOLEM > Import Folder)
                    Drag(1.2f, "laptop_r2", "lid", -100f, 2.0f);
                    Drag(5.6f, "laptop_r2", "lid", 100f, 2.0f);
                    End(9.6f);
                    break;
                default:
                    return null;
            }
            return s;
        }
    }
}
