using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

namespace Golem
{
    /// <summary>
    /// Camera presets for recording: 0 frames the whole line of props, 1-9 a close-up of each prop
    /// in line order (left to right on screen). GolemStage computes the views so each prop's whole
    /// range of motion stays in frame. The camera glides to a view over transitionSeconds.
    /// </summary>
    [RequireComponent(typeof(Camera))]
    public class GolemCameraRig : MonoBehaviour
    {
        [System.Serializable]
        public class View
        {
            public string name;
            public Vector3 position;
            public Quaternion rotation = Quaternion.identity;
        }

        public View[] views = new View[0];
        public float transitionSeconds = 0.8f;

        View to;
        Vector3 fromPosition;
        Quaternion fromRotation;
        float start;

        void Update()
        {
            for (var i = 0; i < Mathf.Min(10, views.Length); i++)
                if (DigitPressed(i))
                    Show(i);
            if (to == null)
                return;
            var t = transitionSeconds > 0f ? Mathf.Clamp01((Time.time - start) / transitionSeconds) : 1f;
            t = t * t * (3f - 2f * t);  // ease in and out
            transform.SetPositionAndRotation(Vector3.Lerp(fromPosition, to.position, t), Quaternion.Slerp(fromRotation, to.rotation, t));
            if (t >= 1f)
                to = null;
        }

        /// <summary>Glide to view <paramref name="index"/> (0 = wide), or jump there when instant.</summary>
        public void Show(int index, bool instant = false)
        {
            if (index < 0 || index >= views.Length)
                return;
            to = views[index];
            fromPosition = transform.position;
            fromRotation = transform.rotation;
            start = Time.time;
            if (instant)
            {
                transform.SetPositionAndRotation(to.position, to.rotation);
                to = null;
            }
        }

#if ENABLE_INPUT_SYSTEM
        static readonly Key[] Digits = { Key.Digit0, Key.Digit1, Key.Digit2, Key.Digit3, Key.Digit4, Key.Digit5, Key.Digit6, Key.Digit7, Key.Digit8, Key.Digit9 };
        static readonly Key[] Numpad = { Key.Numpad0, Key.Numpad1, Key.Numpad2, Key.Numpad3, Key.Numpad4, Key.Numpad5, Key.Numpad6, Key.Numpad7, Key.Numpad8, Key.Numpad9 };

        static bool DigitPressed(int digit)
        {
            var keyboard = Keyboard.current;
            return keyboard != null && (keyboard[Digits[digit]].wasPressedThisFrame || keyboard[Numpad[digit]].wasPressedThisFrame);
        }
#else
        static bool DigitPressed(int digit) =>
            Input.GetKeyDown(KeyCode.Alpha0 + digit) || Input.GetKeyDown(KeyCode.Keypad0 + digit);
#endif
    }
}
