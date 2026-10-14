"""Import each v2 glb and check: loose parts with inward-facing normals, node names, rod/tail transforms."""
import bpy, bmesh, os, sys, json
from mathutils import Vector
D = sys.argv[-1]
for f in sorted(os.listdir(D)):
    if not f.endswith(".glb"): continue
    bpy.ops.wm.read_factory_settings(use_empty=True)
    bpy.ops.import_scene.gltf(filepath=os.path.join(D, f))
    bad = []
    nparts = 0
    for o in bpy.context.scene.objects:
        if o.type != "MESH": continue
        bm = bmesh.new(); bm.from_mesh(o.data); bm.faces.ensure_lookup_table()
        seen = set()
        for f0 in bm.faces:
            if f0.index in seen: continue
            stack = [f0]; part = []
            seen.add(f0.index)
            while stack:
                fa = stack.pop(); part.append(fa)
                for e in fa.edges:
                    for fb in e.link_faces:
                        if fb.index not in seen:
                            seen.add(fb.index); stack.append(fb)
            nparts += 1
            area = sum(x.calc_area() for x in part)
            c = sum((x.calc_center_median() * x.calc_area() for x in part), Vector()) / max(area, 1e-9)
            closed = all(e.is_manifold for x in part for e in x.edges)
            if closed:
                vol = 0.0
                for x in part:
                    vs = [v.co - c for v in x.verts]
                    for k in range(1, len(vs) - 1):
                        vol += vs[0].dot(vs[k].cross(vs[k + 1])) / 6
                if vol < 0:
                    bad.append((o.name, "closed", len(part), round(vol, 5)))
            else:
                score = sum(x.normal.dot(x.calc_center_median() - c) * x.calc_area() for x in part)
                if score < -1e-4:
                    bad.append((o.name, "open", len(part), round(score, 5)))
        bm.free()
    print(f, "parts", nparts, "INWARD:", bad[:8] if bad else "none")
