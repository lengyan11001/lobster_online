"""Lightweight CDP inspector: 扫淘宝 publish.htm 上所有可见 label / 属性字段名 / select 控件，
帮我们对齐 ecommerce_detail.specs 的 key 与淘宝实际属性 label。

用法（Chrome 已开 9222 且当前 tab 在 publish.htm）：
    python scripts/tb_inspect_attrs.py
"""
from __future__ import annotations

import asyncio
import json
from typing import Any

from playwright.async_api import async_playwright


async def main() -> None:
    async with async_playwright() as p:
        browser = await p.chromium.connect_over_cdp("http://localhost:9222")
        ctx = browser.contexts[0]
        page = None
        for pg in ctx.pages:
            url = (pg.url or "").lower()
            if "item.upload.taobao.com/sell/v2/publish.htm" in url:
                page = pg
                break
        if page is None:
            print("[inspect] 未找到 publish.htm tab，先在 Chrome 里开淘宝商品发布页")
            return
        print(f"[inspect] using tab: {page.url[:160]}")

        # 1) 找出所有"必填"属性条目（淘宝 React 通常用 .next-form-item-label / dt 等）
        info: dict[str, Any] = await page.evaluate(
            """
            () => {
                // 筛选可见
                const visible = (el) => {
                    const r = el.getBoundingClientRect();
                    if (r.width < 5 || r.height < 5) return false;
                    const cs = getComputedStyle(el);
                    if (cs.visibility === 'hidden' || cs.display === 'none' || cs.opacity === '0') return false;
                    return true;
                };

                // 收集 form 区域所有 label-like 节点
                const candidates = [];
                document.querySelectorAll(
                    '.next-form-item-label, .next-form-item label, label, dt, [class*="label"], [class*="Label"]'
                ).forEach(el => {
                    if (!visible(el)) return;
                    const t = (el.textContent || '').trim();
                    if (!t || t.length > 24) return;
                    candidates.push({
                        text: t,
                        tag: el.tagName.toLowerCase(),
                        cls: el.className || '',
                    });
                });

                // 找有 select / combobox / placeholder=请选择 的控件
                const triggers = [];
                document.querySelectorAll(
                    'input[placeholder*="请选择"], [role="combobox"], [class*="select-trigger"], [class*="Select"]'
                ).forEach(el => {
                    if (!visible(el)) return;
                    const ph = el.getAttribute('placeholder') || '';
                    triggers.push({
                        tag: el.tagName.toLowerCase(),
                        cls: (el.className || '').toString().slice(0, 80),
                        ph: ph,
                        val: (el.value || '').slice(0, 40),
                    });
                });

                // 收集所有"商品属性"区附近的 label（淘宝用 sell-component-info-wrapper-label）
                const sellLabels = [];
                document.querySelectorAll('.sell-component-info-wrapper-label').forEach(el => {
                    if (!visible(el)) return;
                    const t = (el.textContent || '').trim();
                    if (!t || t.length > 30) return;
                    // 找最近祖先里的控件
                    let p = el;
                    let ctrlInfo = null;
                    for (let i = 0; i < 8 && p && p.parentElement; i++) {
                        p = p.parentElement;
                        const inp = p.querySelector(
                            'input, textarea, [role="combobox"], [class*="select-trigger"], [class*="next-radio"]'
                        );
                        if (inp) {
                            ctrlInfo = {
                                tag: inp.tagName.toLowerCase(),
                                type: inp.getAttribute('type') || '',
                                ph: inp.getAttribute('placeholder') || '',
                                val: (inp.value || '').slice(0, 40),
                                cls: (inp.className || '').toString().slice(0, 80),
                            };
                            break;
                        }
                    }
                    sellLabels.push({ label: t, ctrl: ctrlInfo });
                });

                // 收集所有 next-form-item 区块的 label + 控件类型
                const items = [];
                document.querySelectorAll('.next-form-item').forEach(item => {
                    if (!visible(item)) return;
                    const lab = item.querySelector('.next-form-item-label, label, dt');
                    const labText = lab ? (lab.textContent || '').trim() : '';
                    if (!labText || labText.length > 30) return;
                    const inp = item.querySelector(
                        'input, textarea, [role="combobox"], button, [class*="select-trigger"]'
                    );
                    const inpInfo = inp ? {
                        tag: inp.tagName.toLowerCase(),
                        type: inp.getAttribute('type') || '',
                        ph: inp.getAttribute('placeholder') || '',
                        cls: (inp.className || '').toString().slice(0, 80),
                        text: (inp.textContent || '').trim().slice(0, 30),
                    } : null;
                    items.push({ label: labText, ctrl: inpInfo });
                });

                // 也扫一下页面文本里"必填"前后 30 字符的 label，用于发现折叠/未渲染区
                const txt = (document.body.innerText || '');
                const requiredHints = [];
                const re = /([\\u4e00-\\u9fa5A-Za-z0-9（）\\(\\)·\\-/×]{2,12})\\s*[\\*\\u2731]/g;
                let m; let cnt = 0;
                while ((m = re.exec(txt)) !== null && cnt < 80) {
                    requiredHints.push(m[1].trim());
                    cnt++;
                }

                return {
                    n_candidates: candidates.length,
                    sample_labels: candidates.slice(0, 40),
                    triggers_count: triggers.length,
                    triggers_sample: triggers.slice(0, 20),
                    form_items_count: items.length,
                    form_items_sample: items.slice(0, 60),
                    sell_labels_count: sellLabels.length,
                    sell_labels: sellLabels.slice(0, 120),
                    required_label_hints: Array.from(new Set(requiredHints)).slice(0, 60),
                };
            }
            """
        )
        out_path = "E:/lobster_online/logs/tb_inspect_attrs.json"
        with open(out_path, "w", encoding="utf-8") as fp:
            json.dump(info, fp, ensure_ascii=False, indent=2)
        print(f"[inspect] saved -> {out_path}")
        print(f"[inspect] form_items={info.get('form_items_count')} candidates={info.get('n_candidates')} triggers={info.get('triggers_count')}")

        # 2) 单独搜素：6 个待填属性
        targets = ["材质", "层数", "适用宠物", "颜色", "适用体型", "稳固性"]
        attr_hits = {}
        for label in targets:
            hits = await page.evaluate(
                """
                (label) => {
                    const out = [];
                    document.querySelectorAll('span, label, div, dt, td, a, p').forEach(el => {
                        const t = (el.textContent || '').trim();
                        if (!t) return;
                        if (t.length > 25) return;
                        if (t === label || t.includes(label) || label.includes(t)) {
                            const r = el.getBoundingClientRect();
                            out.push({
                                tag: el.tagName.toLowerCase(),
                                text: t,
                                visible: r.width > 5 && r.height > 5,
                                top: Math.round(r.top),
                            });
                        }
                    });
                    return out.slice(0, 8);
                }
                """,
                label,
            )
            attr_hits[label] = hits
        with open("E:/lobster_online/logs/tb_inspect_target_attrs.json", "w", encoding="utf-8") as fp:
            json.dump(attr_hits, fp, ensure_ascii=False, indent=2)
        print("[inspect] target attr hits saved -> logs/tb_inspect_target_attrs.json")
        for k, v in attr_hits.items():
            print(f"  [{k}] hits={len(v)}")


if __name__ == "__main__":
    asyncio.run(main())
