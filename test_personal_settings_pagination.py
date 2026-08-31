from pathlib import Path


ROOT = Path(__file__).resolve().parent


def test_uploaded_and_memory_lists_bind_pagination_after_each_render():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "function bindPsListPagers()" in script
    assert "root.querySelectorAll('[data-ps-list-page]')" in script
    assert "data-ps-list-page-bound" in script
    assert "ev.stopPropagation();" in script
    assert "psListPages[id] = Math.max(1, (psListPages[id] || 1) + delta);" in script
    assert "bindPsListPagers();" in script
    assert "document.addEventListener('click'" not in script


def test_saved_template_render_does_not_reference_generated_doc_box():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")
    saved_templates = script.split("function renderSavedTemplates()", 1)[1].split(
        "function parseHostSaveResult", 1
    )[0]
    generated_docs = script.split("function renderGeneratedDocs()", 1)[1].split(
        "function fetchMemoryContent", 1
    )[0]

    assert "box.querySelectorAll('[data-download-generated-doc]')" not in saved_templates
    assert "box.querySelectorAll('[data-download-generated-doc]')" in generated_docs


def test_template_multi_select_can_select_all_rows_across_pages():
    script = (ROOT / "static" / "js" / "personal-settings.js").read_text(encoding="utf-8")

    assert "data-ps-select-all" in script
    assert "var allSelected = !!rows.length && rows.every" in script
    assert "rows.forEach(function(row)" in script
    assert "selected[id] = !allSelected" in script
    assert "updatePsMultiSelectSummary(el, rows, idFn, titleFn, selected)" in script
