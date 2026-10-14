"""HookLine 3D models, built procedurally in Blender (bpy) and exported as glTF binaries.

Run: bvenv/bin/python blender/build_models.py <out_dir>
Blender is Z-up; the glTF exporter converts to Y-up. Boats and fish face +X.
"""
import math
import random
import sys

import bpy
import bmesh  # noqa: E402  (must come after bpy)
from mathutils import Vector

OUT = sys.argv[-1] if len(sys.argv) > 1 and not sys.argv[-1].endswith(".py") else "."


# ---------------------------------------------------------------- helpers
def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    _mats.clear()


_mats = {}


def mat(name, color, rough=0.6, metal=0.0, emit=None):
    if name in _mats:
        return _mats[name]
    m = bpy.data.materials.new(name)
    m.use_nodes = True
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (*srgb(color), 1)
    b.inputs["Roughness"].default_value = rough
    b.inputs["Metallic"].default_value = metal
    if emit:
        b.inputs["Emission Color"].default_value = (*srgb(emit), 1)
        b.inputs["Emission Strength"].default_value = 1.5
    _mats[name] = m
    return m


def srgb(hexstr):
    h = hexstr.lstrip("#")
    c = [int(h[i:i + 2], 16) / 255 for i in (0, 2, 4)]
    return tuple(((x + 0.055) / 1.055) ** 2.4 if x > 0.04045 else x / 12.92 for x in c)


def assign(obj, m):
    obj.data.materials.clear()
    obj.data.materials.append(m)


def smooth(obj, on=True):
    for p in obj.data.polygons:
        p.use_smooth = on


def sphere(name, loc, scale, m, seg=24, rings=14):
    bpy.ops.mesh.primitive_uv_sphere_add(segments=seg, ring_count=rings, location=loc)
    o = bpy.context.active_object
    o.name = name
    o.scale = scale
    assign(o, m)
    smooth(o)
    return o


def cyl(name, loc, r, depth, m, rot=(0, 0, 0), verts=24, r2=None):
    if r2 is None:
        bpy.ops.mesh.primitive_cylinder_add(vertices=verts, radius=r, depth=depth, location=loc, rotation=rot)
    else:
        bpy.ops.mesh.primitive_cone_add(vertices=verts, radius1=r, radius2=r2, depth=depth, location=loc, rotation=rot)
    o = bpy.context.active_object
    o.name = name
    assign(o, m)
    smooth(o)
    return o


def cube(name, loc, scale, m, bevel=0.02):
    bpy.ops.mesh.primitive_cube_add(location=loc)
    o = bpy.context.active_object
    o.name = name
    o.scale = scale
    assign(o, m)
    if bevel:
        bpy.ops.object.transform_apply(scale=True)
        mod = o.modifiers.new("bevel", "BEVEL")
        mod.width = bevel
        mod.segments = 3
    return o


def torus(name, loc, major, minor, m, scale=(1, 1, 1)):
    bpy.ops.mesh.primitive_torus_add(major_radius=major, minor_radius=minor, location=loc, major_segments=48, minor_segments=12)
    o = bpy.context.active_object
    o.name = name
    o.scale = scale
    assign(o, m)
    smooth(o)
    return o


def limb(name, a, b, r, m):
    """Cylinder from point a to point b, with rounded ends."""
    a, b = Vector(a), Vector(b)
    d = b - a
    o = cyl(name, (a + b) / 2, r, d.length, m, verts=16)
    o.rotation_mode = "QUATERNION"
    o.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d.normalized())
    sphere(name + "_a", a, (r, r, r), m, 12, 8)
    sphere(name + "_b", b, (r, r, r), m, 12, 8)
    return o


def join(objs, name):
    bpy.ops.object.select_all(action="DESELECT")
    for o in objs:
        o.select_set(True)
    bpy.context.view_layer.objects.active = objs[0]
    bpy.ops.object.join()
    o = bpy.context.active_object
    o.name = name
    return o


def export(path, objects=None):
    bpy.ops.object.select_all(action="DESELECT")
    for o in (objects or bpy.context.scene.objects):
        o.select_set(True)
    bpy.ops.export_scene.gltf(filepath=path, export_format="GLB", use_selection=True, export_apply=True,
                              export_yup=True, export_extras=False, export_cameras=False, export_lights=False)
    print("exported", path)


# ---------------------------------------------------------------- boat + fisherman
def build_boat():
    reset()
    wood = mat("Wood", "#c48a54", 0.7)
    wood_dark = mat("WoodDark", "#8a5a33", 0.75)
    stripe = mat("Stripe", "#d9443a", 0.5)
    coat = mat("Coat", "#2f6fc0", 0.65)
    skin = mat("Skin", "#f2bf98", 0.55)
    beard = mat("Beard", "#d0662c", 0.8)
    hat = mat("Hat", "#f6c431", 0.45)
    pants = mat("Pants", "#5b6a3e", 0.8)
    boot = mat("Boot", "#3b2c22", 0.6)
    black = mat("Black", "#141414", 0.3)
    metal = mat("Metal", "#9aa3ad", 0.35, 0.8)
    motor = mat("Motor", "#2a2f38", 0.45, 0.2)
    rodm = mat("Rod", "#3a2a20", 0.4)

    # hull: half an ellipsoid, hollowed with a solidify modifier
    hull = sphere("Hull", (0, 0, 0.42), (1.55, 0.78, 0.62), wood, 40, 20)
    bpy.ops.object.mode_set(mode="EDIT")
    bm = bmesh.from_edit_mesh(hull.data)
    bmesh.ops.bisect_plane(bm, geom=bm.verts[:] + bm.edges[:] + bm.faces[:], plane_co=(0, 0, 0), plane_no=(0, 0, 1), clear_outer=True)
    bmesh.update_edit_mesh(hull.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    sol = hull.modifiers.new("solid", "SOLIDIFY")
    sol.thickness = 0.07
    sol.offset = -1
    # pointier bow
    for v in hull.data.vertices:
        if v.co.x > 0.3:
            v.co.y *= 1 - (v.co.x - 0.3) * 0.45
    hull.data.update()
    rim = torus("Rim", (0, 0, 0.42), 1, 0.045, wood_dark, (1.55, 0.78 * 0.99, 1))
    for v in rim.data.vertices:
        if v.co.x > 0.3 / 1.55:
            v.co.y *= 1 - (v.co.x * 1.55 - 0.3) * 0.45
    band = torus("Stripe", (0, 0, 0.24), 1, 0.05, stripe, (1.47, 0.70, 1))
    for v in band.data.vertices:
        if v.co.x > 0.3 / 1.47:
            v.co.y *= 1 - (v.co.x * 1.47 - 0.3) * 0.45
    floor = cube("Floor", (0, 0, 0.06), (1.0, 0.45, 0.02), wood_dark, 0)
    bench = cube("Bench", (-0.35, 0, 0.36), (0.2, 0.68, 0.035), wood_dark)
    bench2 = cube("Bench2", (0.75, 0, 0.32), (0.16, 0.5, 0.03), wood_dark)

    # outboard motor at the stern
    m1 = cube("MotorHead", (-1.62, 0, 0.62), (0.18, 0.15, 0.17), motor, 0.05)
    m2 = cyl("MotorShaft", (-1.66, 0, 0.18), 0.04, 0.7, metal)
    m3 = cyl("Prop", (-1.66, 0, -0.16), 0.11, 0.05, metal, rot=(0, math.radians(90), 0), verts=8)

    # fisherman on the bench, facing the bow
    body = sphere("Body", (-0.35, 0, 0.78), (0.3, 0.32, 0.4), coat)
    head = sphere("Head", (-0.3, 0, 1.3), (0.21, 0.21, 0.22), skin)
    nose = sphere("Nose", (-0.1, 0, 1.28), (0.06, 0.06, 0.06), mat("Nose", "#ec8f7a", 0.5))
    brd = sphere("Beard", (-0.16, 0, 1.12), (0.17, 0.2, 0.2), beard)
    eyes = [sphere(f"Eye{i}", (-0.12, s * 0.08, 1.36), (0.025, 0.025, 0.03), black, 10, 6) for i, s in enumerate((-1, 1))]
    brim = cyl("Brim", (-0.3, 0, 1.44), 0.33, 0.03, hat, verts=32)
    crown = cyl("Crown", (-0.31, 0, 1.55), 0.2, 0.22, hat, verts=32, r2=0.16)
    legs = [limb(f"Leg{i}", (-0.25, s * 0.13, 0.5), (0.25, s * 0.15, 0.42), 0.085, pants) for i, s in enumerate((-1, 1))]
    shins = [limb(f"Shin{i}", (0.25, s * 0.15, 0.42), (0.3, s * 0.15, 0.12), 0.075, pants) for i, s in enumerate((-1, 1))]
    boots = [sphere(f"Boot{i}", (0.36, s * 0.15, 0.1), (0.12, 0.08, 0.06), boot) for i, s in enumerate((-1, 1))]
    hands_at = (0.05, 0.0, 0.9)
    arms = [limb(f"Arm{i}", (-0.33, s * 0.27, 0.95), (hands_at[0] - 0.02, s * 0.07, hands_at[2]), 0.075, coat) for i, s in enumerate((-1, 1))]
    hands = [sphere(f"Hand{i}", (hands_at[0], s * 0.06, hands_at[2]), (0.07, 0.07, 0.07), skin) for i, s in enumerate((-1, 1))]

    parts = [hull, rim, band, floor, bench, bench2, m1, m2, m3, body, head, nose, brd, *eyes, brim, crown]
    for group in (legs, shins, arms):
        for o in group:
            parts.append(o)
            parts += [bpy.data.objects[o.name + "_a"], bpy.data.objects[o.name + "_b"]]
    parts += boots + hands
    boat = join(parts, "Boat")

    # the rod is separate so the game can bend and aim it; RodTip marks where the line starts
    ang = math.radians(52)
    d = Vector((math.sin(ang), 0, math.cos(ang)))
    L = 2.4
    butt = Vector(hands_at) + Vector((-0.12, 0, -0.08))
    rod = cyl("RodPole", butt + d * (L / 2), 0.028, L, rodm, verts=10, r2=0.008)
    rod.rotation_mode = "QUATERNION"
    rod.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d)
    reel = cyl("Reel", butt + d * 0.32 + Vector((0, -0.07, 0)), 0.065, 0.05, metal, rot=(math.radians(90), 0, 0), verts=16)
    rodobj = join([rod, reel], "Rod")
    bpy.ops.object.empty_add(type="PLAIN_AXES", location=butt + d * L)
    tip = bpy.context.active_object
    tip.name = "RodTip"
    tip.parent = rodobj
    tip.matrix_parent_inverse = rodobj.matrix_world.inverted()
    export(f"{OUT}/boat.glb")


# ---------------------------------------------------------------- fish (tinted per market in the game)
def build_fish():
    reset()
    body_m = mat("FishBody", "#ffffff", 0.35)
    belly_m = mat("FishBelly", "#f4f1ea", 0.4)
    fin_m = mat("FishFin", "#dcdcdc", 0.5)
    black = mat("Black", "#111111", 0.2)
    white = mat("EyeWhite", "#ffffff", 0.2)
    body = sphere("Body", (0, 0, 0), (0.62, 0.2, 0.3), body_m, 32, 16)
    belly = sphere("Belly", (0.05, 0, -0.08), (0.5, 0.17, 0.2), belly_m, 24, 12)
    bpy.ops.mesh.primitive_cone_add(vertices=4, radius1=0.26, radius2=0.0, depth=0.35, location=(-0.72, 0, 0), rotation=(0, math.radians(-90), 0))
    tail = bpy.context.active_object
    tail.scale = (1, 0.18, 1)
    assign(tail, fin_m)
    bpy.ops.mesh.primitive_cone_add(vertices=3, radius1=0.2, radius2=0.0, depth=0.25, location=(-0.05, 0, 0.32))
    dorsal = bpy.context.active_object
    dorsal.scale = (1.3, 0.15, 1)
    assign(dorsal, fin_m)
    eyes = []
    for s in (-1, 1):
        eyes.append(sphere(f"EyeW{s}", (0.42, s * 0.13, 0.08), (0.075, 0.03, 0.075), white, 12, 8))
        eyes.append(sphere(f"EyeP{s}", (0.45, s * 0.155, 0.08), (0.04, 0.015, 0.04), black, 10, 6))
    join([body, belly, tail, dorsal, *eyes], "Fish")
    export(f"{OUT}/fish.glb")


# ---------------------------------------------------------------- islands
def pine(x, y, z, s, leaf):
    trunk = cyl("Trunk", (x, y, z + 0.4 * s), 0.12 * s, 0.8 * s, mat("TrunkM", "#6b4a2f", 0.8), verts=8)
    parts = [trunk]
    for i, (r, h) in enumerate(((0.9, 1.0), (0.7, 0.9), (0.45, 0.8))):
        c = cyl("Cone", (x, y, z + (0.9 + i * 0.6) * s), r * s, h * s, leaf, verts=10, r2=0.0)
        parts.append(c)
    return parts


def palm(x, y, z, s, rnd):
    trunk_m = mat("PalmTrunk", "#a77b4f", 0.8)
    leaf_m = mat("PalmLeaf", "#3fae5a", 0.6)
    parts = []
    lean = rnd.uniform(-0.4, 0.4)
    px, py, pz = x, y, z
    for i in range(6):
        nx, nz = px + lean * 0.12 * s, pz + 0.42 * s
        parts.append(limb("Palm", (px, py, pz), (nx, py, nz), 0.09 * s * (1 - i * 0.08), trunk_m))
        parts += [bpy.data.objects["Palm_a"], bpy.data.objects["Palm_b"]] if False else []
        px, pz = nx, nz
    for k in range(6):
        a = k / 6 * math.tau + rnd.uniform(0, 0.4)
        bpy.ops.mesh.primitive_cone_add(vertices=4, radius1=0.25 * s, radius2=0.0, depth=1.3 * s,
                                        location=(px + math.cos(a) * 0.55 * s, py + math.sin(a) * 0.55 * s, pz - 0.1 * s),
                                        rotation=(math.radians(100) * math.sin(a) * -1, math.radians(100) * math.cos(a), 0))
        leaf = bpy.context.active_object
        leaf.scale = (1, 0.25, 1)
        assign(leaf, leaf_m)
        parts.append(leaf)
    return parts


def build_island(name, seed, radius, kind):
    reset()
    rnd = random.Random(seed)
    sand = mat("Sand", "#f0d8a0", 0.9)
    grass = mat("Grass", "#6cc45a" if kind != "swamp" else "#5d8a45", 0.85)
    rock = mat("Rock", "#9aa1a8", 0.8)
    leaf = mat("Pine", "#2f8a4c", 0.7)
    shelf = mat("Shelf", "#e7cf96", 0.95)
    parts = []
    # underwater sand shelf that shows through the water as a shallow halo
    sh = cyl("Shelf", (0, 0, -0.55), radius * 1.55, 0.3, shelf, verts=48)
    parts.append(sh)
    beach = sphere("Beach", (0, 0, -0.2), (radius * 1.18, radius * 1.18, 0.65), sand, 48, 16)
    top = sphere("Top", (0, 0, 0.15), (radius, radius * 0.92, 1.3), grass, 48, 18)
    for o in (beach, top):
        tex = bpy.data.textures.new(f"n{o.name}{seed}", "CLOUDS")
        tex.noise_scale = 0.8
        d = o.modifiers.new("disp", "DISPLACE")
        d.texture = tex
        d.strength = 0.6
        d.mid_level = 0.5
    parts += [beach, top]
    for _ in range(rnd.randint(3, 6)):
        a, r = rnd.uniform(0, math.tau), rnd.uniform(radius * 0.95, radius * 1.3)
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=rnd.uniform(0.3, 0.8), location=(math.cos(a) * r, math.sin(a) * r, 0.0))
        rk = bpy.context.active_object
        rk.scale = (1, rnd.uniform(0.7, 1.1), rnd.uniform(0.6, 0.9))
        assign(rk, rock)
        parts.append(rk)
    n = int(radius * 1.6)
    for _ in range(n):
        a, r = rnd.uniform(0, math.tau), rnd.uniform(0, radius * 0.7)
        x, y = math.cos(a) * r, math.sin(a) * r
        z = 0.15 + 1.3 * math.sqrt(max(0.0, 1 - (r / radius) ** 2)) - 0.15
        s = rnd.uniform(0.8, 1.3)
        parts += palm(x, y, z, s, rnd) if (kind == "tropic" and rnd.random() < 0.7) else pine(x, y, z, s, leaf)
    join([o for o in bpy.context.scene.objects if o.type == "MESH"], name)
    export(f"{OUT}/{name}.glb")


# ---------------------------------------------------------------- buoy with a flag
def build_buoy():
    reset()
    red = mat("BuoyRed", "#e8443a", 0.4)
    white = mat("BuoyWhite", "#f6f4ee", 0.4)
    pole_m = mat("Pole", "#4a4f57", 0.4, 0.6)
    flag_m = mat("Flag", "#ffffff", 0.6)
    lamp = mat("Lamp", "#ffd23f", 0.3, 0, emit="#ffd23f")
    a = cyl("B1", (0, 0, 0.15), 0.55, 0.3, red, verts=24)
    b = cyl("B2", (0, 0, 0.45), 0.5, 0.3, white, verts=24, r2=0.4)
    c = cyl("B3", (0, 0, 0.7), 0.4, 0.2, red, verts=24, r2=0.18)
    p = cyl("Pole", (0, 0, 1.6), 0.04, 1.8, pole_m, verts=8)
    l = sphere("Lamp", (0, 0, 2.55), (0.12, 0.12, 0.12), lamp, 12, 8)
    join([a, b, c, p, l], "Buoy")
    bpy.ops.mesh.primitive_plane_add(size=1, location=(0.55, 0, 2.15), rotation=(math.radians(90), 0, 0))
    flag = bpy.context.active_object
    flag.name = "Flag"
    flag.scale = (1.0, 0.6, 1)
    assign(flag, flag_m)
    export(f"{OUT}/buoy.glb")


# ---------------------------------------------------------------- kraken tentacle
def build_tentacle():
    reset()
    m = mat("Tentacle", "#8a3fb8", 0.45)
    cups = mat("Suckers", "#f0b6d8", 0.5)
    parts = []
    pts = []
    for i in range(14):
        t = i / 13
        pts.append(Vector((math.sin(t * 2.4) * 0.6 * t, 0, t * 3.2)))
    for i in range(13):
        r = 0.32 * (1 - i / 14) + 0.04
        parts.append(limb("T", pts[i], pts[i + 1], r, m))
        parts += [bpy.data.objects["T_a"], bpy.data.objects["T_b"]] if False else []
        if i % 2 == 0:
            parts.append(sphere("S", pts[i] + Vector((0.15, -r * 0.8, 0)), (r * 0.35, r * 0.2, r * 0.35), cups, 10, 6))
    join([o for o in bpy.context.scene.objects if o.type == "MESH"], "Tentacle")
    export(f"{OUT}/tentacle.glb")


if __name__ == "__main__":
    build_boat()
    build_fish()
    build_island("island_tropic", 7, 5.0, "tropic")
    build_island("island_pine", 11, 6.5, "pine")
    build_island("island_swamp", 23, 4.2, "swamp")
    build_buoy()
    build_tentacle()
