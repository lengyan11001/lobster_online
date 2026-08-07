from pathlib import Path

from backend.app.services import runtime_dependency_repair
from backend.app.services import sam3d_runtime
from scripts import check_client_code_update as updater


ROOT = Path(__file__).resolve().parent


def test_sam3d_dependencies_are_not_general_runtime_requirements():
    requirements = (ROOT / "requirements.txt").read_text(encoding="utf-8")

    for package in ("torch", "torchvision", "opencv-python", "segment-anything"):
        assert package not in requirements
    assert all(group.get("name") != "sam3d_runtime" for group in updater.RUNTIME_DEPENDENCY_GROUPS)
    assert all(group[0] != "sam3d" for group in runtime_dependency_repair._IMPORT_GROUPS)


def test_sam3d_runtime_status_lists_each_optional_dependency(monkeypatch):
    monkeypatch.setattr(sam3d_runtime, "_module_available", lambda _name: False)
    monkeypatch.setattr(sam3d_runtime, "_package_version", lambda _name: "")
    monkeypatch.setattr(sam3d_runtime, "_INSTALL_THREAD", None)
    monkeypatch.setitem(sam3d_runtime._STATE, "status", "idle")

    status = sam3d_runtime.sam3d_runtime_status()

    assert status["ready"] is False
    assert status["installing"] is False
    assert [item["package"] for item in status["dependencies"]] == [
        "torch",
        "torchvision",
        "opencv-python",
        "segment-anything",
    ]


def test_ai3d_page_exposes_runtime_install_flow():
    html = (ROOT / "static" / "views" / "ai-3d-model.html").read_text(encoding="utf-8")
    script = (ROOT / "static" / "js" / "ai-3d-model.js").read_text(encoding="utf-8")
    api = (ROOT / "backend" / "app" / "api" / "ai_3d_model.py").read_text(encoding="utf-8")

    assert 'id="ai3dInstallRuntimeBtn"' in html
    assert "/api/ai-3d-model/runtime/install" in script
    assert "mode === 'component_split_v3' && !state.runtimeReady" in script
    assert '@router.post("/api/ai-3d-model/runtime/install")' in api
    assert "workflow_mode == \"component_split_v3\"" in api
