import sys
ADDONS = "/home/user/.var.app/org.blender.Blender/config/blender/5.2/scripts/addons"
if ADDONS not in sys.path: sys.path.insert(0, ADDONS)
import bpy
import importlib
pkg = "anvil-level-design-main"
cm = importlib.import_module(pkg + ".core.materials")
cm.ensure_default_material()
mat = bpy.data.materials.get("Default")
img = bpy.data.images.load("/home/user/Documents/AnvilPlus/assets/grid.png", check_existing=True)
for prop in ["anvil_roughness_image", "anvil_normal_image", "anvil_displacement_image", "anvil_metallic_image"]:
    try:
        setattr(mat, prop, img)
        print(prop, "OK")
    except Exception as e:
        import traceback; traceback.print_exc()
        print(prop, "ERR:", e)
