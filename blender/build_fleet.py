"""More HookLine models: two unlockable boats and a harbor lighthouse.

Run: bvenv/bin/python blender/build_fleet.py <out_dir>
Uses the helpers from build_models.py. Boats face +X; every boat has a Rod
node with a RodTip empty, and Coat / Hat materials the game recolors.
"""
import math
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import build_models as bm  # noqa: E402
from build_models import bpy, cube, cyl, join, limb, mat, smooth, sphere, torus  # noqa: E402
from mathutils import Vector  # noqa: E402

OUT = sys.argv[-1] if len(sys.argv) > 1 and not sys.argv[-1].endswith(".py") else "."


def half_hull(name, sx, sy, sz, z, m, pinch=0.45, start=0.3, flat=1.0):
    """Half an ellipsoid, hollowed, with a pinched bow."""
    hull = sphere(name, (0, 0, z), (sx, sy, sz), m, 44, 22)
    bpy.ops.object.mode_set(mode="EDIT")
    bme = bm.bmesh.from_edit_mesh(hull.data)
    bm.bmesh.ops.bisect_plane(bme, geom=bme.verts[:] + bme.edges[:] + bme.faces[:], plane_co=(0, 0, 0), plane_no=(0, 0, 1), clear_outer=True)
    bm.bmesh.update_edit_mesh(hull.data)
    bpy.ops.object.mode_set(mode="OBJECT")
    for v in hull.data.vertices:
        if v.co.x > start:
            v.co.y *= max(0.05, 1 - (v.co.x - start) * pinch)
        v.co.z *= flat
    hull.data.update()
    sol = hull.modifiers.new("solid", "SOLIDIFY")
    sol.thickness = 0.06
    sol.offset = -1
    return hull


def pinch_ring(obj, sx, start, pinch):
    for v in obj.data.vertices:
        if v.co.x * sx > start:
            v.co.y *= max(0.05, 1 - (v.co.x * sx - start) * pinch)
    obj.data.update()


def fisherman(x, z, coat, hat_col="#f6c431", beard_col="#d0662c"):
    """The HookLine fisherman sitting at (x, z), facing +X. Returns (parts, hands)."""
    skin = mat("Skin", "#f2bf98", 0.55)
    beard = mat("Beard", beard_col, 0.8)
    hat = mat("Hat", hat_col, 0.45)
    pants = mat("Pants", "#5b6a3e", 0.8)
    boot = mat("Boot", "#3b2c22", 0.6)
    black = mat("Black", "#141414", 0.3)
    coat_m = mat("Coat", coat, 0.65)
    parts = [
        sphere("Body", (x, 0, z + 0.42), (0.3, 0.32, 0.4), coat_m),
        sphere("Head", (x + 0.05, 0, z + 0.94), (0.21, 0.21, 0.22), skin),
        sphere("Nose", (x + 0.25, 0, z + 0.92), (0.06, 0.06, 0.06), mat("Nose", "#ec8f7a", 0.5)),
        sphere("Beard", (x + 0.19, 0, z + 0.76), (0.17, 0.2, 0.2), beard),
        cyl("Brim", (x + 0.05, 0, z + 1.08), 0.33, 0.03, hat, verts=32),
        cyl("Crown", (x + 0.04, 0, z + 1.19), 0.2, 0.22, hat, verts=32, r2=0.16),
    ]
    parts += [sphere(f"Eye{s}", (x + 0.23, s * 0.08, z + 1.0), (0.025, 0.025, 0.03), black, 10, 6) for s in (-1, 1)]
    hands = (x + 0.4, 0.0, z + 0.54)
    for i, s in enumerate((-1, 1)):
        for nm, a, b, r, m in ((f"Leg{i}", (x + 0.1, s * 0.13, z + 0.14), (x + 0.6, s * 0.15, z + 0.06), 0.085, pants),
                               (f"Shin{i}", (x + 0.6, s * 0.15, z + 0.06), (x + 0.65, s * 0.15, z - 0.24), 0.075, pants),
                               (f"Arm{i}", (x + 0.02, s * 0.27, z + 0.59), (hands[0] - 0.02, s * 0.07, hands[2]), 0.075, coat_m)):
            o = limb(nm, a, b, r, m)
            parts += [o, bpy.data.objects[nm + "_a"], bpy.data.objects[nm + "_b"]]
        parts.append(sphere(f"Boot{i}", (x + 0.71, s * 0.15, z - 0.26), (0.12, 0.08, 0.06), boot))
        parts.append(sphere(f"Hand{i}", (hands[0], s * 0.06, hands[2]), (0.07, 0.07, 0.07), skin))
    return parts, hands


def add_rod(hands, length=2.4, angle=52, color="#3a2a20"):
    metal = mat("Metal", "#9aa3ad", 0.35, 0.8)
    ang = math.radians(angle)
    d = Vector((math.sin(ang), 0, math.cos(ang)))
    butt = Vector(hands) + Vector((-0.12, 0, -0.08))
    rod = cyl("RodPole", butt + d * (length / 2), 0.028, length, mat("Rod", color, 0.4), verts=10, r2=0.008)
    rod.rotation_mode = "QUATERNION"
    rod.rotation_quaternion = Vector((0, 0, 1)).rotation_difference(d)
    reel = cyl("Reel", butt + d * 0.32 + Vector((0, -0.07, 0)), 0.065, 0.05, metal, rot=(math.radians(90), 0, 0), verts=16)
    rodobj = join([rod, reel], "Rod")
    bpy.ops.object.empty_add(type="PLAIN_AXES", location=butt + d * length)
    tip = bpy.context.active_object
    tip.name = "RodTip"
    tip.parent = rodobj
    tip.matrix_parent_inverse = rodobj.matrix_world.inverted()


# ---------------------------------------------------------------- speedboat: fast, sleek
def build_speedboat():
    bm.reset()
    white = mat("HullWhite", "#f4f6f8", 0.25)
    red = mat("Stripe", "#e3343c", 0.3)
    navy = mat("Deck", "#1e2f4a", 0.5)
    seat = mat("Seat", "#f0e6d2", 0.6)
    glass = mat("Glass", "#9fd9ff", 0.05)
    glass.blend_method = "BLEND" if hasattr(glass, "blend_method") else None
    glass.node_tree.nodes["Principled BSDF"].inputs["Alpha"].default_value = 0.45
    metal = mat("Metal", "#c9d1d9", 0.25, 0.9)
    motor = mat("Motor", "#111418", 0.35, 0.3)
    hull = half_hull("Hull", 2.15, 0.82, 0.5, 0.38, white, pinch=0.42, start=0.2)
    rim = torus("Rim", (0, 0, 0.38), 1, 0.05, red, (2.15, 0.81, 1))
    pinch_ring(rim, 2.15, 0.2, 0.42)
    band = torus("Band", (0, 0, 0.2), 1, 0.055, red, (2.05, 0.76, 1))
    pinch_ring(band, 2.05, 0.2, 0.42)
    deck = cube("Deck", (0.15, 0, 0.22), (1.55, 0.6, 0.03), navy, 0)
    console = cube("Console", (0.45, 0, 0.55), (0.22, 0.42, 0.25), white, 0.06)
    shield = cube("Windshield", (0.62, 0, 0.92), (0.03, 0.5, 0.16), glass, 0.02)
    shield.rotation_euler = (0, math.radians(-25), 0)
    wheel = torus("Wheel", (0.4, 0.15, 0.86), 0.1, 0.018, metal)
    wheel.rotation_euler = (0, math.radians(65), 0)
    bench = cube("Seat", (-0.45, 0, 0.45), (0.28, 0.6, 0.09), seat, 0.05)
    back = cube("SeatBack", (-0.7, 0, 0.68), (0.06, 0.6, 0.18), seat, 0.04)
    rail = [cyl(f"Rail{s}", (1.15, s * 0.5, 0.58), 0.025, 1.2, metal, rot=(0, math.radians(90), 0), verts=8) for s in (-1, 1)]
    m1 = cube("MotorHead", (-2.2, 0, 0.66), (0.24, 0.2, 0.24), motor, 0.06)
    m2 = cyl("MotorShaft", (-2.25, 0, 0.18), 0.05, 0.8, metal)
    flag = cube("Flag", (-1.9, 0.0, 1.3), (0.01, 0.22, 0.13), red, 0)
    pole = cyl("FlagPole", (-1.9, 0, 1.0), 0.015, 0.8, metal, verts=6)
    man, hands = fisherman(-0.45, 0.55, "#e3343c", "#ffffff", "#5a3a22")
    join([hull, rim, band, deck, console, shield, wheel, bench, back, *rail, m1, m2, flag, pole, *man], "Boat")
    add_rod(hands, 2.5, 50)
    bm.export(f"{OUT}/boat_speed.glb")


# ---------------------------------------------------------------- trawler: slow, big, a cabin
def build_trawler():
    bm.reset()
    hull_m = mat("HullBlue", "#2b5f9e", 0.45)
    red = mat("Stripe", "#d7463b", 0.5)
    deck_m = mat("Deck", "#b98a5a", 0.75)
    cabin_m = mat("Cabin", "#f3efe4", 0.55)
    roof = mat("Roof", "#d7463b", 0.5)
    win = mat("Window", "#1b2a3a", 0.1)
    metal = mat("Metal", "#9aa3ad", 0.35, 0.8)
    orange = mat("Float", "#ff8a2a", 0.4)
    hull = half_hull("Hull", 2.5, 1.05, 0.8, 0.55, hull_m, pinch=0.35, start=0.6)
    rim = torus("Rim", (0, 0, 0.55), 1, 0.06, red, (2.5, 1.04, 1))
    pinch_ring(rim, 2.5, 0.6, 0.35)
    deck = cube("Deck", (0, 0, 0.45), (2.0, 0.85, 0.04), deck_m, 0)
    cabin = cube("Cabin", (0.7, 0, 1.0), (0.55, 0.62, 0.5), cabin_m, 0.06)
    cab_roof = cube("CabinRoof", (0.7, 0, 1.55), (0.66, 0.72, 0.06), roof, 0.04)
    wins = [cube(f"Win{i}", (0.7 + dx, s * 0.63, 1.12), (0.14, 0.01, 0.13), win, 0) for i, (dx, s) in enumerate(((-0.25, 1), (0.2, 1), (-0.25, -1), (0.2, -1)))]
    front = cube("WinFront", (1.26, 0, 1.12), (0.01, 0.45, 0.14), win, 0)
    mast = cyl("Mast", (0.2, 0, 2.3), 0.05, 2.8, metal, verts=10)
    boom = cyl("Boom", (-0.6, 0, 2.6), 0.035, 1.8, metal, rot=(0, math.radians(70), 0), verts=8)
    ring = torus("LifeRing", (0.7, 0.65, 1.0), 0.2, 0.06, orange)
    ring.rotation_euler = (math.radians(90), 0, 0)
    floats = [sphere(f"Bob{i}", (-1.2 + i * 0.45, 0.95, 0.55), (0.13, 0.13, 0.13), orange, 12, 8) for i in range(4)]
    crates = [cube(f"Crate{i}", (-1.3 + i * 0.5, -0.45, 0.62), (0.18, 0.18, 0.15), deck_m, 0.03) for i in range(2)]
    man, hands = fisherman(-0.7, 0.75, "#2f8a4c", "#f6c431", "#9a9a9a")
    join([hull, rim, deck, cabin, cab_roof, *wins, front, mast, boom, ring, *floats, *crates, *man], "Boat")
    add_rod(hands, 2.6, 50)
    bm.export(f"{OUT}/boat_trawler.glb")


# ---------------------------------------------------------------- lighthouse on a rock
def build_lighthouse():
    bm.reset()
    white = mat("White", "#f6f3ec", 0.5)
    red = mat("Red", "#d9443a", 0.45)
    rock = mat("Rock", "#8f969c", 0.85)
    glass = mat("Lamp", "#ffe58a", 0.1, 0, emit="#ffd23f")
    metal = mat("Metal", "#2d333b", 0.4, 0.6)
    base = []
    for i, (dx, dy, r) in enumerate(((0, 0, 2.6), (1.4, 0.8, 1.4), (-1.5, 0.6, 1.2), (0.6, -1.5, 1.3))):
        bpy.ops.mesh.primitive_ico_sphere_add(subdivisions=2, radius=r, location=(dx, dy, -0.2))
        o = bpy.context.active_object
        o.scale = (1, 1, 0.55)
        bm.assign(o, rock)
        base.append(o)
    parts = list(base)
    h0 = 0.8
    for i in range(6):
        r1, r2 = 1.1 - i * 0.09, 1.1 - (i + 1) * 0.09
        parts.append(cyl(f"Seg{i}", (0, 0, h0 + i * 0.9 + 0.45), r1, 0.9, red if i % 2 else white, verts=32, r2=r2))
    top = h0 + 6 * 0.9
    parts.append(cyl("Gallery", (0, 0, top + 0.06), 0.85, 0.12, metal, verts=32))
    parts.append(torus("Railing", (0, 0, top + 0.4), 0.82, 0.025, metal))
    parts.append(cyl("LampRoom", (0, 0, top + 0.55), 0.48, 0.9, glass, verts=24))
    parts.append(cyl("Roof", (0, 0, top + 1.3), 0.6, 0.7, red, verts=24, r2=0.05))
    parts.append(sphere("Finial", (0, 0, top + 1.72), (0.09, 0.09, 0.09), metal, 10, 6))
    parts.append(cube("Door", (1.05, 0, h0 + 0.45), (0.05, 0.25, 0.4), metal, 0.02))
    join(parts, "Lighthouse")
    bm.export(f"{OUT}/lighthouse.glb")


if __name__ == "__main__":
    build_speedboat()
    build_trawler()
    build_lighthouse()
