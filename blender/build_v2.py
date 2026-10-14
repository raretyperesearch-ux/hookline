"""HookLine v2 models: soft, rounded, stylized fish and boats (glTF + embedded-glTF .json).

Run:  bvenv/bin/python blender/build_v2.py [name ...]      (no names = build everything)
Out:  site/models2/<name>.glb and <name>.json

Blender is Z-up; the glTF exporter converts to Y-up. Everything faces +X.
Fish:  root node 'Fish' (body), child 'Tail' (pivot at tail root, wag around vertical),
       optional children 'FinL' / 'FinR' (pivot at fin root).
       Materials: FishBody, FishBelly, FishFin, FishEye, FishPupil, FishAccent.
Boats: root 'Boat' (hull + fisherman) and a sibling 'Rod' (pivot at the grip) with child empty 'RodTip'.
       Fisherman materials include 'Coat' and 'Hat' (recoloured by the game).
"""
import base64
import json
import math
import os
import random
import struct
import sys

import bpy
import bmesh  # noqa: E402
from mathutils import Matrix, Vector

SP = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
OUT = os.path.join(SP, "site", "models2")
TRI_COUNTS = {}


# =====================================================================================  basics
def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _mats.clear()


_mats = {}


def srgb(hexstr):
    h = hexstr.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(((x + 0.055) / 1.055) ** 2.4 if x > 0.04045 else x / 12.92 for x in c)


def mat(name, color, rough=0.5, metal=0.0, emit=None, emit_str=1.0, alpha=None, spec=0.5):
    if name in _mats:
        return _mats[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*srgb(color), 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if "Specular IOR Level" in b.inputs:
        b.inputs["Specular IOR Level"].default_value = spec
    if emit:
        b.inputs["Emission Color"].default_value = (*srgb(emit), 1)
        b.inputs["Emission Strength"].default_value = emit_str
    if alpha is not None:
        b.inputs["Alpha"].default_value = alpha
        if hasattr(m, "surface_render_method"):
            m.surface_render_method = "BLENDED"
        if hasattr(m, "blend_method"):
            try:
                m.blend_method = "BLEND"
            except Exception:
                pass
    m.diffuse_color = (*srgb(color), 1)
    _mats[name] = m
    return m


def link(o):
    bpy.context.scene.collection.objects.link(o)
    return o


def obj_from_bm(name, bm, mats):
    me = bpy.data.meshes.new(name)
    bm.to_mesh(me)
    bm.free()
    for m in mats:
        me.materials.append(m)
    return link(bpy.data.objects.new(name, me))


def set_smooth(o, on=True):
    if o.type == "MESH":
        for p in o.data.polygons:
            p.use_smooth = on


def bake(o):
    """Apply all modifiers (and keep transforms)."""
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(o.evaluated_get(dg), preserve_all_data_layers=True, depsgraph=dg)
    old = o.data
    o.modifiers.clear()
    o.data = me
    if old.users == 0 and isinstance(old, bpy.types.Mesh):
        bpy.data.meshes.remove(old)
    return o


def subsurf(o, lvl=1, do_bake=True):
    m = o.modifiers.new("ss", "SUBSURF")
    m.levels = lvl
    m.render_levels = lvl
    if do_bake:
        bake(o)
        set_smooth(o)
    return o


def apply_xform(o, loc=False):
    mw = o.matrix_basis.copy()
    if not loc:
        t = Matrix.Translation(mw.to_translation())
        m3 = t.inverted() @ mw
        o.data.transform(m3)
        o.matrix_basis = t
    else:
        o.data.transform(mw)
        o.matrix_basis = Matrix.Identity(4)
    o.data.update()


def orient_outward(bm, center=None):
    """Flip all faces if they point inward: signed volume for closed meshes, centroid test otherwise."""
    bm.normal_update()
    if all(e.is_manifold for e in bm.edges):
        vol = 0.0
        for f in bm.faces:
            vs = [v.co for v in f.verts]
            for k in range(1, len(vs) - 1):
                vol += vs[0].dot(vs[k].cross(vs[k + 1]))
        if vol < 0:
            bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
            bm.normal_update()
        return
    if center is None:
        center = sum((v.co for v in bm.verts), Vector()) / max(1, len(bm.verts))
    s = sum((f.normal.dot(f.calc_center_median() - center) * f.calc_area()) for f in bm.faces)
    if s < 0:
        bmesh.ops.reverse_faces(bm, faces=bm.faces[:])
        bm.normal_update()


def rings_mesh(name, rings, mats, cyclic=True, mat_fn=None, cap0=False, cap1=False, recalc=True, center=None):
    """Skin a list of rings (lists of points; a 1-point ring is a pole)."""
    bm = bmesh.new()
    vr = [[bm.verts.new(p) for p in r] for r in rings]
    for i in range(len(vr) - 1):
        a, b = vr[i], vr[i + 1]
        n = max(len(a), len(b))
        segs = n if cyclic else n - 1
        for j in range(segs):
            j2 = (j + 1) % n
            if len(a) == 1:
                f = (a[0], b[j], b[j2])
            elif len(b) == 1:
                f = (a[j], b[0], a[j2])
            else:
                f = (a[j], b[j], b[j2], a[j2])
            try:
                face = bm.faces.new(f)
            except ValueError:
                continue
            face.material_index = mat_fn(i, j) if mat_fn else 0
    if cap0 and len(vr[0]) > 2:
        f = bm.faces.new(vr[0])
        f.material_index = mat_fn(-1, 0) if mat_fn else 0
    if cap1 and len(vr[-1]) > 2:
        f = bm.faces.new(list(reversed(vr[-1])))
        f.material_index = mat_fn(len(vr) - 1, 0) if mat_fn else 0
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-5)
    if recalc:
        bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
        orient_outward(bm, center)
    for f in bm.faces:
        f.smooth = True
    return obj_from_bm(name, bm, mats)


def sphere(name, loc, size, m, u=16, v=10, look=None):
    """UV sphere; size is a radius or (sx, sy, sz). look orients local +Z."""
    bm = bmesh.new()
    bmesh.ops.create_uvsphere(bm, u_segments=u, v_segments=v, radius=1.0)
    for f in bm.faces:
        f.smooth = True
    o = obj_from_bm(name, bm, [m])
    s = size if isinstance(size, (tuple, list)) else (size, size, size)
    o.scale = s
    if look is not None:
        o.rotation_mode = "QUATERNION"
        o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(Vector(look).normalized())
    o.location = loc
    return o


def lathe(name, prof, m, seg=24, loc=(0, 0, 0), mats=None, mat_fn=None, cap_open=True):
    """Revolve a (r, z) profile listed top -> bottom around local Z."""
    rings = []
    for r, z in prof:
        if r <= 1e-6:
            rings.append([(0, 0, z)])
        else:
            rings.append([(r * math.cos(2 * math.pi * k / seg), r * math.sin(2 * math.pi * k / seg), z) for k in range(seg)])
    o = rings_mesh(name, rings, mats or [m], mat_fn=mat_fn)
    o.location = loc
    return o


def tube(name, pts, radii, m, bres=2, ures=4, caps=True, cyclic=False, order=3):
    """Smooth NURBS tube through pts with per-point radius, converted to a mesh."""
    cu = bpy.data.curves.new(name, "CURVE")
    cu.dimensions = "3D"
    cu.bevel_depth = 1.0
    cu.bevel_resolution = bres
    cu.use_fill_caps = caps and not cyclic
    cu.resolution_u = ures
    sp = cu.splines.new("NURBS")
    sp.points.add(len(pts) - 1)
    if not isinstance(radii, (list, tuple)):
        radii = [radii] * len(pts)
    for p, co, r in zip(sp.points, pts, radii):
        p.co = (co[0], co[1], co[2], 1.0)
        p.radius = r
    sp.order_u = min(order, len(pts))
    sp.use_endpoint_u = not cyclic
    sp.use_cyclic_u = cyclic
    co = link(bpy.data.objects.new(name + "_c", cu))
    dg = bpy.context.evaluated_depsgraph_get()
    me = bpy.data.meshes.new_from_object(co.evaluated_get(dg))
    bpy.data.objects.remove(co)
    bm = bmesh.new()
    bm.from_mesh(me)
    bmesh.ops.remove_doubles(bm, verts=bm.verts[:], dist=1e-6)
    if all(e.is_manifold for e in bm.edges):
        orient_outward(bm)
    bm.to_mesh(me)
    bm.free()
    me.materials.clear()
    me.materials.append(m)
    o = link(bpy.data.objects.new(name, me))
    set_smooth(o)
    return o


def densify(outline, k=2):
    out = []
    n = len(outline)
    for i in range(n):
        a, b = Vector(outline[i]), Vector(outline[(i + 1) % n])
        for s in range(k):
            out.append(tuple(a.lerp(b, s / k)))
    return out


def slab(name, outline, thick, m, lvl=1, dens=2, taper=None, plane="XZ", mats=None, mat_fn=None):
    """A soft fin/plate: 2D outline (closed, CCW or CW), extruded +-thick/2, triangulated, subdivided.
    plane 'XZ': outline (x, z), thickness along Y.  'XY': outline (x, y), thickness along Z."""
    pts = densify(outline, dens) if dens > 1 else list(outline)
    bm = bmesh.new()
    # build flat first (so the concave ngons triangulate correctly), then push the thickness out
    top = [bm.verts.new((u, 0.001, v)) for u, v in pts]
    bot = [bm.verts.new((u, -0.001, v)) for u, v in pts]
    f1 = bm.faces.new(top)
    f2 = bm.faces.new(list(reversed(bot)))
    n = len(pts)
    for i in range(n):
        j = (i + 1) % n
        bm.faces.new((top[i], top[j], bot[j], bot[i]))
    res = bmesh.ops.triangulate(bm, faces=[f1, f2], quad_method="BEAUTY", ngon_method="BEAUTY")
    bmesh.ops.join_triangles(bm, faces=res["faces"], angle_face_threshold=math.radians(40), angle_shape_threshold=math.radians(40))
    for v in bm.verts:
        u, w = v.co.x, v.co.z
        t = thick * (taper(u, w) if taper else 1.0) / 2 * (1 if v.co.y > 0 else -1)
        v.co = Vector((u, t, w)) if plane == "XZ" else Vector((u, w, t))
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    orient_outward(bm)
    if mat_fn:
        for f in bm.faces:
            f.material_index = mat_fn(f.calc_center_median())
    o = obj_from_bm(name, bm, mats or [m])
    if lvl:
        subsurf(o, lvl)
    set_smooth(o)
    return o


def rbox(name, size, loc, m, bevel=0.05, seg=3, rot=None, mats=None):
    """Rounded box, full dimensions size=(dx, dy, dz)."""
    bm = bmesh.new()
    bmesh.ops.create_cube(bm, size=1.0)
    for v in bm.verts:
        v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
    o = obj_from_bm(name, bm, mats or [m])
    md = o.modifiers.new("bev", "BEVEL")
    md.width = min(bevel, min(size) * 0.49)
    md.segments = seg
    md.limit_method = "NONE"
    md.harden_normals = True
    bake(o)
    set_smooth(o)
    if rot:
        o.rotation_euler = rot
    o.location = loc
    return o


def torus(name, loc, R, r, m, seg=32, mseg=10, rot=None, mats=None, mat_fn=None):
    rings = []
    for i in range(seg):
        a = 2 * math.pi * i / seg
        c = Vector((math.cos(a), math.sin(a), 0))
        rings.append([tuple(c * (R + r * math.cos(2 * math.pi * k / mseg)) + Vector((0, 0, r * math.sin(2 * math.pi * k / mseg)))) for k in range(mseg)])
    rings.append(rings[0])
    bm = bmesh.new()
    vr = [[bm.verts.new(p) for p in r_] for r_ in rings[:-1]]
    vr.append(vr[0])
    for i in range(seg):
        for k in range(mseg):
            k2 = (k + 1) % mseg
            f = bm.faces.new((vr[i][k], vr[i + 1][k], vr[i + 1][k2], vr[i][k2]))
            f.material_index = mat_fn(i) if mat_fn else 0
            f.smooth = True
    bmesh.ops.recalc_face_normals(bm, faces=bm.faces[:])
    o = obj_from_bm(name, bm, mats or [m])
    if rot:
        o.rotation_euler = rot
    o.location = loc
    return o


def join(objs, name):
    objs = [o for o in objs if o is not None]
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    if len(objs) > 1:
        bpy.ops.object.join()
    o = bpy.context.view_layer.objects.active
    o.name = name
    o.data.name = name
    return o


def join_at(objs, name, origin):
    """Join and set the object origin (pivot) to `origin`, with identity rotation/scale."""
    o = join(objs, name)
    apply_xform(o, loc=True)
    o.data.transform(Matrix.Translation(-Vector(origin)))
    o.location = Vector(origin)
    o.data.update()
    return o


def mirror_y(objs):
    out = []
    for o in objs:
        c = o.copy()
        c.data = o.data.copy()
        link(c)
        c.location.y = -c.location.y
        apply_xform(c, loc=True)
        c.data.transform(Matrix.Scale(-1, 4, (0, 1, 0)))
        c.data.flip_normals()
        c.data.update()
        out.append(c)
    return out


def tri_count(objs=None):
    n = 0
    for o in (objs or bpy.context.scene.objects):
        if o.type == "MESH":
            o.data.calc_loop_triangles()
            n += len(o.data.loop_triangles)
    return n


# =====================================================================================  export
def glb_to_json(glb_path, json_path):
    data = open(glb_path, "rb").read()
    magic, ver, length = struct.unpack_from("<III", data, 0)
    assert magic == 0x46546C67, "not a glb"
    off = 12
    j, binb = None, b""
    while off < length:
        clen, ctype = struct.unpack_from("<II", data, off)
        chunk = data[off + 8: off + 8 + clen]
        if ctype == 0x4E4F534A:
            j = json.loads(chunk.decode("utf-8"))
        else:
            binb = chunk
        off += 8 + clen
    if binb and j.get("buffers"):
        j["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(binb).decode("ascii")
    with open(json_path, "w") as f:
        json.dump(j, f, separators=(",", ":"))


def check_normals(name):
    """Warn about closed loose parts whose normals point inward (negative signed volume)."""
    bad = []
    for o in bpy.context.scene.objects:
        if o.type != "MESH":
            continue
        bm = bmesh.new()
        bm.from_mesh(o.data)
        bm.faces.ensure_lookup_table()
        seen = set()
        for f0 in bm.faces:
            if f0.index in seen:
                continue
            stack, part = [f0], []
            seen.add(f0.index)
            while stack:
                fa = stack.pop()
                part.append(fa)
                for e in fa.edges:
                    for fb in e.link_faces:
                        if fb.index not in seen:
                            seen.add(fb.index)
                            stack.append(fb)
            if not all(e.is_manifold for fa in part for e in fa.edges):
                continue
            vol = 0.0
            for fa in part:
                vs = [v.co for v in fa.verts]
                for k in range(1, len(vs) - 1):
                    vol += vs[0].dot(vs[k].cross(vs[k + 1])) / 6
            if vol < 0:
                bad.append((o.name, len(part), round(vol, 5), tuple(round(c, 2) for c in sum((fa.calc_center_median() for fa in part), Vector()) / len(part))))
        bm.free()
    print(f"normals {name}:", "OK" if not bad else f"INWARD {bad}")
    return bad


def export(name):
    os.makedirs(OUT, exist_ok=True)
    check_normals(name)
    for o in bpy.context.scene.objects:
        if o.type == "MESH":
            # meshes only: drop empty material slots / unused data
            o.data.update()
    path = os.path.join(OUT, name + ".glb")
    bpy.ops.object.select_all(action="SELECT")
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=False, export_apply=True,
                              export_yup=True, export_extras=False, export_cameras=False, export_lights=False)
    glb_to_json(path, os.path.join(OUT, name + ".json"))
    TRI_COUNTS[name] = tri_count()
    print(f"exported {name}: {TRI_COUNTS[name]} tris")


# =====================================================================================  profiles
def pchip(xs, ys):
    n = len(xs)
    h = [xs[i + 1] - xs[i] for i in range(n - 1)]
    d = [(ys[i + 1] - ys[i]) / h[i] for i in range(n - 1)]
    m = [0.0] * n
    m[0], m[-1] = d[0], d[-1]
    for i in range(1, n - 1):
        if d[i - 1] * d[i] <= 0:
            m[i] = 0.0
        else:
            w1, w2 = 2 * h[i] + h[i - 1], h[i] + 2 * h[i - 1]
            m[i] = (w1 + w2) / (w1 / d[i - 1] + w2 / d[i])

    def f(x):
        if x <= xs[0]:
            return ys[0]
        if x >= xs[-1]:
            return ys[-1]
        i = 0
        while xs[i + 1] < x:
            i += 1
        t = (x - xs[i]) / h[i]
        t2, t3 = t * t, t * t * t
        return ((2 * t3 - 3 * t2 + 1) * ys[i] + (t3 - 2 * t2 + t) * h[i] * m[i]
                + (-2 * t3 + 3 * t2) * ys[i + 1] + (t3 - t2) * h[i] * m[i + 1])
    return f


class Body:
    """Lofted fish body. keys: (x, half_width, half_height_top, half_height_bottom, z_center[, y_center])."""

    def __init__(self, keys, p=2.0, seg=22, n=26, nose_ext=0.02, tail_ext=0.02, extra=()):
        keys = sorted(keys)
        xs = [k[0] for k in keys]
        self.W = pchip(xs, [k[1] for k in keys])
        self.HT = pchip(xs, [k[2] for k in keys])
        self.HB = pchip(xs, [k[3] for k in keys])
        self.ZC = pchip(xs, [k[4] for k in keys])
        self.YC = pchip(xs, [k[5] if len(k) > 5 else 0.0 for k in keys])
        self.x0, self.x1 = xs[0], xs[-1]
        self.p, self.seg, self.n = p, seg, n
        self.nose_ext, self.tail_ext = nose_ext, tail_ext
        self.extra = list(extra)

    def stations(self):
        xs = [self.x0 + (self.x1 - self.x0) * (0.5 - 0.5 * math.cos(math.pi * i / (self.n - 1))) for i in range(self.n)]
        for e in self.extra:
            if self.x0 < e < self.x1 and all(abs(e - x) > 0.015 for x in xs):
                xs.append(e)
        return sorted(xs)

    def ring(self, x):
        e = 2.0 / self.p
        w, ht, hb, zc, yc = self.W(x), self.HT(x), self.HB(x), self.ZC(x), self.YC(x)
        pts = []
        for k in range(self.seg):
            a = 2 * math.pi * k / self.seg
            c, s = math.cos(a), math.sin(a)
            y = yc + w * math.copysign(abs(c) ** e, c)
            z = zc + (ht if s >= 0 else hb) * math.copysign(abs(s) ** e, s)
            pts.append((x, y, z))
        return pts

    def build(self, name, mats, mat_fn=None):
        xs = self.stations()
        rings = [[(self.x0 - self.tail_ext, self.YC(self.x0), self.ZC(self.x0))]]
        rings += [self.ring(x) for x in xs]
        rings.append([(self.x1 + self.nose_ext, self.YC(self.x1), self.ZC(self.x1))])
        seg = self.seg
        xm = [self.x0] + [(xs[i] + xs[i + 1]) / 2 for i in range(len(xs) - 1)] + [self.x1]

        def mf(i, j):
            if mat_fn is None:
                return 0
            a = 2 * math.pi * (j + 0.5) / seg
            return mat_fn(xm[max(0, min(i, len(xm) - 1))], a)
        o = rings_mesh(name, rings, mats, mat_fn=mf)
        return o

    def surf_y(self, x, z):
        w, zc = self.W(x), self.ZC(x)
        h = self.HT(x) if z >= zc else self.HB(x)
        t = min(1.0, abs(z - zc) / max(h, 1e-6))
        return self.YC(x) + w * (1 - t ** self.p) ** (1 / self.p)

    def inside(self, x, y, z):
        w, zc = self.W(x), self.ZC(x)
        h = self.HT(x) if z >= zc else self.HB(x)
        if w <= 1e-6 or h <= 1e-6:
            return False
        return (abs(y - self.YC(x)) / w) ** self.p + (abs(z - zc) / h) ** self.p <= 1.0

    def front_x(self, y, z):
        x = self.x1
        while x > self.x0:
            if self.inside(x, y, z):
                return x
            x -= 0.003
        return self.x0

    def normal(self, x, y, z):
        """Approximate outward surface normal (ignores the x slope)."""
        w, zc, yc = self.W(x), self.ZC(x), self.YC(x)
        h = self.HT(x) if z >= zc else self.HB(x)
        p = self.p
        ny = math.copysign(abs((y - yc) / w) ** (p - 1) / w, y - yc)
        nz = math.copysign(abs((z - zc) / h) ** (p - 1) / h, z - zc)
        return Vector((0.0, ny, nz)).normalized()

    def top_z(self, x):
        return self.ZC(x) + self.HT(x)

    def bot_z(self, x):
        return self.ZC(x) - self.HB(x)


# =====================================================================================  fish parts
FISH_MATS = ("FishBody", "FishBelly", "FishFin", "FishEye", "FishPupil", "FishAccent")


def fish_mats(body, belly, fin, accent, accent_emit=None):
    return dict(
        body=mat("FishBody", body, 0.45),
        belly=mat("FishBelly", belly, 0.5),
        fin=mat("FishFin", fin, 0.5),
        eye=mat("FishEye", "#ffffff", 0.35),
        pupil=mat("FishPupil", "#0d0f14", 0.2, spec=0.8),
        accent=mat("FishAccent", accent, 0.45, emit=accent_emit, emit_str=1.0),
    )


def eye(M, center, r, look, up=(0, 0, 1), pupil=0.52, name="Eye", lid=None):
    """White sphere + pupil + tiny highlight. Returns list of objects."""
    look = Vector(look).normalized()
    c = Vector(center)
    parts = [sphere(name + "W", c, r, M["eye"], 18, 10, look=look)]
    pc = c + look * (r * (1 - pupil * 0.38))
    parts.append(sphere(name + "P", pc, (r * pupil, r * pupil, r * pupil * 0.55), M["pupil"], 12, 8, look=look))
    upv = (Vector(up) - look * look.dot(Vector(up))).normalized()
    hc = c + look * (r * 0.9) + upv * (r * 0.33) + Vector((1, 0, 0)) * r * 0.06
    parts.append(sphere(name + "H", hc, r * 0.17, M["eye"], 8, 5))
    if lid is not None:   # sleepy lid: a cap of `lid` material over the top of the eye
        bm = bmesh.new()
        bmesh.ops.create_uvsphere(bm, u_segments=18, v_segments=12, radius=r * 1.16)
        bmesh.ops.delete(bm, geom=[v for v in bm.verts if v.co.z < r * 0.38], context="VERTS")
        for f in bm.faces:
            f.smooth = True
        lo = obj_from_bm(name + "Lid", bm, [lid])
        sol = lo.modifiers.new("s", "SOLIDIFY")
        sol.thickness = r * 0.12
        bake(lo)
        set_smooth(lo)
        lo.rotation_mode = "QUATERNION"
        lo.rotation_quaternion = Vector((0, 0, 1)).rotation_difference((upv * 0.85 + look * 0.5).normalized())
        lo.location = c
        parts.append(lo)
    return parts


def eye_pair(M, B, x, zrel, r, fwd=0.35, upw=0.12, embed=0.45, lid=None):
    z = B.ZC(x) + zrel * B.HT(x)
    y = B.surf_y(x, z)
    parts = []
    for s in (1, -1):
        n = Vector((0.0, s * 1.0, (z - B.ZC(x)) / max(B.HT(x), 1e-3) * 0.5)).normalized()
        c = Vector((x, s * y, z)) - n * r * embed
        look = Vector((fwd, s * 1.0, upw))
        parts += eye(M, c, r, look, name=f"Eye{s}", lid=lid)
    return parts


def mouth_smile(M, B, x_hint, zrel, half_w, curve=0.03, r=0.018, m=None, frown=False, depth=0.004):
    """A little smile tube following the front of the face."""
    pts = []
    zc = B.ZC(x_hint) + zrel * B.HT(x_hint)
    for i in range(9):
        s = -1 + 2 * i / 8
        y = s * half_w
        z = zc + (curve * s * s if not frown else -curve * s * s)
        x = B.front_x(y, z) - depth
        pts.append((x, y, z))
    return tube("Mouth", pts, [r * 0.6] + [r] * 7 + [r * 0.6], m or M["pupil"], bres=1, ures=3)


def teeth(M, pts_dirs, size=0.05):
    out = []
    for i, (p, d) in enumerate(pts_dirs):
        o = lathe(f"Tooth{i}", [(0, size), (size * 0.25, size * 0.6), (size * 0.42, size * 0.1), (size * 0.38, -0.01), (0, -0.02)], M["eye"], seg=8)
        o.rotation_mode = "QUATERNION"
        o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(Vector(d).normalized())
        o.location = p
        out.append(o)
    return out


def fin_obj(name, outline, thick, m, loc, rot=(0, 0, 0), lvl=1, dens=2, taper=None):
    o = slab(name, outline, thick, m, lvl=lvl, dens=dens, taper=taper)
    o.rotation_euler = rot
    o.location = loc
    return o


def root_taper(u0=0.0, u1=-0.5, lo=0.35):
    """Thicker at the fin root (u near u0), thinner towards u1."""
    def f(u, v):
        t = min(1.0, max(0.0, (u - u0) / (u1 - u0))) if u1 != u0 else 0
        return 1.0 - (1.0 - lo) * t
    return f


def pect_pair(M, x, y, z, outline, thick=0.035, droop=-115, yaw=0, lvl=1, m=None):
    """FinL (+Y side) / FinR (-Y side), pivot at the root. outline in local (x back, z out)."""
    fins = []
    for s, nm in ((1, "FinL"), (-1, "FinR")):
        o = slab(nm, outline, thick, m or M["fin"], lvl=lvl, taper=root_taper(0, min(p[0] for p in outline), 0.45))
        o.rotation_euler = (math.radians(droop) * s, 0, math.radians(yaw) * s)
        apply_xform(o)
        o.location = (x, s * y, z)
        fins.append(o)
    return fins


def finish_fish(name, body_parts, tail_parts, tail_root, fins=(), extra_children=()):
    root = join(body_parts, "Fish")
    apply_xform(root, loc=True)
    tail = join_at(tail_parts, "Tail", tail_root)
    tail.parent = root
    for f in fins:
        apply_xform(f, loc=False)
        f.parent = root
    for c in extra_children:
        c.parent = root
    for o in bpy.context.scene.objects:
        set_smooth(o)
    export(name)


def body_mat_fn(belly_thr=-0.3, bands=(), band_mat=2):
    def f(x, a):
        for lo, hi in bands:
            if lo <= x <= hi:
                return band_mat
        return 1 if math.sin(a) < belly_thr else 0
    return f


def tail_outline_fork(L=0.45, H=0.5, notch=0.22, root=0.08, inset=-0.05, sweep=0.06):
    """Forked / crescent tail in local (x<0 backwards, z up)."""
    return [(inset, root), (-L * 0.45, H * 0.6), (-L * 0.85 - sweep, H * 0.97), (-L - sweep, H),
            (-L * 0.92, H * 0.78), (-L * 0.62, H * 0.3), (-notch, 0.0),
            (-L * 0.62, -H * 0.3), (-L * 0.92, -H * 0.78), (-L - sweep, -H), (-L * 0.85 - sweep, -H * 0.97),
            (-L * 0.45, -H * 0.6), (inset, -root)]


def tail_outline_round(L=0.4, H=0.35, root=0.08, inset=-0.05, lobes=0.0):
    pts = [(inset, root)]
    for i in range(11):
        a = math.pi / 2 - math.pi * i / 10
        rr = 1.0 - lobes * (1 - abs(math.sin(a * 2))) if lobes else 1.0
        pts.append((-L * 0.35 - L * 0.65 * math.cos(a) * rr, H * math.sin(a) * (1 if i not in (0, 10) else 0.95)))
    pts.append((inset, -root))
    return pts


def fan(L, H, root=0.06, inset=-0.04, scallop=0, n=12):
    """Fan-shaped tail (rounded end, optional scalloped edge)."""
    pts = [(inset, root)]
    for i in range(n + 1):
        a = math.pi / 2 - math.pi * i / n
        rr = 1.0 - (0.12 if (scallop and i % 2 == 1) else 0)
        pts.append((-L * (0.3 + 0.7 * math.cos(a) * rr), H * math.sin(a) * rr))
    pts.append((inset, -root))
    return pts


def top_fin(name, B, xf, xb, h, m, sweep=0.1, shape="tri", bottom=False, emb=0.06, thick=0.04, lvl=1, dens=2):
    """Dorsal (or ventral when bottom=True) fin whose root follows the body outline from xf (front) to xb (back)."""
    sg = -1 if bottom else 1
    zt = (lambda x: B.bot_z(x)) if bottom else (lambda x: B.top_z(x))
    span = xf - xb
    if shape == "tri":      # swept sickle
        tip = [(xf - span * 0.25, zt(xf) + sg * h * 0.55), (xb + span * 0.15 - sweep, zt(xb + span * 0.3) + sg * h),
               (xb - sweep * 1.1, zt(xb) + sg * h * 0.85), (xb + span * 0.05, zt(xb) + sg * h * 0.25)]
    elif shape == "round":  # rounded paddle
        tip = []
        for i in range(1, 8):
            a = math.pi * i / 8
            x = xf - span * (0.5 - 0.5 * math.cos(a)) - sweep * math.sin(a) * 0.5
            tip.append((x, zt(x) + sg * h * math.sin(a) ** 0.7))
    else:                   # low ridge
        tip = []
        for i in range(1, 10):
            t = i / 10
            x = xf - span * t
            tip.append((x, zt(x) + sg * h * min(1, t * 4, (1 - t) * 4) ** 0.6))
    root = [(xb + span * t, zt(xb + span * t) - sg * emb) for t in (0.0, 0.33, 0.66, 1.0)]
    outline = [root[-1]] + tip + root[:-1][::1]
    outline = [root[3]] + tip + [root[0], root[1], root[2]]
    if bottom:
        outline = outline[::-1]
    return slab(name, outline, thick, m, lvl=lvl, dens=dens,
                taper=lambda u, v: 1.0 - 0.55 * min(1, abs(v - zt(u)) / max(h, 1e-3)))


# =====================================================================================  species
def fish_tuna():
    reset()
    M = fish_mats("#a9bdd6", "#f4f3ee", "#8fa6c4", "#f5d046")
    B = Body([(-0.52, 0.045, 0.06, 0.06, 0.0), (-0.36, 0.09, 0.14, 0.13, 0.0), (-0.05, 0.25, 0.38, 0.35, 0.0),
              (0.3, 0.29, 0.43, 0.40, 0.0), (0.62, 0.25, 0.34, 0.32, -0.01), (0.85, 0.16, 0.21, 0.19, -0.02),
              (0.98, 0.05, 0.07, 0.06, -0.03)], p=2.0, seg=22, n=26)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.28))
    parts = [body]
    parts += eye_pair(M, B, 0.6, 0.22, 0.135)
    parts.append(mouth_smile(M, B, 0.9, -0.38, 0.1, 0.035))
    parts.append(top_fin("Dorsal", B, 0.38, 0.0, 0.3, M["fin"], sweep=0.12))
    parts.append(top_fin("Dorsal2", B, -0.08, -0.25, 0.17, M["accent"], sweep=0.08))
    parts.append(top_fin("Anal", B, -0.08, -0.25, 0.16, M["accent"], sweep=0.08, bottom=True))
    for i, x in enumerate((-0.31, -0.38, -0.45)):
        for bottom in (False, True):
            parts.append(top_fin(f"Finlet{i}{bottom}", B, x + 0.03, x - 0.03, 0.06, M["accent"], sweep=0.03, bottom=bottom, emb=0.03, thick=0.03, dens=1))
    fins = pect_pair(M, 0.38, B.surf_y(0.38, -0.08) - 0.04, -0.08,
                     [(0.05, 0.0), (0.0, 0.1), (-0.12, 0.2), (-0.3, 0.24), (-0.36, 0.14), (-0.22, 0.02), (0.0, -0.05)], droop=-125, yaw=-15)
    tail = slab("TailFin", tail_outline_fork(0.5, 0.5, 0.2, 0.06), 0.045, M["fin"], lvl=1, dens=2, taper=root_taper(0, -0.5, 0.4))
    tail.location = (-0.5, 0, 0)
    finish_fish("fish_tuna", parts, [tail], (-0.5, 0, 0), fins)


def fish_swordfish():
    reset()
    M = fish_mats("#9fb0c8", "#eef1f4", "#8597b3", "#c9d3df")
    B = Body([(-0.55, 0.04, 0.055, 0.055, 0.0), (-0.38, 0.08, 0.12, 0.11, 0.0), (-0.05, 0.19, 0.3, 0.27, 0.0),
              (0.25, 0.21, 0.32, 0.28, 0.0), (0.48, 0.17, 0.24, 0.21, -0.01), (0.6, 0.09, 0.12, 0.1, -0.0)],
             p=2.0, seg=22, n=26, nose_ext=0.0)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.3))
    parts = [body]
    sword = lathe("Sword", [(0, 0.0)], M["accent"])  # placeholder replaced below
    bpy.data.objects.remove(sword)
    parts.append(tube("Sword", [(0.5, 0, 0.0), (0.62, 0, -0.005), (0.8, 0, -0.01), (1.0, 0, -0.01)], [0.085, 0.06, 0.035, 0.008], M["accent"], bres=2, ures=4))
    parts += eye_pair(M, B, 0.38, 0.25, 0.12, fwd=0.45)
    parts.append(mouth_smile(M, B, 0.52, -0.45, 0.07, 0.025, r=0.015))
    parts.append(top_fin("Dorsal", B, 0.3, 0.05, 0.5, M["fin"], sweep=0.18, thick=0.04))
    parts.append(top_fin("Anal", B, -0.12, -0.3, 0.15, M["fin"], sweep=0.08, bottom=True))
    fins = pect_pair(M, 0.28, B.surf_y(0.28, -0.12) - 0.03, -0.12,
                     [(0.05, 0.0), (-0.05, 0.06), (-0.3, 0.09), (-0.5, 0.03), (-0.25, -0.02), (0.0, -0.05)], droop=-125)
    tail = slab("TailFin", tail_outline_fork(0.45, 0.55, 0.2, 0.05, sweep=0.1), 0.045, M["fin"], taper=root_taper(0, -0.45, 0.4))
    tail.location = (-0.53, 0, 0)
    finish_fish("fish_swordfish", parts, [tail], (-0.53, 0, 0), fins)


def fib_dirs(n):
    ga = math.pi * (3 - math.sqrt(5))
    for i in range(n):
        z = 1 - 2 * (i + 0.5) / n
        r = math.sqrt(1 - z * z)
        yield Vector((math.cos(ga * i) * r, math.sin(ga * i) * r, z))


def sphere_keys(cx, R, sx=1.0, sz=1.0, zc=0.0, n=9, tail_w=0.05, x_from=None):
    keys = []
    x0 = cx - R * sx
    for i in range(n + 1):
        t = i / n
        a = math.pi * (1 - t)
        x = cx + R * sx * math.cos(a)
        r = R * math.sin(a)
        keys.append((x, max(r, 0.04 if i in (0, n) else r), max(r * sz, 0.04 if i in (0, n) else r * sz), max(r * sz, 0.04 if i in (0, n) else r * sz), zc))
    return keys


def fish_puffer():
    reset()
    M = fish_mats("#f2d48a", "#fbf3dc", "#e9c070", "#c99a3a")
    R = 0.66
    keys = [(-0.55, 0.05, 0.07, 0.07, 0.0), (-0.42, 0.13, 0.16, 0.15, 0.0)]
    for i in range(1, 12):
        a = math.pi * (1 - i / 12)
        x = 0.12 + R * math.cos(a)
        if x <= -0.38:
            continue
        r = R * math.sin(a)
        keys.append((x, r * 0.97, r, r * 0.95, 0.0))
    keys.append((0.12 + R + 0.0, 0.08, 0.08, 0.08, -0.03))
    B = Body(keys, p=2.0, seg=22, n=26, nose_ext=0.0)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.35))
    parts = [body]
    parts += eye_pair(M, B, 0.5, 0.3, 0.17, fwd=0.6, embed=0.4)
    # pursed "o" lips
    z_m = -0.12
    xm = B.front_x(0, z_m)
    lips = torus("Lips", (xm + 0.01, 0, z_m), 0.055, 0.03, M["accent"], seg=16, mseg=8, rot=(0, math.radians(90), 0))
    parts.append(lips)
    parts.append(sphere("MouthHole", (xm + 0.02, 0, z_m), (0.02, 0.035, 0.035), M["pupil"], 10, 6))
    # soft spikes over the back and sides
    rnd = random.Random(3)
    c = Vector((0.12, 0, 0))
    for i, d in enumerate(fib_dirs(110)):
        if d.x > 0.55 or d.x < -0.75:
            continue
        if d.x > 0.2 and abs(d.y) > 0.5 and d.z > -0.2:   # keep eyes clear
            continue
        if d.z < -0.55:
            continue
        p = c + d * (R * 0.96)
        sp = lathe(f"Spike{i}", [(0, 0.09), (0.016, 0.068), (0.032, 0.0), (0.028, -0.04)], M["accent"], seg=6)
        sp.rotation_mode = "QUATERNION"
        sp.rotation_quaternion = Vector((0, 0, 1)).rotation_difference((d + Vector((-0.35, 0, 0))).normalized())
        sp.location = p
        parts.append(sp)
    parts.append(top_fin("Dorsal", B, -0.3, -0.46, 0.14, M["fin"], shape="round", sweep=0.03, emb=0.08))
    fins = pect_pair(M, 0.15, B.surf_y(0.15, -0.05) - 0.04, -0.05,
                     fan(0.2, 0.13, root=0.04), droop=-95, yaw=0)
    tail = slab("TailFin", fan(0.36, 0.26, root=0.06, scallop=1, n=10), 0.045, M["fin"], taper=root_taper(0, -0.36, 0.5))
    tail.location = (-0.5, 0, 0)
    finish_fish("fish_puffer", parts, [tail], (-0.5, 0, 0), fins)


def fish_catfish():
    reset()
    M = fish_mats("#bba98f", "#efe6d6", "#a8937a", "#5f4f40")
    B = Body([(-0.52, 0.05, 0.07, 0.07, 0.0), (-0.3, 0.13, 0.17, 0.15, 0.0), (0.05, 0.25, 0.27, 0.24, 0.0),
              (0.45, 0.33, 0.27, 0.21, -0.02), (0.75, 0.33, 0.22, 0.17, -0.04), (0.92, 0.26, 0.15, 0.11, -0.05),
              (0.99, 0.1, 0.06, 0.05, -0.05)], p=2.3, seg=22, n=26, nose_ext=0.0)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.3))
    parts = [body]
    parts += eye_pair(M, B, 0.68, 0.55, 0.12, fwd=0.55, upw=0.35, embed=0.5)
    parts.append(mouth_smile(M, B, 0.95, -0.35, 0.22, 0.04, r=0.02))
    # whiskers: long from the mouth corners, short from the chin
    for s in (1, -1):
        y0 = s * 0.2
        z0 = B.ZC(0.9) - 0.02
        x0 = B.front_x(y0, z0) - 0.03
        parts.append(tube(f"Whisk{s}", [(x0, y0, z0), (x0 + 0.12, y0 + s * 0.16, z0 + 0.02), (x0 + 0.1, y0 + s * 0.38, z0 - 0.08),
                                        (x0 - 0.02, y0 + s * 0.52, z0 - 0.2)], [0.03, 0.022, 0.014, 0.006], M["accent"], bres=1, ures=6))
        y1 = s * 0.08
        z1 = B.bot_z(0.85) + 0.03
        x1 = 0.84
        parts.append(tube(f"Chin{s}", [(x1, y1, z1), (x1 + 0.04, y1 + s * 0.04, z1 - 0.1), (x1 + 0.0, y1 + s * 0.08, z1 - 0.2)],
                          [0.018, 0.013, 0.005], M["accent"], bres=1, ures=5))
    parts.append(top_fin("Dorsal", B, 0.3, 0.1, 0.24, M["fin"], shape="tri", sweep=0.06))
    parts.append(top_fin("Adipose", B, -0.05, -0.35, 0.1, M["fin"], shape="ridge", emb=0.05))
    parts.append(top_fin("Anal", B, 0.05, -0.38, 0.11, M["fin"], shape="ridge", bottom=True, emb=0.05))
    fins = pect_pair(M, 0.42, B.surf_y(0.42, -0.12) - 0.05, -0.12,
                     [(0.05, 0.0), (-0.05, 0.1), (-0.25, 0.14), (-0.33, 0.06), (-0.2, -0.03), (0.0, -0.05)], droop=-100)
    tail = slab("TailFin", fan(0.38, 0.27, root=0.06, n=10), 0.045, M["fin"], taper=root_taper(0, -0.38, 0.45))
    tail.location = (-0.5, 0, 0)
    finish_fish("fish_catfish", parts, [tail], (-0.5, 0, 0), fins)


def fish_koi():
    reset()
    M = fish_mats("#f7f2ea", "#fbf8f3", "#f3e6d8", "#f07b3a")
    B = Body([(-0.45, 0.05, 0.07, 0.07, 0.0), (-0.28, 0.13, 0.17, 0.15, 0.0), (0.05, 0.27, 0.37, 0.31, 0.0),
              (0.4, 0.28, 0.36, 0.3, -0.01), (0.7, 0.22, 0.25, 0.22, -0.03), (0.88, 0.13, 0.13, 0.13, -0.05),
              (0.94, 0.07, 0.07, 0.07, -0.06)], p=2.0, seg=22, n=26)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.35))
    parts = [body]
    # orange patches pressed onto the body
    for i, (x, zr, sy, sx) in enumerate(((0.62, 0.55, 0.16, 0.2), (0.15, 0.7, 0.22, 0.32), (-0.18, 0.45, 0.14, 0.18), (0.32, 0.05, 0.12, 0.15))):
        for s in ((1, -1) if i != 1 else (1,)):
            z = B.ZC(x) + zr * B.HT(x)
            if i == 1:   # saddle patch across the back
                parts.append(sphere(f"Spot{i}", (x, 0, B.top_z(x) - 0.145), (sx, B.W(x) * 0.78, 0.17), M["accent"], 14, 8))
                continue
            y = B.surf_y(x, z)
            n = B.normal(x, s * y, z)
            o = sphere(f"Spot{i}{s}", Vector((x, s * y, z)) - n * 0.062, (sx * 1.1, sy * 1.15, 0.075), M["accent"], 12, 7, look=n)
            parts.append(o)
    parts += eye_pair(M, B, 0.64, 0.2, 0.12, fwd=0.45, embed=0.45)
    parts.append(mouth_smile(M, B, 0.9, -0.2, 0.06, 0.02, r=0.016))
    for s in (1, -1):
        x0 = 0.88
        parts.append(tube(f"Barbel{s}", [(x0, s * 0.07, -0.08), (x0 + 0.05, s * 0.13, -0.13), (x0 + 0.02, s * 0.17, -0.2)], [0.016, 0.011, 0.005], M["fin"], bres=1, ures=4))
    parts.append(top_fin("Dorsal", B, 0.3, -0.22, 0.17, M["fin"], shape="round", sweep=0.04))
    fins = pect_pair(M, 0.5, B.surf_y(0.5, -0.15) - 0.04, -0.15, fan(0.28, 0.14, root=0.04), droop=-110)
    tail_ol = [(-0.05, 0.07), (-0.22, 0.25), (-0.5, 0.42), (-0.6, 0.36), (-0.52, 0.18), (-0.38, 0.0),
               (-0.52, -0.18), (-0.6, -0.36), (-0.5, -0.42), (-0.22, -0.25), (-0.05, -0.07)]
    tail = slab("TailFin", tail_ol, 0.035, M["fin"], taper=root_taper(0, -0.6, 0.35))
    tail.location = (-0.43, 0, 0)
    finish_fish("fish_koi", parts, [tail], (-0.43, 0, 0), fins)


def front_pts(B, zrel_x, zrel, half_w, n=7, curve=0.0, inset=0.0):
    zc = B.ZC(zrel_x) + zrel * B.HT(zrel_x)
    out = []
    for i in range(n):
        s = -1 + 2 * i / (n - 1) if n > 1 else 0
        y, z = s * half_w, zc + curve * s * s
        out.append(Vector((B.front_x(y, z) - inset, y, z)))
    return out


def fish_shark():
    reset()
    M = fish_mats("#a3b4c4", "#f3f5f7", "#93a6b8", "#7d90a3")
    B = Body([(-0.5, 0.05, 0.07, 0.06, 0.02), (-0.3, 0.12, 0.16, 0.13, 0.02), (0.0, 0.25, 0.33, 0.27, 0.0),
              (0.35, 0.27, 0.34, 0.28, -0.02), (0.65, 0.22, 0.26, 0.2, -0.02), (0.87, 0.13, 0.15, 0.1, 0.0),
              (0.99, 0.04, 0.05, 0.04, 0.02)], p=2.0)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.2))
    parts = [body]
    parts += eye_pair(M, B, 0.62, 0.28, 0.12, fwd=0.4)
    parts.append(mouth_smile(M, B, 0.75, -0.55, 0.17, 0.06, r=0.02, depth=0.01))
    tp = front_pts(B, 0.75, -0.55, 0.13, n=4, curve=0.06 * (0.13 / 0.17) ** 2, inset=0.012)
    parts += teeth(M, [(p + Vector((0.0, 0, -0.012)), (0.3, 0, -1)) for p in tp], 0.06)
    for s in (1, -1):
        for k in range(3):
            x = 0.38 - k * 0.07
            pts = []
            for t in (-1, -0.5, 0, 0.5, 1):
                z = B.ZC(x) + t * 0.11
                pts.append((x + 0.012 * (1 - t * t), s * (B.surf_y(x, z) - 0.008), z))
            parts.append(tube(f"Gill{s}{k}", pts, [0.008, 0.013, 0.014, 0.013, 0.008], M["accent"], bres=1, ures=3))
    parts.append(top_fin("Dorsal", B, 0.28, -0.02, 0.42, M["fin"], sweep=0.16))
    parts.append(top_fin("Dorsal2", B, -0.25, -0.35, 0.1, M["fin"], sweep=0.05, dens=1))
    parts.append(top_fin("Anal", B, -0.22, -0.33, 0.09, M["fin"], sweep=0.05, bottom=True, dens=1))
    fins = pect_pair(M, 0.33, B.surf_y(0.33, -0.12) - 0.05, -0.12,
                     [(0.06, 0.0), (0.0, 0.12), (-0.15, 0.3), (-0.3, 0.38), (-0.27, 0.2), (-0.13, 0.02), (0.0, -0.05)], droop=-140, yaw=-10)
    tail_ol = [(-0.04, 0.07), (-0.2, 0.25), (-0.4, 0.5), (-0.5, 0.55), (-0.46, 0.36), (-0.32, 0.1), (-0.25, 0.0),
               (-0.33, -0.12), (-0.38, -0.27), (-0.3, -0.27), (-0.15, -0.12), (-0.04, -0.06)]
    tail = slab("TailFin", tail_ol, 0.045, M["fin"], taper=root_taper(0, -0.5, 0.4))
    tail.location = (-0.48, 0, 0.02)
    finish_fish("fish_shark", parts, [tail], (-0.48, 0, 0.02), fins)


def fish_eel():
    reset()
    M = fish_mats("#b2c98f", "#eef0c8", "#93b072", "#6f8f5a")
    B = Body([(-0.42, 0.09, 0.11, 0.1, 0.0), (-0.1, 0.11, 0.13, 0.12, 0.0), (0.3, 0.12, 0.145, 0.13, 0.0),
              (0.62, 0.145, 0.17, 0.15, 0.0), (0.82, 0.16, 0.18, 0.15, 0.0), (0.94, 0.12, 0.13, 0.11, -0.01),
              (1.0, 0.05, 0.06, 0.05, -0.02)], p=2.0, seg=18, n=26)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.35))
    parts = [body]
    parts += eye_pair(M, B, 0.8, 0.45, 0.125, fwd=0.5, upw=0.3, embed=0.4)
    parts.append(mouth_smile(M, B, 0.96, -0.3, 0.07, 0.025, r=0.014))
    parts.append(top_fin("Dorsal", B, 0.55, -0.43, 0.08, M["fin"], shape="ridge", emb=0.05, thick=0.03))
    parts.append(top_fin("Anal", B, 0.2, -0.43, 0.06, M["fin"], shape="ridge", bottom=True, emb=0.05, thick=0.03))
    fins = pect_pair(M, 0.72, B.surf_y(0.72, -0.03) - 0.03, -0.03, fan(0.12, 0.07, root=0.03, n=8), droop=-100, yaw=-10)
    TB = Body([(-1.0, 0.012, 0.014, 0.014, 0.0), (-0.85, 0.045, 0.055, 0.055, 0.0), (-0.6, 0.075, 0.095, 0.088, 0.0),
               (-0.36, 0.092, 0.112, 0.102, 0.0)], p=2.0, seg=18, n=14, nose_ext=0.0)
    tbody = TB.build("TailBody", [M["body"], M["belly"]], body_mat_fn(-0.35))
    tfin = slab("TailFin", [(0.0, 0.11), (-0.3, 0.15), (-0.55, 0.13), (-0.66, 0.0), (-0.55, -0.12), (-0.3, -0.13), (0.0, -0.1)],
                0.025, M["fin"], taper=root_taper(0, -0.66, 0.6))
    tfin.location = (-0.38, 0, 0)
    finish_fish("fish_eel", parts, [tbody, tfin], (-0.4, 0, 0), fins)


def fish_angler():
    reset()
    M = fish_mats("#a99bc0", "#d8cfe6", "#9586b0", "#ffd85a", accent_emit="#ffd23f")
    B = Body([(-0.45, 0.05, 0.07, 0.07, 0.0), (-0.3, 0.14, 0.18, 0.16, 0.0), (0.0, 0.36, 0.42, 0.4, 0.0),
              (0.35, 0.45, 0.48, 0.47, 0.0), (0.68, 0.38, 0.38, 0.42, -0.03), (0.9, 0.22, 0.2, 0.28, -0.05),
              (0.98, 0.07, 0.05, 0.08, -0.06)], p=2.2)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.3))
    parts = [body]
    zm = -0.12
    xm = B.front_x(0, zm)
    parts.append(sphere("Maw", (xm - 0.1, 0, zm), (0.14, 0.34, 0.11), M["pupil"], 20, 10))
    for upper in (True, False):
        pts = front_pts(B, 0.9, 0, 0.3, n=6 if upper else 5, curve=0.08)
        for k, p in enumerate(pts):
            z = zm + (0.07 if upper else -0.08) + 0.12 * (p.y / 0.3) ** 2
            q = Vector((B.front_x(p.y, z) - 0.02, p.y, z))
            parts += teeth(M, [(q, (0.35, 0, -1) if upper else (0.35, 0, 1))], 0.075 if k in (1, len(pts) - 2) else 0.06)
    top = B.top_z(0.55)
    parts.append(tube("Lure", [(0.5, 0, top - 0.04), (0.58, 0, top + 0.3), (0.85, 0, top + 0.45), (1.05, 0, top + 0.32)],
                      [0.03, 0.025, 0.02, 0.016], M["fin"], bres=1, ures=6))
    parts.append(sphere("Bulb", (1.07, 0, top + 0.27), 0.085, M["accent"], 16, 10))
    parts += eye_pair(M, B, 0.62, 0.55, 0.11, fwd=0.6, upw=0.25, embed=0.4)
    parts.append(top_fin("Dorsal", B, -0.0, -0.3, 0.16, M["fin"], shape="round", sweep=0.04))
    parts.append(top_fin("Anal", B, -0.05, -0.3, 0.13, M["fin"], shape="round", sweep=0.04, bottom=True))
    fins = pect_pair(M, 0.1, B.surf_y(0.1, -0.15) - 0.05, -0.15, fan(0.25, 0.16, root=0.04, scallop=1, n=8), droop=-115, yaw=-15)
    tail = slab("TailFin", fan(0.36, 0.28, root=0.06, scallop=1, n=10), 0.045, M["fin"], taper=root_taper(0, -0.36, 0.5))
    tail.location = (-0.43, 0, 0)
    finish_fish("fish_angler", parts, [tail], (-0.43, 0, 0), fins)


def fish_clown():
    reset()
    M = fish_mats("#f8a057", "#fbc690", "#f59447", "#ffffff")
    bands = [(0.46, 0.6), (0.02, 0.17), (-0.4, -0.3)]
    edges = []
    for lo, hi in bands:
        edges += [lo - 0.025, lo, hi, hi + 0.025]
    B = Body([(-0.45, 0.05, 0.08, 0.08, 0.0), (-0.3, 0.12, 0.18, 0.17, 0.0), (0.0, 0.26, 0.4, 0.36, 0.0),
              (0.35, 0.27, 0.4, 0.36, -0.01), (0.68, 0.22, 0.28, 0.25, -0.03), (0.88, 0.13, 0.15, 0.13, -0.05),
              (0.96, 0.05, 0.06, 0.05, -0.05)], p=2.0, extra=edges)

    def mf(x, a):
        for lo, hi in bands:
            if lo <= x <= hi:
                return 2
            if lo - 0.025 <= x < lo or hi < x <= hi + 0.025:
                return 3
        return 1 if math.sin(a) < -0.35 else 0
    body = B.build("Body", [M["body"], M["belly"], M["accent"], M["pupil"]], mf)
    parts = [body]
    parts += eye_pair(M, B, 0.7, 0.22, 0.12, fwd=0.4)
    parts.append(mouth_smile(M, B, 0.9, -0.3, 0.08, 0.03, r=0.016))
    parts.append(top_fin("Dorsal", B, 0.3, -0.22, 0.2, M["fin"], shape="round", sweep=0.05))
    parts.append(top_fin("Anal", B, -0.02, -0.25, 0.15, M["fin"], shape="round", sweep=0.04, bottom=True))
    fins = pect_pair(M, 0.42, B.surf_y(0.42, -0.12) - 0.04, -0.12, fan(0.26, 0.15, root=0.04, n=8), droop=-115, yaw=-15)
    tail = slab("TailFin", fan(0.38, 0.3, root=0.06, n=10), 0.045, M["fin"], taper=root_taper(0, -0.38, 0.45))
    tail.location = (-0.43, 0, 0)
    finish_fish("fish_clown", parts, [tail], (-0.43, 0, 0), fins)


def fish_blobfish():
    reset()
    M = fish_mats("#f3b9bd", "#f9d8da", "#eaa5ab", "#e38e97")
    B = Body([(-0.42, 0.05, 0.07, 0.07, -0.05), (-0.25, 0.18, 0.2, 0.18, -0.05), (0.1, 0.42, 0.42, 0.38, -0.02),
              (0.45, 0.46, 0.43, 0.4, 0.0), (0.75, 0.37, 0.33, 0.38, -0.08), (0.92, 0.2, 0.17, 0.24, -0.14),
              (0.98, 0.06, 0.05, 0.07, -0.15)], p=2.0)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.4))
    parts = [body]
    parts.append(sphere("Nose", (0.92, 0, -0.16), (0.16, 0.2, 0.2), M["body"], 18, 12))
    parts.append(sphere("NoseTip", (1.0, 0, -0.24), (0.1, 0.13, 0.11), M["accent"], 14, 8))
    parts += eye_pair(M, B, 0.68, 0.3, 0.11, fwd=0.5, embed=0.4, lid=M["body"])
    parts.append(mouth_smile(M, B, 0.8, -0.75, 0.16, 0.06, r=0.02, frown=True, depth=0.01))
    for s in (1, -1):   # rosy cheeks
        x, z = 0.72, -0.2
        y = B.surf_y(x, z)
        parts.append(sphere(f"Cheek{s}", (x, s * (y - 0.02), z), (0.09, 0.03, 0.06), M["accent"], 12, 6, look=(0.2, s, 0)))
    fins = pect_pair(M, 0.25, B.surf_y(0.25, -0.15) - 0.05, -0.15, fan(0.2, 0.13, root=0.04, n=8), droop=-110, yaw=-15)
    tail = slab("TailFin", fan(0.28, 0.2, root=0.05, n=8), 0.045, M["fin"], taper=root_taper(0, -0.28, 0.5))
    tail.location = (-0.4, 0, -0.05)
    finish_fish("fish_blobfish", parts, [tail], (-0.4, 0, -0.05), fins)


def fish_sunfish():
    reset()
    M = fish_mats("#c6d0da", "#eef1f4", "#b3bfcb", "#9eacbb")
    B = Body([(-0.58, 0.08, 0.42, 0.42, 0.0), (-0.48, 0.15, 0.56, 0.54, 0.0), (-0.2, 0.21, 0.63, 0.6, 0.0),
              (0.2, 0.21, 0.6, 0.57, 0.0), (0.55, 0.17, 0.42, 0.4, 0.0), (0.76, 0.11, 0.22, 0.2, 0.0),
              (0.84, 0.05, 0.07, 0.06, 0.0)], p=2.2, nose_ext=0.01, tail_ext=0.0)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.5))
    parts = [body]
    parts += eye_pair(M, B, 0.55, 0.22, 0.12, fwd=0.4)
    xm = B.front_x(0, -0.02)
    parts.append(torus("Lips", (xm - 0.005, 0, -0.02), 0.045, 0.022, M["accent"], seg=16, mseg=8, rot=(0, math.radians(90), 0)))
    parts.append(sphere("MouthHole", (xm + 0.012, 0, -0.02), (0.015, 0.03, 0.03), M["pupil"], 10, 6))
    parts.append(top_fin("Dorsal", B, -0.18, -0.5, 0.48, M["fin"], sweep=0.12, thick=0.05))
    parts.append(top_fin("Anal", B, -0.18, -0.5, 0.48, M["fin"], sweep=0.12, thick=0.05, bottom=True))
    for s in (1, -1):   # faint spots
        for k, (x, z) in enumerate(((0.1, 0.25), (-0.15, -0.1), (0.25, -0.3), (-0.3, 0.3))):
            y = B.surf_y(x, z)
            n = B.normal(x, s * y, z)
            parts.append(sphere(f"Spot{s}{k}", Vector((x, s * y, z)) - n * 0.006, (0.07, 0.07, 0.012), M["accent"], 12, 6, look=n))
    fins = pect_pair(M, 0.3, B.surf_y(0.3, 0.0) - 0.03, 0.0, fan(0.16, 0.1, root=0.04, n=8), droop=-100, yaw=-20)
    tail = slab("TailFin", fan(0.24, 0.5, root=0.4, inset=-0.08, scallop=1, n=12), 0.045, M["fin"], taper=root_taper(0, -0.24, 0.6))
    tail.location = (-0.5, 0, 0)
    finish_fish("fish_sunfish", parts, [tail], (-0.5, 0, 0), fins)


def fish_piranha():
    reset()
    M = fish_mats("#b8c3cc", "#f39a7c", "#a5b1bb", "#4d5862")
    B = Body([(-0.45, 0.05, 0.08, 0.08, 0.0), (-0.3, 0.11, 0.17, 0.17, 0.0), (0.0, 0.24, 0.45, 0.45, 0.0),
              (0.35, 0.25, 0.46, 0.47, -0.02), (0.65, 0.21, 0.34, 0.38, -0.05), (0.88, 0.13, 0.17, 0.22, -0.08),
              (0.97, 0.05, 0.06, 0.09, -0.1)], p=2.0)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.15))
    parts = [body]
    parts.append(sphere("Jaw", (0.84, 0, -0.24), (0.16, 0.17, 0.1), M["belly"], 18, 10))
    tp = [Vector((0.94 - 0.06 * abs(s) ** 2, s * 0.11, -0.17)) for s in (-1, -0.5, 0, 0.5, 1)]
    parts += teeth(M, [(p, (0.25, 0, 1)) for p in tp], 0.075)
    parts += eye_pair(M, B, 0.62, 0.28, 0.12, fwd=0.4)
    for s in (1, -1):   # grumpy brows
        x0 = 0.62
        z0 = B.ZC(x0) + 0.28 * B.HT(x0) + 0.15
        pts = []
        for t in (-1, 0, 1):
            x = x0 + t * 0.09
            z = z0 - 0.04 * t
            pts.append((x, s * (B.surf_y(x, z) + 0.005), z))
        parts.append(tube(f"Brow{s}", pts, [0.018, 0.026, 0.018], M["accent"], bres=1, ures=4))
    parts.append(top_fin("Dorsal", B, 0.1, -0.15, 0.2, M["fin"], shape="round", sweep=0.05))
    parts.append(top_fin("Anal", B, 0.05, -0.25, 0.16, M["fin"], shape="tri", sweep=0.05, bottom=True))
    fins = pect_pair(M, 0.42, B.surf_y(0.42, -0.15) - 0.04, -0.15, fan(0.22, 0.12, root=0.04, n=8), droop=-115, yaw=-15)
    tail = slab("TailFin", tail_outline_fork(0.4, 0.42, 0.17, 0.07), 0.045, M["fin"], taper=root_taper(0, -0.4, 0.45))
    tail.location = (-0.43, 0, 0)
    finish_fish("fish_piranha", parts, [tail], (-0.43, 0, 0), fins)


def fish_jelly():
    reset()
    M = fish_mats("#e7b8f2", "#f6dcfb", "#d9a2ea", "#f4a9d2")
    z0 = 0.35
    prof = [(0, 0.6), (0.18, 0.585), (0.34, 0.54), (0.46, 0.45), (0.55, 0.33), (0.61, 0.19), (0.64, 0.07), (0.63, 0.0),
            (0.58, -0.03), (0.5, -0.0), (0.35, 0.04), (0.15, 0.07), (0, 0.08)]
    rim_ring = 7
    bell = lathe("Bell", [(r, z + z0) for r, z in prof], M["body"], seg=36, mats=[M["body"], M["belly"]],
                 mat_fn=lambda i, j: 1 if i >= rim_ring else 0)
    for v in bell.data.vertices:   # scalloped rim
        r = math.hypot(v.co.x, v.co.y)
        if r > 0.45 and v.co.z < z0 + 0.1:
            a = math.atan2(v.co.y, v.co.x)
            k = 1 + 0.035 * math.cos(9 * a)
            v.co.x *= k
            v.co.y *= k
            v.co.z -= 0.03 * (math.cos(9 * a) + 1) * 0.5
    parts = [bell]

    def bell_r(z):
        for (r1, z1), (r2, z2) in zip(prof, prof[1:]):
            if z2 <= z - z0 <= z1:
                t = (z - z0 - z2) / (z1 - z2) if z1 != z2 else 0
                return r2 + (r1 - r2) * t
        return 0.6
    for s in (1, -1):
        a = math.radians(24) * s
        ze = z0 + 0.3
        r = bell_r(ze)
        n = Vector((math.cos(a), math.sin(a), 0.35)).normalized()
        c = Vector((math.cos(a) * r, math.sin(a) * r, ze)) - n * 0.06
        parts += eye(M, c, 0.13, Vector((math.cos(a) * 1.4, math.sin(a), 0.2)), name=f"Eye{s}")
        a2 = math.radians(42) * s
        zc = z0 + 0.18
        rc = bell_r(zc)
        parts.append(sphere(f"Cheek{s}", (math.cos(a2) * (rc - 0.01), math.sin(a2) * (rc - 0.01), zc), (0.07, 0.07, 0.045),
                            M["accent"], 12, 6, look=(math.cos(a2), math.sin(a2), 0)))
    pts = []
    zm = z0 + 0.15
    rm = bell_r(zm)
    for i in range(7):
        a = math.radians(-14 + 28 * i / 6)
        dz = 0.025 * (((i - 3) / 3) ** 2)
        pts.append((math.cos(a) * (rm + 0.004), math.sin(a) * (rm + 0.004), zm + dz))
    parts.append(tube("Mouth", pts, 0.016, M["pupil"], bres=1, ures=3))
    # tentacles + frilly oral arms form the 'Tail'
    rnd = random.Random(5)
    tent = []
    for k in range(10):
        a = 2 * math.pi * k / 10 + 0.15
        r0 = 0.45
        pts, rad = [], []
        for i in range(8):
            t = i / 7
            rr = r0 * (1 - 0.25 * t)
            w = 0.07 * math.sin(t * 7 + k)
            pts.append((math.cos(a) * rr + w * -math.sin(a) - 0.18 * t * t, math.sin(a) * rr + w * math.cos(a), z0 + 0.02 - 1.25 * t))
            rad.append(0.028 * (1 - t) + 0.006)
        tent.append(tube(f"Tent{k}", pts, rad, M["fin"], bres=1, ures=2))
    for k in range(4):
        a = 2 * math.pi * k / 4 + 0.6
        pts, rad = [], []
        for i in range(9):
            t = i / 8
            rr = 0.12 + 0.05 * math.sin(t * 9 + k)
            tw = a + t * 2.2
            pts.append((math.cos(tw) * rr - 0.1 * t * t, math.sin(tw) * rr, z0 + 0.05 - 0.85 * t))
            rad.append(0.07 * (1 - t) ** 0.7 + 0.01)
        tent.append(tube(f"Arm{k}", pts, rad, M["accent"], bres=2, ures=3))
    finish_fish("fish_jelly", parts, tent, (0, 0, z0 + 0.03))


def fish_crab():
    reset()
    M = fish_mats("#f39a74", "#fbe1cf", "#e9845e", "#f7c2a6")
    B = Body([(-0.42, 0.25, 0.1, 0.06, 0.0), (-0.3, 0.5, 0.24, 0.12, 0.0), (0.0, 0.66, 0.32, 0.14, 0.0),
              (0.28, 0.6, 0.27, 0.12, 0.0), (0.42, 0.42, 0.15, 0.08, -0.01), (0.47, 0.2, 0.06, 0.04, -0.01)],
             p=2.4, seg=26, n=24, nose_ext=0.0, tail_ext=0.0)
    body = B.build("Body", [M["body"], M["belly"], M["accent"]], body_mat_fn(-0.15))
    parts = [body]
    for s in (1, -1):
        base = Vector((0.28, s * 0.17, 0.18))
        tip = Vector((0.36, s * 0.24, 0.52))
        parts.append(tube(f"Stalk{s}", [base, base.lerp(tip, 0.5) + Vector((0.03, 0, 0)), tip], [0.05, 0.04, 0.035], M["body"], bres=1, ures=3))
        parts += eye(M, tip + Vector((0.02, s * 0.01, 0.06)), 0.13, (0.9, s * 0.45, 0.15), name=f"Eye{s}")
        for k in range(3):   # legs
            x = 0.12 - k * 0.2
            y0 = B.surf_y(x, 0.0) - 0.06
            pts = [(x, s * y0, 0.0), (x - 0.04, s * (y0 + 0.26), 0.14), (x - 0.1, s * (y0 + 0.42), -0.05), (x - 0.13, s * (y0 + 0.46), -0.25)]
            parts.append(tube(f"Leg{s}{k}", pts, [0.085, 0.075, 0.06, 0.03], M["fin"], bres=2, ures=4))
    parts.append(mouth_smile(M, B, 0.4, -0.2, 0.12, 0.035, r=0.018))
    for s in (1, -1):
        x, z = 0.36, -0.02
        parts.append(sphere(f"Cheek{s}", (x, s * 0.36, z), (0.07, 0.04, 0.045), M["accent"], 12, 6, look=(0.6, s, 0)))
    fins = []
    for s, nm in ((1, "FinL"), (-1, "FinR")):
        sh = Vector((0.25, s * 0.5, 0.02))
        el = Vector((0.52, s * 0.78, 0.1))
        wr = Vector((0.78, s * 0.66, 0.15))
        cp = [tube(nm + "Arm", [sh, el, wr], [0.095, 0.09, 0.085], M["body"], bres=2, ures=5)]
        cp.append(sphere(nm + "Palm", wr + Vector((0.12, -s * 0.02, 0.02)), (0.23, 0.16, 0.17), M["body"], 18, 10))
        cp.append(tube(nm + "Up", [wr + Vector((0.24, 0, 0.07)), wr + Vector((0.42, -s * 0.03, 0.13)), wr + Vector((0.52, -s * 0.06, 0.03))],
                       [0.11, 0.08, 0.025], M["accent"], bres=2, ures=4))
        cp.append(tube(nm + "Lo", [wr + Vector((0.24, 0, -0.05)), wr + Vector((0.38, -s * 0.03, -0.09)), wr + Vector((0.45, -s * 0.05, -0.02))],
                       [0.09, 0.065, 0.022], M["accent"], bres=2, ures=4))
        f = join_at(cp, nm, sh)
        fins.append(f)
    flap = sphere("Flap", (-0.38, 0, -0.05), (0.12, 0.2, 0.05), M["belly"], 14, 8)
    root = join(parts, "Fish")
    apply_xform(root, loc=True)
    tail = join_at([flap], "Tail", (-0.3, 0, -0.05))
    tail.parent = root
    for f in fins:
        f.parent = root
    for o in bpy.context.scene.objects:
        set_smooth(o)
    export("fish_crab")


def fish_octopus():
    reset()
    M = fish_mats("#f3a2b9", "#fbd9e2", "#ee8fab", "#fbd9e2")
    mantle = sphere("Mantle", (0, 0, 0), (0.5, 0.46, 0.55), M["body"], 26, 16)
    mantle.data.materials.append(M["belly"])
    apply_xform(mantle, loc=False)
    for p in mantle.data.polygons:
        if p.center.z < -0.3:
            p.material_index = 1
    mantle.rotation_euler = (0, math.radians(-25), 0)
    mantle.location = (0.0, 0, 0.3)
    parts = [mantle]
    bpy.context.view_layer.update()
    mw = mantle.matrix_world

    def surf(dirv):
        # point on the (rotated) mantle ellipsoid along a local direction
        d = Vector(dirv).normalized()
        k = 1 / math.sqrt((d.x / 0.5) ** 2 + (d.y / 0.46) ** 2 + (d.z / 0.55) ** 2)
        return mw @ (d * k)
    for s in (1, -1):
        p = surf((1, s * 0.42, -0.22))
        n = (p - mw @ Vector((0, 0, 0))).normalized()
        parts += eye(M, p - n * 0.06, 0.15, (1, s * 0.5, 0.1), name=f"Eye{s}")
        q = surf((1, s * 0.68, -0.5))
        parts.append(sphere(f"Cheek{s}", q, (0.07, 0.07, 0.045), M["accent"], 12, 6, look=(q - mw @ Vector((0, 0, 0))).normalized()))
    mp = []
    for i in range(7):
        t = -1 + 2 * i / 6
        mp.append(surf((1, t * 0.2, -0.55 + 0.06 * t * t)) + Vector((0.004, 0, 0)))
    parts.append(tube("Mouth", mp, 0.016, M["pupil"], bres=1, ures=3))
    tents = []
    for k in range(8):
        a = 2 * math.pi * (k + 0.5) / 8
        ca, sa = math.cos(a), math.sin(a)
        r0 = 0.24
        pts, rad = [], []
        for i in range(9):
            t = i / 8
            out = r0 + 0.55 * t
            back = -0.55 * t * t
            curl = 0.18 * math.sin(t * math.pi * 1.1) if t > 0.5 else 0
            x = ca * out * 0.65 + back - 0.05
            y = sa * out
            z = -0.12 - 0.28 * math.sin(t * math.pi * 0.6) + curl * 1.2 * (t - 0.5) * 2
            pts.append((x, y, z))
            rad.append(0.125 * (1 - t) ** 0.8 + 0.015)
        tents.append(tube(f"Tent{k}", pts, rad, M["body"], bres=2, ures=3))
    finish_fish("fish_octopus", parts, tents, (0.0, 0, -0.1))


FISH = ["tuna", "swordfish", "puffer", "catfish", "koi", "shark", "eel", "angler", "clown", "blobfish", "sunfish", "piranha",
        "jelly", "crab", "octopus"]


# =====================================================================================  boats
class Hull:
    """Lofted boat hull: stations from stern (t=0, x=L0) to bow (t=1, x=L1).
    Sections run port gunwale -> keel -> starboard gunwale as a superellipse (exponent p: 2 round, <2 V, >2 boxy)."""

    def __init__(self, L0, L1, beam, sheer, keel, p, n_st=24, n_sec=14):
        self.L0, self.L1 = L0, L1
        self.beam, self.sheer, self.keel, self.p = beam, sheer, keel, p
        self.n_st, self.n_sec = n_st, n_sec

    def t(self, x):
        return (x - self.L0) / (self.L1 - self.L0)

    def section(self, t):
        x = self.L0 + (self.L1 - self.L0) * t
        B, S, K, p = self.beam(t), self.sheer(t), self.keel(t), self.p(t)
        e = 2.0 / p
        pts = []
        for k in range(self.n_sec + 1):
            th = -math.pi / 2 + math.pi * k / self.n_sec
            sn, cs = math.sin(th), math.cos(th)
            pts.append((x, B * math.copysign(abs(sn) ** e, sn), S - (S - K) * abs(cs) ** e))
        return pts

    def half_width(self, x, z):
        t = min(1, max(0, self.t(x)))
        B, S, K, p = self.beam(t), self.sheer(t), self.keel(t), self.p(t)
        if z >= S:
            return B
        if z <= K:
            return 0.0
        c = ((S - z) / (S - K)) ** (p / 2)
        sn = math.sqrt(max(0, 1 - c * c))
        return B * sn ** (2 / p)

    def build(self, name, base_mats, band_fn, interior, rim, thick=0.06, transom=None):
        ts = [0.5 - 0.5 * math.cos(math.pi * i / (self.n_st - 1)) for i in range(self.n_st)]
        rings = [self.section(t) for t in ts]
        ns = self.n_sec
        nb = len(base_mats)
        mats = list(base_mats) + [interior] * nb + [rim] * nb

        def mf(i, j):
            if i < 0 or i >= len(rings) - 1:
                return band_fn(0) if transom is None else transom
            return band_fn(min(j, ns - 1 - j))
        cen = Vector(((self.L0 + self.L1) / 2, 0, (self.sheer(0.5) + self.keel(0.5)) / 2 + 0.2))
        o = rings_mesh(name, rings, mats, cyclic=False, mat_fn=mf, cap0=True, center=cen)
        sol = o.modifiers.new("solid", "SOLIDIFY")
        sol.thickness = thick
        sol.offset = -1
        sol.use_even_offset = True
        sol.use_rim = True
        sol.material_offset = nb
        sol.material_offset_rim = nb * 2
        ss = o.modifiers.new("ss", "SUBSURF")
        ss.levels = ss.render_levels = 1
        bake(o)
        set_smooth(o)
        self.ts = ts
        return o

    def gunwale_loop(self, inset=0.03, dz=0.0, n=22):
        pts = []
        ts = [0.5 - 0.5 * math.cos(math.pi * i / (n - 1)) for i in range(n)]
        for t in ts:                       # starboard (y<0) stern -> bow
            x = self.L0 + (self.L1 - self.L0) * t
            B = self.beam(t)
            pts.append((x, -max(0.0, B - inset), self.sheer(t) + dz))
        for t in reversed(ts[:-1]):        # port bow -> stern
            x = self.L0 + (self.L1 - self.L0) * t
            B = self.beam(t)
            pts.append((x, max(0.0, B - inset), self.sheer(t) + dz))
        for k in range(1, 6):              # across the transom
            y = self.beam(0) - inset - 2 * (self.beam(0) - inset) * k / 6
            pts.append((self.L0 + 0.005, y, self.sheer(0) + dz))
        return pts

    def floor(self, name, zf, x0, x1, m, thick=0.04, gap=0.02, n=14):
        top, bot = [], []
        for i in range(n):
            x = x0 + (x1 - x0) * i / (n - 1)
            w = max(0.02, self.half_width(x, zf) - gap)
            top.append((x, w))
            bot.append((x, -w))
        outline = bot + top[::-1]
        o = slab(name, outline, thick, m, lvl=1, dens=1, plane="XY")
        o.location.z = zf
        return o

    def thwart(self, name, x, z, depth, m, thick=0.05, gap=0.02):
        w = 2 * (self.half_width(x, z) - gap)
        return rbox(name, (depth, w, thick), (x, 0, z), m, bevel=0.02, seg=2)


def hull_beam(Bmax, tm, stern=0.8, bow_exp=2.0, bow_pow=0.6):
    def f(t):
        if t <= tm:
            u = (tm - t) / tm
            return Bmax * (1 - (1 - stern) * u * u)
        u = (t - tm) / (1 - tm)
        return Bmax * max(0.0, 1 - u ** bow_exp) ** bow_pow
    return f


def fisherman(x0, zs, coat_hex, hat_hex="#f6c431", beard_hex="#d0662c", pants_hex="#5b6a3e", rod_angle=50):
    """Rounded friendly fisherman seated with seat top at (x0, zs), facing +X. Returns (parts, grip, rod_dir)."""
    coat = mat("Coat", coat_hex, 0.6)
    hat = mat("Hat", hat_hex, 0.42)
    skin = mat("Skin", "#f5c4a1", 0.55)
    nose = mat("Nose", "#f0a088", 0.5)
    cheek = mat("Cheek", "#f28d84", 0.6)
    beard = mat("Beard", beard_hex, 0.85)
    pants = mat("Pants", pants_hex, 0.75)
    boot = mat("Boot", "#3b2c22", 0.5)
    black = mat("Black", "#15161a", 0.25, spec=0.8)
    white = mat("White", "#ffffff", 0.3)
    button = mat("Button", "#5a3b22", 0.4)
    band = mat("HatBand", "#2c3440", 0.5)
    P = []
    # legs
    P.append(sphere("Pelvis", (x0 + 0.02, 0, zs + 0.13), (0.23, 0.26, 0.15), pants, 18, 10))
    for s in (1, -1):
        hip = Vector((x0 + 0.02, s * 0.13, zs + 0.13))
        knee = Vector((x0 + 0.42, s * 0.15, zs + 0.15))
        ank = Vector((x0 + 0.5, s * 0.15, zs - 0.24))
        P.append(tube(f"Thigh{s}", [hip, hip.lerp(knee, 0.5), knee], [0.11, 0.105, 0.1], pants, bres=2, ures=3))
        P.append(sphere(f"Knee{s}", knee, 0.1, pants, 14, 8))
        P.append(tube(f"Shin{s}", [knee, knee.lerp(ank, 0.5), ank], [0.092, 0.087, 0.082], pants, bres=2, ures=3))
        P.append(sphere(f"Boot{s}", (x0 + 0.57, s * 0.15, zs - 0.28), (0.17, 0.1, 0.09), boot, 16, 10))
        P.append(sphere(f"BootTop{s}", (x0 + 0.5, s * 0.15, zs - 0.21), (0.1, 0.1, 0.07), boot, 14, 8))
    # torso (an egg in the coat), collar, buttons
    torso = lathe("Torso", [(0, 0.68), (0.12, 0.67), (0.21, 0.62), (0.27, 0.52), (0.3, 0.38), (0.3, 0.24), (0.28, 0.12),
                            (0.24, 0.04), (0.15, 0.0), (0, -0.01)], coat, seg=22)
    torso.scale = (0.9, 1.05, 1.0)
    torso.rotation_euler = (0, math.radians(8), 0)
    torso.location = (x0, 0, zs + 0.06)
    P.append(torso)
    P.append(torus("Collar", (x0 + 0.06, 0, zs + 0.7), 0.13, 0.05, coat, seg=20, mseg=8, rot=(0, math.radians(14), 0)))
    for k, z in enumerate((0.52, 0.38, 0.24)):
        P.append(sphere(f"Btn{k}", (x0 + 0.27 + 0.03 * (0.52 - z) / 0.28 + 0.035 * math.sin(math.radians(8)) * 0 + (z - 0.24) * 0.14, 0, zs + 0.06 + z),
                        (0.03, 0.03, 0.03), button, 10, 6))
    # head
    hc = Vector((x0 + 0.09, 0, zs + 0.97))
    P.append(sphere("Head", hc, 0.24, skin, 24, 16))
    for s in (1, -1):
        P.append(sphere(f"Ear{s}", hc + Vector((-0.02, s * 0.235, -0.01)), (0.05, 0.035, 0.065), skin, 12, 8))
        P.append(sphere(f"EyeB{s}", hc + Vector((0.205, s * 0.09, 0.05)), (0.03, 0.03, 0.042), black, 12, 8))
        P.append(sphere(f"EyeH{s}", hc + Vector((0.232, s * 0.083, 0.064)), 0.01, white, 6, 4))
        P.append(tube(f"Brow{s}", [hc + Vector((0.2, s * 0.045, 0.13)), hc + Vector((0.2, s * 0.095, 0.145)), hc + Vector((0.185, s * 0.14, 0.13))],
                      0.022, beard, bres=1, ures=3))
        P.append(sphere(f"Cheek{s}", hc + Vector((0.175, s * 0.145, -0.035)), (0.035, 0.06, 0.04), cheek, 12, 6, look=(0.7, s * 0.7, 0)))
        P.append(sphere(f"Stache{s}", hc + Vector((0.245, s * 0.065, -0.085)), (0.05, 0.085, 0.045), beard, 14, 8, look=(0.2, s * 0.3, 1)))
    P.append(sphere("Nose", hc + Vector((0.255, 0, -0.025)), (0.085, 0.08, 0.08), nose, 16, 10))
    P.append(sphere("BeardM", hc + Vector((0.12, 0, -0.14)), (0.17, 0.22, 0.2), beard, 20, 12))
    # hat: a rounded fisherman's bucket hat
    hat_o = lathe("HatO", [(0, 0.29), (0.1, 0.285), (0.18, 0.26), (0.235, 0.2), (0.255, 0.12), (0.27, 0.07), (0.33, 0.035),
                           (0.375, 0.0), (0.38, -0.02), (0.36, -0.025), (0.3, 0.01), (0.25, 0.04), (0.235, 0.04)], hat, seg=28)
    hat_o.rotation_euler = (0, math.radians(-10), 0)
    hat_o.location = hc + Vector((-0.02, 0, 0.11))
    P.append(hat_o)
    hb = torus("HatBand", hc + Vector((-0.02, 0, 0.21)), 0.262, 0.028, band, seg=28, mseg=6, rot=(0, math.radians(-10), 0))
    P.append(hb)
    # arms reaching to the rod grip
    a = math.radians(rod_angle)
    d = Vector((math.sin(a), 0, math.cos(a)))
    grip = Vector((x0 + 0.5, 0, zs + 0.46))
    for s, along in ((1, 0.11), (-1, -0.07)):
        sh = Vector((x0 + 0.03, s * 0.27, zs + 0.56))
        el = Vector((x0 + 0.22, s * 0.31, zs + 0.33))
        hand = grip + d * along + Vector((0, s * 0.045, 0))
        P.append(sphere(f"Shoulder{s}", sh, 0.095, coat, 14, 8))
        P.append(tube(f"Arm{s}", [sh, el, hand + (el - hand).normalized() * 0.06], [0.09, 0.085, 0.08], coat, bres=2, ures=4))
        P.append(torus(f"Cuff{s}", hand + (el - hand).normalized() * 0.07, 0.07, 0.025, coat, seg=14, mseg=6))
        P.append(sphere(f"Hand{s}", hand, (0.075, 0.07, 0.075), skin, 14, 8))
    FISHERMAN_PARTS.update(o.name.split(".")[0] for o in P)
    return P, grip, rod_angle


FISHERMAN_PARTS = set()


def make_rod(grip, angle, L, color="#2f4f7a"):
    rodm = mat("Rod", color, 0.35)
    cork = mat("Cork", "#c9a06e", 0.8)
    metal = mat("Metal", "#c3cbd3", 0.3, 0.85)
    dark = mat("RodGrip", "#23262b", 0.5)
    P = []
    P.append(lathe("Handle", [(0, 0.24), (0.03, 0.24), (0.036, 0.2), (0.036, -0.18), (0.042, -0.22), (0.042, -0.25), (0, -0.26)],
                   cork, seg=12, mats=[cork, dark], mat_fn=lambda i, j: 1 if i >= 4 else 0))
    P.append(tube("Blank", [(0, 0, 0.2), (0, 0, L * 0.5), (0, 0, L)], [0.024, 0.016, 0.008], rodm, bres=1, ures=3))
    reel = lathe("Reel", [(0, 0.03), (0.05, 0.03), (0.062, 0.015), (0.062, -0.015), (0.05, -0.03), (0, -0.03)], metal, seg=16)
    reel.rotation_euler = (math.radians(90), 0, 0)
    reel.location = (0.085, 0, 0.05)
    P.append(reel)
    P.append(tube("ReelFoot", [(0.03, 0, 0.05), (0.06, 0, 0.05)], 0.012, metal, bres=1, ures=2))
    P.append(sphere("ReelKnob", (0.085, 0.05, 0.05), 0.018, dark, 8, 5))
    for k, z in enumerate((0.7, 1.2, 1.65, 2.0)):
        if z < L - 0.1:
            r = 0.022 - 0.004 * k
            P.append(torus(f"Guide{k}", (0.026 + r, 0, z * L / 2.4), r, 0.005, metal, seg=10, mseg=4, rot=(math.radians(90), 0, 0)))
    P.append(sphere("TipTop", (0, 0, L), 0.012, metal, 8, 5))
    rod = join(P, "Rod")
    rod.rotation_mode = "XYZ"
    rod.rotation_euler = (0, math.radians(angle), 0)
    rod.location = grip
    tip = bpy.data.objects.new("RodTip", None)
    link(tip)
    tip.empty_display_type = "PLAIN_AXES"
    tip.parent = rod
    tip.location = (0, 0, L)
    return rod


def finish_boat(name, parts):
    if os.environ.get("DEBUG_TRIS"):
        rows = sorted(((tri_count([o]), o.name) for o in bpy.context.scene.objects if o.type == "MESH"), reverse=True)
        print("TOP", rows[:14], "fisherman-ish total", sum(n for n, nm in rows if nm.split(".")[0] in FISHERMAN_PARTS))
    boat = join(parts, "Boat")
    apply_xform(boat, loc=True)
    for o in bpy.context.scene.objects:
        set_smooth(o)
    export(name)


def boat_row():
    reset()
    wood = mat("Wood", "#c98f57", 0.65)
    wood_l = mat("WoodLight", "#ddb07a", 0.65)
    wood_d = mat("WoodDark", "#8a5a36", 0.7)
    stripe = mat("Stripe", "#d9483d", 0.45)
    H = Hull(-1.5, 1.62, hull_beam(0.78, 0.42, stern=0.8, bow_exp=2.0, bow_pow=0.62),
             lambda t: 0.42 + 0.05 * (1 - t) ** 4 + 0.24 * t ** 3,
             lambda t: (-0.2 + 0.08 * max(0, 1 - t / 0.3) ** 2) if t < 0.7 else -0.2 + (0.66 - 0.07 + 0.2) * ((t - 0.7) / 0.3) ** 2.2,
             lambda t: 2.5 - 0.5 * t, n_st=24, n_sec=14)
    hull = H.build("Hull", [stripe, wood, wood_l], lambda dk: 0 if dk == 0 else (1 if dk % 2 else 2), wood_d, wood_d, thick=0.06, transom=1)
    P = [hull]
    P.append(tube("Rail", H.gunwale_loop(inset=0.035, dz=0.01), 0.048, wood_d, bres=2, ures=2, cyclic=True))
    P.append(H.floor("Floor", -0.07, -1.35, 1.2, wood_l, thick=0.035))
    P.append(H.thwart("Thwart", -0.35, 0.36, 0.3, wood_d))
    P.append(H.thwart("Thwart2", 0.78, 0.36, 0.24, wood_d))
    P.append(H.thwart("Stern", -1.28, 0.3, 0.3, wood_d))
    # oars resting inside along the sides
    blade_m = wood_l
    for s in (1, -1):
        a = Vector((-1.15, s * 0.5, 0.43))
        b = Vector((0.55, s * 0.47, 0.43))
        P.append(tube(f"Oar{s}", [a, a.lerp(b, 0.5), b], 0.03, wood_l, bres=1, ures=2))
        P.append(sphere(f"OarGrip{s}", a, (0.06, 0.035, 0.035), wood_d, 10, 6))
        bl = slab(f"Blade{s}", [(0.0, 0.035), (0.15, 0.075), (0.42, 0.08), (0.48, 0.0), (0.42, -0.08), (0.15, -0.075), (0.0, -0.035)],
                  0.03, blade_m, lvl=1, dens=1, plane="XY")
        bl.location = b
        bl.rotation_euler = (math.radians(s * 20), 0, math.radians(-s * 2))
        P.append(bl)
        P.append(torus(f"Oarlock{s}", (-0.1, s * (H.half_width(-0.1, 0.45) - 0.01), 0.5), 0.045, 0.012,
                       mat("Metal", "#c3cbd3", 0.3, 0.85), seg=12, mseg=5, rot=(math.radians(90), 0, 0)))
    # tackle box + bait bucket
    P.append(rbox("Tackle", (0.32, 0.22, 0.18), (-1.05, -0.28, 0.06), mat("Tackle", "#3f8f5a", 0.5), bevel=0.04))
    P.append(rbox("TackleLid", (0.34, 0.24, 0.05), (-1.05, -0.28, 0.165), mat("TackleLid", "#2f6e45", 0.5), bevel=0.02))
    P.append(lathe("Bucket", [(0, 0.2), (0.13, 0.2), (0.14, 0.19), (0.12, 0.02), (0.1, 0.0), (0, 0.0)], mat("Bucket", "#3f86c8", 0.45), seg=18,
                   loc=(0.35, 0.3, -0.06)))
    man, grip, ang = fisherman(-0.35, 0.385, "#2f6fc0", "#f6c431", "#d0662c")
    P += man
    make_rod(grip, ang, 2.4)
    finish_boat("boat_row", P)


def boat_speed():
    reset()
    white = mat("HullWhite", "#f5f7f9", 0.25)
    red = mat("Stripe", "#e3343c", 0.3)
    bottom = mat("HullBottom", "#c92a33", 0.4)
    inner = mat("Interior", "#ece6da", 0.5)
    navy = mat("Deck", "#1f3354", 0.5)
    seat = mat("Seat", "#f2e8d5", 0.6)
    glass = mat("Glass", "#a8dcff", 0.05, alpha=0.45)
    metal = mat("Metal", "#d3dae1", 0.2, 0.9)
    motor = mat("Motor", "#1a1d22", 0.35, 0.2)
    H = Hull(-2.05, 2.2, hull_beam(0.84, 0.35, stern=0.92, bow_exp=1.6, bow_pow=0.75),
             lambda t: 0.4 + 0.13 * t ** 2,
             lambda t: -0.14 if t < 0.6 else -0.14 + (0.53 - 0.05 + 0.14) * ((t - 0.6) / 0.4) ** 1.8,
             lambda t: 1.45 - 0.2 * t, n_st=26, n_sec=14)
    hull = H.build("Hull", [white, red, bottom], lambda dk: 1 if dk in (1,) else (2 if dk >= 4 else 0), inner, white, thick=0.06, transom=0)
    P = [hull]
    P.append(tube("Rail", H.gunwale_loop(inset=0.035, dz=0.012), 0.05, white, bres=2, ures=2, cyclic=True))
    P.append(H.floor("Deck", 0.1, -1.95, 1.75, navy, thick=0.04))
    # console + windshield + wheel
    P.append(rbox("Console", (0.55, 0.66, 0.6), (0.42, 0, 0.42), white, bevel=0.13, seg=4))
    P.append(rbox("Dash", (0.4, 0.6, 0.08), (0.36, 0, 0.73), navy, bevel=0.035, seg=3))
    rows = []
    for zz, dx in ((0.7, 0.0), (1.06, -0.16)):
        row = []
        for i in range(13):
            y = -0.52 + 1.04 * i / 12
            row.append((0.66 + dx - 0.3 * (y / 0.52) ** 2, y, zz))
        rows.append(row)
    ws = rings_mesh("Windshield", rows, [glass], cyclic=False, recalc=False)
    sol = ws.modifiers.new("s", "SOLIDIFY")
    sol.thickness = 0.015
    bake(ws)
    set_smooth(ws)
    P.append(ws)
    P.append(tube("WsFrame", [Vector(p) + Vector((0, 0, 0.005)) for p in rows[1]], 0.02, metal, bres=1, ures=2))
    for i in (0, 12):
        P.append(tube(f"WsPost{i}", [rows[0][i], rows[1][i]], 0.02, metal, bres=1, ures=2))
    P.append(torus("Wheel", (0.2, 0.12, 0.87), 0.11, 0.018, metal, seg=20, mseg=6, rot=(0, math.radians(-60), 0)))
    P.append(tube("WheelPost", [(0.27, 0.12, 0.76), (0.22, 0.12, 0.84)], 0.02, metal, bres=1, ures=2))
    # seats
    P.append(rbox("SeatCushion", (0.5, 0.56, 0.15), (-0.45, 0, 0.47), seat, bevel=0.06, seg=3))
    P.append(rbox("SeatBase", (0.42, 0.46, 0.34), (-0.45, 0, 0.25), white, bevel=0.06, seg=3))
    P.append(rbox("Bench", (0.45, 1.3, 0.16), (-1.6, 0, 0.46), seat, bevel=0.07, seg=3))
    P.append(rbox("BenchBack", (0.14, 1.3, 0.34), (-1.85, 0, 0.66), seat, bevel=0.06, seg=3, rot=(0, math.radians(-12), 0)))
    # bow rails
    for s in (1, -1):
        pts = []
        for i in range(6):
            x = 0.9 + 1.15 * i / 5
            pts.append((x, s * max(0.05, H.half_width(x, 0.5) - 0.08), H.sheer(H.t(x)) + 0.16))
        pts.append((2.12, 0, H.sheer(H.t(2.1)) + 0.14))
        P.append(tube(f"BowRail{s}", pts, 0.022, metal, bres=1, ures=3))
        for i in (0, 3):
            x, y, z = pts[i]
            P.append(tube(f"Stanchion{s}{i}", [(x, y, z), (x, y * 1.02, z - 0.17)], 0.018, metal, bres=1, ures=2))
    # outboard motor (rounded)
    P.append(rbox("Bracket", (0.12, 0.3, 0.24), (-2.12, 0, 0.42), motor, bevel=0.04))
    cowl = lathe("Cowl", [(0, 0.3), (0.12, 0.29), (0.2, 0.24), (0.23, 0.12), (0.22, -0.02), (0.17, -0.12), (0.1, -0.16), (0, -0.165)],
                 motor, seg=20, mats=[motor, red], mat_fn=lambda i, j: 1 if i == 3 else 0)
    cowl.scale = (1.15, 0.9, 1.0)
    cowl.location = (-2.32, 0, 0.72)
    P.append(cowl)
    P.append(sphere("CowlCap", (-2.3, 0, 0.99), (0.16, 0.13, 0.05), white, 14, 8))
    P.append(tube("Leg", [(-2.3, 0, 0.58), (-2.31, 0, 0.2), (-2.33, 0, -0.18)], [0.07, 0.06, 0.055], motor, bres=2, ures=3))
    P.append(rbox("Plate", (0.34, 0.2, 0.025), (-2.3, 0, 0.06), motor, bevel=0.01, seg=2))
    P.append(sphere("Hub", (-2.33, 0, -0.18), (0.12, 0.06, 0.06), motor, 14, 8))
    for k in range(3):
        bl = slab(f"Prop{k}", [(0.0, -0.03), (0.0, 0.03), (0.06, 0.07), (0.13, 0.05), (0.15, 0.0), (0.1, -0.04)], 0.012, metal, lvl=1, dens=1, plane="XY")
        bl.rotation_euler = (math.radians(120 * k), math.radians(90), 0)
        bl.location = (-2.44, 0, -0.18)
        bl.rotation_mode = "XYZ"
        P.append(bl)
    # flag
    P.append(tube("FlagPole", [(-1.95, 0.55, 0.45), (-1.95, 0.55, 1.35)], 0.014, metal, bres=1, ures=2))
    fl = slab("Flag", [(0, 0), (-0.32, -0.08), (0, -0.2)], 0.012, red, lvl=1, dens=2)
    fl.location = (-1.95, 0.55, 1.34)
    P.append(fl)
    man, grip, ang = fisherman(-0.45, 0.555, "#e3343c", "#ffffff", "#5a3a22")
    P += man
    make_rod(grip, ang, 2.5, "#1f2a38")
    finish_boat("boat_speed", P)


def boat_trawler():
    reset()
    blue = mat("HullBlue", "#2f66a8", 0.42)
    trim = mat("HullTrim", "#f4f1e8", 0.45)
    bottom = mat("HullBottom", "#cf4a3c", 0.5)
    bulwark = mat("Bulwark", "#e7ebee", 0.55)
    deck_m = mat("Deck", "#c09162", 0.7)
    cabin_m = mat("Cabin", "#f5f1e6", 0.5)
    roof = mat("Roof", "#d7463b", 0.45)
    win = mat("Window", "#22384f", 0.08, spec=0.9)
    metal = mat("Metal", "#c3cbd3", 0.3, 0.85)
    orange = mat("Float", "#ff8a2a", 0.4)
    crate = mat("Crate", "#b9824e", 0.7)
    crate_d = mat("CrateDark", "#8c5d34", 0.7)
    lamp = mat("Lamp", "#ffe58a", 0.2, emit="#ffd23f", emit_str=1.0)
    net = mat("Net", "#4f9a7a", 0.85)
    H = Hull(-2.45, 2.6, hull_beam(1.06, 0.45, stern=0.86, bow_exp=2.0, bow_pow=0.55),
             lambda t: 0.56 + 0.42 * t ** 2.5 + 0.04 * (1 - t) ** 3,
             lambda t: -0.25 + 0.06 * max(0, 1 - t / 0.25) ** 2 if t < 0.72 else -0.25 + (0.98 - 0.08 + 0.25) * ((t - 0.72) / 0.28) ** 2.0,
             lambda t: 2.9 - 0.8 * t, n_st=26, n_sec=14)
    hull = H.build("Hull", [trim, blue, bottom], lambda dk: 0 if dk == 0 else (2 if dk >= 5 else 1), bulwark, trim, thick=0.07, transom=1)
    P = [hull]
    P.append(tube("Rail", H.gunwale_loop(inset=0.04, dz=0.015), 0.06, trim, bres=2, ures=2, cyclic=True))
    P.append(H.floor("Deck", 0.3, -2.35, 2.1, deck_m, thick=0.05))
    # cabin
    P.append(rbox("Cabin", (1.15, 1.3, 0.92), (0.9, 0, 0.76), cabin_m, bevel=0.15, seg=4))
    P.append(rbox("CabinRoof", (1.38, 1.5, 0.12), (0.88, 0, 1.27), roof, bevel=0.06, seg=3))
    for s in (1, -1):
        P.append(rbox(f"WinF{s}", (0.04, 0.42, 0.3), (1.47, s * 0.27, 0.98), win, bevel=0.06, seg=3))
        for k, x in enumerate((0.62, 1.12)):
            P.append(torus(f"Port{s}{k}", (x, s * 0.655, 0.9), 0.115, 0.03, metal, seg=20, mseg=6, rot=(math.radians(90), 0, 0)))
            P.append(sphere(f"PortGlass{s}{k}", (x, s * 0.645, 0.9), (0.11, 0.025, 0.11), win, 16, 8))
    P.append(rbox("Door", (0.04, 0.42, 0.62), (0.31, 0, 0.66), mat("DoorBlue", "#2f66a8", 0.45), bevel=0.06))
    P.append(sphere("Knob", (0.28, 0.13, 0.66), 0.03, metal, 8, 5))
    # life ring on the cabin side (alternating orange / white)
    lr = torus("LifeRing", (0.87, -0.7, 0.9), 0.19, 0.065, orange, seg=32, mseg=10, rot=(math.radians(90), 0, 0),
               mats=[orange, trim], mat_fn=lambda i: (i // 4) % 2)
    P.append(lr)
    # mast, crossbar, lamp, boom
    mx, my = 1.15, 0.5
    P.append(tube("Mast", [(mx, my, 1.3), (mx, my, 2.2), (mx, my, 2.95)], [0.055, 0.048, 0.04], metal, bres=2, ures=2))
    P.append(tube("Yard", [(mx - 0.3, my, 2.45), (mx, my, 2.47), (mx + 0.3, my, 2.45)], 0.028, metal, bres=1, ures=2))
    P.append(sphere("MastLamp", (mx, my, 3.0), 0.07, lamp, 12, 8))
    P.append(slab("Pennant", [(0, 0), (-0.34, -0.07), (0, -0.16)], 0.012, roof, lvl=1, dens=2))
    bpy.context.view_layer.objects.active = None
    P[-1].location = (mx, my, 2.9)
    P.append(sphere("Radar", (0.5, -0.35, 1.38), (0.16, 0.16, 0.1), cabin_m, 16, 8))
    P.append(tube("Horn", [(0.45, 0.4, 1.33), (0.45, 0.4, 1.45), (0.6, 0.4, 1.5)], [0.03, 0.03, 0.06], metal, bres=1, ures=2))
    # crates (one is the fisherman's seat), fenders, net pile
    for k, (x, y, sx, sy, sz) in enumerate(((-0.78, 0.0, 0.5, 0.62, 0.4), (-1.65, -0.48, 0.44, 0.44, 0.36), (-1.62, 0.42, 0.4, 0.4, 0.3))):
        z = 0.33 + sz / 2
        P.append(rbox(f"Crate{k}", (sx, sy, sz), (x, y, z), crate, bevel=0.045, seg=3))
        P.append(rbox(f"CrateBand{k}", (sx + 0.02, sy + 0.02, 0.06), (x, y, z + sz * 0.18), crate_d, bevel=0.025, seg=2))
    for s in (1, -1):
        for k, x in enumerate((-1.3, -0.2)):
            P.append(sphere(f"Fender{s}{k}", (x, s * (H.half_width(x, 0.3) + 0.08), 0.32), (0.11, 0.11, 0.19), orange, 14, 8))
            P.append(tube(f"FenderRope{s}{k}", [(x, s * (H.half_width(x, 0.6) - 0.02), H.sheer(H.t(x)) + 0.03),
                                                (x, s * (H.half_width(x, 0.3) + 0.08), 0.5)], 0.012, mat("Rope", "#e8dcc0", 0.8), bres=1, ures=2))
    for k, (x, y, r) in enumerate(((-2.0, 0.15, 0.28), (-2.15, -0.25, 0.22), (-1.95, -0.05, 0.2))):
        P.append(sphere(f"Net{k}", (x, y, 0.35 + r * 0.45), (r, r * 1.1, r * 0.6), net, 14, 8))
    for k, x in enumerate((-2.05, -1.85)):
        P.append(sphere(f"NetFloat{k}", (x, 0.35 - k * 0.5, 0.5), 0.07, orange, 10, 6))
    man, grip, ang = fisherman(-0.8, 0.745, "#2f8a4c", "#f6c431", "#9a9a9a")
    P += man
    make_rod(grip, ang, 2.6, "#7a2f2f")
    finish_boat("boat_trawler", P)


ALL = ["fish_" + f for f in FISH] + ["boat_row", "boat_speed", "boat_trawler"]

if __name__ == "__main__":
    names = [a for a in sys.argv[1:] if not a.endswith(".py")] or ALL
    for n in names:
        globals()[n]()
    print("TRIS", json.dumps(TRI_COUNTS))
