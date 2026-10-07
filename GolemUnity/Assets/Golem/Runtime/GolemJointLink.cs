using UnityEngine;

namespace Golem
{
    /// <summary>
    /// Stops a moving part colliding with the part it is hinged to: their convex colliders
    /// overlap along the seam and would otherwise push the joint apart.
    /// </summary>
    public class GolemJointLink : MonoBehaviour
    {
        [Tooltip("World direction toward the hinge side: positive joint rotation moves the part this way.")]
        public Vector3 outward;

        void Awake() => Apply(transform);

        /// <summary>Also called by the editor self-test, where Awake doesn't run.</summary>
        public static void Apply(Transform part)
        {
            var parent = part.parent ? part.parent.GetComponentInParent<ArticulationBody>() : null;
            if (parent == null)
                return;
            var own = part.GetComponentsInChildren<Collider>();
            foreach (var other in parent.GetComponentsInChildren<Collider>())
                foreach (var mine in own)
                    if (mine != other)
                        Physics.IgnoreCollision(mine, other);
        }
    }
}
