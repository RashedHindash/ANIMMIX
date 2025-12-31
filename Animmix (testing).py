"""
Animmix for 3ds Max 2026 - Fixed List Access
"""

import pymxs
from PySide6 import QtWidgets, QtCore, QtGui
import qtmax
import os
import datetime
import glob


rt = pymxs.runtime
# ============================================================================
# CORE LOGIC (FIXED)
# ============================================================================

class TweenData:
    __slots__ = ['obj', 'prop', 'ctrl', 'sub_ctrls', 'is_euler', 'is_xyz', 'prev_key', 'next_key', 
                 'prev_val', 'next_val', 'orig_val']

_cache = {'valid': False, 'ct': 0, 'items': [], 'ca_items': [], 'obj_items': {}}

def clear_cache():
    global _cache
    _cache['valid'] = False
    _cache['items'] = []
    _cache['ca_items'] = []
    _cache['obj_items'] = {}

def has_key_at_time(ctrl, time):
    try:
        return rt.getKeyIndex(ctrl, time) > 0
    except:
        return False

def is_list_controller(ctrl):
    if not ctrl: return False
    try:
        # Standard Max List Controller check
        return rt.isProperty(ctrl, "weight") and rt.isProperty(ctrl, "count")
    except: return False

def resolve_controller(ctrl):
    """
    FIXED: Robustly digs out the active controller from a list.
    """
    if ctrl is None: return None
    
    loop_guard = 0
    # Loop to handle nested lists (List inside a List)
    while is_list_controller(ctrl) and loop_guard < 5:
        loop_guard += 1
        try:
            # 1. Get Active Index (1-based in Max)
            active_idx = ctrl.getActive()
            
            # If no layer is active, we cannot proceed
            if active_idx is None or active_idx < 1: 
                break 
            
            # 2. Extract Sub-Controller
            # Note: In pymxs, list controllers act like arrays.
            # Usually index 0 in Python is index 1 in Max.
            # But sometimes pymxs wrappers use 1-based for list items. 
            # We try both to be safe.
            sub_found = None
            
            try:
                # Attempt A: 0-based access (Standard Python)
                item = ctrl[active_idx - 1]
                if hasattr(item, 'controller') and item.controller:
                    sub_found = item.controller
                else:
                    sub_found = item
            except:
                try:
                    # Attempt B: 1-based access (Max Wrapper)
                    item = ctrl[active_idx]
                    if hasattr(item, 'controller') and item.controller:
                        sub_found = item.controller
                    else:
                        sub_found = item
                except: pass

            # 3. Update or Break
            if sub_found:
                ctrl = sub_found
            else:
                break
                
        except: break
        
    return ctrl

def get_controller(obj, prop):
    try:
        tm_ctrl = obj.controller
        p_map = {"position": "Position", "rotation": "Rotation", "scale": "Scale"}
        
        # 1. Get Base
        base_ctrl = rt.getPropertyController(tm_ctrl, p_map[prop])
        
        # 2. Drill Down
        final_ctrl = resolve_controller(base_ctrl)
        
        return final_ctrl
    except: return None

def is_xyz_controller(ctrl):
    if ctrl is None: return False
    name = str(rt.classof(ctrl)).lower()
    return "xyz" in name or "euler" in name

def is_euler_rotation(ctrl):
    if ctrl is None: return False
    cls = rt.classof(ctrl)
    return cls == rt.Euler_XYZ or "euler" in str(cls).lower()

def controller_has_keys(ctrl):
    if ctrl is None: return False
    
    # 1. Direct Check
    try:
        nk = rt.numKeys(ctrl)
        if nk > 0: return True
    except: pass
    
    # 2. Sub-Anim Check (Required for Euler/PositionXYZ)
    if is_xyz_controller(ctrl):
        for i in range(3):
            try:
                # Access X, Y, or Z
                sub = ctrl[i]
                sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                # IMPORTANT: Resolve sub-track if it is also a list
                sub_ctrl = resolve_controller(sub_ctrl)
                
                if sub_ctrl and rt.numKeys(sub_ctrl) > 0: return True
            except: pass
            
    return False

def get_all_key_times(ctrl):
    times = set()
    if ctrl is None: return list(times)
    
    def collect(c):
        if not c: return
        try:
            nk = rt.numKeys(c)
            if nk > 0:
                for k in range(1, nk + 1):
                    times.add(int(rt.getKeyTime(c, k)))
        except: pass

    # 1. Main
    collect(ctrl)
    
    # 2. Subs
    if is_xyz_controller(ctrl):
        for i in range(3):
            try:
                sub = ctrl[i]
                sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                sub_ctrl = resolve_controller(sub_ctrl)
                collect(sub_ctrl)
            except: pass
            
    return sorted(list(times))

# ... (Standard helpers) ...
def get_all_custom_attribute_defs(obj):
    ca_defs = []
    try:
        num_ca = rt.custAttributes.count(obj)
        for i in range(1, num_ca + 1):
            ca_def = rt.custAttributes.get(obj, i)
            if ca_def: ca_defs.append((obj, i, ca_def))
        if rt.isProperty(obj, rt.Name("modifiers")):
            for m in obj.modifiers:
                try:
                    num_ca = rt.custAttributes.count(m)
                    for i in range(1, num_ca + 1):
                        ca_def = rt.custAttributes.get(m, i)
                        if ca_def: ca_defs.append((m, i, ca_def))
                except: pass
    except: pass
    return ca_defs

def get_ca_parameters(ca_def):
    params = []
    try:
        props = rt.getPropNames(ca_def)
        for prop in props:
            try:
                ctrl = rt.getPropertyController(ca_def, prop)
                if ctrl: params.append(prop)
            except: pass
    except: pass
    return params

def get_ca_controller(owner, ca_index, param_name):
    try:
        ca_def = rt.custAttributes.get(owner, ca_index)
        if ca_def: return rt.getPropertyController(ca_def, param_name)
    except: pass
    return None
    
def detect_center_rotation_flips(obj):
    """
    Detect which rotation axes need to flip for a center controller.
    Uses actual movement testing to handle any axis order correctly.
    """
    try:
        def get_euler_ctrl(o):
            try:
                rot = rt.getPropertyController(o.controller, "Rotation")
                if "list" in str(rt.classOf(rot)).lower():
                    item = rot[1]
                    if hasattr(item, 'controller'):
                        return item.controller
                    return item
                return rot
            except:
                return None
        
        def get_xyz_values(ctrl):
            if ctrl is None:
                return None
            try:
                vals = [0.0, 0.0, 0.0]
                for i in range(3):
                    sub = ctrl[i]
                    if hasattr(sub, 'value'):
                        vals[i] = float(sub.value)
                return vals
            except:
                return None
        
        def set_xyz_values(ctrl, vals):
            if ctrl is None:
                return False
            try:
                for i in range(3):
                    sub = ctrl[i]
                    if hasattr(sub, 'value'):
                        sub.value = vals[i]
                return True
            except:
                return False
        
        ctrl = get_euler_ctrl(obj)
        if ctrl is None:
            return [False, True, True]
        
        saved = get_xyz_values(ctrl)
        if saved is None:
            return [False, True, True]
        
        # Zero it
        set_xyz_values(ctrl, [0.0, 0.0, 0.0])
        
        # Get a reference point
        def get_test_point():
            try:
                if obj.children and len(obj.children) > 0:
                    return obj.children[0].transform.position
                tm = obj.transform
                return rt.Point3(
                    tm.position.x + tm.row2.x * 10,
                    tm.position.y + tm.row2.y * 10,
                    tm.position.z + tm.row2.z * 10
                )
            except:
                return obj.transform.position
        
        zero_point = get_test_point()
        
        flips = [False, False, False]
        
        for axis in range(3):
            # Rotate +30 on this axis
            test_rot = [0.0, 0.0, 0.0]
            test_rot[axis] = 30.0
            set_xyz_values(ctrl, test_rot)
            
            pos_point = get_test_point()
            
            # Rotate -30 on this axis
            test_rot[axis] = -30.0
            set_xyz_values(ctrl, test_rot)
            
            neg_point = get_test_point()
            
            # Reset
            set_xyz_values(ctrl, [0.0, 0.0, 0.0])
            
            # Calculate movement vectors
            pos_move = rt.Point3(
                pos_point.x - zero_point.x,
                pos_point.y - zero_point.y,
                pos_point.z - zero_point.z
            )
            neg_move = rt.Point3(
                neg_point.x - zero_point.x,
                neg_point.y - zero_point.y,
                neg_point.z - zero_point.z
            )
            
            # Mirror the positive movement across YZ plane
            pos_move_mirrored = rt.Point3(-pos_move.x, pos_move.y, pos_move.z)
            
            # Which is closer to the mirrored movement?
            dist_same = rt.distance(pos_move_mirrored, pos_move)
            dist_neg = rt.distance(pos_move_mirrored, neg_move)
            
            flips[axis] = (dist_neg < dist_same)
        
        # Restore
        set_xyz_values(ctrl, saved)
        
        return flips
    except:
        return [False, True, True]

# ============================================================================
# IMPROVED SNAPSHOT SYSTEM WITH AXIS ORDER SUPPORT
# ============================================================================

def get_euler_order(ctrl):
    """
    Get the Euler rotation order from a controller.
    Returns: int (1-6) or None
    Orders: 1=XYZ, 2=XZY, 3=YZX, 4=YXZ, 5=ZXY, 6=ZYX
    """
    try:
        # Check if it's an Euler controller
        ctrl_class = str(rt.classOf(ctrl)).lower()
        
        if 'euler' in ctrl_class:
            # Try to get axisOrder property
            if rt.isProperty(ctrl, rt.Name("axisOrder")):
                order = rt.getProperty(ctrl, rt.Name("axisOrder"))
                return int(order)
        
        # For Euler_XYZ specifically
        if hasattr(ctrl, 'axisOrder'):
            return int(ctrl.axisOrder)
        
        # Default to XYZ (order 1)
        return 1
    except:
        return 1  # Default XYZ


def get_euler_order_name(order_int):
    """Convert order integer to readable name."""
    orders = {
        1: "XYZ",
        2: "XZY",
        3: "YZX",
        4: "YXZ",
        5: "ZXY",
        6: "ZYX"
    }
    return orders.get(order_int, "XYZ")


def get_axis_indices(order_int):
    """
    Get the axis indices for a given Euler order.
    Returns tuple of (first, second, third) axis indices.
    X=0, Y=1, Z=2
    """
    orders = {
        1: (0, 1, 2),  # XYZ
        2: (0, 2, 1),  # XZY
        3: (1, 2, 0),  # YZX
        4: (1, 0, 2),  # YXZ
        5: (2, 0, 1),  # ZXY
        6: (2, 1, 0),  # ZYX
    }
    return orders.get(order_int, (0, 1, 2))


# ============================================================================
# SNAPSHOT DATA CLASS - EXTENDED
# ============================================================================

class SnapshotController:
    """Stores data for a single controller in a snapshot."""
    __slots__ = ['obj', 'ctrl', 'prop', 'axis_idx', 
                 'value', 'is_xyz', 'euler_order',
                 'sub_values', 'world_pos', 'world_rot']


class Snapshot:
    """Complete pose snapshot with axis order information."""
    
    def __init__(self):
        self.name = ""
        self.time = 0
        self.controllers = []  # List of SnapshotController
        self.object_transforms = {}  # {obj_name: transform_matrix}
    
    def capture(self, objects=None):
        """Capture current pose of selected or specified objects."""
        if objects is None:
            if rt.selection.count == 0:
                return False
            objects = list(rt.selection)
        
        self.time = int(rt.currentTime)
        self.controllers = []
        self.object_transforms = {}
        
        for obj in objects:
            # Store world transform
            try:
                self.object_transforms[str(obj.name)] = rt.copy(obj.transform)
            except:
                pass
            
            # Capture each property
            for prop in ["position", "rotation", "scale"]:
                ctrl = get_controller(obj, prop)
                if ctrl is None:
                    continue
                
                if is_xyz_controller(ctrl) or (prop == "rotation" and is_euler_rotation(ctrl)):
                    # XYZ or Euler controller - capture each axis
                    euler_order = 1
                    if prop == "rotation":
                        euler_order = get_euler_order(ctrl)
                    
                    for i in range(3):
                        try:
                            sub = ctrl[i]
                            sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                            sub_ctrl = resolve_controller(sub_ctrl)
                            
                            if sub_ctrl is None:
                                continue
                            
                            sc = SnapshotController()
                            sc.obj = obj
                            sc.ctrl = sub_ctrl
                            sc.prop = prop
                            sc.axis_idx = i
                            sc.value = float(sub_ctrl.value)
                            sc.is_xyz = True
                            sc.euler_order = euler_order
                            
                            self.controllers.append(sc)
                        except:
                            pass
                else:
                    # Single value controller
                    try:
                        resolved = resolve_controller(ctrl)
                        if resolved:
                            sc = SnapshotController()
                            sc.obj = obj
                            sc.ctrl = resolved
                            sc.prop = prop
                            sc.axis_idx = -1
                            sc.value = float(resolved.value)
                            sc.is_xyz = False
                            sc.euler_order = 1
                            
                            self.controllers.append(sc)
                    except:
                        pass
        
        return len(self.controllers) > 0
    
    def apply(self, blend=1.0):
        """Apply snapshot to objects with optional blend."""
        if not self.controllers:
            return False
        
        with pymxs.animate(True):
            for sc in self.controllers:
                try:
                    if blend >= 1.0:
                        sc.ctrl.value = sc.value
                    else:
                        current = float(sc.ctrl.value)
                        sc.ctrl.value = lerp(current, sc.value, blend)
                except:
                    pass
        
        rt.redrawViews()
        return True
    
    def apply_mirrored(self, mirror_axis='x', blend=1.0):
        """
        Apply snapshot with mirroring, respecting axis order.
        mirror_axis: 'x', 'y', or 'z'
        """
        if not self.controllers:
            return False
        
        axis_map = {'x': 0, 'y': 1, 'z': 2}
        mirror_idx = axis_map.get(mirror_axis.lower(), 0)
        
        with pymxs.animate(True):
            for sc in self.controllers:
                try:
                    value = sc.value
                    
                    if sc.prop == "position":
                        # Mirror position on the specified axis
                        if sc.axis_idx == mirror_idx:
                            value = -value
                    
                    elif sc.prop == "rotation":
                        # Mirror rotation considering axis order
                        # Get the actual axis this controller represents
                        axis_indices = get_axis_indices(sc.euler_order)
                        actual_axis = axis_indices[sc.axis_idx] if sc.axis_idx < 3 else sc.axis_idx
                        
                        # Determine which rotations to negate based on mirror axis
                        if mirror_idx == 0:  # Mirror X
                            # Negate Y and Z rotations
                            if actual_axis in [1, 2]:
                                value = -value
                        elif mirror_idx == 1:  # Mirror Y
                            # Negate X and Z rotations
                            if actual_axis in [0, 2]:
                                value = -value
                        elif mirror_idx == 2:  # Mirror Z
                            # Negate X and Y rotations
                            if actual_axis in [0, 1]:
                                value = -value
                    
                    # Apply with blend
                    if blend >= 1.0:
                        sc.ctrl.value = value
                    else:
                        current = float(sc.ctrl.value)
                        sc.ctrl.value = lerp(current, value, blend)
                
                except:
                    pass
        
        rt.redrawViews()
        return True


# ============================================================================
# SNAPSHOT STORAGE
# ============================================================================

_snapshots = {
    'A': None,
    'B': None,
    'temp': None  # For internal use (like undo)
}


def capture_snapshot(slot='A'):
    """Capture current pose to a snapshot slot."""
    global _snapshots
    
    snap = Snapshot()
    if snap.capture():
        _snapshots[slot] = snap
        print(f"Snapshot {slot}: Captured {len(snap.controllers)} controllers")
        return True
    else:
        print(f"Snapshot {slot}: Nothing to capture")
        return False


def apply_snapshot(slot='A', blend=1.0, mirrored=False, mirror_axis='x'):
    """Apply a stored snapshot."""
    global _snapshots
    
    snap = _snapshots.get(slot)
    if snap is None:
        print(f"Snapshot {slot}: Empty")
        return False
    
    if mirrored:
        snap.apply_mirrored(mirror_axis, blend)
        print(f"Snapshot {slot}: Applied mirrored ({mirror_axis} axis)")
    else:
        snap.apply(blend)
        print(f"Snapshot {slot}: Applied")
    
    return True


def blend_snapshots(slot_a='A', slot_b='B', blend=0.5):
    """Blend between two snapshots."""
    global _snapshots
    
    snap_a = _snapshots.get(slot_a)
    snap_b = _snapshots.get(slot_b)
    
    if snap_a is None or snap_b is None:
        print("Both snapshots must be captured first")
        return False
    
    # Build lookup for snap_b values
    b_values = {}
    for sc in snap_b.controllers:
        key = (str(sc.obj.name), sc.prop, sc.axis_idx)
        b_values[key] = sc.value
    
    # Apply blended values
    with pymxs.animate(True):
        for sc in snap_a.controllers:
            try:
                key = (str(sc.obj.name), sc.prop, sc.axis_idx)
                
                val_a = sc.value
                val_b = b_values.get(key, val_a)
                
                blended = lerp(val_a, val_b, blend)
                sc.ctrl.value = blended
            except:
                pass
    
    rt.redrawViews()
    return True


# ============================================================================
# DEBUG: Show axis order for selected objects
# ============================================================================

def show_axis_orders():
    """Display axis order information for selected objects."""
    if rt.selection.count == 0:
        print("Select objects first")
        return
    
    print(f"\n{'='*60}")
    print("AXIS ORDER INFORMATION")
    print(f"{'='*60}")
    
    for obj in rt.selection:
        print(f"\n{obj.name}:")
        
        rot_ctrl = get_controller(obj, "rotation")
        if rot_ctrl is None:
            print("  No rotation controller")
            continue
        
        ctrl_class = str(rt.classOf(rot_ctrl))
        print(f"  Controller: {ctrl_class}")
        
        if is_euler_rotation(rot_ctrl):
            order = get_euler_order(rot_ctrl)
            order_name = get_euler_order_name(order)
            print(f"  Euler Order: {order} ({order_name})")
            
            # Show individual axes
            for i in range(3):
                try:
                    sub = rot_ctrl[i]
                    sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                    sub_ctrl = resolve_controller(sub_ctrl)
                    if sub_ctrl:
                        axis_name = ['X', 'Y', 'Z'][i]
                        print(f"    Axis {i} ({axis_name}): {float(sub_ctrl.value):.2f}")
                except:
                    pass
        else:
            print("  Not an Euler controller")
    
    print(f"\n{'='*60}\n")

show_axis_orders()

# ============================================================================
# CACHE & TWEEN LOGIC
# ============================================================================

def build_cache():
    global _cache
    _cache['valid'] = False; _cache['items'] = []; _cache['ca_items'] = []; _cache['obj_items'] = {}
    if rt.selection.count == 0: return
    
    ct = rt.currentTime
    ct_int = int(ct)
    _cache['ct'] = ct
    
    for obj in rt.selection:
        # Transforms
        for prop in ["position", "rotation", "scale"]:
            ctrl = get_controller(obj, prop)
            
            if ctrl is None: continue
            if not controller_has_keys(ctrl): continue
            
            key_times = get_all_key_times(ctrl)
            if len(key_times) < 2: continue
            
            prev_key = next((t for t in reversed(key_times) if t < ct_int), None)
            next_key = next((t for t in key_times if t > ct_int), None)
            
            if prev_key is None or next_key is None: continue
            
            data = TweenData()
            data.obj = obj; data.prop = prop; data.ctrl = ctrl
            data.is_euler = (prop == "rotation" and is_euler_rotation(ctrl))
            data.is_xyz = is_xyz_controller(ctrl)
            data.prev_key = prev_key; data.next_key = next_key
            
            if data.is_xyz:
                sub_ctrls = []
                for i in range(3):
                    try:
                        sub = ctrl[i]
                        sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                        sub_ctrl = resolve_controller(sub_ctrl)
                        sub_ctrls.append(sub_ctrl)
                    except: sub_ctrls.append(None)
                data.sub_ctrls = sub_ctrls
                
                data.prev_val = [0.0]*3; data.next_val = [0.0]*3; data.orig_val = [0.0]*3
                
                with pymxs.attime(prev_key):
                    for i, sc in enumerate(sub_ctrls):
                        if sc: data.prev_val[i] = float(sc.value)
                with pymxs.attime(next_key):
                    for i, sc in enumerate(sub_ctrls):
                        if sc: data.next_val[i] = float(sc.value)
                with pymxs.attime(ct_int):
                    for i, sc in enumerate(sub_ctrls):
                        if sc: data.orig_val[i] = float(sc.value)
            else:
                data.sub_ctrls = None
                with pymxs.attime(prev_key):
                    try: data.prev_val = rt.copy(ctrl.value)
                    except: data.prev_val = None
                with pymxs.attime(next_key):
                    try: data.next_val = rt.copy(ctrl.value)
                    except: data.next_val = None
                with pymxs.attime(ct_int):
                    try: data.orig_val = rt.copy(ctrl.value)
                    except: data.orig_val = None
            _cache['items'].append(data)
        
        # CAs
        for ca_def in get_all_custom_attribute_defs(obj):
            owner = ca_def[0]; ca_index = ca_def[1]; ca_obj = ca_def[2]
            for param_name in get_ca_parameters(ca_obj):
                ctrl = get_ca_controller(owner, ca_index, param_name)
                if ctrl is None or not controller_has_keys(ctrl): continue
                key_times = sorted(get_all_key_times(ctrl))
                if len(key_times) < 2: continue
                prev_key = next((t for t in reversed(key_times) if t < ct_int), None)
                next_key = next((t for t in key_times if t > ct_int), None)
                if prev_key is None or next_key is None: continue
                with pymxs.attime(prev_key):
                    try: ca = rt.custAttributes.get(owner, ca_index); prev_val = float(rt.getProperty(ca, param_name))
                    except: continue
                with pymxs.attime(next_key):
                    try: ca = rt.custAttributes.get(owner, ca_index); next_val = float(rt.getProperty(ca, param_name))
                    except: continue
                with pymxs.attime(ct_int):
                    try: ca = rt.custAttributes.get(owner, ca_index); orig_val = float(rt.getProperty(ca, param_name))
                    except: orig_val = prev_val
                _cache['ca_items'].append({'owner': owner, 'ca_index': ca_index, 'param_name': param_name, 'prev_val': prev_val, 'next_val': next_val, 'orig_val': orig_val})

    _cache['valid'] = len(_cache['items']) > 0 or len(_cache['ca_items']) > 0
    obj_items = {}
    for data in _cache['items']:
        name = str(data.obj.name)
        if name not in obj_items: obj_items[name] = {'obj':data.obj, 'position':None, 'rotation':None, 'scale':None}
        obj_items[name][data.prop] = data
    _cache['obj_items'] = obj_items

def lerp(a, b, t): return a + (b - a) * t
def lerp3(a, b, t): return [a[0]+(b[0]-a[0])*t, a[1]+(b[1]-a[1])*t, a[2]+(b[2]-a[2])*t]

def apply_cached_tween(tween_amount, mode):
    global _cache
    if not _cache['valid']: return False
    ct = rt.currentTime
    
    # Determine tween type based on mode
    if mode == 1:   # Tween
        tween_type = 'lerp'
        t = (tween_amount + 1.0) / 2.0
    elif mode == 2: # Space
        tween_type = 'space'
        t = (tween_amount + 1.0) / 2.0
    elif mode == 3: # Offset
        tween_type = 'offset'
        t = (tween_amount + 1.0) / 2.0
    elif mode == 5: # Default (blend to zero/rest)
        tween_type = 'default'
        t = abs(tween_amount)
    elif mode == 6: # Push Pull (Force)
        tween_type = 'pushpull'
        t = tween_amount  # -1 to +1, negative = pull toward average, positive = push away
    else:
        tween_type = 'lerp'
        t = (tween_amount + 1.0) / 2.0
    
    with pymxs.attime(ct):
        with pymxs.animate(True):
            for items in _cache['obj_items'].values():
                obj = items['obj']
                
                # POSITION
                if items['position']:
                    d = items['position']
                    if tween_type == 'offset':
                        diff = [(d.next_val[i] - d.prev_val[i]) * t for i in range(3)]
                        res = [d.prev_val[i] + diff[i] for i in range(3)]
                    elif tween_type == 'default':
                        res = lerp3(d.orig_val, [0, 0, 0], t)
                    elif tween_type == 'pushpull':
                        # Calculate average of prev and next
                        avg = [(d.prev_val[i] + d.next_val[i]) / 2.0 for i in range(3)]
                        # Calculate how far original value is from average
                        diff_from_avg = [d.orig_val[i] - avg[i] for i in range(3)]
                        # Scale: t=-1 -> 0 (average), t=0 -> 1 (original), t=+1 -> 2 (exaggerated)
                        scale = 1.0 + t
                        res = [avg[i] + diff_from_avg[i] * scale for i in range(3)]
                    else:  # lerp
                        res = lerp3(d.prev_val, d.next_val, t)
                    
                    if d.is_xyz and d.sub_ctrls:
                        for i, sc in enumerate(d.sub_ctrls):
                            if sc: sc.value = res[i]
                    else:
                        d.ctrl.value = rt.Point3(res[0], res[1], res[2])
                
                # ROTATION
                if items['rotation']:
                    d = items['rotation']
                    if d.is_euler and d.is_xyz and d.sub_ctrls:
                        if tween_type == 'offset':
                            diff = [(d.next_val[i] - d.prev_val[i]) * t for i in range(3)]
                            res = [d.prev_val[i] + diff[i] for i in range(3)]
                        elif tween_type == 'default':
                            res = lerp3(d.orig_val, [0, 0, 0], t)
                        elif tween_type == 'pushpull':
                            avg = [(d.prev_val[i] + d.next_val[i]) / 2.0 for i in range(3)]
                            diff_from_avg = [d.orig_val[i] - avg[i] for i in range(3)]
                            scale = 1.0 + t
                            res = [avg[i] + diff_from_avg[i] * scale for i in range(3)]
                        else:  # lerp
                            res = lerp3(d.prev_val, d.next_val, t)
                        
                        for i, sc in enumerate(d.sub_ctrls):
                            if sc: sc.value = res[i]
                    else:
                        # Quaternion rotation
                        if tween_type == 'default':
                            res = rt.slerp(d.orig_val, rt.quat(0, 0, 0, 1), t)
                        elif tween_type == 'pushpull':
                            # For quaternions: slerp between average and exaggerated
                            avg_quat = rt.slerp(d.prev_val, d.next_val, 0.5)
                            if t >= 0:
                                # Push: go beyond original (slerp from avg through orig)
                                res = rt.slerp(avg_quat, d.orig_val, 1.0 + t)
                            else:
                                # Pull: toward average
                                res = rt.slerp(d.orig_val, avg_quat, abs(t))
                        else:  # lerp
                            res = rt.slerp(d.prev_val, d.next_val, t)
                        d.ctrl.value = res
                
                # SCALE
                if items['scale']:
                    d = items['scale']
                    if tween_type == 'offset':
                        diff = [(d.next_val[i] - d.prev_val[i]) * t for i in range(3)]
                        res = [d.prev_val[i] + diff[i] for i in range(3)]
                    elif tween_type == 'default':
                        res = lerp3(d.orig_val, [1, 1, 1], t)
                    elif tween_type == 'pushpull':
                        avg = [(d.prev_val[i] + d.next_val[i]) / 2.0 for i in range(3)]
                        diff_from_avg = [d.orig_val[i] - avg[i] for i in range(3)]
                        scale = 1.0 + t
                        res = [avg[i] + diff_from_avg[i] * scale for i in range(3)]
                    else:  # lerp
                        res = lerp3(d.prev_val, d.next_val, t)
                    
                    if d.is_xyz and d.sub_ctrls:
                        for i, sc in enumerate(d.sub_ctrls):
                            if sc: rt.addNewKey(sc, ct).value = res[i]
                    else:
                        if d.ctrl: rt.addNewKey(d.ctrl, ct)
                        d.ctrl.value = rt.Point3(res[0], res[1], res[2])
            
            # CUSTOM ATTRIBUTES
            for ca_data in _cache['ca_items']:
                prev, nxt, orig = ca_data['prev_val'], ca_data['next_val'], ca_data['orig_val']
                
                if tween_type == 'offset':
                    result = prev + ((nxt - prev) * t)
                elif tween_type == 'default':
                    result = lerp(orig, 0.0, t)
                elif tween_type == 'pushpull':
                    avg = (prev + nxt) / 2.0
                    diff_from_avg = orig - avg
                    scale = 1.0 + t
                    result = avg + diff_from_avg * scale
                else:  # lerp
                    result = lerp(prev, nxt, t)
                
                try:
                    ca = rt.custAttributes.get(ca_data['owner'], ca_data['ca_index'])
                    rt.setProperty(ca, ca_data['param_name'], result)
                except: pass
    
    rt.completeRedraw()
    return True

def finalize_selected_keys(tween_amount, mode):
    if mode == 3:  # Offset mode
        return apply_time_offset(tween_amount)
    if mode == 4:
        return do_ease(tween_amount)
    if mode == 6:
        return apply_pushpull(tween_amount)
    if _cache['valid']:
        return apply_cached_tween(tween_amount, mode)
    build_cache()
    if _cache['valid']:
        result = apply_cached_tween(tween_amount, mode)
        clear_cache()
        return result
    return False
# ============================================================================
# TIME OFFSET - Wave Riding (Fixed Start/End Tangents)
# ============================================================================
_offset_cache = {'valid': False, 'controllers': []}

def clear_offset_cache():
    global _offset_cache
    _offset_cache = {'valid': False, 'controllers': []}


def build_offset_cache():
    """
    Cache the wave pattern from selected keys.
    """
    global _offset_cache
    _offset_cache = {'valid': False, 'controllers': []}
    
    if rt.selection.count == 0:
        return
    
    for obj in rt.selection:
        for prop in ["position", "rotation", "scale"]:
            ctrl = get_controller(obj, prop)
            if ctrl is None:
                continue
            
            if is_xyz_controller(ctrl):
                for i in range(3):
                    try:
                        sub = ctrl[i]
                        sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                        sub_ctrl = resolve_controller(sub_ctrl)
                        if sub_ctrl:
                            _cache_wave_controller(sub_ctrl)
                    except:
                        pass
            else:
                _cache_wave_controller(ctrl)
    
    _offset_cache['valid'] = len(_offset_cache['controllers']) > 0
    print(f"[Offset] Cached {len(_offset_cache['controllers'])} controllers")


def _cache_wave_controller(ctrl):
    """
    Cache the wave pattern with VALUE and SLOPE at each sample point.
    """
    global _offset_cache
    
    if ctrl is None:
        return
    
    try:
        num_keys = rt.numKeys(ctrl)
        if num_keys < 2:
            return
    except:
        return
    
    # Find keys
    all_keys = []
    selected_keys = []
    
    for k_idx in range(1, num_keys + 1):
        try:
            key = rt.getKey(ctrl, k_idx)
            val = key.value
            
            if not isinstance(val, (int, float)):
                return
            
            key_data = {
                'index': k_idx,
                'time': float(key.time),
                'value': float(val),
                'selected': key.selected
            }
            all_keys.append(key_data)
            
            if key.selected:
                selected_keys.append(key_data)
        except:
            pass
    
    if len(all_keys) < 2 or len(selected_keys) < 1:
        return
    
    all_keys.sort(key=lambda x: x['time'])
    selected_keys.sort(key=lambda x: x['time'])
    
    # Wave range
    first_time = all_keys[0]['time']
    last_time = all_keys[-1]['time']
    wave_duration = last_time - first_time
    
    if wave_duration < 1:
        return
    
    # Sample the wave with VALUE and SLOPE
    sample_step = 0.1
    delta = 0.05  # For slope calculation
    base_wave = []
    
    t = first_time
    while t <= last_time + sample_step:
        try:
            with pymxs.attime(t):
                val = float(ctrl.value)
            
            # Calculate slope at this point
            with pymxs.attime(t - delta):
                val_before = float(ctrl.value)
            with pymxs.attime(t + delta):
                val_after = float(ctrl.value)
            
            slope = (val_after - val_before) / (delta * 2.0)
            
            base_wave.append({
                'local_time': t - first_time,
                'value': val,
                'slope': slope
            })
        except:
            pass
        t += sample_step
    
    if len(base_wave) < 10:
        return
    
    # Find first and last selected key indices
    first_sel_idx = selected_keys[0]['index']
    last_sel_idx = selected_keys[-1]['index']
    
    _offset_cache['controllers'].append({
        'ctrl': ctrl,
        'all_keys': all_keys,
        'selected_keys': selected_keys,
        'base_wave': base_wave,
        'first_time': first_time,
        'last_time': last_time,
        'wave_duration': wave_duration,
        'first_sel_idx': first_sel_idx,
        'last_sel_idx': last_sel_idx,
        'num_keys': num_keys
    })


def _sample_wave_at_local_time(base_wave, wave_duration, local_time):
    """
    Sample the wave VALUE at a local time, with looping.
    Returns: value
    """
    if not base_wave or wave_duration < 0.001:
        return 0.0
    
    # Wrap to [0, wave_duration]
    wrapped_time = local_time % wave_duration
    if wrapped_time < 0:
        wrapped_time += wave_duration
    
    # Find bracketing samples
    best_before = base_wave[0]
    best_after = base_wave[-1]
    
    for sample in base_wave:
        if sample['local_time'] <= wrapped_time:
            best_before = sample
        if sample['local_time'] >= wrapped_time:
            best_after = sample
            break
    
    # Interpolate value
    t_range = best_after['local_time'] - best_before['local_time']
    if t_range < 0.001:
        return best_before['value']
    
    ratio = (wrapped_time - best_before['local_time']) / t_range
    return best_before['value'] + (best_after['value'] - best_before['value']) * ratio


def _sample_wave_slope_at_local_time(base_wave, wave_duration, local_time):
    """
    Sample the wave SLOPE at a local time, with looping.
    Returns: slope (value change per frame)
    """
    if not base_wave or wave_duration < 0.001:
        return 0.0
    
    # Wrap to [0, wave_duration]
    wrapped_time = local_time % wave_duration
    if wrapped_time < 0:
        wrapped_time += wave_duration
    
    # Find bracketing samples
    best_before = base_wave[0]
    best_after = base_wave[-1]
    
    for sample in base_wave:
        if sample['local_time'] <= wrapped_time:
            best_before = sample
        if sample['local_time'] >= wrapped_time:
            best_after = sample
            break
    
    # Interpolate slope
    t_range = best_after['local_time'] - best_before['local_time']
    if t_range < 0.001:
        return best_before['slope']
    
    ratio = (wrapped_time - best_before['local_time']) / t_range
    return best_before['slope'] + (best_after['slope'] - best_before['slope']) * ratio


def apply_time_offset(amount):
    """
    WAVE RIDING OFFSET with smart tangent handling.
    
    - Middle keys: Use smooth/auto tangents (Max figures it out)
    - Start/End keys: Manually set tangent slope to match wave shape
    """
    global _offset_cache
    
    if not _offset_cache['valid']:
        return "No offset cache"
    
    MAX_OFFSET_FRAMES = 20.0
    frame_offset = amount * MAX_OFFSET_FRAMES
    
    total_count = 0
    
    for ctrl_data in _offset_cache['controllers']:
        ctrl = ctrl_data['ctrl']
        selected_keys = ctrl_data['selected_keys']
        base_wave = ctrl_data['base_wave']
        wave_duration = ctrl_data['wave_duration']
        first_time = ctrl_data['first_time']
        first_sel_idx = ctrl_data['first_sel_idx']
        last_sel_idx = ctrl_data['last_sel_idx']
        num_keys = ctrl_data['num_keys']
        
        for key_info in selected_keys:
            try:
                key_idx = key_info['index']
                key_time = key_info['time']
                
                # Calculate new local time on the wave
                original_local_time = key_time - first_time
                new_local_time = original_local_time - frame_offset
                
                # Sample value from looping wave
                new_value = _sample_wave_at_local_time(base_wave, wave_duration, new_local_time)
                
                # Sample slope from looping wave
                new_slope = _sample_wave_slope_at_local_time(base_wave, wave_duration, new_local_time)
                
                # Apply value
                key = rt.getKey(ctrl, key_idx)
                key.value = new_value
                
                # Determine if this is a boundary key (first or last in selection)
                is_first_key = (key_idx == first_sel_idx)
                is_last_key = (key_idx == last_sel_idx)
                is_boundary = is_first_key or is_last_key
                
                # Also check if it's at the actual curve boundary
                is_curve_start = (key_idx == 1)
                is_curve_end = (key_idx == num_keys)
                
                if is_boundary or is_curve_start or is_curve_end:
                    # BOUNDARY KEY: Set custom tangent matching wave slope
                    try:
                        key.inTangentType = rt.Name("custom")
                        key.outTangentType = rt.Name("custom")
                        key.freeHandle = False
                        key.inTangent = new_slope
                        key.outTangent = new_slope
                        
                        # Use moderate tangent length for smooth curve
                        key.inTangentLength = 0.333
                        key.outTangentLength = 0.333
                    except:
                        pass
                else:
                    # MIDDLE KEY: Let Max calculate smooth tangent
                    try:
                        key.inTangentType = rt.Name("smooth")
                        key.outTangentType = rt.Name("smooth")
                    except:
                        pass
                
                total_count += 1
                
            except Exception as e:
                print(f"[Offset] Error: {e}")
    
    rt.redrawViews()
    return total_count


def test_offset_v3():
    """
    Test with boundary tangent fix.
    """
    # Delete old test
    try:
        old = rt.getNodeByName("OffsetTest3")
        if old:
            rt.delete(old)
    except:
        pass
    
    test_obj = rt.Point(name="OffsetTest3", size=10)
    
    # Create sine wave
    with pymxs.animate(True):
        with pymxs.attime(0):
            test_obj.position = rt.Point3(0, 0, 0)
        with pymxs.attime(10):
            test_obj.position = rt.Point3(100, 0, 0)
        with pymxs.attime(20):
            test_obj.position = rt.Point3(0, 0, 0)
        with pymxs.attime(30):
            test_obj.position = rt.Point3(-100, 0, 0)
        with pymxs.attime(40):
            test_obj.position = rt.Point3(0, 0, 0)
    
    rt.select(test_obj)
    
    # Get X controller
    ctrl = get_controller(test_obj, "position")
    sub_ctrl = None
    
    if is_xyz_controller(ctrl):
        sub = ctrl[0]
        sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
        sub_ctrl = resolve_controller(sub_ctrl)
    
    if sub_ctrl is None:
        print("Could not get controller")
        return
    
    # Set all to smooth and select
    for k_idx in range(1, rt.numKeys(sub_ctrl) + 1):
        key = rt.getKey(sub_ctrl, k_idx)
        key.inTangentType = rt.Name("smooth")
        key.outTangentType = rt.Name("smooth")
        key.selected = True
    
    print("Created test - smooth tangents, all keys selected")
    
    # Sample original
    print("\nOriginal curve (key values: 0, 100, 0, -100, 0):")
    for t in [0, 5, 10, 15, 20, 25, 30, 35, 40]:
        with pymxs.attime(t):
            v = float(sub_ctrl.value)
        print(f"  t={t}: {v:.2f}")
    
    # Build cache
    build_offset_cache()
    
    # Test multiple offsets
    test_amounts = [0.125, 0.25, 0.375, 0.5]  # 5, 10, 15, 20 frames
    
    for amt in test_amounts:
        frames = amt * 20
        print(f"\n{'='*50}")
        print(f"Offset: {amt} ({frames:.0f} frames)")
        print(f"{'='*50}")
        
        # Reset first
        for k_idx in range(1, rt.numKeys(sub_ctrl) + 1):
            key = rt.getKey(sub_ctrl, k_idx)
            key.inTangentType = rt.Name("smooth")
            key.outTangentType = rt.Name("smooth")
            key.selected = True
        
        with pymxs.attime(0): sub_ctrl.value = 0
        with pymxs.attime(10): sub_ctrl.value = 100
        with pymxs.attime(20): sub_ctrl.value = 0
        with pymxs.attime(30): sub_ctrl.value = -100
        with pymxs.attime(40): sub_ctrl.value = 0
        
        # Rebuild cache with fresh curve
        build_offset_cache()
        
        # Apply offset
        apply_time_offset(amt)
        
        # Sample and check
        max_v = -99999
        min_v = 99999
        for t in range(0, 41):
            with pymxs.attime(t):
                v = float(sub_ctrl.value)
            max_v = max(max_v, v)
            min_v = min(min_v, v)
        
        amplitude = max_v - min_v
        print(f"  Max: {max_v:.2f}, Min: {min_v:.2f}")
        print(f"  Amplitude: {amplitude:.2f} (target: 200)")
        
        # Show key values
        print("  Key values:")
        for k_idx in range(1, 6):
            key = rt.getKey(sub_ctrl, k_idx)
            print(f"    Key {k_idx}: {key.value:.2f}")
# ============================================================================
# PUSH/PULL CACHE AND FUNCTIONS (Add after the existing _cache definition)
# ============================================================================
_pushpull_cache = {'valid': False, 'controllers': []}

def clear_pushpull_cache():
    global _pushpull_cache
    _pushpull_cache = {'valid': False, 'controllers': []}

def build_pushpull_cache():
    """Cache selected keys for push/pull."""
    global _pushpull_cache
    _pushpull_cache = {'valid': False, 'controllers': []}
    
    if rt.selection.count == 0:
        return
    
    for obj in rt.selection:
        for prop in ["position", "rotation", "scale"]:
            ctrl = get_controller(obj, prop)
            if ctrl is None:
                continue
            
            if is_xyz_controller(ctrl):
                for i in range(3):
                    try:
                        sub = ctrl[i]
                        sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                        sub_ctrl = resolve_controller(sub_ctrl)
                        if sub_ctrl:
                            _cache_controller_keys(sub_ctrl)
                    except:
                        pass
            else:
                _cache_controller_keys(ctrl)
    
    _pushpull_cache['valid'] = len(_pushpull_cache['controllers']) > 0


def _cache_controller_keys(ctrl):
    """Cache selected keys and find neighboring unselected keys for reference line."""
    global _pushpull_cache
    
    if ctrl is None:
        return
    
    try:
        num_keys = rt.numKeys(ctrl)
        if num_keys < 1:
            return
    except:
        return
    
    # Collect ALL keys (selected and unselected)
    all_keys = []
    for k_idx in range(1, num_keys + 1):
        try:
            key = rt.getKey(ctrl, k_idx)
            val = key.value
            if isinstance(val, (int, float)):
                all_keys.append({
                    'index': k_idx,
                    'time': float(key.time),
                    'value': float(val),
                    'selected': key.selected
                })
        except:
            pass
    
    if len(all_keys) < 2:
        return
    
    # Sort by time
    all_keys.sort(key=lambda x: x['time'])
    
    # Find selected keys
    selected_keys = [k for k in all_keys if k['selected']]
    
    if len(selected_keys) < 1:
        return
    
    # Find the key BEFORE first selected (reference A)
    first_selected_time = selected_keys[0]['time']
    last_selected_time = selected_keys[-1]['time']
    
    ref_before = None
    ref_after = None
    
    for k in all_keys:
        if k['time'] < first_selected_time:
            ref_before = k  # Keep updating - we want the closest one before
        if k['time'] > last_selected_time and ref_after is None:
            ref_after = k  # First one after
            break
    
    # If no key before, use first selected key as anchor
    if ref_before is None:
        ref_before = selected_keys[0]
    
    # If no key after, use last selected key as anchor
    if ref_after is None:
        ref_after = selected_keys[-1]
    
    # Line from ref_before to ref_after
    time_a = ref_before['time']
    time_b = ref_after['time']
    value_a = ref_before['value']
    value_b = ref_after['value']
    
    time_range = time_b - time_a
    if time_range < 0.001:
        return
    
    # Calculate line value and offset for each SELECTED key
    cached_keys = []
    for key_data in selected_keys:
        t = key_data['time']
        ratio = (t - time_a) / time_range
        line_value = value_a + ratio * (value_b - value_a)
        
        cached_keys.append({
            'index': key_data['index'],
            'time': t,
            'original_value': key_data['value'],
            'line_value': line_value,
            'offset': key_data['value'] - line_value
        })
    
    _pushpull_cache['controllers'].append({
        'ctrl': ctrl,
        'keys': cached_keys
    })


def apply_pushpull(amount):
    """
    Push/Pull - scale selected keys relative to line between neighboring keys.
    
    Pull (-100%): All selected keys ON the line (straight from before to after)
    Push (+100%): All selected keys 2x further from line (exaggerated)
    """
    global _pushpull_cache
    
    if not _pushpull_cache['valid']:
        return "No keys cached"
    
    scale = 1.0 + amount
    count = 0
    
    for ctrl_data in _pushpull_cache['controllers']:
        ctrl = ctrl_data['ctrl']
        
        for key_data in ctrl_data['keys']:
            try:
                key = rt.getKey(ctrl, key_data['index'])
                new_value = key_data['line_value'] + (key_data['offset'] * scale)
                key.value = new_value
                count += 1
            except:
                pass
    
    rt.redrawViews()
    return count

# ============================================================================
# Action Functions
# ============================================================================

def do_key_hammer():
    if rt.selection.count == 0: return "Select objects first"
    count = 0
    for obj in rt.selection:
        all_key_times = []
        for prop in ["position", "rotation", "scale"]:
            ctrl = get_controller(obj, prop)
            if ctrl and controller_has_keys(ctrl):
                times = get_all_key_times(ctrl)
                for kt in times:
                    if kt not in all_key_times: all_key_times.append(kt)
        for key_time in all_key_times:
            for prop in ["position", "rotation", "scale"]:
                ctrl = get_controller(obj, prop)
                if ctrl and controller_has_keys(ctrl) and not has_key_at_time(ctrl, key_time):
                    with pymxs.attime(key_time):
                        with pymxs.animate(True):
                            if prop == "position": obj.position = obj.position
                            elif prop == "rotation": obj.rotation = obj.rotation
                            elif prop == "scale": obj.scale = obj.scale
                    count += 1
    rt.redrawViews()
    return f"Key Hammer: {count} keys"

def ease_keys_on_controller(ctrl, ease_amount):
    if not ctrl or rt.numKeys(ctrl) < 2: return 0
    count = 0; key_data = []
    for k in range(1, rt.numKeys(ctrl)+1):
        try: key = rt.getKey(ctrl, k); key_data.append({'index': k, 'time': float(rt.getKeyTime(ctrl, k)), 'value': float(key.value), 'selected': key.selected})
        except: pass
    if len(key_data) < 2: return 0
    for i, kd in enumerate(key_data):
        if not kd['selected']: continue
        prev_key = key_data[i-1] if i > 0 else None
        next_key = key_data[i+1] if i < len(key_data)-1 else None
        new_val = kd['value']
        if prev_key and next_key:
            tr = next_key['time'] - prev_key['time']
            if abs(tr) > 0.001:
                ratio = (kd['time'] - prev_key['time']) / tr
                lin_val = prev_key['value'] + ratio * (next_key['value'] - prev_key['value'])
                if ease_amount > 0: new_val = lin_val + ease_amount * (next_key['value'] - lin_val)
                else: new_val = lin_val + abs(ease_amount) * (prev_key['value'] - lin_val)
        elif prev_key: new_val = kd['value'] + abs(ease_amount) * (prev_key['value'] - kd['value'])
        elif next_key: new_val = kd['value'] + abs(ease_amount) * (next_key['value'] - kd['value'])
        try: rt.getKey(ctrl, kd['index']).value = new_val; count += 1
        except: pass
    return count

def do_ease(ease_amount):
    if rt.selection.count == 0: return "Select objects"
    if abs(ease_amount) < 0.01: return "Ease 0%"
    count = 0
    for obj in rt.selection:
        for prop in ["position", "rotation", "scale"]:
            ctrl = get_controller(obj, prop)
            if ctrl:
                if is_xyz_controller(ctrl):
                    for i in range(3):
                        try: 
                            sub = ctrl[i]
                            actual = sub.controller if hasattr(sub, 'controller') else sub
                            actual = resolve_controller(actual)
                            count += ease_keys_on_controller(actual, ease_amount)
                        except: pass
                else: count += ease_keys_on_controller(ctrl, ease_amount)
    rt.completeRedraw()
    return f"Ease: {count} keys"

def do_delete_keys(): rt.execute("deleteKeys selection #selection"); rt.redrawViews(); return "Deleted"
def do_nudge(f): rt.execute(f"moveKeys selection {f} #selection"); return "Nudged"

def do_reset_pose():
    if rt.selection.count == 0: return "Select objects"
    ct = rt.currentTime
    with pymxs.attime(ct):
        with pymxs.animate(True):
            for obj in rt.selection:
                ctrl = get_controller(obj, "position")
                if ctrl: 
                    if is_xyz_controller(ctrl): 
                        for i in range(3): 
                            try: ctrl[i].value = 0.0
                            except: pass
                    else: 
                        try: obj.position = rt.Point3(0,0,0)
                        except: pass
                ctrl = get_controller(obj, "rotation")
                if ctrl: 
                    if is_xyz_controller(ctrl): 
                        for i in range(3): 
                            try: ctrl[i].value = 0.0
                            except: pass
                    else: 
                        try: obj.rotation = rt.quat(0,0,0,1)
                        except: pass
                ctrl = get_controller(obj, "scale")
                if ctrl: 
                    if is_xyz_controller(ctrl): 
                        for i in range(3): 
                            try: ctrl[i].value = 1.0
                            except: pass
                    else: 
                        try: obj.scale = rt.Point3(1,1,1)
                        except: pass
    rt.completeRedraw()
    return "Reset Done"

def do_set_key():
    if rt.selection.count == 0: return "Select objects first"
    ct = rt.currentTime
    count = 0
    
    def get_derivative(ctrl, t):
        try:
            if rt.classOf(ctrl.value) == rt.Quat: return None
            delta = 0.05
            with pymxs.attime(t - delta): v_prev = ctrl.value
            with pymxs.attime(t + delta): v_next = ctrl.value
            return (v_next - v_prev) / (delta * 2.0)
        except:
            return None

    def is_value_different(val_a, val_b):
        try:
            if hasattr(val_a, "x"): return rt.distance(val_a, val_b) > 0.001
            return abs(val_a - val_b) > 0.001
        except: return True

    with pymxs.animate(True):
        for obj in rt.selection:
            targets = []
            for prop in ["position", "rotation", "scale"]:
                main_ctrl = get_controller(obj, prop)
                if not main_ctrl: continue
                if is_xyz_controller(main_ctrl):
                    for i in range(3):
                        try:
                            sub = main_ctrl[i]
                            c = sub.controller if hasattr(sub, 'controller') and sub.controller else sub
                            c = resolve_controller(c)
                            targets.append(c)
                        except: pass
                else:
                    targets.append(main_ctrl)
            
            for c in targets:
                try:
                    if rt.numKeys(c) == 0:
                        rt.addNewKey(c, ct)
                        count += 1
                        continue

                    with pymxs.attime(ct): curve_val = c.value
                    current_val = c.value 
                    user_moved = is_value_different(curve_val, current_val)
                    
                    if user_moved:
                        rt.addNewKey(c, ct)
                        k_index = rt.getKeyIndex(c, ct)
                        if k_index > 0:
                            key = rt.getKey(c, k_index)
                            if rt.isProperty(key, "inTangentType"):
                                key.inTangentType = rt.Name("auto")
                                key.outTangentType = rt.Name("auto")
                    else:
                        slope = get_derivative(c, ct)
                        rt.insertKey(c, ct)
                        if slope is not None:
                            k_index = rt.getKeyIndex(c, ct)
                            if k_index > 0:
                                key = rt.getKey(c, k_index)
                                if rt.isProperty(key, "inTangent"):
                                    key.inTangentType = rt.Name("custom")
                                    key.outTangentType = rt.Name("custom")
                                    key.freeHandle = False 
                                    key.inTangent = slope
                                    key.outTangent = slope
                    count += 1
                
                except Exception as e:
                    try: rt.addNewKey(c, ct)
                    except: pass

    rt.completeRedraw()
    return f"Smart Key: {count} keys"

# ============================================================================
# ANIM RECOVERY TOOLS
# ============================================================================
class AnimRecoveryTools:
    temp_dir = os.path.join(os.getenv('TEMP'), "Animmix_Recovery")
    if not os.path.exists(temp_dir): os.makedirs(temp_dir)
    
    DEBUG = False 

    @staticmethod
    def log(msg):
        if AnimRecoveryTools.DEBUG:
            print(f"[Animmix Debug] {msg}")

    @staticmethod
    def cleanup_old_files(max_files=20):
        try:
            files = glob.glob(os.path.join(AnimRecoveryTools.temp_dir, "*.max"))
            files.sort(key=os.path.getctime, reverse=True)
            if len(files) > max_files:
                for f in files[max_files:]:
                    try: os.remove(f)
                    except: pass
        except: pass

    @staticmethod
    def create_snapshot():
        if len(rt.objects) == 0: return "Empty Scene"
        tagged_objs = []
        for obj in rt.objects:
            try: 
                rt.setUserProp(obj, "AnimmixHandle", str(obj.inode.handle))
                rt.setUserProp(obj, "AnimmixName", str(obj.name))
                tagged_objs.append(obj)
            except: pass
        ts = datetime.datetime.now().strftime("%Y-%m-%d_%H-%M-%S")
        fname = os.path.join(AnimRecoveryTools.temp_dir, f"recovery_{ts}.max").replace("\\", "/")
        try:
            rt.saveNodes(rt.objects, fname, quiet=True)
            AnimRecoveryTools.cleanup_old_files()
            for obj in tagged_objs:
                try: 
                    rt.deleteUserProp(obj, "AnimmixHandle")
                    rt.deleteUserProp(obj, "AnimmixName")
                except: pass
            return f"Snapshot: {ts}"
        except: return "Save Error"

    @staticmethod
    def restore_snapshot(filepath):
        filepath = filepath.replace("\\", "/")
        if not os.path.exists(filepath): return "File missing"
        old_quiet = rt.getQuietMode()
        rt.setQuietMode(True)
        try:
            rt.clearSelection()
            success = False
            try: success = rt.execute(f'mergeMAXFile "{filepath}" #select #autoRenameDups')
            except: 
                try: success = rt.execute(f'mergeMAXFile "{filepath}" #select')
                except: pass
            if not success:
                rt.setQuietMode(old_quiet)
                return "Merge Failed"
            merged_objs = list(rt.selection)
            if not merged_objs:
                rt.setQuietMode(old_quiet)
                return "No Data"
            restored_list = []
            for obj in merged_objs:
                h_str = rt.getUserProp(obj, "AnimmixHandle")
                n_str = rt.getUserProp(obj, "AnimmixName")
                target = None
                if h_str:
                    try: target = rt.maxOps.getNodeByHandle(int(h_str))
                    except: pass
                if not target and n_str:
                    try: target = rt.getNodeByName(n_str)
                    except: pass
                if target:
                    try:
                        target.controller = rt.copy(obj.controller)
                        restored_list.append(target)
                    except: pass
            rt.delete(merged_objs)
            rt.setQuietMode(old_quiet)
            if restored_list:
                rt.select(restored_list)
                with pymxs.animate(False):
                    for t in restored_list:
                        try: t.transform = t.transform
                        except: pass
                    try: rt.move(rt.selection, rt.Point3(0,0,0))
                    except: pass
            rt.redrawViews()
            return "Restored"
        except:
            rt.setQuietMode(old_quiet)
            return "Error"
    
    @staticmethod
    def get_recent_snapshots(limit=15):
        try:
            files = glob.glob(os.path.join(AnimRecoveryTools.temp_dir, "*.max"))
            files.sort(key=os.path.getctime, reverse=True)
            return files[:limit]
        except: return []

# ============================================================================
# POSE TOOLS (WORLD-SPACE MIRRORING)
# ============================================================================
class SnapshotManager:
    """
    Stores rig snapshots with auto-detected mirror pairs and flip patterns.
    Works with any rig, no naming conventions required.
    """
    
    _snapshots = {}
    _active_snapshot = None
    
    POSITION_TOLERANCE = 0.5
    
    # ========================
    # HELPER METHODS
    # ========================
    
    @staticmethod
    def _get_base_name(name):
        """Extract base name without L/R indicators for comparison."""
        patterns_to_remove = [
            '_Left', '_Right', '_left', '_right', '_LEFT', '_RIGHT',
            '_Lft', '_Rgt', '_lft', '_rgt', '_LFT', '_RGT',
            '_L_', '_R_', '_l_', '_r_',
            '_L', '_R', '_l', '_r',
            'Left_', 'Right_', 'left_', 'right_',
            'L_', 'R_', 'l_', 'r_',
            '.L', '.R', '.l', '.r',
        ]
        
        result = name
        for pattern in patterns_to_remove:
            result = result.replace(pattern, '_SIDE_')
        
        return result
    
    @staticmethod
    def _has_side_indicator(name):
        """Check if name contains L/R indicator."""
        patterns = [
            '_Left', '_Right', '_left', '_right', '_LEFT', '_RIGHT',
            '_Lft', '_Rgt', '_lft', '_rgt', '_LFT', '_RGT',
            '_L_', '_R_', '_l_', '_r_',
            '_L', '_R', '_l', '_r',
            'Left_', 'Right_', 'left_', 'right_',
            'L_', 'R_', 'l_', 'r_',
            '.L', '.R', '.l', '.r',
        ]
        for p in patterns:
            if p in name:
                return True
        return False
    
    @staticmethod
    def _names_match(name_a, name_b):
        """Check if two names are likely the same controller (just L/R different)."""
        base_a = SnapshotManager._get_base_name(name_a)
        base_b = SnapshotManager._get_base_name(name_b)
        return base_a == base_b
    
    @staticmethod
    def _detect_snapshot_name(selection):
        """Try to find common parent name for snapshot."""
        try:
            roots = set()
            for obj in selection:
                root = obj
                while root.parent:
                    root = root.parent
                roots.add(str(root.name))
            
            if len(roots) == 1:
                return list(roots)[0]
            
            parents = {}
            for obj in selection:
                p = obj.parent
                if p:
                    p_name = str(p.name)
                    parents[p_name] = parents.get(p_name, 0) + 1
            
            if parents:
                most_common = max(parents.keys(), key=lambda k: parents[k])
                if parents[most_common] > len(selection) * 0.5:
                    return most_common
        except:
            pass
        
        return None
    
    @staticmethod
    def _detect_rotation_flips(obj_a, obj_b):
        """Detect which rotation axes need to be negated for mirroring."""
        return WorldSpaceMirror.detect_axis_flips_at_zero(obj_a, obj_b)
    
    @staticmethod
    def _detect_position_flips(data_a, data_b):
        """Detect which position axes need to be negated for mirroring."""
        try:
            obj_a = data_a.get("object")
            obj_b = data_b.get("object")
            
            if obj_a is None or obj_b is None:
                return [True, False, False]
            
            def get_pos_ctrl(obj):
                try:
                    pos = rt.getPropertyController(obj.controller, "Position")
                    if pos and rt.isProperty(pos, "count"):
                        item = pos[1]
                        if hasattr(item, 'controller') and item.controller:
                            return item.controller
                        return item
                    return pos
                except:
                    return None
            
            def get_xyz(ctrl):
                if ctrl is None:
                    return None
                try:
                    vals = [0.0, 0.0, 0.0]
                    for i in range(3):
                        sub = ctrl[i]
                        if hasattr(sub, 'value'):
                            vals[i] = float(sub.value)
                    return vals
                except:
                    return None
            
            def set_xyz(ctrl, vals):
                if ctrl is None:
                    return False
                try:
                    for i in range(3):
                        sub = ctrl[i]
                        if hasattr(sub, 'value'):
                            sub.value = vals[i]
                    return True
                except:
                    return False
            
            ctrl_a = get_pos_ctrl(obj_a)
            ctrl_b = get_pos_ctrl(obj_b)
            
            if ctrl_a is None or ctrl_b is None:
                return [True, False, False]
            
            saved_a = get_xyz(ctrl_a)
            saved_b = get_xyz(ctrl_b)
            
            set_xyz(ctrl_a, [0.0, 0.0, 0.0])
            set_xyz(ctrl_b, [0.0, 0.0, 0.0])
            
            zero_a = obj_a.transform.position
            zero_b = obj_b.transform.position
            
            flips = [False, False, False]
            
            for axis in range(3):
                test_pos = [0.0, 0.0, 0.0]
                test_pos[axis] = 5.0
                set_xyz(ctrl_a, test_pos)
                
                new_a = obj_a.transform.position
                move_a = rt.Point3(new_a.x - zero_a.x, new_a.y - zero_a.y, new_a.z - zero_a.z)
                move_a_mir = rt.Point3(-move_a.x, move_a.y, move_a.z)
                
                set_xyz(ctrl_b, test_pos)
                new_b_copy = obj_b.transform.position
                move_b_copy = rt.Point3(new_b_copy.x - zero_b.x, new_b_copy.y - zero_b.y, new_b_copy.z - zero_b.z)
                
                neg_pos = [0.0, 0.0, 0.0]
                neg_pos[axis] = -5.0
                set_xyz(ctrl_b, neg_pos)
                new_b_neg = obj_b.transform.position
                move_b_neg = rt.Point3(new_b_neg.x - zero_b.x, new_b_neg.y - zero_b.y, new_b_neg.z - zero_b.z)
                
                dist_copy = rt.distance(move_b_copy, move_a_mir)
                dist_neg = rt.distance(move_b_neg, move_a_mir)
                
                flips[axis] = (dist_neg < dist_copy)
                
                set_xyz(ctrl_a, [0.0, 0.0, 0.0])
                set_xyz(ctrl_b, [0.0, 0.0, 0.0])
            
            set_xyz(ctrl_a, saved_a)
            set_xyz(ctrl_b, saved_b)
            
            return flips
        except:
            return [True, False, False]
    
    # ========================
    # SET FLIP PATTERNS
    # ========================
    
    @staticmethod
    def set_position_flips(obj_name, flip_x, flip_y, flip_z):
        """
        Manually set position flip pattern for a controller pair.
        
        Args:
            obj_name: Name of one controller in the pair
            flip_x, flip_y, flip_z: True = negate, False = copy
        """
        if not SnapshotManager._active_snapshot:
            return "No active snapshot"
        
        snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
        if not snap:
            return "Snapshot not found"
        
        if obj_name not in snap["controllers"]:
            return f"Controller '{obj_name}' not in snapshot"
        
        flips = [flip_x, flip_y, flip_z]
        
        snap["position_flips"][obj_name] = flips
        
        pair_name = snap["pairs"].get(obj_name)
        if pair_name:
            snap["position_flips"][pair_name] = flips
            print(f"Set position flips for {obj_name} <-> {pair_name}: {flips}")
        else:
            print(f"Set position flips for {obj_name}: {flips}")
        
        return "Flips updated"
    
    @staticmethod
    def set_rotation_flips(obj_name, flip_x, flip_y, flip_z):
        """
        Manually set rotation flip pattern for a controller pair.
        
        Args:
            obj_name: Name of one controller in the pair
            flip_x, flip_y, flip_z: True = negate, False = copy
        """
        if not SnapshotManager._active_snapshot:
            return "No active snapshot"
        
        snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
        if not snap:
            return "Snapshot not found"
        
        if obj_name not in snap["controllers"]:
            return f"Controller '{obj_name}' not in snapshot"
        
        flips = [flip_x, flip_y, flip_z]
        
        snap["rotation_flips"][obj_name] = flips
        
        pair_name = snap["pairs"].get(obj_name)
        if pair_name:
            snap["rotation_flips"][pair_name] = flips
            print(f"Set rotation flips for {obj_name} <-> {pair_name}: {flips}")
        else:
            print(f"Set rotation flips for {obj_name}: {flips}")
        
        return "Flips updated"
    # ========================
    # SNAPSHOT MANAGEMENT
    # ========================
    
    @staticmethod
    def take_snapshot(name=None):
        """Take a snapshot of selected controllers."""
        if rt.selection.count == 0:
            return "Select controllers first"
        
        selection = list(rt.selection)
        
        if name is None:
            name = SnapshotManager._detect_snapshot_name(selection)
            if name is None:
                name = "Snapshot_" + str(len(SnapshotManager._snapshots) + 1)
        
        snapshot = {
            "controllers": {},
            "pairs": {},
            "rotation_flips": {},
            "position_flips": {},
            "center_controllers": [],
            "axis_orders": {},  # NEW
        }
        
        # Step 1: Store each controller's data
        for obj in selection:
            obj_name = str(obj.name)
            
            pos = WorldSpaceMirror.get_position(obj)
            rot = WorldSpaceMirror.get_local_rotation(obj)
            world_pos = [float(obj.transform.position.x), 
                        float(obj.transform.position.y), 
                        float(obj.transform.position.z)]
            
            # Get rotation axis order
            rot_ctrl = get_controller(obj, "rotation")
            axis_order = 1  # Default XYZ
            if rot_ctrl and is_euler_rotation(rot_ctrl):
                axis_order = get_euler_order(rot_ctrl)
            
            attrs = {}
            attr_names = AttributeMirror.list_custom_attributes(obj)
            for attr in attr_names:
                val = AttributeMirror.get_custom_attribute(obj, attr)
                if val is not None:
                    attrs[attr] = val
            
            is_pos_list = PoseTools._is_position_list(obj)
            pos_list_val = None
            if is_pos_list:
                pos_list_val = PoseTools._get_position_list_value(obj)
            
            snapshot["controllers"][obj_name] = {
                "object": obj,
                "position": pos,
                "rotation": rot,
                "world_position": world_pos,
                "attributes": attrs,
                "is_position_list": is_pos_list,
                "position_list_value": pos_list_val,
                "axis_order": axis_order,
            }
            
            snapshot["axis_orders"][obj_name] = axis_order
        
        controller_names = list(snapshot["controllers"].keys())
        paired = set()
        
        # PASS 1: Match by NAME
        for name_a in controller_names:
            if name_a in paired:
                continue
            
            if not SnapshotManager._has_side_indicator(name_a):
                continue
            
            for name_b in controller_names:
                if name_b == name_a or name_b in paired:
                    continue
                
                if SnapshotManager._names_match(name_a, name_b):
                    snapshot["pairs"][name_a] = name_b
                    snapshot["pairs"][name_b] = name_a
                    paired.add(name_a)
                    paired.add(name_b)
                    
                    obj_a = snapshot["controllers"][name_a]["object"]
                    obj_b = snapshot["controllers"][name_b]["object"]
                    data_a = snapshot["controllers"][name_a]
                    data_b = snapshot["controllers"][name_b]
                    
                    rot_flips = SnapshotManager._detect_rotation_flips(obj_a, obj_b)
                    snapshot["rotation_flips"][name_a] = rot_flips
                    snapshot["rotation_flips"][name_b] = rot_flips
                    
                    pos_flips = SnapshotManager._detect_position_flips(data_a, data_b)
                    snapshot["position_flips"][name_a] = pos_flips
                    snapshot["position_flips"][name_b] = pos_flips
                    
                    break
        
        # PASS 2: Match by POSITION
        for name_a in controller_names:
            if name_a in paired:
                continue
                
            data_a = snapshot["controllers"][name_a]
            world_a = data_a["world_position"]
            
            if abs(world_a[0]) < SnapshotManager.POSITION_TOLERANCE:
                continue
            
            best_match = None
            best_dist = SnapshotManager.POSITION_TOLERANCE
            
            for name_b in controller_names:
                if name_b == name_a or name_b in paired:
                    continue
                
                data_b = snapshot["controllers"][name_b]
                world_b = data_b["world_position"]
                
                mirror_a = [-world_a[0], world_a[1], world_a[2]]
                dist = ((world_b[0] - mirror_a[0])**2 + 
                       (world_b[1] - mirror_a[1])**2 + 
                       (world_b[2] - mirror_a[2])**2) ** 0.5
                
                if dist < best_dist:
                    best_dist = dist
                    best_match = name_b
        
            if best_match:
                snapshot["pairs"][name_a] = best_match
                snapshot["pairs"][best_match] = name_a
                paired.add(name_a)
                paired.add(best_match)
                
                obj_a = data_a["object"]
                obj_b = snapshot["controllers"][best_match]["object"]
                
                rot_flips = SnapshotManager._detect_rotation_flips(obj_a, obj_b)
                snapshot["rotation_flips"][name_a] = rot_flips
                snapshot["rotation_flips"][best_match] = rot_flips
                
                pos_flips = SnapshotManager._detect_position_flips(data_a, snapshot["controllers"][best_match])
                snapshot["position_flips"][name_a] = pos_flips
                snapshot["position_flips"][best_match] = pos_flips
        
        # Mark unpaired as center and detect their flip patterns
        snapshot["center_rotation_flips"] = {}
        
        for name in controller_names:
            if name not in paired:
                snapshot["center_controllers"].append(name)
                
                # Detect rotation flips for this center controller
                obj = snapshot["controllers"][name]["object"]
                center_flips = detect_center_rotation_flips(obj)
                snapshot["center_rotation_flips"][name] = center_flips
        
        SnapshotManager._snapshots[name] = snapshot
        SnapshotManager._active_snapshot = name
        
        pair_count = len(snapshot["pairs"]) // 2
        center_count = len(snapshot["center_controllers"])
        
        return f"Snapshot '{name}': {len(selection)} controllers, {pair_count} pairs, {center_count} center"
        
        @staticmethod
        def save_to_file(filepath=None):
            """Save active snapshot to a JSON file."""
            if not SnapshotManager._active_snapshot:
                return "No active snapshot"
            
            snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
            if not snap:
                return "Snapshot not found"
            
            if filepath is None:
                filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
                    None,
                    "Save Snapshot",
                    SnapshotManager._active_snapshot + ".json",
                    "JSON Files (*.json)"
                )
                if not filepath:
                    return "Cancelled"
            
            import json
            
            save_data = {
                "name": SnapshotManager._active_snapshot,
                "controllers": {},
                "pairs": snap["pairs"],
                "rotation_flips": snap["rotation_flips"],
                "position_flips": snap["position_flips"],
                "center_controllers": snap["center_controllers"],
            }
            
            for name, data in snap["controllers"].items():
                save_data["controllers"][name] = {
                    "position": data["position"],
                    "rotation": data["rotation"],
                    "world_position": data["world_position"],
                    "attributes": data["attributes"],
                    "is_position_list": data["is_position_list"],
                    "position_list_value": data["position_list_value"],
                }
            
            try:
                with open(filepath, 'w') as f:
                    json.dump(save_data, f, indent=2)
                return f"Saved to {filepath}"
            except Exception as e:
                return f"Error saving: {e}"
            
    @staticmethod
    def load_from_file(filepath=None):
        """Load snapshot from a JSON file."""
        if filepath is None:
            filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
                None,
                "Load Snapshot",
                "",
                "JSON Files (*.json)"
            )
            if not filepath:
                return "Cancelled"
        
        import json
        
        try:
            with open(filepath, 'r') as f:
                save_data = json.load(f)
        except Exception as e:
            return f"Error loading: {e}"
        
        name = save_data.get("name", "Loaded_Snapshot")
        
        snapshot = {
            "controllers": {},
            "pairs": save_data["pairs"],
            "rotation_flips": save_data["rotation_flips"],
            "position_flips": save_data["position_flips"],
            "center_controllers": save_data["center_controllers"],
        }
        
        missing = []
        for ctrl_name, data in save_data["controllers"].items():
            obj = rt.getNodeByName(ctrl_name)
            if obj:
                snapshot["controllers"][ctrl_name] = {
                    "object": obj,
                    "position": data["position"],
                    "rotation": data["rotation"],
                    "world_position": data["world_position"],
                    "attributes": data["attributes"],
                    "is_position_list": data["is_position_list"],
                    "position_list_value": data["position_list_value"],
                }
            else:
                missing.append(ctrl_name)
        
        SnapshotManager._snapshots[name] = snapshot
        SnapshotManager._active_snapshot = name
        
        if missing:
            return f"Loaded '{name}' ({len(missing)} controllers not found in scene)"
        return f"Loaded '{name}': {len(snapshot['controllers'])} controllers"
        
    @staticmethod
    def rename_snapshot(new_name=None):
        """Rename the active snapshot."""
        if not SnapshotManager._active_snapshot:
            return "No active snapshot"
        
        if new_name is None:
            new_name, ok = QtWidgets.QInputDialog.getText(
                None,
                "Rename Snapshot",
                "Enter new name:",
                text=SnapshotManager._active_snapshot
            )
            if not ok or not new_name:
                return "Cancelled"
        
        old_name = SnapshotManager._active_snapshot
        
        if new_name == old_name:
            return "Name unchanged"
        
        if new_name in SnapshotManager._snapshots:
            return f"Name '{new_name}' already exists"
        
        SnapshotManager._snapshots[new_name] = SnapshotManager._snapshots.pop(old_name)
        SnapshotManager._active_snapshot = new_name
        
        return f"Renamed '{old_name}' to '{new_name}'"
    # ========================
    # GETTERS
    # ========================
    
    @staticmethod
    def has_snapshot():
        return len(SnapshotManager._snapshots) > 0
    
    @staticmethod
    def get_active_snapshot():
        return SnapshotManager._active_snapshot
    
    @staticmethod
    def set_active_snapshot(name):
        if name in SnapshotManager._snapshots:
            SnapshotManager._active_snapshot = name
            return True
        return False
    
    @staticmethod
    def list_snapshots():
        return list(SnapshotManager._snapshots.keys())
    
    @staticmethod
    def delete_snapshot(name):
        if name in SnapshotManager._snapshots:
            del SnapshotManager._snapshots[name]
            if SnapshotManager._active_snapshot == name:
                SnapshotManager._active_snapshot = None
            return True
        return False
    
    @staticmethod
    def clear_all_snapshots():
        SnapshotManager._snapshots = {}
        SnapshotManager._active_snapshot = None
    
    @staticmethod
    def _find_controller_snapshot(obj):
        obj_name = str(obj.name)
        
        if SnapshotManager._active_snapshot:
            snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
            if snap and obj_name in snap["controllers"]:
                return SnapshotManager._active_snapshot, snap
        
        for name, snap in SnapshotManager._snapshots.items():
            if obj_name in snap["controllers"]:
                return name, snap
        
        return None, None
    
    @staticmethod
    def get_pair(obj):
        """Get the mirror pair of an object from snapshot."""
        obj_name = str(obj.name)
        snap_name, snap = SnapshotManager._find_controller_snapshot(obj)
        
        if snap is None:
            return None
        
        pair_name = snap["pairs"].get(obj_name)
        if pair_name:
            pair_data = snap["controllers"].get(pair_name)
            if pair_data:
                return pair_data.get("object")
        
        return None
    
    @staticmethod
    def get_side(obj):
        """Get side: 'L', 'R', or 'C' (center) from snapshot."""
        obj_name = str(obj.name)
        snap_name, snap = SnapshotManager._find_controller_snapshot(obj)
        
        if snap is None:
            return None
        
        if obj_name in snap["center_controllers"]:
            return 'C'
        
        if obj_name in snap["pairs"]:
            data = snap["controllers"].get(obj_name)
            if data:
                world_x = data["world_position"][0]
                return 'L' if world_x > 0 else 'R'
        
        return None
    
    @staticmethod
    def get_rotation_flips(obj):
        """Get rotation flip pattern for this object."""
        obj_name = str(obj.name)
        snap_name, snap = SnapshotManager._find_controller_snapshot(obj)
        
        if snap is None:
            return None
        
        return snap["rotation_flips"].get(obj_name)
    
    @staticmethod
    def get_position_flips(obj):
        """Get position flip pattern for this object."""
        obj_name = str(obj.name)
        snap_name, snap = SnapshotManager._find_controller_snapshot(obj)
        
        if snap is None:
            return None
        
        return snap["position_flips"].get(obj_name)
    
    @staticmethod
    def is_center(obj):
        """Check if object is a center controller."""
        return SnapshotManager.get_side(obj) == 'C'
    
    @staticmethod
    def is_left(obj):
        """Check if object is a left side controller."""
        return SnapshotManager.get_side(obj) == 'L'
    
    @staticmethod
    def is_right(obj):
        """Check if object is a right side controller."""
        return SnapshotManager.get_side(obj) == 'R'
    
    # ========================
    # RESET TO SNAPSHOT
    # ========================
    
    @staticmethod
    def reset_to_snapshot(objects=None):
        """Reset objects to their snapshot pose."""
        if not SnapshotManager._active_snapshot:
            return "No active snapshot"
        
        snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
        if not snap:
            return "Snapshot not found"
        
        if objects is None:
            objects = list(rt.selection)
        
        count = 0
        
        with pymxs.undo(True, "Reset to Snapshot"):
            with pymxs.animate(True):
                for obj in objects:
                    obj_name = str(obj.name)
                    data = snap["controllers"].get(obj_name)
                    
                    if data is None:
                        continue
                    
                    try:
                        if data["is_position_list"] and data["position_list_value"]:
                            PoseTools._set_position_list_value(obj, data["position_list_value"])
                        elif data["position"]:
                            WorldSpaceMirror.set_position(obj, data["position"])
                        
                        if data["rotation"]:
                            WorldSpaceMirror.set_local_rotation(obj, data["rotation"])
                        
                        for attr, val in data["attributes"].items():
                            AttributeMirror.set_custom_attribute(obj, attr, val)
                        
                        count += 1
                    except:
                        pass
        
        rt.redrawViews()
        return f"Reset {count} controllers"
    
    # ========================
    # SELECTION HELPERS
    # ========================
    @staticmethod
    def select_opposite():
        """Select the mirror pairs of currently selected controllers."""
        if rt.selection.count == 0:
            return "Select controllers first"
        
        opposites = []
        for obj in rt.selection:
            pair = SnapshotManager.get_pair(obj)
            if pair:
                opposites.append(pair)
        
        if opposites:
            rt.select(opposites)
            rt.redrawViews()
            return f"Selected {len(opposites)} opposite controllers"
        
        return "No opposites found"

    @staticmethod
    def select_all_left():
        """Select all left side controllers from active snapshot."""
        if not SnapshotManager._active_snapshot:
            return "No active snapshot"
        
        snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
        if not snap:
            return "Snapshot not found"
        
        left_ctrls = []
        for name, data in snap["controllers"].items():
            if name in snap["pairs"]:
                world_x = data["world_position"][0]
                if world_x > 0:
                    left_ctrls.append(data["object"])
        
        if left_ctrls:
            rt.select(left_ctrls)
            rt.redrawViews()
            return f"Selected {len(left_ctrls)} left controllers"
        
        return "No left controllers found"

    @staticmethod
    def select_all_right():
        """Select all right side controllers from active snapshot."""
        if not SnapshotManager._active_snapshot:
            return "No active snapshot"
        
        snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
        if not snap:
            return "Snapshot not found"
        
        right_ctrls = []
        for name, data in snap["controllers"].items():
            if name in snap["pairs"]:
                world_x = data["world_position"][0]
                if world_x < 0:
                    right_ctrls.append(data["object"])
        
        if right_ctrls:
            rt.select(right_ctrls)
            rt.redrawViews()
            return f"Selected {len(right_ctrls)} right controllers"
        
        return "No right controllers found"

    @staticmethod
    def select_all_center():
        """Select all center controllers from active snapshot."""
        if not SnapshotManager._active_snapshot:
            return "No active snapshot"
        
        snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
        if not snap:
            return "Snapshot not found"
        
        center_ctrls = []
        for name in snap["center_controllers"]:
            data = snap["controllers"].get(name)
            if data:
                center_ctrls.append(data["object"])
        
        if center_ctrls:
            rt.select(center_ctrls)
            rt.redrawViews()
            return f"Selected {len(center_ctrls)} center controllers"
        
        return "No center controllers found"

    @staticmethod
    def select_all():
        """Select all controllers from active snapshot."""
        if not SnapshotManager._active_snapshot:
            return "No active snapshot"
        
        snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
        if not snap:
            return "Snapshot not found"
        
        all_ctrls = []
        for name, data in snap["controllers"].items():
            all_ctrls.append(data["object"])
        
        if all_ctrls:
            rt.select(all_ctrls)
            rt.redrawViews()
            return f"Selected {len(all_ctrls)} controllers"
        
        return "No controllers found"
    
    @staticmethod
    def set_rotation_flips(obj_name, flip_x, flip_y, flip_z):
        """
        Manually set rotation flip pattern for a controller pair.
        
        Args:
            obj_name: Name of one controller in the pair (e.g., "CTRL_Shoulder_L")
            flip_x, flip_y, flip_z: True = negate, False = copy
        """
        if not SnapshotManager._active_snapshot:
            return "No active snapshot"
        
        snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
        if not snap:
            return "Snapshot not found"
        
        if obj_name not in snap["controllers"]:
            return f"Controller '{obj_name}' not in snapshot"
        
        flips = [flip_x, flip_y, flip_z]
        
        # Set for this controller
        snap["rotation_flips"][obj_name] = flips
        
        # Also set for its pair
        pair_name = snap["pairs"].get(obj_name)
        if pair_name:
            snap["rotation_flips"][pair_name] = flips
            print(f"Set rotation flips for {obj_name} <-> {pair_name}:")
        else:
            print(f"Set rotation flips for {obj_name} (no pair):")
        
        print(f"  X: {'NEGATE' if flip_x else 'COPY'}")
        print(f"  Y: {'NEGATE' if flip_y else 'COPY'}")
        print(f"  Z: {'NEGATE' if flip_z else 'COPY'}")
        
        return "Flips updated"

    @staticmethod
    def set_position_flips(obj_name, flip_x, flip_y, flip_z):
        """
        Manually set position flip pattern for a controller pair.
        
        Args:
            obj_name: Name of one controller in the pair
            flip_x, flip_y, flip_z: True = negate, False = copy
        """
        if not SnapshotManager._active_snapshot:
            return "No active snapshot"
        
        snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
        if not snap:
            return "Snapshot not found"
        
        if obj_name not in snap["controllers"]:
            return f"Controller '{obj_name}' not in snapshot"
        
        flips = [flip_x, flip_y, flip_z]
        
        # Set for this controller
        snap["position_flips"][obj_name] = flips
        
        # Also set for its pair
        pair_name = snap["pairs"].get(obj_name)
        if pair_name:
            snap["position_flips"][pair_name] = flips
            print(f"Set position flips for {obj_name} <-> {pair_name}:")
        else:
            print(f"Set position flips for {obj_name} (no pair):")
        
        print(f"  X: {'NEGATE' if flip_x else 'COPY'}")
        print(f"  Y: {'NEGATE' if flip_y else 'COPY'}")
        print(f"  Z: {'NEGATE' if flip_z else 'COPY'}")
        
        return "Flips updated"

    @staticmethod
    def get_flip_info(obj_name):
        """Show current flip settings for a controller."""
        if not SnapshotManager._active_snapshot:
            return "No active snapshot"
        
        snap = SnapshotManager._snapshots.get(SnapshotManager._active_snapshot)
        if not snap:
            return "Snapshot not found"
        
        if obj_name not in snap["controllers"]:
            return f"Controller '{obj_name}' not in snapshot"
        
        pair_name = snap["pairs"].get(obj_name, "None")
        rot_flips = snap["rotation_flips"].get(obj_name, [None, None, None])
        pos_flips = snap["position_flips"].get(obj_name, [None, None, None])
        
        print(f"\n{'='*50}")
        print(f"Flip info: {obj_name}")
        print(f"{'='*50}")
        print(f"Pair: {pair_name}")
        print(f"\nRotation flips:")
        print(f"  X: {'NEGATE' if rot_flips[0] else 'COPY'}")
        print(f"  Y: {'NEGATE' if rot_flips[1] else 'COPY'}")
        print(f"  Z: {'NEGATE' if rot_flips[2] else 'COPY'}")
        print(f"\nPosition flips:")
        print(f"  X: {'NEGATE' if pos_flips[0] else 'COPY'}")
        print(f"  Y: {'NEGATE' if pos_flips[1] else 'COPY'}")
        print(f"  Z: {'NEGATE' if pos_flips[2] else 'COPY'}")
        print(f"{'='*50}\n")
        
        return "Done"
    
    # ========================
    # DEBUG
    # ========================
    
    @staticmethod
    def debug_snapshot(name=None):
        """Print snapshot details for debugging."""
        if name is None:
            name = SnapshotManager._active_snapshot
        
        if name is None:
            print("No active snapshot")
            return
        
        snap = SnapshotManager._snapshots.get(name)
        if snap is None:
            print(f"Snapshot '{name}' not found")
            return
        
        print(f"\n{'='*60}")
        print(f"Snapshot: {name}")
        print(f"{'='*60}")
        print(f"Controllers: {len(snap['controllers'])}")
        print(f"Pairs: {len(snap['pairs']) // 2}")
        print(f"Center: {len(snap['center_controllers'])}")
        
        print(f"\n--- Pairs ---")
        shown = set()
        for a, b in snap['pairs'].items():
            if a not in shown:
                flips = snap['rotation_flips'].get(a, [])
                flip_str = f"[{'N' if flips[0] else 'D'}, {'N' if flips[1] else 'D'}, {'N' if flips[2] else 'D'}]" if flips else "?"
                print(f"  {a} <-> {b}  rot_flips: {flip_str}")
                shown.add(a)
                shown.add(b)
        
        print(f"\n--- Center ---")
        for c in snap['center_controllers']:
            print(f"  {c}")
        
        print(f"{'='*60}\n")
class ColorDelegate(QtWidgets.QStyledItemDelegate):
    """Custom delegate to draw colored backgrounds for list items."""
    
    def paint(self, painter, option, index):
        painter.save()
        
        # Get the background and foreground colors
        bg_color = index.data(QtCore.Qt.BackgroundRole)
        fg_color = index.data(QtCore.Qt.ForegroundRole)
        text = index.data(QtCore.Qt.DisplayRole)
        
        # Draw background with rounded corners
        rect = option.rect.adjusted(2, 2, -2, -2)
        if bg_color and bg_color.isValid():
            painter.setRenderHint(QtGui.QPainter.Antialiasing)
            painter.setBrush(QtGui.QBrush(bg_color))
            painter.setPen(QtCore.Qt.NoPen)
            painter.drawRoundedRect(rect, 4, 4)
        
        # Draw selection border
        if option.state & QtWidgets.QStyle.State_Selected:
            painter.setBrush(QtCore.Qt.NoBrush)
            painter.setPen(QtGui.QPen(QtGui.QColor("#FFFFFF"), 2))
            painter.drawRoundedRect(rect, 4, 4)
        
        # Draw text
        if fg_color and fg_color.isValid():
            painter.setPen(fg_color)
        else:
            painter.setPen(QtGui.QColor("#FFFFFF"))
        
        text_rect = rect.adjusted(10, 0, -10, 0)
        painter.drawText(text_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text or "")
        
        painter.restore()
    
    def sizeHint(self, option, index):
        return QtCore.QSize(option.rect.width(), 32)

class SelectionSetsManager(QtWidgets.QDockWidget):
    """
    Dockable window for managing selection sets.
    Features: Create, rename, color-code, delete, save/load sets.
    Smart namespace support for multiple characters.
    """
    
    _instance = None
    _sets = {}
    
    COLORS = {
        "White": "#FFFFFF",
        "Light Gray": "#C0C0C0",
        "Gray": "#808080",
        "Dark Gray": "#404040",
        "Red": "#FF4444",
        "Dark Red": "#AA0000",
        "Orange": "#FF8800",
        "Yellow": "#FFFF00",
        "Green": "#44FF44",
        "Dark Green": "#00AA00",
        "Cyan": "#00FFFF",
        "Blue": "#4444FF",
        "Dark Blue": "#0000AA",
        "Purple": "#AA44FF",
        "Pink": "#FF44AA",
    }
    
    def __init__(self, parent=None):
        if parent is None:
            parent = qtmax.GetQMaxMainWindow()
        
        super(SelectionSetsManager, self).__init__("Selection Sets", parent)
        
        self.setObjectName("SelectionSetsDockWidget")
        self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.setMinimumSize(250, 300)
        self.setFloating(True)
        
        # Main widget
        self.main_widget = QtWidgets.QWidget()
        self.setWidget(self.main_widget)
        
        self.setup_ui()
        self.refresh_list()
    
    @classmethod
    def show_window(cls):
        """Show the Selection Sets Manager window."""
        main_window = qtmax.GetQMaxMainWindow()
        
        # Check if already exists
        if cls._instance is not None:
            try:
                if cls._instance.isVisible():
                    cls._instance.raise_()
                    cls._instance.activateWindow()
                    return cls._instance
            except:
                cls._instance = None
        
        cls._instance = cls(main_window)
        main_window.addDockWidget(QtCore.Qt.RightDockWidgetArea, cls._instance)
        cls._instance.setFloating(True)
        cls._instance.show()
        cls._instance.raise_()
        return cls._instance
    
    @classmethod
    def close_window(cls):
        """Close the window."""
        if cls._instance is not None:
            cls._instance.close()
            cls._instance = None
    
    def setup_ui(self):
        """Setup the user interface."""
        layout = QtWidgets.QVBoxLayout(self.main_widget)
        layout.setContentsMargins(6, 6, 6, 6)
        layout.setSpacing(6)
        
        # Top buttons: New, Delete
        top_row = QtWidgets.QHBoxLayout()
        top_row.setSpacing(4)
        
        btn_new = QtWidgets.QPushButton("+ New Set")
        btn_new.clicked.connect(self.create_set)
        btn_new.setToolTip("Create new set from selection")
        
        btn_delete = QtWidgets.QPushButton("Delete")
        btn_delete.clicked.connect(self.delete_set)
        btn_delete.setToolTip("Delete selected set")
        
        top_row.addWidget(btn_new)
        top_row.addWidget(btn_delete)
        layout.addLayout(top_row)
        
        # List widget for sets
        self.list_widget = QtWidgets.QListWidget()
        self.list_widget.setSelectionMode(QtWidgets.QAbstractItemView.SingleSelection)
        self.list_widget.itemDoubleClicked.connect(self.select_set_contents)
        self.list_widget.setContextMenuPolicy(QtCore.Qt.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self.show_context_menu)
        self.list_widget.setStyleSheet("""
            QListWidget {
                background-color: #1A1A1A;
                border: 1px solid #3A3A3A;
                border-radius: 4px;
            }
            QListWidget::item {
                padding: 8px;
                margin: 2px;
                border-radius: 4px;
                border: none;
            }
            QListWidget::item:selected {
                border: 2px solid #FFFFFF;
            }
        """)
        self.list_widget.setItemDelegate(ColorDelegate())
        layout.addWidget(self.list_widget)
        
        # Selection buttons
        sel_row = QtWidgets.QHBoxLayout()
        sel_row.setSpacing(4)
        
        btn_select = QtWidgets.QPushButton("Select")
        btn_select.clicked.connect(self.select_set_contents)
        
        btn_add_sel = QtWidgets.QPushButton("Add Sel")
        btn_add_sel.clicked.connect(self.add_to_selection)
        
        btn_remove_sel = QtWidgets.QPushButton("Remove Sel")
        btn_remove_sel.clicked.connect(self.remove_from_selection)
        
        sel_row.addWidget(btn_select)
        sel_row.addWidget(btn_add_sel)
        sel_row.addWidget(btn_remove_sel)
        layout.addLayout(sel_row)
        
        # Update/Edit buttons
        edit_row = QtWidgets.QHBoxLayout()
        edit_row.setSpacing(4)
        
        btn_update = QtWidgets.QPushButton("Update Set")
        btn_update.clicked.connect(self.update_set)
        
        btn_add_to_set = QtWidgets.QPushButton("Add to Set")
        btn_add_to_set.clicked.connect(self.add_to_set)
        
        edit_row.addWidget(btn_update)
        edit_row.addWidget(btn_add_to_set)
        layout.addLayout(edit_row)
        
        # Smart Select checkbox
        self.smart_select_cb = QtWidgets.QCheckBox("Smart Namespace")
        self.smart_select_cb.setToolTip("Match controller names across different character namespaces")
        layout.addWidget(self.smart_select_cb)
        
        # Separator
        line = QtWidgets.QFrame()
        line.setFrameShape(QtWidgets.QFrame.HLine)
        line.setStyleSheet("background-color: #3A3A3A;")
        layout.addWidget(line)
        
        # Save/Load buttons
        file_row = QtWidgets.QHBoxLayout()
        file_row.setSpacing(4)
        
        btn_save = QtWidgets.QPushButton("Save Sets...")
        btn_save.clicked.connect(self.save_sets_to_file)
        
        btn_load = QtWidgets.QPushButton("Load Sets...")
        btn_load.clicked.connect(self.load_sets_from_file)
        
        file_row.addWidget(btn_save)
        file_row.addWidget(btn_load)
        layout.addLayout(file_row)
        
        # Apply Animmix Pro style
        self.setStyleSheet("""
            QDockWidget {
                background-color: #2B2B2B;
                color: #EEE;
                font-family: 'Segoe UI';
                font-size: 11px;
            }
            QDockWidget::title {
                background-color: #2B2B2B;
                padding: 6px;
                color: #EEE;
            }
            QWidget {
                background-color: #2B2B2B;
                color: #EEE;
                font-family: 'Segoe UI';
                font-size: 11px;
            }
            QPushButton {
                background-color: #404040;
                border: 1px solid #202020;
                border-radius: 4px;
                padding: 5px;
                color: #DDD;
                min-height: 18px;
            }
            QPushButton:hover {
                background-color: #505050;
                border-color: #606060;
                color: #FFF;
            }
            QPushButton:pressed {
                background-color: #222;
            }
            QCheckBox {
                color: #888;
            }
        """)
    
    def refresh_list(self):
        """Refresh the list widget with current sets."""
        self.list_widget.clear()
        
        for set_name, set_data in SelectionSetsManager._sets.items():
            item = QtWidgets.QListWidgetItem()
            
            ctrl_count = len(set_data.get("controllers", []))
            item.setText(f"  {set_name}  ({ctrl_count})")
            
            # Get the color
            color = set_data.get("color", "#4A4A4A")
            
            # Calculate text color based on background brightness
            r = int(color[1:3], 16)
            g = int(color[3:5], 16)
            b = int(color[5:7], 16)
            brightness = (r * 299 + g * 587 + b * 114) / 1000
            text_color = "#000000" if brightness > 128 else "#FFFFFF"
            
            # Set colors using Qt.BackgroundRole and Qt.ForegroundRole
            item.setData(QtCore.Qt.BackgroundRole, QtGui.QColor(color))
            item.setData(QtCore.Qt.ForegroundRole, QtGui.QColor(text_color))
            
            # Store set name for retrieval
            item.setData(QtCore.Qt.UserRole, set_name)
            
            self.list_widget.addItem(item)
    
    def get_selected_set_name(self):
        """Get the currently selected set name."""
        item = self.list_widget.currentItem()
        if item:
            return item.data(QtCore.Qt.UserRole)
        return None
    
    def create_set(self):
        """Create a new set from current selection."""
        if rt.selection.count == 0:
            QtWidgets.QMessageBox.warning(self, "Warning", "Select objects first")
            return
        
        name, ok = QtWidgets.QInputDialog.getText(
            self, "New Selection Set", "Enter set name:"
        )
        
        if not ok or not name:
            return
        
        if name in SelectionSetsManager._sets:
            QtWidgets.QMessageBox.warning(self, "Warning", f"Set '{name}' already exists")
            return
        
        controllers = []
        for obj in rt.selection:
            controllers.append(str(obj.name))
        
        SelectionSetsManager._sets[name] = {
            "controllers": controllers,
            "color": "#4A4A4A"
        }
        
        self.refresh_list()
        print(f"Created set '{name}' with {len(controllers)} controllers")
    
    def delete_set(self):
        """Delete the selected set."""
        set_name = self.get_selected_set_name()
        if not set_name:
            return
        
        reply = QtWidgets.QMessageBox.question(
            self, "Delete Set", 
            f"Delete set '{set_name}'?",
            QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No
        )
        
        if reply == QtWidgets.QMessageBox.Yes:
            del SelectionSetsManager._sets[set_name]
            self.refresh_list()
            print(f"Deleted set '{set_name}'")
    
    def select_set_contents(self):
        """Select the contents of the selected set."""
        set_name = self.get_selected_set_name()
        if not set_name:
            return
        
        set_data = SelectionSetsManager._sets.get(set_name)
        if not set_data:
            return
        
        objects = self._find_objects(set_data["controllers"])
        
        if objects:
            rt.select(objects)
            rt.redrawViews()
            print(f"Selected {len(objects)} objects from '{set_name}'")
        else:
            print(f"No objects found for set '{set_name}'")
    
    def add_to_selection(self):
        """Add set contents to current selection."""
        set_name = self.get_selected_set_name()
        if not set_name:
            return
        
        set_data = SelectionSetsManager._sets.get(set_name)
        if not set_data:
            return
        
        objects = self._find_objects(set_data["controllers"])
        
        if objects:
            current = list(rt.selection)
            rt.select(current + objects)
            rt.redrawViews()
            print(f"Added {len(objects)} objects to selection")
    
    def remove_from_selection(self):
        """Remove set contents from current selection."""
        set_name = self.get_selected_set_name()
        if not set_name:
            return
        
        set_data = SelectionSetsManager._sets.get(set_name)
        if not set_data:
            return
        
        set_objects = self._find_objects(set_data["controllers"])
        set_names = [str(o.name) for o in set_objects]
        
        remaining = []
        for obj in rt.selection:
            if str(obj.name) not in set_names:
                remaining.append(obj)
        
        rt.select(remaining)
        rt.redrawViews()
        print(f"Removed set objects from selection")
    
    def update_set(self):
        """Update the selected set with current selection."""
        set_name = self.get_selected_set_name()
        if not set_name:
            return
        
        if rt.selection.count == 0:
            QtWidgets.QMessageBox.warning(self, "Warning", "Select objects first")
            return
        
        controllers = []
        for obj in rt.selection:
            controllers.append(str(obj.name))
        
        SelectionSetsManager._sets[set_name]["controllers"] = controllers
        self.refresh_list()
        print(f"Updated set '{set_name}' with {len(controllers)} controllers")
    
    def add_to_set(self):
        """Add current selection to the selected set."""
        set_name = self.get_selected_set_name()
        if not set_name:
            return
        
        if rt.selection.count == 0:
            QtWidgets.QMessageBox.warning(self, "Warning", "Select objects first")
            return
        
        existing = SelectionSetsManager._sets[set_name]["controllers"]
        
        for obj in rt.selection:
            name = str(obj.name)
            if name not in existing:
                existing.append(name)
        
        self.refresh_list()
        print(f"Added objects to set '{set_name}'")
    
    def _find_objects(self, controller_names):
        """Find objects in scene by name, with optional smart namespace matching."""
        objects = []
        
        use_smart = self.smart_select_cb.isChecked()
        
        namespace = ""
        if use_smart and rt.selection.count > 0:
            first_sel = str(rt.selection[0].name)
            if ":" in first_sel:
                namespace = first_sel.rsplit(":", 1)[0] + ":"
        
        for name in controller_names:
            obj = None
            
            obj = rt.getNodeByName(name)
            
            if obj is None and use_smart and namespace:
                base_name = name.split(":")[-1] if ":" in name else name
                obj = rt.getNodeByName(namespace + base_name)
            
            if obj:
                objects.append(obj)
        
        return objects
    
    def show_context_menu(self, pos):
        """Show context menu for set item."""
        item = self.list_widget.itemAt(pos)
        if not item:
            return
        
        set_name = item.data(QtCore.Qt.UserRole)
        
        menu = QtWidgets.QMenu(self)
        menu.setStyleSheet("""
            QMenu {
                background-color: #3C3C3C;
                color: #EEE;
                border: 1px solid #555;
            }
            QMenu::item:selected {
                background-color: #5A5A5A;
            }
        """)
        
        # Rename action
        rename_action = menu.addAction("Rename...")
        current_name = set_name
        rename_action.triggered.connect(lambda checked=False, n=current_name: self.rename_set(n))
        
        # Color submenu
        color_menu = menu.addMenu("Set Color")
        for color_name, color_hex in self.COLORS.items():
            action = color_menu.addAction(color_name)
            pixmap = QtGui.QPixmap(16, 16)
            pixmap.fill(QtGui.QColor(color_hex))
            action.setIcon(QtGui.QIcon(pixmap))
            action.triggered.connect(
                lambda checked=False, c=color_hex, n=set_name: self._apply_color(n, c)
            )
        
        menu.addSeparator()
        
        # Delete action
        delete_action = menu.addAction("Delete")
        delete_action.triggered.connect(lambda checked=False: self.delete_set())
        
        menu.exec(self.list_widget.mapToGlobal(pos))
    
    def _apply_color(self, set_name, color_hex):
        """Apply color to a set and refresh."""
        print(f"Setting color {color_hex} for {set_name}")
        self.set_color(set_name, color_hex)
    
    def rename_set(self, old_name):
        """Rename a set."""
        new_name, ok = QtWidgets.QInputDialog.getText(
            self, "Rename Set", "Enter new name:", text=old_name
        )
        
        if not ok or not new_name or new_name == old_name:
            return
        
        if new_name in SelectionSetsManager._sets:
            QtWidgets.QMessageBox.warning(self, "Warning", f"Set '{new_name}' already exists")
            return
        
        SelectionSetsManager._sets[new_name] = SelectionSetsManager._sets.pop(old_name)
        self.refresh_list()
        print(f"Renamed set '{old_name}' to '{new_name}'")
    
    def set_color(self, set_name, color_hex):
        """Set the color of a set."""
        if set_name in SelectionSetsManager._sets:
            SelectionSetsManager._sets[set_name]["color"] = color_hex
            self.refresh_list()
    
    def save_sets_to_file(self):
        """Save all sets to a JSON file."""
        if not SelectionSetsManager._sets:
            QtWidgets.QMessageBox.warning(self, "Warning", "No sets to save")
            return
        
        filepath, _ = QtWidgets.QFileDialog.getSaveFileName(
            self, "Save Selection Sets", "selection_sets.json", "JSON Files (*.json)"
        )
        
        if not filepath:
            return
        
        import json
        
        try:
            with open(filepath, 'w') as f:
                json.dump(SelectionSetsManager._sets, f, indent=2)
            print(f"Saved {len(SelectionSetsManager._sets)} sets to {filepath}")
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to save: {e}")
    
    def load_sets_from_file(self):
        """Load sets from a JSON file."""
        filepath, _ = QtWidgets.QFileDialog.getOpenFileName(
            self, "Load Selection Sets", "", "JSON Files (*.json)"
        )
        
        if not filepath:
            return
        
        import json
        
        try:
            with open(filepath, 'r') as f:
                loaded_sets = json.load(f)
            
            if SelectionSetsManager._sets:
                reply = QtWidgets.QMessageBox.question(
                    self, "Load Sets",
                    "Merge with existing sets? (No = Replace all)",
                    QtWidgets.QMessageBox.Yes | QtWidgets.QMessageBox.No | QtWidgets.QMessageBox.Cancel
                )
                
                if reply == QtWidgets.QMessageBox.Cancel:
                    return
                elif reply == QtWidgets.QMessageBox.No:
                    SelectionSetsManager._sets = {}
            
            SelectionSetsManager._sets.update(loaded_sets)
            self.refresh_list()
            print(f"Loaded {len(loaded_sets)} sets from {filepath}")
            
        except Exception as e:
            QtWidgets.QMessageBox.critical(self, "Error", f"Failed to load: {e}")
    
    def closeEvent(self, event):
        """Handle window close."""
        SelectionSetsManager._instance = None
        event.accept()
        
class PoseTools:
    _clipboard = {}
    _mirror_cache = {}
    
    @staticmethod
    def copy_pose():
        if rt.selection.count == 0:
            return "Select objects"
        
        PoseTools._clipboard = {}
        for obj in rt.selection:
            name = str(obj.name)
            base_name = name.split(':')[-1] if ':' in name else name
            PoseTools._clipboard[base_name] = rt.copy(obj.transform)
        
        return f"Copied {len(PoseTools._clipboard)} poses"
    
    @staticmethod
    def paste_pose():
        if not PoseTools._clipboard:
            return "Clipboard empty"
        
        count = 0
        with pymxs.animate(True):
            for obj in rt.selection:
                name = str(obj.name)
                base_name = name.split(':')[-1] if ':' in name else name
                
                if base_name in PoseTools._clipboard:
                    try:
                        obj.transform = PoseTools._clipboard[base_name]
                        count += 1
                    except:
                        pass
        
        rt.redrawViews()
        return f"Pasted {count} poses"
    
    @staticmethod
    def _is_position_list(obj):
        """Check if object has Position_List controller."""
        try:
            ctrl = rt.getPropertyController(obj.controller, "position")
            if ctrl and "position_list" in str(rt.classOf(ctrl)).lower():
                return True
        except:
            pass
        return False
    
    @staticmethod
    def _get_position_list_value(obj):
        """Get position from Position_List controller."""
        try:
            ctrl = rt.getPropertyController(obj.controller, "position")
            if ctrl and ctrl.count >= 1:
                sub = ctrl[1]
                if hasattr(sub, 'value'):
                    val = sub.value
                    return [float(val.x), float(val.y), float(val.z)]
        except:
            pass
        return None
    
    @staticmethod
    def _set_position_list_value(obj, vals):
        """Set position on Position_List controller."""
        try:
            ctrl = rt.getPropertyController(obj.controller, "position")
            if ctrl and ctrl.count >= 1:
                ctrl[1].value = rt.Point3(vals[0], vals[1], vals[2])
                return True
        except:
            pass
        return False
    
    @staticmethod
    def _get_pair_and_flips(obj, selection):
        """
        Get mirror pair and flip patterns.
        Uses SnapshotManager if available, falls back to MirrorPairDetector.
        """
        pair = None
        side = None
        rot_flips = None
        pos_flips = None
        
        # Try SnapshotManager first
        if SnapshotManager.has_snapshot():
            pair = SnapshotManager.get_pair(obj)
            side = SnapshotManager.get_side(obj)
            rot_flips = SnapshotManager.get_rotation_flips(obj)
            pos_flips = SnapshotManager.get_position_flips(obj)
            
            # If snapshot says it's CENTER, return no pair
            if side == 'C':
                return None, 'C', None, None
            
            if pair and side:
                return pair, side, rot_flips, pos_flips
        
        # Fallback to MirrorPairDetector
        pair, side = MirrorPairDetector.find_pair(obj, selection)
        
        # Use auto-detection for flips
        if pair:
            rot_flips = WorldSpaceMirror.detect_axis_flips_at_zero(obj, pair)
            pos_flips = [True, False, False]  # Default: negate X
        
        return pair, side, rot_flips, pos_flips
    
    @staticmethod
    def _is_center(obj):
        """Check if object is a center controller."""
        # Try SnapshotManager first
        if SnapshotManager.has_snapshot():
            side = SnapshotManager.get_side(obj)
            if side is not None:
                return side == 'C'
        
        # Fallback to name-based detection
        return PoseTools._is_center_by_name(obj)
    
    @staticmethod
    def _is_center_by_name(obj):
        """Check if object has NO L/R naming pattern."""
        name = str(obj.name)
        for left_pat, right_pat in MirrorPairDetector.NAME_PATTERNS:
            if left_pat in name or right_pat in name:
                return False
        return True
    
    @staticmethod
    def _flip_center_rotation(obj):
        """Flip rotation for center controllers using detected flip pattern."""
        try:
            rot = WorldSpaceMirror.get_local_rotation(obj)
            if rot is None:
                return False
            
            # Get flip pattern from snapshot
            flips = [False, True, True]  # Default: [X, -Y, -Z]
            
            if SnapshotManager.has_snapshot():
                snap_name = SnapshotManager._active_snapshot
                snap = SnapshotManager._snapshots.get(snap_name)
                if snap:
                    obj_name = str(obj.name)
                    stored_flips = snap.get("center_rotation_flips", {}).get(obj_name)
                    if stored_flips:
                        flips = stored_flips
            
            # Apply flips
            flipped_rot = [(-rot[i] if flips[i] else rot[i]) for i in range(3)]
            return WorldSpaceMirror.set_local_rotation(obj, flipped_rot)
        except:
            return False
            
    @staticmethod
    def _flip_center_position(obj):
        """Flip position for center controllers. Negates X axis."""
        try:
            # For position, we typically just negate X (mirror across YZ plane)
            # But we could also detect this per-controller if needed
            
            if PoseTools._is_position_list(obj):
                pos = PoseTools._get_position_list_value(obj)
                if pos:
                    flipped_pos = [-pos[0], pos[1], pos[2]]
                    return PoseTools._set_position_list_value(obj, flipped_pos)
            else:
                pos = WorldSpaceMirror.get_position(obj)
                if pos:
                    flipped_pos = [-pos[0], pos[1], pos[2]]
                    return WorldSpaceMirror.set_position(obj, flipped_pos)
        except:
            pass
        return False

    @staticmethod
    def _do_swap(obj, pair, rot_flips=None, pos_flips=None):
        """Swap transforms between two objects, respecting axis order."""
        
        # Get axis orders from snapshot
        order_a = 1
        order_b = 1
        
        if SnapshotManager.has_snapshot():
            snap_name = SnapshotManager._active_snapshot
            snap = SnapshotManager._snapshots.get(snap_name)
            if snap:
                order_a = snap.get("axis_orders", {}).get(str(obj.name), 1)
                order_b = snap.get("axis_orders", {}).get(str(pair.name), 1)
        
        # Get flips if not provided
        if rot_flips is None:
            if SnapshotManager.has_snapshot():
                rot_flips = SnapshotManager.get_rotation_flips(obj)
            if rot_flips is None:
                rot_flips = WorldSpaceMirror.detect_axis_flips_at_zero(obj, pair)
        
        if pos_flips is None:
            if SnapshotManager.has_snapshot():
                pos_flips = SnapshotManager.get_position_flips(obj)
            if pos_flips is None:
                pos_flips = [True, False, False]
        
        # Handle Position_List controllers
        if PoseTools._is_position_list(obj):
            pos_a = PoseTools._get_position_list_value(obj)
            pos_b = PoseTools._get_position_list_value(pair)
            
            if pos_a is not None and pos_b is not None:
                mir_pos_a = [(-pos_a[i] if pos_flips[i] else pos_a[i]) for i in range(3)]
                mir_pos_b = [(-pos_b[i] if pos_flips[i] else pos_b[i]) for i in range(3)]
                PoseTools._set_position_list_value(obj, mir_pos_b)
                PoseTools._set_position_list_value(pair, mir_pos_a)
            
            # Handle rotation separately
            rot_a = WorldSpaceMirror.get_local_rotation(obj)
            rot_b = WorldSpaceMirror.get_local_rotation(pair)
            if rot_a is not None and rot_b is not None:
                mir_rot_a = PoseTools._apply_mirror_rotation(rot_a, rot_flips, order_a, order_b)
                mir_rot_b = PoseTools._apply_mirror_rotation(rot_b, rot_flips, order_b, order_a)
                WorldSpaceMirror.set_local_rotation(obj, mir_rot_b)
                WorldSpaceMirror.set_local_rotation(pair, mir_rot_a)
        else:
            # Handle rotation
            rot_a = WorldSpaceMirror.get_local_rotation(obj)
            rot_b = WorldSpaceMirror.get_local_rotation(pair)
            pos_a = WorldSpaceMirror.get_position(obj)
            pos_b = WorldSpaceMirror.get_position(pair)
            
            if rot_a is not None and rot_b is not None:
                mir_rot_a = PoseTools._apply_mirror_rotation(rot_a, rot_flips, order_a, order_b)
                mir_rot_b = PoseTools._apply_mirror_rotation(rot_b, rot_flips, order_b, order_a)
                WorldSpaceMirror.set_local_rotation(obj, mir_rot_b)
                WorldSpaceMirror.set_local_rotation(pair, mir_rot_a)
            
            if pos_a is not None and pos_b is not None:
                mir_pos_a = [(-pos_a[i] if pos_flips[i] else pos_a[i]) for i in range(3)]
                mir_pos_b = [(-pos_b[i] if pos_flips[i] else pos_b[i]) for i in range(3)]
                WorldSpaceMirror.set_position(obj, mir_pos_b)
                WorldSpaceMirror.set_position(pair, mir_pos_a)
        
        # Swap attributes
        attrs_obj = AttributeMirror.list_custom_attributes(obj)
        attrs_pair = AttributeMirror.list_custom_attributes(pair)
        attr_names = list(set(attrs_obj + attrs_pair))
        if attr_names:
            AttributeMirror.swap_attributes(obj, pair, attr_names)
    @staticmethod
    def _apply_mirror_rotation(rot, flips, source_order, target_order):
        """
        Apply mirror flips and convert between axis orders if needed.
        
        Args:
            rot: [x, y, z] rotation values
            flips: [bool, bool, bool] which axes to negate
            source_order: Euler order of source (1=XYZ, 4=YXZ, etc.)
            target_order: Euler order of target
        
        Returns:
            [x, y, z] mirrored rotation for target
        """
        # Apply flips
        flipped = [(-rot[i] if flips[i] else rot[i]) for i in range(3)]
        
        # If same order, just return flipped values
        if source_order == target_order:
            return flipped
        
        # Different orders - need to remap
        # Get which actual axis (X=0, Y=1, Z=2) is at each position
        source_indices = get_axis_indices(source_order)
        target_indices = get_axis_indices(target_order)
        
        # Map source rotation to actual X/Y/Z values
        actual_xyz = [0.0, 0.0, 0.0]
        for i in range(3):
            actual_axis = source_indices[i]
            actual_xyz[actual_axis] = flipped[i]
        
        # Map actual X/Y/Z to target order
        result = [0.0, 0.0, 0.0]
        for i in range(3):
            actual_axis = target_indices[i]
            result[i] = actual_xyz[actual_axis]
        
        return result
        
    @staticmethod
    def _do_mirror(source_obj, target_obj, rot_flips=None, pos_flips=None):
        """Mirror from source to target, respecting axis order."""
        
        # Get axis orders from snapshot
        source_order = 1
        target_order = 1
        
        if SnapshotManager.has_snapshot():
            snap_name = SnapshotManager._active_snapshot
            snap = SnapshotManager._snapshots.get(snap_name)
            if snap:
                source_order = snap.get("axis_orders", {}).get(str(source_obj.name), 1)
                target_order = snap.get("axis_orders", {}).get(str(target_obj.name), 1)
        
        # Get flips if not provided
        if rot_flips is None:
            if SnapshotManager.has_snapshot():
                rot_flips = SnapshotManager.get_rotation_flips(source_obj)
            if rot_flips is None:
                rot_flips = WorldSpaceMirror.detect_axis_flips_at_zero(source_obj, target_obj)
        
        if pos_flips is None:
            if SnapshotManager.has_snapshot():
                pos_flips = SnapshotManager.get_position_flips(source_obj)
            if pos_flips is None:
                pos_flips = [True, False, False]
        
        # Get source rotation
        rot = WorldSpaceMirror.get_local_rotation(source_obj)
        
        if rot is not None:
            # Convert rotation considering axis orders
            mir_rot = PoseTools._apply_mirror_rotation(rot, rot_flips, source_order, target_order)
            WorldSpaceMirror.set_local_rotation(target_obj, mir_rot)
        
        # Handle position
        if PoseTools._is_position_list(source_obj):
            pos = PoseTools._get_position_list_value(source_obj)
            if pos is not None:
                mir_pos = [(-pos[i] if pos_flips[i] else pos[i]) for i in range(3)]
                PoseTools._set_position_list_value(target_obj, mir_pos)
        else:
            pos = WorldSpaceMirror.get_position(source_obj)
            if pos is not None:
                mir_pos = [(-pos[i] if pos_flips[i] else pos[i]) for i in range(3)]
                WorldSpaceMirror.set_position(target_obj, mir_pos)
        
        # Mirror attributes
        attrs = AttributeMirror.list_custom_attributes(source_obj)
        if attrs:
            AttributeMirror.mirror_attributes(source_obj, target_obj, attrs)
    
    @staticmethod
    def mirror_pose():
        """Auto-detect side and mirror TO the opposite side. Also flips center controls."""
        if rt.selection.count == 0:
            return "Select objects"
        
        selection = list(rt.selection)
        processed = set()
        pair_count = 0
        center_count = 0
        
        with pymxs.undo(True, "Mirror Pose"):
            with pymxs.animate(True):
                for obj in selection:
                    obj_name = str(obj.name)
                    
                    if obj_name in processed:
                        continue
                    
                    pair, side, rot_flips, pos_flips = PoseTools._get_pair_and_flips(obj, selection)
                    
                    if pair:
                        pair_name = str(pair.name)
                        
                        # Mirror FROM selected TO pair
                        PoseTools._do_mirror(obj, pair, rot_flips, pos_flips)
                        pair_count += 1
                        processed.add(obj_name)
                        processed.add(pair_name)
                    else:
                        # No pair - check if center controller
                        if PoseTools._is_center(obj):
                            rot_ok = PoseTools._flip_center_rotation(obj)
                            pos_ok = PoseTools._flip_center_position(obj)
                            if rot_ok or pos_ok:
                                center_count += 1
                        processed.add(obj_name)
        
        rt.redrawViews()
        return f"Mirrored {pair_count} pairs + {center_count} center"
    
    @staticmethod
    def mirror_left_to_right():
        """Force mirror from Left to Right."""
        if rt.selection.count == 0:
            return "Select objects"
        
        selection = list(rt.selection)
        processed = set()
        count = 0
        
        with pymxs.undo(True, "Mirror L->R"):
            with pymxs.animate(True):
                for obj in selection:
                    obj_name = str(obj.name)
                    
                    if obj_name in processed:
                        continue
                    
                    pair, side, rot_flips, pos_flips = PoseTools._get_pair_and_flips(obj, selection)
                    
                    if pair:
                        pair_name = str(pair.name)
                        
                        if side == 'L':
                            PoseTools._do_mirror(obj, pair, rot_flips, pos_flips)
                            count += 1
                        
                        processed.add(obj_name)
                        processed.add(pair_name)
        
        rt.redrawViews()
        return f"Mirrored {count} L->R"
    
    @staticmethod
    def mirror_right_to_left():
        """Force mirror from Right to Left."""
        if rt.selection.count == 0:
            return "Select objects"
        
        selection = list(rt.selection)
        processed = set()
        count = 0
        
        with pymxs.undo(True, "Mirror R->L"):
            with pymxs.animate(True):
                for obj in selection:
                    obj_name = str(obj.name)
                    
                    if obj_name in processed:
                        continue
                    
                    pair, side, rot_flips, pos_flips = PoseTools._get_pair_and_flips(obj, selection)
                    
                    if pair:
                        pair_name = str(pair.name)
                        
                        if side == 'R':
                            PoseTools._do_mirror(obj, pair, rot_flips, pos_flips)
                            count += 1
                        
                        processed.add(obj_name)
                        processed.add(pair_name)
        
        rt.redrawViews()
        return f"Mirrored {count} R->L"
    
    @staticmethod
    def flip_pose():
        """Flip entire pose - swaps L/R AND flips center controllers."""
        if rt.selection.count == 0:
            return "Select objects"
        
        selection = list(rt.selection)
        processed = set()
        pair_count = 0
        center_count = 0
        
        with pymxs.undo(True, "Flip Pose"):
            with pymxs.animate(True):
                for obj in selection:
                    obj_name = str(obj.name)
                    
                    if obj_name in processed:
                        continue
                    
                    pair, side, rot_flips, pos_flips = PoseTools._get_pair_and_flips(obj, selection)
                    
                    if pair:
                        pair_name = str(pair.name)
                        PoseTools._do_swap(obj, pair, rot_flips, pos_flips)
                        pair_count += 2
                        processed.add(obj_name)
                        processed.add(pair_name)
                    
                    else:
                        # No pair - check if center controller
                        if PoseTools._is_center(obj):
                            # Flip both rotation AND position for center controls
                            rot_ok = PoseTools._flip_center_rotation(obj)
                            pos_ok = PoseTools._flip_center_position(obj)
                            if rot_ok or pos_ok:
                                center_count += 1
                        processed.add(obj_name)
        
        rt.redrawViews()
        return f"Flipped {pair_count} L/R + {center_count} center"
    
    @staticmethod
    def reset_pose():
        """Reset to snapshot or zero pose."""
        if SnapshotManager.has_snapshot():
            return SnapshotManager.reset_to_snapshot()
        else:
            return do_reset_pose()
        
class MirrorPairDetector:
    """Detects mirror pairs using naming conventions and position fallback."""
    
    # Name patterns to check (left_pattern, right_pattern)
    NAME_PATTERNS = [
        # Full words first (more specific)
        ("_Left", "_Right"),
        ("_left", "_right"),
        ("_LEFT", "_RIGHT"),
        ("Left_", "Right_"),
        ("left_", "right_"),
        # Then abbreviations
        ("_Lft", "_Rgt"),
        ("_lft", "_rgt"),
        ("_LFT", "_RGT"),
        # Then single letters
        ("_L_", "_R_"),
        ("_l_", "_r_"),
        ("_L", "_R"),
        ("_l", "_r"),
        ("L_", "R_"),
        ("l_", "r_"),
        (".L", ".R"),
        (".l", ".r"),
        ("_L.", "_R."),
    ]
    
    POSITION_TOLERANCE = 0.1
    
    @staticmethod
    def get_mirror_name(name):
        """
        Try to find mirror name based on naming conventions.
        Returns (mirror_name, side) where side is 'L', 'R', or None
        """
        for left_pat, right_pat in MirrorPairDetector.NAME_PATTERNS:
            if left_pat in name:
                return name.replace(left_pat, right_pat, 1), 'L'
            if right_pat in name:
                return name.replace(right_pat, left_pat, 1), 'R'
        return None, None
    
    @staticmethod
    def find_pair_by_name(obj):
        """Find mirror pair using naming convention."""
        name = str(obj.name)
        
        # Handle namespaces (e.g., "Namespace:Bone_L")
        namespace = ""
        base_name = name
        if ":" in name:
            parts = name.rpartition(":")
            namespace = parts[0] + ":"
            base_name = parts[2]
        
        mirror_base, side = MirrorPairDetector.get_mirror_name(base_name)
        if mirror_base:
            full_mirror_name = namespace + mirror_base
            try:
                target = rt.getNodeByName(full_mirror_name)
                if target:
                    return target, side
            except:
                pass
        
        return None, None
    
    @staticmethod
    def find_pair_by_position(obj, candidates=None):
        """Find mirror pair by checking for mirrored world position."""
        try:
            pos = obj.transform.position
            mirror_pos = rt.Point3(-pos.x, pos.y, pos.z)
            
            search_list = candidates if candidates else rt.objects
            
            best_match = None
            best_dist = MirrorPairDetector.POSITION_TOLERANCE
            
            for other in search_list:
                if other == obj:
                    continue
                try:
                    other_pos = other.transform.position
                    dist = rt.distance(other_pos, mirror_pos)
                    if dist < best_dist:
                        best_dist = dist
                        best_match = other
                except:
                    pass
            
            if best_match:
                side = 'L' if pos.x > 0 else 'R' if pos.x < 0 else None
                return best_match, side
                
        except:
            pass
        
        return None, None
    
    @staticmethod
    def find_pair(obj, candidates=None):
        """
        Find mirror pair using hybrid approach:
        1. Try name-based matching first (fast, reliable)
        2. Fall back to position-based matching
        """
        target, side = MirrorPairDetector.find_pair_by_name(obj)
        if target:
            return target, side
        
        target, side = MirrorPairDetector.find_pair_by_position(obj, candidates)
        return target, side
    
    @staticmethod
    def get_side(obj):
        """Determine which side an object is on (L, R, or None for center)."""
        name = str(obj.name)
        
        for left_pat, right_pat in MirrorPairDetector.NAME_PATTERNS:
            if left_pat in name:
                return 'L'
            if right_pat in name:
                return 'R'
        
        try:
            pos = obj.transform.position
            if abs(pos.x) < MirrorPairDetector.POSITION_TOLERANCE:
                return None
            return 'L' if pos.x > 0 else 'R'
        except:
            return None


class WorldSpaceMirror:
    """Smart mirroring with per-rig flip profiles."""
    
    _axis_flip_cache = {}
    _rig_profiles = {}  # Store flip patterns per rig type
    
    @staticmethod
    def clear_cache():
        WorldSpaceMirror._axis_flip_cache = {}
    
    @staticmethod
    def save_rig_profile(profile_name, flip_x, flip_y, flip_z):
        """
        Save a flip pattern with a name for reuse.
        Example: save_rig_profile("MyCharacterRig", True, False, True)
        """
        WorldSpaceMirror._rig_profiles[profile_name] = [flip_x, flip_y, flip_z]
        print(f"Saved rig profile '{profile_name}':")
        print(f"  X: {'NEGATE' if flip_x else 'DIRECT'}")
        print(f"  Y: {'NEGATE' if flip_y else 'DIRECT'}")
        print(f"  Z: {'NEGATE' if flip_z else 'DIRECT'}")
    
    @staticmethod
    def apply_rig_profile(profile_name, obj_a, obj_b):
        """
        Apply a saved rig profile to a specific pair.
        """
        if profile_name not in WorldSpaceMirror._rig_profiles:
            print(f"Error: Profile '{profile_name}' not found!")
            print(f"Available profiles: {list(WorldSpaceMirror._rig_profiles.keys())}")
            return False
        
        flips = WorldSpaceMirror._rig_profiles[profile_name]
        cache_key = WorldSpaceMirror.get_pair_key(obj_a, obj_b)
        WorldSpaceMirror._axis_flip_cache[cache_key] = flips
        
        print(f"Applied profile '{profile_name}' to {obj_a.name} <-> {obj_b.name}")
        return True
    
    @staticmethod
    def list_rig_profiles():
        """Show all saved rig profiles."""
        if not WorldSpaceMirror._rig_profiles:
            print("No rig profiles saved yet.")
            return
        
        print("\nSaved Rig Profiles:")
        print("="*60)
        for name, flips in WorldSpaceMirror._rig_profiles.items():
            print(f"{name}:")
            print(f"  X: {'NEGATE' if flips[0] else 'DIRECT'}")
            print(f"  Y: {'NEGATE' if flips[1] else 'DIRECT'}")
            print(f"  Z: {'NEGATE' if flips[2] else 'DIRECT'}")
        print("="*60)
    
    @staticmethod
    def mirror_matrix(matrix):
        """
        Mirror a transform matrix across the YZ plane.
        This negates the X column and X row to flip across X=0.
        """
        # Create a mirrored copy
        m = rt.Matrix3(1)
        
        # Negate X components (column 1)
        m.row1 = rt.Point3(-matrix.row1.x, matrix.row1.y, matrix.row1.z)
        m.row2 = rt.Point3(-matrix.row2.x, matrix.row2.y, matrix.row2.z)
        m.row3 = rt.Point3(-matrix.row3.x, matrix.row3.y, matrix.row3.z)
        m.row4 = rt.Point3(-matrix.row4.x, matrix.row4.y, matrix.row4.z)
        
        return m
    
    @staticmethod
    def set_world_transform(obj, target_matrix):
        """
        Set an object's world transform using a matrix.
        This properly handles parent transforms.
        """
        try:
            # If object has a parent, we need to compute local transform
            if obj.parent:
                parent_inv = rt.inverse(obj.parent.transform)
                local_matrix = target_matrix * parent_inv
                obj.transform = local_matrix
            else:
                obj.transform = target_matrix
            return True
        except Exception as e:
            print(f"Error setting transform: {e}")
            return False
    
    @staticmethod
    def matrix_based_swap(obj_a, obj_b):
        """
        Swap transforms using pure matrix operations.
        This avoids all Euler angle issues.
        """
        try:
            # Get world transforms
            tm_a = obj_a.transform
            tm_b = obj_b.transform
            
            # Mirror both matrices
            mir_tm_a = WorldSpaceMirror.mirror_matrix(tm_a)
            mir_tm_b = WorldSpaceMirror.mirror_matrix(tm_b)
            
            # Apply swapped transforms
            WorldSpaceMirror.set_world_transform(obj_a, mir_tm_b)
            WorldSpaceMirror.set_world_transform(obj_b, mir_tm_a)
            
            return True
        except Exception as e:
            print(f"Matrix swap failed: {e}")
            return False
    
    @staticmethod
    def matrix_based_mirror(source_obj, target_obj):
        """
        Mirror from source to target using pure matrix operations.
        """
        try:
            # Get source world transform
            tm_source = source_obj.transform
            
            # Mirror it
            mir_tm = WorldSpaceMirror.mirror_matrix(tm_source)
            
            # Apply to target
            WorldSpaceMirror.set_world_transform(target_obj, mir_tm)
            
            return True
        except Exception as e:
            print(f"Matrix mirror failed: {e}")
            return False
    
    @staticmethod
    def get_rotation_order(obj):
        """Get the rotation order of the controller."""
        try:
            ctrl = get_controller(obj, "rotation")
            if ctrl and is_euler_rotation(ctrl):
                if hasattr(ctrl, 'rotationOrder'):
                    order = ctrl.rotationOrder
                    order_names = {1: "XYZ", 2: "XZY", 3: "YZX", 4: "YXZ", 5: "ZXY", 6: "ZYX"}
                    return order_names.get(order, f"Unknown({order})")
        except:
            pass
        return "Unknown"
    
    @staticmethod
    def get_local_rotation(obj):
        ctrl = get_controller(obj, "rotation")
        if ctrl and is_euler_rotation(ctrl):
            try:
                vals = [0.0, 0.0, 0.0]
                for i in range(3):
                    sub = ctrl[i]
                    sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                    sub_ctrl = resolve_controller(sub_ctrl)
                    if sub_ctrl:
                        vals[i] = float(sub_ctrl.value)
                return vals
            except:
                pass
        return None
    
    @staticmethod
    def set_local_rotation(obj, vals):
        ctrl = get_controller(obj, "rotation")
        if ctrl and is_euler_rotation(ctrl):
            try:
                for i in range(3):
                    sub = ctrl[i]
                    sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                    sub_ctrl = resolve_controller(sub_ctrl)
                    if sub_ctrl:
                        sub_ctrl.value = vals[i]
                return True
            except:
                pass
        return False
    
    @staticmethod
    def get_position(obj):
        ctrl = get_controller(obj, "position")
        if ctrl and is_xyz_controller(ctrl):
            try:
                vals = [0.0, 0.0, 0.0]
                for i in range(3):
                    sub = ctrl[i]
                    sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                    sub_ctrl = resolve_controller(sub_ctrl)
                    if sub_ctrl:
                        vals[i] = float(sub_ctrl.value)
                return vals
            except:
                pass
        try:
            pos = obj.position
            return [float(pos.x), float(pos.y), float(pos.z)]
        except:
            return None
    
    @staticmethod
    def set_position(obj, vals):
        ctrl = get_controller(obj, "position")
        if ctrl and is_xyz_controller(ctrl):
            try:
                for i in range(3):
                    sub = ctrl[i]
                    sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                    sub_ctrl = resolve_controller(sub_ctrl)
                    if sub_ctrl:
                        sub_ctrl.value = vals[i]
                return True
            except:
                pass
        try:
            obj.position = rt.Point3(vals[0], vals[1], vals[2])
            return True
        except:
            return False
    
    @staticmethod
    def mirror_position(pos):
        if pos is None:
            return None
        return [-pos[0], pos[1], pos[2]]
    
    @staticmethod
    def get_pair_key(obj_a, obj_b):
        name_a = str(obj_a.name)
        name_b = str(obj_b.name)
        return tuple(sorted([name_a, name_b]))
    
    @staticmethod
    def get_local_axes_in_world(obj):
        """Get the local X, Y, Z axes in world space."""
        try:
            tm = obj.transform
            x_axis = rt.Point3(tm.row1.x, tm.row1.y, tm.row1.z)
            y_axis = rt.Point3(tm.row2.x, tm.row2.y, tm.row2.z)
            z_axis = rt.Point3(tm.row3.x, tm.row3.y, tm.row3.z)
            return [x_axis, y_axis, z_axis]
        except:
            return None
    
    @staticmethod
    def detect_axis_flips_at_zero(obj_a, obj_b):
        """
        Detect axis flips by temporarily resetting to zero pose,
        comparing orientations, then restoring.
        """
        cache_key = WorldSpaceMirror.get_pair_key(obj_a, obj_b)
        
        if cache_key in WorldSpaceMirror._axis_flip_cache:
            return WorldSpaceMirror._axis_flip_cache[cache_key]
        
        # Save current rotations
        orig_a = WorldSpaceMirror.get_local_rotation(obj_a)
        orig_b = WorldSpaceMirror.get_local_rotation(obj_b)
        
        if orig_a is None or orig_b is None:
            return [True, False, False]  # Default fallback
        
        # Reset both to zero
        WorldSpaceMirror.set_local_rotation(obj_a, [0.0, 0.0, 0.0])
        WorldSpaceMirror.set_local_rotation(obj_b, [0.0, 0.0, 0.0])
        
        # Get axes at zero pose
        axes_a = WorldSpaceMirror.get_local_axes_in_world(obj_a)
        axes_b = WorldSpaceMirror.get_local_axes_in_world(obj_b)
        
        # Restore original rotations
        WorldSpaceMirror.set_local_rotation(obj_a, orig_a)
        WorldSpaceMirror.set_local_rotation(obj_b, orig_b)
        
        if axes_a is None or axes_b is None:
            return [True, False, False]  # Default fallback
        
        flips = [False, False, False]
        
        for i in range(3):
            axis_a = axes_a[i]
            axis_b = axes_b[i]
            
            # Mirror axis_a across YZ plane (negate X component)
            axis_a_mirrored = rt.Point3(-axis_a.x, axis_a.y, axis_a.z)
            
            # Normalize for comparison
            try:
                axis_a_mirrored = rt.normalize(axis_a_mirrored)
                axis_b_norm = rt.normalize(axis_b)
            except:
                continue
            
            # Dot product tells us alignment after mirroring
            # Close to +1 = mirrored axes align = axis is symmetric = NEEDS negation
            # Close to -1 = mirrored axes oppose = axis is antisymmetric = NO negation
            dot = axis_a_mirrored.x * axis_b_norm.x + \
                  axis_a_mirrored.y * axis_b_norm.y + \
                  axis_a_mirrored.z * axis_b_norm.z
            
            # If mirrored axes align (positive dot), the axis is mirrored, needs negation
            # If mirrored axes oppose (negative dot), the axis stays consistent, no negation
            flips[i] = (dot > 0)
        
        WorldSpaceMirror._axis_flip_cache[cache_key] = flips
        return flips
    
    @staticmethod
    def apply_flips(rot, flips):
        """Apply axis flips to rotation values."""
        if rot is None:
            return None
        result = rot[:]
        for i in range(3):
            if flips[i]:
                result[i] = -result[i]
        return result
    
    @staticmethod
    def get_test_point(obj):
        """Get a point that moves when the object rotates."""
        try:
            # Try to find a child
            if obj.children and len(obj.children) > 0:
                return obj.children[0].transform.position
            
            # Otherwise, project a point along local X axis
            tm = obj.transform
            pos = tm.position
            offset = 10.0
            test_point = rt.Point3(
                pos.x + tm.row1.x * offset,
                pos.y + tm.row1.y * offset,
                pos.z + tm.row1.z * offset
            )
            return test_point
        except:
            return None
    
    @staticmethod
    def set_manual_flips(obj_a, obj_b, flip_x, flip_y, flip_z):
        """
        Manually set which axes to flip for this pair.
        Use this when auto-detection doesn't work.
        
        flip_x, flip_y, flip_z: True = negate, False = direct copy
        """
        cache_key = WorldSpaceMirror.get_pair_key(obj_a, obj_b)
        flips = [flip_x, flip_y, flip_z]
        WorldSpaceMirror._axis_flip_cache[cache_key] = flips
        
        print(f"Manually set flips for {obj_a.name} <-> {obj_b.name}:")
        print(f"  X: {'NEGATE' if flip_x else 'DIRECT'}")
        print(f"  Y: {'NEGATE' if flip_y else 'DIRECT'}")
        print(f"  Z: {'NEGATE' if flip_z else 'DIRECT'}")
        
        return flips
    
    @staticmethod
    def test_flip_combination(obj_a, obj_b, flip_x, flip_y, flip_z, test_angle=45.0):
        """
        Test a specific flip combination by rotating A and showing what B becomes.
        Helps you visually verify if a combination is correct.
        """
        print(f"\nTesting combination: X={'NEG' if flip_x else 'DIR'}, Y={'NEG' if flip_y else 'DIR'}, Z={'NEG' if flip_z else 'DIR'}")
        
        # Save originals
        orig_a = WorldSpaceMirror.get_local_rotation(obj_a)
        orig_b = WorldSpaceMirror.get_local_rotation(obj_b)
        
        flips = [flip_x, flip_y, flip_z]
        
        tests = [
            ([test_angle, 0, 0], "X-axis"),
            ([0, test_angle, 0], "Y-axis"),
            ([0, 0, test_angle], "Z-axis"),
        ]
        
        for test_rot, name in tests:
            # Reset both
            WorldSpaceMirror.set_local_rotation(obj_a, [0, 0, 0])
            WorldSpaceMirror.set_local_rotation(obj_b, [0, 0, 0])
            
            # Apply test
            WorldSpaceMirror.set_local_rotation(obj_a, test_rot)
            mirrored = WorldSpaceMirror.apply_flips(test_rot, flips)
            WorldSpaceMirror.set_local_rotation(obj_b, mirrored)
            
            print(f"  {name}: A {test_rot} -> B {mirrored}")
            rt.redrawViews()
            
        # Restore
        WorldSpaceMirror.set_local_rotation(obj_a, orig_a)
        WorldSpaceMirror.set_local_rotation(obj_b, orig_b)
        
        print("Check if B mirrors A correctly, then try another combination if needed.\n")
    
    @staticmethod
    def manual_flip_test(obj_a, obj_b):
        """
        Interactive test - manually rotate obj_a and see which flip makes obj_b mirror it.
        User observes visually which combination works.
        """
        print(f"\n{'='*60}")
        print(f"Manual Flip Test: {obj_a.name} <-> {obj_b.name}")
        print(f"{'='*60}")
        print("\nI'll rotate A by +45° on each axis.")
        print("Watch B and tell me which combination looks correct!")
        print()
        
        # Save originals
        orig_a = WorldSpaceMirror.get_local_rotation(obj_a)
        orig_b = WorldSpaceMirror.get_local_rotation(obj_b)
        
        test_angle = 45.0
        test_cases = [
            ([test_angle, 0, 0], "X"),
            ([0, test_angle, 0], "Y"),
            ([0, 0, test_angle], "Z"),
        ]
        
        import itertools
        combo_num = 1
        
        for flip_x, flip_y, flip_z in itertools.product([False, True], repeat=3):
            flips = [flip_x, flip_y, flip_z]
            flip_name = f"[{'N' if flip_x else 'D'}, {'N' if flip_y else 'D'}, {'N' if flip_z else 'D'}]"
            
            print(f"\n--- Combination #{combo_num}: {flip_name} ---")
            
            for test_rot, axis_name in test_cases:
                # Reset both
                WorldSpaceMirror.set_local_rotation(obj_a, [0, 0, 0])
                WorldSpaceMirror.set_local_rotation(obj_b, [0, 0, 0])
                
                # Rotate A
                WorldSpaceMirror.set_local_rotation(obj_a, test_rot)
                
                # Mirror to B with this flip combo
                mirrored = WorldSpaceMirror.apply_flips(test_rot, flips)
                WorldSpaceMirror.set_local_rotation(obj_b, mirrored)
                
                print(f"  {axis_name}: A={test_rot} -> B={mirrored}")
                
                # Pause so user can see
                input(f"    Press Enter to continue...")
            
            combo_num += 1
        
        # Restore
        WorldSpaceMirror.set_local_rotation(obj_a, orig_a)
        WorldSpaceMirror.set_local_rotation(obj_b, orig_b)
        
        print("\n" + "="*60)
        print("Which combination looked correct? Enter the number (1-8):")
        
    @staticmethod
    def test_all_flip_combinations(obj_a, obj_b):
        """
        Brute force test all 8 possible flip combinations to find the correct one.
        Tests by rotating and checking if endpoint movements mirror properly.
        """
        import itertools
        
        print(f"\n{'='*60}")
        print(f"Testing all flip combinations: {obj_a.name} <-> {obj_b.name}")
        print(f"{'='*60}")
        
        # Save originals
        orig_a = WorldSpaceMirror.get_local_rotation(obj_a)
        orig_b = WorldSpaceMirror.get_local_rotation(obj_b)
        
        # Test each axis independently
        test_configs = [
            ([30.0, 0.0, 0.0], "X-axis test"),
            ([0.0, 30.0, 0.0], "Y-axis test"),
            ([0.0, 0.0, 30.0], "Z-axis test"),
        ]
        
        results = {}
        
        for flip_x, flip_y, flip_z in itertools.product([False, True], repeat=3):
            flips = [flip_x, flip_y, flip_z]
            flip_key = (flip_x, flip_y, flip_z)
            total_error = 0
            
            for test_rot, test_name in test_configs:
                # Reset to zero
                WorldSpaceMirror.set_local_rotation(obj_a, [0.0, 0.0, 0.0])
                WorldSpaceMirror.set_local_rotation(obj_b, [0.0, 0.0, 0.0])
                
                # Get zero positions
                zero_point_a = WorldSpaceMirror.get_test_point(obj_a)
                zero_point_b = WorldSpaceMirror.get_test_point(obj_b)
                
                if zero_point_a is None or zero_point_b is None:
                    total_error += 999999
                    continue
                
                # Apply test rotation to A
                WorldSpaceMirror.set_local_rotation(obj_a, test_rot)
                rotated_point_a = WorldSpaceMirror.get_test_point(obj_a)
                
                # Apply mirrored rotation to B
                mirrored_rot = WorldSpaceMirror.apply_flips(test_rot, flips)
                WorldSpaceMirror.set_local_rotation(obj_b, mirrored_rot)
                rotated_point_b = WorldSpaceMirror.get_test_point(obj_b)
                
                if rotated_point_a is None or rotated_point_b is None:
                    total_error += 999999
                    continue
                
                # Calculate movement vectors
                move_a = rt.Point3(
                    rotated_point_a.x - zero_point_a.x,
                    rotated_point_a.y - zero_point_a.y,
                    rotated_point_a.z - zero_point_a.z
                )
                move_b = rt.Point3(
                    rotated_point_b.x - zero_point_b.x,
                    rotated_point_b.y - zero_point_b.y,
                    rotated_point_b.z - zero_point_b.z
                )
                
                # Mirror A's movement
                move_a_mirrored = rt.Point3(-move_a.x, move_a.y, move_a.z)
                
                # Calculate error (how different the movements are)
                error = rt.distance(move_a_mirrored, move_b)
                total_error += error
            
            results[flip_key] = total_error
        
        # Restore originals
        WorldSpaceMirror.set_local_rotation(obj_a, orig_a)
        WorldSpaceMirror.set_local_rotation(obj_b, orig_b)
        
        # Find best
        best_combo = min(results.keys(), key=lambda k: results[k])
        best_score = results[best_combo]
        
        # Print all results sorted by error
        print("\nAll combinations (sorted by error):")
        for combo in sorted(results.keys(), key=lambda k: results[k]):
            flip_str = f"[{'N' if combo[0] else 'D'}, {'N' if combo[1] else 'D'}, {'N' if combo[2] else 'D'}]"
            print(f"  {flip_str}: error = {results[combo]:.4f}")
        
        print(f"\nBest combination (lowest error = {best_score:.4f}):")
        print(f"  X-axis: {'NEGATE' if best_combo[0] else 'DIRECT COPY'}")
        print(f"  Y-axis: {'NEGATE' if best_combo[1] else 'DIRECT COPY'}")
        print(f"  Z-axis: {'NEGATE' if best_combo[2] else 'DIRECT COPY'}")
        print(f"{'='*60}\n")
        
        return list(best_combo)
    
    @staticmethod
    def swap_transforms(obj_a, obj_b):
        """Swap transforms with zero-pose analysis."""
        pos_a = WorldSpaceMirror.get_position(obj_a)
        pos_b = WorldSpaceMirror.get_position(obj_b)
        rot_a = WorldSpaceMirror.get_local_rotation(obj_a)
        rot_b = WorldSpaceMirror.get_local_rotation(obj_b)
        
        if rot_a is None or rot_b is None:
            return False
        
        # Detect which axes need flipping (uses zero pose internally)
        flips = WorldSpaceMirror.detect_axis_flips_at_zero(obj_a, obj_b)
        
        # Apply flips
        mir_rot_a = WorldSpaceMirror.apply_flips(rot_a, flips)
        mir_rot_b = WorldSpaceMirror.apply_flips(rot_b, flips)
        
        # Mirror positions
        mir_pos_a = WorldSpaceMirror.mirror_position(pos_a)
        mir_pos_b = WorldSpaceMirror.mirror_position(pos_b)
        
        success = True
        
        # A gets B's values
        if mir_pos_b:
            success = WorldSpaceMirror.set_position(obj_a, mir_pos_b) and success
        success = WorldSpaceMirror.set_local_rotation(obj_a, mir_rot_b) and success
        
        # B gets A's values
        if mir_pos_a:
            success = WorldSpaceMirror.set_position(obj_b, mir_pos_a) and success
        success = WorldSpaceMirror.set_local_rotation(obj_b, mir_rot_a) and success
        
        return success
    
    @staticmethod
    def mirror_transform(source_obj, target_obj):
        """Mirror from source to target with zero-pose analysis."""
        pos = WorldSpaceMirror.get_position(source_obj)
        rot = WorldSpaceMirror.get_local_rotation(source_obj)
        
        if rot is None:
            return False
        
        flips = WorldSpaceMirror.detect_axis_flips_at_zero(source_obj, target_obj)
        mir_rot = WorldSpaceMirror.apply_flips(rot, flips)
        mir_pos = WorldSpaceMirror.mirror_position(pos)
        
        success = True
        if mir_pos:
            success = WorldSpaceMirror.set_position(target_obj, mir_pos) and success
        success = WorldSpaceMirror.set_local_rotation(target_obj, mir_rot) and success
        
        return success
    
    @staticmethod
    def debug_pair(obj_a, obj_b):
        """Debug with zero-pose analysis."""
        rot_a = WorldSpaceMirror.get_local_rotation(obj_a)
        rot_b = WorldSpaceMirror.get_local_rotation(obj_b)
        
        print(f"\n{'='*60}")
        print(f"Mirror Debug: {obj_a.name} <-> {obj_b.name}")
        print(f"{'='*60}")
        
        # Check rotation orders
        order_a = WorldSpaceMirror.get_rotation_order(obj_a)
        order_b = WorldSpaceMirror.get_rotation_order(obj_b)
        print(f"\nRotation Orders:")
        print(f"  A: {order_a}")
        print(f"  B: {order_b}")
        if order_a != order_b:
            print(f"  WARNING: Different rotation orders detected!")
        
        print(f"\nCurrent rotations:")
        print(f"  A: X={rot_a[0]:.2f}, Y={rot_a[1]:.2f}, Z={rot_a[2]:.2f}")
        print(f"  B: X={rot_b[0]:.2f}, Y={rot_b[1]:.2f}, Z={rot_b[2]:.2f}")
        
        # Save current
        orig_a = rot_a[:]
        orig_b = rot_b[:]
        
        # Go to zero and analyze
        WorldSpaceMirror.set_local_rotation(obj_a, [0.0, 0.0, 0.0])
        WorldSpaceMirror.set_local_rotation(obj_b, [0.0, 0.0, 0.0])
        
        axes_a = WorldSpaceMirror.get_local_axes_in_world(obj_a)
        axes_b = WorldSpaceMirror.get_local_axes_in_world(obj_b)
        
        print(f"\nWorld-space local axes at ZERO pose:")
        if axes_a and axes_b:
            axis_names = ['X', 'Y', 'Z']
            for i in range(3):
                axis_a = axes_a[i]
                axis_b = axes_b[i]
                axis_a_mir = rt.Point3(-axis_a.x, axis_a.y, axis_a.z)
                
                try:
                    axis_a_mir_norm = rt.normalize(axis_a_mir)
                    axis_b_norm = rt.normalize(axis_b)
                    dot = axis_a_mir_norm.x * axis_b_norm.x + \
                          axis_a_mir_norm.y * axis_b_norm.y + \
                          axis_a_mir_norm.z * axis_b_norm.z
                except:
                    dot = 0
                
                print(f"  {axis_names[i]}-axis:")
                print(f"    A: ({axis_a.x:.3f}, {axis_a.y:.3f}, {axis_a.z:.3f})")
                print(f"    B: ({axis_b.x:.3f}, {axis_b.y:.3f}, {axis_b.z:.3f})")
                print(f"    A mirrored: ({-axis_a.x:.3f}, {axis_a.y:.3f}, {axis_a.z:.3f})")
                print(f"    Dot product: {dot:.3f} ({'same' if dot > 0 else 'opposite'})")
        
        # Restore
        WorldSpaceMirror.set_local_rotation(obj_a, orig_a)
        WorldSpaceMirror.set_local_rotation(obj_b, orig_b)
        
        flips = WorldSpaceMirror.detect_axis_flips_at_zero(obj_a, obj_b)
        
        print(f"\nAxis flip detection:")
        axis_names = ['X', 'Y', 'Z']
        for i in range(3):
            status = "NEGATE" if flips[i] else "DIRECT COPY"
            print(f"  {axis_names[i]}-axis: {status}")
        
        mir_rot_a = WorldSpaceMirror.apply_flips(rot_a, flips)
        mir_rot_b = WorldSpaceMirror.apply_flips(rot_b, flips)
        
        print(f"\nAfter swap:")
        print(f"  A will get: X={mir_rot_b[0]:.2f}, Y={mir_rot_b[1]:.2f}, Z={mir_rot_b[2]:.2f}")
        print(f"  B will get: X={mir_rot_a[0]:.2f}, Y={mir_rot_a[1]:.2f}, Z={mir_rot_a[2]:.2f}")
        print(f"{'='*60}\n")
        

        
class PositionMirror:
    """Mirror position values for IK controls and position-based rigs."""
    
    _position_flip_cache = {}
    _position_profiles = {}
    
    @staticmethod
    def clear_cache():
        PositionMirror._position_flip_cache = {}
    
    @staticmethod
    def save_position_profile(profile_name, flip_x, flip_y, flip_z):
        """
        Save a position flip pattern with a name for reuse.
        flip_x should almost always be True (mirror across YZ plane)
        flip_y and flip_z depend on the rig's coordinate system
        """
        PositionMirror._position_profiles[profile_name] = [flip_x, flip_y, flip_z]
        print(f"Saved position profile '{profile_name}':")
        print(f"  X: {'NEGATE' if flip_x else 'DIRECT'}")
        print(f"  Y: {'NEGATE' if flip_y else 'DIRECT'}")
        print(f"  Z: {'NEGATE' if flip_z else 'DIRECT'}")
    
    @staticmethod
    def apply_position_profile(profile_name, obj_a, obj_b):
        """Apply a saved position profile to a specific pair."""
        if profile_name not in PositionMirror._position_profiles:
            print(f"Error: Profile '{profile_name}' not found!")
            return False
        
        flips = PositionMirror._position_profiles[profile_name]
        cache_key = tuple(sorted([str(obj_a.name), str(obj_b.name)]))
        PositionMirror._position_flip_cache[cache_key] = flips
        
        print(f"Applied position profile '{profile_name}' to {obj_a.name} <-> {obj_b.name}")
        return True
    
    @staticmethod
    def set_manual_position_flips(obj_a, obj_b, flip_x, flip_y, flip_z):
        """Manually set which position axes to flip for this pair."""
        cache_key = tuple(sorted([str(obj_a.name), str(obj_b.name)]))
        flips = [flip_x, flip_y, flip_z]
        PositionMirror._position_flip_cache[cache_key] = flips
        
        print(f"Manually set position flips for {obj_a.name} <-> {obj_b.name}:")
        print(f"  X: {'NEGATE' if flip_x else 'DIRECT'}")
        print(f"  Y: {'NEGATE' if flip_y else 'DIRECT'}")
        print(f"  Z: {'NEGATE' if flip_z else 'DIRECT'}")
        
        return flips
    
    @staticmethod
    def get_position_flips(obj_a, obj_b):
        """Get position flips from cache or use default (negate X only)."""
        cache_key = tuple(sorted([str(obj_a.name), str(obj_b.name)]))
        if cache_key in PositionMirror._position_flip_cache:
            return PositionMirror._position_flip_cache[cache_key]
        # Default: only negate X (standard world-space mirror)
        return [True, False, False]
    
    @staticmethod
    def apply_position_flips(pos, flips):
        """Apply axis flips to position values."""
        if pos is None:
            return None
        result = pos[:]
        for i in range(3):
            if flips[i]:
                result[i] = -result[i]
        return result
    
    @staticmethod
    def test_position_flip_combination(obj_a, obj_b, flip_x, flip_y, flip_z, test_offset=10.0):
        """
        Test a specific position flip combination.
        Moves A and shows what B becomes - watch visually to verify.
        """
        print(f"\nTesting position combination: X={'NEG' if flip_x else 'DIR'}, Y={'NEG' if flip_y else 'DIR'}, Z={'NEG' if flip_z else 'DIR'}")
        
        # Save originals
        orig_a = PositionMirror.get_position(obj_a)
        orig_b = PositionMirror.get_position(obj_b)
        
        flips = [flip_x, flip_y, flip_z]
        
        tests = [
            ([orig_a[0] + test_offset, orig_a[1], orig_a[2]], "X-axis"),
            ([orig_a[0], orig_a[1] + test_offset, orig_a[2]], "Y-axis"),
            ([orig_a[0], orig_a[1], orig_a[2] + test_offset], "Z-axis"),
        ]
        
        for test_pos, name in tests:
            # Reset both
            PositionMirror.set_position(obj_a, orig_a)
            PositionMirror.set_position(obj_b, orig_b)
            
            # Move A
            PositionMirror.set_position(obj_a, test_pos)
            
            # Calculate mirrored position for B
            mirrored = PositionMirror.apply_position_flips(test_pos, flips)
            PositionMirror.set_position(obj_b, mirrored)
            
            delta = [test_pos[i] - orig_a[i] for i in range(3)]
            mir_delta = [mirrored[i] - orig_b[i] for i in range(3)]
            
            print(f"  {name}: A moved by ({delta[0]:.1f}, {delta[1]:.1f}, {delta[2]:.1f})")
            print(f"         B moved by ({mir_delta[0]:.1f}, {mir_delta[1]:.1f}, {mir_delta[2]:.1f})")
            rt.redrawViews()
            
        # Restore
        PositionMirror.set_position(obj_a, orig_a)
        PositionMirror.set_position(obj_b, orig_b)
        
        print("Check if B mirrors A correctly.\n")
    
    @staticmethod
    def get_position(obj):
        """Get position from controller or transform."""
        ctrl = get_controller(obj, "position")
        if ctrl and is_xyz_controller(ctrl):
            try:
                vals = [0.0, 0.0, 0.0]
                for i in range(3):
                    sub = ctrl[i]
                    sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                    sub_ctrl = resolve_controller(sub_ctrl)
                    if sub_ctrl:
                        vals[i] = float(sub_ctrl.value)
                return vals
            except:
                pass
        try:
            pos = obj.position
            return [float(pos.x), float(pos.y), float(pos.z)]
        except:
            return None
    
    @staticmethod
    def set_position(obj, vals):
        """Set position on controller or transform."""
        ctrl = get_controller(obj, "position")
        if ctrl and is_xyz_controller(ctrl):
            try:
                for i in range(3):
                    sub = ctrl[i]
                    sub_ctrl = sub.controller if hasattr(sub, 'controller') else sub
                    sub_ctrl = resolve_controller(sub_ctrl)
                    if sub_ctrl:
                        sub_ctrl.value = vals[i]
                return True
            except:
                pass
        try:
            obj.position = rt.Point3(vals[0], vals[1], vals[2])
            return True
        except:
            return False
    
    @staticmethod
    def swap_positions(obj_a, obj_b):
        """Swap positions between two objects with mirroring."""
        pos_a = PositionMirror.get_position(obj_a)
        pos_b = PositionMirror.get_position(obj_b)
        
        if pos_a is None or pos_b is None:
            print("Error: Could not get positions")
            return False
        
        # Get flips for this pair
        flips = PositionMirror.get_position_flips(obj_a, obj_b)
        
        # Mirror both positions with flips
        mir_pos_a = PositionMirror.apply_position_flips(pos_a, flips)
        mir_pos_b = PositionMirror.apply_position_flips(pos_b, flips)
        
        # Swap them
        success = True
        success = PositionMirror.set_position(obj_a, mir_pos_b) and success
        success = PositionMirror.set_position(obj_b, mir_pos_a) and success
        
        return success
    
    @staticmethod
    def mirror_position_from_to(source_obj, target_obj):
        """Mirror position from source to target."""
        pos = PositionMirror.get_position(source_obj)
        
        if pos is None:
            print("Error: Could not get source position")
            return False
        
        # Get flips for this pair
        flips = PositionMirror.get_position_flips(source_obj, target_obj)
        mir_pos = PositionMirror.apply_position_flips(pos, flips)
        
        success = PositionMirror.set_position(target_obj, mir_pos)
        
        return success
    
    @staticmethod
    def test_position_control(obj):
        """Test if we can actually control the position of this object."""
        print(f"\n{'='*60}")
        print(f"Testing position control: {obj.name}")
        print(f"{'='*60}")
        
        # Get current position
        orig_pos = PositionMirror.get_position(obj)
        if orig_pos is None:
            print("ERROR: Cannot read position!")
            return False
        
        print(f"Original position: X={orig_pos[0]:.3f}, Y={orig_pos[1]:.3f}, Z={orig_pos[2]:.3f}")
        
        # Try to move it slightly
        test_pos = [orig_pos[0] + 5.0, orig_pos[1], orig_pos[2]]
        print(f"Attempting to set: X={test_pos[0]:.3f}, Y={test_pos[1]:.3f}, Z={test_pos[2]:.3f}")
        
        success = PositionMirror.set_position(obj, test_pos)
        if not success:
            print("ERROR: set_position returned False!")
            return False
        
        # Read it back
        new_pos = PositionMirror.get_position(obj)
        print(f"Position after set: X={new_pos[0]:.3f}, Y={new_pos[1]:.3f}, Z={new_pos[2]:.3f}")
        
        # Check if it actually changed
        if abs(new_pos[0] - test_pos[0]) < 0.001:
            print("SUCCESS: Position is controllable!")
            result = True
        else:
            print("WARNING: Position did not change as expected!")
            print("This control may be:")
            print("  - Locked")
            print("  - Constrained to another object")
            print("  - Controlled by a different controller type")
            result = False
        
        # Restore original
        PositionMirror.set_position(obj, orig_pos)
        print(f"Restored to original position")
        print(f"{'='*60}\n")
        
        return result
    
    @staticmethod
    def debug_positions(obj_a, obj_b):
        """Show position information for debugging."""
        pos_a = PositionMirror.get_position(obj_a)
        pos_b = PositionMirror.get_position(obj_b)
        
        print(f"\n{'='*60}")
        print(f"Position Debug: {obj_a.name} <-> {obj_b.name}")
        print(f"{'='*60}")
        
        if pos_a:
            print(f"\nA position: X={pos_a[0]:.3f}, Y={pos_a[1]:.3f}, Z={pos_a[2]:.3f}")
            mir_a = PositionMirror.mirror_position(pos_a)
            print(f"A mirrored: X={mir_a[0]:.3f}, Y={mir_a[1]:.3f}, Z={mir_a[2]:.3f}")
        else:
            print("\nA position: Could not read")
        
        if pos_b:
            print(f"\nB position: X={pos_b[0]:.3f}, Y={pos_b[1]:.3f}, Z={pos_b[2]:.3f}")
            mir_b = PositionMirror.mirror_position(pos_b)
            print(f"B mirrored: X={mir_b[0]:.3f}, Y={mir_b[1]:.3f}, Z={mir_b[2]:.3f}")
        else:
            print("\nB position: Could not read")
        
        print(f"\nAfter swap:")
        if pos_a and pos_b:
            mir_pos_a = PositionMirror.mirror_position(pos_a)
            mir_pos_b = PositionMirror.mirror_position(pos_b)
            print(f"  A will get: X={mir_pos_b[0]:.3f}, Y={mir_pos_b[1]:.3f}, Z={mir_pos_b[2]:.3f}")
            print(f"  B will get: X={mir_pos_a[0]:.3f}, Y={mir_pos_a[1]:.3f}, Z={mir_pos_a[2]:.3f}")
        
        print(f"{'='*60}\n")    

class AttributeMirror:
    """
    Mirror custom attributes on controllers.
    Only targets Attribute Holder modifiers, not base object properties.
    """
    
    _attribute_flip_cache = {}
    
    @staticmethod
    def clear_cache():
        AttributeMirror._attribute_flip_cache = {}

    @staticmethod
    def _normalize_attr_name(attr_name):
        """Normalize attribute names by replacing spaces with underscores."""
        return str(attr_name).replace(' ', '_').replace('#', '')

    @staticmethod
    def _get_attribute_holder_modifiers(obj):
        """
        Get Attribute Holder modifiers.
        Returns list of (modifier, modifier_index) tuples.
        """
        results = []
        
        if hasattr(obj, 'modifiers'):
            for idx, mod in enumerate(obj.modifiers):
                mod_class = str(rt.classOf(mod))
                mod_name = str(mod.name).lower()
                
                is_attribute_holder = (
                    "EmptyModifier" in mod_class or 
                    "attribute" in mod_name or
                    "Attribute" in str(mod.name)
                )
                
                if is_attribute_holder:
                    results.append((mod, idx + 1))  # 1-based index for MaxScript
        
        return results

    @staticmethod
    def _get_props_via_execute(obj, mod_index, ca_index):
        """Get property names using rt.execute (workaround for getPropNames bug)."""
        try:
            obj_name = str(obj.name)
            # Escape special characters in name
            obj_name_escaped = obj_name.replace("'", "\\'")
            
            cmd = f'getPropNames (custAttributes.get $\'{obj_name_escaped}\'.modifiers[{mod_index}] {ca_index})'
            result = rt.execute(cmd)
            
            if result:
                # Convert MaxScript array to Python list of strings
                props = []
                for item in result:
                    prop_str = str(item).replace('#', '')
                    props.append(prop_str)
                return props
        except Exception as e:
            print(f"_get_props_via_execute error: {e}")
        
        return []

    @staticmethod
    def list_custom_attributes(obj):
        """
        List ONLY attributes from Attribute Holder modifiers.
        Uses rt.execute workaround for getPropNames.
        """
        found = []
        
        mods = AttributeMirror._get_attribute_holder_modifiers(obj)
        
        for mod, mod_index in mods:
            try:
                ca_count = rt.custAttributes.count(mod)
                for ca_index in range(1, ca_count + 1):
                    props = AttributeMirror._get_props_via_execute(obj, mod_index, ca_index)
                    for p in props:
                        if p not in found:
                            found.append(p)
            except:
                pass
        
        return found

    @staticmethod
    def list_custom_attributes_verbose(obj):
        """List attributes with full output for debugging."""
        print(f"\n{'='*60}")
        print(f"Attribute Holder Attributes on: {obj.name}")
        print(f"{'='*60}")
        
        found = []
        mods = AttributeMirror._get_attribute_holder_modifiers(obj)
        
        if not mods:
            print("No Attribute Holder modifiers found!")
            return found
        
        for mod, mod_index in mods:
            try:
                ca_count = rt.custAttributes.count(mod)
                print(f"\nModifier '{mod.name}' (index {mod_index}): {ca_count} CA(s)")
                
                for ca_index in range(1, ca_count + 1):
                    ca = rt.custAttributes.get(mod, ca_index)
                    ca_name = ca.name if hasattr(ca, 'name') and ca.name else f"CA_{ca_index}"
                    print(f"  CA[{ca_index}]: {ca_name}")
                    
                    props = AttributeMirror._get_props_via_execute(obj, mod_index, ca_index)
                    for p in props:
                        try:
                            val = rt.getProperty(ca, p)
                            print(f"    {p} = {val}")
                            if p not in found:
                                found.append(p)
                        except:
                            print(f"    {p} = (error)")
            except Exception as e:
                print(f"  Error: {e}")
        
        print(f"\n--- Total: {len(found)} attributes ---")
        print(f"{'='*60}\n")
        
        return found

    @staticmethod
    def _find_attribute_container(obj, attr_name):
        """
        Find which CA container holds a specific attribute.
        Returns (ca_def, actual_property_name) or (None, None)
        """
        normalized = AttributeMirror._normalize_attr_name(attr_name)
        names_to_try = [attr_name, normalized]
        if attr_name != normalized:
            names_to_try = [normalized, attr_name]
        
        mods = AttributeMirror._get_attribute_holder_modifiers(obj)
        
        for mod, mod_index in mods:
            try:
                ca_count = rt.custAttributes.count(mod)
                for ca_index in range(1, ca_count + 1):
                    ca = rt.custAttributes.get(mod, ca_index)
                    
                    for name in names_to_try:
                        try:
                            # Test if property exists by trying to get it
                            val = rt.getProperty(ca, name)
                            return ca, name
                        except:
                            pass
            except:
                pass
        
        return None, None

    @staticmethod
    def get_custom_attribute(obj, attr_name):
        """Get a custom attribute value."""
        container, real_name = AttributeMirror._find_attribute_container(obj, attr_name)
        if container:
            try:
                return rt.getProperty(container, real_name)
            except:
                pass
        return None

    @staticmethod
    def set_custom_attribute(obj, attr_name, value):
        """Set a custom attribute value."""
        container, real_name = AttributeMirror._find_attribute_container(obj, attr_name)
        if container:
            try:
                rt.setProperty(container, real_name, value)
                return True
            except:
                pass
        return False

    @staticmethod
    def set_attribute_flip(obj_a, obj_b, attr_name, should_negate):
        """Set whether an attribute should be negated when mirroring."""
        normalized_name = AttributeMirror._normalize_attr_name(attr_name)
        cache_key = tuple(sorted([str(obj_a.name), str(obj_b.name)]))
        
        if cache_key not in AttributeMirror._attribute_flip_cache:
            AttributeMirror._attribute_flip_cache[cache_key] = {}
        
        AttributeMirror._attribute_flip_cache[cache_key][normalized_name] = should_negate
    
    @staticmethod
    def get_attribute_flip(obj_a, obj_b, attr_name):
        """Get whether an attribute should be negated."""
        normalized_name = AttributeMirror._normalize_attr_name(attr_name)
        cache_key = tuple(sorted([str(obj_a.name), str(obj_b.name)]))
        
        if cache_key in AttributeMirror._attribute_flip_cache:
            if normalized_name in AttributeMirror._attribute_flip_cache[cache_key]:
                return AttributeMirror._attribute_flip_cache[cache_key][normalized_name]
        
        return False

    @staticmethod
    def mirror_attribute(source_obj, target_obj, attr_name, negate=None):
        """Mirror a single attribute from source to target."""
        val = AttributeMirror.get_custom_attribute(source_obj, attr_name)
        if val is None:
            return False

        if negate is None:
            should_negate = AttributeMirror.get_attribute_flip(source_obj, target_obj, attr_name)
        else:
            should_negate = negate

        final_val = val
        if should_negate:
            try:
                final_val = -float(val)
            except:
                pass

        return AttributeMirror.set_custom_attribute(target_obj, attr_name, final_val)
    
    @staticmethod
    def swap_attribute(obj_a, obj_b, attr_name):
        """Swap a single attribute between two objects."""
        val_a = AttributeMirror.get_custom_attribute(obj_a, attr_name)
        val_b = AttributeMirror.get_custom_attribute(obj_b, attr_name)
        
        if val_a is None or val_b is None:
            return False
        
        should_negate = AttributeMirror.get_attribute_flip(obj_a, obj_b, attr_name)
        
        new_val_a = val_b
        new_val_b = val_a
        
        if should_negate:
            try:
                new_val_a = -float(val_b)
                new_val_b = -float(val_a)
            except:
                pass

        ok_a = AttributeMirror.set_custom_attribute(obj_a, attr_name, new_val_a)
        ok_b = AttributeMirror.set_custom_attribute(obj_b, attr_name, new_val_b)
        
        return ok_a and ok_b

    @staticmethod
    def mirror_attributes(source_obj, target_obj, attr_names):
        """Mirror multiple attributes from source to target."""
        count = 0
        if isinstance(attr_names, list):
            for attr_name in attr_names:
                if AttributeMirror.mirror_attribute(source_obj, target_obj, attr_name):
                    count += 1
        elif isinstance(attr_names, dict):
            for attr_name, negate in attr_names.items():
                if AttributeMirror.mirror_attribute(source_obj, target_obj, attr_name, negate):
                    count += 1
        return count
    
    @staticmethod
    def swap_attributes(obj_a, obj_b, attr_names):
        """Swap multiple attributes between two objects."""
        count = 0
        for attr_name in attr_names:
            if AttributeMirror.swap_attribute(obj_a, obj_b, attr_name):
                count += 1
        return count


# ADD THIS HELPER CLASS FOR POSE MIRRORING WITH ATTRIBUTES
class PoseAttributeMirror:
    """
    Helper class to mirror both transforms and custom attributes together.
    """
    
    @staticmethod
    def mirror_with_attributes(source_obj, target_obj, attr_names=None, mode='mirror'):
        """
        Mirror both transform and custom attributes.
        
        Args:
            source_obj: Source object
            target_obj: Target object
            attr_names: List of attribute names to mirror (None = auto-detect)
            mode: 'mirror' = copy source to target, 'swap' = swap both
        
        Returns:
            True if successful
        """
        success = True
        
        # Auto-detect attributes if not provided
        if attr_names is None:
            attr_names = AttributeMirror.list_custom_attributes(source_obj)
        
        if mode == 'swap':
            # Swap attributes
            print(f"    Swapping attributes between {source_obj.name} <-> {target_obj.name}")
            AttributeMirror.swap_attributes(source_obj, target_obj, attr_names)
        else:
            # Mirror attributes (source -> target)
            print(f"    Mirroring attributes {source_obj.name} -> {target_obj.name}")
            AttributeMirror.mirror_attributes(source_obj, target_obj, attr_names)
        
        return success
        
class PoseToolsDebug:
    @staticmethod
    def mirror_pose(mode=0):
        """
        Debug version of mirror_pose.
        Prints exactly what is happening step-by-step.
        """
        print(f"\n{'='*20} START MIRROR DEBUG {'='*20}")
        
        if rt.selection.count == 0:
            print("ERROR: Nothing selected.")
            return

        selection = list(rt.selection)
        processed = set()
        count = 0
        
        def should_negate_attr(attr_name):
            n = attr_name.lower()
            if "scale" in n or "vis" in n or "volume" in n: 
                return False
            return False  # Default: don't negate, just copy

        with pymxs.undo(True, "Mirror Pose Debug"):
            with pymxs.animate(True):
                for obj in selection:
                    obj_name = str(obj.name)
                    print(f"\nProcessing: {obj_name}")
                    
                    if obj_name in processed:
                        print("  Skipping (already processed).")
                        continue
                    
                    try:
                        pair, side = MirrorPairDetector.find_pair(obj, selection)
                    except NameError:
                        print("  ERROR: MirrorPairDetector class is missing!")
                        break

                    if pair:
                        pair_name = str(pair.name)
                        print(f"  > Pair Found: {pair_name} (Side: {side})")
                        
                        attrs = AttributeMirror.list_custom_attributes(obj)
                        print(f"  > Attributes Found: {attrs}")
                        
                        print(f"  > Mode: {mode}")
                        
                        # Mode 0: Swap
                        if mode == 0:
                            print("    Action: SWAP L <-> R")
                            
                            # Get values BEFORE
                            print("\n    --- BEFORE ---")
                            for attr in attrs:
                                val_obj = AttributeMirror.get_custom_attribute(obj, attr)
                                val_pair = AttributeMirror.get_custom_attribute(pair, attr)
                                print(f"      {attr}: {obj_name}={val_obj}, {pair_name}={val_pair}")
                            
                            # Do the swap
                            for attr in attrs:
                                success = AttributeMirror.swap_attribute(obj, pair, attr)
                                print(f"    Swapping '{attr}': {'OK' if success else 'FAILED'}")
                            
                            # Get values AFTER
                            print("\n    --- AFTER ---")
                            for attr in attrs:
                                val_obj = AttributeMirror.get_custom_attribute(obj, attr)
                                val_pair = AttributeMirror.get_custom_attribute(pair, attr)
                                print(f"      {attr}: {obj_name}={val_obj}, {pair_name}={val_pair}")
                            
                            processed.add(obj_name)
                            processed.add(pair_name)
                            count += 2
                        
                        # Mode 1: Left -> Right
                        elif mode == 1:
                            if side == 'L':
                                print("    Action: Mirroring L -> R")
                                
                                print("\n    --- BEFORE ---")
                                for attr in attrs:
                                    val_obj = AttributeMirror.get_custom_attribute(obj, attr)
                                    val_pair = AttributeMirror.get_custom_attribute(pair, attr)
                                    print(f"      {attr}: {obj_name}={val_obj}, {pair_name}={val_pair}")
                                
                                for attr in attrs:
                                    negate = should_negate_attr(attr)
                                    success = AttributeMirror.mirror_attribute(obj, pair, attr, negate=negate)
                                    print(f"    Mirroring '{attr}' (negate={negate}): {'OK' if success else 'FAILED'}")
                                
                                print("\n    --- AFTER ---")
                                for attr in attrs:
                                    val_obj = AttributeMirror.get_custom_attribute(obj, attr)
                                    val_pair = AttributeMirror.get_custom_attribute(pair, attr)
                                    print(f"      {attr}: {obj_name}={val_obj}, {pair_name}={val_pair}")
                                
                                processed.add(obj_name)
                                processed.add(pair_name)
                                count += 1
                            else:
                                print(f"    Skipping: Object is '{side}', but mode requires 'L'")

                        # Mode 2: Right -> Left
                        elif mode == 2:
                            if side == 'R':
                                print("    Action: Mirroring R -> L")
                                
                                print("\n    --- BEFORE ---")
                                for attr in attrs:
                                    val_obj = AttributeMirror.get_custom_attribute(obj, attr)
                                    val_pair = AttributeMirror.get_custom_attribute(pair, attr)
                                    print(f"      {attr}: {obj_name}={val_obj}, {pair_name}={val_pair}")
                                
                                for attr in attrs:
                                    negate = should_negate_attr(attr)
                                    success = AttributeMirror.mirror_attribute(obj, pair, attr, negate=negate)
                                    print(f"    Mirroring '{attr}' (negate={negate}): {'OK' if success else 'FAILED'}")
                                
                                print("\n    --- AFTER ---")
                                for attr in attrs:
                                    val_obj = AttributeMirror.get_custom_attribute(obj, attr)
                                    val_pair = AttributeMirror.get_custom_attribute(pair, attr)
                                    print(f"      {attr}: {obj_name}={val_obj}, {pair_name}={val_pair}")
                                
                                processed.add(obj_name)
                                processed.add(pair_name)
                                count += 1
                            else:
                                print(f"    Skipping: Object is '{side}', but mode requires 'R'")

                    else:
                        print("  ! NO PAIR FOUND.")

        print(f"\n{'='*20} END DEBUG {'='*20}")
        print(f"Total mirrored: {count}")

# ============================================================================
# TANGENT TOOLS (Improved - AnimBot-style)
# ============================================================================
class TangentTools:
    @staticmethod
    def get_sel_ctrls():
        """Get all animation controllers from selected objects."""
        c = []
        if rt.selection.count == 0: return c
        for o in rt.selection:
            for p in ["position","rotation","scale"]:
                ctrl = get_controller(o, p)
                if not ctrl: continue
                if is_xyz_controller(ctrl):
                    for i in range(3):
                        try: 
                            sub = ctrl[i].controller if hasattr(ctrl[i],'controller') else ctrl[i]
                            sub = resolve_controller(sub)
                            if sub: c.append(sub)
                        except: pass
                else: c.append(ctrl)
        return c
    
    @staticmethod
    def set_native(t):
        """Set native tangent type on selected keys."""
        m = {
            'auto': rt.Name("auto"),
            'smooth': rt.Name("smooth"), 
            'linear': rt.Name("linear"),
            'flat': rt.Name("flat"),
            'step': rt.Name("step"),
            'fast': rt.Name("fast"), 
            'slow': rt.Name("slow"), 
            'custom': rt.Name("custom")
        }
        tn = m.get(t.lower(), rt.Name("auto"))
        count = 0
        with pymxs.animate(True): 
            for c in TangentTools.get_sel_ctrls():
                if rt.numKeys(c) > 0:
                    for k_idx in range(1, rt.numKeys(c) + 1):
                        key = rt.getKey(c, k_idx)
                        if key.selected:
                            try: 
                                key.inTangentType = tn
                                key.outTangentType = tn
                                count += 1
                            except: pass
        rt.redrawViews()
        return t
    
    @staticmethod
    def cycle_match():
        """
        Match first and last key tangents for seamless looping.
        
        AnimBot style: 
        1. Set last key to spline/auto so Maya calculates natural tangent
        2. Copy that calculated tangent to first key's out tangent
        3. Make first key's in tangent match last key's out tangent
        """
        count = 0
        with pymxs.undo(True, "Cycle Match Tangents"):
            with pymxs.animate(True):
                for c in TangentTools.get_sel_ctrls():
                    n = rt.numKeys(c)
                    if n < 2: continue
                    
                    k1 = rt.getKey(c, 1)
                    kn = rt.getKey(c, n)
                    
                    try:
                        # Step 1: Make values match (last = first for perfect loop)
                        kn.value = k1.value
                        
                        # Step 2: Set last key to auto/spline to calculate natural flow
                        kn.inTangentType = rt.Name("auto")
                        kn.outTangentType = rt.Name("auto")
                        
                        # Step 3: Also set first key to auto temporarily
                        k1.inTangentType = rt.Name("auto")
                        k1.outTangentType = rt.Name("auto")
                        
                        # Step 4: Now convert both to custom and match tangents
                        # The out tangent of first key should equal in tangent of last key
                        # And vice versa for seamless loop
                        k1.inTangentType = rt.Name("custom")
                        k1.outTangentType = rt.Name("custom")
                        kn.inTangentType = rt.Name("custom")
                        kn.outTangentType = rt.Name("custom")
                        
                        # Match the tangent slopes
                        # First key's OUT should match Last key's OUT (for looping forward)
                        # Last key's IN should match First key's IN (for looping backward)
                        k1.freeHandle = False
                        kn.freeHandle = False
                        
                        # Get the tangent from the "middle" of the animation
                        # Use first key's calculated out tangent for both
                        if hasattr(k1, 'outTangent'):
                            kn.inTangent = k1.outTangent
                            kn.outTangent = k1.outTangent
                            k1.inTangent = k1.outTangent
                        
                        if hasattr(k1, 'outTangentLength'):
                            kn.inTangentLength = k1.outTangentLength
                        
                        count += 1
                    except Exception as e:
                        pass
                        
        rt.redrawViews()
        return f"Cycle Matched {count} controllers"
    
    @staticmethod
    def best_guess():
        """
        Intelligently guess the best tangent type based on curve context.
        
        AnimBot style:
        - Detects holds (flat tangents)
        - Detects linear segments
        - Detects peaks/valleys (use flat to prevent overshoot)
        - Detects direction changes
        - Falls back to smooth/auto for flowing motion
        """
        count = 0
        hold_tol = 0.001
        linear_tol = 0.05
        
        with pymxs.undo(True, "Best Guess Tangents"):
            with pymxs.animate(True):
                for c in TangentTools.get_sel_ctrls():
                    n = rt.numKeys(c)
                    if n < 2: continue
                    
                    # Collect all key data first
                    key_data = []
                    for k_idx in range(1, n + 1):
                        key = rt.getKey(c, k_idx)
                        try:
                            val = float(key.value) if isinstance(key.value, (int, float)) else None
                            key_data.append({
                                'key': key,
                                'index': k_idx,
                                'time': float(key.time),
                                'value': val,
                                'selected': key.selected
                            })
                        except:
                            key_data.append(None)
                    
                    for i, kd in enumerate(key_data):
                        if kd is None or not kd['selected']:
                            continue
                        if kd['value'] is None:
                            continue
                        
                        key = kd['key']
                        prev_kd = key_data[i-1] if i > 0 and key_data[i-1] else None
                        next_kd = key_data[i+1] if i < len(key_data)-1 and key_data[i+1] else None
                        
                        v = kd['value']
                        vp = prev_kd['value'] if prev_kd and prev_kd['value'] is not None else None
                        vn = next_kd['value'] if next_kd and next_kd['value'] is not None else None
                        
                        t_type = rt.Name("auto")  # Default
                        
                        try:
                            # Case 1: First or last key - use auto
                            if prev_kd is None or next_kd is None:
                                t_type = rt.Name("auto")
                            
                            # Case 2: Hold detection - value same as neighbor
                            elif vp is not None and abs(v - vp) < hold_tol:
                                t_type = rt.Name("flat")
                            elif vn is not None and abs(v - vn) < hold_tol:
                                t_type = rt.Name("flat")
                            
                            # Case 3: Peak/Valley detection - direction change
                            elif vp is not None and vn is not None:
                                going_up_from_prev = (v - vp) > hold_tol
                                going_up_to_next = (vn - v) > hold_tol
                                going_down_from_prev = (vp - v) > hold_tol
                                going_down_to_next = (v - vn) > hold_tol
                                
                                # Peak: was going up, now going down
                                is_peak = going_up_from_prev and going_down_to_next
                                # Valley: was going down, now going up
                                is_valley = going_down_from_prev and going_up_to_next
                                
                                if is_peak or is_valley:
                                    # Use flat to prevent overshoot at extremes
                                    t_type = rt.Name("flat")
                                else:
                                    # Case 4: Check for linear motion
                                    tp = prev_kd['time']
                                    tc = kd['time']
                                    tn = next_kd['time']
                                    
                                    dt1 = tc - tp
                                    dt2 = tn - tc
                                    
                                    if dt1 > 0.001 and dt2 > 0.001:
                                        slope1 = (v - vp) / dt1
                                        slope2 = (vn - v) / dt2
                                        
                                        # If slopes are similar, it's linear
                                        if abs(slope1) > 0.001 or abs(slope2) > 0.001:
                                            max_slope = max(abs(slope1), abs(slope2), 0.001)
                                            slope_diff = abs(slope1 - slope2) / max_slope
                                            
                                            if slope_diff < linear_tol:
                                                t_type = rt.Name("linear")
                                            else:
                                                t_type = rt.Name("auto")
                                        else:
                                            t_type = rt.Name("flat")
                                    else:
                                        t_type = rt.Name("auto")
                            
                            key.inTangentType = t_type
                            key.outTangentType = t_type
                            count += 1
                            
                        except Exception as e:
                            pass
                            
        rt.redrawViews()
        return f"Best Guess: {count} keys"
    
    @staticmethod
    def polished():
        """
        Create smooth, professional curves with optimal tangent angles.
        
        AnimBot style:
        - Calculates weighted average of incoming/outgoing slopes
        - Uses ~0.333 tangent length for balanced easing
        - Prevents overshoots by clamping tangents at peaks/valleys
        - Handles edge cases (first/last keys)
        """
        count = 0
        
        with pymxs.undo(True, "Polished Tangents"):
            with pymxs.animate(True):
                for c in TangentTools.get_sel_ctrls():
                    n = rt.numKeys(c)
                    if n < 2: continue
                    
                    # Collect all key data
                    all_key_data = []
                    for k_idx in range(1, n + 1):
                        key = rt.getKey(c, k_idx)
                        all_key_data.append({
                            'key': key,
                            'index': k_idx,
                            'time': float(key.time),
                            'value': key.value,
                            'selected': key.selected
                        })
                    
                    for i in range(len(all_key_data)):
                        current_data = all_key_data[i]
                        if not current_data['selected']:
                            continue
                        
                        key = current_data['key']
                        prev_data = all_key_data[i-1] if i > 0 else None
                        next_data = all_key_data[i+1] if i < len(all_key_data)-1 else None
                        
                        try:
                            key.inTangentType = rt.Name("custom")
                            key.outTangentType = rt.Name("custom")
                            key.freeHandle = False
                            
                            # Handle Point3 values (position/rotation)
                            if isinstance(key.value, rt.Point3):
                                if prev_data and next_data:
                                    vp = prev_data['value']
                                    vc = current_data['value']
                                    vn = next_data['value']
                                    tp = prev_data['time']
                                    tc = current_data['time']
                                    tn = next_data['time']
                                    
                                    dt_in = tc - tp
                                    dt_out = tn - tc
                                    
                                    if dt_in > 0.001 and dt_out > 0.001:
                                        # Calculate slopes
                                        slope_in = rt.Point3(
                                            (vc.x - vp.x) / dt_in,
                                            (vc.y - vp.y) / dt_in,
                                            (vc.z - vp.z) / dt_in
                                        )
                                        slope_out = rt.Point3(
                                            (vn.x - vc.x) / dt_out,
                                            (vn.y - vc.y) / dt_out,
                                            (vn.z - vc.z) / dt_out
                                        )
                                        
                                        # Weighted average (weight by time distance)
                                        total_dt = dt_in + dt_out
                                        w_in = dt_out / total_dt  # Opposite weighting
                                        w_out = dt_in / total_dt
                                        
                                        avg_slope = rt.Point3(
                                            slope_in.x * w_in + slope_out.x * w_out,
                                            slope_in.y * w_in + slope_out.y * w_out,
                                            slope_in.z * w_in + slope_out.z * w_out
                                        )
                                        
                                        key.inTangent = avg_slope
                                        key.outTangent = avg_slope
                                        key.inTangentLength = 0.333
                                        key.outTangentLength = 0.333
                                        count += 1
                                else:
                                    # Edge key - use smooth
                                    key.inTangentType = rt.Name("smooth")
                                    key.outTangentType = rt.Name("smooth")
                                    count += 1
                            
                            # Handle float values
                            elif isinstance(key.value, (int, float)):
                                if prev_data and next_data:
                                    vp = float(prev_data['value'])
                                    vc = float(current_data['value'])
                                    vn = float(next_data['value'])
                                    tp = prev_data['time']
                                    tc = current_data['time']
                                    tn = next_data['time']
                                    
                                    dt_in = tc - tp
                                    dt_out = tn - tc
                                    
                                    if dt_in > 0.001 and dt_out > 0.001:
                                        slope_in = (vc - vp) / dt_in
                                        slope_out = (vn - vc) / dt_out
                                        
                                        # Check for peak/valley (overshoot prevention)
                                        is_peak = (vc > vp) and (vc > vn)
                                        is_valley = (vc < vp) and (vc < vn)
                                        
                                        if is_peak or is_valley:
                                            # Flat tangent at extremes to prevent overshoot
                                            key.inTangent = 0.0
                                            key.outTangent = 0.0
                                        else:
                                            # Weighted average
                                            total_dt = dt_in + dt_out
                                            w_in = dt_out / total_dt
                                            w_out = dt_in / total_dt
                                            avg_slope = slope_in * w_in + slope_out * w_out
                                            
                                            key.inTangent = avg_slope
                                            key.outTangent = avg_slope
                                        
                                        key.inTangentLength = 0.333
                                        key.outTangentLength = 0.333
                                        count += 1
                                else:
                                    # Edge key
                                    key.inTangentType = rt.Name("smooth")
                                    key.outTangentType = rt.Name("smooth")
                                    count += 1
                            else:
                                # Unknown type - use smooth
                                key.inTangentType = rt.Name("smooth")
                                key.outTangentType = rt.Name("smooth")
                                count += 1
                                
                        except Exception as e:
                            pass
                            
        rt.redrawViews()
        return f"Polished: {count} keys"
    
    @staticmethod
    def flow():
        """
        Create flowing transitions that inherit momentum from neighbors.
        
        AnimBot style:
        - Calculates slope from prev key directly to next key
        - Ignores current key's value (allows overshoots)
        - Perfect for tails, flowing fabric, wide arcs
        - Prioritizes flow over hitting exact poses
        """
        count = 0
        
        with pymxs.undo(True, "Flow Tangents"):
            with pymxs.animate(True):
                for c in TangentTools.get_sel_ctrls():
                    n = rt.numKeys(c)
                    if n < 3: continue
                    
                    # Collect key data
                    all_key_data = []
                    for k_idx in range(1, n + 1):
                        key = rt.getKey(c, k_idx)
                        all_key_data.append({
                            'key': key,
                            'index': k_idx,
                            'time': float(key.time),
                            'value': key.value,
                            'selected': key.selected
                        })
                    
                    for i in range(1, len(all_key_data) - 1):  # Skip first and last
                        current_data = all_key_data[i]
                        if not current_data['selected']:
                            continue
                        
                        key = current_data['key']
                        prev_data = all_key_data[i-1]
                        next_data = all_key_data[i+1]
                        
                        try:
                            t_prev = prev_data['time']
                            t_next = next_data['time']
                            total_dt = t_next - t_prev
                            
                            if total_dt <= 0.001:
                                continue
                            
                            key.inTangentType = rt.Name("custom")
                            key.outTangentType = rt.Name("custom")
                            key.freeHandle = False
                            
                            # Calculate slope from prev directly to next (ignoring current value)
                            if isinstance(key.value, rt.Point3):
                                val_prev = prev_data['value']
                                val_next = next_data['value']
                                
                                flow_slope = rt.Point3(
                                    (val_next.x - val_prev.x) / total_dt,
                                    (val_next.y - val_prev.y) / total_dt,
                                    (val_next.z - val_prev.z) / total_dt
                                )
                                
                                key.inTangent = flow_slope
                                key.outTangent = flow_slope
                                
                                # Longer tangent lengths for smoother flow
                                key.inTangentLength = 0.4
                                key.outTangentLength = 0.4
                                count += 1
                                
                            elif isinstance(key.value, (int, float)):
                                val_prev = float(prev_data['value'])
                                val_next = float(next_data['value'])
                                
                                flow_slope = (val_next - val_prev) / total_dt
                                
                                key.inTangent = flow_slope
                                key.outTangent = flow_slope
                                key.inTangentLength = 0.4
                                key.outTangentLength = 0.4
                                count += 1
                                
                        except Exception as e:
                            pass
                            
        rt.redrawViews()
        return f"Flow: {count} keys"
    
    @staticmethod
    def bounce(mode=0):
        """
        Create bouncy ease-in/ease-out tangents.
        
        AnimBot style:
        - mode 0: Both (affects both in and out tangents)
        - mode 1: Bounce In (only affects IN/left tangent - fast approach)
        - mode 2: Bounce Out (only affects OUT/right tangent - fast departure)
        """
        count = 0
        
        # Tangent length multipliers
        FAST_LENGTH = 0.15   # Short tangent = fast/snappy
        
        with pymxs.undo(True, "Bounce Tangents"):
            with pymxs.animate(True):
                for c in TangentTools.get_sel_ctrls():
                    n = rt.numKeys(c)
                    if n < 2: continue
                    
                    # Collect key data
                    all_key_data = []
                    for k_idx in range(1, n + 1):
                        key = rt.getKey(c, k_idx)
                        all_key_data.append({
                            'key': key,
                            'index': k_idx,
                            'time': float(key.time),
                            'value': key.value,
                            'selected': key.selected
                        })
                    
                    for i in range(len(all_key_data)):
                        current_data = all_key_data[i]
                        if not current_data['selected']:
                            continue
                        
                        key = current_data['key']
                        prev_data = all_key_data[i-1] if i > 0 else None
                        next_data = all_key_data[i+1] if i < len(all_key_data)-1 else None
                        
                        try:
                            # Enable free handle for asymmetric tangents
                            key.freeHandle = True
                            
                            tc = current_data['time']
                            
                            # IN tangent (left side) - only for mode 0 or mode 1
                            if prev_data and (mode == 0 or mode == 1):
                                tp = prev_data['time']
                                dt_in = tc - tp
                                
                                if dt_in > 0.001:
                                    key.inTangentType = rt.Name("custom")
                                    
                                    if isinstance(key.value, (int, float)):
                                        vp = float(prev_data['value'])
                                        vc = float(current_data['value'])
                                        slope_in = (vc - vp) / dt_in
                                        key.inTangent = slope_in
                                    elif isinstance(key.value, rt.Point3):
                                        vp = prev_data['value']
                                        vc = current_data['value']
                                        slope_in = rt.Point3(
                                            (vc.x - vp.x) / dt_in,
                                            (vc.y - vp.y) / dt_in,
                                            (vc.z - vp.z) / dt_in
                                        )
                                        key.inTangent = slope_in
                                    
                                    key.inTangentLength = FAST_LENGTH
                            
                            # OUT tangent (right side) - only for mode 0 or mode 2
                            if next_data and (mode == 0 or mode == 2):
                                tn = next_data['time']
                                dt_out = tn - tc
                                
                                if dt_out > 0.001:
                                    key.outTangentType = rt.Name("custom")
                                    
                                    if isinstance(key.value, (int, float)):
                                        vc = float(current_data['value'])
                                        vn = float(next_data['value'])
                                        slope_out = (vn - vc) / dt_out
                                        key.outTangent = slope_out
                                    elif isinstance(key.value, rt.Point3):
                                        vc = current_data['value']
                                        vn = next_data['value']
                                        slope_out = rt.Point3(
                                            (vn.x - vc.x) / dt_out,
                                            (vn.y - vc.y) / dt_out,
                                            (vn.z - vc.z) / dt_out
                                        )
                                        key.outTangent = slope_out
                                    
                                    key.outTangentLength = FAST_LENGTH
                            
                            count += 1
                            
                        except Exception as e:
                            pass
                            
        rt.redrawViews()
        
        mode_names = {0: "Bounce", 1: "Bounce In", 2: "Bounce Out"}
        return f"{mode_names.get(mode, 'Bounce')}: {count} keys"

# ============================================================================
# MODERN UI IMPLEMENTATION
# ============================================================================

class AnimmixSlider(QtWidgets.QSlider):
    def __init__(self, parent=None):
        super().__init__(QtCore.Qt.Horizontal, parent)
        self.setRange(-100, 100); self.setValue(0)
        self.active_color = QtGui.QColor("#32CD32"); self.is_active = False

    def set_color(self, color_str): self.active_color = QtGui.QColor(color_str); self.update()

    def paintEvent(self, event):
        painter = QtGui.QPainter(self); painter.setRenderHint(QtGui.QPainter.Antialiasing)
        rect = self.rect(); center_y = rect.height() / 2
        margin = 16 
        usable_width = rect.width() - (margin * 2)
        track_height = 16 if self.is_active else 6; track_radius = track_height / 2
        val = self.value(); range_len = self.maximum() - self.minimum()
        norm = (val - self.minimum()) / range_len
        handle_x = margin + (norm * usable_width); center_x = rect.width() / 2
        painter.setPen(QtCore.Qt.NoPen); painter.setBrush(QtGui.QColor("#1A1A1A"))
        painter.drawRoundedRect(margin, int(center_y - track_radius), int(usable_width), int(track_height), track_radius, track_radius)
        painter.setBrush(self.active_color)
        if val != 0:
            if val > 0: w = handle_x - center_x; r = QtCore.QRectF(center_x, center_y - track_radius, w, track_height)
            else: w = center_x - handle_x; r = QtCore.QRectF(handle_x, center_y - track_radius, w, track_height)
            painter.drawRoundedRect(r, track_radius, track_radius)
        painter.setBrush(QtGui.QColor("#444"))
        for tick_val in [-100, -75, -50, -25, 0, 25, 50, 75, 100]:
            tick_norm = (tick_val - self.minimum()) / range_len
            tick_x = margin + (tick_norm * usable_width)
            radius = 1.0 if abs(tick_val) in [25, 50, 75] else 1.5
            painter.drawEllipse(QtCore.QPointF(tick_x, center_y), radius, radius)
        handle_radius = 8 if self.is_active else 6
        painter.setBrush(self.active_color); painter.setPen(QtGui.QPen(QtGui.QColor("#111"), 2))
        painter.drawEllipse(QtCore.QPointF(handle_x, center_y), handle_radius, handle_radius)
        if val != 0:
            text_str = f"{val}%"; font = QtGui.QFont("Segoe UI", 9, QtGui.QFont.Bold)
            painter.setFont(font); painter.setPen(QtGui.QColor("white"))
            txt_pad = 10; left_bound = margin + txt_pad; right_bound = rect.width() - margin - txt_pad
            if val > 0: t_rect = QtCore.QRectF(left_bound, 0, 100, rect.height()); painter.drawText(t_rect, QtCore.Qt.AlignLeft | QtCore.Qt.AlignVCenter, text_str)
            else: t_rect = QtCore.QRectF(right_bound - 100, 0, 100, rect.height()); painter.drawText(t_rect, QtCore.Qt.AlignRight | QtCore.Qt.AlignVCenter, text_str)
        painter.end()
    
    def mousePressEvent(self, e): self.is_active=True; self.update(); super().mousePressEvent(e)
    def mouseReleaseEvent(self, e): self.is_active=False; self.update(); super().mouseReleaseEvent(e)

class RecoveryHistoryDialog(QtWidgets.QDialog):
    def __init__(self, parent=None):
        super(RecoveryHistoryDialog, self).__init__(parent)
        self.setWindowTitle("History"); self.setFixedWidth(200); self.setWindowFlags(QtCore.Qt.Popup)
        self.setStyleSheet("QDialog { background: #333; border: 1px solid #555; } QLabel { color: #888; }")
        layout = QtWidgets.QVBoxLayout(self); layout.setContentsMargins(5, 5, 5, 5); layout.setSpacing(2)
        files = AnimRecoveryTools.get_recent_snapshots(15)
        if not files: layout.addWidget(QtWidgets.QLabel("No snapshots found"))
        else:
            for f in files:
                f_norm = f.replace("\\", "/") 
                ts_str = os.path.basename(f).replace("recovery_", "").replace(".max", "")
                display_str = ts_str.replace("_", " ")
                btn = QtWidgets.QPushButton(display_str)
                btn.setStyleSheet("QPushButton { text-align: left; padding: 6px; background: #444; border: none; color: #EEE; border-radius: 3px; } QPushButton:hover { background: #555; }")
                btn.clicked.connect(lambda ch=False, path=f_norm: self.do_restore(path))
                layout.addWidget(btn)

    def do_restore(self, path):
        res = AnimRecoveryTools.restore_snapshot(path); print(f"[Animmix] Restore result: {res}"); self.close()

class AnimmixDockWidget(QtWidgets.QDockWidget):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Animmix Pro"); self.setObjectName("AnimmixDockWidget"); self.setAttribute(QtCore.Qt.WA_DeleteOnClose)
        self.main_widget = QtWidgets.QWidget(); self.setWidget(self.main_widget)
        self.layout = QtWidgets.QVBoxLayout(self.main_widget); self.layout.setContentsMargins(6,6,6,6); self.layout.setSpacing(8)
        self.setStyleSheet("""
            QWidget { background-color: #2B2B2B; color: #EEE; font-family: 'Segoe UI'; font-size: 11px; }
            QGroupBox { background-color: #323232; border: 1px solid #3A3A3A; border-radius: 6px; margin-top: 12px; padding-top: 12px; }
            QGroupBox::title { subcontrol-origin: margin; left: 10px; padding: 0 4px; color: #AAA; font-weight: bold; background-color: #2B2B2B; }
            QPushButton, QToolButton { background-color: #404040; border: 1px solid #202020; border-radius: 4px; padding: 5px; color: #DDD; min-height: 18px; }
            QPushButton:hover, QToolButton:hover { background-color: #505050; border-color: #606060; color: #FFF; }
            QPushButton:pressed, QToolButton:pressed { background-color: #222; }
            QToolButton { padding-right: 15px; }
            QToolButton::menu-button { border-left: 1px solid #202020; width: 10px; background-color: rgba(0,0,0,0.2); border-top-right-radius: 4px; border-bottom-right-radius: 4px; }
            QToolButton::menu-button:hover { background-color: rgba(0,0,0,0.4); }
            QToolButton::menu-arrow { image: none; }
            QSpinBox { background: #202020; border: 1px solid #444; border-radius: 4px; padding: 4px; color: #FFF; selection-background-color: #555; }
        """)
        self.mode = 1; self.overshoot = False; self.recovery_active = True
        self.setup_ui()
        self.recovery_timer = QtCore.QTimer(self); self.recovery_timer.setInterval(60000); self.recovery_timer.timeout.connect(self.auto_save); self.recovery_timer.start()
        self.auto_save()

    def setup_ui(self):
        # 1. MODES
        mode_layout = QtWidgets.QHBoxLayout(); mode_layout.setSpacing(4)
        self.btn_tween = QtWidgets.QToolButton(); self.btn_tween.setText("Tween"); self.btn_tween.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        self.btn_tween.clicked.connect(lambda: self.set_mode(1))
        tm = QtWidgets.QMenu(self.btn_tween); tm.setStyleSheet("QMenu { background: #333; color: #EEE; } QMenu::item:selected { background: #555; }")
        tm.addAction("Tween").triggered.connect(lambda: self.set_mode(1)); tm.addAction("Space").triggered.connect(lambda: self.set_mode(2)); tm.addAction("Offset").triggered.connect(lambda: self.set_mode(3))
        self.btn_tween.setMenu(tm); self.btn_tween.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)

        self.btn_ease = QtWidgets.QToolButton(); self.btn_ease.setText("Blend"); self.btn_ease.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        self.btn_ease.clicked.connect(lambda: self.set_mode(4))
        em = QtWidgets.QMenu(self.btn_ease); em.setStyleSheet("QMenu { background: #333; color: #EEE; } QMenu::item:selected { background: #555; }")
        em.addAction("Blend").triggered.connect(lambda: self.set_mode(4)); em.addAction("Force").triggered.connect(lambda: self.set_mode(6))
        self.btn_ease.setMenu(em); self.btn_ease.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        
        self.btn_default = QtWidgets.QPushButton("Default"); self.btn_default.clicked.connect(lambda: self.set_mode(5))
        mode_layout.addWidget(self.btn_tween); mode_layout.addWidget(self.btn_ease); mode_layout.addWidget(self.btn_default)
        self.layout.addLayout(mode_layout)

        # 2. SLIDER
        slider_group = QtWidgets.QGroupBox("INTENSITY"); v_sl = QtWidgets.QVBoxLayout(slider_group); v_sl.setSpacing(2); v_sl.setContentsMargins(0, 20, 0, 5)
        self.anchor_widget = QtWidgets.QWidget(); self.anchor_layout = QtWidgets.QHBoxLayout(self.anchor_widget); self.anchor_layout.setContentsMargins(13,0,13,0); self.anchor_layout.setSpacing(0)
        self.snap_btns = []
        snap_values = [-100, -75, -50, -25, 0, 25, 50, 75, 100]
        for i, val in enumerate(snap_values):
            size = 6; b = QtWidgets.QPushButton(); b.setFixedSize(size, size); b.setCursor(QtCore.Qt.PointingHandCursor)
            is_tiny = abs(val) in [25, 50, 75]; b.setProperty("base_size", 4 if is_tiny else 6) 
            b.setStyleSheet(f"QPushButton {{ background-color: #555; border: none; border-radius: {size/2}px; min-width: {size}px; max-width: {size}px; min-height: {size}px; max-height: {size}px; padding: 0px; margin: {(6-size)/2}px; }} QPushButton:hover {{ background-color: #FFF; }}")
            b.clicked.connect(lambda c=False, v=val: self.snap_click(v))
            self.anchor_layout.addWidget(b); self.snap_btns.append(b)
            if i < len(snap_values) - 1: self.anchor_layout.addStretch(1)
        v_sl.addWidget(self.anchor_widget)
        self.slider = AnimmixSlider(); self.slider.setFixedHeight(30)
        self.slider.sliderPressed.connect(self.sl_press); self.slider.sliderReleased.connect(self.sl_release); self.slider.valueChanged.connect(self.sl_change)
        v_sl.addWidget(self.slider)
        ov_lay = QtWidgets.QHBoxLayout()
        self.btn_overshoot = QtWidgets.QPushButton("Overshoot: OFF"); self.btn_overshoot.setFixedHeight(20); self.btn_overshoot.setStyleSheet("font-size: 10px; background: transparent; border: 1px solid #444; color: #888;")
        self.btn_overshoot.clicked.connect(self.toggle_overshoot)
        ov_lay.addStretch(); ov_lay.addWidget(self.btn_overshoot)
        v_sl.addLayout(ov_lay)
        self.layout.addWidget(slider_group)
        
        # 3. TANGENTS
        tg_grp = QtWidgets.QGroupBox("TANGENTS"); tg_lay = QtWidgets.QGridLayout(tg_grp); tg_lay.setSpacing(6); tg_lay.setContentsMargins(8,15,8,8)
        tg_lay.addWidget(self.mk_btn("Cycle", TangentTools.cycle_match), 0,0); tg_lay.addWidget(self.mk_btn("Guess", TangentTools.best_guess), 0,1); tg_lay.addWidget(self.mk_btn("Polish", TangentTools.polished), 0,2)
        tg_lay.addWidget(self.mk_btn("Flow", TangentTools.flow), 1,0)
        btn_bnc = QtWidgets.QToolButton(); btn_bnc.setText("Bounce"); btn_bnc.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        btn_bnc.clicked.connect(lambda: TangentTools.bounce(0))
        bm = QtWidgets.QMenu(btn_bnc); bm.setStyleSheet("QMenu { background: #333; color: #EEE; } QMenu::item:selected { background: #555; }")
        bm.addAction("Bounce In").triggered.connect(lambda: TangentTools.bounce(1)); bm.addAction("Bounce Out").triggered.connect(lambda: TangentTools.bounce(2))
        btn_bnc.setMenu(bm); tg_lay.addWidget(btn_bnc, 1, 1)
        btn_nat = QtWidgets.QToolButton(); btn_nat.setText("Native"); btn_nat.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        btn_nat.clicked.connect(lambda: TangentTools.set_native("Auto"))
        nm = QtWidgets.QMenu(btn_nat); nm.setStyleSheet("QMenu { background: #333; color: #EEE; } QMenu::item:selected { background: #555; }")
        for t in ["Auto","Smooth","Linear","Flat","Step","Fast","Slow","Custom"]: nm.addAction(t).triggered.connect(lambda c=False,x=t: TangentTools.set_native(x))
        btn_nat.setMenu(nm); tg_lay.addWidget(btn_nat, 1,2)
        self.layout.addWidget(tg_grp)

        # 4. POSE
        pg_grp = QtWidgets.QGroupBox("POSE")
        p_lay = QtWidgets.QVBoxLayout(pg_grp)
        p_lay.setContentsMargins(8, 15, 8, 8)
        p_lay.setSpacing(6)
        
        # Row 1: Copy, Paste, Mirror, Reset
        row1 = QtWidgets.QHBoxLayout()
        row1.setSpacing(4)
        
        btn_copy = self.mk_btn("Copy", PoseTools.copy_pose)
        btn_paste = self.mk_btn("Paste", PoseTools.paste_pose)
        
        btn_mir = QtWidgets.QToolButton()
        btn_mir.setText("Mirror")
        btn_mir.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        btn_mir.clicked.connect(lambda: PoseTools.mirror_pose())
        mm = QtWidgets.QMenu(btn_mir)
        mm.setStyleSheet("QMenu { background: #333; color: #EEE; } QMenu::item:selected { background: #555; }")
        mm.addAction("Left ?").triggered.connect(lambda: PoseTools.mirror_left_to_right())
        mm.addAction("? Right").triggered.connect(lambda: PoseTools.mirror_right_to_left())
        mm.addSeparator()
        mm.addAction("Flip Pose").triggered.connect(lambda: PoseTools.flip_pose())
        btn_mir.setMenu(mm)
        
        btn_reset = self.mk_btn("Reset", PoseTools.reset_pose)
        
        for b in [btn_copy, btn_paste, btn_mir, btn_reset]:
            b.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
            row1.addWidget(b)
        
        p_lay.addLayout(row1)
        
    # Row 2: Snapshot, Select Opposite
        row2 = QtWidgets.QHBoxLayout()
        row2.setSpacing(4)
        
        # Snapshot button
        btn_snap = QtWidgets.QToolButton()
        btn_snap.setText("Snapshot")
        btn_snap.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        btn_snap.clicked.connect(self.take_snapshot)
        
        snap_menu = QtWidgets.QMenu(btn_snap)
        snap_menu.setStyleSheet("QMenu { background: #333; color: #EEE; } QMenu::item:selected { background: #555; }")
        snap_menu.addAction("Take Snapshot").triggered.connect(self.take_snapshot)
        snap_menu.addAction("Rename Snapshot").triggered.connect(self.rename_snapshot)
        snap_menu.addSeparator()
        snap_menu.addAction("Save Snapshot...").triggered.connect(self.save_snapshot)
        snap_menu.addAction("Load Snapshot...").triggered.connect(self.load_snapshot)
        snap_menu.addSeparator()
        snap_menu.addAction("Clear Snapshot").triggered.connect(self.clear_snapshot)
        btn_snap.setMenu(snap_menu)
        
        # Select Opposite button
        btn_sel_opp = QtWidgets.QToolButton()
        btn_sel_opp.setText("Select Opposite")
        btn_sel_opp.setPopupMode(QtWidgets.QToolButton.MenuButtonPopup)
        btn_sel_opp.clicked.connect(lambda: print(SnapshotManager.select_opposite()))
        
        sel_menu = QtWidgets.QMenu(btn_sel_opp)
        sel_menu.setStyleSheet("QMenu { background: #333; color: #EEE; } QMenu::item:selected { background: #555; }")
        sel_menu.addAction("Select All").triggered.connect(lambda: print(SnapshotManager.select_all()))
        sel_menu.addAction("Select Left").triggered.connect(lambda: print(SnapshotManager.select_all_left()))
        sel_menu.addAction("Select Right").triggered.connect(lambda: print(SnapshotManager.select_all_right()))
        sel_menu.addAction("Select Center").triggered.connect(lambda: print(SnapshotManager.select_all_center()))
        btn_sel_opp.setMenu(sel_menu)
        
        btn_snap.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        btn_sel_opp.setSizePolicy(QtWidgets.QSizePolicy.Expanding, QtWidgets.QSizePolicy.Fixed)
        
        row2.addWidget(btn_snap)
        row2.addWidget(btn_sel_opp)
        
        p_lay.addLayout(row2)
        
        # Row 3: Selection Sets (full width)
        btn_sel_sets = QtWidgets.QPushButton("Selection Sets")
        btn_sel_sets.clicked.connect(lambda: SelectionSetsManager.show_window())
        btn_sel_sets.setToolTip("Open Selection Sets Manager")
        p_lay.addWidget(btn_sel_sets)
        
        self.layout.addWidget(pg_grp)

        # 5. KEYS
        k_grp = QtWidgets.QGroupBox("KEYS") 
        k_lay = QtWidgets.QVBoxLayout(k_grp); k_lay.setSpacing(6); k_lay.setContentsMargins(8,15,8,8)
        r1 = QtWidgets.QHBoxLayout(); r1.setSpacing(6)
        r1.addWidget(self.mk_btn("Hammer", do_key_hammer))
        r1.addWidget(self.mk_btn("Smart Key", do_set_key))
        r1.addWidget(self.mk_btn("Delete", do_delete_keys))
        k_lay.addLayout(r1)
        r2 = QtWidgets.QHBoxLayout(); r2.setSpacing(6)
        self.sp_nudge = QtWidgets.QSpinBox(); self.sp_nudge.setRange(1,100); self.sp_nudge.setValue(1); self.sp_nudge.setAlignment(QtCore.Qt.AlignCenter)
        r2.addWidget(self.mk_btn("< Nudge", lambda: do_nudge(-self.sp_nudge.value()))); r2.addWidget(self.sp_nudge); r2.addWidget(self.mk_btn("Nudge >", lambda: do_nudge(self.sp_nudge.value())))
        k_lay.addLayout(r2)
        
        self.layout.addWidget(k_grp)
        
        # 6. RECOVERY
        rec_frame = QtWidgets.QFrame(); rec_frame.setStyleSheet("background: #1F1F1F; border-radius: 4px; padding: 4px; border: 1px solid #333;")
        r_lay = QtWidgets.QHBoxLayout(rec_frame); r_lay.setContentsMargins(4,0,4,0)
        self.btn_rec_toggle = QtWidgets.QPushButton(); self.btn_rec_toggle.setFixedSize(10,10); self.btn_rec_toggle.clicked.connect(self.toggle_recovery)
        lbl_rec = QtWidgets.QLabel("Auto-Recovery"); lbl_rec.setStyleSheet("color: #777; font-weight: bold; border: none; background: transparent; margin-left: 5px;")
        self.btn_hist = QtWidgets.QPushButton("History"); self.btn_hist.setStyleSheet("background: transparent; color: #888; text-align: right; padding: 0; font-size: 10px; border: none;")
        self.btn_hist.setCursor(QtCore.Qt.PointingHandCursor); self.btn_hist.clicked.connect(self.show_history)
        r_lay.addWidget(self.btn_rec_toggle); r_lay.addWidget(lbl_rec); r_lay.addStretch(); r_lay.addWidget(self.btn_hist)
        self.layout.addStretch(); self.layout.addWidget(rec_frame)
        self.update_recovery_ui(); self.set_mode(1)

    def mk_btn(self, txt, func):
        b = QtWidgets.QPushButton(txt); b.clicked.connect(func); return b

    def set_mode(self, m):
        self.mode = m
        clear_cache()
        clear_pushpull_cache()
        
        cols = {1:"#32CD32", 2:"#00CED1", 3:"#FFA500", 4:"#FFD700", 5:"#AAAAAA", 6:"#FF4444"}
        c = cols.get(m, "#32CD32")
        self.slider.set_color(c)
        
        # Update button text based on mode
        tween_modes = {1: "Tween", 2: "Space", 3: "Offset"}
        blend_modes = {4: "Blend", 6: "Force"}
        
        if m in tween_modes:
            self.btn_tween.setText(tween_modes[m])
        if m in blend_modes:
            self.btn_ease.setText(blend_modes[m])
        
        base = ""
        act = f"background-color: {c}; color: #111; border: 1px solid {c}; font-weight: bold;"
        
        self.btn_tween.setStyleSheet(act if m in [1,2,3] else base)
        self.btn_ease.setStyleSheet(act if m in [4,6] else base)
        self.btn_default.setStyleSheet(act if m==5 else base)
        
        for b in self.snap_btns: 
            size = b.property("base_size")
            if not size:
                size = 6
            b.setStyleSheet(f"QPushButton {{ background-color: {c}; border: none; border-radius: {size/2}px; min-width: {size}px; max-width: {size}px; min-height: {size}px; max-height: {size}px; padding: 0px; margin: {(6-size)/2}px; }} QPushButton:hover {{ background-color: #FFF; }}")

    def take_snapshot(self):
        result = SnapshotManager.take_snapshot()
        print(result)

    def rename_snapshot(self):
        result = SnapshotManager.rename_snapshot()
        print(result)

    def save_snapshot(self):
        result = SnapshotManager.save_to_file()
        print(result)

    def load_snapshot(self):
        result = SnapshotManager.load_from_file()
        print(result)

    def clear_snapshot(self):
        SnapshotManager.clear_all_snapshots()
        print("Snapshots cleared")

    def sl_press(self):
        if self.mode == 3:  # Offset mode
            build_offset_cache()  # This now calls the new function
        elif self.mode == 6:  # Force/Push-Pull mode
            build_pushpull_cache()
        elif self.mode != 4:
            build_cache()

    def sl_release(self):
        clear_cache()
        clear_pushpull_cache()
        clear_offset_cache()
        self.slider.blockSignals(True)
        self.slider.setValue(0)
        self.slider.blockSignals(False)

    def sl_change(self, val):
        t = val / 100.0
        finalize_selected_keys(t, self.mode)
    def snap_click(self, val): 
        self.sl_press(); self.slider.setValue(val); self.sl_release()
    def toggle_overshoot(self):
        self.overshoot = not self.overshoot; self.btn_overshoot.setText(f"Overshoot: {'ON' if self.overshoot else 'OFF'}")
        self.slider.setRange(-200 if self.overshoot else -100, 200 if self.overshoot else 100)
    def toggle_recovery(self):
        self.recovery_active = not self.recovery_active
        if self.recovery_active: self.recovery_timer.start(); AnimRecoveryTools.create_snapshot()
        else: self.recovery_timer.stop()
        self.update_recovery_ui()
    def update_recovery_ui(self):
        c = "#32CD32" if self.recovery_active else "#444"
        self.btn_rec_toggle.setStyleSheet(f"background-color: {c}; border-radius: 5px; border: none; min-width: 10px; max-width: 10px; min-height: 10px; max-height: 10px; padding: 0px; margin: 0px;")
    
    def auto_save(self):
        if self.recovery_active: AnimRecoveryTools.create_snapshot()
        
    def show_history(self):
        d = RecoveryHistoryDialog(self); p = self.btn_hist.mapToGlobal(QtCore.QPoint(0,0)); d.move(p.x(), p.y() - d.height()); d.show()

def launch():
    main_window = qtmax.GetQMaxMainWindow()
    existing = main_window.findChild(QtWidgets.QDockWidget, "AnimmixDockWidget")
    if existing: existing.close(); existing.deleteLater()
    widget = AnimmixDockWidget(main_window)
    main_window.addDockWidget(QtCore.Qt.LeftDockWidgetArea, widget)
    widget.setFloating(True); widget.show()

launch()