from __future__ import annotations

import json
import os
import uuid
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from xml.sax.saxutils import escape


class Model3MFError(RuntimeError):
    pass


_MESHFIX_MAX_BOUNDARY_EDGES = 12000
_MESHFIX_MAX_TRIANGLES = 180000
_PRINT_TARGET_MAX_DIMENSION_MM = float(os.environ.get("AI3D_3MF_TARGET_MAX_MM") or "120")
_PRINT_MAX_SAFE_DIMENSION_MM = float(os.environ.get("AI3D_3MF_MAX_SAFE_MM") or "180")
_PRINT_MIN_REASONABLE_DIMENSION_MM = float(os.environ.get("AI3D_3MF_MIN_REASONABLE_MM") or "20")
_PRINT_LAYER_CHECK_STEP_MM = float(os.environ.get("AI3D_3MF_LAYER_CHECK_STEP_MM") or "1.0")
_PRINT_MIN_SLICE_AREA_MM2 = float(os.environ.get("AI3D_3MF_MIN_SLICE_AREA_MM2") or "0.45")
_PRINT_MIN_THIN_BAND_HEIGHT_MM = float(os.environ.get("AI3D_3MF_MIN_THIN_BAND_HEIGHT_MM") or "2.0")


def _import_trimesh():
    try:
        import numpy as np  # type: ignore
        import trimesh  # type: ignore
    except Exception as exc:  # pragma: no cover - depends on runtime package install
        raise Model3MFError("3MF 导出依赖 trimesh 未安装") from exc
    return trimesh, np


def _import_pymeshfix():
    try:
        import pymeshfix  # type: ignore
    except Exception:
        return None
    return pymeshfix


def is_available() -> bool:
    try:
        _import_trimesh()
        return True
    except Exception:
        return False


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _load_mesh(source_path: Path):
    trimesh, _np = _import_trimesh()
    loaded = trimesh.load(source_path, force="scene", process=True)
    if isinstance(loaded, trimesh.Scene):
        if not loaded.geometry:
            raise Model3MFError("模型场景里没有可导出的网格")
        mesh = loaded.dump(concatenate=True)
    else:
        mesh = loaded
    if not isinstance(mesh, trimesh.Trimesh):
        raise Model3MFError("无法把模型转换为三角网格")
    return mesh


def _load_scene_mesh(source_path: Path, *, process: bool = False):
    trimesh, _np = _import_trimesh()
    loaded = trimesh.load(source_path, force="scene", process=process)
    if isinstance(loaded, trimesh.Scene):
        if not loaded.geometry:
            raise Model3MFError("模型场景里没有可导出的网格")
        mesh = loaded.dump(concatenate=True)
    else:
        mesh = loaded
    if not isinstance(mesh, trimesh.Trimesh):
        raise Model3MFError("无法把模型转换为三角网格")
    return mesh


def _edge_stats(mesh: Any) -> Tuple[int, int]:
    _trimesh, np = _import_trimesh()
    if len(mesh.faces) <= 0:
        return 0, 0
    edges = mesh.edges_sorted
    if edges is None or len(edges) <= 0:
        return 0, 0
    _unique, counts = np.unique(edges, axis=0, return_counts=True)
    boundary_edges = int((counts == 1).sum())
    non_manifold_edges = int((counts > 2).sum())
    return boundary_edges, non_manifold_edges


def _component_count(mesh: Any) -> int:
    try:
        return int(len(mesh.split(only_watertight=False)))
    except Exception:
        return 0


def _mesh_report(mesh: Any) -> Dict[str, Any]:
    _trimesh, np = _import_trimesh()
    vertices = getattr(mesh, "vertices", np.array([]))
    faces = getattr(mesh, "faces", np.array([]))
    vertex_count = int(len(vertices))
    triangle_count = int(len(faces))
    finite_vertices = bool(vertex_count > 0 and np.isfinite(vertices).all())
    extents_value = getattr(mesh, "extents", None)
    extents = [float(value) for value in (extents_value.tolist() if extents_value is not None else [])]
    bounds = getattr(mesh, "bounds", None)
    bounds_list = bounds.tolist() if bounds is not None and len(bounds) else None
    boundary_edges, non_manifold_edges = _edge_stats(mesh)
    is_watertight = bool(getattr(mesh, "is_watertight", False))
    volume: Optional[float] = None
    if is_watertight:
        try:
            volume = float(abs(mesh.volume))
        except Exception:
            volume = None
    return {
        "vertex_count": vertex_count,
        "triangle_count": triangle_count,
        "component_count": _component_count(mesh),
        "finite_vertices": finite_vertices,
        "bounds": bounds_list,
        "extents": extents,
        "is_watertight": is_watertight,
        "boundary_edge_count": boundary_edges,
        "non_manifold_edge_count": non_manifold_edges,
        "volume": volume,
    }


def _repair_for_print(mesh: Any) -> Any:
    trimesh, _np = _import_trimesh()
    repaired = mesh.copy()
    try:
        repaired.process(validate=True)
    except Exception:
        pass
    try:
        repaired.remove_unreferenced_vertices()
    except Exception:
        pass
    try:
        repaired.merge_vertices()
    except Exception:
        pass
    try:
        trimesh.repair.fix_normals(repaired)
    except Exception:
        pass
    try:
        trimesh.repair.fill_holes(repaired)
    except Exception:
        pass
    try:
        repaired.process(validate=True)
    except Exception:
        pass
    report = _mesh_report(repaired)
    if report.get("is_watertight"):
        return repaired
    boundary_edges = int(report.get("boundary_edge_count") or 0)
    triangle_count = int(report.get("triangle_count") or 0)
    pymeshfix = _import_pymeshfix()
    if (
        pymeshfix is not None
        and 0 < boundary_edges <= _MESHFIX_MAX_BOUNDARY_EDGES
        and 0 < triangle_count <= _MESHFIX_MAX_TRIANGLES
    ):
        try:
            fixer = pymeshfix.MeshFix(repaired.vertices, repaired.faces)
            fixer.repair(joincomp=True, remove_smallest_components=False)
            vertices = getattr(fixer, "v", None)
            faces = getattr(fixer, "f", None)
            if vertices is None:
                vertices = fixer.points
            if faces is None:
                faces = fixer.faces
            fixed = trimesh.Trimesh(vertices=vertices, faces=faces, process=True)
            if len(fixed.vertices) and len(fixed.faces):
                return fixed
        except Exception:
            pass
    return repaired


def _blocking_issues(report: Dict[str, Any]) -> List[str]:
    issues: List[str] = []
    if int(report.get("vertex_count") or 0) < 8:
        issues.append("顶点数过低，模型不是有效 3D 网格")
    if int(report.get("triangle_count") or 0) < 12:
        issues.append("三角面数过低，模型不是有效 3D 网格")
    if not report.get("finite_vertices"):
        issues.append("顶点坐标包含 NaN/Inf，模型几何无效")
    extents = [float(value) for value in (report.get("extents") or [])]
    if len(extents) < 3 or max(extents or [0.0]) <= 0:
        issues.append("模型包围盒尺寸无效")
    elif min(extents) <= max(extents) * 0.0001:
        issues.append("模型厚度接近 0，不适合作为 3MF 制造格式")
    if not report.get("is_watertight"):
        boundary = int(report.get("boundary_edge_count") or 0)
        non_manifold = int(report.get("non_manifold_edge_count") or 0)
        issues.append(f"模型不是封闭网格，边界边={boundary}，非流形边={non_manifold}")
    volume = report.get("volume")
    if report.get("is_watertight") and (volume is None or float(volume or 0) <= 0):
        issues.append("封闭网格体积无效，可能存在法线或自交问题")
    return issues


def _normalize_for_3mf_print(mesh: Any) -> Tuple[Any, Dict[str, Any]]:
    normalized = mesh.copy()
    report = _mesh_report(normalized)
    extents = [float(value) for value in (report.get("extents") or [])]
    max_extent = max(extents or [0.0])
    scale = 1.0
    reason = "kept_source_scale"
    if max_extent > 0:
        if max_extent > _PRINT_MAX_SAFE_DIMENSION_MM:
            scale = _PRINT_TARGET_MAX_DIMENSION_MM / max_extent
            reason = "scaled_down_to_print_size"
        elif max_extent < _PRINT_MIN_REASONABLE_DIMENSION_MM:
            scale = _PRINT_TARGET_MAX_DIMENSION_MM / max_extent
            reason = "scaled_up_from_unitless_model"
    if scale != 1.0:
        normalized.apply_scale(scale)
    try:
        bounds = normalized.bounds
        if bounds is not None and len(bounds) == 2:
            normalized.apply_translation([-float(bounds[0][0]), -float(bounds[0][1]), -float(bounds[0][2])])
    except Exception:
        pass
    normalized_report = _mesh_report(normalized)
    return normalized, {
        "target_max_dimension_mm": _PRINT_TARGET_MAX_DIMENSION_MM,
        "max_safe_dimension_mm": _PRINT_MAX_SAFE_DIMENSION_MM,
        "min_reasonable_dimension_mm": _PRINT_MIN_REASONABLE_DIMENSION_MM,
        "source_max_extent": max_extent,
        "scale": scale,
        "reason": reason,
        "translated_min_to_origin": True,
        "print_mesh": normalized_report,
    }


def _scale_print_mesh_to_max_dimension(mesh: Any, target_max_dimension_mm: float) -> Tuple[Any, Dict[str, Any]]:
    scaled = mesh.copy()
    report = _mesh_report(scaled)
    extents = [float(value) for value in (report.get("extents") or [])]
    max_extent = max(extents or [0.0])
    scale = 1.0
    if max_extent > 0:
        scale = float(target_max_dimension_mm) / max_extent
        scaled.apply_scale(scale)
    try:
        bounds = scaled.bounds
        if bounds is not None and len(bounds) == 2:
            scaled.apply_translation([-float(bounds[0][0]), -float(bounds[0][1]), -float(bounds[0][2])])
    except Exception:
        pass
    return scaled, {
        "target_max_dimension_mm": float(target_max_dimension_mm),
        "source_max_extent": max_extent,
        "scale": scale,
        "reason": "scaled_up_for_printability",
        "translated_min_to_origin": True,
        "print_mesh": _mesh_report(scaled),
    }


def _section_area_mm2(mesh: Any, z: float) -> Tuple[float, int, float]:
    section = mesh.section(plane_origin=[0, 0, float(z)], plane_normal=[0, 0, 1])
    if section is None:
        return 0.0, 0, 0.0
    length = 0.0
    try:
        length = float(section.length)
    except Exception:
        pass
    try:
        if hasattr(section, "to_2D"):
            path_2d, _transform = section.to_2D()
        else:
            path_2d, _transform = section.to_planar()
        polygons = list(path_2d.polygons_full)
        area = float(sum(float(poly.area) for poly in polygons))
        return area, len(polygons), length
    except Exception:
        return 0.0, 0, length


def _thin_slice_bands(samples: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    bands: List[Dict[str, Any]] = []
    current: List[Dict[str, Any]] = []

    def flush() -> None:
        nonlocal current
        if not current:
            return
        height = float(current[-1]["z"] - current[0]["z"] + _PRINT_LAYER_CHECK_STEP_MM)
        if height >= _PRINT_MIN_THIN_BAND_HEIGHT_MM:
            areas = [float(item.get("area_mm2") or 0.0) for item in current]
            bands.append({
                "z_start": current[0]["z"],
                "z_end": current[-1]["z"],
                "height_mm": height,
                "min_area_mm2": min(areas) if areas else 0.0,
                "max_area_mm2": max(areas) if areas else 0.0,
                "sample_count": len(current),
            })
        current = []

    for sample in samples:
        area = float(sample.get("area_mm2") or 0.0)
        if 0.0 < area < _PRINT_MIN_SLICE_AREA_MM2:
            current.append(sample)
        else:
            flush()
    flush()
    return bands


def _slice_printability_report(mesh: Any) -> Dict[str, Any]:
    report = _mesh_report(mesh)
    extents = [float(value) for value in (report.get("extents") or [])]
    z_max = float(extents[2]) if len(extents) >= 3 else 0.0
    step = max(0.2, float(_PRINT_LAYER_CHECK_STEP_MM or 1.0))
    samples: List[Dict[str, Any]] = []
    if z_max <= 0:
        return {
            "checked": False,
            "passed": False,
            "reason": "invalid_z_extent",
            "issues": ["模型高度无效，无法做切片可打印性检查"],
        }
    z = step
    # Skip the first layer and sample at coarse layer intervals. This catches
    # long thin/island bands without turning export into a full slicer.
    while z < z_max - step:
        area, polygon_count, length = _section_area_mm2(mesh, z)
        samples.append({
            "z": round(float(z), 4),
            "area_mm2": round(float(area), 6),
            "polygon_count": int(polygon_count),
            "section_length_mm": round(float(length), 6),
        })
        z += step
    thin_bands = _thin_slice_bands(samples)
    issues: List[str] = []
    if thin_bands:
        first = thin_bands[0]
        issues.append(
            "模型存在连续薄层/疑似空层："
            f"z={first['z_start']}-{first['z_end']}mm，"
            f"最小截面积={first['min_area_mm2']}mm^2；切片器可能提示浮空或无法打印。"
        )
    return {
        "checked": True,
        "passed": not issues,
        "layer_step_mm": step,
        "min_slice_area_mm2": _PRINT_MIN_SLICE_AREA_MM2,
        "min_thin_band_height_mm": _PRINT_MIN_THIN_BAND_HEIGHT_MM,
        "thin_bands": thin_bands[:12],
        "thin_band_count": len(thin_bands),
        "sample_count": len(samples),
        "issues": issues,
    }


def _texture_face_colors_from_reference(reference_path: Path) -> Tuple[Optional[Any], Dict[str, Any]]:
    trimesh, np = _import_trimesh()
    if not reference_path or not reference_path.is_file():
        return None, {"available": False, "reason": "missing_texture_reference"}
    try:
        mesh = _load_scene_mesh(reference_path, process=False)
        visual = getattr(mesh, "visual", None)
        uv = getattr(visual, "uv", None)
        material = getattr(visual, "material", None)
        image = getattr(material, "baseColorTexture", None) or getattr(material, "image", None)
        if uv is None or image is None:
            return None, {"available": False, "reason": "reference_has_no_texture_uv"}
        image = image.convert("RGBA")
        tex = np.asarray(image, dtype=np.uint8)
        faces = getattr(mesh, "faces", None)
        vertices = getattr(mesh, "vertices", None)
        if faces is None or vertices is None or len(faces) <= 0:
            return None, {"available": False, "reason": "reference_has_no_faces"}
        face_uv = uv[faces].mean(axis=1)
        u = np.clip(face_uv[:, 0], 0.0, 1.0)
        v = np.clip(face_uv[:, 1], 0.0, 1.0)
        px = np.clip((u * (image.width - 1)).round().astype(int), 0, image.width - 1)
        py = np.clip(((1.0 - v) * (image.height - 1)).round().astype(int), 0, image.height - 1)
        colors = tex[py, px, :4]
        centroids = vertices[faces].mean(axis=1)
        return (centroids, colors), {
            "available": True,
            "reference": str(reference_path),
            "method": "uv_texture_face_centroid_sampling",
            "texture_size": [image.width, image.height],
            "reference_face_count": int(len(faces)),
        }
    except Exception as exc:
        return None, {"available": False, "reason": str(exc)}


def _map_reference_colors_to_mesh(print_mesh: Any, reference_path: Optional[Path]) -> Tuple[Optional[Any], Dict[str, Any]]:
    _trimesh, np = _import_trimesh()
    if not reference_path:
        return None, {"available": False, "reason": "missing_texture_reference"}
    reference, meta = _texture_face_colors_from_reference(reference_path)
    if reference is None:
        return None, meta
    ref_centroids, ref_colors = reference
    try:
        from scipy.spatial import cKDTree  # type: ignore
    except Exception as exc:
        return None, {"available": False, "reason": f"scipy cKDTree unavailable: {exc}"}
    faces = getattr(print_mesh, "faces", None)
    vertices = getattr(print_mesh, "vertices", None)
    if faces is None or vertices is None or len(faces) <= 0:
        return None, {"available": False, "reason": "print_mesh_has_no_faces"}
    target_centroids = vertices[faces].mean(axis=1)
    ref_min = ref_centroids.min(axis=0)
    ref_span = ref_centroids.max(axis=0) - ref_min
    tgt_min = target_centroids.min(axis=0)
    tgt_span = target_centroids.max(axis=0) - tgt_min
    ref_norm = (ref_centroids - ref_min) / np.where(ref_span == 0, 1.0, ref_span)
    tgt_norm = (target_centroids - tgt_min) / np.where(tgt_span == 0, 1.0, tgt_span)
    tree = cKDTree(ref_norm)
    dist, idx = tree.query(tgt_norm, k=1)
    colors = ref_colors[idx].astype(np.uint8)
    meta.update({
        "mapped": True,
        "target_face_count": int(len(faces)),
        "unique_color_count": int(len({tuple(int(v) for v in color[:4]) for color in colors[::max(1, len(colors)//5000)]})),
        "nearest_distance_mean": float(np.mean(dist)) if len(dist) else 0.0,
        "nearest_distance_max": float(np.max(dist)) if len(dist) else 0.0,
    })
    return colors, meta


def _write_colored_3mf(mesh: Any, dest_3mf: Path, face_colors: Any, *, object_name: str = "geometry_0") -> Dict[str, Any]:
    _trimesh, np = _import_trimesh()
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=int)
    colors = np.asarray(face_colors, dtype=np.uint8)
    if len(colors) != len(faces):
        raise Model3MFError("颜色数量与三角面数量不一致")
    color_to_id: Dict[Tuple[int, int, int, int], int] = {}
    color_ids: List[int] = []
    for color in colors:
        rgba = tuple(int(v) for v in color[:4])
        if rgba not in color_to_id:
            color_to_id[rgba] = len(color_to_id)
        color_ids.append(color_to_id[rgba])
    id_to_color = [None] * len(color_to_id)
    for rgba, idx in color_to_id.items():
        id_to_color[idx] = rgba

    def color_hex(rgba: Tuple[int, int, int, int]) -> str:
        r, g, b, a = rgba
        return f"#{r:02X}{g:02X}{b:02X}{a:02X}"

    model_uuid = str(uuid.uuid4())
    model_lines: List[str] = [
        "<?xml version='1.0' encoding='utf-8'?>",
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:m="http://schemas.microsoft.com/3dmanufacturing/material/2015/02" '
        'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" unit="millimeter">',
        "<resources>",
        '<m:colorgroup id="2">',
    ]
    for rgba in id_to_color:
        model_lines.append(f'<m:color color="{color_hex(rgba)}" />')
    model_lines.extend([
        "</m:colorgroup>",
        f'<object id="1" name="{escape(object_name)}" type="model" p:UUID="{model_uuid}">',
        "<mesh>",
        "<vertices>",
    ])
    for vertex in vertices:
        model_lines.append(f'<vertex x="{float(vertex[0])}" y="{float(vertex[1])}" z="{float(vertex[2])}" />')
    model_lines.extend(["</vertices>", "<triangles>"])
    for face, color_id in zip(faces, color_ids):
        model_lines.append(
            f'<triangle v1="{int(face[0])}" v2="{int(face[1])}" v3="{int(face[2])}" pid="2" p1="{int(color_id)}" />'
        )
    model_lines.extend([
        "</triangles>",
        "</mesh>",
        "</object>",
        "</resources>",
        "<build>",
        '<item objectid="1" />',
        "</build>",
        "</model>",
    ])
    rels = (
        "<?xml version='1.0' encoding='utf-8'?>"
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" />'
        "</Relationships>"
    )
    content_types = (
        "<?xml version='1.0' encoding='utf-8'?>"
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />'
        "</Types>"
    )
    dest_3mf.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_3mf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
        zf.writestr("3D/3dmodel.model", "\n".join(model_lines).encode("utf-8"))
        zf.writestr("_rels/.rels", rels.encode("utf-8"))
        zf.writestr("[Content_Types].xml", content_types.encode("utf-8"))
    return {
        "colored": True,
        "color_group_count": 1,
        "unique_color_count": len(id_to_color),
    }


def _write_single_material_3mf(
    mesh: Any,
    dest_3mf: Path,
    *,
    object_name: str = "geometry_0",
    material_name: str = "PLA",
    display_color: str = "#D9D0B5FF",
) -> Dict[str, Any]:
    _trimesh, np = _import_trimesh()
    vertices = np.asarray(mesh.vertices, dtype=float)
    faces = np.asarray(mesh.faces, dtype=int)
    model_uuid = str(uuid.uuid4())
    build_uuid = str(uuid.uuid4())
    item_uuid = str(uuid.uuid4())
    model_lines: List[str] = [
        "<?xml version='1.0' encoding='utf-8'?>",
        '<model xmlns="http://schemas.microsoft.com/3dmanufacturing/core/2015/02" '
        'xmlns:p="http://schemas.microsoft.com/3dmanufacturing/production/2015/06" unit="millimeter">',
        "<resources>",
        '<basematerials id="2">',
        f'<base name="{escape(material_name)}" displaycolor="{display_color}" />',
        "</basematerials>",
        f'<object id="1" name="{escape(object_name)}" type="model" pid="2" pindex="0" p:UUID="{model_uuid}">',
        "<mesh>",
        "<vertices>",
    ]
    for vertex in vertices:
        model_lines.append(f'<vertex x="{float(vertex[0])}" y="{float(vertex[1])}" z="{float(vertex[2])}" />')
    model_lines.extend(["</vertices>", "<triangles>"])
    for face in faces:
        model_lines.append(f'<triangle v1="{int(face[0])}" v2="{int(face[1])}" v3="{int(face[2])}" />')
    model_lines.extend([
        "</triangles>",
        "</mesh>",
        "</object>",
        "</resources>",
        f'<build p:UUID="{build_uuid}">',
        f'<item objectid="1" p:UUID="{item_uuid}" />',
        "</build>",
        "</model>",
    ])
    rels = (
        "<?xml version='1.0' encoding='utf-8'?>"
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Target="/3D/3dmodel.model" Id="rel0" '
        'Type="http://schemas.microsoft.com/3dmanufacturing/2013/01/3dmodel" />'
        "</Relationships>"
    )
    content_types = (
        "<?xml version='1.0' encoding='utf-8'?>"
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml" />'
        '<Default Extension="model" ContentType="application/vnd.ms-package.3dmanufacturing-3dmodel+xml" />'
        "</Types>"
    )
    dest_3mf.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(dest_3mf, "w", compression=zipfile.ZIP_DEFLATED, compresslevel=5) as zf:
        zf.writestr("3D/3dmodel.model", "\n".join(model_lines).encode("utf-8"))
        zf.writestr("_rels/.rels", rels.encode("utf-8"))
        zf.writestr("[Content_Types].xml", content_types.encode("utf-8"))
    return {
        "single_material": True,
        "base_material_count": 1,
        "material_name": material_name,
        "display_color": display_color,
    }


def export_glb_to_3mf(
    source_glb: Path,
    dest_3mf: Path,
    *,
    report_path: Optional[Path] = None,
    label: str = "",
    texture_reference_path: Optional[Path] = None,
) -> Dict[str, Any]:
    report_path = report_path or dest_3mf.with_name(dest_3mf.name + ".check.json")
    report: Dict[str, Any] = {
        "source": str(source_glb),
        "target": str(dest_3mf),
        "label": label,
        "created_at": _utc_now(),
        "status": "failed",
        "format": "3mf",
        "issues": [],
    }
    try:
        if not source_glb.is_file() or source_glb.stat().st_size <= 0:
            raise Model3MFError("源 GLB 文件不存在或为空")
        raw_mesh = _load_mesh(source_glb)
        raw_report = _mesh_report(raw_mesh)
        repaired_mesh = _repair_for_print(raw_mesh)
        repaired_report = _mesh_report(repaired_mesh)
        issues = _blocking_issues(repaired_report)
        print_mesh = repaired_mesh
        print_normalization: Dict[str, Any] = {}
        printability: Dict[str, Any] = {}
        if not issues:
            print_mesh, print_normalization = _normalize_for_3mf_print(repaired_mesh)
            printability = _slice_printability_report(print_mesh)
            if not printability.get("passed") and _PRINT_MAX_SAFE_DIMENSION_MM > _PRINT_TARGET_MAX_DIMENSION_MM:
                scaled_mesh, scaled_normalization = _scale_print_mesh_to_max_dimension(print_mesh, _PRINT_MAX_SAFE_DIMENSION_MM)
                scaled_printability = _slice_printability_report(scaled_mesh)
                printability["retry_at_max_safe_dimension"] = {
                    "normalization": scaled_normalization,
                    "printability": scaled_printability,
                }
                if scaled_printability.get("passed"):
                    print_mesh = scaled_mesh
                    print_normalization = {
                        **print_normalization,
                        "printability_retry_used": True,
                        "retry_normalization": scaled_normalization,
                    }
                    printability = scaled_printability
            if not printability.get("passed"):
                issues.extend(str(item) for item in (printability.get("issues") or []))
        report.update({
            "source_file_size": source_glb.stat().st_size,
            "raw_mesh": raw_report,
            "checked_mesh": repaired_report,
            "print_normalization": print_normalization,
            "printability": printability,
            "issues": issues,
            "passed": not issues,
        })
        if issues:
            if dest_3mf.exists():
                dest_3mf.unlink(missing_ok=True)
            return report
        dest_3mf.parent.mkdir(parents=True, exist_ok=True)
        color_meta: Dict[str, Any] = {"available": False, "reason": "not_requested"}
        face_colors = None
        if texture_reference_path is not None:
            face_colors, color_meta = _map_reference_colors_to_mesh(print_mesh, texture_reference_path)
        if face_colors is not None:
            color_meta.update(_write_colored_3mf(print_mesh, dest_3mf, face_colors, object_name=label or dest_3mf.stem))
        else:
            color_meta.update(_write_single_material_3mf(print_mesh, dest_3mf, object_name=label or dest_3mf.stem))
        if not dest_3mf.is_file() or dest_3mf.stat().st_size <= 0:
            raise Model3MFError("3MF 导出后文件为空")
        report.update({
            "status": "exported",
            "passed": True,
            "file_size": dest_3mf.stat().st_size,
            "color_export": color_meta,
        })
        return report
    except Exception as exc:
        report.update({
            "status": "failed",
            "passed": False,
            "issues": [str(exc)],
        })
        if dest_3mf.exists():
            dest_3mf.unlink(missing_ok=True)
        return report
    finally:
        try:
            report_path.parent.mkdir(parents=True, exist_ok=True)
            report_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass
