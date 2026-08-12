from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]


def test_local_bestseller_uses_blob_preview_and_image_fallbacks():
    script = (ROOT / "static" / "js" / "local-bestseller.js").read_text(encoding="utf-8")

    assert "function mediaPreviewUrl" in script
    assert "function imageWithFallbackHtml" in script
    assert "URL.createObjectURL(file)" in script
    assert "lb-image-fallback" in script


def test_seedance_segment_ui_does_not_render_unprotected_broken_images():
    script = (ROOT / "static" / "js" / "comfly-seedance-tvc-studio.js").read_text(encoding="utf-8")

    assert "function seedanceImageWithFallbackHtml" in script
    assert "function generatedSegmentImageUrl" in script
    assert "reference_image_url" in script
    assert "seedance-media-fallback" in script
