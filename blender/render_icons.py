import bpy, sys, math, os
from mathutils import Vector
MODELS, OUT = sys.argv[-2], sys.argv[-1]
def reset():
    bpy.ops.wm.read_factory_settings(use_empty=True)
def setup(res_x, res_y, samples=48, transparent=True):
    sc = bpy.context.scene
    sc.render.engine = 'CYCLES'; sc.cycles.device = 'CPU'; sc.cycles.samples = samples
    sc.cycles.use_denoising = True
    sc.render.resolution_x, sc.render.resolution_y = res_x, res_y
    sc.render.film_transparent = transparent
    sc.view_settings.view_transform = 'AgX'
    sc.view_settings.look = 'AgX - Punchy' if 'AgX - Punchy' in [i.identifier for i in sc.view_settings.bl_rna.properties['look'].enum_items] else 'None'
    w = bpy.data.worlds.new('W'); sc.world = w; w.use_nodes = True
    bg = w.node_tree.nodes['Background']; bg.inputs[0].default_value = (0.75, 0.9, 1.0, 1); bg.inputs[1].default_value = 0.9
    return sc
def sun(rot=(50, 10, 35), e=4.0):
    d = bpy.data.lights.new('Sun', 'SUN'); d.energy = e; d.angle = math.radians(6)
    o = bpy.data.objects.new('Sun', d); bpy.context.collection.objects.link(o); o.rotation_euler = [math.radians(a) for a in rot]
def imp(name, loc=(0,0,0), rotz=0, s=1.0):
    before = set(bpy.data.objects)
    bpy.ops.import_scene.gltf(filepath=os.path.join(MODELS, name + '.glb'))
    new = [o for o in bpy.data.objects if o not in before]
    roots = [o for o in new if o.parent is None]
    for r in roots: r.location = Vector(loc); r.rotation_mode = 'XYZ'; r.rotation_euler = (0, 0, rotz); r.scale = r.scale * s
    return new
def cam(loc, target, lens=50):
    c = bpy.data.cameras.new('C'); c.lens = lens; o = bpy.data.objects.new('C', c); bpy.context.collection.objects.link(o)
    o.location = Vector(loc); d = Vector(target) - o.location; o.rotation_euler = d.to_track_quat('-Z', 'Y').to_euler()
    bpy.context.scene.camera = o
for n in ['boat', 'boat_speed', 'boat_trawler', 'lighthouse']:
    reset(); setup(512, 512)
    sun()
    imp(n, rotz=math.radians(-20))
    if n == 'lighthouse': cam((14, -22, 14), (0, 0, 6.5), 50)
    elif n == 'boat_trawler': cam((6.6, -7.9, 5.6), (0, 0, 1.5), 50)
    else: cam((5.4, -6.4, 4.4), (0, 0, 0.8), 50)
    bpy.context.scene.render.filepath = os.path.join(OUT, f'icon_{n}.png')
    bpy.ops.render.render(write_still=True)
    print('done', n)
