using UnityEngine;
#if ENABLE_INPUT_SYSTEM
using UnityEngine.InputSystem;
#endif

namespace Golem
{
    /// <summary>
    /// Physics proof: rolls a heavy ball into a prop. The first shot fires automatically shortly
    /// after Play (handy for recording); each shot can be repeated with its key. A light wooden
    /// cabinet gets shoved, a cast-iron vault door barely notices, because every part carries
    /// the mass GOLEM computed for it.
    /// </summary>
    public class GolemBallLauncher : MonoBehaviour
    {
        [System.Serializable]
        public class Shot
        {
            public string key = "B";           // keyboard key that fires this shot
            public Transform target;
            public Vector3 spawn;
            public float speed = 7f;           // m/s
        }

        public Rigidbody ball;
        public Shot[] shots = new Shot[0];
        [Tooltip("Seconds after Play before the first shot fires by itself; negative to disable.")]
        public float autoLaunchDelay = 1.5f;

        float playStart;
        bool autoFired;

        void Start() => playStart = Time.time;

        void Update()
        {
            if (!autoFired && autoLaunchDelay >= 0 && shots.Length > 0 && Time.time - playStart >= autoLaunchDelay)
            {
                autoFired = true;
                Fire(shots[0]);
            }
            foreach (var shot in shots)
                if (KeyPressed(shot.key))
                    Fire(shot);
        }

        public void Fire(Shot shot)
        {
            if (ball == null || shot.target == null)
                return;
            var renderers = shot.target.GetComponentsInChildren<Renderer>();
            var aim = shot.target.position;
            if (renderers.Length > 0)
            {
                var bounds = renderers[0].bounds;
                foreach (var r in renderers)
                    bounds.Encapsulate(r.bounds);
                aim = bounds.center;
            }
            aim.y = shot.spawn.y;  // roll along the floor
            ball.position = shot.spawn;
            ball.transform.position = shot.spawn;
            ball.angularVelocity = Vector3.zero;
            ball.linearVelocity = (aim - shot.spawn).normalized * shot.speed;
        }

#if ENABLE_INPUT_SYSTEM
        static bool KeyPressed(string key) =>
            Keyboard.current != null && System.Enum.TryParse(key, true, out Key k) && Keyboard.current[k].wasPressedThisFrame;
#else
        static bool KeyPressed(string key) =>
            System.Enum.TryParse(key, true, out KeyCode k) && Input.GetKeyDown(k);
#endif
    }
}
