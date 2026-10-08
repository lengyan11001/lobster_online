# -*- coding: utf-8 -*-
"""把猎聘技能包写进 skill_registry.json（幂等）。"""
import io, json
from pathlib import Path

REG = Path(r"D:\lobster_online\skill_registry.json")

PKG = {
    "name": "猎聘招聘",
    "description": "猎聘招聘端（lpt.liepin.com）：先扫码登录，再搜人才 / 读候选人 / 沟通发消息 / 出候选人表。搜索走协议直连（api-lpt.liepin.com，无需浏览器界面），发消息与开简历走已登录浏览器。",
    "type": "builtin",
    "store_visibility": "online",
    "default_installed": True,
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
            "description": "搜索猎聘人才库，返回结构化候选人（姓名/性别/年龄/年限/学历/现居/期望城市/期望职位薪资/行业标签/公司履历/学校/活跃度/是否已沟通/res_id）。默认走协议直连（一次一页约 20 人，可翻页），失败自动回落到浏览器驱动。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {
                "query": {"type": "string", "description": "关键词，如「东南亚 销售总监 电表」；可为空（纯靠 filters 过滤）"},
                "pages": {"type": "integer", "default": 1, "description": "抓几页（每页约 20 人）"},
                "limit": {"type": "integer", "default": 20},
                "mode": {"type": "string", "enum": ["auto", "protocol", "browser"], "default": "auto"},
                "filters": {"type": "object", "description": "协议直连过滤条件", "properties": {
                    "must_include": {"type": "array", "items": {"type": "string"}, "description": "结果必须包含的关键词"},
                    "cities": {"type": "array", "items": {"type": "string"}},
                    "min_years": {"type": "integer"},
                    "dqs": {"type": "string", "description": "目前城市代码或中文，如 印度尼西亚/越南"},
                    "want_dqs": {"type": "string", "description": "期望城市"},
                    "jobtitles": {"type": "string"}, "company": {"type": "string"},
                    "workyears": {"type": "string", "description": "年限区间，如 0,99 / 8,99"},
                    "edu_levels": {"type": "array", "items": {"type": "string"}},
                    "language": {"type": "string", "description": "语言要求，如 英语/印尼语"},
                    "age": {"type": "string"}, "sex": {"type": "string"},
                    "active_status": {"type": "string"}, "manage_exp": {"type": "string"}}}},
                "required": []},
        },
        "liepin.candidates.suggest": {
            "description": "搜索词联想（协议直连），用于把用户口语化需求（如「东南亚电表销售」）变成猎聘能命中的关键词。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {"keyword": {"type": "string"}},
                           "required": ["keyword"]},
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
        "liepin.session.info": {
            "description": "猎聘账号概览：登录账号、会员身份与到期时间、开聊额度(b_open_chat)、未读消息数、新招呼数。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {}},
        },
        "liepin.applications.list": {
            "description": "猎聘求职者投递列表（分页）：投递人、期望职位、期望城市、学历。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {
                "page": {"type": "integer", "default": 0},
                "page_size": {"type": "integer", "default": 10}}},
        },
        "liepin.chat.list": {
            "description": "读取猎聘「沟通」页的会话列表（全部/新招呼/我发起的/我回复的等）。",
            "upstream": "local", "upstream_tool": "liepin", "enabled": True,
            "is_default": False, "unit_credits": 0,
            "arg_schema": {"type": "object", "properties": {
                "page": {"type": "integer", "default": 0},
                "page_size": {"type": "integer", "default": 30},
                "mode": {"type": "string", "enum": ["auto", "protocol", "browser"], "default": "auto"}}},
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
