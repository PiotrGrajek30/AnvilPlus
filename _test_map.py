import sys
ADDONS = "/home/user/.var.app/org.blender.Blender/config/blender/5.2/scripts/addons"
if ADDONS not in sys.path: sys.path.insert(0, ADDONS)
import bpy
import importlib
pkg = "anvil-level-design-main"
cm = importlib.import_module(pkg + ".core.materials")
ms = importlib.import_module(pkg + ".core.material_shader")
cm.ensure_default_material()
mat = bpy.data.materials.get("Default")
# create an image from file if possible
img_path = "/home/user/Documents/AnvilPlus/assets/grid.png"
img = bpy.data.images.load(img_path, check_existing=True)
print("before set")
try:
    mat.anvil_primary_image = img
    print("set OK")
except Exception as e:
    import traceback; traceback.print_exc()
    print("ERR:", e)
print("schema:", mat.anvil_material_schema_version)
