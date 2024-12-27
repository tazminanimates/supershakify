# This program is free software; you can redistribute it and/or modify
# it under the terms of the GNU General Public License as published by
# the Free Software Foundation; either version 3 of the License, or
# (at your option) any later version.
#
# This program is distributed in the hope that it will be useful, but
# WITHOUT ANY WARRANTY; without even the implied warranty of
# MERCHANTIBILITY or FITNESS FOR A PARTICULAR PURPOSE. See the GNU
# General Public License for more details.
#
# You should have received a copy of the GNU General Public License
# along with this program. If not, see <http://www.gnu.org/licenses/>.

bl_info = {
    "name" : "Camera Shakify Rework",
    "author" : "Tazmin, Nathan Vegdahl, Ian Hubert", 
    "description" : "Add, import, and share captured camera shake/wobble from your cameras.",
    "blender" : (4, 2, 0),
    "version" : (0, 0, 2),
    "location" : "Side Panel/Camera Settings",
    "warning" : "VERY EXPERIMENTAL, MAY CRASH BLENDER/CORRUPT FILES, PLEASE REPORT ANY BUG TO https://forms.gle/b5WSwkwYrHddQhSU7",
    "doc_url": "", 
    "tracker_url": "", 
    "category" : "3D View" 
}
import bpy
import re
import math
from bpy.types import Camera, Context
from .action_utils import action_to_python_data_text, python_data_to_loop_action, action_frame_range
from .shake_data import SHAKE_LIST
import bpy.utils.previews
import os
from bpy_extras.io_utils import ImportHelper, ExportHelper
import pprint
import ast
from bpy.app.handlers import persistent
import importlib.util
import webbrowser

BASE_NAME = "CameraShakifyRework.v2"
COLLECTION_NAME = BASE_NAME
FRAME_EMPTY_NAME = BASE_NAME + "_frame_empty"

# Maximum values of our per-camera scaling/influence properties.
INFLUENCE_MAX = 10.0
SCALE_MAX = 100.0

# The maximum supported world unit scale.
UNIT_SCALE_MAX = 1000.0


#========================================================


class CameraShakifyPanel(bpy.types.Panel):
    """Add shake to your Cameras."""
    bl_label = "Camera Shakify Rework"
    bl_idname = "DATA_PT_camera_shakify"
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = "data"

    @classmethod
    def poll(cls, context):
        return context.active_object.type == 'CAMERA'

    def draw(self, context):
        wm = context.window_manager
        layout = self.layout

        camera = context.active_object

        row = layout.row()
        row.template_list(
            listtype_name="OBJECT_UL_camera_shake_items",
            list_id="Camera Shakes",
            dataptr=camera,
            propname="camera_shakes",
            active_dataptr=camera,
            active_propname="camera_shakes_active_index",
        )
        col = row.column()
        col.operator("object.camera_shake_add", text="", icon='ADD')
        col.operator("object.camera_shake_remove", text="", icon='REMOVE')
        col.operator("object.camera_shake_move", text="", icon='TRIA_UP').type = 'UP'
        col.operator("object.camera_shake_move", text="", icon='TRIA_DOWN').type = 'DOWN'
        if camera.camera_shakes_active_index < len(camera.camera_shakes):
            shake = camera.camera_shakes[camera.camera_shakes_active_index]
            row = layout.row()
            col = row.column(align=True)
            col.alignment = 'RIGHT'
            col.use_property_split = True
            
            # New selection UI
            col.prop(shake, "phone_freaked_out", text="Phone Freaked Out")

        if camera.camera_shakes_active_index < len(camera.camera_shakes):
            shake = camera.camera_shakes[camera.camera_shakes_active_index]
            row = layout.row()
            col = row.column(align=True)
            col.alignment = 'RIGHT'
            col.use_property_split = True
            col.prop(shake, "shake_type", text="Shake")
            col.separator()
            col.prop(shake, "influence", slider=True)
            col.separator()
            col.prop(shake, "scale")
            col.separator()
            col.prop(shake, "use_manual_timing")
            if shake.use_manual_timing:
                col.prop(shake, "time")
            else:
                col.prop(shake, "speed")
                col.prop(shake, "offset")

        col.separator(factor=2.0)

        row = layout.row()
        row.alignment = 'LEFT'
        header_text = "Misc Utilities"
        if wm.camera_shake_show_utils:
            row.prop(wm, "camera_shake_show_utils", icon="DISCLOSURE_TRI_DOWN", text=header_text, expand=False, emboss=False)
        else:
            row.prop(wm, "camera_shake_show_utils", icon="DISCLOSURE_TRI_RIGHT", text=header_text, emboss=False)
        row.separator_spacer()

        col = layout.column()
        if wm.camera_shake_show_utils:
            col.operator("object.camera_shakes_fix_global")


class OBJECT_UL_camera_shake_items(bpy.types.UIList):
    def draw_item(self, context, layout, data, item, icon, active_data, active_propname):
        ob = data
        # draw_item must handle the three layout types... Usually 'DEFAULT' and 'COMPACT' can share the same code.
        if self.layout_type in {'DEFAULT', 'COMPACT'}:
            col = layout.column()
            col.label(
                text=str(item.shake_type).replace("_", " ").title(),
                icon='FCURVE_SNAPSHOT',
            )

            col = layout.column()
            col.alignment = 'RIGHT'
            col.prop(item, "influence", text="", expand=False, slider=True, emboss=False)
        # 'GRID' layout type should be as compact as possible (typically a single icon!).
        elif self.layout_type in {'GRID'}:
            layout.alignment = 'CENTER'
            layout.label(text="", icon_value=icon)


#========================================================

# Creates a camera shake setup for the given camera and
# shake item index, using the given collection to store
# shake empties.
def build_single_shake(camera, shake_item_index, collection, context):
    shake = camera.camera_shakes[shake_item_index]
    shake_data = SHAKE_LIST[shake.shake_type]

    action_name = BASE_NAME + "_" + shake.shake_type.lower()
    shake_object_name = BASE_NAME + "_" + camera.name + "_" + str(shake_item_index)

    # Ensure the needed action exists, and fetch it.
    action = None
    if action_name in bpy.data.actions:
        action = bpy.data.actions[action_name]
    else:
        action = python_data_to_loop_action(
            shake_data[2],
            action_name,
            INFLUENCE_MAX,
            INFLUENCE_MAX * SCALE_MAX * UNIT_SCALE_MAX
        )

    # Ensure the needed shake object exists, fetch it.
    shake_object = None
    if shake_object_name in bpy.data.objects:
        shake_object = bpy.data.objects[shake_object_name]
    else:
        shake_object = bpy.data.objects.new(shake_object_name, None)

    # Make sure the shake object is linked into our collection.
    if shake_object.name not in collection.objects:
        collection.objects.link(shake_object)

    #----------------
    # Set up the constraints and drivers on the shake object.
    #----------------

    # Clear out all constraints and drivers, and fetch animation data block.
    shake_object.constraints.clear()
    shake_object.animation_data_clear()
    anim_data = shake_object.animation_data_create()

    # Some weird gymnastics needed because of a Blender bug.
    # Without first assigning an action to the animation data,
    # then on a fresh scene we won't be able to assign an action
    # to the action constraint (below).
    anim_data.action = action
    shake_object.location = (0,0,0)
    shake_object.rotation_euler = (0,0,0)
    shake_object.rotation_quaternion = (0,0,0,0)
    shake_object.rotation_axis_angle = (0,0,0,0)
    shake_object.scale = (1,1,1)

    # Get action info for calculations below.
    action_fps = shake_data[1]
    action_range = action_frame_range(action)
    action_length = action_range[1] - action_range[0]

    # Create the action constraint.
    constraint = shake_object.constraints.new('ACTION')
    try:
        constraint.use_eval_time = True
    except AttributeError as exc:
        raise Exception("Camera Shakify addon requires a minimum Blender version of 2.91") from exc
    constraint.mix_mode = 'BEFORE'
    constraint.action = action
    constraint.frame_start = math.floor(action_range[0])
    constraint.frame_end = math.ceil(action_range[1])

    # Create the driver for the constraint's eval time.
    driver = constraint.driver_add("eval_time").driver
    driver.type = 'SCRIPTED'
    fps_factor = 1.0 / ((context.scene.render.fps / context.scene.render.fps_base) / action_fps)
    driver.expression = \
        "((time if manual else ((-frame_offset + frame) * speed)) * {}) % 1.0" \
        .format(fps_factor / action_length)

    manual_timing_var = driver.variables.new()
    manual_timing_var.name = "manual"
    manual_timing_var.type = 'SINGLE_PROP'
    manual_timing_var.targets[0].id_type = 'OBJECT'
    manual_timing_var.targets[0].id = camera
    manual_timing_var.targets[0].data_path = 'camera_shakes[{}].use_manual_timing'.format(shake_item_index)

    time_var = driver.variables.new()
    time_var.name = "time"
    time_var.type = 'SINGLE_PROP'
    time_var.targets[0].id_type = 'OBJECT'
    time_var.targets[0].id = camera
    time_var.targets[0].data_path = 'camera_shakes[{}].time'.format(shake_item_index)

    speed_var = driver.variables.new()
    speed_var.name = "speed"
    speed_var.type = 'SINGLE_PROP'
    speed_var.targets[0].id_type = 'OBJECT'
    speed_var.targets[0].id = camera
    speed_var.targets[0].data_path = 'camera_shakes[{}].speed'.format(shake_item_index)

    offset_var = driver.variables.new()
    offset_var.name = "frame_offset"
    offset_var.type = 'SINGLE_PROP'
    offset_var.targets[0].id_type = 'OBJECT'
    offset_var.targets[0].id = camera
    offset_var.targets[0].data_path = 'camera_shakes[{}].offset'.format(shake_item_index)

    #----------------
    # Set up the constraints and drivers on the camera object.
    #----------------

    loc_constraint_name = BASE_NAME + "_loc_" + str(shake_item_index)
    rot_constraint_name = BASE_NAME + "_rot_" + str(shake_item_index)

    # Create the new constraints.
    loc_constraint = camera.constraints.new(type='COPY_LOCATION')
    rot_constraint = camera.constraints.new(type='COPY_ROTATION')
    loc_constraint.name = loc_constraint_name
    rot_constraint.name = rot_constraint_name
    loc_constraint.show_expanded = False
    rot_constraint.show_expanded = False

    # Set up location constraint.
    loc_constraint.target = shake_object
    loc_constraint.target_space = 'WORLD'
    loc_constraint.owner_space = 'LOCAL'
    loc_constraint.use_offset = True

    # Set up rotation constraint.
    rot_constraint.target = shake_object
    rot_constraint.target_space = 'WORLD'
    rot_constraint.owner_space = 'LOCAL'
    rot_constraint.mix_mode = 'AFTER'

    # Set up the location constraint driver.
    driver = loc_constraint.driver_add("influence").driver
    driver.type = 'SCRIPTED'
    driver.expression = "{} * influence * location_scale / unit_scale".format(1.0 / (UNIT_SCALE_MAX * INFLUENCE_MAX * SCALE_MAX))
    if "influence" not in driver.variables:
        var = driver.variables.new()
        var.name = "influence"
        var.type = 'SINGLE_PROP'
        var.targets[0].id_type = 'OBJECT'
        var.targets[0].id = camera
        var.targets[0].data_path = 'camera_shakes[{}].influence'.format(shake_item_index)
    if "location_scale" not in driver.variables:
        var = driver.variables.new()
        var.name = "location_scale"
        var.type = 'SINGLE_PROP'
        var.targets[0].id_type = 'OBJECT'
        var.targets[0].id = camera
        var.targets[0].data_path = 'camera_shakes[{}].scale'.format(shake_item_index)
    if "unit_scale" not in driver.variables:
        var = driver.variables.new()
        var.name = "unit_scale"
        var.type = 'SINGLE_PROP'
        var.targets[0].id_type = 'SCENE'
        var.targets[0].id = context.scene
        var.targets[0].data_path ='unit_settings.scale_length'

    # Set up the rotation constraint driver.
    driver = rot_constraint.driver_add("influence").driver
    driver.type = 'SCRIPTED'
    driver.expression = "influence * {}".format(1.0 / INFLUENCE_MAX)
    if "influence" not in driver.variables:
        var = driver.variables.new()
        var.name = "influence"
        var.type = 'SINGLE_PROP'
        var.targets[0].id_type = 'OBJECT'
        var.targets[0].id = camera
        var.targets[0].data_path = 'camera_shakes[{}].influence'.format(shake_item_index)


# The main function that actually does the real work of this addon.
# It's called whenever anything relevant in the shake list on a
# camera is changed, and just tears down and completely rebuilds
# the camera-shake setup for it.
def rebuild_camera_shakes(camera, context):
    # Ensure that our camera shakify collection exists and fetch it.
    collection = None
    if BASE_NAME in context.scene.collection.children:
        collection = context.scene.collection.children[BASE_NAME]
    else:
        if BASE_NAME not in bpy.data.collections:
            collection = bpy.data.collections.new(BASE_NAME)
            collection.hide_viewport = True
            collection.hide_render = True
            collection.hide_select = True
        else:
            collection = bpy.data.collections[BASE_NAME]
        context.scene.collection.children.link(bpy.data.collections[BASE_NAME])
        for layer in context.scene.view_layers:
            if collection.name in layer.layer_collection.children:
                layer.layer_collection.children[collection.name].exclude = True

    #----------------
    # First, completely tear down the current setup, if any.
    #----------------

    # Remove shake constraints from the camera.
    remove_list = []
    for constraint in camera.constraints:
        if constraint.name.startswith(BASE_NAME):
            constraint.driver_remove("influence")
            remove_list += [constraint]
    for constraint in remove_list:
        camera.constraints.remove(constraint)

    # Remove shake empties for this camera.
    name_match = re.compile("{}_[0-9]+".format(re.escape(BASE_NAME + "_" + camera.name)))
    for obj in collection.objects:
        if name_match.fullmatch(obj.name) != None:
            obj.constraints[0].driver_remove("eval_time")
            obj.animation_data_clear()
            bpy.data.objects.remove(obj)

    #----------------
    # Then build the new setup.
    #----------------

    for shake_item_index in range(0, len(camera.camera_shakes)):
        build_single_shake(camera, shake_item_index, collection, context)

    #----------------
    # Finally, clean up any data that's no longer needed, up to and
    # including removing the collection itself if there no shakes left.
    #----------------

    # If there's nothing left in the collection, delete it.
    if len(collection.objects) == 0:
        context.scene.collection.children.unlink(collection)
        if collection.users == 0:
            bpy.data.collections.remove(collection)

    # Delete unused actions.
    to_remove = []
    for action in bpy.data.actions:
        if action.name.startswith(BASE_NAME):
            if action.users == 0:
                to_remove += [action]
    for action in to_remove:
        bpy.data.actions.remove(action)


# Fixes camera shake setups across the whole scene.
# This can be necessary if e.g. a user has duplicated cameras
# around, etc.
def fix_camera_shakes_globally(context):
    # Delete the collection and everything in it.
    if BASE_NAME in context.scene.collection.children:
        collection = context.scene.collection.children[BASE_NAME]

        for obj in collection.objects:
            obj.constraints[0].driver_remove("eval_time")
            obj.animation_data_clear()
            bpy.data.objects.remove(obj)

        context.scene.collection.children.unlink(collection)
        if collection.users == 0:
            bpy.data.collections.remove(collection)

    # Delete unused actions.
    to_remove = []
    for action in bpy.data.actions:
        if action.name.startswith(BASE_NAME):
            if action.users == 0:
                to_remove += [action]
    for action in to_remove:
        bpy.data.actions.remove(action)

    # Loop through all cameras and re-build their camera shakes.
    for obj in context.scene.objects:
        if obj.type == 'CAMERA':
            rebuild_camera_shakes(obj, context)


def on_shake_type_update(shake_instance, context):
    rebuild_camera_shakes(shake_instance.id_data, context)


#class ActionToPythonData(bpy.types.Operator):
#    """Writes the action on the currently selected object to a text block as Python data"""
#    bl_idname = "object.action_to_python_data"
#    bl_label = "Action to Python Data"
#    bl_options = {'UNDO'}
#
#    @classmethod
#    def poll(cls, context):
#        return context.active_object is not None \
#               and context.active_object.animation_data is not None \
#               and context.active_object.animation_data.action is not None
#
#    def execute(self, context):
#        action_to_python_data_text(context.active_object.animation_data.action, "action_output.txt")
#        return {'FINISHED'}


class CameraShakeAdd(bpy.types.Operator):
    """Adds the selected camera shake to the list"""
    bl_idname = "object.camera_shake_add"
    bl_label = "Add Shake Item"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.active_object is not None and context.active_object.type == 'CAMERA'

    def execute(self, context):
        camera = context.active_object
        shake = camera.camera_shakes.add()
        camera.camera_shakes_active_index = len(camera.camera_shakes) - 1
        rebuild_camera_shakes(camera, context)
        return {'FINISHED'}


class CameraShakeRemove(bpy.types.Operator):
    """Removes the selected camera shake item from the list"""
    bl_idname = "object.camera_shake_remove"
    bl_label = "Remove Shake Item"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'CAMERA' and len(obj.camera_shakes) > 0

    def execute(self, context):
        camera = context.active_object
        if camera.camera_shakes_active_index < len(camera.camera_shakes):
            camera.camera_shakes.remove(camera.camera_shakes_active_index)
            rebuild_camera_shakes(camera, context)
            if camera.camera_shakes_active_index >= len(camera.camera_shakes) and camera.camera_shakes_active_index > 0:
                camera.camera_shakes_active_index -= 1
        return {'FINISHED'}


class CameraShakeMove(bpy.types.Operator):
    """Moves the selected camera shake up/down in the list"""
    bl_idname = "object.camera_shake_move"
    bl_label = "Move Shake Item"
    bl_options = {'UNDO'}

    type: bpy.props.EnumProperty(items = [
        ('UP', "", ""),
        ('DOWN', "", ""),
    ])

    @classmethod
    def poll(cls, context):
        obj = context.active_object
        return obj is not None and obj.type == 'CAMERA' and len(obj.camera_shakes) > 1

    def execute(self, context):
        camera = context.active_object
        index = int(camera.camera_shakes_active_index)
        if self.type == 'UP' and index > 0:
            camera.camera_shakes.move(index, index - 1)
            camera.camera_shakes_active_index -= 1
        elif self.type == 'DOWN' and (index + 1) < len(camera.camera_shakes):
            camera.camera_shakes.move(index, index + 1)
            camera.camera_shakes_active_index += 1
        rebuild_camera_shakes(camera, context)
        return {'FINISHED'}


class CameraShakesFixGlobal(bpy.types.Operator):
    """Ensures that all camera shakes in the scene are set up properly. This generally shouldn't be necessary, but if things are behaving strangely this should fix it"""
    bl_idname = "object.camera_shakes_fix_global"
    bl_label = "Fix All Camera Shakes"
    bl_options = {'UNDO'}

    @classmethod
    def poll(cls, context):
        return context.mode == 'OBJECT'

    def execute(self, context):
        fix_camera_shakes_globally(context)
        return {'FINISHED'}


# An actual instance of Camera shake added to a camera.
class CameraShakeInstance(bpy.types.PropertyGroup):
    shake_type: bpy.props.EnumProperty(
        name = "Shake Type",
        items = [(id, SHAKE_LIST[id][0], "") for id in SHAKE_LIST.keys()],
        options = set(), # Not animatable.
        override = set(), # Not library overridable.
        update = on_shake_type_update,
    )
    influence: bpy.props.FloatProperty(
        name="Influence",
        description="How much the camera shake affects the camera",
        default=1.0,
        min=0.0, max=INFLUENCE_MAX,
        soft_min=0.0, soft_max=1.0,
    )
    scale: bpy.props.FloatProperty(
        name="Scale",
        description="The scale of the shake's location component",
        default=1.0,
        min=0.0, max=SCALE_MAX,
        soft_min=0.0, soft_max=2.0,
    )
    use_manual_timing: bpy.props.BoolProperty(
        name="Manual Timing",
        description="Manually animate the progression of time through the camera shake animation",
        default=False,
    )
    time: bpy.props.FloatProperty(
        name="Time",
        description="Current time (in frame number) of the shake animation",
        default=0.0,
        precision=1,
        step=100.0,
    )
    speed: bpy.props.FloatProperty(
        name="Speed",
        description="Multiplier for how fast the shake animation plays",
        default=1.0,
        soft_min=0.0, soft_max=4.0,
        options = set(), # Not animatable.
    )
    offset: bpy.props.FloatProperty(
        name="Frame Offset",
        description="How many frames to offset the shake animation",
        default=0.0,
        precision=1,
        step=100.0,
    )




def string_to_int(value):
    if value.isdigit():
        return int(value)
    return 0


def string_to_icon(value):
    if value in bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items.keys():
        return bpy.types.UILayout.bl_rna.functions["prop"].parameters["icon"].enum_items[value].value
    return string_to_int(value)


addon_keymaps = {}
_icons = None


def display_collection_id(uid, vars):
    id = f"coll_{uid}"
    for var in vars.keys():
        if var.startswith("i_"):
            id += f"_{var}_{vars[var]}"
    return id


class SNA_UL_display_collection_list_B4700(bpy.types.UIList):

    def draw_item(self, context, layout, data, item_B4700, icon, active_data, active_propname, index_B4700):
        row = layout
        layout.prop(item_B4700, 'shake_name', text='', icon_value=string_to_icon('CON_CAMERASOLVER'), emboss=False)
        op = layout.operator('sna.uninstall_shake_88f90', text='', icon_value=string_to_icon('CANCEL'), emboss=False, depress=False)
        op.sna_item_index = index_B4700

    def filter_items(self, context, data, propname):
        flt_flags = []
        for item in getattr(data, propname):
            if not self.filter_name or self.filter_name.lower() in item.name.lower():
                if True:
                    flt_flags.append(self.bitflag_filter_item)
                else:
                    flt_flags.append(0)
            else:
                flt_flags.append(0)
        return flt_flags, []


def sna_update_sna_imported_shake_index_126B9(self, context):
    sna_updated_prop = self.sna_imported_shake_index
    bpy.context.scene.sna_imported_shake_index = -32


class SNA_PT_SHAKIFY_REWORK_51BA1(bpy.types.Panel):
    bl_label = 'Shakify Rework'
    bl_idname = 'SNA_PT_SHAKIFY_REWORK_51BA1'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = ''
    bl_category = 'Camera Shakify Rework'
    bl_order = 0
    bl_ui_units_x=0

    @classmethod
    def poll(cls, context):
        return not (False)

    def draw_header(self, context):
        layout = self.layout

    def draw(self, context):
        layout = self.layout
        if bpy.context.view_layer.objects.selected[0].type == 'CAMERA':
            pass
        else:
            box_AD5CA = layout.box()
            box_AD5CA.alert = True
            box_AD5CA.enabled = True
            box_AD5CA.active = True
            box_AD5CA.use_property_split = False
            box_AD5CA.use_property_decorate = False
            box_AD5CA.alignment = 'Expand'.upper()
            box_AD5CA.scale_x = 1.0
            box_AD5CA.scale_y = 2.0
            if not True: box_AD5CA.operator_context = "EXEC_DEFAULT"
            box_AD5CA.label(text='Selected Object is Not Camera', icon_value=string_to_icon('PMARKER_ACT'))


class SNA_PT_IMPORTED_SHAKES_F02AD(bpy.types.Panel):
    bl_label = 'Imported Shakes'
    bl_idname = 'SNA_PT_IMPORTED_SHAKES_F02AD'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = ''
    bl_order = 1
    bl_parent_id = 'SNA_PT_SHAKIFY_REWORK_51BA1'
    bl_ui_units_x=0

    @classmethod
    def poll(cls, context):
        return not (False)

    def draw_header(self, context):
        layout = self.layout

    def draw(self, context):
        layout = self.layout
        op = layout.operator('sna.list_shakes_1252f', text='Reload Shakes', icon_value=string_to_icon('FILE_REFRESH'), emboss=True, depress=False)
        row_B8F37 = layout.row(heading='', align=True)
        row_B8F37.alert = False
        row_B8F37.enabled = bpy.context.view_layer.objects.selected[0].type == 'CAMERA'
        row_B8F37.active = True
        row_B8F37.use_property_split = False
        row_B8F37.use_property_decorate = False
        row_B8F37.scale_x = 1.0
        row_B8F37.scale_y = 1.0
        row_B8F37.alignment = 'Expand'.upper()
        row_B8F37.operator_context = "INVOKE_DEFAULT" if True else "EXEC_DEFAULT"
        coll_id = display_collection_id('B4700', locals())
        row_B8F37.template_list('SNA_UL_display_collection_list_B4700', coll_id, bpy.context.scene, 'sna_all_shakes', bpy.context.scene, 'sna_imported_shake_index', rows=0)
        col_A9BE3 = layout.column(heading='', align=False)
        col_A9BE3.alert = False
        col_A9BE3.enabled = True
        col_A9BE3.active = True
        col_A9BE3.use_property_split = False
        col_A9BE3.use_property_decorate = False
        col_A9BE3.scale_x = 1.0
        col_A9BE3.scale_y = 2.5
        col_A9BE3.alignment = 'Expand'.upper()
        col_A9BE3.operator_context = "INVOKE_DEFAULT" if True else "EXEC_DEFAULT"
        op = col_A9BE3.operator('sna.import_shakes_743f2', text='Import Shake', icon_value=string_to_icon('IMPORT'), emboss=True, depress=False)


class SNA_OT_Export_Shake_54408(bpy.types.Operator, ExportHelper):
    bl_idname = "sna.export_shake_54408"
    bl_label = "Export Shake."
    bl_description = "Exports Custom Shake out of Super Shakify"
    bl_options = {"REGISTER", "UNDO"}
    filter_glob: bpy.props.StringProperty( default='*.py', options={'HIDDEN'} )
    filename_ext = '.py'

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        shake_namer = bpy.context.scene.sna_shake_name
        frame_starts = bpy.context.scene.sna_frame_begin
        frame_ends = bpy.context.scene.sna_frame_end
        pathe = self.filepath
        success = None
        from bpy.types import Action

        def action_to_python_data_text(frame_start, frame_end, shake_name, export_path):
            obj = bpy.context.object
            if not obj or not obj.animation_data or not obj.animation_data.action:
                raise ValueError("No active object with an action found.")
            act = obj.animation_data.action
            # Convert the shake_name to the desired format (uppercase with underscores)
            shake_id = shake_name.upper().replace(" ", "_")
            # Collect channel data
            channels = {}
            for curve in act.fcurves:
                baked_keys = []
                for frame in range(frame_start, frame_end + 1):
                    baked_keys.append((frame, curve.evaluate(frame)))
                channels[(curve.data_path, curve.array_index)] = baked_keys
            # Generate Python data text
            text = "SHAKE_LIST = {\n"
            text += f"    '{shake_id}': ('{shake_name}', 24.0, {{\n"
            for (data_path, array_index), points in channels.items():
                text += f"        ('{data_path}', {array_index}): ["
                text += ", ".join(f"({frame}, {value:.6f})" for frame, value in points)
                text += "],\n"
            text += "    })\n"
            text += "}\n"
            # Write to file
            with open(export_path, "w") as f:
                f.write(text)
                success = True
            print(f"Shake data exported to {export_path}")
        # Execute the function
        action_to_python_data_text(frame_starts, frame_ends, shake_namer, pathe)
        print(bpy.context.scene.sna_shake_name + ' was exported :0')
        return {"FINISHED"}


class SNA_OT_Import_Shakes_743F2(bpy.types.Operator, ImportHelper):
    bl_idname = "sna.import_shakes_743f2"
    bl_label = "Import Shake(s)"
    bl_description = "Imports a shake into Super Shakify"
    bl_options = {"REGISTER", "UNDO"}
    filter_glob: bpy.props.StringProperty( default='*.py', options={'HIDDEN'} )

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        pathi = self.filepath
        import os
        # Define `filepath` based on `pathi` (already defined outside the script)
        filepath = pathi
        print("Current working directory:", os.getcwd())

        def load_shake_list(filepath):
            """Load the SHAKE_LIST from the given Python file."""
            shake_list = None
            with open(filepath, 'r') as file:
                file_content = file.read()
                # Check if SHAKE_LIST is defined
                if "SHAKE_LIST" in file_content:
                    # Execute the file to get the SHAKE_LIST into a local scope
                    exec(file_content, globals())
                    shake_list = globals().get("SHAKE_LIST")
            return shake_list

        def save_shake_list(filepath, shake_list):
            """Overwrite the SHAKE_LIST in the target Python file with Python dictionary formatting."""
            with open(filepath, 'w') as file:
                file.write("SHAKE_LIST = {\n")
                for key, value in shake_list.items():
                    file.write(f'    "{key}": ')
                    pprint.pprint(value, stream=file, indent=4, width=120)
                    file.write(", ")
                file.write("}\n")
        # Ensure the target file exists and resolve its path
        addon_directory = os.path.dirname(__file__)
        target_file = os.path.join(addon_directory, "shake_data.py")
        if not os.path.exists(target_file):
            raise FileNotFoundError(f"shake_data.py not found at: {target_file}")
        # Load the SHAKE_LIST from the defined filepath and target file
        source_shake_list = load_shake_list(filepath)
        if source_shake_list is None:
            raise ValueError(f"No SHAKE_LIST found in the source file: {filepath}")
        target_shake_list = load_shake_list(target_file)
        if target_shake_list is None:
            raise ValueError(f"No SHAKE_LIST found in the target file: {target_file}")
        # Merge the lists
        target_shake_list = target_shake_list | source_shake_list
        # Save the updated list back to the target file, fully overwriting it with formatting
        save_shake_list(target_file, target_shake_list)
        print("Shake List successfully updated and overwritten in formatted style!")
        prev_context = bpy.context.area.type
        bpy.context.area.type = 'VIEW_3D'
        bpy.ops.sna.list_shakes_1252f('INVOKE_DEFAULT', )
        bpy.context.area.type = prev_context
        return {"FINISHED"}


class SNA_PT_CAMERA_SHAKIFY_2_9D90B(bpy.types.Panel):
    bl_label = 'Camera Shakify 2'
    bl_idname = 'SNA_PT_CAMERA_SHAKIFY_2_9D90B'
    bl_space_type = 'PROPERTIES'
    bl_region_type = 'WINDOW'
    bl_context = 'data'
    bl_category = 'Camera Shakify 2.0'
    bl_order = 3
    bl_ui_units_x=0

    @classmethod
    def poll(cls, context):
        return not (True)

    def draw_header(self, context):
        layout = self.layout

    def draw(self, context):
        layout = self.layout
        col_47E7A = layout.column(heading='', align=False)
        col_47E7A.alert = False
        col_47E7A.enabled = True
        col_47E7A.active = True
        col_47E7A.use_property_split = True
        col_47E7A.use_property_decorate = False
        col_47E7A.scale_x = 1.0
        col_47E7A.scale_y = 1.0
        col_47E7A.alignment = 'Expand'.upper()
        col_47E7A.operator_context = "INVOKE_DEFAULT" if True else "EXEC_DEFAULT"
        col_47E7A.prop(bpy.context.scene, 'sna_shake_author', text='Shake', icon_value=0, emboss=True)
        col_47E7A.separator(factor=0.5)
        col_47E7A.prop(bpy.context.scene, 'sna_shake_author', text='Influence', icon_value=0, emboss=True)
        col_47E7A.separator(factor=0.5)
        col_47E7A.prop(bpy.context.scene, 'sna_shake_author', text='Scale', icon_value=0, emboss=True)


class SNA_OT_Uninstall_Shake_88F90(bpy.types.Operator):
    bl_idname = "sna.uninstall_shake_88f90"
    bl_label = "Uninstall Shake"
    bl_description = "You do want to remove this shake? This cannot be undone."
    bl_options = {"REGISTER", "UNDO"}
    sna_item_index: bpy.props.IntProperty(name='Item Index', description='', options={'HIDDEN'}, default=0, subtype='NONE')

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        item_to_remove = bpy.context.scene.sna_all_shakes[self.sna_item_index].shake_id
        # Importing necessary module
        # Define the variable with the value to delete
        current_dir = os.path.dirname(os.path.abspath(__file__))
        file_name = "shake_data.py"
        file_path = os.path.join(current_dir, file_name)
        # Read the original file content
        with open(file_path, "r") as file:
            data = file.read()
        # Parse the file content into a Python dictionary
        parsed_data = ast.literal_eval(data[data.index('=') + 1:].strip())
        # Remove the value from the dictionary
        if item_to_remove in parsed_data:
            del parsed_data[item_to_remove]
        # Write the updated data back to the file
        with open(file_path, "w") as file:
            file.write(f"SHAKE_LIST = {parsed_data}")
        if (self.sna_item_index == int(len(bpy.context.scene.sna_all_shakes) - 1.0)):
            if len(bpy.context.scene.sna_all_shakes) > self.sna_item_index:
                bpy.context.scene.sna_all_shakes.remove(self.sna_item_index)
        else:
            if len(bpy.context.scene.sna_all_shakes) > self.sna_item_index:
                bpy.context.scene.sna_all_shakes.remove(self.sna_item_index)
        return {"FINISHED"}

    def invoke(self, context, event):
        return context.window_manager.invoke_confirm(self, event)


@persistent
def load_pre_handler_59087(dummy):
    prev_context = bpy.context.area.type
    bpy.context.area.type = 'VIEW_3D'
    bpy.ops.sna.list_shakes_1252f('INVOKE_DEFAULT', )
    bpy.context.area.type = prev_context


class SNA_OT_List_Shakes_1252F(bpy.types.Operator):
    bl_idname = "sna.list_shakes_1252f"
    bl_label = "List Shakes"
    bl_description = ""
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        bpy.context.scene.sna_all_shakes.clear()
        shake_names = None
        shake_ids = None
        import os

        def load_shake_data():
            # Get the directory of the currently loaded addon
            addon_dir = os.path.dirname(os.path.abspath(__file__))
            # Construct the path to shake_data.py
            file_name = "shake_data.py"
            file_path = os.path.join(addon_dir, file_name)
            # Check if the file exists
            if not os.path.isfile(file_path):
                print(f"Error: {file_name} not found in the addon directory.")
                return None, None
            # Dynamically load the shake_data.py file
            spec = importlib.util.spec_from_file_location("shake_data", file_path)
            shake_data = importlib.util.module_from_spec(spec)
            spec.loader.exec_module(shake_data)
            # Extract SHAKE_LIST from the loaded module
            SHAKE_LIST = getattr(shake_data, "SHAKE_LIST", None)
            if SHAKE_LIST is None:
                print("Error: SHAKE_LIST not found in shake_data.py.")
                return None, None
            # Extract shake names and IDs
            shake_names = [value[0] for value in SHAKE_LIST.values()]
            shake_ids = list(SHAKE_LIST.keys())
            return shake_names, shake_ids
        # Use the function within the addon
        shake_names, shake_ids = load_shake_data()
        # Print results for debugging
        if shake_names and shake_ids:
            print("Shake Names:", shake_names)
            print("Shake IDs:", shake_ids)
        for i_CF8A3 in range(len(shake_names)):
            item_6F9F2 = bpy.context.scene.sna_all_shakes.add()
            item_6F9F2.shake_id = shake_ids[i_CF8A3]
            item_6F9F2.shake_name = shake_names[i_CF8A3]
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_AddonPreferences_80B3B(bpy.types.AddonPreferences):
    bl_idname = __package__

    def draw(self, context):
        if not (False):
            layout = self.layout 
            layout.label(text='VERY EXPERIMENTAL', icon_value=string_to_icon('WARNING_LARGE'))
            layout.label(text='MAY CRASH BLENDER/CORRUPT FILES', icon_value=string_to_icon('WARNING_LARGE'))
            split_29DDD = layout.split(factor=0.5, align=False)
            split_29DDD.alert = False
            split_29DDD.enabled = True
            split_29DDD.active = True
            split_29DDD.use_property_split = False
            split_29DDD.use_property_decorate = False
            split_29DDD.scale_x = 1.0
            split_29DDD.scale_y = 1.0
            split_29DDD.alignment = 'Expand'.upper()
            if not True: split_29DDD.operator_context = "EXEC_DEFAULT"
            split_29DDD.label(text='PLEASE REPORT ANY BUG TO', icon_value=707)
            op = split_29DDD.operator('sna.open_report_cf637', text='Google Forms', icon_value=100, emboss=True, depress=False)


class SNA_OT_Open_Report_Cf637(bpy.types.Operator):
    bl_idname = "sna.open_report_cf637"
    bl_label = "Open Report"
    bl_description = ""
    bl_options = {"REGISTER", "UNDO"}

    @classmethod
    def poll(cls, context):
        if bpy.app.version >= (3, 0, 0) and True:
            cls.poll_message_set('')
        return not False

    def execute(self, context):
        webbrowser.open('https://docs.google.com/forms/d/e/1FAIpQLSe6kpkTUCDTfEn1czsim7gFjbwI1S7Wq4n-jjdENwfrngPpOg/viewform?usp=dialog')  # Go to example.com
        return {"FINISHED"}

    def invoke(self, context, event):
        return self.execute(context)


class SNA_PT_EXPORT_SHAKE_AD9A3(bpy.types.Panel):
    bl_label = 'Export Shake'
    bl_idname = 'SNA_PT_EXPORT_SHAKE_AD9A3'
    bl_space_type = 'VIEW_3D'
    bl_region_type = 'UI'
    bl_context = ''
    bl_order = 3
    bl_options = {'DEFAULT_CLOSED'}
    bl_parent_id = 'SNA_PT_SHAKIFY_REWORK_51BA1'
    bl_ui_units_x=0

    @classmethod
    def poll(cls, context):
        return not (False)

    def draw_header(self, context):
        layout = self.layout

    def draw(self, context):
        layout = self.layout
        col_3FD36 = layout.column(heading='', align=False)
        col_3FD36.alert = False
        col_3FD36.enabled = bpy.context.view_layer.objects.selected[0].type == 'CAMERA'
        col_3FD36.active = True
        col_3FD36.use_property_split = False
        col_3FD36.use_property_decorate = False
        col_3FD36.scale_x = 1.0
        col_3FD36.scale_y = 1.0
        col_3FD36.alignment = 'Expand'.upper()
        col_3FD36.operator_context = "INVOKE_DEFAULT" if True else "EXEC_DEFAULT"
        col_3FD36.prop(bpy.context.scene, 'sna_camera', text='Camera', icon_value=string_to_icon('CAMERA_DATA'), emboss=True)
        col_01142 = col_3FD36.column(heading='', align=False)
        col_01142.alert = False
        col_01142.enabled = True
        col_01142.active = True
        col_01142.use_property_split = False
        col_01142.use_property_decorate = False
        col_01142.scale_x = 1.0
        col_01142.scale_y = 1.0
        col_01142.alignment = 'Expand'.upper()
        col_01142.operator_context = "INVOKE_DEFAULT" if True else "EXEC_DEFAULT"
        col_01142.prop(bpy.context.scene, 'sna_shake_name', text='Shake Name', icon_value=0, emboss=True)
        col_3FD36.prop(bpy.context.scene, 'sna_shake_author', text='', icon_value=0, emboss=True)
        col_3FD36.prop(bpy.context.scene, 'sna_shake_type', text='Shake Type', icon_value=string_to_icon('CON_CAMERASOLVER'), emboss=True)
        col_5A414 = col_3FD36.column(heading='', align=True)
        col_5A414.alert = False
        col_5A414.enabled = True
        col_5A414.active = True
        col_5A414.use_property_split = True
        col_5A414.use_property_decorate = False
        col_5A414.scale_x = 1.0
        col_5A414.scale_y = 1.0
        col_5A414.alignment = 'Expand'.upper()
        col_5A414.operator_context = "INVOKE_DEFAULT" if True else "EXEC_DEFAULT"
        col_5A414.prop(bpy.context.scene, 'sna_frame_begin', text='Frame Start', icon_value=0, emboss=True)
        col_5A414.prop(bpy.context.scene, 'sna_frame_end', text='End', icon_value=0, emboss=True)
        split_D881A = col_3FD36.split(factor=0.5, align=True)
        split_D881A.alert = False
        split_D881A.enabled = True
        split_D881A.active = True
        split_D881A.use_property_split = False
        split_D881A.use_property_decorate = False
        split_D881A.scale_x = 1.0
        split_D881A.scale_y = 2.0
        split_D881A.alignment = 'Expand'.upper()
        if not True: split_D881A.operator_context = "EXEC_DEFAULT"
        split_D881A.prop(bpy.context.scene, 'sna_affects_position', text='Affects Position', icon_value=string_to_icon('ORIENTATION_GLOBAL'), emboss=True, invert_checkbox=True)
        split_D881A.prop(bpy.context.scene, 'sna_affects_rotation', text='Affects Rotation', icon_value=string_to_icon('ORIENTATION_GIMBAL'), emboss=True, invert_checkbox=True)
        col_DEB08 = col_3FD36.column(heading='', align=False)
        col_DEB08.alert = False
        col_DEB08.enabled = (bool(bpy.context.scene.sna_shake_name) and (not bpy.context.scene.sna_camera.type == 'CAMERA'))
        col_DEB08.active = True
        col_DEB08.use_property_split = False
        col_DEB08.use_property_decorate = False
        col_DEB08.scale_x = 1.0
        col_DEB08.scale_y = 2.5
        col_DEB08.alignment = 'Expand'.upper()
        col_DEB08.operator_context = "INVOKE_DEFAULT" if True else "EXEC_DEFAULT"
        op = col_DEB08.operator('sna.export_shake_54408', text='Export Shake', icon_value=string_to_icon('EXPORT'), emboss=True, depress=False)


class SNA_GROUP_sna_property_groups(bpy.types.PropertyGroup):
    shake_name: bpy.props.StringProperty(name='Shake Name', description='', default='', subtype='NONE', maxlen=0)
    affects_location: bpy.props.BoolProperty(name='Affects Location', description='', default=False)
    affects_rotation: bpy.props.BoolProperty(name='Affects Rotation', description='', default=False)
    shake_id: bpy.props.StringProperty(name='Shake ID', description='', default='', subtype='NONE', maxlen=0)


def sna_shakes_enum_items(self, context):
    return [("No Items", "No Items", "No generate enum items node found to create items!", "ERROR", 0)]


#========================================================


def register():
    global _icons
    _icons = bpy.utils.previews.new()
    bpy.utils.register_class(SNA_GROUP_sna_property_groups)
    bpy.types.Scene.sna_camera = bpy.props.PointerProperty(name='Camera', description='', type=bpy.types.Camera)
    bpy.types.Scene.sna_shake_name = bpy.props.StringProperty(name='Shake Name', description='', default='', subtype='NONE', maxlen=0)
    bpy.types.Scene.sna_shake_type = bpy.props.EnumProperty(name='Shake Type', description='', items=[('Handheld', 'Handheld', 'Handeheld Camera Type. Replicates the movements a camera would make when held.', 0, 0), ('Cinematic', 'Cinematic', 'Cinematic Camera Type. Replicates the shake of a cinematic camera', 0, 1)])
    bpy.types.Scene.sna_affects_position = bpy.props.BoolProperty(name='Affects Position', description='', default=False)
    bpy.types.Scene.sna_affects_rotation = bpy.props.BoolProperty(name='Affects Rotation', description='', default=False)
    bpy.types.Scene.sna_frame_begin = bpy.props.IntProperty(name='Frame Begin', description='', default=1, subtype='NONE', soft_min=0)
    bpy.types.Scene.sna_frame_end = bpy.props.IntProperty(name='Frame End', description='', default=250, subtype='NONE', soft_min=0)
    bpy.types.Scene.sna_selected = bpy.props.IntProperty(name='Selected', description='', default=0, subtype='NONE')
    bpy.types.Scene.sna_shakes = bpy.props.EnumProperty(name='Shakes', description='', items=sna_shakes_enum_items)
    bpy.types.Scene.sna_imported_shake_index = bpy.props.IntProperty(name='Imported Shake index', description='', default=0, subtype='NONE', update=sna_update_sna_imported_shake_index_126B9)
    bpy.types.Scene.sna_all_shakes = bpy.props.CollectionProperty(name='All Shakes', description='', type=SNA_GROUP_sna_property_groups)
    bpy.types.Scene.sna_shake_author = bpy.props.StringProperty(name='Shake Author', description='', default='', subtype='NONE', maxlen=0)
    bpy.utils.register_class(SNA_PT_SHAKIFY_REWORK_51BA1)
    bpy.utils.register_class(SNA_PT_IMPORTED_SHAKES_F02AD)
    bpy.utils.register_class(SNA_UL_display_collection_list_B4700)
    bpy.utils.register_class(SNA_OT_Export_Shake_54408)
    bpy.utils.register_class(SNA_OT_Import_Shakes_743F2)
    bpy.utils.register_class(SNA_PT_CAMERA_SHAKIFY_2_9D90B)
    bpy.utils.register_class(SNA_OT_Uninstall_Shake_88F90)
    bpy.app.handlers.load_pre.append(load_pre_handler_59087)
    bpy.utils.register_class(SNA_OT_List_Shakes_1252F)
    bpy.utils.register_class(SNA_AddonPreferences_80B3B)
    bpy.utils.register_class(SNA_OT_Open_Report_Cf637)
    bpy.utils.register_class(SNA_PT_EXPORT_SHAKE_AD9A3)
    bpy.utils.register_class(CameraShakifyPanel)
    bpy.utils.register_class(OBJECT_UL_camera_shake_items)
    bpy.utils.register_class(CameraShakeInstance)
    bpy.utils.register_class(CameraShakeAdd)
    bpy.utils.register_class(CameraShakeRemove)
    bpy.utils.register_class(CameraShakeMove)
    bpy.utils.register_class(CameraShakesFixGlobal)
    #bpy.utils.register_class(ActionToPythonData)
    #bpy.types.VIEW3D_MT_object.append(
    #    lambda self, context : self.layout.operator(ActionToPythonData.bl_idname)
    #)

    # The list of camera shakes active on an camera, along with each shake's parameters.
    bpy.types.Object.camera_shakes = bpy.props.CollectionProperty(type=CameraShakeInstance)
    bpy.types.Object.camera_shakes_active_index = bpy.props.IntProperty(name="Camera Shake List Active Item Index")
    

    bpy.types.WindowManager.camera_shake_show_utils = bpy.props.BoolProperty(name="Show Camera Shake Utils UI", default=False)
    


def unregister():
    global _icons
    bpy.utils.previews.remove(_icons)
    wm = bpy.context.window_manager
    kc = wm.keyconfigs.addon
    for km, kmi in addon_keymaps.values():
        km.keymap_items.remove(kmi)
    addon_keymaps.clear()
    del bpy.types.Scene.sna_shake_author
    del bpy.types.Scene.sna_all_shakes
    del bpy.types.Scene.sna_imported_shake_index
    del bpy.types.Scene.sna_shakes
    del bpy.types.Scene.sna_selected
    del bpy.types.Scene.sna_frame_end
    del bpy.types.Scene.sna_frame_begin
    del bpy.types.Scene.sna_affects_rotation
    del bpy.types.Scene.sna_affects_position
    del bpy.types.Scene.sna_shake_type
    del bpy.types.Scene.sna_shake_name
    del bpy.types.Scene.sna_camera
    bpy.utils.unregister_class(SNA_GROUP_sna_property_groups)
    bpy.utils.unregister_class(SNA_PT_SHAKIFY_REWORK_51BA1)
    bpy.utils.unregister_class(SNA_PT_IMPORTED_SHAKES_F02AD)
    bpy.utils.unregister_class(SNA_UL_display_collection_list_B4700)
    bpy.utils.unregister_class(SNA_OT_Export_Shake_54408)
    bpy.utils.unregister_class(SNA_OT_Import_Shakes_743F2)
    bpy.utils.unregister_class(SNA_PT_CAMERA_SHAKIFY_2_9D90B)
    bpy.utils.unregister_class(SNA_OT_Uninstall_Shake_88F90)
    bpy.app.handlers.load_pre.remove(load_pre_handler_59087)
    bpy.utils.unregister_class(SNA_OT_List_Shakes_1252F)
    bpy.utils.unregister_class(SNA_AddonPreferences_80B3B)
    bpy.utils.unregister_class(SNA_OT_Open_Report_Cf637)
    bpy.utils.unregister_class(SNA_PT_EXPORT_SHAKE_AD9A3)
    bpy.utils.unregister_class(CameraShakifyPanel)
    bpy.utils.unregister_class(OBJECT_UL_camera_shake_items)
    bpy.utils.unregister_class(CameraShakeInstance)
    bpy.utils.unregister_class(CameraShakeAdd)
    bpy.utils.unregister_class(CameraShakeRemove)
    bpy.utils.unregister_class(CameraShakeMove)
    bpy.utils.unregister_class(CameraShakesFixGlobal)
    #bpy.utils.unregister_class(ActionToPythonData)


if __name__ == "__main__":
    register()
