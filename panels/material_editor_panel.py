"""Material editor panel."""

import bpy
from bpy.types import Panel

from ..core.workspace_check import is_level_design_workspace
from ..operators import material_tools


_panel_last_material_count = -1

def _panel_debug(msg):
    material_tools._debug(f"[panel] {msg}")


class LEVELDESIGN_PT_material_editor_panel(Panel):
    bl_label = "Material Editor"
    bl_idname = "LEVELDESIGN_PT_material_editor_panel"
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_category = 'Anvil Meshes'
    bl_order = 5

    @classmethod
    def poll(cls, context):
        return is_level_design_workspace()

    def draw(self, context):
        global _panel_last_material_count
        layout = self.layout
        scene = context.scene
        mats = bpy.data.materials
        if len(mats) != _panel_last_material_count:
            _panel_debug(f"draw materials={[m.name for m in mats]} active_idx={scene.anvil_active_material_index}")
            _panel_last_material_count = len(mats)
        idx = scene.anvil_active_material_index
        if mats and idx >= len(mats):
            idx = 0
        for mat in mats:
            try:
                _ = mat.preview.icon_id
            except Exception:
                pass

        layout.prop(scene, "anvil_material_list_filter", text="", icon='VIEWZOOM')

        layout.template_list(
            "LEVELDESIGN_UL_anvil_materials",
            "",
            bpy.data,
            "materials",
            scene,
            "anvil_active_material_index",
            rows=8,
        )

        row = layout.row(align=True)
        row.operator("leveldesign.add_anvil_material", text="Add", icon='ADD')
        row.operator("leveldesign.delete_anvil_material", text="Delete", icon='REMOVE')
        row.operator("leveldesign.cleanup_unused_materials", text="Clean", icon='TRASH')

        if mats:
            mat = mats[idx]
            apply_row = layout.row()
            apply_op = apply_row.operator("leveldesign.apply_anvil_material", text="Apply Material", icon='CHECKMARK')
            apply_op.material_name = mat.name

        if not mats:
            layout.label(text="No materials", icon='INFO')
            return

        mat = mats[idx]
        layout.separator()
        layout.prop(mat, "name")
        row = layout.row(align=True)
        row.prop(mat, "anvil_base_color", text="Base Color")
        cl = row.operator("leveldesign.clear_material_map", text="", icon='X')
        cl.map_type = 'base_color'
        layout.separator()

        def _map_row(label, map_type, image):
            row = layout.row(align=True)
            current = getattr(image, "name", None) if image is not None else None
            row.label(text=f"{label}: {current if current else 'None'}")
            op = row.operator("leveldesign.load_material_map", text="Load", icon='FILEBROWSER')
            op.map_type = map_type
            cl = row.operator("leveldesign.clear_material_map", text="", icon='X')
            cl.map_type = map_type

        _map_row("Color Map", "primary", mat.anvil_primary_image)
        _map_row("Roughness Map", "roughness", mat.anvil_roughness_image)
        _map_row("Normal Map", "normal", mat.anvil_normal_image)
        _map_row("Displacement Map", "displacement", mat.anvil_displacement_image)
        _map_row("Metallic Map", "metallic", mat.anvil_metallic_image)
        layout.separator()
        layout.prop(mat, "anvil_uv_scale", text="UV Scale")
        layout.prop(mat, "anvil_interpolation", text="Filtering")
        layout.prop(mat, "anvil_roughness", text="Roughness")
        layout.prop(mat, "anvil_metallic", text="Metallic")
        layout.prop(context.scene.level_design_props, "default_auto_alpha", text="Auto Alpha")


def register():
    bpy.utils.register_class(LEVELDESIGN_PT_material_editor_panel)


def unregister():
    bpy.utils.unregister_class(LEVELDESIGN_PT_material_editor_panel)
