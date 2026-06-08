from __future__ import annotations

from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
FRONTEND = ROOT / "static" / "js" / "cutcli-template-studio.js"
CSS = ROOT / "static" / "css" / "index.css"
BACKEND = ROOT / "backend" / "app" / "api" / "cutcli_templates_local.py"


def require(text: str, needle: str, label: str) -> None:
    if needle not in text:
        raise SystemExit(f"[FAIL] missing {label}: {needle}")
    print(f"[OK] {label}")


def main() -> None:
    js = FRONTEND.read_text(encoding="utf-8")
    css = CSS.read_text(encoding="utf-8")
    py = BACKEND.read_text(encoding="utf-8")

    require(js, "function previewInlineStyle(tpl, targetKey)", "single preview style generator")
    require(js, "var layout = overlayLayout(tpl);", "preview uses template overlay layout")
    require(js, "layout === 'education_focus_bar'", "education template preview branch")
    require(js, "background: '#ffffff'", "education white prompt bar preview")
    require(js, "color: '#000000'", "education prompt bar black text preview")
    require(js, "assColorToCss(overlay.headline_color", "ASS color mapped into preview")
    require(js, "previewInlineStyle(tpl, target.key)", "preview nodes receive generated inline style")

    require(css, "container-type: inline-size;", "preview font scales against video box")
    require(css, ".cutcli-template-preview-line", "multi-line preview text line wrapper")

    require(py, "badge_center_y = int(height * _float_value(overlay_style.get(\"badge_y_ratio\")", "backend badge uses center Y override")
    require(py, "x1 = _clamp_int(badge_x - bar_w // 2", "backend education bar follows badge X")
    require(py, "text_x = _clamp_int(badge_x", "backend education text follows bar clamp")

    print("[OK] cutcli preview/render style consistency guard passed")


if __name__ == "__main__":
    main()
