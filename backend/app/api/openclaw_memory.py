"""User-uploaded OpenClaw memory documents.

The bundled OpenClaw workspace files are product defaults and may be updated by
OTA. User memory is kept separately under openclaw/user_memory/ and mirrored
into OpenClaw workspace memory folders so memory_search can see it.
"""
from __future__ import annotations

import html
import io
import hashlib
import json
import logging
import os
import re
import shutil
import tempfile
import zipfile
from datetime import datetime, timezone
from pathlib import Path
from typing import Any
from xml.etree import ElementTree as ET

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile

from .auth import _ServerUser, get_current_user_for_local

logger = logging.getLogger(__name__)
router = APIRouter()

_BASE_DIR = Path(__file__).resolve().parent.parent.parent.parent
_OPENCLAW_DIR = _BASE_DIR / "openclaw"
_USER_MEMORY_DIR = _OPENCLAW_DIR / "user_memory"
_MAX_UPLOAD_BYTES = 30 * 1024 * 1024
_MAX_EXTRACTED_CHARS = 500_000
_ALLOWED_TEXT_SUFFIXES = {
    ".txt",
    ".md",
    ".markdown",
    ".csv",
    ".tsv",
    ".json",
    ".jsonl",
    ".yaml",
    ".yml",
    ".html",
    ".htm",
    ".log",
}
_ALLOWED_DOCUMENT_SUFFIXES = {
    ".pdf",
    ".doc",
    ".docx",
    ".xls",
    ".xlsx",
    ".xlsm",
    ".ppt",
    ".pptx",
}
_SUPPORTED_SUFFIXES = _ALLOWED_TEXT_SUFFIXES | _ALLOWED_DOCUMENT_SUFFIXES
_DEFAULT_WORKSPACE_NAMES = (
    "workspace",
    "workspace-lobster-sutui-deepseek-chat",
    "workspace-lobster-sutui-gpt-4o-mini",
)


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _user_dir(user_id: int) -> Path:
    return _USER_MEMORY_DIR / f"user_{user_id}"


def _index_path(user_id: int) -> Path:
    return _user_dir(user_id) / "index.json"


def _load_index(user_id: int) -> list[dict[str, Any]]:
    p = _index_path(user_id)
    if not p.is_file():
        return []
    try:
        data = json.loads(p.read_text(encoding="utf-8"))
        docs = data.get("documents") if isinstance(data, dict) else data
        if isinstance(docs, list):
            return [d for d in docs if isinstance(d, dict)]
    except Exception as exc:
        logger.warning("[openclaw-memory] read index failed user_id=%s: %s", user_id, exc)
    return []


def _save_index(user_id: int, docs: list[dict[str, Any]]) -> None:
    p = _index_path(user_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(
        json.dumps({"documents": docs}, ensure_ascii=False, indent=2) + "\n",
        encoding="utf-8",
    )


def _normalize_extracted_text(text: str) -> str:
    text = html.unescape(text or "")
    text = text.replace("\x00", " ")
    text = re.sub(r"[ \t\r\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return text.strip()


def _limit_extracted_text(text: str) -> str:
    text = _normalize_extracted_text(text)
    if len(text) <= _MAX_EXTRACTED_CHARS:
        return text
    return (
        text[:_MAX_EXTRACTED_CHARS].rstrip()
        + f"\n\n[系统提示] 原文件抽取文本超过 {_MAX_EXTRACTED_CHARS} 字，后续内容已截断；"
        "如需完整记忆，请拆分文件后分批上传。"
    )


def _zip_xml_text(data: bytes, names: list[str]) -> str:
    parts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for name in names:
            if name not in zf.namelist():
                continue
            root = ET.fromstring(zf.read(name))
            buf: list[str] = []
            for elem in root.iter():
                tag = elem.tag.rsplit("}", 1)[-1]
                if tag == "t" and elem.text:
                    buf.append(elem.text)
                elif tag == "tab":
                    buf.append("\t")
                elif tag in {"br", "cr", "p", "tr"}:
                    buf.append("\n")
                elif tag == "tc":
                    buf.append("\t")
            chunk = "".join(buf).strip()
            if chunk:
                parts.append(chunk)
    return "\n\n".join(parts)


def _extract_docx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = ["word/document.xml"]
        names.extend(
            sorted(
                n
                for n in zf.namelist()
                if re.match(r"word/(header|footer)\d+\.xml$", n)
            )
        )
    return _zip_xml_text(data, names)


def _extract_pptx_text(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = sorted(
            n
            for n in zf.namelist()
            if re.match(r"ppt/slides/slide\d+\.xml$", n)
        )
    slide_texts: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for idx, name in enumerate(names, start=1):
            root = ET.fromstring(zf.read(name))
            texts = [
                elem.text
                for elem in root.iter()
                if elem.tag.rsplit("}", 1)[-1] == "t" and elem.text
            ]
            if texts:
                slide_texts.append(f"## Slide {idx}\n" + "\n".join(texts))
    return "\n\n".join(slide_texts)


def _xlsx_shared_strings(zf: zipfile.ZipFile) -> list[str]:
    if "xl/sharedStrings.xml" not in zf.namelist():
        return []
    root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
    out: list[str] = []
    for si in root:
        texts = [
            elem.text or ""
            for elem in si.iter()
            if elem.tag.rsplit("}", 1)[-1] == "t"
        ]
        out.append("".join(texts))
    return out


def _xlsx_sheet_names(zf: zipfile.ZipFile) -> dict[str, str]:
    if "xl/workbook.xml" not in zf.namelist() or "xl/_rels/workbook.xml.rels" not in zf.namelist():
        return {}
    rels_root = ET.fromstring(zf.read("xl/_rels/workbook.xml.rels"))
    rel_targets: dict[str, str] = {}
    for rel in rels_root:
        rid = rel.attrib.get("Id")
        target = rel.attrib.get("Target", "")
        if rid and target:
            rel_targets[rid] = "xl/" + target.lstrip("/")

    wb_root = ET.fromstring(zf.read("xl/workbook.xml"))
    names: dict[str, str] = {}
    for elem in wb_root.iter():
        if elem.tag.rsplit("}", 1)[-1] != "sheet":
            continue
        rid = elem.attrib.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")
        sheet_name = elem.attrib.get("name") or rid or "Sheet"
        target = rel_targets.get(rid or "")
        if target:
            names[target] = sheet_name
    return names


def _extract_xlsx_text(data: bytes) -> str:
    sheets: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared = _xlsx_shared_strings(zf)
        sheet_names = _xlsx_sheet_names(zf)
        worksheet_paths = sorted(
            n
            for n in zf.namelist()
            if re.match(r"xl/worksheets/sheet\d+\.xml$", n)
        )
        for idx, name in enumerate(worksheet_paths, start=1):
            root = ET.fromstring(zf.read(name))
            rows: list[str] = []
            for row in root.iter():
                if row.tag.rsplit("}", 1)[-1] != "row":
                    continue
                vals: list[str] = []
                for cell in row:
                    if cell.tag.rsplit("}", 1)[-1] != "c":
                        continue
                    cell_type = cell.attrib.get("t", "")
                    value = ""
                    if cell_type == "inlineStr":
                        texts = [
                            elem.text or ""
                            for elem in cell.iter()
                            if elem.tag.rsplit("}", 1)[-1] == "t"
                        ]
                        value = "".join(texts)
                    else:
                        v = next((elem for elem in cell if elem.tag.rsplit("}", 1)[-1] == "v"), None)
                        raw = (v.text or "") if v is not None else ""
                        if cell_type == "s":
                            try:
                                value = shared[int(raw)]
                            except Exception:
                                value = raw
                        else:
                            value = raw
                    vals.append(str(value).strip())
                if any(vals):
                    rows.append("\t".join(vals).rstrip())
            if rows:
                display_name = sheet_names.get(name, f"Sheet{idx}")
                sheets.append(f"## {display_name}\n" + "\n".join(rows))
    return "\n\n".join(sheets)


def _extract_xls_text(data: bytes) -> str:
    try:
        import xlrd  # type: ignore
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="读取 .xls 需要安装 xlrd；建议另存为 .xlsx 后上传。",
        ) from exc
    book = xlrd.open_workbook(file_contents=data)
    chunks: list[str] = []
    for sheet in book.sheets():
        rows: list[str] = []
        for r in range(sheet.nrows):
            vals = [str(sheet.cell_value(r, c)).strip() for c in range(sheet.ncols)]
            if any(vals):
                rows.append("\t".join(vals).rstrip())
        if rows:
            chunks.append(f"## {sheet.name}\n" + "\n".join(rows))
    return "\n\n".join(chunks)


def _extract_pdf_text(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="读取 PDF 需要安装 pypdf；请安装依赖后重试。",
        ) from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        pages: list[str] = []
        for idx, page in enumerate(reader.pages, start=1):
            text = page.extract_text() or ""
            if text.strip():
                pages.append(f"## Page {idx}\n{text}")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"PDF 文本抽取失败：{exc}") from exc
    return "\n\n".join(pages)


def _extract_doc_via_word_com(data: bytes, suffix: str) -> str:
    try:
        import win32com.client  # type: ignore
    except Exception as exc:
        raise HTTPException(
            status_code=400,
            detail="旧版 .doc 需要本机安装 Microsoft Word 与 pywin32；建议另存为 .docx 后上传。",
        ) from exc

    tmp_path = ""
    app = None
    doc = None
    try:
        with tempfile.NamedTemporaryFile(delete=False, suffix=suffix) as tmp:
            tmp.write(data)
            tmp_path = tmp.name
        app = win32com.client.DispatchEx("Word.Application")
        app.Visible = False
        doc = app.Documents.Open(tmp_path, ReadOnly=True, AddToRecentFiles=False, ConfirmConversions=False)
        return str(doc.Content.Text or "")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"旧版 Word 文档抽取失败：{exc}") from exc
    finally:
        try:
            if doc is not None:
                doc.Close(False)
        except Exception:
            pass
        try:
            if app is not None:
                app.Quit()
        except Exception:
            pass
        if tmp_path:
            try:
                os.unlink(tmp_path)
            except OSError:
                pass


def _decode_text_payload(data: bytes, filename: str) -> str:
    suffix = Path(filename or "").suffix.lower()
    if suffix not in _SUPPORTED_SUFFIXES:
        raise HTTPException(
            status_code=400,
            detail="当前资料记忆支持 txt/md/csv/json/yaml/html、PDF、Word(docx)、Excel(xlsx/xls)、PPT(pptx) 等文件。",
        )

    text = ""
    if suffix in _ALLOWED_TEXT_SUFFIXES:
        for enc in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = data.decode(enc)
                break
            except UnicodeDecodeError:
                continue
        if not text:
            raise HTTPException(status_code=400, detail="文件内容无法按文本解析")

        if suffix in {".json", ".jsonl"}:
            try:
                if suffix == ".json":
                    parsed = json.loads(text)
                    text = json.dumps(parsed, ensure_ascii=False, indent=2)
            except Exception:
                pass
        elif suffix in {".html", ".htm"}:
            text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
    elif suffix == ".pdf":
        text = _extract_pdf_text(data)
    elif suffix == ".docx":
        try:
            text = _extract_docx_text(data)
        except (zipfile.BadZipFile, ET.ParseError) as exc:
            raise HTTPException(status_code=400, detail=f"Word 文档解析失败：{exc}") from exc
    elif suffix == ".doc":
        text = _extract_doc_via_word_com(data, suffix)
    elif suffix in {".xlsx", ".xlsm"}:
        try:
            text = _extract_xlsx_text(data)
        except (zipfile.BadZipFile, ET.ParseError) as exc:
            raise HTTPException(status_code=400, detail=f"Excel 文档解析失败：{exc}") from exc
    elif suffix == ".xls":
        text = _extract_xls_text(data)
    elif suffix == ".pptx":
        try:
            text = _extract_pptx_text(data)
        except (zipfile.BadZipFile, ET.ParseError) as exc:
            raise HTTPException(status_code=400, detail=f"PPT 文档解析失败：{exc}") from exc
    elif suffix == ".ppt":
        raise HTTPException(
            status_code=400,
            detail="旧版 .ppt 暂不支持直接抽取；请另存为 .pptx、PDF 或文本后上传。",
        )

    text = _limit_extracted_text(text)
    if not text:
        raise HTTPException(status_code=400, detail="文件没有可写入记忆库的文本内容")
    return text


def _short_title(raw: str, fallback: str) -> str:
    title = (raw or "").strip() or (fallback or "").strip() or "用户资料"
    title = re.sub(r"[\x00-\x1f]+", " ", title)
    title = re.sub(r"\s+", " ", title).strip()
    return title[:80] or "用户资料"


def _doc_id_for(user_id: int, filename: str, sha256: str, created_at: str) -> str:
    raw = f"{user_id}\0{filename}\0{sha256}\0{created_at}".encode("utf-8", "ignore")
    return hashlib.sha256(raw).hexdigest()[:16]


def _workspace_dirs() -> list[Path]:
    names: list[str] = list(_DEFAULT_WORKSPACE_NAMES)
    try:
        for p in sorted(_OPENCLAW_DIR.glob("workspace-*")):
            if p.is_dir() and p.name not in names:
                names.append(p.name)
    except OSError:
        pass
    return [_OPENCLAW_DIR / name for name in names]


def _memory_markdown(record: dict[str, Any], text: str) -> str:
    notes = (record.get("notes") or "").strip()
    notes_block = f"\n- notes: {notes}" if notes else ""
    return (
        f"# {record.get('title') or '用户资料'}\n\n"
        "这是一份用户上传给 OpenClaw 的本机资料记忆。回答用户问题时，如果内容相关，可以参考本文；"
        "不要把本文透露给无关用户。\n\n"
        "## 元数据\n\n"
        f"- document_id: {record.get('id')}\n"
        f"- user_id: {record.get('user_id')}\n"
        f"- source_file: {record.get('filename')}\n"
        f"- uploaded_at: {record.get('created_at')}\n"
        f"{notes_block}\n\n"
        "## 内容\n\n"
        f"{text.strip()}\n"
    )


def _write_workspace_memory(record: dict[str, Any], text: str) -> list[str]:
    rel_paths: list[str] = []
    filename = f"lobster_user_{record['user_id']}_{record['id']}.md"
    body = _memory_markdown(record, text)
    for ws in _workspace_dirs():
        try:
            mem_dir = ws / "memory"
            mem_dir.mkdir(parents=True, exist_ok=True)
            out = mem_dir / filename
            out.write_text(body, encoding="utf-8")
            rel_paths.append(out.relative_to(_BASE_DIR).as_posix())
            _write_workspace_memory_index(ws)
        except Exception as exc:
            logger.warning("[openclaw-memory] mirror failed workspace=%s: %s", ws, exc)
    return rel_paths


def _write_workspace_memory_index(workspace_dir: Path) -> None:
    mem_dir = workspace_dir / "memory"
    docs = sorted(mem_dir.glob("lobster_user_*.md"))
    lines = [
        "# Lobster User Memory Index",
        "",
        "User-uploaded memory documents available to OpenClaw memory_search:",
        "",
    ]
    for doc in docs:
        lines.append(f"- memory/{doc.name}")
    try:
        (mem_dir / "LOBSTER_USER_MEMORY_INDEX.md").write_text(
            "\n".join(lines).rstrip() + "\n",
            encoding="utf-8",
        )
    except Exception as exc:
        logger.warning("[openclaw-memory] write index failed workspace=%s: %s", workspace_dir, exc)


def _remove_workspace_memory(user_id: int, doc_id: str) -> list[str]:
    removed: list[str] = []
    filename = f"lobster_user_{user_id}_{doc_id}.md"
    for ws in _workspace_dirs():
        target = ws / "memory" / filename
        try:
            if target.is_file():
                target.unlink()
                removed.append(target.relative_to(_BASE_DIR).as_posix())
            _write_workspace_memory_index(ws)
        except Exception as exc:
            logger.warning("[openclaw-memory] remove mirror failed workspace=%s: %s", ws, exc)
    return removed


@router.get("/api/openclaw/memory/list", summary="List user-uploaded OpenClaw memory docs")
async def list_openclaw_memory(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    docs = _load_index(current_user.id)
    return {"ok": True, "documents": docs}


@router.post("/api/openclaw/memory/upload", summary="Upload a document into OpenClaw memory")
async def upload_openclaw_memory(
    file: UploadFile = File(...),
    title: str = Form(""),
    notes: str = Form(""),
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    data = await file.read(_MAX_UPLOAD_BYTES + 1)
    if len(data) > _MAX_UPLOAD_BYTES:
        raise HTTPException(status_code=413, detail="文件超过 30MB，请拆分后上传")
    filename = (file.filename or "document.txt").strip() or "document.txt"
    text = _decode_text_payload(data, filename)
    sha256 = hashlib.sha256(data).hexdigest()
    created_at = _utc_now_iso()
    doc_id = _doc_id_for(current_user.id, filename, sha256, created_at)
    record = {
        "id": doc_id,
        "user_id": current_user.id,
        "title": _short_title(title, filename),
        "filename": filename,
        "notes": (notes or "").strip()[:500],
        "size": len(data),
        "sha256": sha256,
        "created_at": created_at,
    }

    user_dir = _user_dir(current_user.id)
    docs_dir = user_dir / "documents"
    docs_dir.mkdir(parents=True, exist_ok=True)
    markdown_path = docs_dir / f"{doc_id}.md"
    markdown_path.write_text(_memory_markdown(record, text), encoding="utf-8")
    record["canonical_path"] = markdown_path.relative_to(_BASE_DIR).as_posix()
    record["workspace_paths"] = _write_workspace_memory(record, text)

    docs = [d for d in _load_index(current_user.id) if d.get("id") != doc_id]
    docs.insert(0, record)
    _save_index(current_user.id, docs)
    logger.info(
        "[openclaw-memory] uploaded user_id=%s doc_id=%s filename=%s mirrors=%s",
        current_user.id,
        doc_id,
        filename,
        len(record["workspace_paths"]),
    )
    return {"ok": True, "document": record}


@router.delete("/api/openclaw/memory/clear", summary="Clear all user OpenClaw memory docs")
async def clear_openclaw_memory(
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    docs = _load_index(current_user.id)
    removed: list[str] = []
    for doc in docs:
        clean_id = re.sub(r"[^a-f0-9]", "", str(doc.get("id") or "").lower())[:32]
        if clean_id:
            removed.extend(_remove_workspace_memory(current_user.id, clean_id))

    # Also remove stale mirrored files for this user if the index was missing or incomplete.
    prefix = f"lobster_user_{current_user.id}_"
    for ws in _workspace_dirs():
        mem_dir = ws / "memory"
        try:
            if mem_dir.is_dir():
                for target in mem_dir.glob(prefix + "*.md"):
                    target.unlink()
                    removed.append(target.relative_to(_BASE_DIR).as_posix())
                _write_workspace_memory_index(ws)
        except Exception as exc:
            logger.warning("[openclaw-memory] clear stale mirrors failed workspace=%s: %s", ws, exc)

    user_dir = _user_dir(current_user.id)
    try:
        if user_dir.exists():
            resolved_root = _USER_MEMORY_DIR.resolve()
            resolved_user_dir = user_dir.resolve()
            if resolved_user_dir == resolved_root or resolved_root not in resolved_user_dir.parents:
                raise RuntimeError("refuse to remove path outside user_memory")
            shutil.rmtree(resolved_user_dir, ignore_errors=True)
    except Exception as exc:
        logger.warning("[openclaw-memory] clear user dir failed user_id=%s: %s", current_user.id, exc)
        raise HTTPException(status_code=500, detail="清除个人记忆失败")

    _save_index(current_user.id, [])
    return {"ok": True, "deleted_count": len(docs), "removed_workspace_paths": sorted(set(removed))}


@router.delete("/api/openclaw/memory/{doc_id}", summary="Delete a user OpenClaw memory doc")
async def delete_openclaw_memory(
    doc_id: str,
    current_user: _ServerUser = Depends(get_current_user_for_local),
):
    clean_id = re.sub(r"[^a-f0-9]", "", (doc_id or "").lower())[:32]
    if not clean_id:
        raise HTTPException(status_code=400, detail="document_id 无效")
    docs = _load_index(current_user.id)
    kept: list[dict[str, Any]] = []
    found: dict[str, Any] | None = None
    for doc in docs:
        if doc.get("id") == clean_id:
            found = doc
        else:
            kept.append(doc)
    if not found:
        raise HTTPException(status_code=404, detail="资料不存在")

    canon = _BASE_DIR / str(found.get("canonical_path") or "")
    try:
        if canon.is_file() and _USER_MEMORY_DIR in canon.resolve().parents:
            canon.unlink()
    except Exception as exc:
        logger.warning("[openclaw-memory] delete canonical failed doc_id=%s: %s", clean_id, exc)
    removed = _remove_workspace_memory(current_user.id, clean_id)
    _save_index(current_user.id, kept)
    return {"ok": True, "deleted": clean_id, "removed_workspace_paths": removed}
