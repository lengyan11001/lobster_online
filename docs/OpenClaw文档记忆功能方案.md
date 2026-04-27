# OpenClaw 文档记忆功能方案

> 状态：规划中 · 2026-04-23

## 背景

用户希望通过上传文档（txt/md/csv/Excel 等）给 OpenClaw agent，让 agent "记住"文档内容，后续对话中能基于文档生成内容。

### 现有参考：企微客服的"CSV 记忆"

企微客服已实现类似功能，链路如下：

```
用户在企微配置页上传 CSV
  → POST /api/wecom/upload-materials (wecom.py)
    → 解析 CSV 行（企业名称/公司介绍/产品名称/产品介绍/常用话术）
      → 写入 DB：Enterprise + Product 表
        → 客户发消息时 get_customer_service_reply() (chat.py)
          → 从 DB 取出 company_info / product_intro / common_phrases
            → 拼入 system prompt（【公司信息】【产品介绍】【常用话术】）
              → LLM 基于资料回答
```

**关键代码文件**：
- 前端：`static/js/wecom-detail.js` → `#wecomUploadMaterialsInput`
- 后端：`backend/app/api/wecom.py` → `upload_materials()`
- AI 回复：`backend/app/api/chat.py` → `get_customer_service_reply()`

## 三种实施方案

### 方案一：手动放文件（已可用，无需开发）

用户手动将 `.md` 文件放入 agent workspace 目录：

```
lobster_online/openclaw/workspace-lobster-sutui-deepseek-chat/memory/
```

OpenClaw agent 可通过 `read` 工具读取这些文件。

**优点**：零开发
**缺点**：需要用户有服务器文件访问权限，不适合非技术用户

### 方案二：后端文档导入 API + 前端上传入口（推荐）

#### 整体流程

```
用户在网页聊天附件区上传文档
  → 前端判断是文档类型（非图片/视频）
    → POST /api/openclaw/memory/import (新接口)
      → 后端读取文件内容 → 转为 Markdown
        → 保存到 openclaw/workspace-{agent}/memory/doc-{timestamp}-{filename}.md
          → 返回成功 + 文件路径
    → 前端自动在聊天中发送："我导入了文档《xxx》，请阅读并记住它的内容"
      → Agent 收到后 read 该文件 → 可选择摘要存入 MEMORY.md
```

#### 改动清单

##### 1. 前端：`lobster_online/static/js/chat.js`

**改动位置**：`#chatFileInput` 事件处理（约 2233 行）

```javascript
// 现在的 accept：
chatFileInput.accept = "image/*,video/*";

// 改为：
chatFileInput.accept = "image/*,video/*,.txt,.md,.csv,.pdf,.docx";
```

**新增逻辑**：上传时判断文件类型

```javascript
chatFileInput.addEventListener('change', function() {
  var files = chatFileInput.files;
  if (!files || !files.length) return;
  for (var i = 0; i < files.length; i++) {
    (function(file) {
      var ext = (file.name.split('.').pop() || '').toLowerCase();
      var isDoc = ['txt', 'md', 'csv', 'pdf', 'docx'].indexOf(ext) >= 0;

      if (isDoc) {
        // 文档：走记忆导入 API
        var fd = new FormData();
        fd.append('file', file);
        fetch(LOCAL_API_BASE + '/api/openclaw/memory/import', {
          method: 'POST',
          headers: { 'Authorization': 'Bearer ' + token },
          body: fd
        })
        .then(function(r) { return r.json(); })
        .then(function(d) {
          if (d && d.ok) {
            // 自动发一条消息让 agent 知道
            var autoMsg = '我导入了文档《' + file.name + '》到你的记忆目录，'
              + '文件路径：' + d.path + '。请用 read 工具读取这个文件并记住其内容。';
            // 调用现有的 sendChatMessage 逻辑发送 autoMsg
          }
        });
      } else {
        // 图片/视频：走现有 assets/upload
        // ... 现有逻辑不变
      }
    })(files[i]);
  }
  chatFileInput.value = '';
});
```

##### 2. 后端新接口：`lobster-server/backend/app/api/openclaw_memory.py`（新文件）

```python
"""OpenClaw 记忆文件导入：接收上传的文档，转为 Markdown 存入 agent workspace memory/"""

import os
import time
from pathlib import Path

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile

router = APIRouter()

# agent workspace 路径
_OPENCLAW_BASE = Path(__file__).resolve().parents[3] / "openclaw"
_DEFAULT_AGENT = "lobster-sutui-deepseek-chat"


def _get_memory_dir(agent_id: str = _DEFAULT_AGENT) -> Path:
    ws = _OPENCLAW_BASE / f"workspace-{agent_id}" / "memory"
    ws.mkdir(parents=True, exist_ok=True)
    return ws


def _to_markdown(content: bytes, filename: str) -> str:
    """根据文件扩展名将内容转为 Markdown 文本。"""
    ext = filename.rsplit(".", 1)[-1].lower() if "." in filename else ""

    if ext in ("txt", "md"):
        return content.decode("utf-8", errors="replace")

    if ext == "csv":
        import csv, io
        text = content.decode("utf-8-sig", errors="replace")
        reader = csv.reader(io.StringIO(text))
        rows = list(reader)
        if not rows:
            return "(空文件)"
        # 转为 Markdown 表格
        header = rows[0]
        lines = ["| " + " | ".join(header) + " |"]
        lines.append("| " + " | ".join(["---"] * len(header)) + " |")
        for row in rows[1:]:
            lines.append("| " + " | ".join(row) + " |")
        return "\n".join(lines)

    # .pdf / .docx 等后续扩展
    return content.decode("utf-8", errors="replace")


@router.post("/api/openclaw/memory/import", summary="上传文档到 OpenClaw 记忆目录")
async def import_memory_doc(
    file: UploadFile = File(...),
    # current_user = Depends(get_current_user),  # 按需开启鉴权
):
    if not file.filename:
        raise HTTPException(400, "缺少文件名")

    allowed_exts = {"txt", "md", "csv", "pdf", "docx"}
    ext = (file.filename.rsplit(".", 1)[-1].lower()) if "." in file.filename else ""
    if ext not in allowed_exts:
        raise HTTPException(400, f"不支持的文件格式 .{ext}，支持：{', '.join(allowed_exts)}")

    raw = await file.read()
    if len(raw) > 5 * 1024 * 1024:
        raise HTTPException(400, "文件不能超过 5MB")

    md_content = _to_markdown(raw, file.filename)

    # 文件名：doc-{timestamp}-{原始文件名}.md
    ts = int(time.time())
    safe_name = "".join(c if c.isalnum() or c in "-_." else "_" for c in file.filename)
    save_name = f"doc-{ts}-{safe_name}.md" if not safe_name.endswith(".md") else f"doc-{ts}-{safe_name}"

    memory_dir = _get_memory_dir()
    save_path = memory_dir / save_name
    save_path.write_text(
        f"# 导入文档：{file.filename}\n\n"
        f"> 导入时间：{time.strftime('%Y-%m-%d %H:%M:%S')}\n\n"
        f"{md_content}",
        encoding="utf-8"
    )

    # 返回相对于 workspace 的路径，供 agent read 使用
    rel_path = f"memory/{save_name}"
    return {"ok": True, "path": rel_path, "filename": file.filename, "size": len(md_content)}
```

##### 3. 路由注册

在 `lobster-server/backend/app/main.py` 或 `create_app.py` 中：

```python
from .api import openclaw_memory
app.include_router(openclaw_memory.router)
```

##### 4. Agent workspace 准备

创建 memory 目录（如不存在）：

```
lobster_online/openclaw/workspace-lobster-sutui-deepseek-chat/memory/
  └── .gitkeep
```

##### 5. AGENTS.md 确认

现有 `AGENTS.md` 的 Memory 章节已包含 `memory/*.md` 的读写说明，无需额外修改。确认 agent 知道：

```markdown
## Memory
- Long-term: `MEMORY.md`
- Daily notes: `memory/YYYY-MM-DD.md`
- Imported docs: `memory/doc-*.md`  ← 新增说明
```

#### 支持的文件格式

| 格式 | 处理方式 | 优先级 |
|------|---------|--------|
| `.txt` | 直接存为 Markdown | P0 首批 |
| `.md` | 直接存 | P0 首批 |
| `.csv` | 转为 Markdown 表格 | P0 首批 |
| `.pdf` | 需加 `pymupdf` 依赖提取文本 | P1 后续 |
| `.docx` | 需加 `python-docx` 依赖提取文本 | P1 后续 |
| `.xlsx` | 需加 `openpyxl` 依赖转表格 | P1 后续 |

### 方案三：对话直接投喂（已可用）

修复 system prompt 截断后，用户可以直接在网页聊天中发长文本：

```
请记住以下内容：
---
（粘贴文档内容）
---
后续我会要求你基于此内容生成xxx。
```

Agent 会使用 `edit` 工具将内容保存到 `MEMORY.md` 或 `memory/` 目录。

**可选优化**：前端支持 `.txt/.md` 文件的客户端读取，自动拼入消息：

```javascript
// 选择 .txt 或 .md 文件时，前端读取内容拼入输入框
var reader = new FileReader();
reader.onload = function(e) {
  var content = e.target.result;
  chatInput.value = '请记住以下文档内容（' + file.name + '）：\n---\n' + content + '\n---';
};
reader.readAsText(file);
```

## 推荐实施顺序

1. **方案三优化**（1 小时内）：前端加 `.txt/.md` 客户端读取拼入消息 → 立即可用
2. **方案二核心**（半天）：后端 API + 前端上传入口 → 支持更多格式
3. **方案二扩展**（按需）：PDF/DOCX/XLSX 格式支持、知识库管理界面

## 与企微客服方案的对比

| 维度 | 企微客服 | OpenClaw 对话（本方案） |
|------|---------|----------------------|
| 存储 | DB 结构化表 | 文件系统 .md |
| 格式 | 固定 CSV 表头 | 自由格式 |
| 注入方式 | 代码拼 system prompt | Agent 自主 read |
| 灵活性 | 低（固定字段） | 高（任意文档） |
| 搜索 | SQL 查询 | Agent memory_search / read |
| 容量 | 企业+产品维度 | 无限 .md 文件 |
