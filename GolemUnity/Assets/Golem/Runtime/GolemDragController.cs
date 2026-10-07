using System.Collections.Generic;
using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

namespace Golem
{
    /// <summary>
    /// Play-mode interaction: grab any hinged or sliding part with the left mouse button and drag
    /// it the way it moves; press O to open every part and C to close them.
    ///
    /// Dragging: at the grab we project where the grabbed point would be on screen at each of the
    /// joint's two limits, and map mouse movement along that chord linearly onto the joint's travel.
    /// So a side-hinged door follows horizontal drags, a lid or laptop screen vertical ones, and a
    /// drawer slides toward the camera, from any viewpoint. The mapping is fixed for the whole drag:
    /// a per-frame mapping flips sign wherever the grabbed point's path turns toward the camera (a
    /// laptop screen near upright), which made the screen run away from the mouse.
    ///
    /// Motion: keys and drags set a goal; each part's drive target moves toward it no faster than
    /// the part's top speed, which falls with the square root of its mass (equal kinetic energy),
    /// clamped. A light screen shuts in about a second, a 2.8 t vault door takes four. Jumping the
    /// target straight to a limit made every part, whatever its mass, cover 90% of its travel in a
    /// quarter of a second, and the motor's reaction torque threw free-standing props over.
    /// </summary>
    [RequireComponent(typeof(Camera))]
    public class GolemDragController : MonoBehaviour
    {
        const float MinChordPixels = 20f;
        const float RampSeconds = 0.3f;   // time to reach top speed, and to come to rest

        Camera cam;
        ArticulationBody held;
        Vector2 dragPerUnit;      // screen pixels per unit of joint travel, fixed at the grab
        Vector2 lastMouse;
        bool heldByMouse;

        static readonly Dictionary<ArticulationBody, float> goals = new Dictionary<ArticulationBody, float>();
        static readonly Dictionary<ArticulationBody, float> speeds = new Dictionary<ArticulationBody, float>();

        void Awake() => cam = GetComponent<Camera>();

        void OnDisable()
        {
            goals.Clear();
            speeds.Clear();
        }

        void Update()
        {
            if (PressedThisFrame(out var mouse))
                heldByMouse = Grab(mouse);
            if (heldByMouse)
            {
                if (Held(out mouse))
                    DragTo(mouse);
                else
                {
                    Release();
                    heldByMouse = false;
                }
            }

            if (KeyPressed('o'))
                OpenAll();
            if (KeyPressed('c'))
                CloseAll();

            MoveTargets(Time.deltaTime);
        }

        /// <summary>Grab the moving part under this screen point, if any. The mouse goes through
        /// Grab, DragTo and Release; they are public so an audit can script drags the same way.</summary>
        public bool Grab(Vector2 screen)
        {
            held = null;
            if (Physics.Raycast(cam.ScreenPointToRay(screen), out var hit) && hit.collider.attachedArticulationBody is { } body && !body.isRoot)
            {
                dragPerUnit = DragPerUnit(body, hit.point);
                if (dragPerUnit.sqrMagnitude > 1e-6f)
                {
                    held = body;
                    lastMouse = screen;
                }
            }
            return held != null;
        }

        public ArticulationBody HeldPart => held;

        /// <summary>Screen pixels per unit of joint travel for the current grab.</summary>
        public Vector2 DragPerUnitOnScreen => dragPerUnit;

        public void DragTo(Vector2 screen)
        {
            if (held == null)
                return;
            SetGoal(held, Goal(held) + Vector2.Dot(screen - lastMouse, dragPerUnit) / dragPerUnit.sqrMagnitude);
            lastMouse = screen;
        }

        public void Release() => held = null;

        public static void OpenAll()
        {
            foreach (var body in FindObjectsByType<ArticulationBody>(FindObjectsSortMode.None))
                if (!body.isRoot) SetGoal(body, body.xDrive.upperLimit);
        }

        public static void CloseAll()
        {
            foreach (var body in FindObjectsByType<ArticulationBody>(FindObjectsSortMode.None))
                if (!body.isRoot) SetGoal(body, body.xDrive.lowerLimit);
        }

        /// <summary>Multiplies every part's top speed (below 1 for slow-motion takes).</summary>
        public static float speedScale = 1f;

        /// <summary>Top speed in the drive's units per second: degrees for a hinge, metres for a slide.</summary>
        public static float TopSpeed(ArticulationBody body)
        {
            var lightness = Mathf.Sqrt(20f / Mathf.Max(body.mass, 0.01f));
            return speedScale * (body.jointType == ArticulationJointType.PrismaticJoint
                ? Mathf.Clamp(0.5f * lightness, 0.15f, 0.6f)
                : Mathf.Clamp(90f * lightness, 25f, 120f));
        }

        static float Goal(ArticulationBody body) => goals.TryGetValue(body, out var goal) ? goal : body.xDrive.target;

        static void SetGoal(ArticulationBody body, float goal) =>
            goals[body] = Mathf.Clamp(goal, body.xDrive.lowerLimit, body.xDrive.upperLimit);

        /// <summary>Move each drive target toward its goal with a trapezoidal speed profile: speed up to
        /// the part's top speed over RampSeconds, and slow down in time to arrive at rest. A part that
        /// stops dead at its limit kicks its body: a chest lid arriving at 100 deg/s rocked the chest 7.6 deg.</summary>
        static void MoveTargets(float dt)
        {
            List<ArticulationBody> gone = null;
            foreach (var pair in goals)
            {
                var body = pair.Key;
                if (body == null)
                {
                    (gone ??= new List<ArticulationBody>()).Add(body);
                    continue;
                }
                var drive = body.xDrive;
                var remaining = pair.Value - drive.target;
                var top = TopSpeed(body);
                var accel = top / RampSeconds;
                var speed = speeds.TryGetValue(body, out var s) ? s : 0f;
                var allowed = Mathf.Min(top, Mathf.Sqrt(2f * accel * Mathf.Abs(remaining)));
                speed = Mathf.MoveTowards(speed, Mathf.Sign(remaining) * allowed, accel * dt);
                var step = speed * dt;
                if (Mathf.Abs(remaining) < 1e-4f || (Mathf.Sign(step) == Mathf.Sign(remaining) && Mathf.Abs(step) >= Mathf.Abs(remaining)))
                {
                    drive.target = pair.Value;
                    speed = 0f;
                }
                else
                    drive.target += step;
                speeds[body] = speed;
                // Feed the planned speed to the drive: with a target velocity of zero its damping
                // resists all motion, and the part trailed its target by damping/stiffness x speed
                // (11 deg at 108 deg/s), still catching up when the target stopped.
                drive.targetVelocity = speed;
                body.xDrive = drive;
            }
            if (gone != null)
                foreach (var body in gone)
                {
                    goals.Remove(body);
                    speeds.Remove(body);
                }
        }

        /// <summary>Screen pixels per unit of joint travel along the chord between the grabbed point's
        /// positions at the joint's two limits; the local direction if that chord is too short to use.</summary>
        Vector2 DragPerUnit(ArticulationBody body, Vector3 point)
        {
            var drive = body.xDrive;
            var now = JointValue(body);
            var range = drive.upperLimit - drive.lowerLimit;
            if (range > 1e-4f)
            {
                var chord = Screen(Moved(body, point, drive.upperLimit - now)) - Screen(Moved(body, point, drive.lowerLimit - now));
                if (chord.magnitude >= MinChordPixels)
                    return chord / range;
            }
            var step = body.jointType == ArticulationJointType.PrismaticJoint ? 0.01f : 1f;
            return (Screen(Moved(body, point, step)) - Screen(point)) / step;
        }

        Vector2 Screen(Vector3 world) => cam.WorldToScreenPoint(world);

        /// <summary>Where a point on the part goes for this much joint travel: along the anchor's X axis
        /// for a slide, around it for a hinge (the joint axis in Unity is the anchor's X).</summary>
        public static Vector3 Moved(ArticulationBody body, Vector3 point, float travel)
        {
            var axis = body.transform.rotation * body.anchorRotation * Vector3.right;
            if (body.jointType == ArticulationJointType.PrismaticJoint)
                return point + axis * travel;
            var pivot = body.transform.TransformPoint(body.anchorPosition);
            return pivot + Quaternion.AngleAxis(travel, axis) * (point - pivot);
        }

        /// <summary>The joint's current travel in the drive's units: degrees for a hinge, metres for a slide.</summary>
        static float JointValue(ArticulationBody body)
        {
            if (body.dofCount < 1)
                return body.xDrive.target;
            var position = body.jointPosition[0];
            return body.jointType == ArticulationJointType.PrismaticJoint ? position : position * Mathf.Rad2Deg;
        }

#if ENABLE_INPUT_SYSTEM
        static bool PressedThisFrame(out Vector2 position)
        {
            position = Mouse.current != null ? Mouse.current.position.ReadValue() : Vector2.zero;
            return Mouse.current != null && Mouse.current.leftButton.wasPressedThisFrame;
        }

        static bool Held(out Vector2 position)
        {
            position = Mouse.current != null ? Mouse.current.position.ReadValue() : Vector2.zero;
            return Mouse.current != null && Mouse.current.leftButton.isPressed;
        }

        static bool KeyPressed(char key) =>
            Keyboard.current != null && (key == 'o' ? Keyboard.current.oKey : Keyboard.current.cKey).wasPressedThisFrame;
#else
        static bool PressedThisFrame(out Vector2 position) { position = Input.mousePosition; return Input.GetMouseButtonDown(0); }
        static bool Held(out Vector2 position) { position = Input.mousePosition; return Input.GetMouseButton(0); }
        static bool KeyPressed(char key) => Input.GetKeyDown(key == 'o' ? KeyCode.O : KeyCode.C);
#endif
    }
}
