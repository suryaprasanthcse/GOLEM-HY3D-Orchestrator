using System;
using System.Collections.Generic;
using System.IO;
using UnityEditor;
using UnityEditor.Build;
using UnityEditor.Build.Reporting;
using UnityEditor.SceneManagement;
using UnityEngine;

namespace Golem.EditorTools
{
    /// <summary>
    /// Web (WebGL) build of the demo stage. glTFast stores each prop texture as uncompressed 2048²
    /// RGBA (about 22 MB each, 15 of them), and Unity's texture compression never touches textures
    /// a scripted importer made. So before building, this writes a 1024² DXT1 copy of every prop
    /// texture, and a material copy that uses it, into Assets/GolemProps/_web (gitignored with the
    /// props), saves a copy of SampleScene that points at those materials, and builds that copy.
    /// SampleScene and the imported models are not changed.
    /// The build blocks the Editor, so an agent calls Start and polls the status file it writes.
    /// </summary>
    public static class GolemWebBuild
    {
        const string SourceScene = "Assets/Scenes/SampleScene.unity";
        const string PropsFolder = "Assets/GolemProps/";
        const string WebFolder = "Assets/GolemProps/_web";
        const string WebScene = WebFolder + "/SampleScene_Web.unity";
        const int MaxSize = 1024;
        const string Controls =
            "Click the scene, then drag a lid, door or drawer with the mouse. O opens everything, C closes it. " +
            "B, T and V fire the ball at the cabinet, toolbox and vault. Keys 0-5 move the camera. " +
            "Source and video: <a href=\"https://github.com/suryaprasanthcse/Golem_Arcade_Game_tech\">github.com/suryaprasanthcse/Golem_Arcade_Game_tech</a>";

        public static string DefaultOutput =>
            Path.GetFullPath(Path.Combine(Application.dataPath, "..", "..", "..", "golem_webgl"));

        public static string StatusPath(string outDir) => outDir + ".status";

        [MenuItem("GOLEM/Build WebGL")]
        public static void BuildMenu() => Debug.Log(Build(DefaultOutput));

        /// <summary>Returns at once; the build runs on the next Editor tick and writes outDir.status.</summary>
        public static string Start(string outDir)
        {
            File.WriteAllText(StatusPath(outDir), "queued");
            EditorApplication.delayCall += () =>
            {
                string result;
                try { result = Build(outDir); }
                catch (Exception e) { result = "error: " + e; }
                File.WriteAllText(StatusPath(outDir), result);
            };
            return "queued";
        }

        public static string Build(string outDir)
        {
            if (EditorApplication.isPlaying)
                return "error: stop Play mode first";
            if (EditorUserBuildSettings.activeBuildTarget != BuildTarget.WebGL)
                return "error: switch the platform to Web first (File > Build Profiles > Web > Switch Platform)";

            string swapped = MakeWebScene();
            ConfigurePlayer();
            var report = BuildPipeline.BuildPlayer(new BuildPlayerOptions
            {
                scenes = new[] { WebScene },
                locationPathName = outDir,
                target = BuildTarget.WebGL,
                options = BuildOptions.None,
            });
            var summary = report.summary;
            if (summary.result != BuildResult.Succeeded)
                return $"error: build {summary.result}, {summary.totalErrors} errors (see the Editor console)";
            AddControlsLine(outDir);
            return $"done: {swapped}; {summary.totalSize / 1048576f:0.0} MB in {summary.totalTime.TotalSeconds:0} s";
        }

        static void ConfigurePlayer()
        {
            PlayerSettings.productName = "GOLEM";
            PlayerSettings.WebGL.compressionFormat = WebGLCompressionFormat.Gzip;
            // GitHub Pages sends no Content-Encoding header, so the loader decompresses in JavaScript.
            PlayerSettings.WebGL.decompressionFallback = true;
            // With hashed file names the 6000.6.0f1 build never settles ("Backend has requested a
            // buildprogram run 6 times" on PreprocessJS of the loader).
            PlayerSettings.WebGL.nameFilesAsHashes = false;
            PlayerSettings.WebGL.dataCaching = true;
            PlayerSettings.WebGL.template = "APPLICATION:Default";
            PlayerSettings.defaultWebScreenWidth = 960;
            PlayerSettings.defaultWebScreenHeight = 540;
            PlayerSettings.SetIl2CppCodeGeneration(NamedBuildTarget.WebGL, Il2CppCodeGeneration.OptimizeSize);
        }

        static string MakeWebScene()
        {
            if (AssetDatabase.IsValidFolder(WebFolder))
                AssetDatabase.DeleteAsset(WebFolder);
            AssetDatabase.CreateFolder("Assets/GolemProps", "_web");

            var scene = EditorSceneManager.OpenScene(SourceScene, OpenSceneMode.Single);
            var materials = new Dictionary<Material, Material>();
            var textures = new Dictionary<Texture2D, Texture2D>();
            int swaps = 0;
            foreach (var root in scene.GetRootGameObjects())
            {
                foreach (var renderer in root.GetComponentsInChildren<Renderer>(true))
                {
                    var mats = renderer.sharedMaterials;
                    bool changed = false;
                    for (int i = 0; i < mats.Length; i++)
                    {
                        if (mats[i] == null)
                            continue;
                        if (!materials.TryGetValue(mats[i], out var web))
                        {
                            web = WebMaterial(mats[i], textures);
                            materials[mats[i]] = web;
                        }
                        if (web != mats[i])
                        {
                            mats[i] = web;
                            changed = true;
                            swaps++;
                        }
                    }
                    if (changed)
                        renderer.sharedMaterials = mats;
                }
            }
            EditorSceneManager.SaveScene(scene, WebScene, true);
            EditorSceneManager.OpenScene(SourceScene, OpenSceneMode.Single);
            AssetDatabase.SaveAssets();
            return $"{textures.Count} textures shrunk to {MaxSize}² DXT1, {swaps} material slots swapped";
        }

        static Material WebMaterial(Material source, Dictionary<Texture2D, Texture2D> textures)
        {
            Material copy = null;
            foreach (string property in source.GetTexturePropertyNames())
            {
                if (!(source.GetTexture(property) is Texture2D texture) || !IsPropTexture(texture))
                    continue;
                if (!textures.TryGetValue(texture, out var small))
                {
                    small = Shrink(texture);
                    textures[texture] = small;
                }
                if (copy == null)
                    copy = new Material(source);
                copy.SetTexture(property, small);
            }
            if (copy == null)
                return source;
            AssetDatabase.CreateAsset(copy, WebAssetPath(source, ".mat"));
            return copy;
        }

        static bool IsPropTexture(Texture2D texture)
        {
            string path = AssetDatabase.GetAssetPath(texture);
            return path.StartsWith(PropsFolder) && !path.StartsWith(WebFolder);
        }

        // Copies on the GPU (the imported textures are not CPU-readable), keeping sRGB or linear data
        // as it was, then rebuilds the mip chain and compresses.
        static Texture2D Shrink(Texture2D source)
        {
            int width = Mathf.Min(source.width, MaxSize), height = Mathf.Min(source.height, MaxSize);
            bool srgb = source.isDataSRGB;
            var rt = RenderTexture.GetTemporary(width, height, 0, RenderTextureFormat.ARGB32,
                srgb ? RenderTextureReadWrite.sRGB : RenderTextureReadWrite.Linear);
            var previous = RenderTexture.active;
            Graphics.Blit(source, rt);
            RenderTexture.active = rt;
            var small = new Texture2D(width, height, TextureFormat.RGBA32, true, !srgb);
            small.ReadPixels(new Rect(0, 0, width, height), 0, 0);
            small.Apply(true);
            RenderTexture.active = previous;
            RenderTexture.ReleaseTemporary(rt);

            EditorUtility.CompressTexture(small, TextureFormat.DXT1, TextureCompressionQuality.Normal);
            small.wrapMode = source.wrapMode;
            small.filterMode = source.filterMode;
            small.anisoLevel = source.anisoLevel;
            AssetDatabase.CreateAsset(small, WebAssetPath(source, ".asset"));
            return small;
        }

        // Every prop names its material "model" and its textures "texture_*", so prefix the prop folder.
        static string WebAssetPath(UnityEngine.Object source, string extension)
        {
            string path = AssetDatabase.GetAssetPath(source);
            string prop = string.IsNullOrEmpty(path) ? "scene" : Path.GetFileName(Path.GetDirectoryName(path));
            return AssetDatabase.GenerateUniqueAssetPath($"{WebFolder}/{prop}_{source.name}{extension}");
        }

        // The Default template has no room for instructions, so add a line under its footer.
        static void AddControlsLine(string outDir)
        {
            string index = Path.Combine(outDir, "index.html");
            string html = File.ReadAllText(index);
            int title = html.IndexOf("id=\"unity-build-title\"", StringComparison.Ordinal);
            if (title < 0)
                return;
            int titleEnd = html.IndexOf("</div>", title, StringComparison.Ordinal);
            int footerEnd = html.IndexOf("</div>", titleEnd + 6, StringComparison.Ordinal) + 6;
            string line = "\n      <div id=\"golem-controls\" style=\"font: 14px/1.5 Arial, sans-serif; color: #333; " +
                          "max-width: 960px; padding: 8px 4px\">" + Controls + "</div>";
            File.WriteAllText(index, html.Insert(footerEnd, line));
        }
    }
}
