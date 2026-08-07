from __future__ import annotations

import html
import importlib
import io
import json
import posixpath
import re
import zipfile
from functools import lru_cache
from xml.etree import ElementTree as ET


MAX_EXTRACTED_CHARS = 500_000
TEXT_SUFFIXES = {
    ".txt", ".md", ".markdown", ".csv", ".tsv", ".json", ".jsonl",
    ".yaml", ".yml", ".html", ".htm", ".log",
}
DOCUMENT_SUFFIXES = {".pdf", ".docx", ".xlsx", ".xlsm", ".xls", ".pptx"}
SUPPORTED_SUFFIXES = TEXT_SUFFIXES | DOCUMENT_SUFFIXES
DOCUMENT_PARSER_RUNTIME_MODULES = ("pypdf", "xlrd")


@lru_cache(maxsize=1)
def _document_parser_runtime_status_cached() -> tuple[bool, tuple[str, ...]]:
    missing: list[str] = []
    for module_name in DOCUMENT_PARSER_RUNTIME_MODULES:
        try:
            importlib.import_module(module_name)
        except Exception:
            missing.append(module_name)
    return not missing, tuple(missing)


def document_parser_runtime_status(*, refresh: bool = False) -> tuple[bool, tuple[str, ...]]:
    if refresh:
        _document_parser_runtime_status_cached.cache_clear()
    return _document_parser_runtime_status_cached()


def require_document_parser_runtime(*, refresh: bool = False) -> None:
    ready, missing = document_parser_runtime_status(refresh=refresh)
    if not ready:
        raise RuntimeError(f"Online 资料解析依赖不完整，缺少：{', '.join(missing)}；请先升级或修复依赖")


def _normalize_text(text: str) -> str:
    value = html.unescape(text or "").replace("\x00", " ")
    value = re.sub(r"[ \t\r\f\v]+", " ", value)
    value = re.sub(r" *\n *", "\n", value)
    return re.sub(r"\n{4,}", "\n\n\n", value).strip()


def _limit_text(text: str) -> str:
    value = _normalize_text(text)
    if len(value) <= MAX_EXTRACTED_CHARS:
        return value
    return value[:MAX_EXTRACTED_CHARS].rstrip() + "\n\n[系统提示] 原文件文本过长，后续内容已截断。"


def _part_sort_key(name: str) -> tuple[str, int, str]:
    match = re.search(r"^(.*?)(\d+)\.xml$", name)
    return (match.group(1), int(match.group(2)), name) if match else (name, 0, name)


def _xml_text(zf: zipfile.ZipFile, name: str) -> str:
    if name not in zf.namelist():
        return ""
    root = ET.fromstring(zf.read(name))
    lines: list[str] = []
    current: list[str] = []
    for elem in root.iter():
        tag = elem.tag.rsplit("}", 1)[-1]
        if tag == "t" and elem.text:
            current.append(elem.text)
        elif tag in {"tab", "tc"}:
            current.append("\t")
        elif tag in {"br", "cr"}:
            current.append("\n")
        elif tag in {"p", "tr"} and current:
            lines.append("".join(current).strip())
            current = []
    if current:
        lines.append("".join(current).strip())
    return "\n".join(line for line in lines if line)


def _relationships(zf: zipfile.ZipFile, source_name: str) -> list[tuple[str, str, str]]:
    source_dir = posixpath.dirname(source_name)
    rels_name = posixpath.join(source_dir, "_rels", posixpath.basename(source_name) + ".rels")
    if rels_name not in zf.namelist():
        return []
    root = ET.fromstring(zf.read(rels_name))
    result: list[tuple[str, str, str]] = []
    for rel in root:
        rel_id = str(rel.attrib.get("Id") or "").strip()
        rel_type = str(rel.attrib.get("Type") or "").strip()
        target = str(rel.attrib.get("Target") or "").strip()
        if not rel_id or not target or rel.attrib.get("TargetMode") == "External":
            continue
        resolved = (
            posixpath.normpath(target.lstrip("/"))
            if target.startswith("/")
            else posixpath.normpath(posixpath.join(source_dir, target))
        )
        result.append((rel_id, rel_type, resolved))
    return result


def _extract_docx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        names = ["word/document.xml"]
        names.extend(
            sorted(
                (
                    name for name in zf.namelist()
                    if re.match(r"word/(header|footer)\d+\.xml$", name)
                    or name in {"word/footnotes.xml", "word/endnotes.xml", "word/comments.xml"}
                ),
                key=_part_sort_key,
            )
        )
        return "\n\n".join(filter(None, (_xml_text(zf, name) for name in names)))


def _presentation_slides(zf: zipfile.ZipFile) -> list[str]:
    presentation = "ppt/presentation.xml"
    if presentation not in zf.namelist():
        return sorted(
            (name for name in zf.namelist() if re.match(r"ppt/slides/slide\d+\.xml$", name)),
            key=_part_sort_key,
        )
    rel_map = {
        rel_id: target
        for rel_id, rel_type, target in _relationships(zf, presentation)
        if rel_type.endswith("/slide")
    }
    root = ET.fromstring(zf.read(presentation))
    rel_attr = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id"
    slides: list[str] = []
    for elem in root.iter():
        if elem.tag.rsplit("}", 1)[-1] != "sldId":
            continue
        target = rel_map.get(str(elem.attrib.get(rel_attr) or ""))
        if target and target in zf.namelist() and target not in slides:
            slides.append(target)
    return slides


def _extract_pptx(data: bytes) -> str:
    pages: list[str] = []
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        for index, name in enumerate(_presentation_slides(zf), start=1):
            sections = [value for value in [_xml_text(zf, name)] if value]
            for _rel_id, rel_type, target in _relationships(zf, name):
                if rel_type.endswith(("/notesSlide", "/chart", "/diagramData")):
                    extra = _xml_text(zf, target)
                    if extra:
                        label = "备注" if rel_type.endswith("/notesSlide") else "图表/图示"
                        sections.append(f"【{label}】\n{extra}")
            if sections:
                pages.append(f"## 第 {index} 页\n" + "\n".join(sections))
    return "\n\n".join(pages)


def _extract_xlsx(data: bytes) -> str:
    with zipfile.ZipFile(io.BytesIO(data)) as zf:
        shared: list[str] = []
        if "xl/sharedStrings.xml" in zf.namelist():
            root = ET.fromstring(zf.read("xl/sharedStrings.xml"))
            for item in root:
                shared.append("".join(elem.text or "" for elem in item.iter() if elem.tag.rsplit("}", 1)[-1] == "t"))
        sheets: list[str] = []
        paths = sorted(
            (name for name in zf.namelist() if re.match(r"xl/worksheets/sheet\d+\.xml$", name)),
            key=_part_sort_key,
        )
        for index, name in enumerate(paths, start=1):
            root = ET.fromstring(zf.read(name))
            rows: list[str] = []
            for row in root.iter():
                if row.tag.rsplit("}", 1)[-1] != "row":
                    continue
                values: list[str] = []
                for cell in row:
                    if cell.tag.rsplit("}", 1)[-1] != "c":
                        continue
                    cell_type = cell.attrib.get("t", "")
                    if cell_type == "inlineStr":
                        value = "".join(elem.text or "" for elem in cell.iter() if elem.tag.rsplit("}", 1)[-1] == "t")
                    else:
                        node = next((elem for elem in cell if elem.tag.rsplit("}", 1)[-1] == "v"), None)
                        value = (node.text or "") if node is not None else ""
                        if cell_type == "s":
                            try:
                                value = shared[int(value)]
                            except (ValueError, IndexError):
                                pass
                    values.append(str(value).strip())
                if any(values):
                    rows.append("\t".join(values).rstrip())
            if rows:
                sheets.append(f"## Sheet{index}\n" + "\n".join(rows))
        return "\n\n".join(sheets)


def _extract_xls(data: bytes) -> str:
    try:
        import xlrd  # type: ignore
    except Exception as exc:
        raise RuntimeError("读取 .xls 需要安装 xlrd，建议另存为 .xlsx 后上传") from exc
    book = xlrd.open_workbook(file_contents=data)
    sections: list[str] = []
    for sheet in book.sheets():
        rows = [
            "\t".join(str(sheet.cell_value(row, col)).strip() for col in range(sheet.ncols)).rstrip()
            for row in range(sheet.nrows)
        ]
        rows = [row for row in rows if row]
        if rows:
            sections.append(f"## {sheet.name}\n" + "\n".join(rows))
    return "\n\n".join(sections)


def _extract_pdf(data: bytes) -> str:
    try:
        from pypdf import PdfReader  # type: ignore
    except Exception as exc:
        raise RuntimeError("读取 PDF 需要安装 pypdf") from exc
    try:
        reader = PdfReader(io.BytesIO(data))
        return "\n\n".join(
            f"## Page {index}\n{text}"
            for index, page in enumerate(reader.pages, start=1)
            if (text := page.extract_text() or "").strip()
        )
    except Exception as exc:
        raise RuntimeError(f"PDF 文本抽取失败：{exc}") from exc


def extract_document_text(data: bytes, filename: str) -> str:
    suffix = "." + filename.rsplit(".", 1)[-1].lower() if "." in filename else ""
    if suffix not in SUPPORTED_SUFFIXES:
        raise RuntimeError("不支持的资料格式")
    if suffix in TEXT_SUFFIXES:
        text = ""
        for encoding in ("utf-8-sig", "utf-8", "gb18030"):
            try:
                text = data.decode(encoding)
                break
            except UnicodeDecodeError:
                continue
        if suffix == ".json" and text:
            try:
                text = json.dumps(json.loads(text), ensure_ascii=False, indent=2)
            except Exception:
                pass
        elif suffix in {".html", ".htm"}:
            text = re.sub(r"(?is)<(script|style).*?>.*?</\1>", "", text)
            text = re.sub(r"(?s)<[^>]+>", " ", text)
    elif suffix == ".pdf":
        text = _extract_pdf(data)
    elif suffix == ".docx":
        text = _extract_docx(data)
    elif suffix in {".xlsx", ".xlsm"}:
        text = _extract_xlsx(data)
    elif suffix == ".xls":
        text = _extract_xls(data)
    else:
        text = _extract_pptx(data)
    text = _limit_text(text)
    if not text:
        raise RuntimeError("文件没有可写入记忆库的文本内容")
    return text
