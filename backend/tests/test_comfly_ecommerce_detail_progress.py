from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

from PIL import Image


ROOT = Path(__file__).resolve().parents[2]


def _load_pipeline_module():
    path = ROOT / "skills/comfly_ecommerce_detail/scripts/comfly_ecommerce_detail_pipeline.py"
    name = "test_comfly_ecommerce_detail_pipeline_progress"
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def test_manifest_partial_result_reads_completed_page(tmp_path: Path) -> None:
    from backend.app.services.comfly_ecommerce_detail_job_store import read_manifest_partial_result

    run_dir = tmp_path / "run_20261027_120000"
    run_dir.mkdir()
    (run_dir / "detail").mkdir()
    image_path = run_dir / "detail" / "详情_01.jpg"
    Image.new("RGB", (10, 20), "white").save(image_path)
    manifest = {
        "run_dir": str(run_dir),
        "config": {"image_model": "gpt-image-2", "page_count": 12},
        "pages": {"1": {"final": {"status": "success", "updated_at": "2026-10-27T12:00:00"}}},
    }
    (run_dir / "manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    (run_dir / "page_01_final.json").write_text(
        json.dumps({"index": 1, "local_path": str(image_path), "filename": image_path.name}),
        encoding="utf-8",
    )

    result = read_manifest_partial_result(str(tmp_path))

    assert result is not None
    assert result["partial"] is True
    assert result["page_results"][0]["index"] == 1
    assert result["page_results"][0]["local_path"] == str(image_path)


def test_ecommerce_gpt_image2_edit_request_keeps_url_response_mode(tmp_path: Path) -> None:
    mod = _load_pipeline_module()
    config = mod.PipelineConfig(
        base_url="https://bhzn.top/api/comfly-proxy",
        api_key="token",
        image_generation_retries=1,
        network_retry_delay_seconds=0,
    )
    client = mod.ComflyClient(config, mod.RunLogger(str(tmp_path), config, {}))
    ref_path = tmp_path / "ref.png"
    Image.new("RGB", (8, 8), "white").save(ref_path)
    payload = {"data": [{"url": "https://cdn.example.test/result.png"}]}
    raw = json.dumps(payload).encode("utf-8")
    captured = {}

    class _Response:
        status_code = 200
        content = raw
        text = raw.decode("utf-8")

    def _post(url, **kwargs):
        captured["url"] = url
        captured["data"] = kwargs.get("data")
        captured["files"] = kwargs.get("files")
        return _Response()

    client.session.post = _post
    result, attempts = client.generate_image("gpt-image-2", "product", "9:16", [str(ref_path)], "test")

    assert attempts == 1
    assert result["url"] == "https://cdn.example.test/result.png"
    assert captured["url"].endswith("/v1/images/edits")
    assert captured["data"]["response_format"] == "url"
    assert captured["data"]["size"] == "1080x1920"
    assert len(captured["files"]) == 1


def test_direct_gpt_image2_mode_uses_local_page_plan_without_copy_prompt() -> None:
    mod = _load_pipeline_module()
    config = mod.PipelineConfig(
        base_url="https://bhzn.top/api/comfly-proxy",
        api_key="token",
        image_model="gpt-image-2",
        detail_render_mode="gpt_image2_direct",
    )
    analysis = {
        "product_name": "测试商品",
        "category": "商品",
        "hero_claim": "核心卖点",
        "product_summary": "商品摘要",
        "visual_style": "clean ecommerce",
        "locale_profile": {"language_name": "Simplified Chinese", "platform": "ecommerce"},
    }
    page = {
        "index": 1,
        "slot": "cover",
        "title": "不应绘制的标题",
        "subtitle": "不应绘制的副标题",
        "highlights": ["不应绘制的卖点"],
        "footer": "不应绘制的页脚",
    }

    prompt = mod._compose_direct_detail_page_prompt(page, analysis, config)

    assert "Do not render titles" in prompt
    assert "Title:" not in prompt
    assert "Selling points:" not in prompt
