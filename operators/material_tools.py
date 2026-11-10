import bpy
import os
import time
from bpy.types import Operator, UIList

_DEBUG_LOG = os.path.join(os.path.dirname(os.path.dirname(__file__)), "debug_material.log")

def _debug(msg):
    try:
        with open(_DEBUG_LOG, "a") as f:
            f.write(f"{time.strftime('%Y-%m-%d %H:%M:%S')} {msg}\n")
    except Exception:
        pass

from ..core.materials import (
    MATERIAL_SCHEMA_VERSION,
    MaterialMappingConflictError,
    find_material_with_image,
    get_primary_image_from_material,
    get_texture_node_from_material,
    get_principled_bsdf_from_material,
    is_texture_alpha_connected,
    is_vertex_colors_enabled,
    remove_unused_nodes,
    set_material_primary_image,
    repair_material_shader,
)
from ..core.logging import (
    add_performance_detail,
    begin_performance_operation_report,
    finish_performance_operation_report,
    performance_stage,
)
from ..core.material_shader import build_canonical_material_shader
from ..handlers import get_active_image
from ..core.workspace_check import (
    is_hotspot_mapping_workspace,
    is_level_design_workspace,
)


def get_used_material_indices(obj):
    """Return set of material indices actually used by faces."""
    if obj.type != 'MESH' or not obj.data:
        return set()

    used_indices = set()
    for poly in obj.data.polygons:
        used_indices.add(poly.material_index)
    return used_indices


def cleanup_unused_material_slots(obj):
    """Remove material slots not used by any face. Returns count removed."""
    if obj.type != 'MESH' or not obj.data:
        return 0

    removed = 0
    used_indices = get_used_material_indices(obj)

    # Work backwards to avoid index shifting issues
    for i in range(len(obj.material_slots) - 1, -1, -1):
        if i not in used_indices:
            obj.active_material_index = i
            with bpy.context.temp_override(object=obj, active_object=obj):
                bpy.ops.object.material_slot_remove()
            # Recalculate used indices after removal (they shift down)
            used_indices = get_used_material_indices(obj)
            removed += 1

    return removed


def get_material_images():
    """Return images referenced by image texture nodes in materials."""
    images = set()
    for material in bpy.data.materials:
        if not material.use_nodes or not material.node_tree:
            continue
        for node in material.node_tree.nodes:
            if node.type == 'TEX_IMAGE' and node.image is not None:
                images.add(node.image)
    return images


def _active_mapped_material():
    try:
        return find_material_with_image(get_active_image())
    except MaterialMappingConflictError:
        return None


class LEVELDESIGN_OT_set_interpolation_closest(Operator):
    """Setting interpolation of image texture to closest"""

    bl_idname = "leveldesign.set_interpolation_closest"
    bl_label = "Set interpolation to closest"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mat = _active_mapped_material()
        tex = get_texture_node_from_material(mat)
        if tex:
            tex.interpolation = 'Closest'
        return {'FINISHED'}


class LEVELDESIGN_OT_set_interpolation_linear(Operator):
    """Setting interpolation of image texture to linear"""

    bl_idname = "leveldesign.set_interpolation_linear"
    bl_label = "Set interpolation to linear"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        mat = _active_mapped_material()
        tex = get_texture_node_from_material(mat)
        if tex:
            tex.interpolation = 'Linear'
        return {'FINISHED'}


class LEVELDESIGN_OT_toggle_texture_alpha(Operator):
    """Toggle connecting texture alpha to material alpha"""

    bl_idname = "leveldesign.toggle_texture_alpha"
    bl_label = "Toggle Texture Alpha"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_mapped_material() is not None

    def execute(self, context):
        mat = _active_mapped_material()
        tex = get_texture_node_from_material(mat)
        bsdf = get_principled_bsdf_from_material(mat)

        if not tex or not bsdf:
            self.report({'WARNING'}, "Material missing texture or BSDF node")
            return {'CANCELLED'}

        nt = mat.node_tree

        if is_texture_alpha_connected(mat):
            # Disconnect alpha
            for link in list(nt.links):
                if (
                    link.from_node == tex
                    and link.from_socket.name == "Alpha"
                    and link.to_node == bsdf
                    and link.to_socket.name == "Alpha"
                ):
                    nt.links.remove(link)
            mat.blend_method = 'OPAQUE'
        else:
            # Connect alpha
            nt.links.new(tex.outputs["Alpha"], bsdf.inputs["Alpha"])
            mat.blend_method = 'CLIP'

        return {'FINISHED'}


class LEVELDESIGN_OT_toggle_vertex_colors(Operator):
    """Toggle vertex color multiply on the material"""

    bl_idname = "leveldesign.toggle_vertex_colors"
    bl_label = "Toggle Vertex Colors"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        return _active_mapped_material() is not None

    def execute(self, context):
        mat = _active_mapped_material()
        tex = get_texture_node_from_material(mat)
        bsdf = get_principled_bsdf_from_material(mat)

        if not tex or not bsdf:
            self.report({'WARNING'}, "Material missing texture or BSDF node")
            return {'CANCELLED'}

        nt = mat.node_tree

        if is_vertex_colors_enabled(mat):
            # Disable: reconnect tex Color directly to BSDF Base Color
            # Remove any existing link into Base Color first
            for link in list(nt.links):
                if link.to_node == bsdf and link.to_socket.name == "Base Color":
                    nt.links.remove(link)

            nt.links.new(tex.outputs["Color"], bsdf.inputs["Base Color"])
            remove_unused_nodes(mat)
        else:
            # Enable: insert Multiply node between tex and BSDF
            # Remove existing tex Color -> BSDF Base Color link
            for link in list(nt.links):
                if (
                    link.from_node == tex
                    and link.from_socket.name == "Color"
                    and link.to_node == bsdf
                    and link.to_socket.name == "Base Color"
                ):
                    nt.links.remove(link)

            mix = nt.nodes.new("ShaderNodeMix")
            mix.data_type = 'RGBA'
            mix.blend_type = 'MULTIPLY'
            mix.clamp_result = True
            mix.inputs["Factor"].default_value = 1.0
            mix.location = (
                (tex.location[0] + bsdf.location[0]) / 2,
                tex.location[1] + 200,
            )

            vc = nt.nodes.new("ShaderNodeVertexColor")
            vc.location = (tex.location[0], tex.location[1] - 200)

            nt.links.new(tex.outputs["Color"], mix.inputs[6])
            nt.links.new(vc.outputs["Color"], mix.inputs[7])
            nt.links.new(mix.outputs[2], bsdf.inputs["Base Color"])

        return {'FINISHED'}


class LEVELDESIGN_OT_fix_alpha_bleed(Operator):
    """Set RGB of transparent pixels to a color to fix edge bleeding"""

    bl_idname = "leveldesign.fix_alpha_bleed"
    bl_label = "Fix Alpha Bleed"
    bl_options = {'REGISTER', 'UNDO'}

    color: bpy.props.FloatVectorProperty(
        name="Fill Color",
        subtype='COLOR',
        default=(0.0, 0.0, 0.0),
        min=0.0,
        max=1.0,
        description="Color to set transparent pixels to",
    )

    alpha_threshold: bpy.props.FloatProperty(
        name="Alpha Threshold",
        default=0.01,
        min=0.0,
        max=1.0,
        description="Pixels with alpha below this value will be modified",
    )

    @classmethod
    def poll(cls, context):
        image = get_active_image()
        return image is not None

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self)

    def execute(self, context):
        image = get_active_image()

        if image.packed_file:
            image.unpack(method='USE_ORIGINAL')

        width, height = image.size
        pixels = list(image.pixels[:])

        modified_count = 0
        for i in range(0, len(pixels), 4):
            alpha = pixels[i + 3]
            if alpha < self.alpha_threshold:
                pixels[i] = self.color[0]  # R
                pixels[i + 1] = self.color[1]  # G
                pixels[i + 2] = self.color[2]  # B
                modified_count += 1

        image.pixels[:] = pixels
        image.update()

        if image.filepath:
            image.save()
            self.report(
                {'INFO'},
                f"Fixed {modified_count} pixels and saved to {image.filepath}",
            )
        else:
            self.report(
                {'WARNING'},
                f"Fixed {modified_count} pixels but image has no filepath - pack or save manually",
            )

        return {'FINISHED'}


class LEVELDESIGN_OT_reload_material_images(Operator):
    """Reload external images referenced by materials"""

    bl_idname = "leveldesign.reload_material_images"
    bl_label = "Reload Material Images"
    bl_description = "Reload unpacked, unchanged external images referenced by materials"
    bl_options = {'REGISTER'}

    @classmethod
    def poll(cls, context):
        return is_level_design_workspace()

    def execute(self, context):
        reloaded_count = 0
        dirty_count = 0
        packed_count = 0
        failed_count = 0
        no_filepath_count = 0
        performance_report = begin_performance_operation_report(
            "Reload Material Images",
            "material image discovery, synchronous image reloads, and redraw tagging",
        )

        try:
            with performance_stage(performance_report, "Discover material images"):
                material_images = sorted(
                    get_material_images(),
                    key=lambda image: image.name.lower(),
                )

            add_performance_detail(
                performance_report,
                "Material images found",
                len(material_images),
            )

            with performance_stage(performance_report, "Reload material images"):
                for image in material_images:
                    if not image.filepath:
                        no_filepath_count += 1
                        continue
                    if image.packed_file:
                        packed_count += 1
                        continue
                    if image.is_dirty:
                        dirty_count += 1
                        continue

                    try:
                        image.reload()
                        reloaded_count += 1
                    except RuntimeError:
                        failed_count += 1

            with performance_stage(performance_report, "Tag editor redraws"):
                for window in context.window_manager.windows:
                    for area in window.screen.areas:
                        if area.type in {'VIEW_3D', 'IMAGE_EDITOR', 'NODE_EDITOR'}:
                            area.tag_redraw()
        finally:
            add_performance_detail(performance_report, "Images reloaded", reloaded_count)
            add_performance_detail(performance_report, "Dirty images skipped", dirty_count)
            add_performance_detail(performance_report, "Packed images skipped", packed_count)
            add_performance_detail(
                performance_report,
                "Images without filepaths skipped",
                no_filepath_count,
            )
            add_performance_detail(performance_report, "Failed reloads", failed_count)
            finish_performance_operation_report(performance_report)

        if reloaded_count == 0 and failed_count == 0:
            self.report({'INFO'}, "No material images to reload")
        elif failed_count > 0:
            self.report(
                {'WARNING'},
                f"Reloaded {reloaded_count} image(s), {failed_count} failed",
            )
        else:
            self.report({'INFO'}, f"Reloaded {reloaded_count} image(s)")

        if dirty_count > 0 or packed_count > 0:
            print(
                "Anvil Level Design: Skipped material image reload for "
                f"{dirty_count} dirty image(s), {packed_count} packed image(s)",
                flush=True,
            )

        return {'FINISHED'}


class LEVELDESIGN_OT_material_primary_image_warning(Operator):
    """Explain that the mapped primary image is absent from this shader"""

    bl_idname = "leveldesign.material_primary_image_warning"
    bl_label = "Primary Image Not Used by Shader"
    bl_description = (
        "This shader contains image textures, but none uses the material's "
        "mapped primary image. Repairing will rebuild the graph with the primary image"
    )
    bl_options = {'REGISTER'}

    def execute(self, context):
        self.report(
            {'WARNING'},
            "Shader image textures do not include the mapped primary image",
        )
        return {'FINISHED'}


class LEVELDESIGN_OT_repair_material_shader(Operator):
    """Replace a customized shader with Anvil's canonical node graph"""

    bl_idname = "leveldesign.repair_material_shader"
    bl_label = "Repair Material Shader"
    bl_description = (
        "Rebuild this material using Anvil's canonical shader while preserving "
        "supported material values"
    )
    bl_options = {'REGISTER'}

    material_name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        return is_level_design_workspace()

    def draw(self, context):
        layout = self.layout
        layout.label(text="Replace this material's entire node graph?", icon='ERROR')
        layout.label(text="Custom nodes and connections will be removed.")
        layout.label(text="Supported material values and the primary image are preserved.")

    def invoke(self, context, event):
        return context.window_manager.invoke_props_dialog(self, width=440)

    def execute(self, context):
        material = bpy.data.materials.get(self.material_name)
        if material is None:
            self.report({'ERROR'}, "Material was not found")
            return {'CANCELLED'}
        try:
            result = repair_material_shader(material)
        except (RuntimeError, TypeError, ValueError) as exc:
            self.report({'ERROR'}, f"Could not repair material: {exc}")
            return {'CANCELLED'}
        if not result.is_canonical:
            self.report({'ERROR'}, "Rebuilt shader did not validate")
            return {'CANCELLED'}
        bpy.ops.ed.undo_push(message="Repair Material Shader")
        for window in context.window_manager.windows:
            for area in window.screen.areas:
                if area.type in {'VIEW_3D', 'NODE_EDITOR'}:
                    area.tag_redraw()
        self.report({'INFO'}, f"Repaired material {material.name}")
        return {'FINISHED'}


class LEVELDESIGN_OT_set_default_interpolation(Operator):
    """Set the default interpolation mode for new materials"""

    bl_idname = "leveldesign.set_default_interpolation"
    bl_label = "Set Default Interpolation"

    interpolation: bpy.props.StringProperty()

    def execute(self, context):
        context.scene.level_design_props.default_interpolation = self.interpolation
        return {'FINISHED'}


class LEVELDESIGN_OT_cleanup_unused_materials(Operator):
    """Remove unused materials managed by Anvil"""

    bl_idname = "leveldesign.cleanup_unused_materials"
    bl_label = "Cleanup Unused Materials"
    bl_options = {'REGISTER', 'UNDO'}

    def execute(self, context):
        slots_removed = 0
        materials_removed = 0

        # First, clean up unused material slots from all mesh objects
        for obj in bpy.data.objects:
            if obj.type == 'MESH':
                slots_removed += cleanup_unused_material_slots(obj)

        # Then remove materials with no users
        for mat in list(bpy.data.materials):
            is_managed = (
                getattr(mat, "anvil_material_schema_version", 0)
                == MATERIAL_SCHEMA_VERSION
                and get_primary_image_from_material(mat) is not None
            )
            if is_managed and mat.users == 0:
                bpy.data.materials.remove(mat)
                materials_removed += 1

        if slots_removed > 0 or materials_removed > 0:
            self.report(
                {'INFO'},
                f"Removed {slots_removed} slot(s), {materials_removed} material(s)",
            )
        else:
            self.report({'INFO'}, "No unused materials to remove")

        return {'FINISHED'}


class LEVELDESIGN_OT_add_anvil_material(Operator):
    """Create a new Anvil material from an image file"""
    bl_idname = "leveldesign.add_anvil_material"
    bl_label = "Add Material"
    bl_options = {'REGISTER', 'UNDO'}

    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.bmp;*.tga;*.tif;*.tiff;*.webp;*.exr;*.hdr;*.dds",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        try:
            from ..texture_browser.browser import texture_browser_modal
            if texture_browser_modal.is_popup_window(context.window):
                return True
        except Exception:
            pass
        return (
            is_level_design_workspace()
            or is_hotspot_mapping_workspace()
        )

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        _debug(f"add_anvil_material execute filepath={self.filepath!r}")
        abs_path = os.path.abspath(bpy.path.abspath(self.filepath))
        _debug(f"abs_path={abs_path!r} exists={os.path.isfile(abs_path)}")
        if not abs_path or not os.path.isfile(abs_path):
            _debug("no file, cancel")
            self.report({'ERROR'}, "Pick an image file")
            return {'CANCELLED'}

        try:
            image = bpy.data.images.load(abs_path, check_existing=True)
        except RuntimeError as exc:
            _debug(f"load failed: {exc}")
            self.report({'ERROR'}, f"Could not load image: {exc}")
            return {'CANCELLED'}

        name = os.path.splitext(os.path.basename(abs_path))[0]
        material = bpy.data.materials.new(name)
        _debug(f"created material {material.name}")
        try:
            set_material_primary_image(material, image)
        except MaterialMappingConflictError as exc:
            bpy.data.materials.remove(material)
            _debug(f"conflict: {exc}")
            self.report({'ERROR'}, str(exc))
            return {'CANCELLED'}

        props = context.scene.level_design_props
        material.anvil_uv_scale = 1.0
        material.anvil_interpolation = props.default_interpolation
        material.anvil_roughness = props.default_roughness
        material.anvil_metallic = props.default_metallic
        texture_as_alpha = bool(props.default_texture_as_alpha)
        if getattr(props, "default_auto_alpha", True) and not texture_as_alpha:
            try:
                from ..core.material_shader import image_has_transparency
                if image_has_transparency(image):
                    texture_as_alpha = True
            except Exception:
                pass
        settings = {
            "uv_scale": 1.0,
            "base_color": (1.0, 1.0, 1.0, 1.0),
            "interpolation": props.default_interpolation,
            "texture_as_alpha": texture_as_alpha,
            "vertex_colors": props.default_vertex_colors,
            "roughness": props.default_roughness,
            "metallic": props.default_metallic,
            "emission_strength": props.default_emission_strength,
            "emission_color": props.default_emission_color,
            "specular": props.default_specular,
        }
        build_canonical_material_shader(material, image, settings)
        _debug(f"shader built; materials now={[m.name for m in bpy.data.materials]}")
        self.report({'INFO'}, f"Created material: {material.name}")
        try:
            idx = list(bpy.data.materials).index(material)
            context.scene.anvil_active_material_index = idx
            _debug(f"active index set to {idx}")
        except ValueError as exc:
            _debug(f"index set failed: {exc}")
            pass
        return {'FINISHED'}


class LEVELDESIGN_UL_anvil_materials(UIList):
    def filter_items(self, context, data, propname):
        flt = getattr(context.scene, "anvil_material_list_filter", "") or ""
        flt = flt.strip().lower()
        if not flt:
            return [], []
        items = getattr(data, propname)
        flags = []
        for item in items:
            name = getattr(item, "name", "") or ""
            if flt in name.lower():
                flags.append(self.bitflag_filter_item)
            else:
                flags.append(0)
        return flags, []

    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        mat = item
        if mat is None:
            return
        row = layout.row(align=True)
        preview = getattr(mat, "preview", None)
        icon_id = getattr(preview, "icon_id", 0) if preview is not None else 0
        row.label(text="", icon_value=icon_id)
        row.prop(mat, "name", text="", emboss=False)
        apply = row.operator("leveldesign.apply_anvil_material", text="", icon='CHECKMARK')
        apply.material_name = mat.name


class LEVELDESIGN_OT_apply_anvil_material(Operator):
    """Apply a material to the selected faces/objects"""
    bl_idname = "leveldesign.apply_anvil_material"
    bl_label = "Apply Material"
    bl_options = {'REGISTER', 'UNDO'}

    material_name: bpy.props.StringProperty()

    @classmethod
    def poll(cls, context):
        try:
            from ..texture_browser.browser import texture_browser_modal
            if texture_browser_modal.is_popup_window(context.window):
                return True
        except Exception:
            pass
        return is_level_design_workspace() or is_hotspot_mapping_workspace()

    def execute(self, context):
        material = bpy.data.materials.get(self.material_name)
        if material is None:
            self.report({'ERROR'}, f"Material not found: {self.material_name}")
            return {'CANCELLED'}

        applied = 0
        for obj in context.selected_objects:
            if obj.type != 'MESH':
                continue
            # ensure slot
            slot_index = None
            for i, slot in enumerate(obj.material_slots):
                if slot.material == material:
                    slot_index = i
                    break
            if slot_index is None:
                obj.data.materials.append(material)
                slot_index = len(obj.material_slots) - 1

            obj.active_material_index = slot_index

            if context.mode == 'EDIT_MESH':
                bpy.ops.object.material_slot_assign()
                applied += 1
            else:
                # assign to all polygons
                for poly in obj.data.polygons:
                    poly.material_index = slot_index
                applied += 1

        if applied == 0:
            self.report({'WARNING'}, "No mesh objects selected")
            return {'CANCELLED'}
        self.report({'INFO'}, f"Applied material: {material.name}")
        return {'FINISHED'}


class LEVELDESIGN_OT_delete_anvil_material(Operator):
    """Delete the active material from the Material Editor"""
    bl_idname = "leveldesign.delete_anvil_material"
    bl_label = "Delete Material"
    bl_options = {'REGISTER', 'UNDO'}

    @classmethod
    def poll(cls, context):
        try:
            from ..texture_browser.browser import texture_browser_modal
            if texture_browser_modal.is_popup_window(context.window):
                return True
        except Exception:
            pass
        return is_level_design_workspace() or is_hotspot_mapping_workspace()

    def execute(self, context):
        scene = context.scene
        mats = bpy.data.materials
        idx = getattr(scene, "anvil_active_material_index", 0)
        if not mats or idx >= len(mats):
            self.report({'WARNING'}, "No material selected")
            return {'CANCELLED'}
        mat = mats[idx]
        try:
            bpy.data.materials.remove(mat, do_unlink=True)
        except RuntimeError as exc:
            self.report({'ERROR'}, f"Could not delete material: {exc}")
            return {'CANCELLED'}
        if scene.anvil_active_material_index >= len(bpy.data.materials):
            scene.anvil_active_material_index = max(0, len(bpy.data.materials) - 1)
        self.report({'INFO'}, f"Deleted material: {mat.name}")
        return {'FINISHED'}

class LEVELDESIGN_OT_load_material_map(Operator):
    """Load an image file into one of the material's map slots"""
    bl_idname = "leveldesign.load_material_map"
    bl_label = "Load Map"
    bl_options = {'REGISTER', 'UNDO'}

    map_type: bpy.props.EnumProperty(
        name="Map Type",
        items=[
            ('primary', "Color Map", "Color / albedo map"),
            ('roughness', "Roughness Map", "Roughness map"),
            ('normal', "Normal Map", "Normal map"),
            ('displacement', "Displacement Map", "Displacement / height map"),
            ('metallic', "Metallic Map", "Metallic map"),
        ],
        default='primary',
    )
    filepath: bpy.props.StringProperty(subtype='FILE_PATH')
    filter_glob: bpy.props.StringProperty(
        default="*.png;*.jpg;*.jpeg;*.bmp;*.tga;*.tif;*.tiff;*.webp;*.exr;*.hdr;*.dds",
        options={'HIDDEN'},
    )

    @classmethod
    def poll(cls, context):
        try:
            from ..texture_browser.browser import texture_browser_modal
            if texture_browser_modal.is_popup_window(context.window):
                return True
        except Exception:
            pass
        return is_level_design_workspace() or is_hotspot_mapping_workspace()

    def invoke(self, context, event):
        context.window_manager.fileselect_add(self)
        return {'RUNNING_MODAL'}

    def execute(self, context):
        abs_path = os.path.abspath(bpy.path.abspath(self.filepath))
        if not abs_path or not os.path.isfile(abs_path):
            self.report({'ERROR'}, "Pick an image file")
            return {'CANCELLED'}
        try:
            image = bpy.data.images.load(abs_path, check_existing=True)
        except RuntimeError as exc:
            self.report({'ERROR'}, f"Could not load image: {exc}")
            return {'CANCELLED'}

        scene = context.scene
        mats = bpy.data.materials
        idx = getattr(scene, "anvil_active_material_index", 0)
        if not mats or idx >= len(mats):
            self.report({'ERROR'}, "No material selected")
            return {'CANCELLED'}
        mat = mats[idx]

        prop_map = {
            'primary': "anvil_primary_image",
            'roughness': "anvil_roughness_image",
            'normal': "anvil_normal_image",
            'displacement': "anvil_displacement_image",
            'metallic': "anvil_metallic_image",
        }
        prop_name = prop_map.get(self.map_type)
        if prop_name is None:
            self.report({'ERROR'}, "Unknown map type")
            return {'CANCELLED'}
        # ensure managed first (for primary) so update callback rebuilds
        if getattr(mat, "anvil_material_schema_version", 0) == 0 and self.map_type == 'primary':
            try:
                from ..core.materials import set_material_primary_image
                set_material_primary_image(mat, image)
            except Exception:
                mat.anvil_material_schema_version = 1
                mat.anvil_primary_image = image
        # Auto alpha for primary image
        if self.map_type == 'primary':
            try:
                props = context.scene.level_design_props
                if getattr(props, "default_auto_alpha", True):
                    from ..core.material_shader import image_has_transparency
                    if image_has_transparency(image):
                        mat.blend_method = 'CLIP'
                    else:
                        mat.blend_method = 'OPAQUE'
            except Exception:
                pass
        setattr(mat, prop_name, image)
        self.report({'INFO'}, f"Loaded {self.map_type} map: {image.name}")
        return {'FINISHED'}



class LEVELDESIGN_OT_clear_material_map(Operator):
    """Clear one of the material's map slots"""
    bl_idname = "leveldesign.clear_material_map"
    bl_label = "Clear Map"
    bl_options = {'REGISTER', 'UNDO'}

    map_type: bpy.props.EnumProperty(
        name="Map Type",
        items=[
            ('primary', "Color Map", "Color / albedo map"),
            ('roughness', "Roughness Map", "Roughness map"),
            ('normal', "Normal Map", "Normal map"),
            ('displacement', "Displacement Map", "Displacement / height map"),
            ('metallic', "Metallic Map", "Metallic map"),
            ('base_color', "Base Color", "Base color tint"),
        ],
        default='primary',
    )

    @classmethod
    def poll(cls, context):
        try:
            from ..texture_browser.browser import texture_browser_modal
            if texture_browser_modal.is_popup_window(context.window):
                return True
        except Exception:
            pass
        return is_level_design_workspace() or is_hotspot_mapping_workspace()

    def execute(self, context):
        scene = context.scene
        mats = bpy.data.materials
        idx = getattr(scene, "anvil_active_material_index", 0)
        if not mats or idx >= len(mats):
            self.report({'ERROR'}, "No material selected")
            return {'CANCELLED'}
        mat = mats[idx]

        if self.map_type == 'base_color':
            mat.anvil_base_color = (1.0, 1.0, 1.0, 1.0)
            self.report({'INFO'}, "Cleared base color tint")
            return {'FINISHED'}

        prop_map = {
            'primary': "anvil_primary_image",
            'roughness': "anvil_roughness_image",
            'normal': "anvil_normal_image",
            'displacement': "anvil_displacement_image",
            'metallic': "anvil_metallic_image",
        }
        prop_name = prop_map.get(self.map_type)
        if prop_name is None:
            self.report({'ERROR'}, "Unknown map type")
            return {'CANCELLED'}
        setattr(mat, prop_name, None)
        self.report({'INFO'}, f"Cleared {self.map_type} map")
        return {'FINISHED'}




classes = (
    LEVELDESIGN_OT_set_interpolation_closest,
    LEVELDESIGN_OT_set_interpolation_linear,
    LEVELDESIGN_OT_toggle_texture_alpha,
    LEVELDESIGN_OT_toggle_vertex_colors,
    LEVELDESIGN_OT_fix_alpha_bleed,
    LEVELDESIGN_OT_reload_material_images,
    LEVELDESIGN_OT_material_primary_image_warning,
    LEVELDESIGN_OT_repair_material_shader,
    LEVELDESIGN_OT_set_default_interpolation,
    LEVELDESIGN_OT_cleanup_unused_materials,
    LEVELDESIGN_OT_add_anvil_material,
    LEVELDESIGN_OT_apply_anvil_material,
    LEVELDESIGN_OT_delete_anvil_material,
    LEVELDESIGN_OT_load_material_map,
    LEVELDESIGN_OT_clear_material_map,
    LEVELDESIGN_UL_anvil_materials,
)


def register():
    for cls in classes:
        bpy.utils.register_class(cls)


def unregister():
    for cls in reversed(classes):
        bpy.utils.unregister_class(cls)
