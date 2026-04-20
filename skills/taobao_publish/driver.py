"""淘宝/天猫商品发布驱动 — seller.taobao.com 商品创建。

图片上传真实流程（淘宝当前 v2/publish 表单 + sucai-selector iframe）：
  1. click 主图区空「上传图片」槽 → iframe `crs-qn/sucai-selector-ng/index` 弹出
  2. iframe 默认选中左侧「批量导入」文件夹（空），需要先切到「全部图片」
  3. click iframe 内右上「本地上传」按钮 → 触发 native file_chooser
  4. set_files(多张) → 文件批量上传到淘宝素材库（OSS）
  5. 上传完成后图片出现在「全部图片」网格里（按 mtime 倒序，最新在最前）
  6. 通过 filename 前缀匹配找到刚上传的几张 → 依次 click → 自动填到主图槽
  7. 所有 path 都 click 完后，iframe 内点击次数 = 主图槽数；iframe 通常自动关闭
  8. 如未自动关，按 ESC 或点 mask 外


参考 PDF《自动化上架流程》第 4-14 步实现自动填表（不点提交，留给用户审核）。

核心策略（淘宝 React 表单稳定定位）：
- 标题/价格/库存等"普通文本框"：用 label 文字 → 向上 6 层找 input
- 下拉框（"请选择"占位）：用 label 文字 → 找 input.placeholder='请选择' → click + 弹窗选项点击
- 单选/复选（next-radio-input）：用 label 文字 → 找同 row 的 radio[value=text]
- 图片上传：用 file_chooser 模式（找空 .drag-item → hover → click → set_files），
  必须模拟真人节奏，否则触发滑块风控
- React 受控组件填值：必须 dispatch input + change event，否则 React state 不刷新

防风控（淘宝行为风控）：
- 每张图片上传后随机停 5-9s（比真人手速稍快，但不至于触发风控）
- 环境变量 TAOBAO_PACE_MULT（默认 1）：整体放慢各区间 sleep / 区域切换间隔；严风控时可试 1.8～2.5
- 环境变量 TAOBAO_DETAIL_BATCH_SIZE（默认 6）：详情富文本一次「本地上传」最多几张；拆批可降低 OSS 并发与多选连点强度
- 槽 hover → 抖动 → 短停顿 → click（不秒点）
- 每 3 张图做一次"歇手"（3-5s）
- 不同区域之间停 8-12s（模拟滚动找下个区域）
- 检测到滑块/验证码弹窗时立即 raise，让上层暂停
"""
from __future__ import annotations

import asyncio
import logging
import os
import random
from typing import Any, Dict, List, Optional

from skills._base import BaseDriver
from skills.taobao_publish import sucai_cache

logger = logging.getLogger(__name__)

LOGIN_URL = "https://myseller.taobao.com/home.htm"
# 发布入口三档（按 PDF《自动化上架流程》第 4 步实际跳转链验证）：
#
# PDF 完整跳转链：
#   ai/category.htm?opportunityId=xxx → 用户选类目 → 真实发布表单页：
#     v2/publish.htm?catId=YYY&opportunityType=2&opportunityId=xxx
#                  &fromAICategory=true&fromAIPublish=true&newRouter=1
#                  &keyProps={"p-20000":{"value":30025069481,"text":"无品牌/无注册商标"}}
#
# 我们直接构造该目标 URL，跳过 ai/category.htm 中转步（有 cat_id + opportunityId 即可）。
# DOM 与不带 opportunity 的 v2/publish.htm?catId= 完全一致，driver 现有填表逻辑可复用。
PRODUCT_ADD_OPPORTUNITY_FULL = (
    "https://item.upload.taobao.com/sell/v2/publish.htm"
    "?catId={cat_id}&opportunityType={opportunity_type}&opportunityId={opportunity_id}"
    "&fromAICategory=true&fromAIPublish=true&newRouter=1"
)
# 仅 opportunityId 而无 cat_id 时，走 ai/category.htm 让淘宝自己填类目
PRODUCT_ADD_AI_CATEGORY_URL = (
    "https://item.upload.taobao.com/sell/ai/category.htm"
    "?opportunityType={opportunity_type}&opportunityId={opportunity_id}"
)
PRODUCT_ADD_URL_TEMPLATE = "https://item.upload.taobao.com/sell/v2/publish.htm?catId={cat_id}"
PRODUCT_ADD_FALLBACK_URL = "https://item.upload.taobao.com/sell/v2/publish.htm"


class TaobaoDriver(BaseDriver):

    def login_url(self) -> str:
        return LOGIN_URL

    def product_add_url(
        self,
        cat_id: Optional[int] = None,
        opportunity_id: Optional[int] = None,
        opportunity_type: int = 2,
    ) -> str:
        """构造商品发布 URL，按 PDF 实际跳转链优先级：
        1) opportunity_id + cat_id 都有 → 直接构造 PDF 流程的最终目标 URL（最优）
        2) 仅 opportunity_id → ai/category.htm（淘宝自动填类目，但需 driver 在该页选类目下一步）
        3) 仅 cat_id → v2/publish.htm?catId=xxx（绕过商机发现，原有捷径）
        4) 都没有 → v2/publish.htm（让用户手选类目）
        """
        oid = int(opportunity_id) if opportunity_id and int(opportunity_id) > 0 else None
        cid = int(cat_id) if cat_id and int(cat_id) > 0 else None
        otype = int(opportunity_type or 2)
        if oid and cid:
            return PRODUCT_ADD_OPPORTUNITY_FULL.format(
                cat_id=cid, opportunity_id=oid, opportunity_type=otype,
            )
        if oid:
            return PRODUCT_ADD_AI_CATEGORY_URL.format(
                opportunity_id=oid, opportunity_type=otype,
            )
        if cid:
            return PRODUCT_ADD_URL_TEMPLATE.format(cat_id=cid)
        return PRODUCT_ADD_FALLBACK_URL

    async def check_login(self, page: Any, navigate: bool = True) -> bool:
        try:
            # 若当前页已经是公认的「登录后业务页」，navigate=True 也跳过重定向 —
            # 避免误打误撞导航到某些已下线/变动的 seller.taobao.com 根路径被重定向到登录页。
            cur = (page.url or "").lower()
            already_inside = bool(cur) and (
                "myseller.taobao.com" in cur
                or "item.upload.taobao.com/sell" in cur
                or "taobao.com/home" in cur
                or "sycm.taobao.com" in cur
                or "wsc.taobao.com" in cur
            ) and ("login" not in cur) and ("passport" not in cur)
            if navigate and not already_inside:
                await page.goto(LOGIN_URL, wait_until="domcontentloaded", timeout=20000)
                await asyncio.sleep(3)
            url = (page.url or "").lower()
            if "login" in url or "passport" in url:
                return False
            content = (await page.content() or "")[:3000]
            if "请登录" in content or "扫码登录" in content:
                return False
            return ("taobao.com" in url or "tmall.com" in url) and "login" not in url
        except Exception:
            return False

    # ------------------------------------------------------------------
    # 通用 helper：按 label 文字定位 + 填充
    # ------------------------------------------------------------------

    async def _fill_text_by_label(self, page: Any, label: str, value: str, *, fuzzy: bool = False) -> bool:
        """填普通文本框（含 textarea）。淘宝 React 控件需 dispatch input+change。

        fuzzy=True 时按"包含"匹配 label（用于 specs 字段名不完全一致：'材质' → 匹配 '板材'）。
        """
        try:
            ok = await page.evaluate(
                """
                ({label, value, fuzzy}) => {
                    const all = document.querySelectorAll('span, label, div, dt, td');
                    const matchLabel = (text) => {
                        const t = (text || '').trim();
                        if (t === label || t === label + ' *' || t === '* ' + label) return true;
                        if (!fuzzy) return false;
                        // fuzzy：双向包含 + 至少 2 字共享
                        if (t && label && t.length <= 12 && label.length <= 12) {
                            if (t.includes(label) || label.includes(t)) return true;
                        }
                        return false;
                    };
                    for (const el of all) {
                        if (!matchLabel(el.textContent)) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 5 || r.height < 5) continue;
                        let p = el;
                        for (let i = 0; i < 6 && p; i++) {
                            p = p.parentElement;
                            if (!p) break;
                            const inp = p.querySelector('input[type="text"], input:not([type]), textarea');
                            if (inp) {
                                inp.focus();
                                const setter = Object.getOwnPropertyDescriptor(window.HTMLInputElement.prototype, 'value')
                                    || Object.getOwnPropertyDescriptor(window.HTMLTextAreaElement.prototype, 'value');
                                setter.set.call(inp, value);
                                inp.dispatchEvent(new Event('input', { bubbles: true }));
                                inp.dispatchEvent(new Event('change', { bubbles: true }));
                                inp.blur();
                                return true;
                            }
                        }
                    }
                    return false;
                }
                """,
                {"label": label, "value": str(value), "fuzzy": bool(fuzzy)},
            )
            if ok:
                logger.info("[TAOBAO] filled label=%r value=%r fuzzy=%s", label, str(value)[:60], fuzzy)
            return bool(ok)
        except Exception as e:
            logger.warning("[TAOBAO] _fill_text_by_label %r failed: %s", label, e)
            return False

    async def _select_dropdown_by_label(self, page: Any, label: str, option_text: str) -> bool:
        """点击"请选择"下拉框 → 等待弹层 → 点击匹配 option。"""
        try:
            # 1) 点开下拉
            opened = await page.evaluate(
                """
                (label) => {
                    const all = document.querySelectorAll('span, label, div, dt, td');
                    for (const el of all) {
                        const t = (el.textContent || '').trim();
                        if (t === label || t === label + ' *' || t === '* ' + label) {
                            let p = el;
                            for (let i = 0; i < 6 && p; i++) {
                                p = p.parentElement;
                                if (!p) break;
                                const trigger = p.querySelector('input, [role="combobox"], [class*="select-trigger"]');
                                if (trigger) {
                                    trigger.click();
                                    return true;
                                }
                            }
                        }
                    }
                    return false;
                }
                """,
                label,
            )
            if not opened:
                return False
            await asyncio.sleep(0.5)
            # 2) 在弹层选项里点匹配项
            picked = await page.evaluate(
                """
                (option) => {
                    const items = document.querySelectorAll('[role="option"], [class*="select-menu"] li, [class*="next-menu-item"]');
                    for (const it of items) {
                        const t = (it.textContent || '').trim();
                        if (t === option || t.includes(option)) {
                            const r = it.getBoundingClientRect();
                            if (r.width > 5 && r.height > 5) {
                                it.click();
                                return true;
                            }
                        }
                    }
                    return false;
                }
                """,
                str(option_text),
            )
            if picked:
                logger.info("[TAOBAO] selected dropdown label=%r option=%r", label, option_text)
            else:
                logger.warning("[TAOBAO] dropdown opened but option %r not found for %r", option_text, label)
            return bool(picked)
        except Exception as e:
            logger.warning("[TAOBAO] _select_dropdown_by_label %r failed: %s", label, e)
            return False

    async def _dump_label_neighborhood(self, page: Any, label: str, max_chars: int = 1200) -> str:
        """调试：找含 label 文本的元素，摘出其向上 6 层 parent 的 outerHTML 片段。

        返回纯文本（去掉过长的 svg/inline-style）便于打印到日志，定位淘宝实际属性控件结构。
        """
        try:
            html_snippet = await page.evaluate(
                """
                ({label, max_chars}) => {
                    const all = document.querySelectorAll('span, label, div, dt, td, a');
                    const candidates = [];
                    for (const el of all) {
                        const t = (el.textContent || '').trim();
                        if (!t) continue;
                        if (t === label || t === label + ' *' || t === '* ' + label
                            || (t.length <= 20 && (t.includes(label) || label.includes(t)))) {
                            const r = el.getBoundingClientRect();
                            if (r.width < 5 || r.height < 5) continue;
                            candidates.push(el);
                            if (candidates.length >= 3) break;
                        }
                    }
                    if (!candidates.length) return '';
                    const out = [];
                    for (const el of candidates) {
                        let p = el;
                        for (let i = 0; i < 6 && p && p.parentElement; i++) p = p.parentElement;
                        let html = (p && p.outerHTML) || '';
                        // 精简：去 svg / 长 style / data-*
                        html = html.replace(/<svg[\\s\\S]*?<\\/svg>/g, '<svg/>')
                                   .replace(/\\s(style|data-[\\w-]+)="[^"]{0,400}"/g, '')
                                   .replace(/\\s+/g, ' ');
                        if (html.length > max_chars) html = html.slice(0, max_chars) + '...';
                        out.push(html);
                    }
                    return out.join('\\n---\\n');
                }
                """,
                {"label": label, "max_chars": int(max_chars)},
            )
            return html_snippet or ""
        except Exception as e:
            logger.warning("[TAOBAO] _dump_label_neighborhood %r failed: %s", label, e)
            return ""

    async def _click_radio_by_label(self, page: Any, label: str, option_text: str) -> bool:
        """单选组：在 label 所在 row 找含 option_text 的 radio item 并点击。"""
        try:
            ok = await page.evaluate(
                """
                ({label, option}) => {
                    const all = document.querySelectorAll('span, label, div, dt, td');
                    for (const el of all) {
                        const t = (el.textContent || '').trim();
                        if (t === label || t === label + ' *' || t === '* ' + label) {
                            let p = el;
                            for (let i = 0; i < 8 && p; i++) {
                                p = p.parentElement;
                                if (!p) break;
                                const radios = p.querySelectorAll('label, [class*="radio"]');
                                for (const r of radios) {
                                    const rt = (r.textContent || '').trim();
                                    if (rt === option || rt.includes(option)) {
                                        const rect = r.getBoundingClientRect();
                                        if (rect.width > 5 && rect.height > 5) {
                                            r.click();
                                            return true;
                                        }
                                    }
                                }
                            }
                        }
                    }
                    return false;
                }
                """,
                {"label": label, "option": str(option_text)},
            )
            if ok:
                logger.info("[TAOBAO] clicked radio label=%r option=%r", label, option_text)
            return bool(ok)
        except Exception as e:
            logger.warning("[TAOBAO] _click_radio_by_label %r failed: %s", label, e)
            return False

    async def _upload_to_first_file_input(self, page: Any, paths: List[str]) -> bool:
        """简化：把所有图片塞到第一个 input[type=file]（早期 driver 行为）。"""
        if not paths:
            return False
        try:
            upload = page.locator('input[type="file"]').first
            if await upload.count() > 0:
                await upload.set_input_files(paths)
                logger.info("[TAOBAO] uploaded %d images via first file input", len(paths))
                await asyncio.sleep(2)
                return True
        except Exception as e:
            logger.warning("[TAOBAO] _upload_to_first_file_input failed: %s", e)
        return False

    # ------------------------------------------------------------------
    # 分区上传（淘宝 v2/publish 表单的多个上传区）
    # ------------------------------------------------------------------

    async def _find_zone_input_indexes(self, page: Any, zone_keywords: List[str]) -> List[int]:
        """在页面中找到属于指定区域（如"1:1主图"/"3:4主图"/"白底图"）的所有 file input
        在全页 file input 列表中的下标。

        策略：
          1) 找含任一关键词的元素（短文本，命中区域标题）
          2) 向上爬不超过 12 层，找到既包含该标题、也包含 .drag-item 的祖先 = 区域容器
          3) 在容器内枚举所有 input[type=file]
          4) 比较容器内 inputs 与全页 inputs，输出全页下标
        返回 [] 表示没找到。
        """
        try:
            indexes = await page.evaluate(
                """
                (keywords) => {
                    const allInputs = Array.from(document.querySelectorAll('input[type="file"]'));
                    if (!allInputs.length) return [];
                    const titleEls = [];
                    const candidates = document.querySelectorAll('span, div, label, dt, h1, h2, h3, h4, p, em, b');
                    for (const el of candidates) {
                        const t = (el.textContent || '').trim();
                        if (!t || t.length > 30) continue;
                        for (const kw of keywords) {
                            if (t === kw || t === kw + ' *' || t === '* ' + kw) {
                                titleEls.push(el);
                                break;
                            }
                        }
                    }
                    if (!titleEls.length) return [];
                    for (const titleEl of titleEls) {
                        let p = titleEl;
                        for (let k = 0; k < 12 && p; k++) {
                            p = p.parentElement;
                            if (!p) break;
                            const drags = p.querySelectorAll('.drag-item, [class*="drag-item"]');
                            const inputs = p.querySelectorAll('input[type="file"]');
                            if (drags.length >= 1 && inputs.length >= 1) {
                                const out = [];
                                for (let i = 0; i < allInputs.length; i++) {
                                    if (p.contains(allInputs[i])) out.push(i);
                                }
                                if (out.length > 0) return out;
                            }
                        }
                    }
                    return [];
                }
                """,
                list(zone_keywords),
            )
            return [int(x) for x in (indexes or [])]
        except Exception as e:
            logger.warning("[TAOBAO] _find_zone_input_indexes %s failed: %s", zone_keywords, e)
            return []

    async def _click_white_bg_generate_from_main(self, page: Any) -> bool:
        """白底图区有「从主图生成」按钮，淘宝自动从已上传主图裁出 800x800 白底图。
        优先用这个，不走 sucai-selector iframe（更可靠 + 不占素材库 + 没风控压力）。
        """
        try:
            ok = await page.evaluate(
                """
                () => {
                    // 找含「白底图」短文本的元素
                    const titleEls = [];
                    const els = document.querySelectorAll('span, label, div, dt');
                    for (const e of els) {
                        const t = (e.textContent || '').trim();
                        if (t === '白底图' || t === '白底图 *' || t === '* 白底图') {
                            titleEls.push(e);
                        }
                    }
                    if (!titleEls.length) return false;
                    for (const titleEl of titleEls) {
                        let p = titleEl;
                        for (let k = 0; k < 10 && p; k++) {
                            p = p.parentElement;
                            if (!p) break;
                            // 在父容器里找「从主图生成」按钮/链接
                            const candidates = p.querySelectorAll('a, button, span, [role="button"]');
                            for (const c of candidates) {
                                const ct = (c.textContent || '').trim();
                                if (ct === '从主图生成' || ct.includes('从主图生成')) {
                                    if (ct.length > 20) continue;
                                    const r = c.getBoundingClientRect();
                                    if (r.width < 5 || r.height < 5) continue;
                                    c.scrollIntoView({block: 'center', behavior: 'instant'});
                                    c.click();
                                    return true;
                                }
                            }
                        }
                    }
                    return false;
                }
                """
            )
            return bool(ok)
        except Exception as e:
            print(f"[TAOBAO] _click_white_bg_generate_from_main 失败: {e}", flush=True)
            return False

    async def _click_detail_editor_image_toolbar(self, page: Any) -> Dict[str, Any]:
        """点击「宝贝详情」富文本工具栏上的「图片」类入口，打开 sucai-selector。"""
        return await page.evaluate(
            """
            () => {
                const TARGETS = ['图片', '添加图片', '+ 图片', '+图片', '插入图片'];
                const titleEls = [];
                const els = document.querySelectorAll('span, label, div, dt, h2, h3');
                for (const e of els) {
                    const t = (e.textContent || '').trim();
                    if (t === '宝贝详情' || t === '宝贝详情 *' || t === '* 宝贝详情') {
                        titleEls.push(e);
                    }
                }
                if (!titleEls.length) return {ok: false, why: 'no-title'};
                for (const titleEl of titleEls) {
                    let p = titleEl;
                    for (let k = 0; k < 15 && p; k++) {
                        p = p.parentElement;
                        if (!p) break;
                        const containerText = (p.textContent || '');
                        if (!(containerText.includes('文字') || containerText.includes('源码导入') || containerText.includes('模板'))) {
                            continue;
                        }
                        const buttons = p.querySelectorAll('button, [role="button"], a, span, div, li');
                        for (const b of buttons) {
                            const t = (b.textContent || '').trim();
                            if (!TARGETS.includes(t)) continue;
                            if (t.length > 10) continue;
                            const r = b.getBoundingClientRect();
                            if (r.width < 5 || r.height < 5) continue;
                            if (b.children && b.children.length > 5) continue;
                            b.scrollIntoView({block: 'center', behavior: 'instant'});
                            b.click();
                            return {ok: true, text: t, tag: b.tagName, cls: (b.className || '').toString().slice(0, 60)};
                        }
                        const dump = [];
                        const all = p.querySelectorAll('button, [role="button"], a, span, li');
                        for (const e of all) {
                            const t = (e.textContent || '').trim();
                            if (!t || t.length > 12) continue;
                            const r = e.getBoundingClientRect();
                            if (r.width < 5 || r.height < 5) continue;
                            dump.push({t, tag: e.tagName, cls: (e.className || '').toString().slice(0, 60)});
                            if (dump.length > 30) break;
                        }
                        return {ok: false, why: 'no-match', dump};
                    }
                }
                return {ok: false, why: 'no-container'};
            }
            """
        )

    async def _wait_sucai_iframe(self, page: Any, *, label: str) -> Any:
        sucai_frame = None
        for _ in range(30):
            await asyncio.sleep(0.3)
            for fr in page.frames:
                src = (fr.url or "")
                if "sucai-selector" in src or "crs-qn" in src:
                    try:
                        await fr.wait_for_load_state("domcontentloaded", timeout=2000)
                    except Exception:
                        pass
                    sucai_frame = fr
                    break
            if sucai_frame:
                break
        if not sucai_frame:
            print(f"[TAOBAO] zone '{label}': iframe 没出现", flush=True)
        return sucai_frame

    async def _upload_to_detail_editor(
        self, page: Any, paths: List[str], *, max_files: int = 30
    ) -> int:
        """详情图上传：「宝贝详情」区的「图片」→ sucai-selector → 本地上传 → 多选 → 确定。

        与主图区差别：富文本为多选模式，必须点右下角「确定(N)」才真正插入。

        风控：默认按 TAOBAO_DETAIL_BATCH_SIZE（默认 6）拆成多批，避免单次 set_files 过多张
        与网格内快速连点触发 baxia。
        """
        if not paths:
            return 0
        paths = paths[:max_files]
        label = "详情图"
        try:
            batch_sz = int(os.environ.get("TAOBAO_DETAIL_BATCH_SIZE", "6") or "6")
        except ValueError:
            batch_sz = 6
        batch_sz = max(1, min(12, batch_sz))
        m = self._pace_mult()
        batches = [paths[i : i + batch_sz] for i in range(0, len(paths), batch_sz)]
        print(
            f"[TAOBAO] zone '{label}': 共 {len(paths)} 张，拆 {len(batches)} 批（每批≤{batch_sz}，pace={m:.2f}）",
            flush=True,
        )
        grand_total = 0
        preloaded = getattr(self, "_preloaded", None) or {}
        for bi, batch in enumerate(batches):
            await self._dismiss_floating_dialogs(page)
            await asyncio.sleep(random.uniform(2.0, 3.5) * m)
            if bi > 0:
                # 批与批之间多歇一会，模拟人在编辑器里看图、再插下一组
                gap = random.uniform(4.0, 8.5) * m
                print(f"[TAOBAO] zone '{label}': 第 {bi + 1} 批前间隔 {gap:.1f}s", flush=True)
                await asyncio.sleep(gap)

            # 预加载优先：如果本批 paths 全在 preloaded 里，直接点「图片」→ 多选 OID → 确定
            if preloaded and all(os.path.abspath(p) in preloaded for p in batch):
                pre_ok = await self._detail_batch_apply_preloaded(
                    page, batch, preloaded, batch_label=f"{label}批{bi + 1}"
                )
                if pre_ok > 0:
                    grand_total += pre_ok
                    print(
                        f"[TAOBAO] zone '{label}': 批 {bi + 1} 预加载路径插入 {pre_ok}（累计≈{grand_total}）",
                        flush=True,
                    )
                    continue
                print(
                    f"[TAOBAO] zone '{label}': 批 {bi + 1} 预加载路径失败，回退本地上传",
                    flush=True,
                )

            clicked = await self._click_detail_editor_image_toolbar(page)
            if not (clicked or {}).get("ok"):
                print(f"[TAOBAO] zone '{label}': 批 {bi + 1} 没找到「图片」入口 — 调试={clicked}", flush=True)
                break
            print(
                f"[TAOBAO] zone '{label}': 批 {bi + 1}/{len(batches)} 点了「{clicked.get('text')}」→ 等 iframe",
                flush=True,
            )
            await asyncio.sleep(random.uniform(0.8, 1.5) * m)

            sucai_frame = await self._wait_sucai_iframe(page, label=label)
            if not sucai_frame:
                break
            await asyncio.sleep(random.uniform(1.5, 2.5) * m)

            try:
                all_btn = sucai_frame.locator('text=/^全部图片$/').first
                if await all_btn.count() > 0:
                    await all_btn.click(timeout=3000)
                    await asyncio.sleep(random.uniform(0.8, 1.5) * m)
            except Exception:
                pass

            try:
                local_btn = sucai_frame.locator('button:has-text("本地上传")').first
                await local_btn.wait_for(state="visible", timeout=8000)
            except Exception as e:
                print(f"[TAOBAO] zone '{label}': 批 {bi + 1} 找不到「本地上传」 {e}", flush=True)
                await self._close_sucai_iframe_force(page, sucai_frame)
                break

            try:
                async with page.expect_file_chooser(timeout=10000) as fc_info:
                    await asyncio.sleep(random.uniform(0.45, 0.95) * m)
                    await local_btn.click(timeout=5000)
                fc = await fc_info.value
                await asyncio.sleep(random.uniform(0.65, 1.25) * m)
                await fc.set_files(list(batch))
                wait_sec = max(18.0, min(55.0, 6.0 + 3.2 * len(batch)))
                wait_sec += random.uniform(0, 2.5)
                wait_sec *= m
                print(
                    f"[TAOBAO] zone '{label}': 批 {bi + 1} set_files {len(batch)} 张，等 OSS {wait_sec:.1f}s",
                    flush=True,
                )
                await asyncio.sleep(wait_sec)
            except Exception as e:
                print(f"[TAOBAO] zone '{label}': 批 {bi + 1} chooser 异常 {e}", flush=True)
                await self._close_sucai_iframe_force(page, sucai_frame)
                break

            if await self._is_risk_control_visible(page):
                if not await self._wait_for_risk_control_resolved(page, max_wait_sec=300):
                    await self._close_sucai_iframe_force(page, sucai_frame)
                    break

            selected = await self._select_thumbnails_multi(
                page, sucai_frame, batch, zone_label=f"{label}批{bi + 1}"
            )
            print(
                f"[TAOBAO] zone '{label}': 批 {bi + 1} 选中/计数 {selected}/{len(batch)}",
                flush=True,
            )

            if selected > 0:
                confirmed = await self._click_iframe_confirm_button(
                    sucai_frame, expected_count=selected
                )
                if confirmed:
                    grand_total += selected
                    print(
                        f"[TAOBAO] zone '{label}': 批 {bi + 1} 点「确定」插入 OK（累计≈{grand_total}）",
                        flush=True,
                    )
                else:
                    print(f"[TAOBAO] zone '{label}': 批 {bi + 1} 「确定」click 失败", flush=True)

            await asyncio.sleep(random.uniform(2.5, 4.0) * m)
            try:
                still = not sucai_frame.is_detached()
            except Exception:
                still = False
            if still:
                await self._close_sucai_iframe_force(page, sucai_frame)
            await asyncio.sleep(random.uniform(2.0, 3.2) * m)

        return grand_total

    async def _detail_batch_apply_preloaded(
        self,
        page: Any,
        batch: List[str],
        preloaded: Dict[str, str],
        *,
        batch_label: str,
    ) -> int:
        """详情图批：预加载路径 —— 点「图片」→ 多选勾 OID 命中的 imgBox → 点确定。
        成功插入张数返回；失败返回 0（让上游回退本地上传）。
        """
        oids: List[str] = []
        for p in batch:
            oid = preloaded.get(os.path.abspath(p))
            if not oid:
                return 0
            oids.append(oid)

        clicked_toolbar = await self._click_detail_editor_image_toolbar(page)
        if not (clicked_toolbar or {}).get("ok"):
            print(f"[TAOBAO] {batch_label}: 预加载路径找不到详情图「图片」入口", flush=True)
            return 0
        await asyncio.sleep(random.uniform(0.8, 1.5))

        sucai_frame = await self._wait_sucai_iframe(page, label=batch_label)
        if not sucai_frame:
            return 0
        await asyncio.sleep(random.uniform(1.2, 2.0))

        try:
            all_btn = sucai_frame.locator('text=/^全部图片$/').first
            if await all_btn.count() > 0:
                await all_btn.click(timeout=3000)
                await asyncio.sleep(random.uniform(0.8, 1.3))
        except Exception:
            pass

        target_indexes: List[int] = []
        for oid in oids:
            idx = await self._find_grid_index_by_oid(sucai_frame, oid)
            if idx < 0:
                print(f"[TAOBAO] {batch_label}: OID {oid} 未命中，放弃预加载详情路径", flush=True)
                await self._close_sucai_iframe_force(page, sucai_frame)
                return 0
            target_indexes.append(idx)

        cards_locator = sucai_frame.locator('[class*="PicList_pic_imgBox"]')
        clicked = 0
        last_n = 0
        pm = self._pace_mult()
        for step, idx in enumerate(target_indexes):
            try:
                card = cards_locator.nth(idx)
                try:
                    await card.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass
                ok = await self._multi_select_click_card_at_index(sucai_frame, idx)
                if ok:
                    clicked += 1
                    await asyncio.sleep(random.uniform(0.35, 0.6) * pm)
                    n = await self._read_iframe_confirm_count(sucai_frame)
                    if n is not None and n > last_n:
                        last_n = n
            except Exception as e:
                print(f"[TAOBAO] {batch_label}: 预加载多选 idx={idx} 异常 {e}", flush=True)

        if clicked <= 0:
            await self._close_sucai_iframe_force(page, sucai_frame)
            return 0

        confirmed = await self._click_iframe_confirm_button(sucai_frame, expected_count=clicked)
        if not confirmed:
            print(f"[TAOBAO] {batch_label}: 预加载多选后确定按钮 click 失败", flush=True)
        await asyncio.sleep(random.uniform(2.0, 3.2) * pm)
        try:
            still = not sucai_frame.is_detached()
        except Exception:
            still = False
        if still:
            await self._close_sucai_iframe_force(page, sucai_frame)
        return clicked if confirmed else 0

    async def _multi_select_click_card_at_index(self, sucai_frame: Any, idx: int) -> bool:
        """在 iframe 文档内对第 idx 张 PicList 卡片执行「多选勾选」（避免点中预览区）。

        优先顺序：真实 checkbox → 常见勾选 UI → elementFromPoint(左上角) → 合成事件。
        全部在 iframe 的 document 上执行，坐标与命中比主页面 mouse 更稳。
        """
        try:
            return await sucai_frame.evaluate(
                """
                (idx) => {
                    const cards = Array.from(
                        document.querySelectorAll('[class*="PicList_pic_imgBox"]')
                    );
                    const card = cards[idx];
                    if (!card) return false;

                    const tryClick = (el) => {
                        try {
                            el.dispatchEvent(
                                new MouseEvent("click", { bubbles: true, cancelable: true })
                            );
                            if (typeof el.click === "function") el.click();
                            return true;
                        } catch (e) {
                            return false;
                        }
                    };

                    const selectors = [
                        'input[type="checkbox"]',
                        '[class*="checkbox"]',
                        '[class*="check-box"]',
                        '[class*="select-box"]',
                        '[class*="check-mark"]',
                        '[class*="checkMark"]',
                        '[class*="selectIcon"]',
                        '[class*="pic_select"]',
                        '[class*="Pic_select"]',
                        '[class*="select-icon"]',
                    ];
                    for (const sel of selectors) {
                        const el = card.querySelector(sel);
                        if (!el) continue;
                        const r = el.getBoundingClientRect();
                        if (r.width < 2 || r.height < 2) continue;
                        if (tryClick(el)) return true;
                    }

                    const r = card.getBoundingClientRect();
                    const px = r.left + 12;
                    const py = r.top + 12;
                    const topEl = document.elementFromPoint(px, py);
                    if (topEl && card.contains(topEl) && tryClick(topEl)) return true;

                    const ev = new MouseEvent("click", {
                        bubbles: true,
                        cancelable: true,
                        clientX: px,
                        clientY: py,
                        view: window,
                    });
                    card.dispatchEvent(ev);
                    return true;
                }
                """,
                idx,
            )
        except Exception:
            return False

    async def _select_thumbnails_multi(
        self,
        page: Any,
        sucai_frame: Any,
        paths: List[str],
        *,
        zone_label: str = "",
    ) -> int:
        """多选模式：勾选刚上传的 N 张素材（与主图区相同：优先用文件名匹配网格索引）。

        策略：
          1. 用 img.src 匹配 filename stem → 得到目标卡片下标（避免点到素材库里旧图）
          2. 在 iframe 内对每个下标调用 _multi_select_click_card_at_index（勾选区，非预览）
          3. 用「确定 (N)」数字是否递增校验
        """
        cards_locator = sucai_frame.locator('[class*="PicList_pic_imgBox"]')
        total = await cards_locator.count()
        if total == 0 or not paths:
            return 0

        filename_stems: List[str] = []
        for p in paths:
            base = os.path.basename(p)
            stem, _ext = os.path.splitext(base)
            filename_stems.append(stem)

        src_hits = await self._find_grid_indexes_by_src(sucai_frame, filename_stems)
        if src_hits:
            target_indexes = src_hits
            print(
                f"[TAOBAO] zone '{zone_label}': 多选 src 匹配命中 {len(target_indexes)} 张 indexes={target_indexes[:12]}",
                flush=True,
            )
        else:
            target_indexes = list(range(min(len(paths), total)))
            print(
                f"[TAOBAO] zone '{zone_label}': 多选 src 未匹配，回退前 {len(target_indexes)} 张 imgBox",
                flush=True,
            )

        try:
            sample_html = await sucai_frame.evaluate(
                """
                () => {
                    const card = document.querySelector('[class*="PicList_pic_imgBox"]');
                    if (!card) return null;
                    return card.outerHTML.slice(0, 800);
                }
                """
            )
            if sample_html:
                print(f"[TAOBAO] zone '{zone_label}': imgBox sample HTML = {sample_html[:400]}", flush=True)
        except Exception:
            pass

        clicked = 0
        last_n = 0
        pm = self._pace_mult()
        heavy = len(target_indexes) > 8
        for step, idx in enumerate(target_indexes):
            if idx < 0 or idx >= total:
                continue
            try:
                card = cards_locator.nth(idx)
                await asyncio.sleep(random.uniform(0.35, 0.75) * pm * (1.25 if heavy else 1.0))
                try:
                    await card.scroll_into_view_if_needed(timeout=2000)
                except Exception:
                    pass

                ok = await self._multi_select_click_card_at_index(sucai_frame, idx)
                if ok:
                    clicked += 1
                    await asyncio.sleep(random.uniform(0.35, 0.65) * pm * (1.25 if heavy else 1.0))
                    n = await self._read_iframe_confirm_count(sucai_frame)
                    if n is not None:
                        if n > last_n:
                            last_n = n
                        elif step < 3:
                            print(
                                f"[TAOBAO] zone '{zone_label}': 第 {step + 1} 张(idx={idx})后 N={n}，未累加",
                                flush=True,
                            )
            except Exception as e:
                print(
                    f"[TAOBAO] zone '{zone_label}': 第 {step + 1} 张(idx={idx})异常 {e}",
                    flush=True,
                )
                continue
            if heavy and step > 0 and (step + 1) % 4 == 0:
                await asyncio.sleep(random.uniform(0.9, 1.8) * pm)

        await asyncio.sleep(random.uniform(0.6, 1.0) * pm)
        n_final = await self._read_iframe_confirm_count(sucai_frame)
        print(
            f"[TAOBAO] zone '{zone_label}': 勾选尝试 {clicked} 次，「确定」按钮 N={n_final}",
            flush=True,
        )
        return n_final if n_final and n_final > 0 else clicked

    async def _read_iframe_confirm_count(self, sucai_frame: Any) -> Optional[int]:
        """读取 iframe 右下角「确定 (N)」按钮里的数字 N（兼容全角括号、无空格等）。"""
        try:
            txt = await sucai_frame.evaluate(
                """
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (const b of buttons) {
                        const t = (b.textContent || '').replace(/\\s+/g, ' ').trim();
                        // 确定(12) 确定（12） 确定 (12) 等
                        const m = t.match(/^确定\\s*[（(]?\\s*(\\d+)\\s*[）)]?/);
                        if (m) return parseInt(m[1], 10);
                    }
                    return null;
                }
                """
            )
            if txt is None:
                return None
            return int(txt)
        except Exception:
            return None

    async def _click_iframe_confirm_button(
        self, sucai_frame: Any, *, expected_count: int = 0
    ) -> bool:
        """点击 iframe 内已启用的「确定 (N)」按钮（跳过 disabled）。"""
        try:
            await asyncio.sleep(random.uniform(0.5, 1.0))
            ok = await sucai_frame.evaluate(
                """
                () => {
                    const buttons = document.querySelectorAll('button');
                    for (let i = buttons.length - 1; i >= 0; i--) {
                        const b = buttons[i];
                        if (b.disabled || b.getAttribute('aria-disabled') === 'true') continue;
                        const t = (b.textContent || '').replace(/\\s+/g, ' ').trim();
                        if (!/^确定/.test(t)) continue;
                        if (!/\\d+/.test(t)) continue;
                        b.click();
                        return true;
                    }
                    return false;
                }
                """
            )
            if ok:
                return True
            # 兜底：最后一个「确定」按钮（可能无数字）
            btn = sucai_frame.locator('button:has-text("确定")').last
            if await btn.count() == 0:
                return False
            await btn.click(timeout=4000)
            return True
        except Exception as e:
            print(f"[TAOBAO] _click_iframe_confirm_button 失败: {e}", flush=True)
            return False

    async def _expand_zone_section(self, page: Any, zone_keywords: List[str]) -> bool:
        """点击展开按钮，让折叠的子区域（白底图/商品视频/购后视频/SKU 等）显示上传槽。

        淘宝表单上有「展开 ▽」按钮，旁边跟「商品视频 / 白底图 / 购后视频」等 tab。
        策略：
          1. 找到任一 zone_keyword 对应的 tab 文字（短文本，<=10 字）
          2. 在它附近（同行 / 父容器内）找含「展开」的按钮 → click
          3. click tab 自身（如果展开后是 tab 切换）
        """
        try:
            ok = await page.evaluate(
                """
                (keywords) => {
                    let opened = 0;
                    const tabs = [];
                    const candidates = document.querySelectorAll('span, div, label, dt, button, a');
                    for (const el of candidates) {
                        const t = (el.textContent || '').trim();
                        if (!t || t.length > 12) continue;
                        for (const kw of keywords) {
                            if (t === kw || t === kw + ' *' || t === '* ' + kw) {
                                tabs.push(el);
                                break;
                            }
                        }
                    }
                    if (!tabs.length) return 0;
                    for (const tab of tabs) {
                        const r = tab.getBoundingClientRect();
                        if (r.width < 5 || r.height < 5) continue;
                        // 在同一父行内找「展开」按钮
                        let parent = tab;
                        for (let k = 0; k < 8 && parent; k++) {
                            parent = parent.parentElement;
                            if (!parent) break;
                            const expandBtns = parent.querySelectorAll('button, [role="button"], a, span');
                            for (const b of expandBtns) {
                                const bt = (b.textContent || '').trim();
                                if (bt === '展开' || bt === '展开 ▽' || bt.startsWith('展开')) {
                                    const br = b.getBoundingClientRect();
                                    if (br.width > 5 && br.height > 5) {
                                        b.click();
                                        opened++;
                                        break;
                                    }
                                }
                            }
                            if (opened > 0) break;
                        }
                        // 同时 click 该 tab（确保切到当前类型）
                        try { tab.click(); } catch(e) {}
                    }
                    return opened;
                }
                """,
                list(zone_keywords),
            )
            if ok:
                print(f"[TAOBAO] 展开了 {ok} 个折叠区（{zone_keywords}）", flush=True)
            return bool(ok)
        except Exception as e:
            print(f"[TAOBAO] _expand_zone_section 失败: {e}", flush=True)
            return False

    async def _upload_by_zone(
        self,
        page: Any,
        zone_keywords: List[str],
        paths: List[str],
        *,
        max_files: int = 5,
        zone_label: Optional[str] = None,
        needs_expand: bool = False,
    ) -> int:
        """把 paths 上传到 zone_keywords 命中的上传区。

        **批量优先策略**：单次 click 第一个空槽 → file_chooser 一次性 set_files(全部 paths)
        - 淘宝 React 上传组件的 file chooser 通常 `multiple=true`（UI 写「至多可上传5张」）
        - 一次性多张只触发 1 次上传 XHR，避免风控拦截
        - 失败再降级到单张模式

        返回实际上传的张数。
        """
        if not paths:
            return 0
        zone_label = zone_label or "/".join(zone_keywords)
        paths = paths[:max_files]
        print(f"[TAOBAO] zone '{zone_label}': 准备上传 {len(paths)} 张", flush=True)

        # 进入 zone 前清掉残留 dialog（如上一区上传完弹的「上传结果」）+ 模拟人滚到该区慢慢看
        await self._dismiss_floating_dialogs(page)
        await asyncio.sleep(random.uniform(2.5, 4.0))

        # 折叠区需要先展开（白底图 / 商品视频 / 购后视频 / SKU 等）
        if needs_expand:
            await self._expand_zone_section(page, zone_keywords)
            await asyncio.sleep(random.uniform(1.0, 2.0))

        # ─ 策略 0：先试 hidden input[type=file] 一次性多张（最快最不显眼）
        try:
            indexes = await self._find_zone_input_indexes(page, zone_keywords)
            if indexes:
                all_inputs = page.locator('input[type="file"]')
                total = await all_inputs.count()
                if total > 0 and indexes[0] < total:
                    try:
                        await all_inputs.nth(indexes[0]).set_input_files(paths)
                        print(
                            f"[TAOBAO] zone '{zone_label}': hidden input 一次性 {len(paths)} 张 OK",
                            flush=True,
                        )
                        await asyncio.sleep(random.uniform(3.0, 5.0))
                        return len(paths)
                    except Exception as e_h:
                        print(
                            f"[TAOBAO] zone '{zone_label}': hidden input 多张失败 {e_h}，试 file_chooser",
                            flush=True,
                        )
        except Exception:
            pass

        # ─ 策略 1：file_chooser 一次性多张（首选 — 只 click 1 次，几乎不触发风控）
        # 上传前先检风控：如果 baxia/滑块挡着，先阻塞等用户解
        if await self._is_risk_control_visible(page):
            print(f"[TAOBAO] zone '{zone_label}': 上传前检测到风控，等待解决", flush=True)
            if not await self._wait_for_risk_control_resolved(page, max_wait_sec=300):
                print(f"[TAOBAO] zone '{zone_label}': 风控未解，跳过本区", flush=True)
                return 0

        n = await self._upload_by_zone_via_chooser_batch(
            page, zone_keywords, paths, zone_label=zone_label
        )
        # **不再降级单张**：单张模式会 click N 次，必然触发 baxia 风控
        # 即使 batch 只成功了 1 张也好过单张模式触发风控让全部失败
        return n

    async def _upload_by_zone_via_chooser_batch(
        self,
        page: Any,
        zone_keywords: List[str],
        paths: List[str],
        *,
        zone_label: Optional[str] = None,
    ) -> int:
        """淘宝当前发布表单的真实上传路径（已实测确认）：
          1. click 主图区空槽 → sucai-selector iframe 弹出（默认显示「批量导入」空文件夹）
          2. 进 iframe → 切「全部图片」侧栏（让上传后能看到新图）
          3. click「本地上传」→ expect_file_chooser → set_files(多张)
          4. 等淘宝 OSS 上传 + 网格刷新
          5. 在网格中通过 filename 匹配找新上传图，依次 click 应用到主图槽
          6. iframe 通常应用完后自动关闭；不关则 ESC 兜底

        **预加载优先**：如果 self._preloaded 已经覆盖本 zone 的全部 paths，就走
        `_zone_apply_from_preloaded` —— 不再本地上传一次。
        """
        import os as _os
        zone_label = zone_label or "/".join(zone_keywords)

        preloaded = getattr(self, "_preloaded", None) or {}
        if preloaded and paths and all(os.path.abspath(p) in preloaded for p in paths):
            n = await self._zone_apply_from_preloaded(
                page,
                zone_keywords,
                paths,
                zone_label=zone_label,
                preloaded=preloaded,
                multi_select=False,
            )
            if n >= 0:
                print(
                    f"[TAOBAO] zone '{zone_label}': 预加载路径应用 {n}/{len(paths)} 张",
                    flush=True,
                )
                if n > 0:
                    # 和老路径一样做一次区内 img 计数
                    final_count = await self._count_uploaded_in_zone(page, zone_keywords)
                    print(
                        f"[TAOBAO] zone '{zone_label}': 主图槽内最终缩略图 {final_count} 张",
                        flush=True,
                    )
                    return final_count if final_count > 0 else n
                # n==0 走回退
            # n==-1 表明预加载路径遇到问题 → 走下面老的本地上传路径
            print(f"[TAOBAO] zone '{zone_label}': 预加载路径未完成，回退本地上传", flush=True)

        try:
            slot = await self._find_empty_upload_slot_handle(page, zone_keywords)
            if not slot:
                print(f"[TAOBAO] zone '{zone_label}' batch: 没找到空槽", flush=True)
                return 0

            # 1. 模拟真人 hover + click slot
            await self._humanize_click(page, slot)
            try:
                await slot.click(timeout=5000)
            except Exception as e_click_slot:
                print(f"[TAOBAO] zone '{zone_label}': 点上传槽失败 {e_click_slot}", flush=True)
                return 0

            # 2. 等待素材中心 iframe 出现
            print(f"[TAOBAO] zone '{zone_label}': 等待素材中心 iframe 弹出", flush=True)
            sucai_frame = None
            for _ in range(30):
                await asyncio.sleep(0.3)
                for fr in page.frames:
                    src = (fr.url or "")
                    if "sucai-selector" in src or "crs-qn" in src:
                        try:
                            await fr.wait_for_load_state("domcontentloaded", timeout=2000)
                        except Exception:
                            pass
                        sucai_frame = fr
                        break
                if sucai_frame:
                    break
            if not sucai_frame:
                print(f"[TAOBAO] zone '{zone_label}': iframe 没出现（10s 超时）", flush=True)
                return 0
            await asyncio.sleep(random.uniform(1.5, 2.5))

            # 3. 切「全部图片」侧栏（确保上传后能看到新文件）
            try:
                all_btn = sucai_frame.locator('text=/^全部图片$/').first
                if await all_btn.count() > 0:
                    await all_btn.click(timeout=3000)
                    print(f"[TAOBAO] zone '{zone_label}': 切到「全部图片」", flush=True)
                    await asyncio.sleep(random.uniform(0.8, 1.5))
            except Exception as e_all:
                print(f"[TAOBAO] zone '{zone_label}': 切「全部图片」失败 {e_all}（继续）", flush=True)

            # 4. click「本地上传」→ expect_file_chooser → set_files(多张)
            try:
                local_btn = sucai_frame.locator(
                    'button:has-text("本地上传")'
                ).first
                await local_btn.wait_for(state="visible", timeout=8000)
            except Exception as e_btn:
                print(f"[TAOBAO] zone '{zone_label}': 找不到「本地上传」按钮 {e_btn}", flush=True)
                await self._close_sucai_iframe_force(page, sucai_frame)
                return 0
            try:
                async with page.expect_file_chooser(timeout=10000) as fc_info:
                    await asyncio.sleep(random.uniform(0.4, 0.8))
                    await local_btn.click(timeout=5000)
                fc = await fc_info.value
                await asyncio.sleep(random.uniform(0.6, 1.2))
                await fc.set_files(list(paths))
                # OSS 上传 + 网格刷新等够
                wait_sec = max(15.0, min(45.0, 6.0 + 4.0 * len(paths)))
                wait_sec += random.uniform(-0.5, 2.0)
                print(
                    f"[TAOBAO] zone '{zone_label}': 批量 set_files {len(paths)} 张，等 OSS 上传 {wait_sec:.1f}s",
                    flush=True,
                )
                await asyncio.sleep(wait_sec)
            except Exception as e_fc:
                print(f"[TAOBAO] zone '{zone_label}': chooser 异常 {e_fc}", flush=True)
                await self._close_sucai_iframe_force(page, sucai_frame)
                return 0

            # 风控检测
            if await self._is_risk_control_visible(page):
                if not await self._wait_for_risk_control_resolved(page, max_wait_sec=300):
                    await self._close_sucai_iframe_force(page, sucai_frame)
                    return 0

            # 5. 在网格中找新上传的图（通过 filename 前缀匹配），依次 click
            applied = await self._click_uploaded_thumbnails(
                page, sucai_frame, paths, zone_label=zone_label
            )
            print(
                f"[TAOBAO] zone '{zone_label}': 网格中点击应用 {applied}/{len(paths)} 张",
                flush=True,
            )

            # 6. iframe 通常自动关；如未关则强制关
            await asyncio.sleep(random.uniform(1.5, 2.5))
            still = False
            try:
                still = not sucai_frame.is_detached()
                if still:
                    for fr in page.frames:
                        if fr is sucai_frame:
                            still = True
                            break
                    else:
                        still = False
            except Exception:
                still = False
            if still:
                await self._close_sucai_iframe_force(page, sucai_frame)
            await asyncio.sleep(random.uniform(2.0, 3.0))

            # 7. 校验：zone 内 img 数量
            final_count = await self._count_uploaded_in_zone(page, zone_keywords)
            print(
                f"[TAOBAO] zone '{zone_label}': 主图槽内最终缩略图 {final_count} 张",
                flush=True,
            )
            return final_count if final_count > 0 else applied
        except Exception as e:
            print(f"[TAOBAO] zone '{zone_label}' batch 顶层异常: {e}", flush=True)
            return 0

    async def _click_uploaded_thumbnails(
        self,
        page: Any,
        sucai_frame: Any,
        paths: List[str],
        *,
        zone_label: str = "",
    ) -> int:
        """在 sucai-selector iframe 网格中点击刚上传的 N 张图（按上传时间倒序，最新的在前）。

        策略：
          1. 优先：通过 img.src 匹配 filename stem 找精确的卡片（最稳）
          2. 兜底：直接点前 N 个 PicList_pic_imgBox 卡片（假设最新的排在前）
        """
        import os as _os
        # 提取每个 path 的 filename stem
        filename_stems: List[str] = []
        for p in paths:
            base = _os.path.basename(p)
            stem, _ext = _os.path.splitext(base)
            filename_stems.append(stem)

        # 先 dump img.src 看实际 URL 是否含 filename stem，便于精确匹配
        src_hits = await self._find_grid_indexes_by_src(sucai_frame, filename_stems)
        if src_hits:
            print(
                f"[TAOBAO] zone '{zone_label}': src 匹配命中 {len(src_hits)} 张：indexes={src_hits[:10]}",
                flush=True,
            )
        else:
            print(
                f"[TAOBAO] zone '{zone_label}': src 没匹配上，回退到「前 N 张 imgBox」策略",
                flush=True,
            )

        cards_locator = sucai_frame.locator('[class*="PicList_pic_imgBox"]')
        total = await cards_locator.count()
        print(f"[TAOBAO] zone '{zone_label}': 网格中共 {total} 张图", flush=True)
        if total == 0:
            await self._dump_grid_items(sucai_frame, zone_label=zone_label)
            return 0

        # 先压掉「本地上传」触发的隐藏 file input overlay（`next-overlay-wrapper opened` 里的 `<input type="file">`），
        # 否则它会继续 intercept pointer events，点缩略图全超时。
        try:
            neutralised = await sucai_frame.evaluate(
                """
                () => {
                    let n = 0;
                    document.querySelectorAll('input[type="file"]').forEach(el => {
                        try {
                            el.style.pointerEvents = 'none';
                            el.style.position = 'static';
                            el.style.width = '0px';
                            el.style.height = '0px';
                            n++;
                        } catch (_e) {}
                    });
                    // next-overlay-wrapper 的隐形 mask 也可能挡住；若其中没可见内容则禁 pointer
                    document.querySelectorAll('.next-overlay-wrapper.opened').forEach(w => {
                        try {
                            const rect = w.getBoundingClientRect();
                            const hasVisibleChild = Array.from(w.querySelectorAll('*')).some(c => {
                                if (c.tagName === 'INPUT' && c.type === 'file') return false;
                                const r = c.getBoundingClientRect();
                                return r.width > 10 && r.height > 10;
                            });
                            if (!hasVisibleChild) w.style.pointerEvents = 'none';
                        } catch (_e) {}
                    });
                    return n;
                }
                """
            )
            if neutralised:
                print(f"[TAOBAO] zone '{zone_label}': 压掉 {neutralised} 个 file-input overlay", flush=True)
        except Exception as e_neu:
            print(f"[TAOBAO] zone '{zone_label}': overlay 中和失败（继续）{e_neu}", flush=True)

        # 决定要点哪些下标
        target_indexes: List[int] = src_hits if src_hits else list(range(min(len(paths), total)))

        applied = 0
        for idx in target_indexes:
            try:
                card = cards_locator.nth(idx)
                await asyncio.sleep(random.uniform(0.6, 1.2))
                try:
                    await card.scroll_into_view_if_needed(timeout=3000)
                except Exception:
                    pass
                try:
                    await card.click(timeout=4000)
                except Exception:
                    # 如果仍被 intercept，fallback 到 dispatch_event('click') 绕过 pointer-events 检查
                    print(
                        f"[TAOBAO] zone '{zone_label}': 卡片 idx={idx} 常规 click 失败，dispatch_event fallback",
                        flush=True,
                    )
                    await card.dispatch_event("click", timeout=4000)
                applied += 1
                # 等淘宝处理（应用到主图槽 + 可能 iframe 自动关）
                await asyncio.sleep(random.uniform(1.5, 2.5))
                try:
                    if sucai_frame.is_detached():
                        print(
                            f"[TAOBAO] zone '{zone_label}': iframe 已自动关闭于第 {applied} 张",
                            flush=True,
                        )
                        return applied
                except Exception:
                    return applied
            except Exception as e:
                print(
                    f"[TAOBAO] zone '{zone_label}': 点第 {applied + 1} 张缩略图(idx={idx})失败: {e}",
                    flush=True,
                )
                continue
        return applied

    async def _find_grid_indexes_by_src(
        self, sucai_frame: Any, stems: List[str]
    ) -> List[int]:
        """通过 img.src 包含 filename stem 找 PicList_pic_imgBox 索引。"""
        try:
            return await sucai_frame.evaluate(
                """
                (stems) => {
                    const cards = Array.from(document.querySelectorAll('[class*="PicList_pic_imgBox"]'));
                    const result = [];
                    for (const stem of stems) {
                        for (let i = 0; i < cards.length; i++) {
                            const img = cards[i].querySelector('img');
                            if (!img) continue;
                            const src = (img.src || '').toLowerCase();
                            const alt = (img.alt || '').toLowerCase();
                            if (src.includes(stem.toLowerCase()) || alt.includes(stem.toLowerCase())) {
                                if (!result.includes(i)) result.push(i);
                                break;
                            }
                        }
                    }
                    return result;
                }
                """,
                stems,
            )
        except Exception:
            return []

    async def _dismiss_upload_result_dialog(self, page: Any) -> int:
        """淘宝 sucai 单文件上传完成后，会在**主页面**弹一个「上传结果」dialog，
        它是 `.next-overlay-wrapper.opened`，会挡住 iframe 内的「本地上传」按钮 + 阻塞 iframe 网格刷新。

        策略：所有主页面上不含 sucai iframe 的 `.next-overlay-wrapper.opened` 视为临时 dialog，
        优先点「确定/完成/知道了/关闭」按钮；不行就直接 display:none + pointerEvents:none 硬隐藏。
        返回 (hidden_count, sample_header)。
        """
        try:
            result = await page.evaluate(
                """
                () => {
                    let closed = 0;
                    const details = [];
                    const wrappers = document.querySelectorAll('.next-overlay-wrapper.opened');
                    for (const w of wrappers) {
                        const hasSucaiFrame = !!w.querySelector('iframe[src*="sucai-selector"], iframe#mainImagesGroup');
                        if (hasSucaiFrame) continue;  // 这是 sucai iframe 的壳，别碰
                        // 尝试点确认按钮
                        let clicked = false;
                        const btns = Array.from(w.querySelectorAll('button'));
                        for (const b of btns) {
                            const t = (b.textContent || '').trim();
                            if (['确定', '完成', '知道了', '关闭', '好的', 'OK'].some(k => t && t.includes(k))) {
                                try { b.click(); clicked = true; break; } catch (_e) {}
                            }
                        }
                        if (!clicked) {
                            const close = w.querySelector('.next-dialog-close, [class*="close"], [aria-label*="关闭"]');
                            if (close) { try { close.click(); clicked = true; } catch (_e) {} }
                        }
                        // 无论是否点到，都 hide 兜底：避免下一轮还挡着
                        try {
                            w.style.display = 'none';
                            w.style.pointerEvents = 'none';
                        } catch (_e) {}
                        const header = w.querySelector('.next-dialog-header, [role="heading"]');
                        details.push((header ? (header.textContent || '').trim() : '') || '(no header)');
                        closed++;
                    }
                    return { closed, details };
                }
                """
            )
            return result.get("closed", 0) if isinstance(result, dict) else 0
        except Exception:
            return 0

    async def _snapshot_grid_oids(self, sucai_frame: Any) -> List[str]:
        """dump 当前 iframe 全部图片网格里每张 imgBox 的 OID（`O1CN01..`），按 DOM 顺序返回。
        未抽出 OID 的位置以 "" 占位，保序。
        """
        try:
            return await sucai_frame.evaluate(
                """
                () => {
                    const cards = Array.from(document.querySelectorAll('[class*="PicList_pic_imgBox"]'));
                    const re = /(O1CN[0-9A-Za-z]+)/i;
                    return cards.map(card => {
                        const img = card.querySelector('img');
                        const s = img ? (img.src || '') : '';
                        const m = s.match(re);
                        return m ? m[1] : '';
                    });
                }
                """
            )
        except Exception:
            return []

    async def _find_grid_index_by_oid(self, sucai_frame: Any, oid: str) -> int:
        """在 iframe 里按 OID 精确定位 imgBox 下标；-1 表示没找到。"""
        if not oid:
            return -1
        try:
            return await sucai_frame.evaluate(
                """
                (oid) => {
                    const cards = Array.from(document.querySelectorAll('[class*="PicList_pic_imgBox"]'));
                    for (let i = 0; i < cards.length; i++) {
                        const img = cards[i].querySelector('img');
                        if (!img) continue;
                        if ((img.src || '').includes(oid)) return i;
                    }
                    return -1;
                }
                """,
                oid,
            )
        except Exception:
            return -1

    async def _bulk_preload_to_sucai(
        self,
        page: Any,
        all_paths: List[str],
        *,
        platform: str = "taobao",
        account: str = "default",
        trigger_zone_keywords: Optional[List[str]] = None,
        per_batch: int = 8,
    ) -> Dict[str, str]:
        """一次性把本轮要用到的所有本地图预上传到素材库，返回 `abs_path → remote_oid` 映射。

        策略：
          1. 全部路径算 md5，查 sqlite 缓存；命中的直接给 OID
          2. 未命中的按 per_batch 分批：
             - click 主图区空槽 → iframe → 切「全部图片」
             - 记录上传前全部 imgBox 的 OID set
             - 点「本地上传」→ set_files(batch) → 等 OSS
             - 记录上传后的 OID 列表；新增 OID 按 DOM 顺序与 batch 顺序一一对应
             - 写回 sqlite
             - ESC 关闭 iframe（不 apply 到任何槽）
          3. 全部完成后返回 `{abs_path: oid}`
        """
        result: Dict[str, str] = {}
        if not all_paths:
            return result

        # 规范化 + 去重（同一张图可能在多个 zone 出现）
        norm_paths: List[str] = []
        seen = set()
        for p in all_paths:
            if not p:
                continue
            ap = os.path.abspath(p)
            if ap in seen:
                continue
            if not os.path.isfile(ap):
                print(f"[SUCAI-PRELOAD] 文件不存在，跳过: {ap}", flush=True)
                continue
            seen.add(ap)
            norm_paths.append(ap)

        if not norm_paths:
            return result

        # 1) 算 md5 + 查缓存
        path_to_md5: Dict[str, str] = {}
        for ap in norm_paths:
            try:
                path_to_md5[ap] = sucai_cache.compute_md5(ap)
            except Exception as e:
                print(f"[SUCAI-PRELOAD] md5 计算失败，跳过 {ap}: {e}", flush=True)

        cached = sucai_cache.bulk_lookup(platform, account, path_to_md5.values())
        need_upload: List[str] = []
        for ap, md5 in path_to_md5.items():
            entry = cached.get(md5)
            if entry and entry.remote_oid:
                result[ap] = entry.remote_oid
            else:
                need_upload.append(ap)

        print(
            f"[SUCAI-PRELOAD] 总 {len(norm_paths)} 张，缓存命中 {len(result)} 张，需上传 {len(need_upload)} 张",
            flush=True,
        )
        # 更新 last_used_at
        if result:
            sucai_cache.mark_used(platform, account, [path_to_md5[ap] for ap in result.keys()])

        if not need_upload:
            return result

        trigger_zone_keywords = trigger_zone_keywords or [
            "1:1主图", "1：1主图", "1:1 主图",
            "3:4主图", "3：4主图",
            "主图", "商品主图",
        ]

        # 2) 触发 iframe：需要一个空槽
        await self._dismiss_floating_dialogs(page)
        slot = None
        for kw in trigger_zone_keywords:
            slot = await self._find_empty_upload_slot_handle(page, [kw])
            if slot:
                print(f"[SUCAI-PRELOAD] 用 '{kw}' 空槽触发素材库 iframe", flush=True)
                break
        if not slot:
            print("[SUCAI-PRELOAD] 没找到任何可触发 iframe 的空槽，放弃预上传（各 zone 走老路径）", flush=True)
            return result

        # 分批上传（一个 iframe 生命周期内逐张 `本地上传` —— 每张完成后
        # 读网格 idx=0 的 OID 即该文件的 OID；无论淘宝返回新 OID 还是 dedup 命中
        # 已有 OID，都能正确配对，且保留 mtime DESC 语义）
        pm = self._pace_mult()
        batches = [need_upload[i : i + per_batch] for i in range(0, len(need_upload), per_batch)]
        print(
            f"[SUCAI-PRELOAD] 分 {len(batches)} 批、共 {len(need_upload)} 张单文件串行（pace={pm:.2f}）",
            flush=True,
        )

        for bi, batch in enumerate(batches):
            print(f"[SUCAI-PRELOAD] 批 {bi + 1}/{len(batches)} 开始（{len(batch)} 张）", flush=True)

            # 每批都需要新触发 iframe（因为上批结束我们 ESC 了）
            if bi > 0:
                await asyncio.sleep(random.uniform(3.0, 5.0) * pm)
                # 先清掉残留「上传结果」+ 其它悬浮 dialog，确保能找到空槽
                try:
                    await self._dismiss_upload_result_dialog(page)
                except Exception:
                    pass
                try:
                    await self._dismiss_floating_dialogs(page)
                except Exception:
                    pass
                slot = None
                for kw in trigger_zone_keywords:
                    slot = await self._find_empty_upload_slot_handle(page, [kw])
                    if slot:
                        break
                if not slot:
                    print(f"[SUCAI-PRELOAD] 批 {bi + 1} 找不到空槽，中止", flush=True)
                    break

            try:
                await self._humanize_click(page, slot)
                await slot.click(timeout=5000)
            except Exception as e:
                print(f"[SUCAI-PRELOAD] 批 {bi + 1} 点空槽失败: {e}", flush=True)
                break

            sucai_frame = await self._wait_sucai_iframe(page, label=f"SUCAI-PRELOAD批{bi + 1}")
            if not sucai_frame:
                print(f"[SUCAI-PRELOAD] 批 {bi + 1} iframe 没出现，中止", flush=True)
                break
            await asyncio.sleep(random.uniform(1.5, 2.5) * pm)

            try:
                all_btn = sucai_frame.locator('text=/^全部图片$/').first
                if await all_btn.count() > 0:
                    await all_btn.click(timeout=3000)
                    await asyncio.sleep(random.uniform(0.8, 1.5) * pm)
            except Exception:
                pass

            try:
                local_btn = sucai_frame.locator('button:has-text("本地上传")').first
                await local_btn.wait_for(state="visible", timeout=8000)
            except Exception as e:
                print(f"[SUCAI-PRELOAD] 批 {bi + 1} 找不到「本地上传」: {e}", flush=True)
                await self._close_sucai_iframe_force(page, sucai_frame)
                break

            # 记录 iframe 进来时的 top OID，用来检测后续每张上传是否完成
            oids_init = await self._snapshot_grid_oids(sucai_frame)
            prev_top = oids_init[0] if oids_init else ""
            print(
                f"[SUCAI-PRELOAD] 批 {bi + 1} init top={prev_top} grid_len={len(oids_init)}",
                flush=True,
            )

            matched = 0
            for fi, fp in enumerate(batch):
                print(
                    f"[SUCAI-PRELOAD] 批 {bi + 1} [{fi + 1}/{len(batch)}] 上传 {os.path.basename(fp)}",
                    flush=True,
                )
                # 每张前先关掉上一张的「上传结果」dialog（如果存在），不然 click 会被遮挡
                try:
                    n_closed = await self._dismiss_upload_result_dialog(page)
                    if n_closed:
                        print(f"[SUCAI-PRELOAD] 关掉 {n_closed} 个「上传结果」dialog", flush=True)
                        await asyncio.sleep(random.uniform(0.6, 1.0))
                except Exception:
                    pass

                try:
                    async with page.expect_file_chooser(timeout=10000) as fc_info:
                        await asyncio.sleep(random.uniform(0.45, 0.9) * pm)
                        await local_btn.click(timeout=5000)
                    fc = await fc_info.value
                    await asyncio.sleep(random.uniform(0.5, 0.9) * pm)
                    await fc.set_files([fp])
                except Exception as e:
                    print(f"[SUCAI-PRELOAD] 批 {bi + 1} 文件 {fi + 1} chooser 异常: {e}", flush=True)
                    # 再尝试关一次 dialog 再跳过
                    try:
                        await self._dismiss_upload_result_dialog(page)
                    except Exception:
                        pass
                    continue

                # 等 top OID 变化（最多 60s）：淘宝 OSS 回调后会把该文件置于 idx 0
                new_top = ""
                poll_start = asyncio.get_event_loop().time()
                while asyncio.get_event_loop().time() - poll_start < 60.0 * pm:
                    # 主动关「上传结果」dialog —— 它可能会阻塞网格刷新 + 挡下一次 click
                    try:
                        await self._dismiss_upload_result_dialog(page)
                    except Exception:
                        pass
                    try:
                        all_btn_after = sucai_frame.locator('text=/^全部图片$/').first
                        if await all_btn_after.count() > 0:
                            await all_btn_after.click(timeout=1500)
                    except Exception:
                        pass
                    await asyncio.sleep(1.5)
                    oids_now = await self._snapshot_grid_oids(sucai_frame)
                    top = oids_now[0] if oids_now else ""
                    if top and top != prev_top:
                        new_top = top
                        break
                if not new_top:
                    print(
                        f"[SUCAI-PRELOAD] 批 {bi + 1} 文件 {fi + 1} 超时：top 未变化 (prev_top={prev_top})",
                        flush=True,
                    )
                    # 风控检测
                    if await self._is_risk_control_visible(page):
                        if not await self._wait_for_risk_control_resolved(page, max_wait_sec=300):
                            break
                    continue

                prev_top = new_top
                matched += 1

                # 回写 sqlite + result
                ap = fp
                md5 = path_to_md5.get(ap) or ""
                result[ap] = new_top
                try:
                    stat = os.stat(ap)
                except Exception:
                    stat = None
                try:
                    remote_src_full = await sucai_frame.evaluate(
                        """
                        (oid) => {
                            const imgs = Array.from(document.querySelectorAll('img'));
                            for (const img of imgs) {
                                if ((img.src || '').includes(oid)) return img.src;
                            }
                            return '';
                        }
                        """,
                        new_top,
                    )
                except Exception:
                    remote_src_full = ""
                try:
                    sucai_cache.put(
                        platform,
                        account,
                        md5,
                        remote_src_full or new_top,
                        filename=os.path.basename(ap),
                        file_size=stat.st_size if stat else None,
                    )
                except Exception as e:
                    print(f"[SUCAI-PRELOAD] sqlite put 失败 {e}", flush=True)

                await asyncio.sleep(random.uniform(0.6, 1.2) * pm)

            print(
                f"[SUCAI-PRELOAD] 批 {bi + 1} 完成：{matched}/{len(batch)} 张已配对 OID",
                flush=True,
            )

            # ESC 关 iframe —— 关键：**不**应用到触发槽
            try:
                await page.keyboard.press("Escape")
                await asyncio.sleep(random.uniform(0.8, 1.3))
            except Exception:
                pass
            try:
                still = not sucai_frame.is_detached()
            except Exception:
                still = False
            if still:
                await self._close_sucai_iframe_force(page, sucai_frame)
            await asyncio.sleep(random.uniform(1.5, 2.5))

        print(
            f"[SUCAI-PRELOAD] 完成：{len(result)}/{len(norm_paths)} 张已拿到 OID",
            flush=True,
        )
        return result

    async def _zone_apply_from_preloaded(
        self,
        page: Any,
        zone_keywords: List[str],
        paths: List[str],
        *,
        zone_label: str,
        preloaded: Dict[str, str],
        multi_select: bool = False,
    ) -> int:
        """预加载模式下：打开 iframe → 切「全部图片」→ 按 OID 定位 imgBox → 点击应用到当前 zone。

        multi_select=True 时走详情图的「多选 + 确定」路径；否则走主图区单点路径。
        返回成功 apply 的张数（**必须全部命中，否则回 -1 告诉上游降级**）。
        """
        # 收集 OID 列表（保序）
        oids: List[str] = []
        missing_paths: List[str] = []
        for p in paths:
            ap = os.path.abspath(p)
            oid = preloaded.get(ap)
            if oid:
                oids.append(oid)
            else:
                missing_paths.append(p)
        if missing_paths:
            print(
                f"[TAOBAO] zone '{zone_label}': 预加载缺 {len(missing_paths)} 张 OID，走本地上传回退",
                flush=True,
            )
            return -1
        if not oids:
            return 0

        slot = await self._find_empty_upload_slot_handle(page, zone_keywords)
        if not slot:
            print(f"[TAOBAO] zone '{zone_label}': 预加载模式找不到空槽", flush=True)
            return 0

        try:
            await self._humanize_click(page, slot)
            await slot.click(timeout=5000)
        except Exception as e:
            print(f"[TAOBAO] zone '{zone_label}': 预加载模式点空槽失败 {e}", flush=True)
            return 0

        sucai_frame = await self._wait_sucai_iframe(page, label=zone_label)
        if not sucai_frame:
            return 0
        await asyncio.sleep(random.uniform(1.2, 2.0))

        try:
            all_btn = sucai_frame.locator('text=/^全部图片$/').first
            if await all_btn.count() > 0:
                await all_btn.click(timeout=3000)
                await asyncio.sleep(random.uniform(0.8, 1.3))
        except Exception:
            pass

        # 按 OID 查每张图的 index
        target_indexes: List[int] = []
        for oid in oids:
            idx = await self._find_grid_index_by_oid(sucai_frame, oid)
            if idx < 0:
                print(
                    f"[TAOBAO] zone '{zone_label}': OID {oid} 未在网格中命中，放弃预加载路径",
                    flush=True,
                )
                # 关 iframe 再降级
                await self._close_sucai_iframe_force(page, sucai_frame)
                return -1
            target_indexes.append(idx)

        print(
            f"[TAOBAO] zone '{zone_label}': 预加载 OID→index 全部命中 {target_indexes}",
            flush=True,
        )

        # 压掉潜在的 file-input overlay（可能也没有，但压一下没坏处）
        try:
            await sucai_frame.evaluate(
                """
                () => {
                    document.querySelectorAll('input[type="file"]').forEach(el => {
                        try { el.style.pointerEvents = 'none'; } catch (_e) {}
                    });
                }
                """
            )
        except Exception:
            pass

        cards_locator = sucai_frame.locator('[class*="PicList_pic_imgBox"]')

        if multi_select:
            # 详情图路径：多选勾 + 确定
            clicked = 0
            last_n = 0
            for step, idx in enumerate(target_indexes):
                try:
                    card = cards_locator.nth(idx)
                    try:
                        await card.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    ok = await self._multi_select_click_card_at_index(sucai_frame, idx)
                    if ok:
                        clicked += 1
                        await asyncio.sleep(random.uniform(0.35, 0.6))
                        n = await self._read_iframe_confirm_count(sucai_frame)
                        if n is not None and n > last_n:
                            last_n = n
                except Exception as e:
                    print(f"[TAOBAO] zone '{zone_label}': 预加载多选 idx={idx} 异常 {e}", flush=True)
            if clicked > 0:
                confirmed = await self._click_iframe_confirm_button(sucai_frame, expected_count=clicked)
                if not confirmed:
                    print(f"[TAOBAO] zone '{zone_label}': 预加载多选后点确定失败", flush=True)
            # 关 iframe
            await asyncio.sleep(random.uniform(1.5, 2.5))
            try:
                still = not sucai_frame.is_detached()
            except Exception:
                still = False
            if still:
                await self._close_sucai_iframe_force(page, sucai_frame)
            return clicked
        else:
            # 主图区：单点就 apply，逐张点
            applied = 0
            for idx in target_indexes:
                try:
                    card = cards_locator.nth(idx)
                    await asyncio.sleep(random.uniform(0.5, 1.0))
                    try:
                        await card.scroll_into_view_if_needed(timeout=2000)
                    except Exception:
                        pass
                    try:
                        await card.click(timeout=4000)
                    except Exception:
                        await card.dispatch_event("click", timeout=4000)
                    applied += 1
                    await asyncio.sleep(random.uniform(1.2, 2.0))
                    try:
                        if sucai_frame.is_detached():
                            return applied
                    except Exception:
                        return applied
                except Exception as e:
                    print(f"[TAOBAO] zone '{zone_label}': 预加载点 idx={idx} 异常 {e}", flush=True)
            # 主图区如果还没关，强关
            await asyncio.sleep(random.uniform(1.5, 2.5))
            try:
                still = not sucai_frame.is_detached()
            except Exception:
                still = False
            if still:
                await self._close_sucai_iframe_force(page, sucai_frame)
            return applied

    async def _dump_grid_items(self, sucai_frame: Any, *, zone_label: str = ""):
        """调试：dump iframe 网格中前 20 个 img/卡片元素的位置 + 文本，帮助找正确 selector。"""
        try:
            dump = await sucai_frame.evaluate(
                """
                () => {
                    const out = [];
                    // 找所有有 img 子元素的容器
                    const imgs = document.querySelectorAll('img');
                    for (const img of imgs) {
                        const r = img.getBoundingClientRect();
                        if (r.width < 30 || r.height < 30) continue;
                        let parent = img.closest('[class*="card"], [class*="item"], [class*="image"], li, div');
                        if (!parent) parent = img.parentElement;
                        const text = (parent.textContent || '').trim().slice(0, 80);
                        const cls = (parent.className || '').toString().slice(0, 80);
                        out.push({
                            x: Math.round(r.x), y: Math.round(r.y),
                            w: Math.round(r.width), h: Math.round(r.height),
                            parent_cls: cls, text: text,
                        });
                    }
                    return out.slice(0, 20);
                }
                """
            )
            print(f"[TAOBAO] zone '{zone_label}' iframe 网格 img dump: {dump}", flush=True)
        except Exception as e:
            print(f"[TAOBAO] grid dump 失败: {e}", flush=True)

    async def _close_sucai_iframe_force(self, page: Any, sucai_frame: Any) -> bool:
        """强制关闭 iframe：ESC + 点 mask 外；再不行就直接 DOM 移除包裹层，避免残留挡下一 zone 的 click。"""
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(0.8)
            if await self._wait_iframe_gone(page, sucai_frame, max_sec=3):
                return True
        except Exception:
            pass
        try:
            vp = page.viewport_size
            if vp:
                await page.mouse.click(vp["width"] - 20, 10)
                await asyncio.sleep(0.8)
                if await self._wait_iframe_gone(page, sucai_frame, max_sec=3):
                    return True
        except Exception:
            pass
        # 最后的硬兜底：直接把所有 .next-overlay-wrapper.v2.opened 从 DOM 摘掉，防止挡下一 zone。
        try:
            removed = await page.evaluate(
                """
                () => {
                    let n = 0;
                    document.querySelectorAll('.next-overlay-wrapper.v2.opened').forEach(w => {
                        try { w.style.display = 'none'; w.style.pointerEvents = 'none'; n++; } catch (_e) {}
                    });
                    // mainImagesGroup iframe 如果还在，也隐藏
                    document.querySelectorAll('iframe#mainImagesGroup, iframe[src*="sucai-selector"]').forEach(f => {
                        try { f.style.display = 'none'; n++; } catch (_e) {}
                    });
                    return n;
                }
                """
            )
            if removed:
                print(f"[TAOBAO] force-close: DOM 硬隐藏 {removed} 个残留 iframe/overlay", flush=True)
                await asyncio.sleep(0.5)
                if await self._wait_iframe_gone(page, sucai_frame, max_sec=2):
                    return True
                # 即使 is_detached() 仍 False，只要它 display=none 了也不会挡后续 click
                return True
        except Exception as e:
            print(f"[TAOBAO] force-close DOM 硬清失败 {e}", flush=True)
        return False

    async def _close_sucai_iframe(self, page: Any, sucai_frame: Any, *, cancel: bool = False) -> bool:
        """关闭素材中心 iframe（含裁剪确认/上传完成确认）。
        sucai-selector iframe 内可能的按钮文字（按出现顺序优先级）：
        - 上传完成后："使用"/"保留"/"应用"/"选择"/"确认"/"确定"/"完成"/"保存"/"下一步"
        - 取消时："取消"/"关闭"
        """
        if cancel:
            target_texts = ["取消", "关闭"]
        else:
            target_texts = [
                "使用", "保留", "应用", "选择",
                "确认", "确定", "完成", "保存", "下一步", "OK",
            ]
        # 1) 先在 iframe 内找按钮
        for txt in target_texts:
            try:
                btn = sucai_frame.locator(f'button:has-text("{txt}")').last
                if await btn.count() == 0:
                    continue
                # 跳过禁用按钮
                try:
                    if await btn.is_disabled():
                        continue
                except Exception:
                    pass
                await asyncio.sleep(random.uniform(0.4, 0.8))
                await btn.click(timeout=4000)
                if await self._wait_iframe_gone(page, sucai_frame, max_sec=8):
                    return True
            except Exception:
                continue
        # 2) iframe 外（主页面，部分组件确认按钮在 iframe 外）
        for txt in target_texts:
            try:
                btn = page.locator(f'.next-overlay-wrapper.v2.opened button:has-text("{txt}")').last
                if await btn.count() == 0:
                    continue
                await btn.click(timeout=3000)
                if await self._wait_iframe_gone(page, sucai_frame, max_sec=6):
                    return True
            except Exception:
                continue
        # 3) 兜底：ESC + 点 mask 外
        try:
            await page.keyboard.press("Escape")
            await asyncio.sleep(1.0)
            if await self._wait_iframe_gone(page, sucai_frame, max_sec=3):
                return True
        except Exception:
            pass
        # 4) 调试：dump iframe + 父 wrapper 内所有按钮 / 可点元素文字
        try:
            buttons_dump = await sucai_frame.evaluate(
                """
                () => {
                    const out = [];
                    const els = document.querySelectorAll('button, [role="button"], a, [class*="btn"]');
                    for (const e of els) {
                        const t = (e.textContent || '').trim();
                        const cls = (e.className || '').toString().slice(0, 80);
                        const tag = e.tagName;
                        const r = e.getBoundingClientRect();
                        if (r.width < 5 || r.height < 5) continue;
                        if (t.length > 30) continue;
                        out.push({tag, t, cls, x: Math.round(r.x), y: Math.round(r.y)});
                    }
                    return out.slice(0, 40);
                }
                """
            )
            print(f"[TAOBAO] iframe 内可见按钮 dump: {buttons_dump}", flush=True)
        except Exception as e_dump:
            print(f"[TAOBAO] iframe 按钮 dump 失败: {e_dump}", flush=True)
        try:
            outer_dump = await page.evaluate(
                """
                () => {
                    const wrappers = document.querySelectorAll('.next-overlay-wrapper.v2.opened');
                    const out = [];
                    for (const w of wrappers) {
                        const els = w.querySelectorAll('button, [role="button"]');
                        for (const e of els) {
                            const t = (e.textContent || '').trim();
                            const r = e.getBoundingClientRect();
                            if (r.width < 5 || r.height < 5 || t.length > 30) continue;
                            out.push({t, x: Math.round(r.x), y: Math.round(r.y)});
                        }
                    }
                    return out.slice(0, 40);
                }
                """
            )
            print(f"[TAOBAO] iframe 外层 wrapper 内按钮 dump: {outer_dump}", flush=True)
        except Exception:
            pass
        # 5) 强力兜底：直接点页面右上角空白区域（iframe 外）→ 大概率会让 modal 失焦关闭
        try:
            vp = page.viewport_size
            if vp:
                await page.mouse.click(vp["width"] - 20, 10)
                await asyncio.sleep(0.8)
                if await self._wait_iframe_gone(page, sucai_frame, max_sec=3):
                    return True
        except Exception:
            pass
        return False

    async def _wait_iframe_gone(self, page: Any, sucai_frame: Any, max_sec: int = 8) -> bool:
        """等到 iframe 从 page.frames 消失或 detached，或外层 .next-overlay-wrapper.v2 关闭。"""
        for _ in range(int(max_sec * 3)):
            await asyncio.sleep(0.33)
            try:
                if sucai_frame.is_detached():
                    return True
            except Exception:
                return True
            still = False
            for fr in page.frames:
                if fr is sucai_frame:
                    still = True
                    break
            if not still:
                return True
            # 外层 wrapper 已关也算关闭
            try:
                opened = await page.evaluate(
                    """() => !!document.querySelector('.next-overlay-wrapper.v2.opened iframe[src*="sucai-selector"]')"""
                )
                if not opened:
                    return True
            except Exception:
                pass
        return False

    async def _dismiss_floating_dialogs(self, page: Any) -> int:
        """关闭页面上残留的 dialog/弹窗（如「上传结果」「成功提示」「使用须知」等）。
        返回关闭的 dialog 数。
        """
        dismissed = 0
        for _ in range(4):
            try:
                closed = await page.evaluate(
                    """
                    () => {
                        let n = 0;
                        // 1) 找带 next-dialog-header 的 dialog
                        const dialogs = document.querySelectorAll('.next-overlay-wrapper.opened, .next-dialog');
                        for (const d of dialogs) {
                            // 跳过 sucai-selector iframe wrapper（v2 标记）
                            if (d.classList.contains('v2')) continue;
                            // 找 X 关闭按钮
                            const closeBtn = d.querySelector('.next-dialog-close, [aria-label="Close"], [class*="close-btn"]');
                            if (closeBtn) {
                                closeBtn.click();
                                n++;
                                continue;
                            }
                            // 找 footer 的「确定」「关闭」「我知道了」按钮
                            const footer = d.querySelector('.next-dialog-footer, .next-dialog-body');
                            if (footer) {
                                const buttons = footer.querySelectorAll('button');
                                for (const b of buttons) {
                                    const t = (b.textContent || '').trim();
                                    if (t === '确定' || t === '关闭' || t === '我知道了' || t === '知道了' || t === '确认') {
                                        b.click();
                                        n++;
                                        break;
                                    }
                                }
                            }
                        }
                        return n;
                    }
                    """
                )
                if not closed:
                    break
                dismissed += int(closed)
                await asyncio.sleep(0.6)
            except Exception:
                break
        if dismissed > 0:
            print(f"[TAOBAO] 清理掉 {dismissed} 个浮动 dialog", flush=True)
        return dismissed

    async def _count_uploaded_in_zone(self, page: Any, zone_keywords: List[str]) -> int:
        """统计 zone 容器内已渲染的 img 数（缩略图）。"""
        try:
            n = await page.evaluate(
                """
                (keywords) => {
                    const titleEls = [];
                    const candidates = document.querySelectorAll('span, div, label, dt, h1, h2, h3, h4, p, em, b');
                    for (const el of candidates) {
                        const t = (el.textContent || '').trim();
                        if (!t || t.length > 30) continue;
                        for (const kw of keywords) {
                            if (t === kw || t === kw + ' *' || t === '* ' + kw) {
                                titleEls.push(el);
                                break;
                            }
                        }
                    }
                    if (!titleEls.length) return 0;
                    for (const titleEl of titleEls) {
                        let p = titleEl;
                        for (let k = 0; k < 14 && p; k++) {
                            p = p.parentElement;
                            if (!p) break;
                            const slots = p.querySelectorAll('.drag-item, [class*="drag-item"]');
                            if (slots.length === 0) continue;
                            let imgs = 0;
                            for (const s of slots) if (s.querySelector('img')) imgs++;
                            return imgs;
                        }
                    }
                    return 0;
                }
                """,
                list(zone_keywords),
            )
            return int(n or 0)
        except Exception:
            return 0

    async def _find_empty_upload_slot_handle(
        self, page: Any, zone_keywords: List[str]
    ) -> Any:
        """在区域容器里找下一个空的 `.drag-item`（textContent 含「上传图片」且无 img 子元素）
        并 scrollIntoView。返回 ElementHandle 或 None。
        """
        try:
            handle = await page.evaluate_handle(
                """
                (keywords) => {
                    const titleEls = [];
                    const candidates = document.querySelectorAll('span, div, label, dt, h1, h2, h3, h4, p, em, b');
                    for (const el of candidates) {
                        const t = (el.textContent || '').trim();
                        if (!t || t.length > 30) continue;
                        for (const kw of keywords) {
                            if (t === kw || t === kw + ' *' || t === '* ' + kw) {
                                titleEls.push(el);
                                break;
                            }
                        }
                    }
                    if (!titleEls.length) return null;
                    for (const titleEl of titleEls) {
                        let p = titleEl;
                        for (let k = 0; k < 14 && p; k++) {
                            p = p.parentElement;
                            if (!p) break;
                            const slots = p.querySelectorAll('.drag-item, [class*="drag-item"]');
                            if (slots.length === 0) continue;
                            for (const slot of slots) {
                                const txt = (slot.textContent || '').trim();
                                if (!txt.includes('上传图片')) continue;
                                if (slot.querySelector('img')) continue;  // 已上传
                                const r = slot.getBoundingClientRect();
                                if (r.width < 5 || r.height < 5) continue;
                                slot.scrollIntoView({block: 'center', behavior: 'instant'});
                                return slot;
                            }
                            // 容器找到了但没有空槽（已满）
                            return null;
                        }
                    }
                    return null;
                }
                """,
                list(zone_keywords),
            )
            elem = handle.as_element() if handle else None
            return elem
        except Exception as e:
            logger.warning("[TAOBAO] _find_empty_upload_slot_handle %s failed: %s", zone_keywords, e)
            return None

    async def _is_risk_control_visible(self, page: Any) -> bool:
        """检测淘宝滑块验证码 / 风控弹窗是否出现。
        覆盖：nocaptcha / baxia / 普通滑块 / 安全验证文字
        """
        try:
            return bool(await page.evaluate(
                """
                () => {
                    // 1. baxia（淘宝/天猫主要风控弹窗 — 整页 mask 挡 click）
                    const baxia = document.querySelector(
                        '.baxia-dialog, .baxia-dialog-mask, #baxia-dialog-content, [class*="baxia-dialog"]'
                    );
                    if (baxia) {
                        const r = baxia.getBoundingClientRect();
                        if (r.width > 50 && r.height > 50) return true;
                    }
                    // 2. nocaptcha 滑块
                    if (document.querySelector(
                        '#nc_1_n1z, #nc_1_wrapper, .nc-container, [id*="nocaptcha"], [id*="nc_1_"]'
                    )) return true;
                    // 3. 文字提示（避免把页脚/帮助里的「网络异常」等误判为验证码）
                    const text = (document.body && document.body.innerText) || '';
                    const strong = [
                        '请拖动下方滑块', '请按住滑块', '拖动滑块完成验证',
                        '通过验证以确保', '请向右拖动', '拼图验证', '旋转图片',
                        '请完成安全验证', '完成拼图验证',
                    ];
                    for (const kw of strong) {
                        if (text.includes(kw)) return true;
                    }
                    if (text.includes('滑块') && (text.includes('验证') || text.includes('安全'))) return true;
                    if (text.includes('nocaptcha') || text.includes('NECaptcha')) return true;
                    return false;
                }
                """
            ))
        except Exception:
            return False

    async def _wait_for_risk_control_resolved(
        self, page: Any, max_wait_sec: int = 300
    ) -> bool:
        """检测到风控时阻塞等用户人工解滑块。每 5s 轮询一次。"""
        if not await self._is_risk_control_visible(page):
            return True
        print(
            f"[TAOBAO] [RISK] 检测到风控（baxia/滑块）— 请在浏览器手动完成验证，"
            f"最多等 {max_wait_sec}s",
            flush=True,
        )
        deadline = asyncio.get_event_loop().time() + max_wait_sec
        i = 0
        while asyncio.get_event_loop().time() < deadline:
            await asyncio.sleep(5)
            i += 1
            if i % 6 == 0:
                remain = int(deadline - asyncio.get_event_loop().time())
                print(f"[TAOBAO] 风控等待中... 剩余 {remain}s", flush=True)
            if not await self._is_risk_control_visible(page):
                print("[TAOBAO] [OK] 风控已解，继续 — 额外缓冲 10s 让页面稳定", flush=True)
                await asyncio.sleep(10)
                return True
        print(f"[TAOBAO] [TIMEOUT] 风控等待超时（{max_wait_sec}s）", flush=True)
        return False

    async def _humanize_click(self, page: Any, slot: Any) -> None:
        """模拟真人点击：hover → 抖动 → 短停顿 → click。"""
        try:
            await slot.scroll_into_view_if_needed(timeout=3000)
        except Exception:
            pass
        await asyncio.sleep(random.uniform(0.4, 0.9))
        try:
            box = await slot.bounding_box()
        except Exception:
            box = None
        if box:
            cx = box["x"] + box["width"] / 2 + random.uniform(-8, 8)
            cy = box["y"] + box["height"] / 2 + random.uniform(-6, 6)
            try:
                await page.mouse.move(cx + random.uniform(-30, 30), cy + random.uniform(-25, 25))
                await asyncio.sleep(random.uniform(0.15, 0.35))
                await page.mouse.move(cx, cy, steps=random.randint(6, 12))
            except Exception:
                pass
        await asyncio.sleep(random.uniform(0.3, 0.7))

    async def _upload_by_zone_via_chooser(
        self,
        page: Any,
        zone_keywords: List[str],
        paths: List[str],
        *,
        zone_label: Optional[str] = None,
    ) -> int:
        """逐张上传，模拟真人节奏：
          - 找空槽 → scrollIntoView → 短停顿
          - 鼠标先 move 到附近抖一下 → move 到中心 → 停顿 → click（expect_file_chooser）
          - set_files
          - 上传后随机 sleep 5-9s（模拟人浏览缩略图）
          - 每 3 张做一次"歇手" 4-7s
          - 每张前先检测风控：如果触发滑块就阻塞等用户解
        """
        zone_label = zone_label or "/".join(zone_keywords)
        # 进入 zone 前停一下（模拟滚到这一区慢慢看）
        await asyncio.sleep(random.uniform(2.5, 4.5))
        uploaded = 0
        for i, p in enumerate(paths):
            # 风控检测（上一张可能触发了）
            if not await self._wait_for_risk_control_resolved(page, max_wait_sec=180):
                break
            try:
                slot = await self._find_empty_upload_slot_handle(page, zone_keywords)
                if not slot:
                    if i == 0:
                        logger.warning("[TAOBAO] zone %r: 没找到任何空上传槽", zone_label)
                    else:
                        logger.info("[TAOBAO] zone %r: 已上传 %d 张，无更多空槽", zone_label, uploaded)
                    break
                # 模拟人扫视到位 + 抖动
                await self._humanize_click(page, slot)
                try:
                    async with page.expect_file_chooser(timeout=10000) as fc_info:
                        await slot.click(timeout=5000)
                    fc = await fc_info.value
                    # 模拟人选完图片到 confirm 的迟疑
                    await asyncio.sleep(random.uniform(0.6, 1.2))
                    await fc.set_files(p)
                    uploaded += 1
                    logger.info(
                        "[TAOBAO] zone %r 上传 %d/%d: %s",
                        zone_label, uploaded, len(paths), p[-60:],
                    )
                    # 上传后随机停（不固定 2.5s）
                    base = random.uniform(5.0, 8.5)
                    await asyncio.sleep(base)
                    # 每 3 张歇一下
                    if uploaded % 3 == 0:
                        rest = random.uniform(4.0, 7.0)
                        logger.info("[TAOBAO] zone %r 已传 %d 张，歇 %.1fs", zone_label, uploaded, rest)
                        await asyncio.sleep(rest)
                except Exception as e_click:
                    logger.info(
                        "[TAOBAO] zone %r 第 %d 张 file_chooser 失败 (%s)，跳过",
                        zone_label, i + 1, e_click,
                    )
                    # 失败则也歇一下，再继续
                    await asyncio.sleep(random.uniform(3.0, 5.0))
            except Exception as e:
                logger.warning("[TAOBAO] zone %r 第 %d 张异常: %s", zone_label, i + 1, e)
                break
        # 离开 zone 时也停一下
        await asyncio.sleep(random.uniform(2.0, 4.0))
        return uploaded

    async def _fill_textarea_by_keywords(
        self, page: Any, keywords: List[str], value: str
    ) -> bool:
        """找最近含关键词的 label/标题 → 向下找 textarea 填值。
        用于卖点 / 购买须知 / 商品亮点 等多行文本字段。
        """
        if not value:
            return False
        try:
            ok = await page.evaluate(
                """
                ({keywords, value}) => {
                    const matches = (text) => {
                        const t = (text || '').trim();
                        if (!t || t.length > 30) return false;
                        return keywords.some(kw => t === kw || t === kw + ' *' || t === '* ' + kw || t.includes(kw));
                    };
                    const titleEls = Array.from(document.querySelectorAll('span, label, div, dt'))
                        .filter(e => matches(e.textContent));
                    for (const titleEl of titleEls) {
                        let p = titleEl;
                        for (let k = 0; k < 8 && p; k++) {
                            p = p.parentElement;
                            if (!p) break;
                            const ta = p.querySelector('textarea');
                            if (ta) {
                                ta.focus();
                                const setter = Object.getOwnPropertyDescriptor(
                                    window.HTMLTextAreaElement.prototype, 'value'
                                );
                                setter.set.call(ta, value);
                                ta.dispatchEvent(new Event('input', { bubbles: true }));
                                ta.dispatchEvent(new Event('change', { bubbles: true }));
                                ta.blur();
                                return true;
                            }
                        }
                    }
                    return false;
                }
                """,
                {"keywords": list(keywords), "value": str(value)},
            )
            if ok:
                logger.info("[TAOBAO] textarea filled by keywords=%s value=%r", keywords, value[:80])
            return bool(ok)
        except Exception as e:
            logger.warning("[TAOBAO] _fill_textarea_by_keywords %s failed: %s", keywords, e)
            return False

    # ------------------------------------------------------------------
    # 老 publish 接口（保留兼容）
    # ------------------------------------------------------------------

    async def publish(
        self,
        page: Any,
        file_path: str,
        title: str,
        description: str,
        tags: str,
        options: Optional[Dict[str, Any]] = None,
        cover_path: Optional[str] = None,
    ) -> Dict[str, Any]:
        """老接口：仅打开商品创建页（不自动填）。新流程请用 open_product_form。"""
        cat_id = (options or {}).get("cat_id") or (options or {}).get("taobao_cat_id")
        try:
            await page.goto(self.product_add_url(cat_id), wait_until="domcontentloaded", timeout=30000)
            await asyncio.sleep(3)
            url = (page.url or "").lower()
            if "login" in url or "passport" in url:
                return {"ok": False, "error": "登录已过期，请重新登录淘宝卖家中心"}
            return {
                "ok": True,
                "message": "已打开淘宝商品创建页面，请检查后手动填写并发布",
                "url": page.url,
            }
        except Exception as e:
            logger.exception("[TAOBAO] publish failed")
            return {"ok": False, "error": str(e)}

    @staticmethod
    def _pace_mult() -> float:
        """环境变量 TAOBAO_PACE_MULT：拉长各区间 sleep（默认 1，建议风控严时 1.5～2.5）。"""
        try:
            m = float(os.environ.get("TAOBAO_PACE_MULT", "1") or "1")
        except ValueError:
            return 1.0
        return max(0.5, min(6.0, m))

    async def _warmup_human_like(self, page: Any) -> None:
        """进高敏页后：真实 pointer move + wheel，减轻「无鼠标轨迹 + 纯定时器滚动」特征。"""
        m = self._pace_mult()
        try:
            vp = page.viewport_size
        except Exception:
            vp = None
        w = int((vp or {}).get("width") or 1280)
        h = int((vp or {}).get("height") or 800)
        for _ in range(random.randint(3, 6)):
            x = random.uniform(120.0, max(160.0, w - 120))
            y = random.uniform(100.0, max(140.0, h - 100))
            try:
                await page.mouse.move(x, y, steps=random.randint(10, 22))
            except Exception:
                pass
            await asyncio.sleep(random.uniform(0.1, 0.32) * m)
        for _ in range(random.randint(3, 8)):
            try:
                await page.mouse.wheel(0, random.randint(160, 420))
            except Exception:
                break
            await asyncio.sleep(random.uniform(0.22, 0.5) * m)
        await asyncio.sleep(random.uniform(0.6, 1.4) * m)

    # ------------------------------------------------------------------
    # Phase T2 新接口：完整 listing_payload 自动填充
    # ------------------------------------------------------------------

    @staticmethod
    def _install_dialog_handler(page: Any) -> None:
        """为 page 挂一个 auto-dismiss dialog handler，避免 playwright 驱动处理遗留 dialog 崩溃。"""
        if getattr(page, "_lobster_dialog_handler_installed", False):
            return
        try:
            async def _on_dialog(dlg):
                try:
                    await dlg.dismiss()
                except Exception:
                    pass
            page.on("dialog", lambda d: asyncio.create_task(_on_dialog(d)))
            page._lobster_dialog_handler_installed = True  # type: ignore[attr-defined]
        except Exception:
            pass

    async def open_product_form(
        self,
        page: Any,
        *,
        title: Optional[str] = None,
        price: Optional[str] = None,
        category: Optional[str] = None,  # 旧接口（中文类目名），优先级低于 cat_id
        main_image_paths: Optional[List[str]] = None,  # 老接口：1:1 主图（兼容旧 caller）
        detail_image_paths: Optional[List[str]] = None,
        # ── Phase T2 新增：完整 listing payload ──
        cat_id: Optional[int] = None,
        guide_title: Optional[str] = None,
        brand: Optional[str] = None,
        no_brand: bool = False,  # True 时选"无品牌/无注册商标"
        specs: Optional[Dict[str, str]] = None,
        stock: Optional[int] = None,
        delivery_time: Optional[str] = None,  # "24小时内发货" / "48小时内发货" / "大于48小时发货"
        delivery_location: Optional[str] = None,  # 例 "浙江/金华"
        portrait_image_paths: Optional[List[str]] = None,  # 1440x1920 竖图（3:4 主图区）
        # ── Phase T3：电商详情图全资源接入 ──
        main_square_image_paths: Optional[List[str]] = None,  # 1:1 主图（1440x1440）
        white_bg_image_paths: Optional[List[str]] = None,    # 白底图
        sku_image_paths: Optional[List[str]] = None,          # SKU 规格图（销售规格区，需先建规格）
        selling_points: Optional[List[str]] = None,           # 5 条卖点
        hero_claim: Optional[str] = None,                     # 主推标语
        # ── PDF 完整流程：商机发现入口 ──
        opportunity_id: Optional[int] = None,
        opportunity_type: int = 2,
        # ── 素材库预加载：供 sucai_cache 写入用（去重关键）──
        account_nickname: Optional[str] = None,
    ) -> Dict[str, Any]:
        """打开淘宝商品创建页并自动填充。**不点提交**，留给用户审核。

        URL 优先级：opportunity_id > cat_id > fallback。
        参数全部可选，传哪个填哪个。失败的字段不会阻断整体流程，最后返回 auto_filled 列表。
        """
        try:
            self._install_dialog_handler(page)
            target_url = self.product_add_url(
                cat_id=cat_id, opportunity_id=opportunity_id, opportunity_type=opportunity_type
            )
            # 如果当前 tab 已经在目标 publish.htm（CDP 重跑常见），直接复用，不 goto 避免 beforeunload dialog
            cur = (page.url or "").lower()
            if (
                "item.upload.taobao.com/sell/v2/publish.htm" in cur
                and ("login" not in cur) and ("passport" not in cur)
            ):
                print(f"[TAOBAO] 复用当前已经在发布页的 tab: {page.url[:180]}", flush=True)
            else:
                await page.goto(target_url, wait_until="domcontentloaded", timeout=30000)
            # 硬兜底：清掉一切上一次 run 遗留的 `.next-overlay-wrapper.opened`（含被我们 display:none 的空壳）
            # 这些浮层会挡 zone 底下的「本地上传」点击。正常待机页面不应出现它们。
            try:
                n_purged = await page.evaluate(
                    """() => {
                        let n = 0;
                        document.querySelectorAll('.next-overlay-wrapper.opened, .next-overlay-wrapper').forEach(w => {
                            const has_sucai = !!w.querySelector('iframe[src*="sucai-selector"]');
                            if (has_sucai) return;
                            try { w.remove(); n += 1; } catch(e) {}
                        });
                        // 重置可能被注入的 body.style.pointerEvents / overflow
                        try { document.body.style.pointerEvents = ''; document.body.style.overflow = ''; } catch(e){}
                        return n;
                    }"""
                )
                if n_purged:
                    print(f"[TAOBAO] 启动清理：移除残留 overlay wrapper {n_purged} 个", flush=True)
            except Exception as e:
                print(f"[TAOBAO] 启动清理 overlay 失败（继续）: {e}", flush=True)
            await asyncio.sleep(4 * self._pace_mult())
            await self._warmup_human_like(page)

            url = (page.url or "").lower()
            if "login" in url or "passport" in url:
                return {"ok": False, "error": "登录已过期，请重新登录淘宝卖家中心"}

            # 进表单页第一时间检风控（baxia 通常 3-8s 内出现）
            await asyncio.sleep(3.0 * self._pace_mult())
            if await self._is_risk_control_visible(page):
                if not await self._wait_for_risk_control_resolved(page, max_wait_sec=300):
                    return {
                        "ok": False,
                        "error": "进入表单时遇风控验证，等待超时；请稍后重试或先在浏览器手动完成验证",
                        "url": page.url,
                    }

            # 检查是否进入了"类目为空"提示页
            try:
                body_preview = await page.evaluate("() => (document.body.innerText || '').substring(0, 300)")
            except Exception:
                body_preview = ""
            if "类目为空" in body_preview or "类目错挂" in body_preview:
                return {
                    "ok": False,
                    "need_category": True,
                    "error": "未传 cat_id 或类目不存在；请先在 ecommerce_detail 表单填淘宝类目 ID",
                    "url": page.url,
                }

            # 滚动让所有懒加载组件渲染（淘宝 React 表单上传区是懒加载，必须慢滚一遍）
            try:
                await page.evaluate(
                    """
                    async () => {
                        const delay = ms => new Promise(r => setTimeout(r, ms));
                        // 第 1 遍慢滚到底，让 lazy 组件 mount
                        const total1 = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, 6000);
                        for (let y = 0; y < total1; y += 400) { window.scrollTo(0, y); await delay(220); }
                        await delay(800);
                        // 重新计算高度（懒加载后页面可能变长）
                        const total2 = Math.max(document.body.scrollHeight, document.documentElement.scrollHeight, 8000);
                        for (let y = 0; y < total2; y += 500) { window.scrollTo(0, y); await delay(180); }
                        await delay(500);
                        window.scrollTo(0, 0); await delay(500);
                    }
                    """
                )
                # 显式等待 .drag-item（图片上传槽）出现，最多 8s
                try:
                    await page.wait_for_selector('.drag-item, [class*="drag-item"]', timeout=8000)
                except Exception:
                    pass
            except Exception:
                pass

            filled: List[str] = []
            failed: List[str] = []

            # ── 素材库预加载（把本轮所有要用的本地图 一次性传到「全部图片」，
            # 各 zone 后面只按 OID 点缩略图，不再反复本地上传）──
            square_paths_pre = main_square_image_paths or main_image_paths or []
            preload_paths: List[str] = []
            for group in (
                list(square_paths_pre or [])[:5],
                list(portrait_image_paths or [])[:5],
                list(white_bg_image_paths or [])[:5],
                list(sku_image_paths or [])[:20],
                list(detail_image_paths or [])[:30],
            ):
                for p in group:
                    if p and p not in preload_paths:
                        preload_paths.append(p)

            self._preloaded: Dict[str, str] = {}
            # 预加载默认关闭；需要时显式打开：TAOBAO_SUCAI_PRELOAD=1
            preload_enabled = (os.environ.get("TAOBAO_SUCAI_PRELOAD", "") or "").strip().lower() in (
                "1", "true", "yes", "on",
            )
            if preload_paths and preload_enabled:
                try:
                    acct_key = (account_nickname or "default").strip() or "default"
                    self._preloaded = await self._bulk_preload_to_sucai(
                        page,
                        preload_paths,
                        platform="taobao",
                        account=acct_key,
                    )
                    if self._preloaded:
                        filled.append(f"素材库预加载({len(self._preloaded)}张)")
                except Exception as e:
                    logger.exception("[TAOBAO] preload failed (fallback to per-zone upload)")
                    print(f"[TAOBAO] 素材库预加载失败（降级走每区本地上传）: {e}", flush=True)
                    self._preloaded = {}
            elif preload_paths:
                print(
                    f"[TAOBAO] 素材库预加载已关闭（TAOBAO_SUCAI_PRELOAD=1 可开启实验），走每区本地上传",
                    flush=True,
                )

            # ── 标题（PDF 第 4 步 60 字）──
            if title:
                if await self._fill_text_by_label(page, "宝贝标题", title):
                    filled.append("宝贝标题")
                else:
                    failed.append("宝贝标题")

            # ── 导购标题（PDF 第 5 步 30 字）──
            if guide_title:
                if await self._fill_text_by_label(page, "导购标题", guide_title):
                    filled.append("导购标题")
                else:
                    failed.append("导购标题")

            # ── 品牌（PDF 第 4 步第 2 项）──
            if brand:
                if await self._fill_text_by_label(page, "品牌", brand):
                    filled.append(f"品牌={brand}")
                else:
                    failed.append("品牌")
            elif no_brand:
                # 淘宝实际页面"无品牌/无注册商标"是嵌在文字里的可点击元素，
                # 也可能直接在品牌输入框下拉里有"【无品牌】"选项。两种都试。
                try:
                    clicked = await page.evaluate(
                        """
                        () => {
                            // 1) 找最小可点击元素（避免点到大段说明文字外层 div）
                            const all = document.querySelectorAll('a, button, [role="button"], span[onclick], em, b, u');
                            // 优先级：含"无品牌"且 textContent 短（≤30 字）= 真按钮
                            const candidates = [];
                            for (const el of all) {
                                const t = (el.textContent || '').trim();
                                if (!t) continue;
                                if (!(t.includes('无品牌') || t.includes('无注册商标'))) continue;
                                if (t.length > 30) continue;
                                const r = el.getBoundingClientRect();
                                if (r.width < 5 || r.height < 5) continue;
                                candidates.push({ el, len: t.length });
                            }
                            candidates.sort((a, b) => a.len - b.len);
                            if (candidates.length > 0) {
                                candidates[0].el.click();
                                return true;
                            }
                            // 2) 兜底：找品牌输入框 → focus 触发下拉 → 找含"无品牌"的 option
                            const brandLabels = [...document.querySelectorAll('span, label, div, dt')]
                                .filter(e => (e.textContent || '').trim() === '品牌');
                            for (const lab of brandLabels) {
                                let p = lab;
                                for (let i = 0; i < 6 && p; i++) {
                                    p = p.parentElement;
                                    if (!p) break;
                                    const inp = p.querySelector('input');
                                    if (inp) {
                                        inp.focus();
                                        inp.click();
                                        return 'opened_dropdown';
                                    }
                                }
                            }
                            return false;
                        }
                        """
                    )
                    if clicked == "opened_dropdown":
                        # 等弹层 → 点选项
                        await asyncio.sleep(0.6)
                        picked = await page.evaluate(
                            """
                            () => {
                                const items = document.querySelectorAll('[role="option"], [class*="select-menu"] li, li');
                                for (const it of items) {
                                    const t = (it.textContent || '').trim();
                                    if (t.includes('无品牌') || t.includes('无注册商标')) {
                                        const r = it.getBoundingClientRect();
                                        if (r.width > 5 && r.height > 5) {
                                            it.click();
                                            return true;
                                        }
                                    }
                                }
                                return false;
                            }
                            """
                        )
                        if picked:
                            filled.append("品牌=无品牌（下拉选）")
                        else:
                            failed.append("品牌=无品牌")
                    elif clicked:
                        filled.append("品牌=无品牌")
                    else:
                        failed.append("品牌=无品牌")
                except Exception as e:
                    logger.warning("[TAOBAO] click 无品牌 failed: %s", e)
                    failed.append("品牌=无品牌")

            # ── 商品属性（PDF 第 6 步）──
            # specs 字段名常常跟淘宝实际类目字段不一致，需要按"别名候选列表"逐个尝试。
            # 例：宠物/猫窝类目 ecommerce_detail 给"材质" → 淘宝叫"板材"；"适用体型" → "适用猫体型"。
            # 找不到对应 label 的属性（颜色、层数、稳固性等）属于该类目无此字段，记 skipped 不算 failed。
            SPEC_ALIAS_MAP: Dict[str, List[str]] = {
                "材质": ["材质", "板材", "主材质", "面料材质", "外壳材质", "材料"],
                "层数": ["层数", "层级", "结构层数"],
                "适用宠物": ["适用宠物", "适用宠物种类", "宠物种类", "宠物类型", "适合宠物"],
                "颜色": ["颜色", "颜色分类", "外观颜色", "主色调"],
                "适用体型": ["适用体型", "适用猫体型", "适用犬体型", "适用宠物体型", "宠物体型"],
                "稳固性": ["稳固性", "稳定性", "防侧翻", "防倾倒", "底盘稳固性"],
                "尺寸": ["尺寸", "规格尺寸", "外观尺寸", "整体尺寸"],
            }
            if specs:
                for spec_label, spec_value in specs.items():
                    if not spec_label or not spec_value:
                        continue
                    candidates = SPEC_ALIAS_MAP.get(spec_label, []) or [spec_label]
                    if spec_label not in candidates:
                        candidates = [spec_label] + candidates
                    ok = False
                    matched_alias = ""
                    for cand in candidates:
                        if await self._fill_text_by_label(page, cand, str(spec_value), fuzzy=False):
                            ok, matched_alias = True, cand
                            break
                        if await self._fill_text_by_label(page, cand, str(spec_value), fuzzy=True):
                            ok, matched_alias = True, cand
                            break
                        if await self._select_dropdown_by_label(page, cand, str(spec_value)):
                            ok, matched_alias = True, cand
                            break
                    if ok and matched_alias and matched_alias != spec_label:
                        print(
                            f"[TAOBAO] 属性别名命中：{spec_label!r} -> {matched_alias!r} = {spec_value!r}",
                            flush=True,
                        )
                    if ok:
                        filled.append(f"属性[{spec_label}]={spec_value}")
                    else:
                        # 检查是否所有候选 label 都根本不在页面上（=该类目无此字段，运营手填即可）
                        any_present = False
                        for cand in candidates:
                            try:
                                snippet = await self._dump_label_neighborhood(page, cand, max_chars=200)
                            except Exception:
                                snippet = ""
                            if snippet:
                                any_present = True
                                print(
                                    f"[TAOBAO][ATTR-DUMP] label={spec_label!r}->{cand!r} 在页面上有元素但填值/选项失败\n  HTML => {snippet[:1200]}",
                                    flush=True,
                                )
                                break
                        if any_present:
                            failed.append(f"属性[{spec_label}]")
                        else:
                            print(
                                f"[TAOBAO] 属性 {spec_label!r}（候选 {candidates}）该类目无此字段，跳过",
                                flush=True,
                            )

            # ── 价格 / 库存 ──
            if price:
                if await self._fill_text_by_label(page, "一口价", str(price)):
                    filled.append(f"一口价={price}")
                else:
                    failed.append("一口价")

            if stock is not None and int(stock) > 0:
                if await self._fill_text_by_label(page, "总库存", str(stock)):
                    filled.append(f"总库存={stock}")
                else:
                    failed.append("总库存")

            # ── 发货时间（PDF 第 12 步：48 小时）──
            if delivery_time:
                if await self._click_radio_by_label(page, "发货时间", delivery_time):
                    filled.append(f"发货时间={delivery_time}")
                else:
                    failed.append("发货时间")

            # ── 价格 / 库存（兜底再填一次，确保已 render 后能命中）──
            # （上面已尝试，这里不重复）

            # ── 卖点 / hero_claim（多行文本：卖点 / 商品亮点 / 购买须知）──
            # 淘宝部分类目有「卖点描述」textarea；普通类目没有就降级到「购买须知」
            if hero_claim:
                if await self._fill_textarea_by_keywords(
                    page, ["卖点", "商品亮点", "卖点描述", "主推卖点"], hero_claim
                ):
                    filled.append("hero_claim")
                else:
                    # 降级到购买须知
                    if await self._fill_textarea_by_keywords(
                        page, ["购买须知"], hero_claim
                    ):
                        filled.append("hero_claim→购买须知")
                    else:
                        failed.append("hero_claim")

            if selling_points:
                sp_text = "\n".join(
                    f"{i + 1}. {str(p).strip()}"
                    for i, p in enumerate(selling_points)
                    if p and str(p).strip()
                )
                if sp_text:
                    # 优先「卖点」专用 textarea；如果 hero_claim 已占用「购买须知」，
                    # 就把卖点拼到 hero_claim 后面一起塞「购买须知」
                    if await self._fill_textarea_by_keywords(
                        page, ["卖点描述", "卖点", "商品亮点", "主推卖点"], sp_text
                    ):
                        filled.append(f"卖点({len(selling_points)}条)")
                    elif "hero_claim→购买须知" in filled and hero_claim:
                        merged = f"{hero_claim}\n\n核心卖点：\n{sp_text}"
                        if await self._fill_textarea_by_keywords(
                            page, ["购买须知"], merged
                        ):
                            filled.append(f"卖点+hero_claim→购买须知({len(selling_points)}条)")
                        else:
                            failed.append("卖点")
                    elif await self._fill_textarea_by_keywords(
                        page, ["购买须知"], sp_text
                    ):
                        filled.append(f"卖点→购买须知({len(selling_points)}条)")
                    else:
                        failed.append("卖点")

            # ── 分区上传（每个 zone 之间停 8-12s 模拟人换区域）─────
            async def _inter_zone_pause():
                gap = random.uniform(8.0, 12.0) * self._pace_mult()
                print(f"[TAOBAO] 区域切换间隔 {gap:.1f}s (pace={self._pace_mult():.2f})", flush=True)
                await asyncio.sleep(gap)

            # 主图 1:1 （兼容老 caller：传 main_image_paths 视为 1:1）
            square_paths = main_square_image_paths or main_image_paths or []
            if square_paths:
                n = await self._upload_by_zone(
                    page,
                    ["1:1主图", "1：1主图", "1:1 主图"],
                    list(square_paths),
                    max_files=5,
                    zone_label="1:1主图",
                )
                if n > 0:
                    filled.append(f"1:1主图({n}张)")
                else:
                    failed.append("1:1主图上传")
                await _inter_zone_pause()

            # 主图 3:4 竖图
            if portrait_image_paths:
                n = await self._upload_by_zone(
                    page,
                    ["3:4主图", "3：4主图", "3:4 主图"],
                    list(portrait_image_paths),
                    max_files=5,
                    zone_label="3:4主图",
                )
                if n > 0:
                    filled.append(f"3:4主图({n}张)")
                else:
                    failed.append("3:4主图上传")
                await _inter_zone_pause()

            # 白底图：先点「从主图生成」让淘宝自动从主图裁；不行再走 sucai 上传
            if white_bg_image_paths:
                # 折叠区先展开
                await self._expand_zone_section(page, ["白底图", "白底主图", "白底"])
                await asyncio.sleep(random.uniform(1.0, 2.0))
                if await self._click_white_bg_generate_from_main(page):
                    filled.append("白底图(从主图生成)")
                    print("[TAOBAO] 白底图：点了「从主图生成」按钮", flush=True)
                    await asyncio.sleep(random.uniform(2.0, 3.5))
                else:
                    n = await self._upload_by_zone(
                        page,
                        ["白底图", "白底主图", "白底"],
                        list(white_bg_image_paths),
                        max_files=5,
                        zone_label="白底图",
                    )
                    if n > 0:
                        filled.append(f"白底图({n}张上传)")
                    else:
                        failed.append("白底图上传")
                await _inter_zone_pause()

            # SKU 图：销售规格区一般要先创建规格才能上传，先尝试塞看看（也可能折叠）
            if sku_image_paths:
                n = await self._upload_by_zone(
                    page,
                    ["销售规格", "规格图"],
                    list(sku_image_paths),
                    max_files=20,
                    zone_label="SKU图",
                    needs_expand=True,
                )
                if n > 0:
                    filled.append(f"SKU图({n}张)")
                else:
                    failed.append("SKU图（需先建规格）")
                await _inter_zone_pause()

            # 详情图：「宝贝详情」富文本编辑器 → 「添加图片」按钮 → sucai-selector iframe → 点缩略图插入
            if detail_image_paths:
                n = await self._upload_to_detail_editor(
                    page, list(detail_image_paths), max_files=30
                )
                if n > 0:
                    filled.append(f"详情图({n}张)")
                else:
                    failed.append("详情图（富文本编辑器入口未找到）")

            msg_parts = ["已打开淘宝商品创建页面"]
            if filled:
                msg_parts.append(f"自动填充 {len(filled)} 项: {', '.join(filled[:8])}{'...' if len(filled) > 8 else ''}")
            if failed:
                msg_parts.append(f"未填 {len(failed)} 项: {', '.join(failed[:6])}")
            msg_parts.append("请检查并补充其余信息后手动发布")

            return {
                "ok": True,
                "message": "，".join(msg_parts),
                "url": page.url,
                "auto_filled": filled,
                "failed_fields": failed,
            }
        except Exception as e:
            logger.exception("[TAOBAO] open_product_form failed")
            return {"ok": False, "error": str(e)}
