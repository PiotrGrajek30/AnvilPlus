import sys
ADDONS = "/home/user/.var/app/org.blender.Blender/config/blender/5.2/scripts/addons"
if ADDONS not in sys.path: sys.path.insert(0, ADDONS)
import bpy
import importlib
pkg = "anvil-level-design-main"
ms = importlib.import_module(pkg + ".core.material_shader")
core_mat = importlib.import_module(pkg + ".core.materials")
# create material
mat = bpy.data.materials.new("TestMat")
img = bpy.data.images.new("TestImg", 4, 4)
core_mat.set_material_primary_image(mat, img)
props = bpy.context.scene.level_design_props
settings = {
    "interpolation": props.default_interpolation,
    "texture_as_alpha": props.default_texture_as_alpha,
    "vertex_colors": props.default_vertex_colors,
    "roughness": 0.5,
    "metallic": 0.0,
    "emission_strength": props.default_emission_strength,
    "emission_color": props.default_emission_color,
    "specular": props.default_specular,
}
ms.build_canonical_material_shader(mat, img, settings)
# print bsdf inputs
for node in mat.node_tree.nodes:
    if node.type == 'BSDF_PRINCIPLED':
        print("BSDF inputs:", [i.name for i in node.inputs])
    if node.type == 'TEX_IMAGE':
        print("TEX interpolation:", node.interpolation)
print("DONE")
