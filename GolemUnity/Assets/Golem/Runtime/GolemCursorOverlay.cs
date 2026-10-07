using UnityEngine;
using UnityEngine.UI;

namespace Golem
{
    /// <summary>
    /// A drawn mouse cursor and key badge for recordings. Scripted drags (GolemAudit, GolemShots) move
    /// no real cursor, so a recording shows this one, on a screen-space overlay canvas that the
    /// Recorder's Game View capture includes. Created on first use; the cursor stays hidden until a
    /// script places it. Pixel positions are the Game view's, as the drag code uses.
    /// </summary>
    public class GolemCursorOverlay : MonoBehaviour
    {
        const int ArrowPixels = 40;   // at 1080 lines; scaled with the screen height

        static GolemCursorOverlay instance;
        static Vector2 position, glideFrom, glideTo;
        static float glideStart, glideSeconds;
        static bool visible, pressed;
        static string badgeText = "";
        static float badgeUntil;

        RectTransform cursor;
        Image arrow;
        Text badge;

        /// <summary>Put the cursor here now; held tints it, as a pressed button.</summary>
        public static void Place(Vector2 screen, bool held)
        {
            Ensure();
            position = glideTo = screen;
            glideSeconds = 0f;
            visible = true;
            pressed = held;
        }

        /// <summary>Glide the cursor to a point over some seconds of game time (from where it is, or appear there).</summary>
        public static void GlideTo(Vector2 screen, float seconds)
        {
            Ensure();
            glideFrom = visible ? position : screen;
            glideTo = screen;
            glideStart = Time.time;
            glideSeconds = Mathf.Max(0.01f, seconds);
            visible = true;
            pressed = false;
        }

        public static void Hide() => visible = false;

        /// <summary>Show a key badge ("O  open everything") for a while.</summary>
        public static void Key(string text, float seconds = 1.5f)
        {
            Ensure();
            badgeText = text;
            badgeUntil = Time.time + seconds;
        }

        static void Ensure()
        {
            if (instance != null)
                return;
            instance = new GameObject("GOLEM Cursor Overlay").AddComponent<GolemCursorOverlay>();
            instance.Build();
        }

        void Build()
        {
            var canvas = gameObject.AddComponent<Canvas>();
            canvas.renderMode = RenderMode.ScreenSpaceOverlay;
            canvas.sortingOrder = 1000;

            var texture = ArrowTexture(ArrowPixels * 2);
            cursor = new GameObject("cursor", typeof(RectTransform), typeof(Image)).GetComponent<RectTransform>();
            cursor.SetParent(transform, false);
            cursor.anchorMin = cursor.anchorMax = Vector2.zero;
            cursor.pivot = new Vector2(0f, 1f);  // the arrow's tip is the texture's top-left corner
            arrow = cursor.GetComponent<Image>();
            arrow.sprite = Sprite.Create(texture, new Rect(0, 0, texture.width, texture.height), new Vector2(0f, 1f));
            arrow.raycastTarget = false;

            badge = new GameObject("key", typeof(RectTransform), typeof(Text)).GetComponent<Text>();
            var rect = badge.rectTransform;
            rect.SetParent(transform, false);
            rect.anchorMin = rect.anchorMax = rect.pivot = new Vector2(0.5f, 0f);
            badge.font = Resources.GetBuiltinResource<Font>("LegacyRuntime.ttf");
            badge.alignment = TextAnchor.MiddleCenter;
            badge.color = Color.white;
            badge.raycastTarget = false;
            badge.gameObject.AddComponent<Outline>().effectColor = new Color(0f, 0f, 0f, 0.85f);
        }

        void LateUpdate()
        {
            if (glideSeconds > 0f)
            {
                var t = Mathf.Clamp01((Time.time - glideStart) / glideSeconds);
                position = Vector2.Lerp(glideFrom, glideTo, t * t * (3f - 2f * t));
            }
            var scale = Screen.height / 1080f;
            cursor.gameObject.SetActive(visible);
            cursor.anchoredPosition = position;
            cursor.sizeDelta = Vector2.one * ArrowPixels * scale;
            arrow.color = pressed ? new Color(1f, 0.82f, 0.35f) : Color.white;

            badge.gameObject.SetActive(Time.time < badgeUntil);
            badge.text = badgeText;
            badge.fontSize = Mathf.RoundToInt(34 * scale);
            badge.rectTransform.sizeDelta = new Vector2(1400f, 60f) * scale;
            badge.rectTransform.anchoredPosition = new Vector2(0f, 48f * scale);
        }

        /// <summary>A classic arrow pointer, white with a black outline, tip at the top-left.</summary>
        static Texture2D ArrowTexture(int size)
        {
            // Outline in units of the texture (x right, y down from the tip).
            Vector2[] shape =
            {
                new Vector2(0.00f, 0.00f), new Vector2(0.00f, 0.78f), new Vector2(0.19f, 0.61f),
                new Vector2(0.33f, 0.92f), new Vector2(0.45f, 0.87f), new Vector2(0.31f, 0.57f),
                new Vector2(0.56f, 0.57f),
            };
            var texture = new Texture2D(size, size, TextureFormat.RGBA32, false) { filterMode = FilterMode.Bilinear };
            var pixels = new Color32[size * size];
            var inner = System.Array.ConvertAll(shape, p => Vector2.Lerp(p, new Vector2(0.14f, 0.5f), 0.22f));
            for (var y = 0; y < size; y++)
                for (var x = 0; x < size; x++)
                {
                    var p = new Vector2((x + 0.5f) / size, (y + 0.5f) / size);
                    var colour = Inside(inner, p) ? new Color32(255, 255, 255, 255)
                        : Inside(shape, p) ? new Color32(0, 0, 0, 255) : new Color32(0, 0, 0, 0);
                    pixels[(size - 1 - y) * size + x] = colour;  // texture rows run bottom-up
                }
            texture.SetPixels32(pixels);
            texture.Apply();
            return texture;
        }

        static bool Inside(Vector2[] polygon, Vector2 p)
        {
            var inside = false;
            for (int i = 0, j = polygon.Length - 1; i < polygon.Length; j = i++)
                if ((polygon[i].y > p.y) != (polygon[j].y > p.y) &&
                    p.x < (polygon[j].x - polygon[i].x) * (p.y - polygon[i].y) / (polygon[j].y - polygon[i].y) + polygon[i].x)
                    inside = !inside;
            return inside;
        }
    }
}
