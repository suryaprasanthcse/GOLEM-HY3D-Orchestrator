using System.Linq;
using UnityEngine;

namespace Golem
{
    /// <summary>
    /// Turns an imported glTF prop plus its joint spec into an ArticulationBody chain.
    ///
    /// glTFast converts glTF (right-handed) to Unity (left-handed) by mirroring X: M = diag(-1, 1, 1).
    /// Points and slide directions map with M. Rotation axes are pseudo-vectors and map with
    /// -M (that is, (x, -y, -z)) so that the same angle opens the part the same way; mirroring a
    /// hinge axis like a position would make every joint open backwards.
    /// </summary>
    public static class GolemArticulator
    {
        const float FootprintBand = 0.02f;       // metres: base vertices this close to the lowest one carry the prop
        const float FootprintThickness = 0.01f;  // metres
        const float DriveHz = 10f;  // natural frequency of every joint drive: at 5 Hz a laptop screen sagged 2 deg under its own weight


        public static Vector3 GltfPoint(float[] p) => new Vector3(-p[0], p[1], p[2]);
        public static Vector3 GltfDirection(float[] d) => new Vector3(-d[0], d[1], d[2]);
        public static Vector3 GltfRotationAxis(float[] a) => new Vector3(a[0], -a[1], -a[2]);

        /// <param name="root">The instantiated glTF scene root; the spec's frame is its local frame.</param>
        /// <param name="density">kg/m3, for part masses (volume estimated from the part's bounds).</param>
        public static void Build(Transform root, GolemSpec spec, float density = 500f)
        {
            var scale = root.lossyScale.x;  // props are scaled uniformly to real-world size
            var partNames = spec.joints.SelectMany(j => new[] { j.parent, j.child }).ToHashSet();
            var childNames = spec.joints.Select(j => j.child).ToHashSet();
            foreach (var joint in spec.joints)
            {
                // A root part (never a child) may have been renamed by glTFast to the file name:
                // it is then the imported model's top object, the root's first child.
                var parent = FindDeep(root, joint.parent)
                    ?? (!childNames.Contains(joint.parent) && root.childCount > 0 ? root.GetChild(0) : null);
                var child = FindDeep(root, joint.child);
                if (parent == null || child == null)
                {
                    var names = string.Join(", ", root.GetComponentsInChildren<Transform>(true).Select(t => t.name));
                    Debug.LogError($"[GOLEM] {spec.asset}: node '{joint.parent}' or '{joint.child}' not found among: {names}");
                    continue;
                }

                var parentBody = EnsureBody(parent, partNames);
                if (parentBody.isRoot)
                {
                    parentBody.immovable = true;  // the body stays put; its parts move
                    AddFootprint(parent, partNames);
                }
                var childBody = EnsureBody(child, partNames);
                childBody.mass = EstimateMass(child, partNames, density);
                parentBody.mass = EstimateMass(parent, partNames, density);

                var pivotWorld = root.TransformPoint(GltfPoint(joint.pivot));
                if (joint.type == "prismatic")
                {
                    childBody.jointType = ArticulationJointType.PrismaticJoint;
                    childBody.linearLockX = ArticulationDofLock.LimitedMotion;
                    childBody.linearLockY = ArticulationDofLock.LockedMotion;
                    childBody.linearLockZ = ArticulationDofLock.LockedMotion;
                    AlignAnchor(childBody, child, pivotWorld, root.TransformDirection(GltfDirection(joint.axis)));
                }
                else
                {
                    childBody.jointType = ArticulationJointType.RevoluteJoint;
                    childBody.twistLock = ArticulationDofLock.LimitedMotion;
                    AlignAnchor(childBody, child, pivotWorld, root.TransformDirection(GltfRotationAxis(joint.axis)));
                }

                var drive = childBody.xDrive;
                // Slide limits are in scene units, so they scale with the prop; hinge limits are degrees.
                var unit = joint.type == "prismatic" ? scale : 1f;
                drive.lowerLimit = joint.limits_deg[0] * unit;
                drive.upperLimit = joint.limits_deg[1] * unit;
                // An acceleration drive scales stiffness and damping by the joint's own inertia, so one
                // setting (critically damped, DriveHz) tracks a 0.9 kg screen and a 2.8 t door equally
                // closely. Gains scaled by mass alone left a heavy chest lid trailing its target and
                // slamming into its limit: the load is the inertia about the hinge, not the mass.
                var omega = 2f * Mathf.PI * DriveHz;
                drive.driveType = ArticulationDriveType.Acceleration;
                drive.stiffness = omega * omega;
                drive.damping = 2f * omega;
                drive.forceLimit = float.MaxValue;
                // Start as generated: closed for a lid, standing open for a laptop screen.
                drive.target = Mathf.Clamp(joint.rest_deg * unit, drive.lowerLimit, drive.upperLimit);
                childBody.xDrive = drive;

                var link = child.TryGetComponent(out GolemJointLink existing) ? existing : child.gameObject.AddComponent<GolemJointLink>();
                link.outward = joint.outward != null && joint.outward.Length == 3
                    ? root.TransformDirection(GltfDirection(joint.outward)).normalized
                    : Vector3.zero;
            }
        }

        /// <summary>Anchor on the pivot, with the anchor's X axis (the joint axis in Unity) along the joint.</summary>
        static void AlignAnchor(ArticulationBody body, Transform part, Vector3 pivotWorld, Vector3 axisWorld)
        {
            body.matchAnchors = true;
            body.anchorPosition = part.InverseTransformPoint(pivotWorld);
            body.anchorRotation = Quaternion.FromToRotation(Vector3.right, part.InverseTransformDirection(axisWorld).normalized);
        }

        /// <summary>An ArticulationBody on the part, with convex colliders for its meshes. glTFast may put
        /// a mesh on the node itself or on child objects, so meshes are collected from the part's
        /// own subtree, stopping at other parts (a hinged lid has its own colliders).</summary>
        static ArticulationBody EnsureBody(Transform part, System.Collections.Generic.HashSet<string> partNames)
        {
            if (!part.TryGetComponent(out MeshCollider _))
            {
                foreach (var filter in OwnMeshes(part, partNames))
                {
                    var collider = filter.gameObject.AddComponent<MeshCollider>();
                    collider.sharedMesh = filter.sharedMesh;
                    collider.convex = true;  // moving bodies need convex colliders
                }
            }
            // TryGetComponent, not "??": the Editor's fake-null objects would fool the ?? operator.
            return part.TryGetComponent(out ArticulationBody body) ? body : part.gameObject.AddComponent<ArticulationBody>();
        }

        /// <summary>A flat base under a root part. Generated bases are rarely flat (the chest's varies by
        /// 2 cm), so a body resting on its convex hull rocks from one facet to another as its centre of
        /// mass moves: the chest rocked 8 deg as its lid opened. The footprint is a thin box at the part's
        /// lowest point, spanning its vertices within FootprintBand of it, so the prop stands on its whole
        /// base like the real object.</summary>
        static void AddFootprint(Transform part, System.Collections.Generic.HashSet<string> partNames)
        {
            const string name = "GOLEM Footprint";
            if (part.Find(name) != null)
                return;
            var points = OwnMeshes(part, partNames).Where(f => f.sharedMesh.isReadable)
                .SelectMany(f => f.sharedMesh.vertices.Select(v => f.transform.TransformPoint(v))).ToList();
            if (points.Count == 0)
                return;
            var bottom = points.Min(p => p.y);
            var basePoints = points.Where(p => p.y < bottom + FootprintBand).ToList();
            var min = new Vector3(basePoints.Min(p => p.x), bottom, basePoints.Min(p => p.z));
            var max = new Vector3(basePoints.Max(p => p.x), bottom + FootprintThickness, basePoints.Max(p => p.z));
            var footprint = new GameObject(name).transform;
            footprint.SetParent(part, false);
            footprint.SetPositionAndRotation((min + max) / 2, Quaternion.identity);
            var box = footprint.gameObject.AddComponent<BoxCollider>();
            var s = footprint.lossyScale;
            box.size = new Vector3((max.x - min.x) / s.x, (max.y - min.y) / s.y, (max.z - min.z) / s.z);
        }

        /// <summary>density x the part's enclosed mesh volume (world scale). The cut parts are capped,
        /// so the volume is real; if a mesh is too open to trust (volume implausibly small or larger
        /// than its bounding box), fall back to half the bounding box.</summary>
        public static float EstimateMass(Transform part, System.Collections.Generic.HashSet<string> partNames, float density)
        {
            var filters = OwnMeshes(part, partNames).ToArray();
            var renderers = filters.Select(f => f.GetComponent<Renderer>()).Where(r => r != null).ToArray();
            if (renderers.Length == 0)
                return 1f;
            var b = renderers[0].bounds;
            foreach (var r in renderers)
                b.Encapsulate(r.bounds);
            var boxVolume = b.size.x * b.size.y * b.size.z;
            var volume = filters.Sum(MeshVolume);
            if (!(volume > 0.02f * boxVolume && volume < boxVolume))
                volume = 0.5f * boxVolume;
            return Mathf.Max(0.1f, volume * density);
        }

        /// <summary>Enclosed volume of a mesh in world units: the sum of signed tetrahedra from the origin.</summary>
        static float MeshVolume(MeshFilter filter)
        {
            var mesh = filter.sharedMesh;
            if (mesh == null || !mesh.isReadable)
                return 0f;
            var toWorld = filter.transform.localToWorldMatrix;
            var vertices = mesh.vertices;
            var triangles = mesh.triangles;
            double volume = 0;
            for (var i = 0; i < triangles.Length; i += 3)
            {
                var a = toWorld.MultiplyPoint3x4(vertices[triangles[i]]);
                var c = toWorld.MultiplyPoint3x4(vertices[triangles[i + 1]]);
                var d = toWorld.MultiplyPoint3x4(vertices[triangles[i + 2]]);
                volume += Vector3.Dot(a, Vector3.Cross(c, d)) / 6.0;
            }
            return Mathf.Abs((float)volume);
        }

        static System.Collections.Generic.IEnumerable<MeshFilter> OwnMeshes(Transform node, System.Collections.Generic.HashSet<string> partNames)
        {
            foreach (var filter in node.GetComponents<MeshFilter>())
                if (filter.sharedMesh != null)
                    yield return filter;
            foreach (Transform child in node)
                if (!partNames.Contains(child.name))
                    foreach (var filter in OwnMeshes(child, partNames))
                        yield return filter;
        }

        public static Transform FindDeep(Transform root, string name)
        {
            if (root.name == name)
                return root;
            return root.GetComponentsInChildren<Transform>(true).FirstOrDefault(t => t.name == name);
        }
    }
}
