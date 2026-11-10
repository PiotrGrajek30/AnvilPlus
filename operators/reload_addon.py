"""Developer operator: reload the add-on from disk."""

import bpy


class LEVELDESIGN_OT_reload_addon(bpy.types.Operator):
    bl_idname = "leveldesign.reload_addon"
    bl_label = "Reload Addon"
    bl_description = (
        "Re-register Anvil from its source files without restarting Blender "
        "(development only)"
    )

    def execute(self, context):
        from ..core import dev_reload

        dev_reload.request_reload()
        self.report({'INFO'}, "Reloading Anvil...")
        return {'FINISHED'}


classes = (LEVELDESIGN_OT_reload_addon,)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
