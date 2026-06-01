"""AI Prompt 模板

定义生成大纲和扩展内容的 prompt，强制 JSON 输出格式。
"""

OUTLINE_SYSTEM_PROMPT = """你是一位专业的演示文稿内容策划师。你的任务是根据用户给出的主题，生成结构清晰、内容专业的 PPT 大纲。

要求：
1. 每页幻灯片内容精炼，避免信息过载
2. 使用中文输出
3. 严格按照 JSON 格式输出，不要添加任何其他文字
4. 幻灯片类型说明：
   - title: 封面页，包含大标题和副标题
   - section: 章节分隔页，仅有章节标题
   - content: 内容页，标题 + 要点列表
   - two_column: 双栏内容页
   - chart: 图表页，包含图表数据
   - table: 表格页，包含表格数据
   - quote: 引用页
   - ending: 结尾页"""

OUTLINE_USER_TEMPLATE = """请为以下主题生成一份 {slide_count} 页的 PPT 大纲：

主题：{topic}
{instructions}

输出 JSON 格式如下：
{{
  "title": "演示文稿标题",
  "author": "",
  "slides": [
    {{
      "slide_type": "title",
      "title": "大标题",
      "subtitle": "副标题"
    }},
    {{
      "slide_type": "section",
      "title": "章节名"
    }},
    {{
      "slide_type": "content",
      "title": "页面标题",
      "elements": [
        {{
          "element_type": "text",
          "text": "要点一",
          "style": {{"bullet": true, "bullet_level": 0}}
        }}
      ]
    }},
    {{
      "slide_type": "chart",
      "title": "图表页标题",
      "elements": [
        {{
          "element_type": "chart",
          "chart_type": "bar",
          "title": "图表标题",
          "categories": ["类别1", "类别2", "类别3"],
          "series": [
            {{"name": "系列1", "values": [100, 200, 150]}}
          ]
        }}
      ]
    }},
    {{
      "slide_type": "table",
      "title": "表格页标题",
      "elements": [
        {{
          "element_type": "table",
          "headers": ["列1", "列2"],
          "rows": [["数据1", "数据2"]]
        }}
      ]
    }},
    {{
      "slide_type": "ending",
      "title": "谢谢"
    }}
  ]
}}"""

EXPAND_SYSTEM_PROMPT = """你是一位专业的演示文稿内容优化师。你的任务是扩展现有 PPT 大纲中每页的详细内容。

要求：
1. 保持原有结构不变，只丰富内容
2. 每个要点可以补充子要点（bullet_level=1）
3. 严格按 JSON 格式输出
4. 使用中文"""

EXPAND_USER_TEMPLATE = """请扩展以下 PPT 大纲的内容，为每页补充更详细的要点：

{outline_json}

输出相同格式的完整 JSON，保持原有 slide_type 和标题，只丰富 elements 内容。"""

REFINE_SYSTEM_PROMPT = """你是一位专业的文案优化师。请根据指令优化给定的文本内容。直接输出优化后的文本，不要添加任何格式。"""

REFINE_USER_TEMPLATE = """请{instruction}以下文本：

{text}"""
