"""Contact sheet for the models3 environment set (Cycles CPU).

Run: bvenv/bin/python blender/render_env3.py <models_dir> <out_png> [panel names...] [--samples N]
Imports each .glb (so what we see is what the game gets), puts it on a blue water plane,
frames a 3/4 view automatically, then tiles the panels plus a wide island-cluster shot.
"""
import math
import os
import sys

import bpy
import numpy as np
from mathutils import Vector

argv = sys.argv[1:]
SAMPLES = 64
if "--samples" in argv:
    i = argv.index("--samples")
    SAMPLES = int(argv[i + 1])
    del argv[i:i + 2]
argv = [a for a in argv if not a.endswith(".py")]
MODELS, OUT = argv[0], argv[1]
PANELS = argv[2:] or ["island_tropic", "island_pine", "island_swamp", "lighthouse", "dock", "buoy", "tentacle", "cluster"]
PW, PH = 640, 480
TMP = os.path.join(os.path.dirname(OUT), "env3_panels")
os.makedirs(TMP, exist_ok=True)


def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)


def setup(w, h):
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = SAMPLES
    sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = w, h
    sc.render.film_transparent = False
    sc.view_settings.view_transform = "AgX"
    looks = [i.identifier for i in sc.view_settings.bl_rna.properties["look"].enum_items]
    sc.view_settings.look = "AgX - Punchy" if "AgX - Punchy" in looks else "None"
    w_ = bpy.data.worlds.new("W")
    sc.world = w_
    w_.use_nodes = True
    nt = w_.node_tree
    bg = nt.nodes["Background"]
    bg.inputs[0].default_value = (0.55, 0.78, 1.0, 1)
    bg.inputs[1].default_value = 0.9
    d = bpy.data.lights.new("Sun", "SUN")
    d.energy = 4.0
    d.angle = math.radians(5)
    d.color = (1.0, 0.95, 0.85)
    o = bpy.data.objects.new("Sun", d)
    sc.collection.objects.link(o)
    o.rotation_euler = (math.radians(48), math.radians(8), math.radians(35))


def water(size=3000):
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, 0))
    o = bpy.context.active_object
    m = bpy.data.materials.new("Water")
    try:
        m.use_nodes = True
    except Exception:
        pass
    b = m.node_tree.nodes["Principled BSDF"]
    b.inputs["Base Color"].default_value = (0.02, 0.28, 0.45, 1)
    b.inputs["Roughness"].default_value = 0.12
    b.inputs["Alpha"].default_value = 0.72
    o.data.materials.append(m)
    # opaque deep floor so we don't see the sky through the water
    bpy.ops.mesh.primitive_plane_add(size=size, location=(0, 0, -2.2))
    f = bpy.context.active_object
    fm = bpy.data.materials.new("Floor")
    fm.node_tree.nodes["Principled BSDF"].inputs["Base Color"].default_value = (0.01, 0.12, 0.2, 1)
    f.data.materials.append(fm)


def imp(name, loc=(0, 0, 0), rotz=0.0, s=1.0):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(MODELS, name + ".glb"))
    new = [o for o in bpy.data.objects if o not in before]
    for r in [o for o in new if o.parent is None]:
        r.location = Vector(loc)
        r.rotation_mode = "XYZ"
        r.rotation_euler = (0, 0, rotz)
        r.scale = r.scale * s
    bpy.context.view_layer.update()
    return new


def bbox(objs):
    mn = Vector((1e9,) * 3)
    mx = Vector((-1e9,) * 3)
    for o in objs:
        if o.type != "MESH":
            continue
        for c in o.bound_box:
            w = o.matrix_world @ Vector(c)
            mn = Vector(map(min, mn, w))
            mx = Vector(map(max, mx, w))
    return mn, mx


def cam(loc, target, lens=50):
    c = bpy.data.cameras.new("C")
    c.lens = lens
    c.clip_end = 1000
    o = bpy.data.objects.new("C", c)
    bpy.context.scene.collection.objects.link(o)
    o.location = Vector(loc)
    o.rotation_euler = (Vector(target) - o.location).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = o


def frame(objs, direction=(1.0, -1.35, 0.75), lens=50, pad=1.08, zmin=-0.3):
    mn, mx = bbox(objs)
    mn.z = max(mn.z, zmin)
    c = (mn + mx) / 2
    r = (mx - mn).length / 2
    fov = 2 * math.atan(36 / 2 / lens) * (PH / PW)
    dist = r / math.sin(fov / 2) * pad * 0.8
    d = Vector(direction).normalized()
    cam(c + d * dist, c, lens)


def render(path):
    bpy.context.scene.render.filepath = path
    bpy.ops.render.render(write_still=True)


def panel(name):
    reset()
    setup(PW, PH)
    water()
    if name == "cluster":
        objs = []
        objs += imp("island_pine", (-14, -6, 0), 0.3)
        objs += imp("island_tropic", (8, -10, 0), 2.1)
        objs += imp("island_swamp", (0, 12, 0), 1.2)
        objs += imp("lighthouse", (-6, 9, 0), 0.4, 0.72)
        objs += imp("dock", (8, 6, 0), 0.5)
        for k, (x, y) in enumerate(((2, -2), (-3, 2.5), (12, 0))):
            objs += imp("buoy", (x, y, 0), k)
        cam((34, -42, 30), (-1, 1, 0), 40)
    else:
        objs = imp(name, rotz=math.radians(-20))
        if name == "tentacle":
            frame(objs, (1.0, -1.6, 0.35), pad=1.1, zmin=-0.5)
        elif name == "buoy":
            frame(objs, (1.0, -1.5, 0.55), pad=1.05)
        elif name == "dock":
            frame(objs, (0.8, -1.4, 0.8), pad=1.0)
        elif name == "lighthouse":
            frame(objs, (1.0, -1.4, 0.55), pad=1.05)
        else:
            frame(objs, (1.0, -1.3, 0.8), pad=0.62)
    p = os.path.join(TMP, name + ".png")
    render(p)
    return p


def load_px(path):
    im = bpy.data.images.load(path)
    a = np.array(im.pixels[:], dtype=np.float32).reshape(im.size[1], im.size[0], 4)
    bpy.data.images.remove(im)
    return a


paths = [panel(n) for n in PANELS]
cols = 4 if len(paths) > 3 else len(paths)
rows = math.ceil(len(paths) / cols)
sheet = np.ones((rows * PH, cols * PW, 4), dtype=np.float32)
for i, p in enumerate(paths):
    r, c = divmod(i, cols)
    a = load_px(p)
    y0 = (rows - 1 - r) * PH   # blender pixel rows start at the bottom
    sheet[y0:y0 + PH, c * PW:(c + 1) * PW] = a
img = bpy.data.images.new("sheet", cols * PW, rows * PH, alpha=True)
img.pixels = sheet.ravel()
img.filepath_raw = OUT
img.file_format = "PNG"
img.save()
print("SHEET", OUT)
