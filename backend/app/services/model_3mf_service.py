from __future__ import annotations

import json
import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple


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


def export_glb_to_3mf(
    source_glb: Path,
    dest_3mf: Path,
    *,
    report_path: Optional[Path] = None,
    label: str = "",
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
        print_mesh.export(dest_3mf)
        if not dest_3mf.is_file() or dest_3mf.stat().st_size <= 0:
            raise Model3MFError("3MF 导出后文件为空")
        report.update({
            "status": "exported",
            "passed": True,
            "file_size": dest_3mf.stat().st_size,
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
