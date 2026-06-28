from __future__ import annotations

import copy
import io
import json
import math
import struct
from collections import defaultdict
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

from PIL import Image


_GLB_JSON = b"JSON"
_GLB_BIN = b"BIN\x00"
_NUMPY_MODULE: Any = None


class GlbAssemblyError(RuntimeError):
    pass


def _np() -> Any:
    global _NUMPY_MODULE
    if _NUMPY_MODULE is not None:
        return _NUMPY_MODULE
    try:
        import numpy as numpy_module  # type: ignore
    except ModuleNotFoundError as exc:
        raise GlbAssemblyError("3D 模型拼装依赖 numpy 未安装，请先安装 3D 运行依赖后再使用该步骤。") from exc
    _NUMPY_MODULE = numpy_module
    return _NUMPY_MODULE


def _align4(value: int) -> int:
    return (value + 3) & ~3


def _read_glb(path: Path) -> Tuple[Dict[str, Any], bytes]:
    raw = path.read_bytes()
    if len(raw) < 20:
        raise GlbAssemblyError(f"GLB is too small: {path.name}")
    magic, version, total_len = struct.unpack_from("<4sII", raw, 0)
    if magic != b"glTF" or version != 2:
        raise GlbAssemblyError(f"Unsupported GLB header: {path.name}")
    if total_len > len(raw):
        raise GlbAssemblyError(f"GLB length is invalid: {path.name}")
    offset = 12
    gltf: Optional[Dict[str, Any]] = None
    bin_chunk = b""
    while offset + 8 <= total_len:
        chunk_len, chunk_type = struct.unpack_from("<I4s", raw, offset)
        offset += 8
        chunk = raw[offset: offset + chunk_len]
        offset += chunk_len
        if chunk_type == _GLB_JSON:
            gltf = json.loads(chunk.rstrip(b" \t\r\n\x00").decode("utf-8"))
        elif chunk_type == _GLB_BIN:
            bin_chunk = chunk
    if not isinstance(gltf, dict):
        raise GlbAssemblyError(f"GLB JSON chunk is missing: {path.name}")
    buffers = gltf.get("buffers") or []
    if len(buffers) > 1:
        raise GlbAssemblyError(f"Only single-buffer GLB files are supported: {path.name}")
    return gltf, bin_chunk


def _write_glb(path: Path, gltf: Dict[str, Any], bin_chunk: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    gltf = copy.deepcopy(gltf)
    gltf["buffers"] = [{"byteLength": len(bin_chunk)}]
    json_raw = json.dumps(gltf, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    json_padded = json_raw + (b" " * (_align4(len(json_raw)) - len(json_raw)))
    bin_padded = bin_chunk + (b"\x00" * (_align4(len(bin_chunk)) - len(bin_chunk)))
    total_len = 12 + 8 + len(json_padded) + 8 + len(bin_padded)
    with path.open("wb") as f:
        f.write(struct.pack("<4sII", b"glTF", 2, total_len))
        f.write(struct.pack("<I4s", len(json_padded), _GLB_JSON))
        f.write(json_padded)
        f.write(struct.pack("<I4s", len(bin_padded), _GLB_BIN))
        f.write(bin_padded)


def _position_bbox(gltf: Dict[str, Any]) -> Dict[str, Any]:
    accessors = gltf.get("accessors") or []
    mins: List[List[float]] = []
    maxs: List[List[float]] = []
    for mesh in gltf.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            attrs = primitive.get("attributes") if isinstance(primitive.get("attributes"), dict) else {}
            pos = attrs.get("POSITION")
            if isinstance(pos, int) and 0 <= pos < len(accessors):
                accessor = accessors[pos] if isinstance(accessors[pos], dict) else {}
                amin = accessor.get("min")
                amax = accessor.get("max")
                if isinstance(amin, list) and isinstance(amax, list) and len(amin) >= 3 and len(amax) >= 3:
                    mins.append([float(amin[0]), float(amin[1]), float(amin[2])])
                    maxs.append([float(amax[0]), float(amax[1]), float(amax[2])])
    if not mins:
        mins = [[-0.5, -0.5, -0.5]]
        maxs = [[0.5, 0.5, 0.5]]
    mn = [min(item[i] for item in mins) for i in range(3)]
    mx = [max(item[i] for item in maxs) for i in range(3)]
    size = [max(1e-6, mx[i] - mn[i]) for i in range(3)]
    center = [(mn[i] + mx[i]) * 0.5 for i in range(3)]
    return {"min": mn, "max": mx, "size": size, "center": center}


def _safe_box(box: Any, frame_width: float, frame_height: float) -> Tuple[float, float, float, float]:
    if isinstance(box, list) and len(box) >= 4:
        x1, y1, x2, y2 = [float(box[i]) for i in range(4)]
    else:
        x1, y1, x2, y2 = 0.0, 0.0, frame_width, frame_height
    x1, x2 = sorted((max(0.0, x1), min(frame_width, x2)))
    y1, y2 = sorted((max(0.0, y1), min(frame_height, y2)))
    if x2 - x1 < 1:
        x1, x2 = 0.0, frame_width
    if y2 - y1 < 1:
        y1, y2 = 0.0, frame_height
    return x1, y1, x2, y2


def _clamp(value: float, low: float, high: float) -> float:
    return max(low, min(high, value))


def _part_text(part: Dict[str, Any]) -> str:
    return " ".join(str(part.get(key) or "").lower() for key in ("role", "label", "source", "name"))


def _overlay_policy(part: Dict[str, Any], *, has_base: bool) -> Tuple[bool, str]:
    if not has_base:
        return True, "parts_only_assembly"

    text = _part_text(part)
    if _replacement_kind(part):
        return True, "base_guided_mesh_replacement"

    headwear_tokens = ("hat", "helmet", "crown", "headwear")
    core_head_tokens = ("head", "face", "screen", "mask")
    if any(token in text for token in headwear_tokens):
        if any(token in text for token in core_head_tokens):
            return False, "headwear_part_contains_core_head_or_face"
        return False, "headwear_accessory_requires_base_mesh_segmentation"

    structural_tokens = (
        "head",
        "face",
        "torso",
        "body",
        "jacket",
        "coat",
        "shirt",
        "arm",
        "hand",
        "glove",
        "sleeve",
        "leg",
        "boot",
        "shoe",
        "foot",
        "pants",
        "hip",
        "pelvis",
    )
    if any(token in text for token in structural_tokens):
        return False, "structural_body_part_requires_true_mesh_replacement"

    return False, "uncertain_part_not_overlaid_on_complete_base_model"


def _component_size(component_type: int) -> int:
    if component_type in (5120, 5121):
        return 1
    if component_type in (5122, 5123):
        return 2
    if component_type in (5125, 5126):
        return 4
    raise GlbAssemblyError(f"Unsupported accessor component type: {component_type}")


def _accessor_byte_offset(gltf: Dict[str, Any], accessor_index: int) -> Tuple[Dict[str, Any], Dict[str, Any], int]:
    accessors = gltf.get("accessors") or []
    buffer_views = gltf.get("bufferViews") or []
    if not isinstance(accessor_index, int) or accessor_index < 0 or accessor_index >= len(accessors):
        raise GlbAssemblyError("Accessor index is invalid")
    accessor = accessors[accessor_index] if isinstance(accessors[accessor_index], dict) else {}
    view_index = accessor.get("bufferView")
    if not isinstance(view_index, int) or view_index < 0 or view_index >= len(buffer_views):
        raise GlbAssemblyError("Accessor bufferView is invalid")
    view = buffer_views[view_index] if isinstance(buffer_views[view_index], dict) else {}
    offset = int(view.get("byteOffset") or 0) + int(accessor.get("byteOffset") or 0)
    return accessor, view, offset


def _read_vec3_accessor(gltf: Dict[str, Any], bin_chunk: bytes, accessor_index: int) -> List[Tuple[float, float, float]]:
    accessor, view, offset = _accessor_byte_offset(gltf, accessor_index)
    if accessor.get("componentType") != 5126 or accessor.get("type") != "VEC3":
        raise GlbAssemblyError("Only FLOAT VEC3 position accessors are supported for mesh clipping")
    count = int(accessor.get("count") or 0)
    stride = int(view.get("byteStride") or 12)
    values: List[Tuple[float, float, float]] = []
    for i in range(count):
        pos = offset + i * stride
        values.append(struct.unpack_from("<fff", bin_chunk, pos))
    return values


def _read_vec2_accessor(gltf: Dict[str, Any], bin_chunk: bytes, accessor_index: int) -> List[Tuple[float, float]]:
    accessor, view, offset = _accessor_byte_offset(gltf, accessor_index)
    if accessor.get("componentType") != 5126 or accessor.get("type") != "VEC2":
        raise GlbAssemblyError("Only FLOAT VEC2 texture coordinate accessors are supported for mesh clipping")
    count = int(accessor.get("count") or 0)
    stride = int(view.get("byteStride") or 8)
    values: List[Tuple[float, float]] = []
    for i in range(count):
        pos = offset + i * stride
        values.append(struct.unpack_from("<ff", bin_chunk, pos))
    return values


def _read_index_accessor(gltf: Dict[str, Any], bin_chunk: bytes, accessor_index: int) -> Tuple[List[int], int]:
    accessor, view, offset = _accessor_byte_offset(gltf, accessor_index)
    component_type = int(accessor.get("componentType") or 0)
    count = int(accessor.get("count") or 0)
    stride = int(view.get("byteStride") or _component_size(component_type))
    if component_type == 5125:
        fmt = "<I"
    elif component_type == 5123:
        fmt = "<H"
    elif component_type == 5121:
        fmt = "<B"
    else:
        raise GlbAssemblyError("Only unsigned integer indices are supported for mesh clipping")
    values = [struct.unpack_from(fmt, bin_chunk, offset + i * stride)[0] for i in range(count)]
    return values, component_type


def _pack_indices(indices: List[int], component_type: int) -> bytes:
    if component_type == 5125:
        fmt = "<" + ("I" * len(indices))
    elif component_type == 5123:
        fmt = "<" + ("H" * len(indices))
    elif component_type == 5121:
        fmt = "<" + ("B" * len(indices))
    else:
        raise GlbAssemblyError("Unsupported index component type")
    return struct.pack(fmt, *indices) if indices else b""


def _primitive_base_color_image(gltf: Dict[str, Any], bin_chunk: bytes, primitive: Dict[str, Any]) -> Optional[Image.Image]:
    material_index = primitive.get("material")
    materials = gltf.get("materials") or []
    textures = gltf.get("textures") or []
    images = gltf.get("images") or []
    buffer_views = gltf.get("bufferViews") or []
    if not isinstance(material_index, int) or material_index < 0 or material_index >= len(materials):
        return None
    material = materials[material_index] if isinstance(materials[material_index], dict) else {}
    pbr = material.get("pbrMetallicRoughness") if isinstance(material.get("pbrMetallicRoughness"), dict) else {}
    tex_info = pbr.get("baseColorTexture") if isinstance(pbr.get("baseColorTexture"), dict) else {}
    texture_index = tex_info.get("index")
    if not isinstance(texture_index, int) or texture_index < 0 or texture_index >= len(textures):
        return None
    texture = textures[texture_index] if isinstance(textures[texture_index], dict) else {}
    source_index = texture.get("source")
    if not isinstance(source_index, int) or source_index < 0 or source_index >= len(images):
        return None
    image = images[source_index] if isinstance(images[source_index], dict) else {}
    view_index = image.get("bufferView")
    if not isinstance(view_index, int) or view_index < 0 or view_index >= len(buffer_views):
        return None
    view = buffer_views[view_index] if isinstance(buffer_views[view_index], dict) else {}
    offset = int(view.get("byteOffset") or 0)
    length = int(view.get("byteLength") or 0)
    if length <= 0:
        return None
    try:
        return Image.open(io.BytesIO(bin_chunk[offset:offset + length])).convert("RGB")
    except Exception:
        return None


def _texture_rgb_at(image: Image.Image, uv: Tuple[float, float]) -> Tuple[int, int, int]:
    width, height = image.size
    u = _clamp(float(uv[0]), 0.0, 1.0)
    v = _clamp(float(uv[1]), 0.0, 1.0)
    x = int(round(u * max(width - 1, 0)))
    y = int(round((1.0 - v) * max(height - 1, 0)))
    return image.getpixel((x, y))


def _is_red_neckwear_rgb(rgb: Tuple[int, int, int]) -> bool:
    r, g, b = rgb
    return r >= 85 and r > g * 1.28 and r > b * 1.18 and (r - g) >= 24


def _point_in_box(point: Tuple[float, float, float], box: Dict[str, Any]) -> bool:
    mn = box.get("min") if isinstance(box.get("min"), list) else []
    mx = box.get("max") if isinstance(box.get("max"), list) else []
    if len(mn) < 3 or len(mx) < 3:
        return False
    return (
        float(mn[0]) <= point[0] <= float(mx[0])
        and float(mn[1]) <= point[1] <= float(mx[1])
        and float(mn[2]) <= point[2] <= float(mx[2])
    )


def _triangle_cut_boxes(
    a: Tuple[float, float, float],
    b: Tuple[float, float, float],
    c: Tuple[float, float, float],
    boxes: List[Dict[str, Any]],
) -> List[Dict[str, Any]]:
    centroid = ((a[0] + b[0] + c[0]) / 3.0, (a[1] + b[1] + c[1]) / 3.0, (a[2] + b[2] + c[2]) / 3.0)
    matched: List[Dict[str, Any]] = []
    for box in boxes:
        if _point_in_box(centroid, box):
            matched.append(box)
            continue
        inside_count = int(_point_in_box(a, box)) + int(_point_in_box(b, box)) + int(_point_in_box(c, box))
        if inside_count >= 2:
            matched.append(box)
    return matched


def _cut_box_for_overlay(part: Dict[str, Any], placement: Dict[str, Any], layout: Dict[str, Any]) -> Dict[str, Any]:
    role_text = _part_text(part)
    anchor = placement.get("anchor") if isinstance(placement.get("anchor"), list) else [0.0, 0.0, 0.0]
    target_size = placement.get("target_size") if isinstance(placement.get("target_size"), dict) else {}
    width = max(0.01, float(target_size.get("width") or 0.01))
    height = max(0.01, float(target_size.get("height") or 0.01))
    base_center = layout.get("base_center") if isinstance(layout.get("base_center"), list) else [0.0, 0.0, 0.0]
    base_size = layout.get("base_size") if isinstance(layout.get("base_size"), list) else [width, height, width * 0.45]
    base_depth = max(0.01, float(base_size[2] if len(base_size) >= 3 else width * 0.45))
    half_x = width * 0.58
    half_y = height * 0.68
    half_z = base_depth * 0.62
    if any(token in role_text for token in ("bandana", "scarf", "neckwear")):
        half_x = width * 0.56
        half_y = height * 0.54
        z_min = float(base_center[2]) - base_depth * 0.08
        z_max = float(base_center[2]) + base_depth * 0.64
    else:
        z_min = float(base_center[2]) - half_z
        z_max = float(base_center[2]) + half_z
    return {
        "role": str(part.get("role") or ""),
        "min": [float(anchor[0]) - half_x, float(anchor[1]) - half_y, z_min],
        "max": [float(anchor[0]) + half_x, float(anchor[1]) + half_y, z_max],
        "anchor": [float(anchor[0]), float(anchor[1]), float(anchor[2])],
        "texture_filter": "red_neckwear" if any(token in role_text for token in ("bandana", "scarf", "neckwear")) else "",
    }


def _clip_base_mesh(gltf: Dict[str, Any], bin_chunk: bytes, cut_boxes: List[Dict[str, Any]]) -> Tuple[Dict[str, Any], bytes, Dict[str, Any]]:
    if not cut_boxes:
        return gltf, bin_chunk, {"enabled": False, "removed_triangles": 0, "total_triangles": 0, "cut_boxes": []}
    gltf = copy.deepcopy(gltf)
    out_bin = bytearray(bin_chunk)
    total_triangles = 0
    removed_triangles = 0
    texture_filtered_triangles = 0
    for mesh in gltf.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            if int(primitive.get("mode", 4) or 4) != 4:
                continue
            attrs = primitive.get("attributes") if isinstance(primitive.get("attributes"), dict) else {}
            pos_index = attrs.get("POSITION")
            uv_index = attrs.get("TEXCOORD_0")
            index_index = primitive.get("indices")
            if not isinstance(pos_index, int) or not isinstance(index_index, int):
                continue
            positions = _read_vec3_accessor(gltf, bin_chunk, pos_index)
            uvs: List[Tuple[float, float]] = []
            if isinstance(uv_index, int):
                try:
                    uvs = _read_vec2_accessor(gltf, bin_chunk, uv_index)
                except Exception:
                    uvs = []
            base_color_image = _primitive_base_color_image(gltf, bin_chunk, primitive) if uvs else None
            indices, component_type = _read_index_accessor(gltf, bin_chunk, index_index)
            if len(indices) < 3:
                continue
            kept: List[int] = []
            for i in range(0, len(indices) - 2, 3):
                tri = indices[i:i + 3]
                total_triangles += 1
                try:
                    a, b, c = positions[tri[0]], positions[tri[1]], positions[tri[2]]
                except Exception:
                    kept.extend(tri)
                    continue
                matched_boxes = _triangle_cut_boxes(a, b, c, cut_boxes)
                if not matched_boxes:
                    kept.extend(tri)
                    continue
                texture_filters = {str(box.get("texture_filter") or "") for box in matched_boxes}
                if "red_neckwear" in texture_filters:
                    if not base_color_image or not uvs:
                        kept.extend(tri)
                        continue
                    try:
                        uv = (
                            (uvs[tri[0]][0] + uvs[tri[1]][0] + uvs[tri[2]][0]) / 3.0,
                            (uvs[tri[0]][1] + uvs[tri[1]][1] + uvs[tri[2]][1]) / 3.0,
                        )
                        if not _is_red_neckwear_rgb(_texture_rgb_at(base_color_image, uv)):
                            kept.extend(tri)
                            continue
                        texture_filtered_triangles += 1
                    except Exception:
                        kept.extend(tri)
                        continue
                if matched_boxes:
                    removed_triangles += 1
                else:
                    kept.extend(tri)
            if len(kept) == len(indices):
                continue
            packed = _pack_indices(kept, component_type)
            offset = _align4(len(out_bin))
            if offset > len(out_bin):
                out_bin.extend(b"\x00" * (offset - len(out_bin)))
            out_bin.extend(packed)
            new_view_index = len(gltf.setdefault("bufferViews", []))
            gltf["bufferViews"].append({
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(packed),
                "target": 34963,
            })
            accessor = gltf["accessors"][index_index]
            accessor["bufferView"] = new_view_index
            accessor["byteOffset"] = 0
            accessor["count"] = len(kept)
            if kept:
                accessor["min"] = [min(kept)]
                accessor["max"] = [max(kept)]
    metrics = {
        "enabled": True,
        "removed_triangles": removed_triangles,
        "total_triangles": total_triangles,
        "removed_ratio": (removed_triangles / total_triangles) if total_triangles else 0.0,
        "texture_filtered_triangles": texture_filtered_triangles,
        "cut_boxes": cut_boxes,
    }
    return gltf, bytes(out_bin), metrics


def _replacement_kind(part: Dict[str, Any]) -> str:
    text = _part_text(part)
    if any(token in text for token in ("bandana", "scarf", "neckwear")):
        return "neckwear"
    if any(token in text for token in ("belt", "holster", "buckle", "strap", "gun", "pistol", "weapon")):
        return "belt_holster"
    return ""


def _mapped_source_target(
    part: Dict[str, Any],
    frame_width: float,
    frame_height: float,
    layout: Dict[str, Any],
) -> Dict[str, Any]:
    x1, y1, x2, y2 = _safe_box(part.get("source_box"), frame_width, frame_height)
    cx = (x1 + x2) * 0.5
    cy = (y1 + y2) * 0.5
    scale_2d = float(layout.get("scale_2d") or 1.0)
    layout_center = layout.get("center") if isinstance(layout.get("center"), list) else [frame_width * 0.5, frame_height * 0.5]
    base_center = layout.get("base_center") if isinstance(layout.get("base_center"), list) else [0.0, 0.0, 0.0]
    base_size = layout.get("base_size") if isinstance(layout.get("base_size"), list) else [1.0, 1.0, 0.35]
    width = max(0.01, (x2 - x1) * scale_2d)
    height = max(0.01, (y2 - y1) * scale_2d)
    anchor = [
        float(base_center[0]) + (cx - float(layout_center[0])) * scale_2d,
        float(base_center[1]) + (float(layout_center[1]) - cy) * scale_2d,
        float(base_center[2]),
    ]
    return {
        "source_box": [x1, y1, x2, y2],
        "anchor": anchor,
        "width": width,
        "height": height,
        "base_center": [float(v) for v in base_center[:3]],
        "base_size": [float(v) for v in base_size[:3]],
    }


def _primitive_triangle_arrays(
    gltf: Dict[str, Any],
    bin_chunk: bytes,
    primitive: Dict[str, Any],
) -> Optional[Dict[str, Any]]:
    np = _np()
    if int(primitive.get("mode", 4) or 4) != 4:
        return None
    attrs = primitive.get("attributes") if isinstance(primitive.get("attributes"), dict) else {}
    pos_index = attrs.get("POSITION")
    index_index = primitive.get("indices")
    if not isinstance(pos_index, int) or not isinstance(index_index, int):
        return None
    positions = np.asarray(_read_vec3_accessor(gltf, bin_chunk, pos_index), dtype=np.float32)
    indices, component_type = _read_index_accessor(gltf, bin_chunk, index_index)
    if len(indices) < 3:
        return None
    triangles = np.asarray(indices, dtype=np.int64).reshape(-1, 3)
    centers = positions[triangles].mean(axis=1)
    rgb: Any = None
    uv_index = attrs.get("TEXCOORD_0")
    if isinstance(uv_index, int):
        try:
            uvs = np.asarray(_read_vec2_accessor(gltf, bin_chunk, uv_index), dtype=np.float32)
            image = _primitive_base_color_image(gltf, bin_chunk, primitive)
            if image is not None and len(uvs) >= len(positions):
                arr = np.asarray(image.convert("RGB"))
                uv_centers = uvs[triangles].mean(axis=1)
                xs = np.clip(uv_centers[:, 0], 0.0, 1.0) * max(arr.shape[1] - 1, 0)
                ys = (1.0 - np.clip(uv_centers[:, 1], 0.0, 1.0)) * max(arr.shape[0] - 1, 0)
                rgb = arr[ys.astype(np.int32), xs.astype(np.int32)]
        except Exception:
            rgb = None
    return {
        "positions": positions,
        "triangles": triangles,
        "centers": centers,
        "rgb": rgb,
        "component_type": component_type,
        "index_accessor": index_index,
    }


def _mesh_color_masks(rgb: Any) -> Dict[str, Any]:
    np = _np()
    if rgb is None:
        empty = np.zeros(0, dtype=bool)
        return {"red": empty, "leather": empty, "dark": empty, "metal": empty, "accessory": empty}
    values = rgb.astype(np.float32)
    r = values[:, 0]
    g = values[:, 1]
    b = values[:, 2]
    mean = (r + g + b) / 3.0
    red = (r >= 72) & (r > g * 1.16) & (r > b * 1.10) & ((r - g) >= 12)
    leather = (r > 36) & (r < 215) & (g > 16) & (b < 170) & (r >= g * 0.88) & (g >= b * 0.62) & ((r - b) >= 12)
    dark = mean < 82
    metal = (mean >= 82) & (mean <= 210) & (np.abs(r - g) < 42) & (np.abs(g - b) < 48)
    return {
        "red": red,
        "leather": leather,
        "dark": dark,
        "metal": metal,
        "accessory": leather | dark | metal,
    }


def _components_from_triangle_mask(
    triangles: Any,
    centers: Any,
    rgb: Any,
    mask: Any,
) -> List[Dict[str, Any]]:
    np = _np()
    tri_ids = np.flatnonzero(mask)
    if len(tri_ids) == 0:
        return []
    vertex_to_tris: Dict[int, List[int]] = defaultdict(list)
    tri_set = set(int(v) for v in tri_ids.tolist())
    for tri_id in tri_ids:
        for vertex in triangles[int(tri_id)]:
            vertex_to_tris[int(vertex)].append(int(tri_id))

    seen: set[int] = set()
    components: List[Dict[str, Any]] = []
    for start in tri_ids.tolist():
        start = int(start)
        if start in seen:
            continue
        stack = [start]
        seen.add(start)
        comp_ids: List[int] = []
        while stack:
            tri_id = stack.pop()
            comp_ids.append(tri_id)
            for vertex in triangles[tri_id]:
                for next_tri in vertex_to_tris[int(vertex)]:
                    if next_tri in tri_set and next_tri not in seen:
                        seen.add(next_tri)
                        stack.append(next_tri)
        comp_array = np.asarray(comp_ids, dtype=np.int64)
        pts = centers[comp_array]
        mn = pts.min(axis=0)
        mx = pts.max(axis=0)
        size = mx - mn
        center = pts.mean(axis=0)
        mean_rgb = rgb[comp_array].mean(axis=0).tolist() if rgb is not None else None
        components.append({
            "tri_ids": comp_array,
            "triangles": int(len(comp_array)),
            "bbox_min": mn.tolist(),
            "bbox_max": mx.tolist(),
            "size": size.tolist(),
            "center": center.tolist(),
            "mean_rgb": mean_rgb,
        })
    components.sort(key=lambda item: int(item.get("triangles") or 0), reverse=True)
    return components


def _bbox_from_points(points: Any) -> Dict[str, Any]:
    np = _np()
    mn = points.min(axis=0)
    mx = points.max(axis=0)
    size = np.maximum(mx - mn, 1e-6)
    center = (mn + mx) * 0.5
    return {
        "min": mn.tolist(),
        "max": mx.tolist(),
        "size": size.tolist(),
        "center": center.tolist(),
    }


def _component_public_summary(component: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "triangles": int(component.get("triangles") or 0),
        "bbox_min": component.get("bbox_min"),
        "bbox_max": component.get("bbox_max"),
        "size": component.get("size"),
        "center": component.get("center"),
        "mean_rgb": component.get("mean_rgb"),
        "score": component.get("score"),
    }


def _belt_component_score(component: Dict[str, Any], target: Dict[str, Any], base_size: List[float]) -> float:
    center = component.get("center") if isinstance(component.get("center"), list) else [0.0, 0.0, 0.0]
    size = component.get("size") if isinstance(component.get("size"), list) else [0.0, 0.0, 0.0]
    tri_count = float(component.get("triangles") or 0)
    y_half = max(float(target["height"]) * 0.78, float(base_size[1]) * 0.075, 1e-6)
    x_half = max(float(target["width"]) * 0.58, float(base_size[0]) * 0.28, 1e-6)
    dy = abs(float(center[1]) - float(target["anchor"][1])) / y_half
    dx = max(0.0, abs(float(center[0]) - float(target["anchor"][0])) - x_half) / max(float(base_size[0]), 1e-6)
    compact_y = 1.0 if float(size[1]) <= max(float(target["height"]) * 1.9, float(base_size[1]) * 0.22) else 0.0
    compact_z = 1.0 if float(size[2]) <= max(float(base_size[2]) * 0.75, 0.05) else 0.0
    support = min(1.0, tri_count / 520.0)
    side_bonus = min(0.22, abs(float(center[0])) / max(float(base_size[0]), 1e-6))
    score = 1.05 - 0.42 * dy - 0.20 * dx + 0.22 * support + side_bonus
    if not compact_y:
        score -= 0.55
    if not compact_z:
        score -= 0.35
    return float(score)


def _find_base_replacement_target(
    gltf: Dict[str, Any],
    bin_chunk: bytes,
    part: Dict[str, Any],
    *,
    kind: str,
    frame_width: float,
    frame_height: float,
    layout: Dict[str, Any],
) -> Dict[str, Any]:
    np = _np()
    target = _mapped_source_target(part, frame_width, frame_height, layout)
    base_center = target["base_center"]
    base_size = [max(1e-6, float(v)) for v in target["base_size"]]
    target["anchor"][2] = base_center[2]
    primitive_selections: Dict[int, Any] = {}
    selected_points: List[Any] = []
    selected_components: List[Dict[str, Any]] = []
    candidate_triangles = 0
    total_triangles = 0
    failure_reason = ""

    primitive_counter = -1
    for mesh in gltf.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            primitive_counter += 1
            arrays = _primitive_triangle_arrays(gltf, bin_chunk, primitive)
            if not arrays:
                continue
            centers = arrays["centers"]
            triangles = arrays["triangles"]
            rgb = arrays["rgb"]
            total_triangles += int(len(triangles))
            if rgb is None:
                failure_reason = "base_texture_unavailable"
                continue
            color = _mesh_color_masks(rgb)
            x = centers[:, 0]
            y = centers[:, 1]
            z = centers[:, 2]
            if kind == "neckwear":
                half_x = max(float(target["width"]) * 0.66, base_size[0] * 0.20)
                half_y = max(float(target["height"]) * 0.78, base_size[1] * 0.065)
                z_min = base_center[2] - base_size[2] * 0.18
                z_max = base_center[2] + base_size[2] * 0.72
                spatial = (
                    (x >= target["anchor"][0] - half_x)
                    & (x <= target["anchor"][0] + half_x)
                    & (y >= target["anchor"][1] - half_y)
                    & (y <= target["anchor"][1] + half_y)
                    & (z >= z_min)
                    & (z <= z_max)
                )
                mask = spatial & color["red"]
                candidate_triangles += int(mask.sum())
                if not int(mask.sum()):
                    continue
                components = _components_from_triangle_mask(triangles, centers, rgb, mask)
                keep_components = [comp for comp in components if int(comp.get("triangles") or 0) >= 12]
                keep_ids = np.concatenate([comp["tri_ids"] for comp in keep_components]) if keep_components else np.flatnonzero(mask)
                primitive_selections[primitive_counter] = keep_ids.astype(np.int64)
                selected_points.append(centers[keep_ids])
                selected_components.extend(keep_components[:16])
            elif kind == "belt_holster":
                half_x = max(float(target["width"]) * 0.62, base_size[0] * 0.44)
                half_y = max(float(target["height"]) * 0.76, base_size[1] * 0.082)
                z_min = base_center[2] - base_size[2] * 0.58
                z_max = base_center[2] + base_size[2] * 0.58
                side_zone = np.abs(x - float(base_center[0])) > base_size[0] * 0.235
                narrow_buckle_zone = (
                    (np.abs(x - float(base_center[0])) < base_size[0] * 0.18)
                    & (np.abs(y - float(target["anchor"][1])) < max(float(target["height"]) * 0.33, base_size[1] * 0.045))
                )
                belt_height_zone = np.abs(y - float(target["anchor"][1])) < max(float(target["height"]) * 0.58, base_size[1] * 0.075)
                if rgb is not None:
                    values = rgb.astype(np.float32)
                    rr = values[:, 0]
                    gg = values[:, 1]
                    bb = values[:, 2]
                    warm_leather = (rr >= gg * 0.94) & (gg >= bb * 0.68) & ((rr - bb) >= 14.0)
                else:
                    warm_leather = np.zeros(len(centers), dtype=bool)
                spatial = (
                    (x >= target["anchor"][0] - half_x)
                    & (x <= target["anchor"][0] + half_x)
                    & (y >= target["anchor"][1] - half_y)
                    & (y <= target["anchor"][1] + half_y)
                    & (z >= z_min)
                    & (z <= z_max)
                )
                mask = spatial & (
                    (color["leather"] & warm_leather)
                    | (color["dark"] & warm_leather & side_zone & belt_height_zone)
                    | (color["metal"] & narrow_buckle_zone)
                )
                candidate_triangles += int(mask.sum())
                if not int(mask.sum()):
                    continue
                components = _components_from_triangle_mask(triangles, centers, rgb, mask)
                scored: List[Dict[str, Any]] = []
                max_size_y = max(float(target["height"]) * 1.9, base_size[1] * 0.22)
                max_size_z = max(base_size[2] * 0.76, 0.055)
                for comp in components:
                    if int(comp.get("triangles") or 0) < 18:
                        continue
                    size = comp.get("size") if isinstance(comp.get("size"), list) else [0.0, 0.0, 0.0]
                    center = comp.get("center") if isinstance(comp.get("center"), list) else [0.0, 0.0, 0.0]
                    mean_rgb = comp.get("mean_rgb") if isinstance(comp.get("mean_rgb"), list) else [0.0, 0.0, 0.0]
                    mean_brightness = (float(mean_rgb[0]) + float(mean_rgb[1]) + float(mean_rgb[2])) / 3.0
                    is_side_component = abs(float(center[0]) - float(base_center[0])) > base_size[0] * 0.225
                    is_brown_leather = (
                        float(mean_rgb[0]) >= float(mean_rgb[1]) * 0.94
                        and float(mean_rgb[1]) >= float(mean_rgb[2]) * 0.68
                        and (float(mean_rgb[0]) - float(mean_rgb[2])) >= 14.0
                    )
                    is_belt_band = float(size[0]) >= base_size[0] * 0.20 and float(size[1]) <= max(float(target["height"]) * 0.62, base_size[1] * 0.090)
                    if not is_brown_leather and not is_belt_band:
                        continue
                    if mean_brightness < 68.0 and not is_side_component and not is_belt_band:
                        continue
                    if not is_side_component and not is_brown_leather and not is_belt_band:
                        continue
                    if not is_side_component and float(size[1]) > max(float(target["height"]) * 0.78, base_size[1] * 0.105):
                        continue
                    if float(size[1]) > max_size_y or float(size[2]) > max_size_z:
                        continue
                    comp = dict(comp)
                    comp["score"] = _belt_component_score(comp, target, base_size)
                    if float(comp["score"]) >= 0.28:
                        scored.append(comp)
                scored.sort(key=lambda item: (float(item.get("score") or 0.0), int(item.get("triangles") or 0)), reverse=True)
                selected_ids: List[Any] = []
                selected_total = 0
                max_remove = max(900, int(len(triangles) * 0.066))
                for comp in scored:
                    tri_ids = comp["tri_ids"]
                    if selected_total + len(tri_ids) > max_remove and selected_total >= 600:
                        continue
                    selected_ids.append(tri_ids)
                    selected_total += len(tri_ids)
                    selected_components.append(comp)
                    if selected_total >= max_remove:
                        break
                if selected_ids:
                    keep_ids = np.unique(np.concatenate(selected_ids)).astype(np.int64)
                    primitive_selections[primitive_counter] = keep_ids
                    selected_points.append(centers[keep_ids])

    if not selected_points:
        return {
            "passed": False,
            "kind": kind,
            "reason": failure_reason or "no_confident_base_mesh_region",
            "candidate_triangles": candidate_triangles,
            "total_triangles": total_triangles,
            "source_target": target,
        }

    all_points = np.concatenate(selected_points, axis=0)
    target_bbox = _bbox_from_points(all_points)
    removed_triangles = int(sum(len(ids) for ids in primitive_selections.values()))
    removed_ratio = float(removed_triangles / max(total_triangles, 1))
    min_required = 260 if kind == "neckwear" else 520
    max_ratio = 0.080 if kind == "neckwear" else 0.125
    passed = removed_triangles >= min_required and removed_ratio <= max_ratio
    reason = "ok" if passed else ("too_few_target_triangles" if removed_triangles < min_required else "target_region_too_large")
    return {
        "passed": passed,
        "kind": kind,
        "reason": reason,
        "remove_by_primitive": primitive_selections,
        "removed_triangles": removed_triangles,
        "removed_ratio": removed_ratio,
        "candidate_triangles": candidate_triangles,
        "total_triangles": total_triangles,
        "source_target": target,
        "target_bbox": target_bbox,
        "component_count": len(selected_components),
        "components": [_component_public_summary(comp) for comp in selected_components[:18]],
    }


def _target_guided_placement(
    part: Dict[str, Any],
    bbox: Dict[str, Any],
    analysis: Dict[str, Any],
    layout: Dict[str, Any],
    frame_width: float,
    frame_height: float,
) -> Dict[str, Any]:
    kind = str(analysis.get("kind") or "")
    target = analysis.get("source_target") if isinstance(analysis.get("source_target"), dict) else {}
    target_bbox = analysis.get("target_bbox") if isinstance(analysis.get("target_bbox"), dict) else {}
    source_target = _mapped_source_target(part, frame_width, frame_height, layout)
    source_box = source_target["source_box"]
    base_size = target.get("base_size") if isinstance(target.get("base_size"), list) else source_target["base_size"]
    base_center = target.get("base_center") if isinstance(target.get("base_center"), list) else source_target["base_center"]
    bbox_size = target_bbox.get("size") if isinstance(target_bbox.get("size"), list) else [source_target["width"], source_target["height"], base_size[2] * 0.25]
    bbox_center = target_bbox.get("center") if isinstance(target_bbox.get("center"), list) else source_target["anchor"]

    if kind == "neckwear":
        desired_x = max(source_target["width"] * 1.00, float(bbox_size[0]) * 1.08, float(base_size[0]) * 0.58)
        desired_y = max(source_target["height"] * 0.90, float(bbox_size[1]) * 0.96)
        desired_z = min(float(base_size[2]) * 0.42, max(float(bbox_size[2]) * 1.16, float(base_size[2]) * 0.15))
        anchor = [
            float(bbox_center[0]),
            float(bbox_center[1]),
            float(bbox_center[2]) + float(base_size[2]) * 0.035,
        ]
    elif kind == "belt_holster":
        desired_x = min(float(base_size[0]) * 0.96, max(source_target["width"] * 0.84, float(base_size[0]) * 0.78))
        desired_y = min(float(base_size[1]) * 0.18, max(source_target["height"] * 0.72, float(base_size[1]) * 0.105))
        desired_z = min(float(base_size[2]) * 0.34, max(float(base_size[2]) * 0.16, float(bbox_size[2]) * 0.72))
        anchor = [
            float(base_center[0]) + (float(source_target["anchor"][0]) - float(base_center[0])) * 0.35,
            float(source_target["anchor"][1]),
            float(base_center[2]) + (float(bbox_center[2]) - float(base_center[2])) * 0.55,
        ]
    else:
        desired_x = max(source_target["width"], float(bbox_size[0]))
        desired_y = max(source_target["height"], float(bbox_size[1]))
        desired_z = max(float(bbox_size[2]), float(base_size[2]) * 0.18)
        anchor = list(source_target["anchor"])

    local_size = bbox.get("size") if isinstance(bbox.get("size"), list) else [1.0, 1.0, 1.0]
    local_center = bbox.get("center") if isinstance(bbox.get("center"), list) else [0.0, 0.0, 0.0]
    scale_x = _clamp(desired_x / max(float(local_size[0]), 1e-6), 0.01, 8.0)
    scale_y = _clamp(desired_y / max(float(local_size[1]), 1e-6), 0.01, 8.0)
    scale_z = _clamp(desired_z / max(float(local_size[2]), 1e-6), 0.01, 8.0)
    translation = [
        float(anchor[0]) - scale_x * float(local_center[0]),
        float(anchor[1]) - scale_y * float(local_center[1]),
        float(anchor[2]) - scale_z * float(local_center[2]),
    ]
    matrix = [
        scale_x, 0.0, 0.0, 0.0,
        0.0, scale_y, 0.0, 0.0,
        0.0, 0.0, scale_z, 0.0,
        translation[0], translation[1], translation[2], 1.0,
    ]
    return {
        "source_box": source_box,
        "anchor": anchor,
        "scale": {"x": scale_x, "y": scale_y, "z": scale_z},
        "matrix": matrix,
        "target_size": {"width": desired_x, "height": desired_y, "depth": desired_z},
        "replacement_kind": kind,
        "replacement_target_bbox": target_bbox,
    }


def _clip_base_mesh_by_triangles(
    gltf: Dict[str, Any],
    bin_chunk: bytes,
    remove_by_primitive: Dict[int, Any],
) -> Tuple[Dict[str, Any], bytes, Dict[str, Any]]:
    np = _np()
    if not remove_by_primitive:
        return gltf, bin_chunk, {"enabled": False, "removed_triangles": 0, "total_triangles": 0, "removed_ratio": 0.0}
    gltf = copy.deepcopy(gltf)
    out_bin = bytearray(bin_chunk)
    total_triangles = 0
    removed_triangles = 0
    primitive_counter = -1
    for mesh in gltf.get("meshes") or []:
        for primitive in mesh.get("primitives") or []:
            primitive_counter += 1
            remove_ids = remove_by_primitive.get(primitive_counter)
            index_index = primitive.get("indices")
            if remove_ids is None or not isinstance(index_index, int):
                try:
                    indices, _ = _read_index_accessor(gltf, bin_chunk, index_index) if isinstance(index_index, int) else ([], 0)
                    total_triangles += len(indices) // 3
                except Exception:
                    pass
                continue
            indices, component_type = _read_index_accessor(gltf, bin_chunk, index_index)
            triangle_count = len(indices) // 3
            total_triangles += triangle_count
            remove_set = set(int(v) for v in np.asarray(remove_ids, dtype=np.int64).tolist())
            if not remove_set:
                continue
            kept: List[int] = []
            for tri_idx in range(triangle_count):
                tri = indices[tri_idx * 3: tri_idx * 3 + 3]
                if tri_idx in remove_set:
                    removed_triangles += 1
                else:
                    kept.extend(tri)
            if len(kept) == len(indices):
                continue
            packed = _pack_indices(kept, component_type)
            offset = _align4(len(out_bin))
            if offset > len(out_bin):
                out_bin.extend(b"\x00" * (offset - len(out_bin)))
            out_bin.extend(packed)
            new_view_index = len(gltf.setdefault("bufferViews", []))
            gltf["bufferViews"].append({
                "buffer": 0,
                "byteOffset": offset,
                "byteLength": len(packed),
                "target": 34963,
            })
            accessor = gltf["accessors"][index_index]
            accessor["bufferView"] = new_view_index
            accessor["byteOffset"] = 0
            accessor["count"] = len(kept)
            if kept:
                accessor["min"] = [min(kept)]
                accessor["max"] = [max(kept)]
    return gltf, bytes(out_bin), {
        "enabled": True,
        "removed_triangles": removed_triangles,
        "total_triangles": total_triangles,
        "removed_ratio": removed_triangles / max(total_triangles, 1),
    }


def _strip_replacement_analysis(analysis: Dict[str, Any]) -> Dict[str, Any]:
    out = {key: value for key, value in analysis.items() if key != "remove_by_primitive"}
    return out


def _source_layout(
    parts: List[Dict[str, Any]],
    frame_width: float,
    frame_height: float,
    *,
    object_height: float = 6.0,
    base_center: Optional[List[float]] = None,
    base_size: Optional[List[float]] = None,
) -> Dict[str, Any]:
    boxes = [_safe_box(part.get("source_box"), frame_width, frame_height) for part in parts]
    if not boxes:
        boxes = [(0.0, 0.0, frame_width, frame_height)]
    x1 = min(box[0] for box in boxes)
    y1 = min(box[1] for box in boxes)
    x2 = max(box[2] for box in boxes)
    y2 = max(box[3] for box in boxes)
    union_w = max(1.0, x2 - x1)
    union_h = max(1.0, y2 - y1)
    if not math.isfinite(object_height) or object_height <= 0:
        object_height = 6.0
    scale_2d = object_height / union_h
    object_width = union_w * scale_2d
    areas = [max(1.0, (box[2] - box[0]) * (box[3] - box[1])) for box in boxes]
    return {
        "box": [x1, y1, x2, y2],
        "center": [(x1 + x2) * 0.5, (y1 + y2) * 0.5],
        "width": union_w,
        "height": union_h,
        "area": union_w * union_h,
        "object_width": object_width,
        "object_height": object_height,
        "scale_2d": scale_2d,
        "min_part_area": min(areas),
        "max_part_area": max(areas),
        "base_center": list(base_center or [0.0, 0.0, 0.0]),
        "base_size": list(base_size or [object_width, object_height, object_width * 0.35]),
    }


def _bounded_anisotropic_scales(scale_x: float, scale_y: float) -> Tuple[float, float]:
    if not math.isfinite(scale_x) or scale_x <= 0:
        scale_x = 1.0
    if not math.isfinite(scale_y) or scale_y <= 0:
        scale_y = 1.0
    base = math.sqrt(scale_x * scale_y)
    if not math.isfinite(base) or base <= 0:
        return 1.0, 1.0
    max_ratio = 3.5
    ratio_x = _clamp(scale_x / base, 1.0 / max_ratio, max_ratio)
    ratio_y = _clamp(scale_y / base, 1.0 / max_ratio, max_ratio)
    return base * ratio_x, base * ratio_y


def _placement(part: Dict[str, Any], bbox: Dict[str, Any], frame_width: float, frame_height: float, layout: Dict[str, Any]) -> Dict[str, Any]:
    x1, y1, x2, y2 = _safe_box(part.get("source_box"), frame_width, frame_height)
    box_w = max(1.0, x2 - x1)
    box_h = max(1.0, y2 - y1)
    cx = (x1 + x2) * 0.5
    cy = (y1 + y2) * 0.5
    scale_2d = float(layout["scale_2d"])
    target_width = max(0.03 * float(layout["object_height"]), box_w * scale_2d)
    target_height = max(0.03 * float(layout["object_height"]), box_h * scale_2d)
    local_size = bbox["size"]
    scale_y = target_height / max(local_size[1], 1e-6)
    scale_x = target_width / max(local_size[0], 1e-6)
    scale_x, scale_y = _bounded_anisotropic_scales(scale_x, scale_y)
    box_area = box_w * box_h
    area_ratio = _clamp(box_area / max(float(layout["area"]), 1.0), 0.0, 1.0)
    scale_x = max(0.05, min(4.0, scale_x if math.isfinite(scale_x) and scale_x > 0 else 1.0))
    scale_y = max(0.05, min(4.0, scale_y if math.isfinite(scale_y) and scale_y > 0 else 1.0))
    if isinstance(layout.get("base_size"), list) and len(layout["base_size"]) >= 2:
        base_w = max(0.01, float(layout["base_size"][0]))
        base_h = max(0.01, float(layout["base_size"][1]))
        role_text = " ".join(str(part.get(key) or "").lower() for key in ("role", "label", "source"))
        if any(token in role_text for token in ("boot", "shoe", "foot", "靴", "鞋", "脚")):
            max_h = base_h * 0.26
            max_w = base_w * 0.72
        elif any(token in role_text for token in ("leg", "腿")):
            max_h = base_h * 0.46
            max_w = base_w * 0.86
        elif any(token in role_text for token in ("arm", "hand", "glove", "臂", "手")):
            max_h = base_h * 0.48
            max_w = base_w * 0.62
        elif any(token in role_text for token in ("head", "hat", "face", "helmet", "头", "帽", "脸", "盔")):
            max_h = base_h * 0.34
            max_w = base_w * 1.18
        elif any(token in role_text for token in ("jacket", "torso", "body", "夹克", "躯干", "身体")):
            max_h = base_h * 0.48
            max_w = base_w * 1.28
        else:
            max_h = base_h * 0.55
            max_w = base_w * 1.18
        scaled_w = local_size[0] * scale_x
        scaled_h = local_size[1] * scale_y
        limiter = min(1.0, max_w / max(scaled_w, 1e-6), max_h / max(scaled_h, 1e-6))
        if limiter < 1.0:
            scale_x *= limiter
            scale_y *= limiter
    depth_scale = math.sqrt(max(scale_x * scale_y, 1e-6))
    aspect = max(target_width / max(target_height, 1e-6), target_height / max(target_width, 1e-6))
    flatness_damping = 1.0 + 0.08 * _clamp(aspect - 1.0, 0.0, 4.0)
    scale_z = depth_scale / flatness_damping
    scale_z = max(min(scale_x, scale_y) * 0.72, min(max(scale_x, scale_y) * 1.12, scale_z))
    scale_z = max(0.05, min(4.0, scale_z if math.isfinite(scale_z) and scale_z > 0 else min(scale_x, scale_y)))
    layout_center = layout["center"]
    base_center = layout.get("base_center") if isinstance(layout.get("base_center"), list) else [0.0, 0.0, 0.0]
    base_size = layout.get("base_size") if isinstance(layout.get("base_size"), list) else [layout["object_width"], layout["object_height"], layout["object_width"] * 0.35]
    base_depth = max(0.0, float(base_size[2] if len(base_size) >= 3 else 0.0))
    anchor = [
        float(base_center[0]) + (cx - float(layout_center[0])) * scale_2d,
        float(base_center[1]) + (float(layout_center[1]) - cy) * scale_2d,
        float(base_center[2]) + max(base_depth * 0.08, float(layout["object_height"]) * 0.012) + (1.0 - area_ratio) * float(layout["object_height"]) * 0.006,
    ]
    center = bbox["center"]
    translation = [
        anchor[0] - scale_x * center[0],
        anchor[1] - scale_y * center[1],
        anchor[2] - scale_z * center[2],
    ]
    matrix = [
        scale_x, 0.0, 0.0, 0.0,
        0.0, scale_y, 0.0, 0.0,
        0.0, 0.0, scale_z, 0.0,
        translation[0], translation[1], translation[2], 1.0,
    ]
    return {
        "source_box": [x1, y1, x2, y2],
        "anchor": anchor,
        "scale": {"x": scale_x, "y": scale_y, "z": scale_z},
        "matrix": matrix,
        "target_size": {"width": target_width, "height": target_height},
        "layout_frame": {
            "width": layout["object_width"],
            "height": layout["object_height"],
            "source_box": layout["box"],
        },
    }


def _append_unique(dst: List[Any], src: Any) -> None:
    for item in src or []:
        if item not in dst:
            dst.append(item)


def _offset_texture_info(value: Any, texture_offset: int) -> None:
    if isinstance(value, dict):
        if isinstance(value.get("index"), int):
            value["index"] += texture_offset
        for child in value.values():
            _offset_texture_info(child, texture_offset)
    elif isinstance(value, list):
        for child in value:
            _offset_texture_info(child, texture_offset)


def _remap_material(material: Dict[str, Any], texture_offset: int) -> Dict[str, Any]:
    item = copy.deepcopy(material)
    pbr = item.get("pbrMetallicRoughness")
    if isinstance(pbr, dict):
        _offset_texture_info(pbr.get("baseColorTexture"), texture_offset)
        _offset_texture_info(pbr.get("metallicRoughnessTexture"), texture_offset)
    for key in ("normalTexture", "occlusionTexture", "emissiveTexture"):
        _offset_texture_info(item.get(key), texture_offset)
    if isinstance(item.get("extensions"), dict):
        _offset_texture_info(item["extensions"], texture_offset)
    return item


def _scene_roots(gltf: Dict[str, Any], node_offset: int, node_count: int) -> List[int]:
    scenes = gltf.get("scenes") if isinstance(gltf.get("scenes"), list) else []
    scene_index = gltf.get("scene") if isinstance(gltf.get("scene"), int) else 0
    if 0 <= scene_index < len(scenes):
        nodes = scenes[scene_index].get("nodes") if isinstance(scenes[scene_index], dict) else None
        if isinstance(nodes, list) and nodes:
            return [node_offset + int(node) for node in nodes if isinstance(node, int)]
    child_nodes = set()
    for idx, node in enumerate(gltf.get("nodes") or []):
        for child in node.get("children") or []:
            if isinstance(child, int):
                child_nodes.add(child)
    roots = [idx for idx in range(node_count) if idx not in child_nodes]
    return [node_offset + idx for idx in roots]


def _merge_part(
    combined: Dict[str, Any],
    combined_bin: bytearray,
    *,
    gltf: Dict[str, Any],
    bin_chunk: bytes,
    wrapper_name: str,
    wrapper_matrix: List[float],
    wrapper_extras: Dict[str, Any],
) -> None:
    offsets = {
        "bufferViews": len(combined.get("bufferViews") or []),
        "accessors": len(combined.get("accessors") or []),
        "images": len(combined.get("images") or []),
        "samplers": len(combined.get("samplers") or []),
        "textures": len(combined.get("textures") or []),
        "materials": len(combined.get("materials") or []),
        "meshes": len(combined.get("meshes") or []),
        "cameras": len(combined.get("cameras") or []),
        "skins": len(combined.get("skins") or []),
        "nodes": len(combined.get("nodes") or []),
    }
    start = _align4(len(combined_bin))
    if start > len(combined_bin):
        combined_bin.extend(b"\x00" * (start - len(combined_bin)))
    combined_bin.extend(bin_chunk)

    for section in ("extensionsUsed", "extensionsRequired"):
        if isinstance(gltf.get(section), list):
            combined.setdefault(section, [])
            _append_unique(combined[section], gltf[section])

    combined.setdefault("bufferViews", [])
    for view in gltf.get("bufferViews") or []:
        item = copy.deepcopy(view)
        item["buffer"] = 0
        item["byteOffset"] = int(item.get("byteOffset") or 0) + start
        combined["bufferViews"].append(item)

    combined.setdefault("accessors", [])
    for accessor in gltf.get("accessors") or []:
        item = copy.deepcopy(accessor)
        if isinstance(item.get("bufferView"), int):
            item["bufferView"] += offsets["bufferViews"]
        sparse = item.get("sparse")
        if isinstance(sparse, dict):
            indices = sparse.get("indices")
            values = sparse.get("values")
            if isinstance(indices, dict) and isinstance(indices.get("bufferView"), int):
                indices["bufferView"] += offsets["bufferViews"]
            if isinstance(values, dict) and isinstance(values.get("bufferView"), int):
                values["bufferView"] += offsets["bufferViews"]
        combined["accessors"].append(item)

    combined.setdefault("images", [])
    for image in gltf.get("images") or []:
        item = copy.deepcopy(image)
        if isinstance(item.get("bufferView"), int):
            item["bufferView"] += offsets["bufferViews"]
        combined["images"].append(item)

    combined.setdefault("samplers", [])
    for sampler in gltf.get("samplers") or []:
        combined["samplers"].append(copy.deepcopy(sampler))

    combined.setdefault("textures", [])
    for texture in gltf.get("textures") or []:
        item = copy.deepcopy(texture)
        if isinstance(item.get("sampler"), int):
            item["sampler"] += offsets["samplers"]
        if isinstance(item.get("source"), int):
            item["source"] += offsets["images"]
        combined["textures"].append(item)

    combined.setdefault("materials", [])
    for material in gltf.get("materials") or []:
        combined["materials"].append(_remap_material(material, offsets["textures"]))

    combined.setdefault("meshes", [])
    for mesh in gltf.get("meshes") or []:
        item = copy.deepcopy(mesh)
        for primitive in item.get("primitives") or []:
            attrs = primitive.get("attributes")
            if isinstance(attrs, dict):
                for key, value in list(attrs.items()):
                    if isinstance(value, int):
                        attrs[key] = value + offsets["accessors"]
            if isinstance(primitive.get("indices"), int):
                primitive["indices"] += offsets["accessors"]
            if isinstance(primitive.get("material"), int):
                primitive["material"] += offsets["materials"]
            for target in primitive.get("targets") or []:
                if isinstance(target, dict):
                    for key, value in list(target.items()):
                        if isinstance(value, int):
                            target[key] = value + offsets["accessors"]
        combined["meshes"].append(item)

    combined.setdefault("cameras", [])
    for camera in gltf.get("cameras") or []:
        combined["cameras"].append(copy.deepcopy(camera))

    combined.setdefault("skins", [])
    for skin in gltf.get("skins") or []:
        item = copy.deepcopy(skin)
        if isinstance(item.get("inverseBindMatrices"), int):
            item["inverseBindMatrices"] += offsets["accessors"]
        if isinstance(item.get("skeleton"), int):
            item["skeleton"] += offsets["nodes"]
        if isinstance(item.get("joints"), list):
            item["joints"] = [joint + offsets["nodes"] if isinstance(joint, int) else joint for joint in item["joints"]]
        combined["skins"].append(item)

    original_node_count = len(gltf.get("nodes") or [])
    combined.setdefault("nodes", [])
    for node in gltf.get("nodes") or []:
        item = copy.deepcopy(node)
        if isinstance(item.get("mesh"), int):
            item["mesh"] += offsets["meshes"]
        if isinstance(item.get("camera"), int):
            item["camera"] += offsets["cameras"]
        if isinstance(item.get("skin"), int):
            item["skin"] += offsets["skins"]
        if isinstance(item.get("children"), list):
            item["children"] = [child + offsets["nodes"] if isinstance(child, int) else child for child in item["children"]]
        combined["nodes"].append(item)

    combined.setdefault("animations", [])
    for animation in gltf.get("animations") or []:
        item = copy.deepcopy(animation)
        for sampler in item.get("samplers") or []:
            if isinstance(sampler.get("input"), int):
                sampler["input"] += offsets["accessors"]
            if isinstance(sampler.get("output"), int):
                sampler["output"] += offsets["accessors"]
        for channel in item.get("channels") or []:
            target = channel.get("target") if isinstance(channel.get("target"), dict) else {}
            if isinstance(target.get("node"), int):
                target["node"] += offsets["nodes"]
        combined["animations"].append(item)

    roots = _scene_roots(gltf, offsets["nodes"], original_node_count)
    wrapper_index = len(combined["nodes"])
    combined["nodes"].append({
        "name": wrapper_name,
        "matrix": wrapper_matrix,
        "children": roots,
        "extras": wrapper_extras,
    })
    combined["scenes"][0]["nodes"].append(wrapper_index)


def assemble_parts(
    *,
    parts: List[Dict[str, Any]],
    frame_width: int,
    frame_height: int,
    dest_glb: Path,
    dest_plan: Path,
    base_glb_path: Optional[Path] = None,
) -> Dict[str, Any]:
    usable = [part for part in parts if part.get("glb_path") and Path(str(part["glb_path"])).is_file()]
    base_path = Path(str(base_glb_path)) if base_glb_path else None
    if base_path and not base_path.is_file():
        base_path = None
    if not base_path and len(usable) < 2:
        raise GlbAssemblyError("Need a base GLB or at least two part GLB files to assemble")
    frame_width = max(1, int(frame_width or 0))
    frame_height = max(1, int(frame_height or 0))
    base_gltf: Optional[Dict[str, Any]] = None
    base_bin_chunk = b""
    base_bbox: Optional[Dict[str, Any]] = None
    if base_path:
        base_gltf, base_bin_chunk = _read_glb(base_path)
        base_bbox = _position_bbox(base_gltf)
    base_height = float((base_bbox or {}).get("size", [0.0, 0.0, 0.0])[1] if base_bbox else 0.0)
    if not math.isfinite(base_height) or base_height <= 0:
        base_height = 6.0
    layout = _source_layout(
        usable,
        float(frame_width),
        float(frame_height),
        object_height=base_height,
        base_center=(base_bbox or {}).get("center") if base_bbox else [0.0, 0.0, 0.0],
        base_size=(base_bbox or {}).get("size") if base_bbox else None,
    )
    combined: Dict[str, Any] = {
        "asset": {"version": "2.0", "generator": "lobster ai_3d_model automatic part assembler"},
        "scene": 0,
        "scenes": [{"name": "Automatic assembled model", "nodes": []}],
        "nodes": [],
        "buffers": [{"byteLength": 0}],
    }
    combined_bin = bytearray()
    plan_parts: List[Dict[str, Any]] = []
    plan_base: Optional[Dict[str, Any]] = None
    if base_path and base_gltf is not None and base_bbox is not None:
        plan_base = {
            "role": "base_model",
            "glb": str(base_path),
            "bbox": base_bbox,
            "matrix": [
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
            ],
        }
    ordered_usable = sorted(usable, key=lambda item: int(item.get("part_index") or 0))
    skipped_parts: List[Dict[str, Any]] = []
    overlay_parts: List[Dict[str, Any]] = []
    for part in ordered_usable:
        allow_overlay, policy_reason = _overlay_policy(part, has_base=bool(base_path))
        if allow_overlay:
            overlay_parts.append(part)
            continue
        part_index = int(part.get("part_index") or 0)
        skipped_parts.append({
            "part_index": part_index,
            "role": str(part.get("role") or f"part_{part_index:02d}"),
            "source": str(part.get("source") or part.get("glb_path") or ""),
            "glb": str(part.get("glb_path") or ""),
            "source_box": list(_safe_box(part.get("source_box"), float(frame_width), float(frame_height))),
            "reason": policy_reason,
        })

    overlay_prepared: List[Dict[str, Any]] = []
    for part in overlay_parts:
        path = Path(str(part["glb_path"]))
        gltf, bin_chunk = _read_glb(path)
        bbox = _position_bbox(gltf)
        placement = _placement(part, bbox, float(frame_width), float(frame_height), layout)
        overlay_prepared.append({
            "part": part,
            "path": path,
            "gltf": gltf,
            "bin_chunk": bin_chunk,
            "bbox": bbox,
            "placement": placement,
        })

    base_clip_metrics = {"enabled": False, "removed_triangles": 0, "total_triangles": 0, "targets": []}
    if base_path and base_gltf is not None and base_bbox is not None:
        prepared_replacements: List[Dict[str, Any]] = []
        remove_by_primitive: Dict[int, Any] = {}
        replacement_targets: List[Dict[str, Any]] = []
        failed_replacements: List[Dict[str, Any]] = []
        for item in overlay_prepared:
            part = item["part"]
            kind = _replacement_kind(part)
            if not kind:
                prepared_replacements.append(item)
                continue
            analysis = _find_base_replacement_target(
                base_gltf,
                base_bin_chunk,
                part,
                kind=kind,
                frame_width=float(frame_width),
                frame_height=float(frame_height),
                layout=layout,
            )
            public_analysis = _strip_replacement_analysis(analysis)
            part_index = int(part.get("part_index") or 0)
            role = str(part.get("role") or f"part_{part_index:02d}")
            if not analysis.get("passed"):
                failed_replacements.append({
                    "part_index": part_index,
                    "role": role,
                    "source": str(part.get("source") or part.get("glb_path") or ""),
                    "glb": str(part.get("glb_path") or ""),
                    "source_box": list(_safe_box(part.get("source_box"), float(frame_width), float(frame_height))),
                    "reason": f"base_mesh_replacement_target_failed:{analysis.get('reason') or 'unknown'}",
                    "replacement_analysis": public_analysis,
                })
                replacement_targets.append({
                    "part_index": part_index,
                    "role": role,
                    "passed": False,
                    "analysis": public_analysis,
                })
                continue
            item["placement"] = _target_guided_placement(
                part,
                item["bbox"],
                analysis,
                layout,
                float(frame_width),
                float(frame_height),
            )
            item["replacement_analysis"] = public_analysis
            np = _np()
            for primitive_index, tri_ids in (analysis.get("remove_by_primitive") or {}).items():
                if primitive_index in remove_by_primitive:
                    remove_by_primitive[primitive_index] = np.unique(np.concatenate([remove_by_primitive[primitive_index], tri_ids]))
                else:
                    remove_by_primitive[primitive_index] = np.asarray(tri_ids, dtype=np.int64)
            replacement_targets.append({
                "part_index": part_index,
                "role": role,
                "passed": True,
                "analysis": public_analysis,
            })
            prepared_replacements.append(item)
        overlay_prepared = prepared_replacements
        skipped_parts.extend(failed_replacements)
        clipped_gltf, clipped_bin_chunk, base_clip_metrics = _clip_base_mesh_by_triangles(base_gltf, base_bin_chunk, remove_by_primitive)
        base_clip_metrics["targets"] = replacement_targets
        base_clip_metrics["mode"] = "base_guided_mesh_replacement"
        _merge_part(
            combined,
            combined_bin,
            gltf=clipped_gltf,
            bin_chunk=clipped_bin_chunk,
            wrapper_name="assembled_base_model",
            wrapper_matrix=[
                1.0, 0.0, 0.0, 0.0,
                0.0, 1.0, 0.0, 0.0,
                0.0, 0.0, 1.0, 0.0,
                0.0, 0.0, 0.0, 1.0,
            ],
            wrapper_extras={
                "role": "base_model",
                "source": str(base_path),
                "assembly_anchor": [0.0, 0.0, 0.0],
                "assembly_scale": {"x": 1.0, "y": 1.0, "z": 1.0},
                "mesh_clip": base_clip_metrics,
            },
        )

    for item in overlay_prepared:
        part = item["part"]
        path = item["path"]
        gltf = item["gltf"]
        bin_chunk = item["bin_chunk"]
        bbox = item["bbox"]
        placement = item["placement"]
        part_index = int(part.get("part_index") or 0)
        role = str(part.get("role") or f"part_{part_index:02d}")
        wrapper_extras = {
            "part_index": part_index,
            "role": role,
            "source": str(part.get("source") or path.name),
            "source_box": placement["source_box"],
            "assembly_anchor": placement["anchor"],
            "assembly_scale": placement["scale"],
        }
        if isinstance(item.get("replacement_analysis"), dict):
            wrapper_extras["replacement_analysis"] = item["replacement_analysis"]
        _merge_part(
            combined,
            combined_bin,
            gltf=gltf,
            bin_chunk=bin_chunk,
            wrapper_name=f"assembled_{part_index:02d}_{role}",
            wrapper_matrix=placement["matrix"],
            wrapper_extras=wrapper_extras,
        )
        plan_parts.append({
            **wrapper_extras,
            "glb": str(path),
            "bbox": bbox,
            "target_size": placement["target_size"],
            "matrix": placement["matrix"],
        })
    final_bin = bytes(combined_bin)
    combined["buffers"] = [{"byteLength": len(final_bin)}]
    combined["extras"] = {
        "assembly_method": "base_guided_mesh_replacement",
        "assembly_version": "semantic-connected-component-replacement-v9",
        "frame_width": frame_width,
        "frame_height": frame_height,
        "source_layout": layout,
        "base_model": plan_base,
        "base_mesh_clip": base_clip_metrics,
        "part_count": len(plan_parts),
        "skipped_part_count": len(skipped_parts),
        "skipped_parts": skipped_parts,
    }
    _write_glb(dest_glb, combined, final_bin)
    plan = {
        "method": "base_guided_mesh_replacement",
        "version": "semantic-connected-component-replacement-v9",
        "frame": {"width": frame_width, "height": frame_height},
        "source_layout": layout,
        "base_model": plan_base,
        "base_mesh_clip": base_clip_metrics,
        "part_count": len(plan_parts),
        "parts": plan_parts,
        "skipped_part_count": len(skipped_parts),
        "skipped_parts": skipped_parts,
        "output_glb": str(dest_glb),
    }
    dest_plan.parent.mkdir(parents=True, exist_ok=True)
    dest_plan.write_text(json.dumps(plan, ensure_ascii=False, indent=2), encoding="utf-8")
    return {"glb_path": dest_glb, "plan_path": dest_plan, "plan": plan}
