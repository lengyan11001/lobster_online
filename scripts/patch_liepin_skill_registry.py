# -*- coding: utf-8 -*-
"""把猎聘技能包写进 skill_registry.json（幂等）。"""
import io, json
from pathlib import Path

REG = Path(r"D:\lobster_online\skill_registry.json")

PKG = {
    "name": "猎聘招聘",
    "description": "驱动本机浏览器操作猎聘招聘端（lpt.liepin.com）：搜索人才、读取候选人卡片与关键字段、进入沟通页按话术发消息、导出候选人清单。账号扫码登录一次后长期复用登录态。",
    "type": "builtin",
    "store_visibility": "online",
    "default_installed": False,
    "status": "available",
    "package_config": {
        "view": "liepin-recruit",
        "site": "https://lpt.liepin.com",
        "browser": "chromium_bundled",
        "cdp_port": 9222,
        "login_mode": "scan_qr",
        "rate_limit": {"searches_per_page": 2, "restart_on_blank": True, "restart_settle_sec": 30}
    },
    "capabilities": {
        "liepin.browser.open": {
            "description": "启动/重启猎聘浏览器窗口并返回登录状态。若未登录，用户在该窗口扫码登录一次即可。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {
                "action": {"type": "string", "enum": ["start", "restart", "status"]}}},
        },
        "liepin.login.status": {
            "description": "查询猎聘登录状态（是否已登录、当前账号、当前页面）。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {}},
        },
        "liepin.candidates.search": {
            "description": "按关键词搜索猎聘人才库，返回结构化候选人卡片（姓名/年龄/年限/学历/现居/期望职位薪资/行业/公司履历/是否已沟通）。遇到风控自动重启窗口重试。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {
                "query": {"type": "string", "description": "搜索词，如「东南亚 销售总监 电表」"},
                "limit": {"type": "integer", "default": 20},
                "filters": {"type": "object", "properties": {
                    "must_include": {"type": "array", "items": {"type": "string"}, "description": "卡片文本必须包含的关键词"},
                    "cities": {"type": "array", "items": {"type": "string"}},
                    "min_years": {"type": "integer"}}}},
                "required": ["query"]},
        },
        "liepin.candidates.detail": {
            "description": "打开某位候选人的简历详情页，抓取完整履历/联系方式可见字段。会消耗猎聘查看权益，必须显式确认。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {
                "name": {"type": "string"}, "age": {"type": "integer"},
                "confirm": {"type": "boolean", "description": "true 才会真正打开（消耗查看权益）"}},
                "required": ["name", "confirm"]},
        },
        "liepin.chat.list": {
            "description": "读取猎聘「沟通」页的会话列表（全部/新招呼/我发起的/我回复的等）。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {"limit": {"type": "integer", "default": 20}}},
        },
        "liepin.chat.send": {
            "description": "给沟通列表中的候选人发送消息（真实触达）。需要 confirm=true，且台账去重（相同内容不会重复发）。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {
                "target": {"type": "string", "description": "会话里的候选人名称"},
                "text": {"type": "string"},
                "confirm": {"type": "boolean"}},
                "required": ["target", "text", "confirm"]},
        },
        "liepin.ledger.read": {
            "description": "读取猎聘动作台账（搜索记录 / 已发消息 / 已开简历），用于去重和汇报。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {
                "kind": {"type": "string", "enum": ["searches", "messages", "details"]}}},
        },
        "liepin.report.export": {
            "description": "把候选人行导出成 Excel/CSV（落到本机 _lobster_runtime/liepin/exports）。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {
                "rows": {"type": "array", "items": {"type": "object"}},
                "format": {"type": "string", "enum": ["xlsx", "csv"]},
                "filename": {"type": "string"}},
                "required": ["rows"]},
        },
    },
    "tags": ["招聘", "猎聘", "HR", "候选人", "浏览器自动化"],
}

def main():
    data = json.loads(REG.read_text(encoding="utf-8"))
    pkgs = data.setdefault("packages", {})
    existed = "liepin_recruit_skill" in pkgs
    pkgs["liepin_recruit_skill"] = PKG
    REG.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    print("registry patched; existed=%s; total=%d" % (existed, len(pkgs)))
    check = json.loads(REG.read_text(encoding="utf-8"))
    caps = check["packages"]["liepin_recruit_skill"]["capabilities"]
    print("capabilities:", len(caps), sorted(caps)[:3])

if __name__ == "__main__":
    main()
