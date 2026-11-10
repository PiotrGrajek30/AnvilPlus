import sys
ADDONS = "/home/user/.var/app/org.blender.Blender/config/blender/5.2/scripts/addons"
if ADDONS not in sys.path: sys.path.insert(0, ADDONS)
import bpy
import importlib
pkg = "anvil-level-design-main"
props_mod = importlib.import_module(pkg + ".properties")
core_mat = importlib.import_module(pkg + ".core.materials")
ms = importlib.import_module(pkg + ".core.material_shader")
mat = bpy.data.materials.new("TestMat2")
img = bpy.data.images.new("TestImg2", 4, 4)
core_mat.set_material_primary_image(mat, img)
scene_props = bpy.context.scene.level_design_props
settings = {
    "interpolation": scene_props.default_interpolation,
    "texture_as_alpha": scene_props.default_texture_as_alpha,
    "vertex_colors": scene_props.default_vertex_colors,
    "roughness": 0.5,
    "metallic": 0.0,
    "emission_strength": scene_props.default_emission_strength,
    "emission_color": scene_props.default_emission_color,
    "specular": scene_props.default_specular,
}
ms.build_canonical_material_shader(mat, img, settings)
print("before roughness:", [n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED'][0].inputs['Roughness'].default_value)
mat.anvil_roughness = 0.9
print("after roughness:", [n for n in mat.node_tree.nodes if n.type=='BSDF_PRINCIPLED'][0].inputs['Roughness'].default_value)
print("schema version:", mat.anvil_material_schema_version)
