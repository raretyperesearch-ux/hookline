"""Render v2 models one by one (Cycles CPU, transparent film, AgX) for the contact sheets and icons.

Run: bvenv/bin/python blender/render_v2.py <models_dir> <out_dir> <res> <samples> name [name ...]
"""
import math
import os
import sys

import bpy
from mathutils import Vector

args = sys.argv[sys.argv.index("--") + 1:] if "--" in sys.argv else sys.argv[1:]
MODELS, OUT, RES, SAMPLES = args[0], args[1], int(args[2]), int(args[3])
NAMES = args[4:]
os.makedirs(OUT, exist_ok=True)


def setup():
    bpy.ops.wm.read_factory_settings(use_empty=True)
    sc = bpy.context.scene
    sc.render.engine = "CYCLES"
    sc.cycles.device = "CPU"
    sc.cycles.samples = SAMPLES
    sc.cycles.use_denoising = True
    sc.render.resolution_x = sc.render.resolution_y = RES
    sc.render.film_transparent = True
    sc.view_settings.view_transform = "AgX"
    try:
        sc.view_settings.look = "AgX - Punchy"
    except Exception:
        pass
    w = bpy.data.worlds.new("W")
    sc.world = w
    w.use_nodes = True
    bg = w.node_tree.nodes["Background"]
    bg.inputs[0].default_value = (0.78, 0.9, 1.0, 1)
    bg.inputs[1].default_value = 1.0
    for nm, rot, e in (("Key", (48, 0, 40), 3.6), ("Rim", (60, 0, 200), 1.6)):
        d = bpy.data.lights.new(nm, "SUN")
        d.energy = e
        d.angle = math.radians(8)
        o = bpy.data.objects.new(nm, d)
        sc.collection.objects.link(o)
        o.rotation_euler = [math.radians(a) for a in rot]
    return sc


def frame(objs, view_dir, lens=50, margin=1.08):
    pts = []
    for o in objs:
        if o.type == "MESH":
            pts += [o.matrix_world @ Vector(c) for c in o.bound_box]
    lo = Vector((min(p.x for p in pts), min(p.y for p in pts), min(p.z for p in pts)))
    hi = Vector((max(p.x for p in pts), max(p.y for p in pts), max(p.z for p in pts)))
    c = (lo + hi) / 2
    r = max((p - c).length for p in pts)
    cam = bpy.data.cameras.new("C")
    cam.lens = lens
    fov = 2 * math.atan(18 / lens)
    dist = r / math.sin(fov / 2) * margin
    o = bpy.data.objects.new("C", cam)
    bpy.context.scene.collection.objects.link(o)
    d = Vector(view_dir).normalized()
    o.location = c + d * dist
    o.rotation_euler = (-d).to_track_quat("-Z", "Y").to_euler()
    bpy.context.scene.camera = o


for n in NAMES:
    sc = setup()
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(MODELS, n + ".glb"))
    new = [o for o in bpy.data.objects if o not in before]
    if os.environ.get("WAG"):
        for o in new:
            if o.name.startswith("Tail"):
                o.rotation_mode = "XYZ"
                o.rotation_euler.z += math.radians(float(os.environ["WAG"]))
            if o.name.startswith("FinL") or o.name.startswith("FinR"):
                o.rotation_mode = "XYZ"
                o.rotation_euler.x += math.radians(25)
            if o.name.startswith("Rod") and not o.name.startswith("RodTip"):
                o.rotation_mode = "XYZ"
                o.rotation_euler.y += math.radians(float(os.environ["WAG"]))
    bpy.context.view_layer.update()
    if n.startswith("boat"):
        view = (0.85, -1.0, 0.62)
    elif n in ("fish_jelly",):
        view = (0.9, -1.0, 0.25)
    else:
        view = (0.55, -1.0, 0.38)
    if os.environ.get("VIEW"):
        view = tuple(float(v) for v in os.environ["VIEW"].split(","))
    frame(new, view, margin=float(os.environ.get("MARGIN", "1.0")))
    sc.render.filepath = os.path.join(OUT, n + os.environ.get("SUFFIX", "") + ".png")
    bpy.ops.render.render(write_still=True)
    print("rendered", n)
