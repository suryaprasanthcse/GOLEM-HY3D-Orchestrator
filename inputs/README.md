The FBX files sent to Tencent HY-3D-Component are not stored here: they are public at
https://github.com/suryaprasanthcse/Golem-tencent-hy4-preview/tree/main/inputs (HY-3D-Component reads only
FBX, only from a public URL). Each run's proof.json records the SHA-256 of the FBX it sent.

They are the hackathon's single-piece Hyper3D meshes, converted with
  blender -b --python tools/blender_tools.py -- glb2fbx <name>.glb inputs/<name>.fbx
