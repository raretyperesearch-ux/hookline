"""HookLine environment, v3 look: smooth, soft, stylised (islands, lighthouse, buoy, tentacle, dock).

Run: bvenv/bin/python blender/build_env_v2.py [out_dir] [only names...]
Default out_dir is site/models3 next to this folder. Writes <name>.glb and an embedded <name>.json.

Everything is generated straight into one bmesh per model, in world coordinates (Blender Z-up;
the glTF exporter turns it into three.js Y-up), so each model is a single node with identity
transform.  Terrain, foliage and rocks use vertex colours (COLOR_0) multiplied with a white
material, which gives soft painted gradients without textures.
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
from mathutils import Matrix, Vector, noise

HERE = os.path.dirname(os.path.abspath(__file__))
ARGS = [a for a in sys.argv[1:] if not a.endswith(".py")]
OUT = ARGS[0] if ARGS else os.path.join(HERE, "..", "site", "models3")
ONLY = set(ARGS[1:])
Z = Vector((0, 0, 1))
X = Vector((1, 0, 0))
TAU = math.tau


# ---------------------------------------------------------------- colour + maths helpers
def lin(hexstr):
    h = hexstr.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(((x + 0.055) / 1.055) ** 2.4 if x > 0.04045 else x / 12.92 for x in c)


WHITE = (1.0, 1.0, 1.0)


def mixc(a, b, t):
    t = max(0.0, min(1.0, t))
    return tuple(a[i] + (b[i] - a[i]) * t for i in range(3))


def mulc(a, k):
    return tuple(min(1.0, x * k) for x in a)


def sstep(e0, e1, x):
    t = max(0.0, min(1.0, (x - e0) / (e1 - e0)))
    return t * t * (3 - 2 * t)


def fbm(x, y, z=0.0, octaves=3):
    s, a, f, tot = 0.0, 1.0, 1.0, 0.0
    for _ in range(octaves):
        s += a * noise.noise(Vector((x * f, y * f, z * f)))
        tot += a
        a *= 0.5
        f *= 2.03
    return s / tot


def bez(p0, p1, p2, p3, t):
    u = 1 - t
    return p0 * (u * u * u) + p1 * (3 * u * u * t) + p2 * (3 * u * t * t) + p3 * (t * t * t)


def align_z(d):
    """Rotation matrix taking +Z onto direction d."""
    return Z.rotation_difference(Vector(d).normalized()).to_matrix()


# ---------------------------------------------------------------- materials
_mats = {}


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _mats.clear()


def mat(name, color="#ffffff", rough=0.7, metal=0.0, emit=None, es=2.5, alpha=None, vcol=False, double=False):
    if name in _mats:
        return _mats[name]
    m = bpy.data.materials.new(name)
    try:
        m.use_nodes = True
    except Exception:
        pass
    nt = m.node_tree
    b = nt.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*lin(color), 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if emit:
        b.inputs["Emission Color"].default_value = (*lin(emit), 1)
        b.inputs["Emission Strength"].default_value = es
    if alpha is not None:
        b.inputs["Alpha"].default_value = alpha
        try:
            m.surface_render_method = "BLENDED"
        except Exception:
            pass
        try:
            m.blend_method = "BLEND"
        except Exception:
            pass
    if vcol:
        vc = nt.nodes.new("ShaderNodeVertexColor")
        vc.layer_name = "Col"
        nt.links.new(vc.outputs["Color"], b.inputs["Base Color"])
    m.use_backface_culling = not double
    m.diffuse_color = (*lin(color), 1)
    _mats[name] = m
    return m


# ---------------------------------------------------------------- mesh builder
class Builder:
    def __init__(self, name):
        self.name = name
        self.bm = bmesh.new()
        self.col = self.bm.verts.layers.float_color.new("Col")
        self.uv = self.bm.loops.layers.uv.new("UVMap")
        self.mats = []

    def m(self, material):
        if material not in self.mats:
            self.mats.append(material)
        return self.mats.index(material)

    def v(self, co, c=WHITE):
        vv = self.bm.verts.new(co)
        vv[self.col] = (*c, 1.0)
        return vv

    def f(self, vs, mi, smooth=True):
        face = self.bm.faces.new(vs)
        face.material_index = mi
        face.smooth = smooth
        return face

    def finish(self):
        me = bpy.data.meshes.new(self.name)
        self.bm.to_mesh(me)
        self.bm.free()
        for m in self.mats:
            me.materials.append(m)
        try:
            me.color_attributes.active_color_index = 0
            me.color_attributes.render_color_index = 0
        except Exception:
            pass
        o = bpy.data.objects.new(self.name, me)
        bpy.context.collection.objects.link(o)
        return o


def ring_faces(b, rings, mi, mat_fn=None, closed=True):
    """Connect consecutive rings (lists of verts; a 1-vert list is a pole). Normals follow
    the right-hand rule: with rings ordered along +axis and verts CCW, faces point outward."""
    faces = []
    for i in range(len(rings) - 1):
        a, c = rings[i], rings[i + 1]
        n = max(len(a), len(c))
        for k in range(n if closed else n - 1):
            k1 = (k + 1) % n
            idx = mat_fn(i, k) if mat_fn else mi
            if len(a) == 1:
                fv = (a[0], c[k1], c[k])
            elif len(c) == 1:
                fv = (a[k], a[k1], c[0])
            else:
                fv = (a[k], a[k1], c[k1], c[k])
            faces.append(b.f(fv, idx))
    return faces


def lathe(b, prof, segs, mi, center=(0, 0, 0), M=None, mat_fn=None, rmod=None, col_fn=None, phase=0.0):
    """Surface of revolution. prof = [(r, z)] ordered bottom pole -> outside -> top pole."""
    c = Vector(center)
    rings = []
    for i, (r, z) in enumerate(prof):
        if r < 1e-6:
            p = Vector((0, 0, z))
            rings.append([b.v(c + (M @ p if M else p), col_fn(i, 0, p) if col_fn else WHITE)])
            continue
        ring = []
        for k in range(segs):
            a = TAU * k / segs + phase
            rr = r * (rmod(i, a) if rmod else 1.0)
            p = Vector((rr * math.cos(a), rr * math.sin(a), z))
            ring.append(b.v(c + (M @ p if M else p), col_fn(i, k, p) if col_fn else WHITE))
        rings.append(ring)
    return ring_faces(b, rings, mi, mat_fn)


def tube(b, pts, radii, segs, mi, cap0=True, cap1=True, mat_fn=None, col_fn=None, normals=None, rmod=None):
    """Tube through pts with per-point radii, rounded caps. Parallel-transport frames unless
    explicit normals are given. mat_fn(i, k) and col_fn(i, k, t) see ring index i (0..n-1)."""
    pts = [Vector(p) for p in pts]
    n = len(pts)
    T = [(pts[min(i + 1, n - 1)] - pts[max(i - 1, 0)]).normalized() for i in range(n)]
    frames = []
    if normals:
        for i in range(n):
            N = Vector(normals[i])
            N = (N - T[i] * N.dot(T[i])).normalized()
            frames.append((N, T[i].cross(N)))
    else:
        ref = Z if abs(T[0].z) < 0.9 else X
        N = (ref - T[0] * ref.dot(T[0])).normalized()
        for i in range(n):
            if i:
                N = T[i - 1].rotation_difference(T[i]) @ N
                N = (N - T[i] * N.dot(T[i])).normalized()
            frames.append((N, T[i].cross(N)))

    def ring(center, N, B, r, i):
        out = []
        for k in range(segs):
            a = TAU * k / segs
            rr = r * (rmod(i, a) if rmod else 1.0)
            out.append(b.v(center + (N * math.cos(a) + B * math.sin(a)) * rr, col_fn(i, k, i / max(1, n - 1)) if col_fn else WHITE))
        return out

    rings, ids = [], []
    if cap0:
        r = radii[0]
        rings.append([b.v(pts[0] - T[0] * r * 0.85, col_fn(0, 0, 0.0) if col_fn else WHITE)])
        ids.append(0)
        rings.append(ring(pts[0] - T[0] * r * 0.5, *frames[0], r * 0.78, 0))
        ids.append(0)
    for i in range(n):
        rings.append(ring(pts[i], *frames[i], radii[i], i))
        ids.append(i)
    if cap1:
        r = radii[-1]
        rings.append(ring(pts[-1] + T[-1] * r * 0.5, *frames[-1], r * 0.78, n - 1))
        ids.append(n - 1)
        rings.append([b.v(pts[-1] + T[-1] * r * 0.85, col_fn(n - 1, 0, 1.0) if col_fn else WHITE)])
        ids.append(n - 1)
    fn = (lambda i, k: mat_fn(ids[i], k)) if mat_fn else None
    return ring_faces(b, rings, mi, fn)


def torus(b, center, R, r, mi, M=None, seg=32, mseg=8, col=WHITE):
    c = Vector(center)
    rings = []
    for i in range(seg):
        a = TAU * i / seg
        d = Vector((math.cos(a), math.sin(a), 0))
        ring = []
        for k in range(mseg):
            t = TAU * k / mseg
            p = d * (R + r * math.cos(t)) + Z * (r * math.sin(t))
            ring.append(b.v(c + (M @ p if M else p), col))
        rings.append(ring)
    rings.append(rings[0])
    # walking around +Z with verts ordered outward->up gives inward normals; flip order
    return ring_faces(b, [list(reversed(r_)) for r_ in rings], mi)


def add_bm(b, tb, mi, xf=None, col_fn=None, smooth=True):
    vm = {}
    for v in tb.verts:
        co = xf(v.co.copy()) if xf else v.co.copy()
        vm[v] = b.v(co, col_fn(co, v) if col_fn else WHITE)
    faces = [b.f([vm[v] for v in f.verts], mi, smooth) for f in tb.faces]
    tb.free()
    return faces


def blob(b, center, size, mi, seed=0, subdiv=2, amp=0.18, freq=1.3, col_fn=None, flat_bottom=None, M=None):
    """Soft noisy pebble / foliage clump."""
    tb = bmesh.new()
    bmesh.ops.create_icosphere(tb, subdivisions=subdiv, radius=1.0)
    c = Vector(center)
    off = Vector((seed * 3.17, seed * 1.31, seed * 2.03))
    for v in tb.verts:
        d = v.co.normalized()
        k = 1 + amp * noise.noise(d * freq + off) + amp * 0.35 * noise.noise(d * freq * 2.7 + off)
        p = Vector((d.x * size[0], d.y * size[1], d.z * size[2])) * k
        if flat_bottom is not None and p.z < -flat_bottom * size[2]:
            p.z = -flat_bottom * size[2] + (p.z + flat_bottom * size[2]) * 0.25
        v.co = p
    return add_bm(b, tb, mi, lambda co: c + (M @ co if M else co), col_fn)


def box(b, center, size, mi, bevel=0.05, segs=2, M=None, col=WHITE, smooth=True):
    tb = bmesh.new()
    bmesh.ops.create_cube(tb, size=1.0)
    for v in tb.verts:
        v.co = Vector((v.co.x * size[0], v.co.y * size[1], v.co.z * size[2]))
    if bevel:
        bmesh.ops.bevel(tb, geom=list(tb.edges), offset=bevel, offset_type="OFFSET", segments=segs,
                        profile=0.5, affect="EDGES", clamp_overlap=True)
    c = Vector(center)
    return add_bm(b, tb, mi, lambda co: c + (M @ co if M else co), (lambda co, v: col), smooth)


def sphere(b, center, r, mi, seg=12, rings=8, col=WHITE, scale=(1, 1, 1)):
    prof = [(0, -1)] + [(math.sin(math.pi * i / rings), -math.cos(math.pi * i / rings)) for i in range(1, rings)] + [(0, 1)]
    prof = [(rr, z) for rr, z in prof]
    c = Vector(center)
    S = Matrix.Diagonal(Vector((scale[0] * r, scale[1] * r, scale[2] * r)))
    return lathe(b, prof, seg, mi, c, M=S, col_fn=lambda i, k, p: col)


# ---------------------------------------------------------------- export + glTF json
def glb_to_json(glb_path):
    data = open(glb_path, "rb").read()
    magic, ver, total = struct.unpack_from("<III", data, 0)
    off = 12
    js, binc = None, b""
    while off < total:
        ln, typ = struct.unpack_from("<II", data, off)
        chunk = data[off + 8: off + 8 + ln]
        if typ == 0x4E4F534A:
            js = json.loads(chunk.decode("utf-8"))
        else:
            binc = chunk
        off += 8 + ln
    js["buffers"][0]["uri"] = "data:application/octet-stream;base64," + base64.b64encode(binc).decode("ascii")
    out = glb_path[:-4] + ".json"
    with open(out, "w") as fh:
        json.dump(js, fh, separators=(",", ":"))
    return out


def export(name, objs):
    os.makedirs(OUT, exist_ok=True)
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    path = os.path.join(OUT, name + ".glb")
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_apply=True,
                              export_yup=True, export_extras=False, export_cameras=False, export_lights=False,
                              export_vertex_color="MATERIAL")
    glb_to_json(path)
    tris = 0
    for o in objs:
        o.data.calc_loop_triangles()
        tris += len(o.data.loop_triangles)
    print(f"EXPORTED {name}: {tris} tris -> {path}")
    return tris


# ================================================================ islands
class Terrain:
    """Height field: beach ring sloping into water, hills inland, noise, optional flat pads."""

    def __init__(self, R, Hb=0.45, bw=0.3, hills=(), seed=0, wob=0.07, namp=0.12, nfreq=0.35, so=0.28, pads=()):
        self.R, self.Hb, self.bw, self.hills, self.seed = R, Hb, bw, hills, seed
        self.wob, self.namp, self.nfreq, self.so, self.pads = wob, namp, nfreq, so, pads

    def shore(self, th):
        s = self.seed
        return self.R * (1 + self.wob * noise.noise(Vector((math.cos(th) * 1.2 + s, math.sin(th) * 1.2, s * 0.37)))
                         + 0.45 * self.wob * noise.noise(Vector((math.cos(th) * 3 + s, math.sin(th) * 3, 1.7))))

    def full(self, x, y):
        th = math.atan2(y, x)
        d = math.hypot(x, y)
        s = d / self.shore(th)
        hill = 0.0
        if s < 1:
            hh = self.Hb * math.sin(math.pi / 2 * min(1.0, (1 - s) / self.bw))
            mask = sstep(0.0, self.bw * 1.4, 1 - s)
            for (hx, hy, hr, hv) in self.hills:
                dd = math.hypot(x - hx, y - hy) / hr
                if dd < 1:
                    hill += hv * (0.5 + 0.5 * math.cos(math.pi * dd))
            nz = self.namp * fbm(x * self.nfreq + self.seed, y * self.nfreq, 0.5)
            hh += (hill + nz) * mask
        else:
            t = (s - 1) / self.so
            hh = -1.2 * math.sin(math.pi / 2 * min(t, 1.0)) if t < 1 else -1.2 - 0.25 * min(1.0, t - 1)
        for (px, py, pr, pz) in self.pads:
            w = sstep(pr * 1.9, pr, math.hypot(x - px, y - py))
            hh = hh * (1 - w) + pz * w
        return hh, hill, s

    def h(self, x, y):
        return self.full(x, y)[0]


def build_terrain(b, T, mi, color_fn, rings=46, segs=120):
    Rout = T.R * (1 + T.so) * 1.03
    cv = b.v((0, 0, T.h(0, 0)))
    grid = [[cv]]
    for j in range(1, rings + 1):
        u = j / rings
        r = Rout * u ** 0.8
        row = []
        for k in range(segs):
            a = TAU * k / segs + (0.5 * TAU / segs if j % 2 else 0)
            x, y = r * math.cos(a), r * math.sin(a)
            row.append(b.v((x, y, T.h(x, y))))
        grid.append(row)
    faces = []
    for j in range(rings):
        a, c = grid[j], grid[j + 1]
        if len(a) == 1:
            for k in range(segs):
                faces.append(b.f((a[0], c[k], c[(k + 1) % segs]), mi))
        else:
            for k in range(segs):
                k1 = (k + 1) % segs
                if (j % 2) == 0:   # rows j even have no offset, j+1 offset by half step
                    faces.append(b.f((a[k], c[k], a[k1]), mi))
                    faces.append(b.f((a[k1], c[k], c[k1]), mi))
                else:
                    faces.append(b.f((a[k], c[k1], a[k1]), mi))
                    faces.append(b.f((a[k], c[k], c[k1]), mi))
    b.bm.normal_update()
    for row in grid:
        for v in row:
            x, y, z = v.co
            hh, hill, s = T.full(x, y)
            v[b.col] = (*color_fn(x, y, hh, hill, s, v.normal.z), 1.0)
    return faces


def place_points(rnd, T, n, smin, smax, mind, avoid=(), tries=400):
    pts = []
    for _ in range(tries):
        if len(pts) >= n:
            break
        a = rnd.uniform(0, TAU)
        s = rnd.uniform(smin, smax)
        r = s * T.shore(a)
        x, y = r * math.cos(a), r * math.sin(a)
        if all(math.hypot(x - px, y - py) > mind for px, py in pts) and all(math.hypot(x - ax, y - ay) > ar for ax, ay, ar in avoid):
            pts.append((x, y))
    return pts


# ---------------------------------------------------------------- vegetation + props
def palm(b, x, y, T, rnd, height=4.0, lean=0.35, mats=None):
    trunk_mi, leaf_mi, nut_mi = mats
    gz = T.h(x, y) - 0.15
    ang = math.atan2(y, x) + rnd.uniform(-0.5, 0.5)       # lean away from the island centre
    ld = Vector((math.cos(ang), math.sin(ang), 0))
    side = Vector((-ld.y, ld.x, 0))
    sway = rnd.uniform(-0.25, 0.25)
    n = 40
    pts, radii = [], []
    for i in range(n):
        t = i / (n - 1)
        p = Vector((x, y, gz)) + ld * (lean * height * t ** 1.8) + side * (sway * height * t * t * (1 - t)) + Z * (height * t)
        pts.append(p)
        bump = 0.5 + 0.5 * math.cos(TAU * t * 11)
        radii.append((0.25 * (1 - 0.42 * t) + 0.06 * (1 - t) ** 6) * (1 + 0.07 * bump))
    c0, c1 = lin("#9a6b43"), lin("#c79a68")
    tube(b, pts, radii, 12, trunk_mi, cap0=False, cap1=True,
         col_fn=lambda i, k, t: mixc(c0, c1, 0.5 + 0.5 * math.cos(TAU * t * 11) * 0.9 + 0.1 * t))
    top = pts[-1]
    # crown bulb + coconuts
    blob(b, top + Z * 0.02, (0.26, 0.26, 0.2), leaf_mi, seed=rnd.random() * 10, subdiv=2, amp=0.1,
         col_fn=lambda co, v: lin("#4f8f3a"))
    for k in range(3):
        a = ang + math.pi + (k - 1) * 0.9 + rnd.uniform(-0.2, 0.2)
        sphere(b, top + Vector((math.cos(a) * 0.2, math.sin(a) * 0.2, -0.2 - 0.05 * k)), 0.15, nut_mi, 10, 7)
    # fronds
    nf = 9
    base_a = rnd.uniform(0, TAU)
    for k in range(nf):
        a = base_a + TAU * k / nf + rnd.uniform(-0.15, 0.15)
        L = rnd.uniform(2.0, 2.5) * height / 4.0
        frond(b, leaf_mi, top + Z * 0.08, a, L, 0.38 * L / 2.2, rise=rnd.uniform(0.35, 0.55), droop=rnd.uniform(1.0, 1.25))
    for k in range(2):
        a = base_a + k * math.pi + 0.7
        frond(b, leaf_mi, top + Z * 0.12, a, 1.3, 0.24, rise=1.4, droop=1.3)


def frond(b, mi, base, ang, L, W, rise=0.45, droop=1.1):
    d = Vector((math.cos(ang), math.sin(ang), 0))
    side = Vector((-d.y, d.x, 0))
    n = 22
    cb, ct = lin("#2f8a3c"), lin("#9bd65c")
    rows = []
    for i in range(n):
        t = i / n
        p = base + d * (L * t) + Z * (L * (rise * t - droop * t * t))
        w = W * max(0.04, math.sin(math.pi * min(1.0, 0.04 + t)) ** 0.55)
        ser = 0.5 if (i % 2 == 1 and 2 < i < n - 1) else 1.0
        we = w * ser
        c = mixc(cb, ct, t ** 0.8)
        offs = [(-we, -0.42 * we), (-0.5 * w, 0.0), (0, 0.16 * w), (0.5 * w, 0.0), (we, -0.42 * we)]
        rows.append([b.v(p + side * ox + Z * oz, c) for ox, oz in offs])
    tip = b.v(base + d * L + Z * (L * (rise - droop)), ct)
    for i in range(n - 1):
        r0, r1 = rows[i], rows[i + 1]
        for j in range(4):
            b.f((r0[j], r1[j], r1[j + 1], r0[j + 1]), mi)
    r0 = rows[-1]
    for j in range(4):
        b.f((r0[j], tip, r0[j + 1]), mi)


def bush(b, x, y, T, rnd, size, mi, flower_mi=None, c_lo="#3f8f3c", c_hi="#8fd060"):
    gz = T.h(x, y)
    lo, hi = lin(c_lo), lin(c_hi)
    for k in range(rnd.randint(2, 4)):
        a = rnd.uniform(0, TAU)
        o = Vector((math.cos(a), math.sin(a), 0)) * size * rnd.uniform(0.2, 0.65)
        s = size * rnd.uniform(0.6, 0.95)
        c = Vector((x, y, gz)) + o + Z * (s * 0.35)
        blob(b, c, (s, s, s * 0.75), mi, seed=rnd.random() * 50, subdiv=2, amp=0.16,
             col_fn=lambda co, v, c=c, s=s: mixc(lo, hi, 0.5 + (co.z - c.z) / (s * 1.4)))
        if flower_mi is not None:
            for _ in range(3):
                fa, fe = rnd.uniform(0, TAU), rnd.uniform(0.25, 1.1)
                p = c + Vector((math.cos(fa) * math.cos(fe), math.sin(fa) * math.cos(fe), math.sin(fe) * 0.75)) * s * 0.98
                sphere(b, p, 0.08 * size / 0.8, flower_mi, 8, 5)


def rock(b, x, y, z, size, rnd, mi, moss=False, c_lo="#7f868d", c_hi="#c3c7cb", subdiv=2):
    lo, hi = lin(c_lo), lin(c_hi)
    mc = lin("#79b552")
    c = Vector((x, y, z))
    rot = Matrix.Rotation(rnd.uniform(0, TAU), 3, "Z")

    def col(co, v):
        nz = (co - c).normalized().z
        base = mixc(lo, hi, 0.5 + 0.6 * nz)
        return mixc(base, mc, sstep(0.55, 0.8, nz)) if moss else base
    blob(b, c, size, mi, seed=rnd.random() * 99, subdiv=subdiv, amp=0.2, freq=1.1, col_fn=col, flat_bottom=0.55, M=rot)


def conifer(b, x, y, T, rnd, H, R, mats):
    trunk_mi, leaf_mi = mats
    gz = T.h(x, y) - 0.1
    lathe(b, [(0, gz - 0.2), (0.2, gz - 0.2), (0.17, gz + 0.3), (0.13, gz + 0.9), (0, gz + 0.95)], 10, trunk_mi,
          col_fn=lambda i, k, p: lin("#7a5233"))
    tiers = 4 if H > 4 else 3
    lo, hi = lin("#1f6e47"), lin("#5fbf6a")
    for k in range(tiers):
        f = k / tiers
        z0 = gz + 0.55 + H * 0.2 * k
        rt = R * (1 - 0.23 * k)
        ht = H * (0.46 - 0.05 * k) if k < tiers - 1 else H - (z0 - gz)
        prof = [(0, z0 + 0.06), (rt * 0.5, z0 + 0.02), (rt * 0.88, z0 + 0.03), (rt, z0 + 0.12), (rt * 0.96, z0 + 0.24)]
        steps = 5
        for i in range(1, steps + 1):
            u = i / (steps + 1)
            prof.append((rt * 0.9 * (1 - u) ** 1.35, z0 + 0.24 + u * (ht - 0.24)))
        prof.append((0, z0 + ht))
        ph = rnd.uniform(0, TAU)
        nrim = 5

        def rmod(i, a, ph=ph, n=len(prof)):
            w = 1.0 if i <= 4 else max(0.0, 1 - (i - 4) / 4)
            return 1 + 0.09 * w * math.sin(7 * a + ph)

        def col(i, kk, p, z0=z0, ht=ht, f=f):
            u = (p.z - (z0 - 0)) / ht if i < len(prof) else 1
            return mixc(lo, hi, 0.25 + 0.5 * u + 0.25 * f)
        lathe(b, [(r, z) for r, z in prof], 14, leaf_mi, center=(x, y, 0), rmod=rmod, col_fn=lambda i, kk, p, c=col: c(i, kk, p))


def log_cabin(b, cx, cy, gz, rot, mats):
    log_mi, roof_mi, door_mi, stone_mi, win_mi, trim_mi = mats
    M = Matrix.Rotation(rot, 3, "Z")
    C = Vector((cx, cy, gz))
    W, D = 2.5, 2.0
    r = 0.13
    lo, hi = lin("#8a5a35"), lin("#b47a4a")

    def P(x, y, z):
        return C + M @ Vector((x, y, z))
    box(b, P(0, 0, 0.85), (W - 0.1, D - 0.1, 1.7), log_mi, bevel=0.05, segs=1, M=M, col=mulc(lo, 0.7))
    for layer in range(7):
        z = r + layer * 2 * r * 0.92
        c = mixc(lo, hi, 0.4 + 0.3 * math.sin(layer * 2.1))
        if True:
            for sy in (-1, 1):
                pts = [P(-W / 2 - 0.22, sy * D / 2, z), P(W / 2 + 0.22, sy * D / 2, z)]
                tube(b, [pts[0].lerp(pts[1], t / 5) for t in range(6)], [r] * 6, 8, log_mi, col_fn=lambda i, k, t, c=c: c)
            if layer == 6:
                continue
            for sx in (-1, 1):
                pts = [P(sx * W / 2, -D / 2 - 0.22, z + r * 0.9), P(sx * W / 2, D / 2 + 0.22, z + r * 0.9)]
                tube(b, [pts[0].lerp(pts[1], t / 5) for t in range(6)], [r] * 6, 8, log_mi, col_fn=lambda i, k, t, c=c: c)
    top = r + 6 * 2 * r * 0.92 + r
    # gables (wedge) front/back
    for sy in (-1, 1):
        tb = bmesh.new()
        hw, gh = D / 2 + 0.05, 0.8
        vs = [tb.verts.new(Vector(p)) for p in ((-0.08, -hw, 0), (-0.08, hw, 0), (-0.08, 0, gh), (0.08, -hw, 0), (0.08, hw, 0), (0.08, 0, gh))]
        bmesh.ops.convex_hull(tb, input=vs)
        bmesh.ops.bevel(tb, geom=list(tb.edges), offset=0.03, offset_type="OFFSET", segments=2, profile=0.5, affect="EDGES", clamp_overlap=True)
        add_bm(b, tb, log_mi, lambda co, sy=sy: P(co.x + sy * W / 2, co.y, co.z + top - 0.12), lambda co, v: lin("#a06c40"))
    # roof slabs, rotated about the ridge (X axis)
    pitch = math.atan2(0.85, D / 2)
    slab_len = math.hypot(0.85, D / 2) + 0.45
    for sy in (-1, 1):
        R_ = M @ Matrix.Rotation(-sy * pitch, 3, "X")
        mid = Vector((0, sy * (slab_len / 2 - 0.12) * math.cos(pitch), top - 0.05 + 0.85 - (slab_len / 2 - 0.12) * math.sin(pitch) + 0.1))
        box(b, C + M @ mid, (W + 0.75, slab_len, 0.16), roof_mi, bevel=0.07, segs=3, M=R_, col=WHITE)
    tube(b, [P(-W / 2 - 0.4 + t * (W + 0.8) / 4, 0, top + 0.95) for t in range(5)], [0.1] * 5, 10, trim_mi)
    # door + window + chimney
    box(b, P(0.35, -D / 2 - 0.15, 0.62), (0.62, 0.1, 1.1), door_mi, bevel=0.05, segs=2, M=M)
    sphere(b, P(0.58, -D / 2 - 0.22, 0.6), 0.05, trim_mi, 8, 5)
    box(b, P(-0.65, -D / 2 - 0.16, 0.95), (0.62, 0.08, 0.52), trim_mi, bevel=0.04, segs=2, M=M)
    box(b, P(-0.65, -D / 2 - 0.19, 0.95), (0.46, 0.06, 0.38), win_mi, bevel=0.02, segs=2, M=M)
    box(b, P(-0.65, -D / 2 - 0.23, 0.95), (0.05, 0.03, 0.38), trim_mi, bevel=0.01, segs=1, M=M)
    box(b, P(-0.65, -D / 2 - 0.23, 0.95), (0.46, 0.03, 0.05), trim_mi, bevel=0.01, segs=1, M=M)
    box(b, P(W / 2 - 0.6, 0.45, top + 0.75), (0.42, 0.42, 1.25), stone_mi, bevel=0.1, segs=3, M=M)
    box(b, P(W / 2 - 0.6, 0.45, top + 1.42), (0.52, 0.52, 0.14), stone_mi, bevel=0.05, segs=2, M=M)


def mangrove(b, x, y, T, rnd, H, mats):
    wood_mi, leaf_mi, moss_mi = mats
    gz = T.h(x, y)
    wlo, whi = lin("#5b4632"), lin("#8a6e50")
    base = Vector((x, y, gz + 0.85))
    lean = Vector((rnd.uniform(-0.4, 0.4), rnd.uniform(-0.4, 0.4), 0))
    top = base + lean + Z * (H - 0.85)
    trunk = [bez(base - Z * 0.2, base + Z * (H * 0.3), top - Z * (H * 0.3) + lean * 0.3, top, i / 13) for i in range(14)]
    tube(b, trunk, [0.26 - 0.12 * i / 13 for i in range(14)], 10, wood_mi, cap0=False, col_fn=lambda i, k, t: mixc(wlo, whi, t))
    # arching prop roots
    nr = 6
    for k in range(nr):
        a = TAU * k / nr + rnd.uniform(-0.25, 0.25)
        d = Vector((math.cos(a), math.sin(a), 0))
        s = base + d * 0.12 + Z * rnd.uniform(0.0, 0.45)
        dist = rnd.uniform(0.9, 1.5)
        e = Vector((x, y, 0)) + d * dist
        e.z = T.h(e.x, e.y) - 0.25
        p1 = s + d * 0.45 + Z * 0.35
        p2 = e + Z * 0.9 - d * 0.1
        tube(b, [bez(s, p1, p2, e, i / 9) for i in range(10)], [0.09 - 0.035 * i / 9 for i in range(10)], 7, wood_mi,
             col_fn=lambda i, k, t: mixc(whi, wlo, t))
    # branches + canopy clumps
    llo, lhi = lin("#2f5a2a"), lin("#7da447")
    mlo, mhi = lin("#8a9a64"), lin("#55703a")
    clumps = [top + Z * 0.25]
    for k in range(3):
        a = rnd.uniform(0, TAU) if k == 0 else a + TAU / 3 + rnd.uniform(-0.3, 0.3)
        d = Vector((math.cos(a), math.sin(a), 0))
        s = trunk[9 + k]
        e = s + d * rnd.uniform(1.0, 1.5) + Z * rnd.uniform(0.4, 0.9)
        tube(b, [s.lerp(e, i / 5) + Z * (0.15 * math.sin(math.pi * i / 5)) for i in range(6)], [0.1 - 0.05 * i / 5 for i in range(6)], 7, wood_mi,
             col_fn=lambda i, k_, t: whi)
        clumps.append(e + Z * 0.15)
    for c in clumps:
        s = rnd.uniform(0.95, 1.2)
        blob(b, c, (s * 1.1, s * 1.1, s * 0.8), leaf_mi, seed=rnd.random() * 77, subdiv=3, amp=0.22, freq=1.6,
             col_fn=lambda co, v, c=c, s=s: mixc(llo, lhi, 0.4 + (co.z - c.z) / (s * 1.3)))
        for q in range(2):
            a = rnd.uniform(0, TAU)
            c2 = c + Vector((math.cos(a), math.sin(a), 0)) * s * 0.6 + Z * s * rnd.uniform(0.15, 0.4)
            s2 = s * rnd.uniform(0.55, 0.7)
            blob(b, c2, (s2, s2, s2 * 0.85), leaf_mi, seed=rnd.random() * 77, subdiv=2, amp=0.2, freq=1.6,
                 col_fn=lambda co, v, c=c, s=s: mixc(llo, lhi, 0.4 + (co.z - c.z) / (s * 1.3)))
        # hanging moss strands under the clump
        for m in range(12):
            a, rr = TAU * m / 12 + rnd.uniform(-0.2, 0.2), rnd.uniform(0.6, 0.95) * s
            p = c + Vector((math.cos(a) * rr, math.sin(a) * rr, -0.15 * s))
            L = rnd.uniform(0.8, 1.6)
            ph = rnd.uniform(0, 6)
            pts = [p + Z * (-L * i / 5) + Vector((0.06 * math.sin(i * 1.3 + ph), 0.06 * math.cos(i * 1.1 + ph), 0)) for i in range(6)]
            tube(b, pts, [0.07 * (1 - i / 5) ** 1.2 + 0.006 for i in range(6)], 4, moss_mi, cap0=False,
                 col_fn=lambda i, k_, t: mixc(mhi, mlo, t))


def reeds(b, x, y, z, rnd, mi, tail_mi):
    lo, hi = lin("#4f8a3a"), lin("#b9d36a")
    for k in range(rnd.randint(6, 9)):
        a = rnd.uniform(0, TAU)
        base = Vector((x, y, z)) + Vector((math.cos(a), math.sin(a), 0)) * rnd.uniform(0, 0.3)
        h = rnd.uniform(0.9, 1.7)
        bend = Vector((math.cos(a), math.sin(a), 0)) * rnd.uniform(0.15, 0.5)
        side = Vector((-math.sin(a + 0.6), math.cos(a + 0.6), 0))
        w = rnd.uniform(0.05, 0.08)
        rows = []
        n = 6
        for i in range(n):
            t = i / n
            p = base + Z * (h * t) + bend * (t * t)
            ww = w * (1 - t)
            rows.append((b.v(p - side * ww, mixc(lo, hi, t)), b.v(p + side * ww, mixc(lo, hi, t))))
        tip = b.v(base + Z * h + bend, hi)
        for i in range(n - 1):
            b.f((rows[i][0], rows[i][1], rows[i + 1][1], rows[i + 1][0]), mi)
        b.f((rows[-1][0], rows[-1][1], tip), mi)
    for k in range(rnd.randint(1, 3)):
        a = rnd.uniform(0, TAU)
        base = Vector((x, y, z)) + Vector((math.cos(a), math.sin(a), 0)) * rnd.uniform(0, 0.25)
        h = rnd.uniform(1.2, 1.7)
        tilt = Vector((math.cos(a), math.sin(a), 0)) * 0.12
        tube(b, [base + (Z * h + tilt) * (i / 4) for i in range(5)], [0.022] * 5, 5, mi, col_fn=lambda i, k_, t: lo)
        tp = base + Z * h + tilt
        tube(b, [tp + (Z + tilt) * (0.08 * i) for i in range(5)], [0.065, 0.075, 0.075, 0.07, 0.06], 8, tail_mi)


def lily(b, x, y, r, rnd, mi, flower_mi=None, center_mi=None):
    notch = 0.35
    a0 = rnd.uniform(0, TAU)
    lo, hi = lin("#3f8a3e"), lin("#86c95a")
    c = b.v((x, y, 0.05), hi)
    n = 16
    ring = []
    for k in range(n + 1):
        a = a0 + notch / 2 + (TAU - notch) * k / n
        rr = r * (1 + 0.04 * math.sin(a * 5))
        ring.append(b.v((x + rr * math.cos(a), y + rr * math.sin(a), 0.07), lo))
    for k in range(n):
        b.f((c, ring[k], ring[k + 1]), mi)
    if flower_mi is not None:
        pc = Vector((x + r * 0.2, y, 0.09))
        for k in range(7):
            a = TAU * k / 7
            d = Vector((math.cos(a), math.sin(a), 0))
            sd = Vector((-d.y, d.x, 0))
            L = 0.24
            base = pc + d * 0.03
            tip = base + d * L * 0.7 + Z * L * 0.75
            m1 = base.lerp(tip, 0.5) + d * 0.04
            v0 = b.v(base, WHITE)
            v1 = b.v(m1 - sd * 0.07, WHITE)
            v2 = b.v(tip, WHITE)
            v3 = b.v(m1 + sd * 0.07, WHITE)
            b.f((v0, v3, v2, v1), flower_mi)
        sphere(b, pc + Z * 0.06, 0.06, center_mi, 8, 5)


def mushroom(b, x, y, z, s, mi_stem, mi_glow):
    lathe(b, [(0, z - 0.1), (0.09 * s, z - 0.1), (0.07 * s, z + 0.2 * s), (0.065 * s, z + 0.42 * s), (0, z + 0.44 * s)], 10, mi_stem)
    zc = z + 0.38 * s
    rc = 0.3 * s
    prof = [(0, zc + 0.02 * s), (rc * 0.6, zc - 0.01 * s), (rc * 0.98, zc + 0.02 * s), (rc, zc + 0.07 * s)]
    for i in range(1, 6):
        a = math.pi / 2 * i / 6
        prof.append((rc * math.cos(a), zc + 0.07 * s + 0.22 * s * math.sin(a)))
    prof.append((0, zc + 0.29 * s))
    lathe(b, prof, 14, mi_glow)


# ---------------------------------------------------------------- the three islands
def build_island_tropic():
    reset()
    rnd = random.Random(7)
    b = Builder("island_tropic")
    T = Terrain(6.3, Hb=0.45, bw=0.32, seed=3.1, hills=((0.4, -0.3, 4.6, 1.05), (-1.9, 1.5, 2.6, 0.45), (2.0, 1.8, 2.2, 0.3)), namp=0.14)
    terr = b.m(mat("Terrain", "#ffffff", 0.92, vcol=True))
    sand, dsand, wet, under = lin("#f5dca2"), lin("#e9c98a"), lin("#cfae74"), lin("#a7dcc4")
    g1, g2 = lin("#69c04f"), lin("#9ad85e")

    def col(x, y, h, hill, s, nz):
        if s >= 1 or h < 0:
            return mixc(wet, under, sstep(0.0, 1.0, -h))
        c = mixc(sand, dsand, 0.5 + fbm(x * 0.9, y * 0.9, 2.0) * 1.2)
        c = mixc(wet, c, sstep(0.0, 0.12, h))
        g = sstep(0.18, 0.38, hill + 0.2 * fbm(x * 0.7 + 4, y * 0.7, 1.0))
        return mixc(c, mixc(g1, g2, 0.5 + fbm(x * 0.6, y * 0.6, 7.0) * 1.5), g)
    build_terrain(b, T, terr, col, rings=44, segs=112)
    trunk = b.m(mat("PalmTrunk", "#ffffff", 0.85, vcol=True))
    leaf = b.m(mat("PalmLeaf", "#ffffff", 0.6, vcol=True, double=True))
    nut = b.m(mat("Coconut", "#6e4a2a", 0.6))
    for (px, py, ht) in ((1.9, -1.6, 4.3), (-2.4, 0.2, 3.7), (0.4, 2.3, 4.0), (3.3, 1.2, 3.3)):
        palm(b, px, py, T, rnd, height=ht, lean=0.3, mats=(trunk, leaf, nut))
    fol = b.m(mat("Foliage", "#ffffff", 0.75, vcol=True))
    flower = b.m(mat("Flower", "#ff6b8b", 0.5))
    for (bx, by, s) in ((-0.8, -2.2, 0.75), (-1.7, 2.6, 0.6), (2.8, -0.2, 0.55), (-0.5, 0.9, 0.5)):
        bush(b, bx, by, T, rnd, s, fol, flower)
    rk = b.m(mat("Rock", "#ffffff", 0.85, vcol=True))
    for k, (a, sc) in enumerate(((0.4, 0.75), (2.2, 0.5), (3.6, 0.95), (4.3, 0.45), (5.4, 0.6))):
        r = T.shore(a) * rnd.uniform(0.93, 1.05)
        x, y = r * math.cos(a), r * math.sin(a)
        rock(b, x, y, T.h(x, y) + sc * 0.15, (sc * 1.2, sc, sc * 0.75), rnd, rk)
    o = b.finish()
    return export("island_tropic", [o])


def build_island_pine():
    reset()
    rnd = random.Random(11)
    b = Builder("island_pine")
    cab = (-2.4, -2.9)
    T = Terrain(7.6, Hb=0.38, bw=0.22, seed=8.4, wob=0.09,
                hills=((0.5, 0.8, 6.0, 1.3), (2.8, 2.2, 3.2, 1.0), (-1.0, 3.2, 2.8, 0.6), (3.4, -2.6, 2.4, 0.5)),
                namp=0.25, nfreq=0.4, pads=((cab[0], cab[1], 1.7, 0.9),))
    terr = b.m(mat("Terrain", "#ffffff", 0.92, vcol=True))
    sand, wet, under = lin("#e3d3a6"), lin("#b7a27a"), lin("#8fc7b5")
    g1, g2 = lin("#5aa74c"), lin("#8ccb5c")
    rockc, dirt = lin("#9c9a92"), lin("#b98d5e")

    def col(x, y, h, hill, s, nz):
        if s >= 1 or h < 0:
            return mixc(wet, under, sstep(0.0, 1.0, -h))
        c = mixc(wet, sand, sstep(0.0, 0.1, h))
        g = sstep(0.16, 0.3, h + 0.12 * fbm(x * 0.6 + 2, y * 0.6, 3.0))
        gc = mixc(g1, g2, 0.5 + fbm(x * 0.5, y * 0.5, 9.0) * 1.6)
        c = mixc(c, gc, g)
        c = mixc(c, rockc, sstep(0.86, 0.72, nz) * sstep(0.1, 0.3, h))
        d = math.hypot(x - cab[0], y - cab[1])
        c = mixc(c, dirt, sstep(2.4, 1.6, d) * 0.8)
        return c
    build_terrain(b, T, terr, col, rings=44, segs=112)
    gz = T.h(*cab)
    log_cabin(b, cab[0], cab[1], gz - 0.05, math.radians(-30),
              (b.m(mat("Logs", "#ffffff", 0.8, vcol=True)), b.m(mat("Roof", "#c4553d", 0.65)), b.m(mat("Door", "#6b4428", 0.7)),
               b.m(mat("Stone", "#a7a39b", 0.85)), b.m(mat("Window", "#ffd98a", 0.3, emit="#ffc860", es=1.2)), b.m(mat("Trim", "#f1e6cf", 0.6))))
    trunk = b.m(mat("Bark", "#ffffff", 0.85, vcol=True))
    leaf = b.m(mat("PineLeaf", "#ffffff", 0.7, vcol=True))
    pts = place_points(rnd, T, 10, 0.12, 0.72, 1.7, avoid=((cab[0], cab[1], 2.4),))
    for (x, y) in pts:
        H = rnd.uniform(3.2, 4.6)
        conifer(b, x, y, T, rnd, H, H * rnd.uniform(0.3, 0.36), (trunk, leaf))
    rk = b.m(mat("Rock", "#ffffff", 0.85, vcol=True))
    for k in range(7):
        a = rnd.uniform(0, TAU)
        s = rnd.uniform(0.85, 1.02)
        r = T.shore(a) * s
        x, y = r * math.cos(a), r * math.sin(a)
        sc = rnd.uniform(0.5, 1.1)
        rock(b, x, y, T.h(x, y) + sc * 0.1, (sc * 1.25, sc, sc * 0.85), rnd, rk, moss=True, subdiv=3 if sc > 0.8 else 2)
    for (x, y, sc) in ((1.2, -1.6, 0.7), (4.5, 0.5, 0.55)):
        rock(b, x, y, T.h(x, y) + 0.1, (sc * 1.2, sc, sc * 0.8), rnd, rk, moss=True)
    fol = b.m(mat("Foliage", "#ffffff", 0.75, vcol=True))
    for (x, y) in place_points(rnd, T, 5, 0.3, 0.8, 1.2, avoid=[(p[0], p[1], 0.9) for p in pts] + [(cab[0], cab[1], 2.4)]):
        bush(b, x, y, T, rnd, 0.5, fol, None, "#3f8a40", "#86c45c")
    o = b.finish()
    return export("island_pine", [o])


def build_island_swamp():
    reset()
    rnd = random.Random(23)
    b = Builder("island_swamp")
    T = Terrain(5.4, Hb=0.22, bw=0.4, seed=5.5, wob=0.1,
                hills=((0.4, 0.3, 3.9, 0.45), (-1.6, -1.2, 1.8, 0.25), (1.8, 1.6, 1.6, 0.3)), namp=0.12, nfreq=0.6, so=0.22)
    terr = b.m(mat("Terrain", "#ffffff", 0.8, vcol=True))
    mud, wmud, under = lin("#7a5c3c"), lin("#4f3c2a"), lin("#4e6b45")
    m1, m2 = lin("#5e8a3a"), lin("#8aab4a")

    def col(x, y, h, hill, s, nz):
        if s >= 1 or h < 0:
            return mixc(wmud, under, sstep(0.0, 0.9, -h))
        c = mixc(wmud, mud, sstep(0.0, 0.14, h))
        g = sstep(0.05, 0.25, hill * 0.8 + 0.25 * fbm(x * 0.9 + 2, y * 0.9, 3.0))
        return mixc(c, mixc(m1, m2, 0.5 + fbm(x * 0.7, y * 0.7, 4.0) * 1.6), g)
    build_terrain(b, T, terr, col, rings=40, segs=104)
    wood = b.m(mat("Mangrove", "#ffffff", 0.85, vcol=True))
    leaf = b.m(mat("SwampLeaf", "#ffffff", 0.75, vcol=True))
    moss = b.m(mat("Moss", "#ffffff", 0.9, vcol=True))
    mangrove(b, 0.6, 0.4, T, rnd, 3.4, (wood, leaf, moss))
    mangrove(b, -2.3, -1.4, T, rnd, 2.6, (wood, leaf, moss))
    reed = b.m(mat("Reed", "#ffffff", 0.7, vcol=True, double=True))
    cattail = b.m(mat("Cattail", "#6a4026", 0.7))
    for a in (0.3, 1.4, 2.5, 3.3, 4.4, 5.6):
        r = T.shore(a) * rnd.uniform(0.95, 1.08)
        x, y = r * math.cos(a), r * math.sin(a)
        reeds(b, x, y, max(T.h(x, y), -0.3) - 0.05, rnd, reed, cattail)
    pad = b.m(mat("LilyPad", "#ffffff", 0.5, vcol=True))
    flw = b.m(mat("LilyFlower", "#ffb3cf", 0.5, double=True))
    ctr = b.m(mat("Pollen", "#ffd54a", 0.5))
    k = 0
    for i in range(30):
        a = rnd.uniform(0, TAU)
        r = T.shore(a) * rnd.uniform(1.05, 1.2)
        x, y = r * math.cos(a), r * math.sin(a)
        lily(b, x, y, rnd.uniform(0.3, 0.55), rnd, pad, flw if i % 5 == 0 else None, ctr)
    stem = b.m(mat("MushStem", "#f2ead6", 0.6))
    glow = b.m(mat("Glow", "#2fd9b0", 0.4, emit="#35f0c0", es=4.0))
    for (x, y, s) in ((-0.1, -2.9, 1.0), (-0.45, -2.75, 0.7), (0.2, -2.55, 0.55), (3.1, -0.3, 0.9), (2.85, -0.65, 0.6)):
        mushroom(b, x, y, T.h(x, y) - 0.03, s * 1.6, stem, glow)
    stump_wood = wood
    sx, sy = 2.0, 2.2
    gz = T.h(sx, sy)
    lathe(b, [(0, gz - 0.2), (0.48, gz - 0.2), (0.4, gz + 0.1), (0.33, gz + 0.45), (0.3, gz + 0.52), (0.15, gz + 0.5), (0, gz + 0.48)], 12, stump_wood,
          rmod=lambda i, a: 1 + 0.12 * math.sin(5 * a) * (1 if i < 3 else 0.3), col_fn=lambda i, k, p: lin("#6b5238") if i < 5 else lin("#c9a46e"))
    rk = b.m(mat("Rock", "#ffffff", 0.85, vcol=True))
    for (a, sc) in ((1.0, 0.45), (3.9, 0.55), (5.0, 0.35)):
        r = T.shore(a) * 0.98
        x, y = r * math.cos(a), r * math.sin(a)
        rock(b, x, y, T.h(x, y) + 0.05, (sc * 1.2, sc, sc * 0.7), rnd, rk, moss=True, c_lo="#5d635c", c_hi="#9aa093")
    o = b.finish()
    return export("island_swamp", [o])


# ================================================================ lighthouse
def build_lighthouse():
    reset()
    rnd = random.Random(5)
    b = Builder("Lighthouse")
    rk = b.m(mat("Rock", "#ffffff", 0.85, vcol=True))
    for j, (x, y, z, s) in enumerate(((0, 0, -0.2, (2.3, 2.2, 1.5)), (1.5, 0.95, 0.05, (1.05, 0.95, 1.15)), (-1.45, 0.9, 0.0, (1.0, 0.95, 1.1)),
                         (0.45, -1.6, 0.05, (1.0, 0.9, 1.05)), (-1.1, -1.25, 0.0, (0.85, 0.8, 0.95)), (1.75, -0.65, -0.1, (0.75, 0.7, 0.85)))):
        rock(b, x, y, z, s, rnd, rk, moss=True, subdiv=3 if j == 0 else 2, c_lo="#6f767c", c_hi="#b5b9bc")
    white = b.m(mat("White", "#f7f2e8", 0.5))
    red = b.m(mat("Red", "#d8433a", 0.45))
    stone = b.m(mat("Stone", "#c9c1b2", 0.85))
    metal = b.m(mat("Metal", "#2f3a44", 0.4, 0.5))
    # plinth on the rock
    lathe(b, [(0, 0.6), (1.85, 0.6), (1.9, 0.95), (1.85, 1.1), (1.72, 1.15), (1.6, 1.2), (0, 1.2)], 32, stone)
    # tower with red / white bands
    z0, z1 = 1.15, 10.9
    bands = [z0, 2.4, 3.6, 4.8, 6.0, 7.2, 8.4, 9.6, z1]
    zs = sorted(set([round(z0 + (z1 - z0) * i / 12, 4) for i in range(13)] + bands))

    def rad(z):
        u = (z - z0) / (z1 - z0)
        return 1.38 - 0.4 * u - 0.05 * math.sin(math.pi * u)
    prof = [(0, z0)] + [(rad(z), z) for z in zs] + [(0, z1)]

    def band_mat(i, k):
        zm = (prof[i][1] + prof[i + 1][1]) / 2
        for j in range(len(bands) - 1):
            if bands[j] <= zm < bands[j + 1]:
                return white if j % 2 == 0 else red
        return white
    lathe(b, prof, 32, white, mat_fn=band_mat)
    torus(b, (0, 0, z0 + 0.12), rad(z0) + 0.02, 0.1, stone, seg=32, mseg=6)
    # gallery
    lathe(b, [(0, 10.5), (0.98, 10.5), (1.3, 10.75), (1.65, 10.95), (1.78, 11.02), (1.8, 11.12), (1.75, 11.2), (0, 11.2)], 32, metal)
    for k in range(16):
        a = TAU * k / 16
        p = Vector((math.cos(a) * 1.66, math.sin(a) * 1.66, 0))
        tube(b, [p + Z * (11.2 + 0.7 * i / 3) for i in range(4)], [0.035] * 4, 6, metal)
    torus(b, (0, 0, 11.92), 1.66, 0.05, metal, seg=36, mseg=6)
    torus(b, (0, 0, 11.55), 1.66, 0.03, metal, seg=36, mseg=5)
    # lamp room
    lathe(b, [(0, 11.2), (1.0, 11.2), (1.02, 11.5), (0.98, 11.55), (0, 11.55)], 32, red)
    glass = b.m(mat("Glass", "#cfeeff", 0.05, alpha=0.35, double=True))
    lathe(b, [(0, 11.5), (0.9, 11.5), (0.9, 12.8), (0, 12.8)], 32, glass)
    for k in range(8):
        a = TAU * k / 8 + 0.2
        p = Vector((math.cos(a) * 0.91, math.sin(a) * 0.91, 0))
        tube(b, [p + Z * (11.5 + 1.3 * i / 3) for i in range(4)], [0.04] * 4, 6, metal, cap0=False, cap1=False)
    lamp = b.m(mat("Lamp", "#fff1c2", 0.2, emit="#ffc65a", es=4.0))
    sphere(b, (0, 0, 12.1), 0.45, lamp, 14, 9)
    lathe(b, [(0, 11.55), (0.22, 11.55), (0.16, 11.75), (0, 11.75)], 12, metal)
    # roof
    rp = [(0, 12.7), (1.12, 12.7), (1.18, 12.8), (1.14, 12.9)]
    for i in range(1, 9):
        u = i / 9
        rp.append((1.08 * (1 - u) ** 1.25 + 0.06, 12.9 + 1.3 * u))
    rp += [(0.05, 14.25), (0, 14.27)]
    lathe(b, rp, 32, red)
    sphere(b, (0, 0, 14.38), 0.16, metal, 12, 8)
    lathe(b, [(0, 14.45), (0.05, 14.45), (0.012, 14.95), (0, 14.96)], 8, metal)
    # door (+X), steps, windows
    door = b.m(mat("Door", "#5b3b25", 0.7))
    rd = rad(1.9)
    box(b, (rd - 0.02, 0, 1.85), (0.2, 0.72, 1.1), door, bevel=0.05, segs=2)
    lathe(b, [(0, -0.1), (0.34, -0.1), (0.36, -0.07), (0.36, 0.07), (0.34, 0.1), (0, 0.1)], 20, door, center=(rd, 0, 2.4), M=align_z(X))
    box(b, (rd + 0.35, 0, 1.2), (0.6, 1.0, 0.18), stone, bevel=0.06, segs=2)
    win = b.m(mat("Window", "#2e4258", 0.15, emit="#ffd27a", es=0.4))
    for (z, a) in ((4.2, 0.6), (6.6, -2.4), (8.9, 1.9)):
        r = rad(z)
        R_ = Matrix.Rotation(a, 3, "Z")
        box(b, R_ @ Vector((r - 0.02, 0, z)), (0.16, 0.5, 0.7), white, bevel=0.05, segs=2, M=R_)
        box(b, R_ @ Vector((r + 0.04, 0, z)), (0.08, 0.34, 0.54), win, bevel=0.04, segs=2, M=R_)
    o = b.finish()
    return export("lighthouse", [o])


# ================================================================ buoy
def build_buoy():
    reset()
    b = Builder("Buoy")
    red = b.m(mat("BuoyRed", "#e8443a", 0.4))
    white = b.m(mat("BuoyWhite", "#f6f4ee", 0.4))
    pole = b.m(mat("Pole", "#4a4f57", 0.4, 0.6))
    rubber = b.m(mat("Rubber", "#2b2f36", 0.7))
    lamp = b.m(mat("Lamp", "#ffd23f", 0.3, emit="#ffc53a", es=3.0))
    prof = [(0, -0.32), (0.3, -0.3), (0.5, -0.2), (0.6, -0.05), (0.64, 0.1), (0.64, 0.25), (0.62, 0.3), (0.6, 0.45), (0.56, 0.58),
            (0.5, 0.62), (0.42, 0.72), (0.32, 0.82), (0.22, 0.88), (0.12, 0.91), (0, 0.92)]

    def mfn(i, k):
        zm = (prof[i][1] + prof[i + 1][1]) / 2
        return white if 0.3 <= zm < 0.62 else red
    lathe(b, prof, 32, red, mat_fn=mfn)
    torus(b, (0, 0, 0.12), 0.67, 0.08, rubber, seg=36, mseg=8)
    # three-leg cage up to the lamp platform
    for k in range(3):
        a = TAU * k / 3 + 0.5
        p0 = Vector((math.cos(a) * 0.32, math.sin(a) * 0.32, 0.8))
        p1 = Vector((math.cos(a) * 0.12, math.sin(a) * 0.12, 1.45))
        tube(b, [p0.lerp(p1, i / 4) for i in range(5)], [0.035] * 5, 6, pole)
    torus(b, (0, 0, 1.15), 0.22, 0.03, pole, seg=24, mseg=6)
    tube(b, [Vector((0, 0, 0.85 + 1.55 * i / 6)) for i in range(7)], [0.045] * 7, 8, pole)
    lathe(b, [(0, 2.35), (0.16, 2.35), (0.17, 2.4), (0.1, 2.42), (0, 2.42)], 12, pole)
    sphere(b, (0, 0, 2.53), 0.13, lamp, 14, 9)
    lathe(b, [(0, 2.64), (0.12, 2.64), (0.08, 2.7), (0, 2.72)], 12, pole)
    body = b.finish()
    # the flag, separate node, UVs filling 0..1 (u along +X away from the pole, v up)
    fb = Builder("Flag")
    fm = fb.m(mat("Flag", "#ffffff", 0.6, double=True))
    x0, x1, z0, z1 = 0.05, 1.05, 1.72, 2.32
    nu, nv = 12, 6
    grid = []
    for j in range(nv + 1):
        row = []
        for i in range(nu + 1):
            u, v = i / nu, j / nv
            x = x0 + (x1 - x0) * u
            y = 0.06 * math.sin(u * math.pi * 1.6) * u
            z = z0 + (z1 - z0) * v - 0.04 * u * u
            row.append(fb.v((x, y, z)))
        grid.append(row)
    for j in range(nv):
        for i in range(nu):
            f = fb.f((grid[j][i], grid[j][i + 1], grid[j + 1][i + 1], grid[j + 1][i]), fm)
            for loop, (uu, vv) in zip(f.loops, ((i, j), (i + 1, j), (i + 1, j + 1), (i, j + 1))):
                loop[fb.uv].uv = (uu / nu, vv / nv)
    flag = fb.finish()
    return export("buoy", [body, flag])


# ================================================================ tentacle
def build_tentacle():
    reset()
    b = Builder("Tentacle")
    skin = b.m(mat("Tentacle", "#ffffff", 0.45, vcol=True))
    belly = b.m(mat("Belly", "#f2a7c3", 0.5))
    cup = b.m(mat("Suckers", "#ffd3e6", 0.45))
    n = 110
    L = 9.6
    th = []
    pts = [Vector((0, 0, -0.4))]
    ys = []
    for i in range(n):
        u = i / (n - 1)
        th.append(-0.22 + 0.5 * u + 6.4 * u ** 3.4)
    for i in range(1, n):
        ds = L / (n - 1)
        t = (th[i - 1] + th[i]) / 2
        pts.append(pts[-1] + Vector((math.sin(t), 0, math.cos(t))) * ds)
    for i, p in enumerate(pts):
        u = i / (n - 1)
        p.y = 0.35 * math.sin(u * 3.0) * u
    radii = [0.62 * (1 - i / (n - 1)) ** 0.85 + 0.035 for i in range(n)]
    zmax = max(p.z + r for p, r in zip(pts, radii))
    k = 6.0 / (zmax + 0.0)
    pts = [Vector((p.x * k, p.y * k, p.z * k)) for p in pts]
    radii = [r * k for r in radii]
    normals = [Vector((math.cos(t), 0, -math.sin(t))) for t in th]
    top, bot = lin("#7b2fa8"), lin("#c23a55")

    def col(i, kk, t):
        return mixc(bot, top, 0.3 + 0.7 * t)

    def mfn(i, kk):
        a = TAU * kk / 16 + TAU / 32
        return belly if math.cos(a) > 0.55 else skin
    tube(b, pts, radii, 16, skin, cap0=False, cap1=True, mat_fn=mfn, col_fn=col, normals=normals)
    # suckers in two staggered rows on the inner side
    s = 0.0
    i = 2
    row = 0
    while i < n - 6:
        r = radii[i]
        T_ = (pts[i + 1] - pts[i - 1]).normalized()
        N = normals[i] - T_ * normals[i].dot(T_)
        N.normalize()
        Bn = T_.cross(N)
        a = 0.42 if row % 2 else -0.42
        d = (N * math.cos(a) + Bn * math.sin(a)).normalized()
        c = pts[i] + d * r * 0.9
        sr = r * 0.3
        lathe(b, [(0, -sr * 0.1), (sr * 0.9, -sr * 0.05), (sr, sr * 0.25), (sr * 0.8, sr * 0.42), (sr * 0.45, sr * 0.25), (0, sr * 0.12)], 8, cup,
              center=c, M=align_z(d))
        step = max(0.12, r * 0.55)
        # advance along the curve by ~step
        acc = 0.0
        while i < n - 6 and acc < step:
            acc += (pts[i + 1] - pts[i]).length
            i += 1
        row += 1
    o = b.finish()
    return export("tentacle", [o])


# ================================================================ dock
def build_dock():
    reset()
    rnd = random.Random(9)
    b = Builder("Dock")
    wood = b.m(mat("Planks", "#ffffff", 0.85, vcol=True))
    post_m = b.m(mat("Posts", "#ffffff", 0.85, vcol=True))
    iron = b.m(mat("Iron", "#33383f", 0.45, 0.6))
    rope = b.m(mat("Rope", "#d8b77a", 0.9))
    glow = b.m(mat("Glow", "#ffe7a3", 0.3, emit="#ffbf4f", es=3.5))
    p1, p2, p3 = lin("#b98552"), lin("#a06e42"), lin("#c99a63")
    n = 24
    pw = 10.0 / n
    for i in range(n):
        x = -5 + pw * (i + 0.5)
        c = (p1, p2, p3)[rnd.randrange(3)]
        M = Matrix.Rotation(rnd.uniform(-0.015, 0.015), 3, "X") @ Matrix.Rotation(rnd.uniform(-0.02, 0.02), 3, "Z")
        box(b, (x, rnd.uniform(-0.03, 0.03), 0.35 + rnd.uniform(-0.01, 0.01)), (pw - 0.045, 2.2, 0.12), wood, bevel=0.035, segs=1, M=M, col=c)
    for y in (-0.8, 0.8):
        box(b, (0, y, 0.2), (10.1, 0.22, 0.18), wood, bevel=0.05, segs=2, col=mulc(p2, 0.7))
    plo, phi = lin("#5e3f26"), lin("#9a6a40")
    for i in range(5):
        x = -4.75 + i * 2.375
        for y in (-1.18, 1.18):
            prof = [(0, -2.2), (0.2, -2.2), (0.2, 0.3), (0.2, 0.5), (0.19, 0.58), (0.15, 0.64), (0.08, 0.67), (0, 0.68)]
            lathe(b, prof, 12, post_m, center=(x, y, 0), rmod=lambda ii, a: 1 + 0.03 * math.sin(3 * a),
                  col_fn=lambda ii, k, p: mixc(plo, phi, sstep(-1.0, 0.4, p.z)))
            if i % 2 == 0:
                torus(b, (x, y, 0.1), 0.215, 0.04, rope, seg=16, mseg=6)
    for (x, y) in ((-3.0, 0.72), (2.6, -0.72)):
        z = 0.4
        lathe(b, [(0, z), (0.24, z), (0.24, z + 0.05), (0.15, z + 0.12), (0.13, z + 0.3), (0.2, z + 0.38), (0.22, z + 0.44), (0.18, z + 0.5), (0, z + 0.52)],
              16, iron, center=(x, y, 0))
        torus(b, (x, y, z + 0.16), 0.165, 0.04, rope, seg=20, mseg=6)
        torus(b, (x, y, z + 0.08), 0.18, 0.04, rope, seg=20, mseg=6)
    # rope coil lying on the deck
    for k in range(3):
        torus(b, (-1.2, -0.6, 0.47 + k * 0.06), 0.3 - k * 0.05, 0.045, rope, seg=24, mseg=6)
    # barrel
    bx, by = 0.9, 0.62
    bprof = [(0, 0.41), (0.27, 0.41), (0.3, 0.45)] + [(0.3 + 0.06 * math.sin(math.pi * u / 6), 0.45 + 0.6 * u / 6) for u in range(1, 6)] + [(0.3, 1.05), (0.27, 1.09), (0, 1.09)]
    lathe(b, bprof, 16, post_m, center=(bx, by, 0), col_fn=lambda ii, k, p: lin("#a8743f"))
    for zz in (0.55, 0.95):
        torus(b, (bx, by, zz), 0.335, 0.025, iron, seg=24, mseg=5)
    # lantern post at the seaward end (+X)
    lx, ly = 4.75, 1.18
    lathe(b, [(0, 0.6), (0.12, 0.6), (0.1, 0.75), (0.09, 2.6), (0.12, 2.68), (0, 2.72)], 10, post_m, center=(lx, ly, 0),
          col_fn=lambda ii, k, p: lin("#6e4a2c"))
    arm = [Vector((lx, ly, 2.45)) + Vector((0, -0.55 * t / 5, 0.12 * math.sin(math.pi * t / 5))) for t in range(6)]
    tube(b, arm, [0.04] * 6, 6, iron)
    hx, hy = lx, ly - 0.55
    tube(b, [Vector((hx, hy, 2.42 - 0.1 * t / 2)) for t in range(3)], [0.015] * 3, 5, iron)
    lathe(b, [(0, 2.17), (0.1, 2.17), (0.16, 2.24), (0.06, 2.33), (0, 2.34)], 10, iron, center=(hx, hy, 0))
    lathe(b, [(0, 1.92), (0.11, 1.92), (0.13, 2.05), (0.11, 2.18), (0, 2.18)], 12, glow, center=(hx, hy, 0))
    lathe(b, [(0, 1.84), (0.14, 1.84), (0.14, 1.92), (0, 1.92)], 10, iron, center=(hx, hy, 0))
    for k in range(4):
        a = TAU * k / 4 + 0.4
        d = Vector((math.cos(a), math.sin(a), 0)) * 0.135
        tube(b, [Vector((hx, hy, 1.9)) + d + Z * (0.32 * t / 2) for t in range(3)], [0.014] * 3, 4, iron)
    o = b.finish()
    return export("dock", [o])


BUILDS = {
    "island_tropic": build_island_tropic,
    "island_pine": build_island_pine,
    "island_swamp": build_island_swamp,
    "lighthouse": build_lighthouse,
    "buoy": build_buoy,
    "tentacle": build_tentacle,
    "dock": build_dock,
}

if __name__ == "__main__":
    for name, fn in BUILDS.items():
        if ONLY and name not in ONLY:
            continue
        fn()
