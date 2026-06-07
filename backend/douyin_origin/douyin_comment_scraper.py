from __future__ import annotations

import asyncio
import os
import random
import re
from datetime import datetime
from typing import Callable, Dict, List, Optional
from urllib.parse import quote, urlparse

import requests
from playwright.async_api import Browser, BrowserContext, Page, Playwright, async_playwright

from douyin_client import DouyinClient, is_port_open


class DouyinMentionCommentStopped(RuntimeError):
    pass


def parse_count_text(text: str) -> int:
    value = str(text or "").strip().replace(",", "").replace("+", "")
    if not value:
        return 0
    try:
        if "万" in value:
            return int(float(re.sub(r"[^0-9.]", "", value) or 0) * 10000)
        if "千" in value:
            return int(float(re.sub(r"[^0-9.]", "", value) or 0) * 1000)
        return int(re.sub(r"[^0-9]", "", value) or 0)
    except Exception:
        return 0


def format_compact_count(count: int) -> str:
    value = max(0, int(count or 0))
    if value >= 100000000:
        text = f"{value / 100000000:.1f}".rstrip("0").rstrip(".")
        return f"{text}亿"
    if value >= 10000:
        text = f"{value / 10000:.1f}".rstrip("0").rstrip(".")
        return f"{text}万"
    return str(value)


def format_count_text(value: object) -> str:
    text = str(value or "").strip()
    if not text:
        return ""
    if re.search(r"[万亿千kKwW]", text):
        return text
    count = parse_count_text(text)
    if count <= 0 and text not in {"0", "0.0"}:
        return text
    return format_compact_count(count)


def format_unix_timestamp_text(value: object) -> str:
    try:
        timestamp = int(float(value or 0))
    except Exception:
        return ""
    if timestamp <= 0:
        return ""
    try:
        return datetime.fromtimestamp(timestamp).strftime("%Y-%m-%d %H:%M")
    except Exception:
        return ""


def extract_aweme_id(video_url: str) -> str:
    parsed = urlparse(video_url)
    parts = [part for part in parsed.path.split("/") if part]
    if "video" in parts:
        idx = parts.index("video")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return ""


def extract_sec_user_id(profile_url: str) -> str:
    parsed = urlparse(profile_url)
    parts = [part for part in parsed.path.split("/") if part]
    if "user" in parts:
        idx = parts.index("user")
        if idx + 1 < len(parts):
            return parts[idx + 1]
    return ""


class DouyinCommentScraper:
    def __init__(
        self,
        headless: Optional[bool] = None,
        account_id: int | None = None,
        cdp_port: int | None = None,
        allow_workspace_fallback: bool = True,
        allow_cdp_reuse: bool = True,
    ):
        env_headless = os.environ.get("DOUYIN_HEADLESS", "").strip().lower()
        if headless is None:
            headless = env_headless in {"1", "true", "yes", "on"}
        self.headless = headless
        self.account_id = account_id
        self.cdp_port = int(cdp_port or 0) if cdp_port else None
        self.allow_workspace_fallback = bool(allow_workspace_fallback)
        self.allow_cdp_reuse = bool(allow_cdp_reuse)
        self.profile_dir = (
            DouyinClient(self.cdp_port or 9330, account_id=account_id).resolve_profile_dir()
        )
        self._playwright: Optional[Playwright] = None
        self._browser: Optional[Browser] = None
        self._context: Optional[BrowserContext] = None
        self._owns_browser = False
        self._owns_context = False

    async def _dispose_browser_runtime(self):
        context = self._context if self._owns_context else None
        browser = self._browser if self._owns_browser else None
        playwright = self._playwright

        self._context = None
        self._browser = None
        self._playwright = None
        self._owns_context = False
        self._owns_browser = False

        try:
            if context:
                await context.close()
        except Exception:
            pass
        finally:
            try:
                if browser:
                    await browser.close()
            except Exception:
                pass
            finally:
                if playwright:
                    try:
                        await playwright.stop()
                    except Exception:
                        pass

    def _emit(self, logger: Optional[Callable[[str, str], None]], message: str, level: str = "info"):
        if not logger:
            return
        try:
            logger(message, level)
        except Exception:
            pass

    def _raise_if_should_stop(
        self,
        should_stop: Optional[Callable[[], bool]] = None,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        try:
            if should_stop and should_stop():
                self._emit(logger, "[抖音评论@客户] 已请求停止，正在中断当前任务", "warning")
                raise DouyinMentionCommentStopped("评论@精准客户任务已停止")
        except DouyinMentionCommentStopped:
            raise
        except Exception:
            return

    def _comment_container_selector(self) -> str:
        return (
            '.comment-input-inner-container, '
            '.comment-input-outer-container, '
            '.comment-input-container, '
            '[class*="comment-input-inner-container"], '
            '[class*="comment-input-outer-container"], '
            '[class*="comment-input-container"]'
        )

    def _visible_comment_container_selector(self) -> str:
        return (
            '.comment-input-inner-container:visible, '
            '.comment-input-outer-container:visible, '
            '.comment-input-container:visible, '
            '[class*="comment-input-inner-container"]:visible, '
            '[class*="comment-input-outer-container"]:visible, '
            '[class*="comment-input-container"]:visible'
        )

    def _comment_editor_selector(self) -> str:
        return (
            '.comment-input-inner-container .public-DraftEditor-content[contenteditable="true"][role="combobox"], '
            '.comment-input-inner-container .public-DraftEditor-content[contenteditable="true"], '
            '.comment-input-inner-container [contenteditable="true"], '
            '.comment-input-outer-container .public-DraftEditor-content[contenteditable="true"][role="combobox"], '
            '.comment-input-outer-container .public-DraftEditor-content[contenteditable="true"], '
            '.comment-input-outer-container [contenteditable="true"], '
            '.comment-input-container .public-DraftEditor-content[contenteditable="true"][role="combobox"], '
            '.comment-input-container .public-DraftEditor-content[contenteditable="true"], '
            '.comment-input-container [contenteditable="true"], '
            '[class*="comment-input-inner-container"] .public-DraftEditor-content[contenteditable="true"][role="combobox"], '
            '[class*="comment-input-inner-container"] .public-DraftEditor-content[contenteditable="true"], '
            '[class*="comment-input-inner-container"] [contenteditable="true"], '
            '[class*="comment-input-outer-container"] .public-DraftEditor-content[contenteditable="true"][role="combobox"], '
            '[class*="comment-input-outer-container"] .public-DraftEditor-content[contenteditable="true"], '
            '[class*="comment-input-outer-container"] [contenteditable="true"], '
            '[class*="comment-input-container"] .public-DraftEditor-content[contenteditable="true"][role="combobox"], '
            '[class*="comment-input-container"] .public-DraftEditor-content[contenteditable="true"], '
            '[class*="comment-input-container"] [contenteditable="true"], '
            '.comment-input-inner-container .richtext-container, '
            '.comment-input-inner-container .DraftEditor-root, '
            '.comment-input-inner-container .DraftEditor-editorContainer, '
            '.comment-input-outer-container .richtext-container, '
            '.comment-input-outer-container .DraftEditor-root, '
            '.comment-input-outer-container .DraftEditor-editorContainer, '
            '.comment-input-container .richtext-container, '
            '.comment-input-container .DraftEditor-root, '
            '.comment-input-container .DraftEditor-editorContainer, '
            '[class*="comment-input-inner-container"] .richtext-container, '
            '[class*="comment-input-inner-container"] .DraftEditor-root, '
            '[class*="comment-input-inner-container"] .DraftEditor-editorContainer, '
            '[class*="comment-input-outer-container"] .richtext-container, '
            '[class*="comment-input-outer-container"] .DraftEditor-root, '
            '[class*="comment-input-outer-container"] .DraftEditor-editorContainer, '
            '[class*="comment-input-container"] .richtext-container, '
            '[class*="comment-input-container"] .DraftEditor-root, '
            '[class*="comment-input-container"] .DraftEditor-editorContainer, '
            'input[placeholder*="留下你的精彩评论吧"], '
            'input[placeholder*="精彩评论"], '
            'input[placeholder*="说点什么"], '
            'input[placeholder*="评论"], '
            'input[placeholder*="回复"], '
            'textarea[placeholder*="留下你的精彩评论吧"], '
            'textarea[placeholder*="精彩评论"], '
            'textarea[placeholder*="说点什么"], '
            'textarea[placeholder*="评论"], '
            'textarea[placeholder*="回复"]'
        )

    def _visible_comment_editor_selector(self) -> str:
        return (
            '.comment-input-inner-container .public-DraftEditor-content[contenteditable="true"][role="combobox"]:visible, '
            '.comment-input-inner-container .public-DraftEditor-content[contenteditable="true"]:visible, '
            '.comment-input-inner-container [contenteditable="true"]:visible, '
            '.comment-input-outer-container .public-DraftEditor-content[contenteditable="true"][role="combobox"]:visible, '
            '.comment-input-outer-container .public-DraftEditor-content[contenteditable="true"]:visible, '
            '.comment-input-outer-container [contenteditable="true"]:visible, '
            '.comment-input-container .public-DraftEditor-content[contenteditable="true"][role="combobox"]:visible, '
            '.comment-input-container .public-DraftEditor-content[contenteditable="true"]:visible, '
            '.comment-input-container [contenteditable="true"]:visible, '
            '[class*="comment-input-inner-container"] .public-DraftEditor-content[contenteditable="true"][role="combobox"]:visible, '
            '[class*="comment-input-inner-container"] .public-DraftEditor-content[contenteditable="true"]:visible, '
            '[class*="comment-input-inner-container"] [contenteditable="true"]:visible, '
            '[class*="comment-input-outer-container"] .public-DraftEditor-content[contenteditable="true"][role="combobox"]:visible, '
            '[class*="comment-input-outer-container"] .public-DraftEditor-content[contenteditable="true"]:visible, '
            '[class*="comment-input-outer-container"] [contenteditable="true"]:visible, '
            '[class*="comment-input-container"] .public-DraftEditor-content[contenteditable="true"][role="combobox"]:visible, '
            '[class*="comment-input-container"] .public-DraftEditor-content[contenteditable="true"]:visible, '
            '[class*="comment-input-container"] [contenteditable="true"]:visible, '
            '.comment-input-inner-container .richtext-container:visible, '
            '.comment-input-inner-container .DraftEditor-root:visible, '
            '.comment-input-inner-container .DraftEditor-editorContainer:visible, '
            '.comment-input-outer-container .richtext-container:visible, '
            '.comment-input-outer-container .DraftEditor-root:visible, '
            '.comment-input-outer-container .DraftEditor-editorContainer:visible, '
            '.comment-input-container .richtext-container:visible, '
            '.comment-input-container .DraftEditor-root:visible, '
            '.comment-input-container .DraftEditor-editorContainer:visible, '
            '[class*="comment-input-inner-container"] .richtext-container:visible, '
            '[class*="comment-input-inner-container"] .DraftEditor-root:visible, '
            '[class*="comment-input-inner-container"] .DraftEditor-editorContainer:visible, '
            '[class*="comment-input-outer-container"] .richtext-container:visible, '
            '[class*="comment-input-outer-container"] .DraftEditor-root:visible, '
            '[class*="comment-input-outer-container"] .DraftEditor-editorContainer:visible, '
            '[class*="comment-input-container"] .richtext-container:visible, '
            '[class*="comment-input-container"] .DraftEditor-root:visible, '
            '[class*="comment-input-container"] .DraftEditor-editorContainer:visible, '
            'input[placeholder*="留下你的精彩评论吧"]:visible, '
            'input[placeholder*="精彩评论"]:visible, '
            'input[placeholder*="说点什么"]:visible, '
            'input[placeholder*="评论"]:visible, '
            'input[placeholder*="回复"]:visible, '
            'textarea[placeholder*="留下你的精彩评论吧"]:visible, '
            'textarea[placeholder*="精彩评论"]:visible, '
            'textarea[placeholder*="说点什么"]:visible, '
            'textarea[placeholder*="评论"]:visible, '
            'textarea[placeholder*="回复"]:visible'
        )

    def _comment_placeholder_selector(self) -> str:
        return (
            '.comment-input-inner-container span:has-text("留下你的精彩评论吧"), '
            '.comment-input-inner-container span:has-text("说点什么"), '
            '.comment-input-inner-container span:has-text("写评论"), '
            '.comment-input-inner-container span:has-text("参与讨论"), '
            '.comment-input-inner-container span:has-text("请先登录后发表评论"), '
            '.comment-input-inner-container .public-DraftEditorPlaceholder-inner, '
            '.comment-input-inner-container .public-DraftEditorPlaceholder-root, '
            '.comment-input-outer-container span:has-text("留下你的精彩评论吧"), '
            '.comment-input-outer-container span:has-text("说点什么"), '
            '.comment-input-outer-container span:has-text("写评论"), '
            '.comment-input-outer-container span:has-text("参与讨论"), '
            '.comment-input-outer-container span:has-text("请先登录后发表评论"), '
            '.comment-input-outer-container .public-DraftEditorPlaceholder-inner, '
            '.comment-input-outer-container .public-DraftEditorPlaceholder-root, '
            '[class*="comment-input-inner-container"] span:has-text("留下你的精彩评论吧"), '
            '[class*="comment-input-inner-container"] span:has-text("说点什么"), '
            '[class*="comment-input-inner-container"] span:has-text("写评论"), '
            '[class*="comment-input-inner-container"] span:has-text("参与讨论"), '
            '[class*="comment-input-inner-container"] span:has-text("请先登录后发表评论"), '
            '[class*="comment-input-inner-container"] .public-DraftEditorPlaceholder-inner, '
            '[class*="comment-input-inner-container"] .public-DraftEditorPlaceholder-root, '
            '[class*="comment-input-outer-container"] span:has-text("留下你的精彩评论吧"), '
            '[class*="comment-input-outer-container"] span:has-text("说点什么"), '
            '[class*="comment-input-outer-container"] span:has-text("写评论"), '
            '[class*="comment-input-outer-container"] span:has-text("参与讨论"), '
            '[class*="comment-input-outer-container"] span:has-text("请先登录后发表评论"), '
            '[class*="comment-input-outer-container"] .public-DraftEditorPlaceholder-inner, '
            '[class*="comment-input-outer-container"] .public-DraftEditorPlaceholder-root, '
            '[class*="comment-input-container"] span:has-text("留下你的精彩评论吧"), '
            '[class*="comment-input-container"] span:has-text("说点什么"), '
            '[class*="comment-input-container"] span:has-text("写评论"), '
            '[class*="comment-input-container"] span:has-text("参与讨论"), '
            '[class*="comment-input-container"] span:has-text("请先登录后发表评论"), '
            '[class*="comment-input-container"] .public-DraftEditorPlaceholder-inner, '
            '[class*="comment-input-container"] .public-DraftEditorPlaceholder-root'
        )

    def _visible_comment_placeholder_selector(self) -> str:
        return (
            '.comment-input-inner-container span:visible:has-text("留下你的精彩评论吧"), '
            '.comment-input-inner-container span:visible:has-text("说点什么"), '
            '.comment-input-inner-container span:visible:has-text("写评论"), '
            '.comment-input-inner-container span:visible:has-text("参与讨论"), '
            '.comment-input-inner-container span:visible:has-text("请先登录后发表评论"), '
            '.comment-input-inner-container .public-DraftEditorPlaceholder-inner:visible, '
            '.comment-input-inner-container .public-DraftEditorPlaceholder-root:visible, '
            '.comment-input-outer-container span:visible:has-text("留下你的精彩评论吧"), '
            '.comment-input-outer-container span:visible:has-text("说点什么"), '
            '.comment-input-outer-container span:visible:has-text("写评论"), '
            '.comment-input-outer-container span:visible:has-text("参与讨论"), '
            '.comment-input-outer-container span:visible:has-text("请先登录后发表评论"), '
            '.comment-input-outer-container .public-DraftEditorPlaceholder-inner:visible, '
            '.comment-input-outer-container .public-DraftEditorPlaceholder-root:visible, '
            '[class*="comment-input-inner-container"] span:visible:has-text("留下你的精彩评论吧"), '
            '[class*="comment-input-inner-container"] span:visible:has-text("说点什么"), '
            '[class*="comment-input-inner-container"] span:visible:has-text("写评论"), '
            '[class*="comment-input-inner-container"] span:visible:has-text("参与讨论"), '
            '[class*="comment-input-inner-container"] span:visible:has-text("请先登录后发表评论"), '
            '[class*="comment-input-inner-container"] .public-DraftEditorPlaceholder-inner:visible, '
            '[class*="comment-input-inner-container"] .public-DraftEditorPlaceholder-root:visible, '
            '[class*="comment-input-outer-container"] span:visible:has-text("留下你的精彩评论吧"), '
            '[class*="comment-input-outer-container"] span:visible:has-text("说点什么"), '
            '[class*="comment-input-outer-container"] span:visible:has-text("写评论"), '
            '[class*="comment-input-outer-container"] span:visible:has-text("参与讨论"), '
            '[class*="comment-input-outer-container"] span:visible:has-text("请先登录后发表评论"), '
            '[class*="comment-input-outer-container"] .public-DraftEditorPlaceholder-inner:visible, '
            '[class*="comment-input-outer-container"] .public-DraftEditorPlaceholder-root:visible, '
            '[class*="comment-input-container"] span:visible:has-text("留下你的精彩评论吧"), '
            '[class*="comment-input-container"] span:visible:has-text("说点什么"), '
            '[class*="comment-input-container"] span:visible:has-text("写评论"), '
            '[class*="comment-input-container"] span:visible:has-text("参与讨论"), '
            '[class*="comment-input-container"] span:visible:has-text("请先登录后发表评论"), '
            '[class*="comment-input-container"] .public-DraftEditorPlaceholder-inner:visible, '
            '[class*="comment-input-container"] .public-DraftEditorPlaceholder-root:visible'
        )

    def _comment_placeholder_node_selector(self) -> str:
        return (
            '.comment-input-inner-container .public-DraftEditorPlaceholder-inner, '
            '.comment-input-inner-container .public-DraftEditorPlaceholder-root, '
            '.comment-input-outer-container .public-DraftEditorPlaceholder-inner, '
            '.comment-input-outer-container .public-DraftEditorPlaceholder-root, '
            '.comment-input-container .public-DraftEditorPlaceholder-inner, '
            '.comment-input-container .public-DraftEditorPlaceholder-root, '
            '[class*="comment-input-inner-container"] .public-DraftEditorPlaceholder-inner, '
            '[class*="comment-input-inner-container"] .public-DraftEditorPlaceholder-root, '
            '[class*="comment-input-outer-container"] .public-DraftEditorPlaceholder-inner, '
            '[class*="comment-input-outer-container"] .public-DraftEditorPlaceholder-root, '
            '[class*="comment-input-container"] .public-DraftEditorPlaceholder-inner, '
            '[class*="comment-input-container"] .public-DraftEditorPlaceholder-root'
        )

    def _comment_send_button_selector(self) -> str:
        return (
            '.commentInput-right-ct button:visible, '
            '.commentInput-right-ct [role="button"]:visible, '
            '.commentInput-right-ct .WFB7wUOX:visible, '
            'button:visible:has-text("发送"), '
            'button:visible:has-text("发布"), '
            '[role="button"]:visible:has-text("发送"), '
            '[role="button"]:visible:has-text("发布")'
        )

    async def _read_comment_submission_snapshot(self, page: Page) -> Dict[str, object]:
        try:
            return await page.evaluate(
                """
                ({ editorSelectors, placeholderSelectors }) => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const isVisible = (node) => {
                        if (!node || typeof node.getBoundingClientRect !== 'function') return false;
                        const style = window.getComputedStyle(node);
                        if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            return false;
                        }
                        const rect = node.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const pickVisibleBySelectors = (selectorText) => String(selectorText || '')
                        .split(',')
                        .map((item) => item.trim())
                        .filter(Boolean)
                        .flatMap((item) => Array.from(document.querySelectorAll(item)))
                        .find(isVisible) || null;

                    const editor = pickVisibleBySelectors(editorSelectors);
                    const placeholder = pickVisibleBySelectors(placeholderSelectors);
                    const suggestionVisible = Array.from(
                        document.querySelectorAll('[class*="atBox-inner-container"], [class*="atBox-inner"]')
                    ).some(isVisible);

                    const sendCandidates = Array.from(
                        document.querySelectorAll('.commentInput-right-ct button, .commentInput-right-ct [role="button"], .commentInput-right-ct .WFB7wUOX, button, [role="button"]')
                    ).filter(isVisible);
                    const sendButton = sendCandidates.find((node) => {
                        const text = normalize(node.innerText || node.textContent || node.getAttribute?.('aria-label') || node.getAttribute?.('title') || '');
                        const className = normalize(node.className || '').toLowerCase();
                        return text.includes('发送')
                            || text.includes('发布')
                            || className.includes('send')
                            || className.includes('publish')
                            || !!node.querySelector?.('svg');
                    }) || null;

                    const comments = Array.from(
                        document.querySelectorAll('.comment-item, [class*="comment-item"], [data-e2e="comment-list"] .comment-item, .comment-mainContent .comment-item')
                    )
                        .filter(isVisible)
                        .map((node) => normalize(
                            node.querySelector('.comment-item-info-wrap, [class*="comment-item-info-wrap"], .comment-content, [class*="comment-content"], .comment-text, [class*="comment-text"]')?.innerText
                            || node.innerText
                            || node.textContent
                            || ''
                        ))
                        .filter(Boolean)
                        .slice(0, 60);

                    let editorText = normalize(
                        editor?.value
                        || editor?.innerText
                        || editor?.textContent
                        || ''
                    );
                    const placeholderText = normalize(
                        placeholder?.innerText
                        || placeholder?.textContent
                        || placeholder?.getAttribute?.('placeholder')
                        || ''
                    );
                    const placeholderHints = ['留下你的精彩评论吧', '说点什么', '写评论', '参与讨论', '请先登录后发表评论'];
                    if (
                        editorText
                        && placeholderHints.some((hint) => editorText === hint || editorText.includes(hint))
                    ) {
                        editorText = '';
                    }
                    if (editorText && placeholderText && editorText === placeholderText) {
                        editorText = '';
                    }
                    const sendDisabled = !!(
                        sendButton?.disabled
                        || String(sendButton?.getAttribute?.('aria-disabled') || '').toLowerCase() === 'true'
                        || String(sendButton?.className || '').toLowerCase().includes('disabled')
                        || !editorText
                    );
                    return {
                        editor_text: editorText,
                        placeholder_visible: !!placeholder,
                        suggestion_visible: suggestionVisible,
                        send_button_visible: !!sendButton,
                        send_button_disabled: sendDisabled,
                        comments,
                    };
                }
                """,
                {
                    "editorSelectors": self._comment_editor_selector(),
                    "placeholderSelectors": self._comment_placeholder_node_selector(),
                },
            )
        except Exception:
            return {
                "editor_text": "",
                "placeholder_visible": False,
                "suggestion_visible": False,
                "send_button_visible": False,
                "send_button_disabled": False,
                "comments": [],
            }

    async def _submit_comment_and_confirm(
        self,
        page: Page,
        input_locator,
        expected_fragments: List[str],
        logger: Optional[Callable[[str, str], None]] = None,
        action_label: str = "评论",
    ) -> None:
        before_snapshot = await self._read_comment_submission_snapshot(page)
        before_comments = "\n".join(
            re.sub(r"\s+", " ", str(item or "")).strip()
            for item in (before_snapshot.get("comments", []) or [])
            if re.sub(r"\s+", " ", str(item or "")).strip()
        )
        normalized_fragments = [
            re.sub(r"\s+", " ", str(item or "")).strip()
            for item in expected_fragments or []
            if re.sub(r"\s+", " ", str(item or "")).strip()
        ]

        send_attempts: List[str] = []
        send_locator = page.locator(self._comment_send_button_selector()).first
        if bool(before_snapshot.get("send_button_visible")) and not bool(before_snapshot.get("send_button_disabled")):
            try:
                await send_locator.click(timeout=5000, force=True)
                send_attempts.append("点击发送按钮")
                await page.wait_for_timeout(1200)
            except Exception as exc:
                self._emit(logger, f"[抖音{action_label}] 点击发送按钮失败，准备回退键盘发送：{exc}", "warning")

        after_snapshot = await self._read_comment_submission_snapshot(page)
        if str(after_snapshot.get("editor_text", "") or "").strip():
            try:
                await input_locator.click(timeout=5000, force=True)
            except Exception:
                pass
            await page.keyboard.press("Enter")
            send_attempts.append("回车发送")
            await page.wait_for_timeout(1200)
            after_snapshot = await self._read_comment_submission_snapshot(page)

        delivered = False
        final_snapshot = after_snapshot
        final_comments = ""
        matched_fragments: List[str] = []
        for _ in range(10):
            final_snapshot = await self._read_comment_submission_snapshot(page)
            final_comments = "\n".join(
                re.sub(r"\s+", " ", str(item or "")).strip()
                for item in (final_snapshot.get("comments", []) or [])
                if re.sub(r"\s+", " ", str(item or "")).strip()
            )
            matched_fragments = [
                item for item in normalized_fragments
                if item and item in final_comments
            ]
            editor_cleared = not str(final_snapshot.get("editor_text", "") or "").strip()
            comment_list_changed = bool(final_comments) and (
                final_comments != before_comments
                or bool(matched_fragments)
            )
            editor_reset_detected = editor_cleared and (
                bool(final_snapshot.get("placeholder_visible"))
                or not bool(final_snapshot.get("suggestion_visible"))
                or bool(final_snapshot.get("send_button_disabled"))
            )
            if comment_list_changed or editor_reset_detected:
                delivered = True
                break
            await page.wait_for_timeout(600)
        if not delivered:
            raise RuntimeError(
                f"{action_label}已尝试发送（{'、'.join(send_attempts) if send_attempts else '未命中发送动作'}），"
                "但未确认评论列表里出现新内容，已拦截误判成功。"
            )

    async def _move_comment_caret_to_end(
        self,
        input_locator,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        try:
            await input_locator.evaluate(
                """
                (el) => {
                    if (!el) return false;
                    const focusTarget =
                        el.matches?.('[contenteditable="true"], input, textarea')
                            ? el
                            : el.querySelector?.('[contenteditable="true"], input, textarea') || el;
                    if (!focusTarget) return false;
                    focusTarget.focus();
                    if (focusTarget.isContentEditable) {
                        const selection = window.getSelection();
                        const range = document.createRange();
                        range.selectNodeContents(focusTarget);
                        range.collapse(false);
                        selection.removeAllRanges();
                        selection.addRange(range);
                        return true;
                    }
                    if (typeof focusTarget.setSelectionRange === 'function') {
                        const length = String(focusTarget.value || '').length;
                        focusTarget.setSelectionRange(length, length);
                        return true;
                    }
                    return false;
                }
                """
            )
        except Exception as exc:
            self._emit(logger, f"[抖音评论@客户] 光标移到评论末尾失败：{exc}", "warning")

    async def _wait_for_visible_comment_editor(
        self,
        page: Page,
        timeout: int = 10000,
    ):
        selector = self._comment_editor_selector()
        visible_selector = self._visible_comment_editor_selector()
        await page.wait_for_function(
            """
            (selectors) => {
                const isVisible = (node) => {
                    if (!node || typeof node.getBoundingClientRect !== 'function') return false;
                    const style = window.getComputedStyle(node);
                    if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        return false;
                    }
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                return String(selectors || '')
                    .split(',')
                    .map((item) => item.trim())
                    .filter(Boolean)
                    .some((item) => Array.from(document.querySelectorAll(item)).some(isVisible));
            }
            """,
            arg=selector,
            timeout=timeout,
        )
        return page.locator(visible_selector).first

    async def _read_comment_surface_state(self, page: Page) -> Dict:
        return await page.evaluate(
            """
            (config) => {
                const splitSelectors = (value) =>
                    String(value || '')
                        .split(',')
                        .map((item) => item.trim())
                        .filter(Boolean);
                const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const isVisible = (node) => {
                    if (!node || typeof node.getBoundingClientRect !== 'function') return false;
                    const style = window.getComputedStyle(node);
                    if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        return false;
                    }
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const hasVisible = (selectors) =>
                    splitSelectors(selectors).some((selector) =>
                        Array.from(document.querySelectorAll(selector)).some(isVisible)
                    );
                const visibleTextsFromSelectors = (selectors) =>
                    splitSelectors(selectors)
                        .flatMap((selector) => Array.from(document.querySelectorAll(selector)))
                        .filter(isVisible)
                        .map((node) => normalize(
                            node.getAttribute?.('placeholder')
                            || node.textContent
                            || node.innerText
                            || ''
                        ))
                        .filter(Boolean);
                const collectHintTexts = (selectors, placeholderHints) => {
                    const hints = new Set();
                    for (const selector of splitSelectors(selectors)) {
                        for (const node of Array.from(document.querySelectorAll(selector))) {
                            if (!isVisible(node)) continue;
                            const scopedNodes = [node, ...Array.from(node.querySelectorAll('span, div, input, textarea, [contenteditable=\"true\"]'))];
                            for (const current of scopedNodes) {
                                if (!isVisible(current)) continue;
                                const text = normalize(
                                    current.getAttribute?.('placeholder')
                                    || current.textContent
                                    || current.innerText
                                    || ''
                                );
                                if (!text || text.length > 40) continue;
                                if (placeholderHints.some((hint) => text.includes(hint))) {
                                    hints.add(text);
                                }
                            }
                        }
                    }
                    return Array.from(hints);
                };

                const bodyText = normalize(document.body?.innerText || '');
                const placeholderHints = [
                    '留下你的精彩评论吧',
                    '说点什么',
                    '写评论',
                    '参与讨论',
                    '回复',
                    '请先登录后发表评论',
                    '登录后即可参与互动讨论',
                    '立即登录',
                ];
                const surfaceTexts = [
                    ...visibleTextsFromSelectors(config.placeholderNodeSelectors),
                    ...visibleTextsFromSelectors(config.editorSelectors),
                    ...collectHintTexts(config.containerSelectors, placeholderHints),
                ].filter((text) => text.length <= 80);
                const placeholderText =
                    surfaceTexts.find((text) => placeholderHints.some((hint) => text.includes(hint))) || '';
                const loginHint =
                    [
                        '请先登录后发表评论',
                        '登录后即可参与互动讨论',
                        '立即登录',
                        '登录后免费畅享高清视频',
                    ].find((item) => bodyText.includes(item) || placeholderText.includes(item)) || '';

                return {
                    hasVisibleEditor: hasVisible(config.editorSelectors),
                    hasVisibleContainer: hasVisible(config.containerSelectors),
                    hasCommentList: hasVisible(config.commentListSelectors),
                    hasCommentEntry: hasVisible(config.commentEntrySelectors),
                    placeholderText,
                    loginHint,
                };
            }
            """,
            {
                "editorSelectors": self._comment_editor_selector(),
                "containerSelectors": self._comment_container_selector(),
                "placeholderNodeSelectors": self._comment_placeholder_node_selector(),
                "commentListSelectors": '[data-e2e="comment-list"], .comment-mainContent, [class*="comment-mainContent"]',
                "commentEntrySelectors": '[data-e2e="feed-comment-icon"], .comment-title, [class*="comment-title"]',
            },
        )

    async def _read_like_surface_state(self, page: Page) -> Dict:
        return await page.evaluate(
            """
            () => {
                const node = document.querySelector('[data-e2e="video-player-digg"]');
                if (!node) {
                    return {
                        found: false,
                        state: '',
                        label: '',
                        visible: false,
                        iconColor: '',
                    };
                }
                const isVisible = (el) => {
                    if (!el || typeof el.getBoundingClientRect !== 'function') return false;
                    const style = window.getComputedStyle(el);
                    if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        return false;
                    }
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const icon =
                    node.querySelector('span[role="img"]')
                    || node.querySelector('svg')
                    || node.querySelector('path');
                const iconStyle = icon ? window.getComputedStyle(icon) : null;
                const iconColor = String(
                    iconStyle?.color
                    || iconStyle?.fill
                    || icon?.getAttribute?.('fill')
                    || ''
                ).trim();
                const label = String(node.textContent || '').replace(/\\s+/g, ' ').trim();
                return {
                    found: true,
                    state: String(node.getAttribute('data-e2e-state') || '').trim(),
                    label,
                    visible: isVisible(node) || isVisible(node.parentElement),
                    iconColor,
                };
            }
            """
        )

    async def _read_like_click_targets(self, page: Page) -> List[Dict]:
        return await page.evaluate(
            """
            () => {
                const likeNode = document.querySelector('[data-e2e="video-player-digg"]');
                const isVisible = (el) => {
                    if (!el || typeof el.getBoundingClientRect !== 'function') return false;
                    const style = window.getComputedStyle(el);
                    if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        return false;
                    }
                    const rect = el.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const toRect = (el) => {
                    if (!el || typeof el.getBoundingClientRect !== 'function') return null;
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) {
                        const clientRect = Array.from(el.getClientRects?.() || []).find((item) => item.width > 0 && item.height > 0);
                        if (clientRect) {
                            return {
                                left: clientRect.left,
                                top: clientRect.top,
                                width: clientRect.width,
                                height: clientRect.height,
                            };
                        }
                        return null;
                    }
                    return {
                        left: rect.left,
                        top: rect.top,
                        width: rect.width,
                        height: rect.height,
                    };
                };
                const describeHit = (x, y) => {
                    const hit = document.elementFromPoint(x, y);
                    return {
                        hitTag: String(hit?.tagName || ''),
                        hitClass: String(hit?.className || ''),
                        hitText: String(hit?.textContent || '').replace(/\\s+/g, ' ').trim().slice(0, 80),
                        hitRole: String(hit?.getAttribute?.('role') || ''),
                        hitDataE2E: String(hit?.getAttribute?.('data-e2e') || ''),
                    };
                };
                const candidates = [];
                const seen = new Set();
                const pushCandidate = (label, node, useUpperHalf = false) => {
                    if (!node || seen.has(node)) return;
                    seen.add(node);
                    const rect = toRect(node);
                    if (!rect) return;
                    const x = rect.left + rect.width / 2;
                    const y = useUpperHalf
                        ? rect.top + Math.max(10, Math.min(rect.height * 0.28, 18))
                        : rect.top + rect.height / 2;
                    candidates.push({
                        label,
                        x,
                        y,
                        width: rect.width,
                        height: rect.height,
                        visible: isVisible(node),
                        ...describeHit(x, y),
                    });
                };

                if (!likeNode) {
                    return [];
                }

                pushCandidate('点赞外层容器', likeNode.closest('div[tabindex="0"]'));
                pushCandidate('点赞图标容器', likeNode.querySelector('.UIQajZAR'));
                pushCandidate('心形图标', likeNode.querySelector('span[role="img"]'));
                pushCandidate('点赞块上半区', likeNode, true);
                pushCandidate('点赞本体中心', likeNode);

                return candidates;
            }
            """
        )

    async def _activate_comment_input(
        self,
        page: Page,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> str:
        placeholder_locator = page.locator(self._visible_comment_placeholder_selector()).first
        try:
            await placeholder_locator.wait_for(state="visible", timeout=1200)
            label = str((await placeholder_locator.text_content()) or "").strip() or "评论占位提示"
            await placeholder_locator.click(timeout=5000)
            self._emit(logger, f"[抖音视频评论] 已激活评论输入区：{label}", "info")
            await page.wait_for_timeout(900)
            return label
        except Exception:
            pass

        input_locator = page.locator(self._visible_comment_editor_selector()).first
        try:
            await input_locator.wait_for(state="visible", timeout=1200)
            placeholder = await input_locator.get_attribute("placeholder")
            label = str(placeholder or "").strip() or "评论输入框"
            await input_locator.click(timeout=5000)
            self._emit(logger, f"[抖音视频评论] 已激活评论输入区：{label}", "info")
            await page.wait_for_timeout(900)
            return label
        except Exception:
            pass

        draft_locator = page.locator(
            '.comment-input-inner-container .DraftEditor-root:visible, '
            '.comment-input-inner-container .DraftEditor-editorContainer:visible, '
            '.comment-input-inner-container .richtext-container:visible, '
            '.comment-input-outer-container .DraftEditor-root:visible, '
            '.comment-input-outer-container .DraftEditor-editorContainer:visible, '
            '.comment-input-outer-container .richtext-container:visible, '
            '[class*="comment-input-inner-container"] .DraftEditor-root:visible, '
            '[class*="comment-input-inner-container"] .DraftEditor-editorContainer:visible, '
            '[class*="comment-input-inner-container"] .richtext-container:visible, '
            '[class*="comment-input-outer-container"] .DraftEditor-root:visible, '
            '[class*="comment-input-outer-container"] .DraftEditor-editorContainer:visible, '
            '[class*="comment-input-outer-container"] .richtext-container:visible'
        ).first
        try:
            await draft_locator.wait_for(state="visible", timeout=1200)
            await draft_locator.click(timeout=5000, force=True)
            self._emit(logger, "[抖音视频评论] 已激活评论输入区：DraftEditor 输入层", "info")
            await page.wait_for_timeout(900)
            return "DraftEditor 输入层"
        except Exception:
            pass

        container_locator = page.locator(self._visible_comment_container_selector()).first
        try:
            await container_locator.wait_for(state="visible", timeout=1200)
            box = await container_locator.bounding_box()
            if box:
                click_x = max(18, min(box["width"] - 18, 42))
                click_y = max(12, min(box["height"] - 12, box["height"] / 2))
                await container_locator.click(
                    position={"x": click_x, "y": click_y},
                    timeout=5000,
                    force=True,
                )
            else:
                await container_locator.click(timeout=5000, force=True)
            self._emit(logger, "[抖音视频评论] 已激活评论输入区：评论输入容器", "info")
            await page.wait_for_timeout(900)
            return "评论输入容器"
        except Exception:
            return ""

    async def _ensure_comment_input_ready(
        self,
        page: Page,
        logger: Optional[Callable[[str, str], None]] = None,
    ):
        for _ in range(3):
            try:
                return await self._wait_for_visible_comment_editor(page, timeout=2500)
            except Exception:
                pass

            state = await self._read_comment_surface_state(page)
            login_hint = str((state or {}).get("loginHint", "") or "").strip()
            if login_hint:
                raise RuntimeError(f"当前抖音账号未处于可评论状态：{login_hint}")

            activated = await self._activate_comment_input(page, logger=logger)
            if activated:
                try:
                    return await self._wait_for_visible_comment_editor(page, timeout=3500)
                except Exception:
                    pass

            if (state or {}).get("hasVisibleContainer") or (state or {}).get("hasCommentList"):
                await page.wait_for_timeout(1200)

        state = await self._read_comment_surface_state(page)
        placeholder_text = str((state or {}).get("placeholderText", "") or "").strip()
        details = []
        if (state or {}).get("hasCommentList"):
            details.append("评论列表已展开")
        if (state or {}).get("hasVisibleContainer"):
            details.append("评论输入容器已出现")
        if placeholder_text:
            details.append(f"可见提示：{placeholder_text[:40]}")
        detail_text = "；".join(details) if details else "未识别到评论输入区域"
        raise RuntimeError(f"评论区已打开，但输入框仍未激活：{detail_text}")

    async def _focus_comment_editor(
        self,
        page: Page,
        input_locator,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        placeholder_locator = page.locator(self._visible_comment_placeholder_selector()).first
        try:
            await placeholder_locator.wait_for(state="visible", timeout=1200)
            label = str((await placeholder_locator.text_content()) or "").strip() or "评论占位提示"
            await placeholder_locator.click(timeout=5000, force=True)
            self._emit(logger, f"[抖音视频评论] 已点击评论占位提示：{label}", "info")
            await page.wait_for_timeout(500)
        except Exception:
            pass

        draft_locator = page.locator(
            '.comment-input-inner-container .DraftEditor-root:visible, '
            '.comment-input-inner-container .DraftEditor-editorContainer:visible, '
            '.comment-input-inner-container .richtext-container:visible, '
            '.comment-input-outer-container .DraftEditor-root:visible, '
            '.comment-input-outer-container .DraftEditor-editorContainer:visible, '
            '.comment-input-outer-container .richtext-container:visible, '
            '[class*="comment-input-inner-container"] .DraftEditor-root:visible, '
            '[class*="comment-input-inner-container"] .DraftEditor-editorContainer:visible, '
            '[class*="comment-input-inner-container"] .richtext-container:visible, '
            '[class*="comment-input-outer-container"] .DraftEditor-root:visible, '
            '[class*="comment-input-outer-container"] .DraftEditor-editorContainer:visible, '
            '[class*="comment-input-outer-container"] .richtext-container:visible'
        ).first
        try:
            await draft_locator.wait_for(state="visible", timeout=1200)
            await draft_locator.click(timeout=5000, force=True)
            self._emit(logger, "[抖音视频评论] 已点击 DraftEditor 输入层", "info")
            await page.wait_for_timeout(500)
        except Exception:
            pass

        container_locator = page.locator(self._visible_comment_container_selector()).first
        try:
            await container_locator.wait_for(state="visible", timeout=1200)
            box = await container_locator.bounding_box()
            if box:
                click_x = max(20, min(box["width"] - 20, 54))
                click_y = max(12, min(box["height"] - 12, box["height"] / 2))
                await container_locator.click(
                    position={"x": click_x, "y": click_y},
                    timeout=5000,
                    force=True,
                )
            else:
                await container_locator.click(timeout=5000, force=True)
            self._emit(logger, "[抖音视频评论] 已点击评论输入容器", "info")
            await page.wait_for_timeout(500)
        except Exception:
            pass

        await input_locator.scroll_into_view_if_needed()
        try:
            await input_locator.click(timeout=5000, force=True)
        except Exception:
            pass
        try:
            await input_locator.focus()
        except Exception:
            pass
        try:
            await input_locator.evaluate(
                """
                (el) => {
                    if (typeof el.focus === 'function') {
                        el.focus();
                    }
                    if ('selectionStart' in el && typeof el.value === 'string') {
                        const len = el.value.length;
                        try {
                            el.selectionStart = len;
                            el.selectionEnd = len;
                        } catch (error) {
                        }
                    }
                }
                """
            )
        except Exception:
            pass

        focused = False
        try:
            focused = bool(
                await input_locator.evaluate(
                    """
                    (el) => {
                        const active = document.activeElement;
                        return active === el || !!el.contains?.(active);
                    }
                    """
                )
            )
        except Exception:
            focused = False

        if not focused:
            raise RuntimeError("评论输入框已出现，但点击后仍未成功聚焦")

    async def _nudge_private_message_region_into_view(
        self,
        page: Page,
        input_locator,
        send_button,
        dialog_locator=None,
    ) -> Dict[str, int]:
        return await page.evaluate(
            """
            ([dialogEl, inputEl, sendEl]) => {
                const visibleArea = (el) => {
                    if (!el || typeof el.getBoundingClientRect !== 'function') return 0;
                    const style = window.getComputedStyle(el);
                    if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        return 0;
                    }
                    const rect = el.getBoundingClientRect();
                    if (rect.width <= 0 || rect.height <= 0) return 0;
                    const left = Math.max(rect.left, 0);
                    const top = Math.max(rect.top, 0);
                    const right = Math.min(rect.right, window.innerWidth);
                    const bottom = Math.min(rect.bottom, window.innerHeight);
                    return Math.round(Math.max(0, right - left) * Math.max(0, bottom - top));
                };
                const scrollAncestors = (el) => {
                    const items = [];
                    let current = el?.parentElement || null;
                    while (current && current !== document.body && current !== document.documentElement) {
                        const style = window.getComputedStyle(current);
                        const overflowY = `${style.overflowY || ''}${style.overflow || ''}`;
                        const scrollable = /(auto|scroll|overlay)/i.test(overflowY) && current.scrollHeight > current.clientHeight + 20;
                        if (scrollable) items.push(current);
                        current = current.parentElement;
                    }
                    return items;
                };
                const centerNode = (el) => {
                    if (!el || typeof el.getBoundingClientRect !== 'function') return;
                    try {
                        el.scrollIntoView({ block: 'center', inline: 'nearest' });
                    } catch (_) {}
                    for (const ancestor of scrollAncestors(el)) {
                        try {
                            const rect = el.getBoundingClientRect();
                            const hostRect = ancestor.getBoundingClientRect();
                            const delta = rect.top - hostRect.top - Math.max((ancestor.clientHeight - rect.height) / 2, 120);
                            ancestor.scrollTop += delta;
                        } catch (_) {}
                    }
                };

                const panel =
                    dialogEl ||
                    document.querySelector('.RightPanel') ||
                    document.querySelector('#messageContent') ||
                    document.querySelector('[data-e2e="im-dialog"]') ||
                    document.querySelector('div[data-e2e="msg-input"]')?.closest('aside, section, div');

                if (document.scrollingElement) {
                    try {
                        document.scrollingElement.scrollTop = Math.max(0, document.scrollingElement.scrollTop - 220);
                    } catch (_) {}
                }
                centerNode(panel);
                centerNode(inputEl);
                centerNode(sendEl);

                return {
                    dialogArea: visibleArea(panel),
                    inputArea: visibleArea(inputEl),
                    sendArea: visibleArea(sendEl),
                    pageScrollY: Math.round(window.scrollY || window.pageYOffset || 0),
                };
            }
            """,
            [
                await dialog_locator.element_handle() if dialog_locator is not None else None,
                await input_locator.element_handle(),
                await send_button.element_handle(),
            ],
        )

    async def _wait_for_message_composer_ready(
        self,
        page: Page,
        input_locator,
        send_button,
        dialog_locator=None,
        logger: Optional[Callable[[str, str], None]] = None,
        log_prefix: str = "[抖音私信]",
        timeout_ms: int = 12000,
    ) -> None:
        deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000.0)
        last_reason = "未拿到更多细节"
        while asyncio.get_event_loop().time() < deadline:
            try:
                if dialog_locator is not None:
                    await dialog_locator.wait_for(state="visible", timeout=1000)
                await input_locator.wait_for(state="visible", timeout=1000)
                await send_button.wait_for(state="visible", timeout=1000)
                try:
                    await input_locator.scroll_into_view_if_needed()
                except Exception:
                    pass
                try:
                    await send_button.scroll_into_view_if_needed()
                except Exception:
                    pass
                state = await self._nudge_private_message_region_into_view(
                    page,
                    input_locator,
                    send_button,
                    dialog_locator=dialog_locator,
                )
                dialog_area = int((state or {}).get("dialogArea", 0) or 0)
                input_area = int((state or {}).get("inputArea", 0) or 0)
                send_area = int((state or {}).get("sendArea", 0) or 0)
                if input_area > 0 and send_area > 0:
                    self._emit(
                        logger,
                        f"{log_prefix} 私信输入区已稳定，弹层可见面积 {dialog_area}，输入框可见面积 {input_area}，发送按钮可见面积 {send_area}",
                        "info",
                    )
                    return
                last_reason = f"弹层可见面积 {dialog_area}，输入框可见面积 {input_area}，发送按钮可见面积 {send_area}"
            except Exception as exc:
                last_reason = str(exc)
            await page.wait_for_timeout(350)
        raise RuntimeError(f"私信面板已打开，但发送区仍未稳定进入视口：{last_reason}")

    async def _find_visible_private_message_button(self, page: Page, selector: str) -> Dict[str, object]:
        buttons = page.locator(selector)
        try:
            total = await buttons.count()
        except Exception as exc:
            return {"index": -1, "count": 0, "area": 0, "reason": str(exc)}
        if total <= 0:
            return {"index": -1, "count": 0, "area": 0, "reason": f"{selector} 未匹配到任何按钮"}

        inspect_total = min(total, 8)
        last_reason = f"{selector} 共 {total} 个匹配按钮，均不可见"
        for idx in range(inspect_total):
            button = buttons.nth(idx)
            try:
                if not await button.is_visible():
                    last_reason = f"{selector} 第 {idx + 1}/{total} 个匹配按钮不可见"
                    continue
                try:
                    await button.scroll_into_view_if_needed()
                except Exception:
                    pass
                area = await button.evaluate(
                    """
                    (el) => {
                        if (!el || typeof el.getBoundingClientRect !== 'function') return 0;
                        const style = window.getComputedStyle(el);
                        if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            return 0;
                        }
                        const rect = el.getBoundingClientRect();
                        const left = Math.max(rect.left, 0);
                        const top = Math.max(rect.top, 0);
                        const right = Math.min(rect.right, window.innerWidth);
                        const bottom = Math.min(rect.bottom, window.innerHeight);
                        return Math.round(Math.max(0, right - left) * Math.max(0, bottom - top));
                    }
                    """
                )
                visible_area = int(area or 0)
                if visible_area > 0:
                    return {"index": idx, "count": total, "area": visible_area, "reason": ""}
                last_reason = f"{selector} 第 {idx + 1}/{total} 个匹配按钮可见面积 0"
            except Exception as exc:
                last_reason = str(exc)
        return {"index": -1, "count": total, "area": 0, "reason": last_reason}

    async def _mark_dom_private_message_button(self, page: Page) -> Dict[str, object]:
        marker = f"aihuoke-pm-{random.randint(100000, 999999)}"
        result = await page.evaluate(
            r"""
            (marker) => {
                const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
                const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
                const areaOf = (rect) => {
                    if (!rect) return 0;
                    const left = Math.max(rect.left, 0);
                    const top = Math.max(rect.top, 0);
                    const right = Math.min(rect.right, viewportWidth);
                    const bottom = Math.min(rect.bottom, viewportHeight);
                    return Math.round(Math.max(0, right - left) * Math.max(0, bottom - top));
                };
                const describe = (el) => {
                    if (!el || typeof el.getBoundingClientRect !== 'function') return null;
                    const rect = el.getBoundingClientRect();
                    const style = window.getComputedStyle(el);
                    const centerX = Math.max(0, Math.min(viewportWidth - 1, rect.left + rect.width / 2));
                    const centerY = Math.max(0, Math.min(viewportHeight - 1, rect.top + rect.height / 2));
                    const topElement = document.elementFromPoint(centerX, centerY);
                    return {
                        tag: String(el.tagName || '').toLowerCase(),
                        text: normalize(el.textContent),
                        className: String(el.className || ''),
                        disabled: !!el.disabled,
                        ariaDisabled: String(el.getAttribute('aria-disabled') || ''),
                        display: style.display,
                        visibility: style.visibility,
                        opacity: style.opacity,
                        pointerEvents: style.pointerEvents,
                        rect: {
                            x: Math.round(rect.x),
                            y: Math.round(rect.y),
                            width: Math.round(rect.width),
                            height: Math.round(rect.height),
                        },
                        area: areaOf(rect),
                        centerCoveredBy: topElement && topElement !== el && !el.contains(topElement)
                            ? `${String(topElement.tagName || '').toLowerCase()}.${String(topElement.className || '').slice(0, 80)}`
                            : '',
                    };
                };
                document.querySelectorAll('[data-aihuoke-private-message-button]').forEach((node) => {
                    node.removeAttribute('data-aihuoke-private-message-button');
                });
                const rawNodes = [
                    ...document.querySelectorAll('button, [role="button"], span.semi-button-content, .semi-button-content'),
                ];
                const seen = new Set();
                const candidates = [];
                for (const raw of rawNodes) {
                    const button = raw.closest('button, [role="button"]') || raw;
                    if (!button || seen.has(button)) continue;
                    seen.add(button);
                    const text = normalize(button.textContent);
                    if (text !== '私信' && !/^私信(\s|$)/.test(text)) continue;
                    const info = describe(button);
                    if (!info) continue;
                    candidates.push({ node: button, info });
                }
                candidates.sort((left, right) => {
                    const leftInfo = left.info;
                    const rightInfo = right.info;
                    const leftBad = leftInfo.disabled || leftInfo.ariaDisabled === 'true' || leftInfo.display === 'none' || leftInfo.visibility === 'hidden';
                    const rightBad = rightInfo.disabled || rightInfo.ariaDisabled === 'true' || rightInfo.display === 'none' || rightInfo.visibility === 'hidden';
                    if (leftBad !== rightBad) return leftBad ? 1 : -1;
                    return Number(rightInfo.area || 0) - Number(leftInfo.area || 0);
                });
                const debug = candidates.slice(0, 8).map((item) => item.info);
                for (const item of candidates) {
                    const info = item.info;
                    const bad = info.disabled || info.ariaDisabled === 'true' || info.display === 'none' || info.visibility === 'hidden';
                    if (bad) continue;
                    item.node.setAttribute('data-aihuoke-private-message-button', marker);
                    try {
                        item.node.scrollIntoView({ block: 'center', inline: 'center' });
                    } catch (error) {}
                    return { found: true, marker, candidate: info, debug };
                }
                return { found: false, marker: '', candidate: null, debug };
            }
            """,
            marker,
        )
        return result if isinstance(result, dict) else {"found": False, "marker": "", "debug": []}

    async def _click_marked_private_message_button(
        self,
        page: Page,
        marker: str,
        logger: Optional[Callable[[str, str], None]] = None,
        log_prefix: str = "[抖音私信]",
        click_method: str = "auto",
    ) -> bool:
        marker = str(marker or "").strip()
        if not marker:
            return False
        locator = page.locator(f'[data-aihuoke-private-message-button="{marker}"]').first
        try:
            await locator.scroll_into_view_if_needed(timeout=2000)
        except Exception:
            pass
        click_method = str(click_method or "auto").strip().lower()
        methods = [click_method] if click_method in {"normal", "force", "coordinate", "native"} else [
            "normal",
            "force",
            "coordinate",
            "native",
        ]
        for method in methods:
            if method == "normal":
                try:
                    await locator.click(timeout=3500)
                    self._emit(logger, f"{log_prefix} 已通过 DOM 标记按钮常规点击私信")
                    return True
                except Exception as exc:
                    self._emit(logger, f"{log_prefix} DOM 标记按钮常规点击失败：{exc}", "warning")
            elif method == "force":
                try:
                    await locator.click(timeout=3500, force=True)
                    self._emit(logger, f"{log_prefix} 已通过 DOM 标记按钮强制点击私信")
                    return True
                except Exception as exc:
                    self._emit(logger, f"{log_prefix} DOM 标记按钮强制点击失败：{exc}", "warning")
            elif method == "coordinate":
                try:
                    box = await locator.bounding_box(timeout=2000)
                    if box:
                        await page.mouse.click(box["x"] + box["width"] / 2, box["y"] + box["height"] / 2)
                        self._emit(logger, f"{log_prefix} 已通过坐标点击私信按钮")
                        return True
                except Exception as exc:
                    self._emit(logger, f"{log_prefix} 坐标点击私信按钮失败：{exc}", "warning")
            elif method == "native":
                try:
                    clicked = await page.evaluate(
                        r"""
                        (marker) => {
                            const node = document.querySelector(`[data-aihuoke-private-message-button="${marker}"]`);
                            if (!node) return false;
                            node.click();
                            return true;
                        }
                        """,
                        marker,
                    )
                    if clicked:
                        self._emit(logger, f"{log_prefix} 已通过原生 DOM click 触发私信按钮")
                        return True
                except Exception as exc:
                    self._emit(logger, f"{log_prefix} 原生 DOM click 私信按钮失败：{exc}", "warning")
        return False

    def _format_private_button_debug(self, debug: object, limit: int = 3) -> str:
        if not isinstance(debug, list) or not debug:
            return "未发现文本为私信的 DOM 候选按钮"
        parts = []
        for item in debug[: max(1, limit)]:
            if not isinstance(item, dict):
                continue
            rect = item.get("rect") if isinstance(item.get("rect"), dict) else {}
            parts.append(
                "text={text}, area={area}, rect={x},{y},{w}x{h}, display={display}, "
                "visibility={visibility}, opacity={opacity}, disabled={disabled}, ariaDisabled={aria}, coveredBy={covered}".format(
                    text=str(item.get("text", "") or "-"),
                    area=str(item.get("area", 0) or 0),
                    x=str(rect.get("x", "-")),
                    y=str(rect.get("y", "-")),
                    w=str(rect.get("width", "-")),
                    h=str(rect.get("height", "-")),
                    display=str(item.get("display", "") or "-"),
                    visibility=str(item.get("visibility", "") or "-"),
                    opacity=str(item.get("opacity", "") or "-"),
                    disabled=str(item.get("disabled", "")),
                    aria=str(item.get("ariaDisabled", "") or "-"),
                    covered=str(item.get("centerCoveredBy", "") or "-"),
                )
            )
        return "；".join(parts) or "未发现文本为私信的 DOM 候选按钮"

    async def _wait_for_profile_action_area_ready(
        self,
        page: Page,
        logger: Optional[Callable[[str, str], None]] = None,
        log_prefix: str = "[抖音私信]",
        timeout_ms: int = 20000,
    ) -> Dict[str, object]:
        deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000.0)
        stable_hits = 0
        stable_key = ""
        last_state: Dict[str, object] = {}
        self._emit(logger, f"{log_prefix} 等待主页用户信息区和操作按钮区加载完成", "info")

        while asyncio.get_event_loop().time() < deadline:
            try:
                await self._raise_if_profile_unavailable(page)
                await self._raise_if_login_intercept(page)
            except Exception:
                raise
            state = await page.evaluate(
                r"""
                () => {
                    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                    const viewportWidth = window.innerWidth || document.documentElement.clientWidth || 0;
                    const viewportHeight = window.innerHeight || document.documentElement.clientHeight || 0;
                    const areaOf = (el) => {
                        if (!el || typeof el.getBoundingClientRect !== 'function') return 0;
                        const rect = el.getBoundingClientRect();
                        const left = Math.max(rect.left, 0);
                        const top = Math.max(rect.top, 0);
                        const right = Math.min(rect.right, viewportWidth);
                        const bottom = Math.min(rect.bottom, viewportHeight);
                        return Math.round(Math.max(0, right - left) * Math.max(0, bottom - top));
                    };
                    const describeButton = (el) => {
                        const rect = el.getBoundingClientRect();
                        const style = window.getComputedStyle(el);
                        return {
                            text: normalize(el.textContent),
                            tag: String(el.tagName || '').toLowerCase(),
                            className: String(el.className || ''),
                            area: areaOf(el),
                            disabled: !!el.disabled,
                            ariaDisabled: String(el.getAttribute('aria-disabled') || ''),
                            display: style.display,
                            visibility: style.visibility,
                            opacity: style.opacity,
                            rect: {
                                x: Math.round(rect.x),
                                y: Math.round(rect.y),
                                width: Math.round(rect.width),
                                height: Math.round(rect.height),
                            },
                        };
                    };
                    const profileRoot =
                        document.querySelector('#user_detail_element')
                        || document.querySelector('[data-e2e="user-detail"]')
                        || document.querySelector('[data-e2e*="user"][data-e2e*="detail"]')
                        || document.querySelector('[data-e2e="user-info"]')
                        || document.querySelector('[class*="user-info"]');
                    const allButtons = Array.from(document.querySelectorAll('button, [role="button"], span.semi-button-content, .semi-button-content'));
                    const buttonNodes = [];
                    const seen = new Set();
                    for (const raw of allButtons) {
                        const node = raw.closest('button, [role="button"]') || raw;
                        if (!node || seen.has(node)) continue;
                        seen.add(node);
                        const text = normalize(node.textContent);
                        if (!text) continue;
                        if (text.includes('私信') || text.includes('关注') || text.includes('已关注') || text.includes('互相关注')) {
                            buttonNodes.push(node);
                        }
                    }
                    const privateButtons = buttonNodes.filter((node) => normalize(node.textContent) === '私信');
                    const actionTexts = buttonNodes.map((node) => normalize(node.textContent)).slice(0, 12);
                    try {
                        if (profileRoot) profileRoot.scrollIntoView({ block: 'center', inline: 'nearest' });
                    } catch (error) {}
                    const profileArea = areaOf(profileRoot);
                    const privateDebug = privateButtons.slice(0, 6).map(describeButton);
                    return {
                        readyState: document.readyState,
                        url: location.href,
                        title: document.title,
                        profileRootFound: !!profileRoot,
                        profileArea,
                        actionTexts,
                        privateCount: privateButtons.length,
                        privateDebug,
                        bodyTextLength: normalize(document.body?.innerText || '').length,
                    };
                }
                """
            )
            if isinstance(state, dict):
                last_state = state
            profile_ready = bool((state or {}).get("profileRootFound")) and int((state or {}).get("profileArea", 0) or 0) > 0
            private_count = int((state or {}).get("privateCount", 0) or 0)
            action_texts = [str(item or "") for item in ((state or {}).get("actionTexts") or [])]
            has_action_area = private_count > 0 or any("关注" in text for text in action_texts)
            current_key = "|".join([
                str(bool(profile_ready)),
                str(private_count),
                ",".join(action_texts[:4]),
                str(int((state or {}).get("bodyTextLength", 0) or 0) // 200),
            ])
            if profile_ready and has_action_area:
                if current_key == stable_key:
                    stable_hits += 1
                else:
                    stable_key = current_key
                    stable_hits = 1
                if stable_hits >= 2:
                    self._emit(
                        logger,
                        f"{log_prefix} 主页操作区加载完成：私信按钮 {private_count} 个，操作按钮 {', '.join(action_texts[:4]) or '-'}",
                        "info",
                    )
                    return state if isinstance(state, dict) else {}
            else:
                stable_hits = 0
                stable_key = current_key
            await page.wait_for_timeout(500)

        private_debug = self._format_private_button_debug(last_state.get("privateDebug"), limit=3)
        self._emit(
            logger,
            (
                f"{log_prefix} 等待主页操作区加载超时，继续尝试私信按钮兜底检测："
                f"profileRootFound={last_state.get('profileRootFound', '-')}, "
                f"profileArea={last_state.get('profileArea', '-')}, "
                f"privateCount={last_state.get('privateCount', '-')}, "
                f"actionTexts={last_state.get('actionTexts', [])}, {private_debug}"
            ),
            "warning",
        )
        return last_state

    async def _wait_for_private_message_button_ready(
        self,
        page: Page,
        button_selectors: List[str],
        logger: Optional[Callable[[str, str], None]] = None,
        log_prefix: str = "[抖音私信]",
        timeout_ms: int = 20000,
    ) -> Dict[str, object]:
        deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000.0)
        last_reason = "未识别到可用私信按钮"
        stable_hits = 0
        matched_selector_key = ""
        self._emit(logger, f"{log_prefix} 主页已打开，等待页面稳定后再检测私信按钮", "info")
        while asyncio.get_event_loop().time() < deadline:
            try:
                await self._raise_if_login_intercept(page)
            except Exception:
                raise
            found_visible_candidate = False
            for selector in button_selectors:
                try:
                    candidate = await self._find_visible_private_message_button(page, selector)
                    visible_index = int(candidate.get("index", -1) or -1)
                    visible_area = int(candidate.get("area", 0) or 0)
                    visible_count = int(candidate.get("count", 0) or 0)
                    candidate_key = f"{selector}#{visible_index}"
                    if visible_index >= 0 and visible_area > 0:
                        if matched_selector_key == candidate_key:
                            stable_hits += 1
                        else:
                            matched_selector_key = candidate_key
                            stable_hits = 1
                        last_reason = f"{selector} 第 {visible_index + 1}/{max(visible_count, 1)} 个匹配按钮可见面积 {visible_area}"
                        if stable_hits >= 2:
                            self._emit(
                                logger,
                                f"{log_prefix} 页面稳定完成，准备点击私信按钮：{selector}（第 {visible_index + 1}/{max(visible_count, 1)} 个匹配元素）",
                                "info",
                            )
                            return {"selector": selector, "index": visible_index}
                        found_visible_candidate = True
                        break
                    else:
                        last_reason = str(candidate.get("reason", "") or f"{selector} 当前没有可见按钮")
                except Exception as exc:
                    last_reason = str(exc)
                    continue
            try:
                dom_candidate = await self._mark_dom_private_message_button(page)
                if dom_candidate.get("found"):
                    marker = str(dom_candidate.get("marker", "") or "")
                    candidate = dom_candidate.get("candidate") if isinstance(dom_candidate.get("candidate"), dict) else {}
                    rect = candidate.get("rect") if isinstance(candidate.get("rect"), dict) else {}
                    area = int((candidate or {}).get("area", 0) or 0)
                    candidate_key = "|".join([
                        "dom",
                        str(candidate.get("text", "") or ""),
                        str(area),
                        str(rect.get("x", "")),
                        str(rect.get("y", "")),
                        str(rect.get("width", "")),
                        str(rect.get("height", "")),
                    ])
                    if matched_selector_key == candidate_key:
                        stable_hits += 1
                    else:
                        matched_selector_key = candidate_key
                        stable_hits = 1
                    last_reason = f"DOM 候选私信按钮可见面积 {area}，{self._format_private_button_debug(dom_candidate.get('debug'), limit=2)}"
                    candidate_is_ready = (
                        area > 0
                        and not bool(candidate.get("disabled"))
                        and str(candidate.get("ariaDisabled", "") or "").lower() != "true"
                        and str(candidate.get("display", "") or "").lower() != "none"
                        and str(candidate.get("visibility", "") or "").lower() != "hidden"
                        and str(candidate.get("opacity", "") or "") != "0"
                        and not str(candidate.get("centerCoveredBy", "") or "").strip()
                    )
                    if stable_hits >= 2 or candidate_is_ready:
                        self._emit(
                            logger,
                            f"{log_prefix} 页面稳定完成，准备点击 DOM 候选私信按钮：{self._format_private_button_debug(dom_candidate.get('debug'), limit=1)}",
                            "info",
                        )
                        return {"selector": f'[data-aihuoke-private-message-button="{marker}"]', "index": 0, "marker": marker}
                    found_visible_candidate = True
                else:
                    last_reason = self._format_private_button_debug(dom_candidate.get("debug"))
            except Exception as exc:
                last_reason = str(exc)
            if found_visible_candidate:
                await page.wait_for_timeout(500)
                continue
            await page.wait_for_timeout(500)
        raise RuntimeError(f"主页已打开，但私信按钮仍未稳定可点：{last_reason}")

    async def _focus_message_editor(
        self,
        page: Page,
        input_locator,
        send_button,
        dialog_locator=None,
        logger: Optional[Callable[[str, str], None]] = None,
        log_prefix: str = "[抖音私信]",
    ) -> None:
        await self._wait_for_message_composer_ready(
            page,
            input_locator,
            send_button,
            dialog_locator=dialog_locator,
            logger=logger,
            log_prefix=log_prefix,
        )
        try:
            await input_locator.scroll_into_view_if_needed()
        except Exception:
            pass
        try:
            await input_locator.click(timeout=5000, force=True)
        except Exception:
            pass
        try:
            await input_locator.focus()
        except Exception:
            pass

        focus_state = {"focused": False, "text": ""}
        try:
            focus_state = await input_locator.evaluate(
                """
                (el) => {
                    const target = el?.matches?.('[contenteditable="true"], input, textarea')
                        ? el
                        : el?.querySelector?.('[contenteditable="true"], input, textarea') || el;
                    if (!target) return { focused: false, text: '' };
                    target.scrollIntoView({ block: 'center', inline: 'nearest' });
                    if (typeof target.focus === 'function') {
                        target.focus();
                    }
                    const selection = window.getSelection ? window.getSelection() : null;
                    if (selection && target.isContentEditable) {
                        const range = document.createRange();
                        range.selectNodeContents(target);
                        range.collapse(false);
                        selection.removeAllRanges();
                        selection.addRange(range);
                    }
                    const active = document.activeElement;
                    return {
                        focused: active === target || !!target.contains?.(active),
                        text: String(target.textContent || target.value || '').trim(),
                    };
                }
                """
            )
        except Exception:
            focus_state = {"focused": False, "text": ""}

        if not bool((focus_state or {}).get("focused")):
            raise RuntimeError("私信输入框已出现，但仍未成功聚焦")
        self._emit(logger, f"{log_prefix} 已聚焦私信输入框", "info")

    def _can_connect_cdp(self) -> bool:
        if not self.cdp_port or not is_port_open(self.cdp_port):
            return False
        try:
            resp = requests.get(f"http://127.0.0.1:{self.cdp_port}/json/version", timeout=2)
            return resp.ok
        except Exception:
            return False

    async def _has_login_intercept(self, page: Page) -> bool:
        try:
            return bool(
                await page.evaluate(
                    """
                    () => {
                        const selectors = [
                            'article#douyin_login_comp_flat_panel',
                            '#douyin_login_comp_scan_code',
                            '#douyin_login_comp_normal_input_id',
                            '#douyin_login_comp_button_input_id',
                            '#douyin_login_comp_btn_id',
                            '[id^="douyin_login_comp_"]',
                        ];
                        const isVisible = (node) => {
                            if (!node || typeof node.getBoundingClientRect !== 'function') {
                                return false;
                            }
                            const style = window.getComputedStyle(node);
                            if (!style) {
                                return false;
                            }
                            if (style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                                return false;
                            }
                            const rect = node.getBoundingClientRect();
                            return rect.width > 0 && rect.height > 0;
                        };
                        return selectors.some((selector) =>
                            Array.from(document.querySelectorAll(selector)).some((node) => isVisible(node))
                        );
                    }
                    """
                )
            )
        except Exception:
            return False

    async def _raise_if_login_intercept(self, page: Page):
        if await self._has_login_intercept(page):
            raise RuntimeError("当前抖音浏览器未登录，或登录态已失效，页面出现登录拦截")

    async def _raise_if_profile_unavailable(self, page: Page):
        try:
            unavailable = await page.evaluate(
                r"""
                () => {
                    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                    const bodyText = normalize(document.body?.innerText || '');
                    if (bodyText.includes('用户不存在')) return true;
                    const exactNode = Array.from(document.querySelectorAll('div, span, p, h1, h2'))
                        .find((node) => normalize(node.textContent || '') === '用户不存在');
                    return Boolean(exactNode);
                }
                """
            )
        except Exception:
            unavailable = False
        if unavailable:
            raise RuntimeError("用户不存在：该抖音主页已失效或已被删除，无法发送私信")

    async def _ensure_browser(self, logger: Optional[Callable[[str, str], None]] = None):
        if self._context:
            return

        self._playwright = await async_playwright().start()

        if self.allow_cdp_reuse and self._can_connect_cdp():
            self._emit(logger, f"[抖音] 复用已登录浏览器会话，port={self.cdp_port}")
            self._browser = await self._playwright.chromium.connect_over_cdp(
                f"http://127.0.0.1:{self.cdp_port}"
            )
            self._owns_browser = False
            if self._browser.contexts:
                self._context = self._browser.contexts[0]
            else:
                self._context = await self._browser.new_context(
                    viewport={"width": 1440, "height": 960},
                    locale="zh-CN",
                )
                self._owns_context = True
            return

        if self.cdp_port and self.allow_workspace_fallback:
            self._emit(logger, f"[抖音] 当前未检测到账号工作位，尝试自动拉起并复用浏览器会话，port={self.cdp_port}", "warning")
            try:
                launched = DouyinClient(self.cdp_port, account_id=self.account_id).launch_browser(
                    start_url="https://www.douyin.com/chat"
                )
                if launched and self._can_connect_cdp():
                    self._emit(logger, f"[抖音] 已自动拉起账号浏览器工作位，准备复用，port={self.cdp_port}", "info")
                    self._browser = await self._playwright.chromium.connect_over_cdp(
                        f"http://127.0.0.1:{self.cdp_port}"
                    )
                    self._owns_browser = False
                    if self._browser.contexts:
                        self._context = self._browser.contexts[0]
                    else:
                        self._context = await self._browser.new_context(
                            viewport={"width": 1440, "height": 960},
                            locale="zh-CN",
                        )
                        self._owns_context = True
                    return
            except Exception as exc:
                self._emit(logger, f"[抖音] 自动拉起账号浏览器工作位失败，准备继续尝试持久化模式：{exc}", "warning")

        launch_kwargs = {
            "user_data_dir": self.profile_dir,
            "headless": self.headless,
            "viewport": {"width": 1440, "height": 960},
            "locale": "zh-CN",
            "args": [
                "--disable-blink-features=AutomationControlled",
                "--disable-dev-shm-usage",
                "--no-sandbox",
            ],
            "user_agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) "
                "Chrome/135.0.0.0 Safari/537.36"
            ),
        }
        if not (self.headless and not self.allow_workspace_fallback):
            launch_kwargs["channel"] = "chrome"
        self._emit(
            logger,
            f"[抖音] 启动持久化浏览器，profile={self.profile_dir}，headless={self.headless}",
        )
        try:
            self._context = await self._playwright.chromium.launch_persistent_context(**launch_kwargs)
        except Exception as exc:
            message = str(exc or "")
            if self.cdp_port and self.allow_workspace_fallback and any(
                marker in message
                for marker in [
                    "Target page, context or browser has been closed",
                    "Browser has been closed",
                    "Connection closed",
                    "has been disconnected",
                ]
            ):
                self._emit(logger, "[抖音] 持久化无头浏览器启动失败，尝试改用账号工作位模式重试", "warning")
                try:
                    launched = DouyinClient(self.cdp_port, account_id=self.account_id).launch_browser(
                        start_url="https://www.douyin.com/chat"
                    )
                    if launched and self._can_connect_cdp():
                        self._browser = await self._playwright.chromium.connect_over_cdp(
                            f"http://127.0.0.1:{self.cdp_port}"
                        )
                        self._owns_browser = False
                        if self._browser.contexts:
                            self._context = self._browser.contexts[0]
                        else:
                            self._context = await self._browser.new_context(
                                viewport={"width": 1440, "height": 960},
                                locale="zh-CN",
                            )
                            self._owns_context = True
                        return
                except Exception as retry_exc:
                    self._emit(logger, f"[抖音] 账号工作位重试也失败：{retry_exc}", "warning")
            raise
        self._owns_context = True
        self._owns_browser = False

    async def close(self):
        await self._dispose_browser_runtime()

    async def _new_page(self, logger: Optional[Callable[[str, str], None]] = None) -> Page:
        await self._ensure_browser(logger=logger)
        if not self._context:
            raise RuntimeError("抖音浏览器上下文初始化失败")
        try:
            return await self._context.new_page()
        except Exception as exc:
            message = str(exc or "")
            closed_markers = [
                "Target page, context or browser has been closed",
                "Browser has been closed",
                "Connection closed",
                "has been disconnected",
            ]
            if not any(marker in message for marker in closed_markers):
                raise

            self._emit(logger, f"[抖音] 浏览器上下文已失效，准备自动重连：{message}", "warning")
            await self._dispose_browser_runtime()
            await self._ensure_browser(logger=logger)
            if not self._context:
                raise RuntimeError("抖音浏览器上下文重连失败")
            self._emit(logger, "[抖音] 浏览器会话已重连，继续执行任务", "info")
            return await self._context.new_page()

    async def open_chat_workspace_page(
        self,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Page:
        page = await self._new_page(logger=logger)
        self._emit(logger, "[抖音私信聚合] 打开当前消息页：https://www.douyin.com/chat")
        await page.goto("https://www.douyin.com/chat", wait_until="domcontentloaded", timeout=60000)
        try:
            await page.wait_for_load_state("networkidle", timeout=4000)
            self._emit(logger, "[抖音私信聚合] 当前消息页首屏网络请求已基本稳定")
        except Exception:
            self._emit(logger, "[抖音私信聚合] 等待当前消息页网络稳定超时，继续检查会话列表", "warning")
        try:
            await page.wait_for_selector(
                '[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]',
                timeout=6000,
            )
            self._emit(logger, "[抖音私信聚合] 当前消息页会话列表已出现，继续读取")
        except Exception:
            self._emit(logger, "[抖音私信聚合] 等待会话列表出现超时，继续尝试读取当前页", "warning")
        await page.wait_for_timeout(800)
        await self._raise_if_login_intercept(page)
        return page

    async def open_chat_page_conversation(
        self,
        target_page: Page,
        row: Dict,
    ) -> tuple[bool, str]:
        payload = row if isinstance(row, dict) else {}
        conversation_key = str(payload.get("conversation_key", "") or payload.get("conversation_id", "") or "").strip()
        stranger_index = str(payload.get("stranger_index", "") or "").strip()
        username = str(payload.get("username", "") or "").strip()
        profile_url = str(payload.get("profile_url", "") or "").strip()
        preview_text = str(
            payload.get("preview_text", "")
            or payload.get("incoming_message", "")
            or ""
        ).strip()
        payload = {
            "conversation_key": conversation_key,
            "stranger_index": stranger_index,
            "username": username,
            "profile_url": profile_url,
            "preview_text": preview_text,
        }

        async def confirm_selected(expected_title: str) -> bool:
            try:
                await target_page.wait_for_function(
                    r"""
                    (payload) => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const expected = normalize(payload?.username || payload?.title || '');
                        const selectedTitle = normalize(
                            document.querySelector('.conversationConversationItemcurConversation .conversationConversationItemtitle')?.textContent || ''
                        );
                        const headerTitle = normalize(
                            document.querySelector('.RightPanelHeadertitle, .RightPanelHeader .semi-typography, .semi-typography')?.textContent || ''
                        );
                        if (expected && (selectedTitle === expected || headerTitle === expected)) return true;
                        return Boolean(document.querySelector('[data-e2e="msg-item-content"]'));
                    }
                    """,
                    {"username": username, "title": expected_title},
                    timeout=4000,
                )
            except Exception:
                pass
            try:
                return bool(
                    await target_page.evaluate(
                        r"""
                        (payload) => {
                            const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                            const expected = normalize(payload?.username || payload?.title || '');
                            const selectedTitle = normalize(
                                document.querySelector('.conversationConversationItemcurConversation .conversationConversationItemtitle')?.textContent || ''
                            );
                            const headerTitle = normalize(
                                document.querySelector('.RightPanelHeadertitle, .RightPanelHeader .semi-typography, .semi-typography')?.textContent || ''
                            );
                            if (expected && (selectedTitle === expected || headerTitle === expected)) return true;
                            return Boolean(document.querySelector('[data-e2e="msg-item-content"]'));
                        }
                        """,
                        {"username": username, "title": expected_title},
                    )
                )
            except Exception:
                return False

        async def try_open_via_search() -> tuple[bool, str]:
            if not username:
                return False, "缺少会话名称"
            search_input = target_page.locator(
                'input[placeholder*="搜索"], .searchSearchInputim-saas-input input, .LeftPanelHeadersearch input, .semi-input'
            ).first
            try:
                await search_input.wait_for(state="visible", timeout=3000)
            except Exception:
                return False, "未找到消息搜索框"
            try:
                await search_input.click(timeout=3000)
                await search_input.fill("")
                await target_page.wait_for_timeout(150)
                await search_input.type(username, delay=25)
                await target_page.wait_for_timeout(700)
            except Exception as exc:
                return False, f"搜索目标会话失败：{exc}"
            click_result = None
            for _ in range(10):
                click_result = await target_page.evaluate(
                    r"""
                    (payload) => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const username = normalize(payload?.username || '');
                        const wrappers = Array.from(document.querySelectorAll('[data-index]'))
                            .filter((node) => node.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]'));
                        if (!wrappers.length) {
                            return { clicked: false, canScroll: false };
                        }
                        const rows = wrappers.map((wrapper) => {
                            const node = wrapper.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]') || wrapper;
                            const title = normalize(node.querySelector('.conversationConversationItemtitle')?.textContent || '');
                            const fullText = normalize(node.innerText || node.textContent || '');
                            return { wrapper, node, title, fullText };
                        });
                        const exact = rows.find((entry) => username && entry.title === username);
                        const partial = rows.find((entry) => username && (entry.title.includes(username) || username.includes(entry.title)));
                        const fallback = rows.find((entry) => username && entry.fullText.includes(username));
                        const target = exact || partial || fallback;
                        if (target) {
                            target.node.scrollIntoView({ block: 'center' });
                            const clickable = target.node.querySelector('.conversationConversationItemrowArea2')
                                || target.node.querySelector('.conversationConversationItemtitleWrapper')
                                || target.node.querySelector('.conversationConversationItemtitle')
                                || target.node;
                            ['pointerdown', 'mousedown', 'mouseup', 'click'].forEach((eventName) => {
                                clickable.dispatchEvent(new MouseEvent(eventName, { bubbles: true, cancelable: true, view: window }));
                            });
                            if (typeof clickable.click === 'function') clickable.click();
                            return {
                                clicked: true,
                                canScroll: true,
                                title: target.title || '',
                            };
                        }
                        const firstRow = wrappers[0];
                        let scroller = firstRow?.parentElement || null;
                        while (scroller && scroller !== document.body) {
                            if (scroller.scrollHeight > scroller.clientHeight + 80) break;
                            scroller = scroller.parentElement;
                        }
                        if (!scroller || scroller === document.body) {
                            return { clicked: false, canScroll: false };
                        }
                        const before = scroller.scrollTop;
                        scroller.scrollTop = Math.min(
                            scroller.scrollTop + Math.max(scroller.clientHeight * 0.85, 420),
                            scroller.scrollHeight
                        );
                        return {
                            clicked: false,
                            canScroll: scroller.scrollTop > before + 4,
                        };
                    }
                    """,
                    {"username": username},
                )
                if click_result and click_result.get("clicked"):
                    break
                if not click_result or not click_result.get("canScroll"):
                    break
                await target_page.wait_for_timeout(250)
            if not click_result or not click_result.get("clicked"):
                return False, "搜索后仍未命中目标会话"
            clicked_title = str(click_result.get("title", "") or "").strip()
            confirmed = await confirm_selected(clicked_title)
            try:
                await search_input.fill("")
            except Exception:
                pass
            if confirmed:
                await target_page.wait_for_timeout(600)
                return True, ""
            return False, "搜索后已点击会话，但右侧未切换"

        if stranger_index:
            try:
                await target_page.evaluate(
                    r"""
                    (payload) => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const targetIndex = Number.parseInt(normalize(payload?.stranger_index || ''), 10);
                        if (!Number.isFinite(targetIndex) || targetIndex < 0) return false;
                        const sampleRows = Array.from(document.querySelectorAll('[data-index]'));
                        const firstRow = sampleRows.find((node) => node.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]'));
                        if (!firstRow) return false;
                        let scroller = firstRow.parentElement;
                        while (scroller && scroller !== document.body) {
                            if (scroller.scrollHeight > scroller.clientHeight + 120) break;
                            scroller = scroller.parentElement;
                        }
                        if (!scroller || scroller === document.body) return false;
                        const firstIndex = Number.parseInt(normalize(firstRow.getAttribute('data-index') || ''), 10);
                        const firstTop = firstRow.getBoundingClientRect().top;
                        const secondRow = sampleRows.find((node) => {
                            const value = Number.parseInt(normalize(node.getAttribute('data-index') || ''), 10);
                            return Number.isFinite(value) && value === firstIndex + 1;
                        });
                        const rowHeight = secondRow
                            ? Math.max(56, Math.abs(secondRow.getBoundingClientRect().top - firstTop))
                            : Math.max(72, firstRow.getBoundingClientRect().height || 72);
                        scroller.scrollTop = Math.max(0, targetIndex * rowHeight - scroller.clientHeight * 0.35);
                        return true;
                    }
                    """,
                    payload,
                )
                await target_page.wait_for_timeout(350)
            except Exception:
                pass

        for _ in range(18):
            click_result = await target_page.evaluate(
                r"""
                (payload) => {
                    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                    const toAbsoluteUserUrl = (value) => {
                        const raw = String(value || '').trim();
                        if (!raw) return '';
                        if (raw.startsWith('http://') || raw.startsWith('https://')) return raw.split('?')[0];
                        if (raw.startsWith('//')) return `https:${raw}`.split('?')[0];
                        if (raw.startsWith('/')) return `https://www.douyin.com${raw}`.split('?')[0];
                        return '';
                    };
                    const conversationKey = normalize(payload?.conversation_key || '');
                    const strangerIndex = normalize(payload?.stranger_index || '');
                    const username = normalize(payload?.username || '');
                    const profileUrl = toAbsoluteUserUrl(payload?.profile_url || '');
                    const preview = normalize(payload?.preview_text || '');
                    const wrappers = Array.from(document.querySelectorAll('[data-index]'))
                        .filter((node) => node.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]'));
                    if (!wrappers.length) return { clicked: false };
                    const readRow = (wrapper) => {
                        const node = wrapper.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]') || wrapper;
                        const title = normalize(node.querySelector('.conversationConversationItemtitle')?.textContent || '');
                        const hint = normalize(node.querySelector('.ConversationItemHinttextBox')?.textContent || '');
                        const fullText = normalize(node.innerText || node.textContent || '');
                        const nodeIndex = normalize(wrapper.getAttribute('data-index') || '');
                        const nodeProfileUrl = Array.from(node.querySelectorAll('a[href*="/user/"]'))
                            .map((anchor) => toAbsoluteUserUrl(anchor.getAttribute('href') || ''))
                            .find((href) => href.includes('/user/')) || '';
                        const syntheticKey = normalize(title);
                        const attrs = [
                            node.getAttribute('data-id'),
                            node.getAttribute('data-conversation-id'),
                            node.getAttribute('data-key'),
                            node.getAttribute('data-im-id'),
                            nodeProfileUrl,
                            syntheticKey,
                            nodeIndex ? `index:${nodeIndex}` : '',
                        ].map(normalize).filter(Boolean);
                        return { wrapper, node, title, hint, fullText, nodeIndex, nodeProfileUrl, attrs };
                    };
                    const rows = wrappers.map(readRow);
                    const scoreNode = (entry) => {
                        const { title, hint, fullText, nodeIndex, nodeProfileUrl, attrs } = entry;
                        let score = 0;
                        if (username && title === username) score += 20;
                        if (username && (title.includes(username) || username.includes(title))) score += 8;
                        if (username && fullText.includes(username)) score += 3;
                        if (conversationKey && attrs.includes(conversationKey)) score += 6;
                        if (profileUrl && nodeProfileUrl === profileUrl) score += 5;
                        if (strangerIndex && nodeIndex === strangerIndex) score += 1;
                        return score;
                    };
                    const target = rows
                        .map((entry) => ({ entry, score: scoreNode(entry) }))
                        .sort((a, b) => b.score - a.score)[0];
                    if (!target || target.score <= 0) return { clicked: false };
                    const node = target.entry.node;
                    node.scrollIntoView({ block: 'center' });
                    const clickable = node.querySelector('.conversationConversationItemrowArea2')
                        || node.querySelector('.conversationConversationItemtitleWrapper')
                        || node.querySelector('.conversationConversationItemtitle')
                        || node;
                    const fire = (element) => {
                        if (!element) return;
                        ['pointerdown','mousedown','mouseup','click'].forEach((eventName) => {
                            element.dispatchEvent(new MouseEvent(eventName, { bubbles: true, cancelable: true, view: window }));
                        });
                    };
                    fire(clickable);
                    if (typeof clickable.click === 'function') clickable.click();
                    return {
                        clicked: true,
                        title: target.entry.title || '',
                        index: target.entry.nodeIndex || '',
                    };
                }
                """,
                payload,
            )
            if click_result and click_result.get("clicked"):
                clicked_title = str(click_result.get("title", "") or "").strip()
                confirmed = await confirm_selected(clicked_title)
                if confirmed:
                    await target_page.wait_for_timeout(600)
                    return True, ""

            scrolled = await target_page.evaluate(
                r"""
                () => {
                    const rows = Array.from(document.querySelectorAll('[data-index]'));
                    const firstRow = rows.find((node) => node.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]'));
                    if (!firstRow) return false;
                    let scroller = firstRow.parentElement;
                    while (scroller && scroller !== document.body) {
                        if (scroller.scrollHeight > scroller.clientHeight + 120) break;
                        scroller = scroller.parentElement;
                    }
                    if (!scroller || scroller === document.body) return false;
                    const before = scroller.scrollTop;
                    scroller.scrollTop += Math.max(scroller.clientHeight * 0.82, 460);
                    return scroller.scrollTop > before;
                }
                """
            )
            if not scrolled:
                break
            await target_page.wait_for_timeout(260)
        search_opened, search_reason = await try_open_via_search()
        if search_opened:
            return True, ""
        try:
            samples = await target_page.evaluate(
                r"""
                () => {
                    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                    return Array.from(document.querySelectorAll('[data-index]'))
                        .map((wrapper) => wrapper.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]') || wrapper)
                        .map((node) => normalize(node.querySelector('.conversationConversationItemtitle')?.textContent || ''))
                        .filter(Boolean)
                        .slice(0, 8);
                }
                """
            )
        except Exception:
            samples = []
        sample_text = " / ".join([str(item).strip() for item in samples if str(item).strip()])
        if search_reason:
            if sample_text:
                return False, f"{search_reason}；可见样本：{sample_text}"
            return False, search_reason
        if sample_text:
            return False, f"未找到目标会话，可见样本：{sample_text}"
        return False, "未找到目标会话"

    async def extract_chat_page_conversation_detail(
        self,
        target_page: Page,
    ) -> Dict:
        return await target_page.evaluate(
            r"""
            () => {
                const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                const toAbsoluteUserUrl = (value) => {
                    const raw = String(value || '').trim();
                    if (!raw) return '';
                    if (raw.startsWith('http://') || raw.startsWith('https://')) return raw.split('?')[0];
                    if (raw.startsWith('//')) return `https:${raw}`.split('?')[0];
                    if (raw.startsWith('/')) return `https://www.douyin.com${raw}`.split('?')[0];
                    return '';
                };
                const panel = document.querySelector('.RightPanel') || document.body;
                const headerName = normalize(
                    document.querySelector('.RightPanelHeadertitle, .RightPanelHeader .semi-typography, .semi-typography')?.textContent || ''
                );
                const headerAvatar = String(
                    document.querySelector('.RightPanelHeader img, .RightPanel img, .chatHeader img')?.src || ''
                ).split('?')[0];
                const readProfileUrl = () => {
                    const nodes = [panel, ...Array.from(panel.querySelectorAll('*'))];
                    for (const node of nodes) {
                        const href = toAbsoluteUserUrl(node?.getAttribute?.('href') || '');
                        if (href.includes('/user/')) return href;
                        const dataset = node?.dataset || {};
                        for (const key of Object.keys(dataset)) {
                            const value = toAbsoluteUserUrl(dataset[key]);
                            if (value.includes('/user/')) return value;
                        }
                    }
                    return '';
                };
                const panelRect = panel.getBoundingClientRect();
                const centerX = panelRect.left + panelRect.width / 2;
                const messages = [];
                const bubbles = Array.from(document.querySelectorAll('[data-e2e="msg-item-content"]'));
                for (const bubble of bubbles) {
                    const text = normalize(bubble.textContent || '');
                    if (!text) continue;
                    const rect = bubble.getBoundingClientRect();
                    const bubbleCenterX = rect.left + rect.width / 2;
                    const direction = bubbleCenterX < centerX ? 'incoming' : 'outgoing';
                    let timeText = '';
                    const container = bubble.closest('[class*="message"], [class*="msg"], li, div');
                    if (container) {
                        const timeCandidate = Array.from(container.querySelectorAll('time, [class*="time"], [data-e2e*="time"], .semi-typography'))
                            .map((node) => normalize(node.textContent || ''))
                            .find((value) => value && value !== text && /(\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}|昨天|前天|周.|刚刚)/.test(value));
                        timeText = timeCandidate || '';
                    }
                    messages.push({
                        direction,
                        text,
                        time_text: timeText,
                        label: direction === 'incoming' ? (headerName || '对方') : '我',
                    });
                }
                const incoming = messages.filter((item) => item.direction === 'incoming');
                const outgoing = messages.filter((item) => item.direction === 'outgoing');
                return {
                    username: headerName,
                    avatar_url: headerAvatar,
                    profile_url: readProfileUrl(),
                    incoming_message: incoming.length ? incoming[incoming.length - 1].text : '',
                    reply_message: outgoing.length ? outgoing[outgoing.length - 1].text : '',
                    messages,
                };
            }
            """
        )

    async def _ensure_comment_panel_ready(
        self,
        page: Page,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> None:
        try:
            await self._wait_for_visible_comment_editor(page, timeout=2500)
            return
        except Exception:
            pass

        clicked = await page.evaluate(
            """
            () => {
                const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const isVisible = (node) => {
                    if (!node || typeof node.getBoundingClientRect !== 'function') return false;
                    const style = window.getComputedStyle(node);
                    if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                        return false;
                    }
                    const rect = node.getBoundingClientRect();
                    return rect.width > 0 && rect.height > 0;
                };
                const nodes = Array.from(document.querySelectorAll('div, span, button, a')).filter(isVisible);
                const matchers = [
                    (text) => /^评论\\(\\d+\\)$/.test(text),
                    (text) => text === '全部评论',
                    (text) => text === '评论',
                ];
                for (const matcher of matchers) {
                    const node = nodes.find((item) => matcher(normalize(item.textContent || item.innerText || '')));
                    if (!node) continue;
                    const target = node.closest('button, a, [role="button"]') || node;
                    target.click();
                    return normalize(node.textContent || node.innerText || '');
                }
                return '';
            }
            """
        )
        clicked = str(clicked or "").strip()
        if clicked:
            self._emit(logger, f"[抖音视频评论] 已点击评论入口：{clicked}")
            await page.wait_for_timeout(1200)

        page_kind = ""
        try:
            page_kind = await page.evaluate(
                """
                () => {
                    const href = String(location.href || '');
                    if (href.includes('/note/')) return 'note';
                    if (href.includes('/video/')) return 'video';
                    return '';
                }
                """
            )
        except Exception:
            page_kind = ""

        login_required = await page.evaluate(
            """
            () => {
                const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const bodyText = normalize(document.body?.innerText || '');
                if (
                    bodyText.includes('请先登录后发表评论')
                    || bodyText.includes('登录后即可参与互动讨论')
                    || bodyText.includes('立即登录')
                    || bodyText.includes('登录后免费畅享高清视频')
                ) {
                    return true;
                }
                const selectors = [
                    '.comment-input-un-login-container',
                    '.video-comment-cover__btn',
                    '.video-comment-cover__desc',
                ];
                return selectors.some((selector) => {
                    const node = document.querySelector(selector);
                    if (!node || typeof node.getBoundingClientRect !== 'function') return false;
                    const style = window.getComputedStyle(node);
                    const rect = node.getBoundingClientRect();
                    return !!style && style.display !== 'none' && style.visibility !== 'hidden' && style.opacity !== '0'
                        && rect.width > 0 && rect.height > 0;
                });
            }
            """
        )
        if login_required:
            raise RuntimeError("当前抖音账号未处于可评论状态，请先登录后再执行评论")

        first_timeout = 18000 if page_kind == "note" else 10000
        try:
            await self._wait_for_visible_comment_editor(page, timeout=first_timeout)
            return
        except Exception:
            state = await self._read_comment_surface_state(page)
            if (
                (state or {}).get("hasVisibleContainer")
                or (state or {}).get("hasCommentList")
                or str((state or {}).get("placeholderText", "") or "").strip()
            ):
                return

        activated = await self._activate_comment_input(page, logger=logger)
        if activated:
            await page.wait_for_timeout(1200)

        try:
            await self._ensure_comment_input_ready(page, logger=logger)
            return
        except Exception:
            pass

        state = await self._read_comment_surface_state(page)
        reason = await page.evaluate(
            """
            () => {
                const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                const bodyText = normalize(document.body?.innerText || '');
                const hints = [
                    '请先登录后发表评论',
                    '登录后即可参与互动讨论',
                    '评论功能已关闭',
                    '暂不支持评论',
                    '作者已关闭评论',
                    '仅互关朋友可评论',
                    '仅允许互关朋友评论',
                    '暂无评论',
                    '立即登录',
                ];
                const matched = hints.find((item) => bodyText.includes(item));
                return matched || '';
            }
            """
        )
        reason = str(reason or "").strip()
        if reason:
            raise RuntimeError(f"已打开评论区，但当前页面不可直接评论：{reason}")
        placeholder_text = str((state or {}).get("placeholderText", "") or "").strip()
        details = []
        if (state or {}).get("hasCommentList"):
            details.append("评论列表已显示")
        if (state or {}).get("hasVisibleContainer"):
            details.append("评论输入容器已显示")
        if placeholder_text:
            details.append(f"可见提示：{placeholder_text[:40]}")
        detail_text = "；".join(details) if details else "未识别到评论输入区域"
        raise RuntimeError(f"已打开评论区，但未检测到可用评论输入框：{detail_text}")

    async def _like_current_work_if_needed(
        self,
        page: Page,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> bool:
        try:
            like_locator = page.locator('[data-e2e="video-player-digg"]').first
            await like_locator.wait_for(state="attached", timeout=3000)

            surface_before = await self._read_like_surface_state(page)
            label = str((surface_before or {}).get("label", "") or "").strip() or "点赞"
            state_before = str((surface_before or {}).get("state", "") or "").strip()
            color_before = str((surface_before or {}).get("iconColor", "") or "").strip().lower()
            if state_before and "no-digged" not in state_before:
                self._emit(logger, f"[抖音视频评论] 当前作品已点赞，跳过重复点赞：{label}", "info")
                return True
            if "#fe2c55" in color_before or "254, 44, 85" in color_before:
                self._emit(logger, f"[抖音视频评论] 当前作品已是红心状态，跳过重复点赞：{label}", "info")
                return True

            page_kind = ""
            try:
                page_kind = await page.evaluate(
                    """
                    () => {
                        const href = String(location.href || '');
                        if (href.includes('/video/')) return 'video';
                        if (href.includes('/note/')) return 'note';
                        return '';
                    }
                    """
                )
            except Exception:
                page_kind = ""

            icon_locator = page.locator('[data-e2e="video-player-digg"] span[role="img"]:visible').first
            wrapper_locator = page.locator(
                'div[tabindex="0"]:has([data-e2e="video-player-digg"])'
            ).first

            if page_kind == "video":
                try:
                    player_locator = page.locator(
                        '[data-e2e="player-container"], [data-e2e="video-detail"], .basePlayerContainer, video'
                    ).first
                    await player_locator.wait_for(state="attached", timeout=2000)
                    try:
                        await player_locator.click(timeout=3000, force=True)
                    except Exception:
                        pass
                    try:
                        await player_locator.focus()
                    except Exception:
                        pass
                    await page.keyboard.press("z")
                    await page.wait_for_timeout(900)
                    surface_after = await self._read_like_surface_state(page)
                    state_after = str((surface_after or {}).get("state", "") or "").strip()
                    color_after = str((surface_after or {}).get("iconColor", "") or "").strip().lower()
                    if (state_after and "no-digged" not in state_after) or "#fe2c55" in color_after or "254, 44, 85" in color_after:
                        self._emit(
                            logger,
                            f"[抖音视频评论] 已先执行点赞：{label}，点击目标=键盘快捷键 Z，状态={state_after or color_after or 'unknown'}",
                            "success",
                        )
                        return True
                except Exception:
                    pass

            if bool((surface_before or {}).get("visible")):
                try:
                    await like_locator.scroll_into_view_if_needed()
                    await wrapper_locator.wait_for(state="visible", timeout=1200)
                    await wrapper_locator.hover(timeout=3000)
                    await wrapper_locator.click(timeout=5000, force=True)
                except Exception:
                    pass

            click_targets = await self._read_like_click_targets(page)
            attempts = []
            try:
                await wrapper_locator.wait_for(state="visible", timeout=1200)
                attempts.append(("点赞外层容器", wrapper_locator, None, False))
            except Exception:
                pass
            try:
                await icon_locator.wait_for(state="visible", timeout=1200)
                attempts.append(("心形图标", icon_locator, None, False))
            except Exception:
                pass
            attempts.append(("点赞外层容器键盘触发", wrapper_locator, None, True))

            clicked_label = ""
            for attempt_label, locator, position, use_keyboard in attempts:
                try:
                    if use_keyboard:
                        await locator.focus()
                        await page.keyboard.press("Enter")
                    elif position:
                        box = await locator.bounding_box()
                        if box:
                            click_x = max(12, min(box["width"] - 12, box["width"] / 2))
                            click_y = max(10, min(box["height"] - 10, box["height"] * 0.28))
                            await locator.click(
                                position={"x": click_x, "y": click_y},
                                timeout=5000,
                                force=True,
                            )
                        else:
                            await locator.click(timeout=5000, force=True)
                    else:
                        await locator.click(timeout=5000, force=True)
                    clicked_label = attempt_label
                    await page.wait_for_timeout(900)
                    surface_after = await self._read_like_surface_state(page)
                    state_after = str((surface_after or {}).get("state", "") or "").strip()
                    color_after = str((surface_after or {}).get("iconColor", "") or "").strip().lower()
                    if (state_after and "no-digged" not in state_after) or "#fe2c55" in color_after or "254, 44, 85" in color_after:
                        self._emit(
                            logger,
                            f"[抖音视频评论] 已先执行点赞：{label}，点击目标={clicked_label}，状态={state_after or color_after or 'unknown'}",
                            "success",
                        )
                        return True
                except Exception:
                    continue

            for target in click_targets:
                try:
                    click_x = float(target.get("x", 0) or 0)
                    click_y = float(target.get("y", 0) or 0)
                    if click_x <= 0 or click_y <= 0:
                        continue
                    await page.mouse.move(click_x, click_y)
                    await page.wait_for_timeout(120)
                    await page.mouse.down()
                    await page.wait_for_timeout(80)
                    await page.mouse.up()
                    clicked_label = str(target.get("label", "") or "坐标点击")
                    await page.wait_for_timeout(900)
                    surface_after = await self._read_like_surface_state(page)
                    state_after = str((surface_after or {}).get("state", "") or "").strip()
                    color_after = str((surface_after or {}).get("iconColor", "") or "").strip().lower()
                    if (state_after and "no-digged" not in state_after) or "#fe2c55" in color_after or "254, 44, 85" in color_after:
                        self._emit(
                            logger,
                            f"[抖音视频评论] 已先执行点赞：{label}，点击目标={clicked_label}，状态={state_after or color_after or 'unknown'}",
                            "success",
                        )
                        return True
                except Exception:
                    continue

            try:
                changed_via_dom = await page.evaluate(
                    """
                    () => {
                        const likeNode = document.querySelector('[data-e2e="video-player-digg"]');
                        const wrapper = document.querySelector('div[tabindex="0"]:has([data-e2e="video-player-digg"])');
                        const target = wrapper || likeNode;
                        if (!target) return false;
                        target.dispatchEvent(new MouseEvent('mousemove', { bubbles: true, cancelable: true, view: window }));
                        target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, view: window }));
                        target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, view: window }));
                        target.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, view: window }));
                        return true;
                    }
                    """
                )
                if changed_via_dom:
                    clicked_label = "DOM click"
                    await page.wait_for_timeout(900)
                    surface_after = await self._read_like_surface_state(page)
                    state_after = str((surface_after or {}).get("state", "") or "").strip()
                    color_after = str((surface_after or {}).get("iconColor", "") or "").strip().lower()
                    if (state_after and "no-digged" not in state_after) or "#fe2c55" in color_after or "254, 44, 85" in color_after:
                        self._emit(
                            logger,
                            f"[抖音视频评论] 已先执行点赞：{label}，点击目标={clicked_label}，状态={state_after or color_after or 'unknown'}",
                            "success",
                        )
                        return True
            except Exception:
                pass

            self._emit(
                logger,
                f"[抖音视频评论] 已尝试点击点赞按钮，但未确认状态变化：{label}，点击目标={clicked_label or '未成功命中'}",
                "warning",
            )
            return False
        except Exception:
            self._emit(logger, "[抖音视频评论] 未识别到可点击的点赞按钮，继续执行评论", "warning")
            return False

    async def check_login_state(
        self,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> bool:
        page = await self._new_page(logger=logger)
        try:
            await page.goto("https://www.douyin.com/user/self", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            cookies = []
            try:
                cookies = await page.context.cookies(["https://www.douyin.com"])
            except Exception:
                cookies = []

            login_component_visible = await self._has_login_intercept(page)

            has_session_cookie = any(
                str(cookie.get("name", "")) in {"sessionid", "sessionid_ss"}
                and str(cookie.get("value", "")).strip()
                for cookie in cookies
                if isinstance(cookie, dict)
            )

            state = await page.evaluate(
                """
                () => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, '');
                    const bodyText = normalize(document.body?.innerText || '');
                    const loginTexts = ['登录', '立即登录', '扫码登录', '去登录', '手机号登录', '验证码登录'];
                    const loginPrompt = Array.from(document.querySelectorAll('button, a, div, span'))
                        .some((el) => {
                            const text = normalize(el.innerText || el.textContent || '');
                            return text && loginTexts.includes(text);
                        });
                    const qrLoginVisible = Array.from(document.querySelectorAll('img, canvas, div'))
                        .some((el) => {
                            const className = normalize(el.className || '').toLowerCase();
                            const alt = normalize(el.getAttribute?.('alt') || '').toLowerCase();
                            const text = normalize(el.innerText || '');
                            return className.includes('qrcode')
                                || className.includes('qr-code')
                                || alt.includes('qr')
                                || text.includes('扫码登录')
                                || text.includes('二维码');
                        });
                    const profileHints = ['退出登录', '账号与安全', '我的作品', '我的喜欢', '获赞', '粉丝', '关注']
                        .some((text) => bodyText.includes(text));
                    const currentPath = location.pathname || '';
                    const userPath = /^\\/user\\/(?!self(?:\\/|$))/.test(currentPath);
                    const profileLinkCount = document.querySelectorAll('a[href*="/user/"]').length;
                    return {
                        loginPrompt,
                        qrLoginVisible,
                        profileHints,
                        userPath,
                        profileLinkCount,
                        currentPath,
                    };
                }
                """
            )
            login_prompt = bool(state.get("loginPrompt"))
            qr_login_visible = bool(state.get("qrLoginVisible"))
            profile_hints = bool(state.get("profileHints"))
            user_path = bool(state.get("userPath"))
            profile_link_count = int(state.get("profileLinkCount", 0) or 0)
            current_path = str(state.get("currentPath", "") or "")

            result = False
            if not login_component_visible and not login_prompt and not qr_login_visible:
                result = bool(profile_hints or user_path or (has_session_cookie and profile_link_count > 0))

            self._emit(
                logger,
                f"[抖音登录检测] 账号{self.account_id or '-'} 状态={'online' if result else 'offline'} "
                f"path={current_path or '-'} cookie={'yes' if has_session_cookie else 'no'} "
                f"login_component={'yes' if login_component_visible else 'no'}",
                "success" if result else "warning",
            )
            return bool(result)
        finally:
            await page.close()

    async def _extract_profile_summary_from_page(
        self,
        page: Page,
        expected_username: str = "",
        fallback_url: str = "",
    ) -> Dict:
        try:
            await page.wait_for_load_state("domcontentloaded", timeout=15000)
        except Exception:
            pass
        await page.wait_for_timeout(2500)

        body_text = await page.locator("body").inner_text()
        body_text = re.sub(r"\s+", " ", body_text or "").strip()

        current_url = page.url.split("?")[0]
        if "/user/" not in current_url:
            discovered_profile_url = await page.evaluate(
                """
                () => {
                    const anchor = Array.from(document.querySelectorAll('a[href*="/user/"]'))
                        .find((node) => {
                            const href = String(node.href || '');
                            return href.includes('/user/');
                        });
                    return anchor ? String(anchor.href || '').split('?')[0] : '';
                }
                """
            )
            if discovered_profile_url:
                current_url = discovered_profile_url

        sec_user_id = extract_sec_user_id(current_url or fallback_url)
        try:
            ssr_sec_user_id = await page.evaluate(
                """
                () => {
                    const fromSSR = String(
                        window.SSR_RENDER_DATA?.app?.user?.info?.secUid
                        || window.SSR_RENDER_DATA?.app?.user?.info?.sec_uid
                        || ''
                    ).trim();
                    if (fromSSR) return fromSSR;
                    const candidate = Array.from(document.querySelectorAll('a[href*="/user/"]'))
                        .map((node) => String(node.href || '').split('?')[0])
                        .find((href) => href.includes('/user/') && !href.endsWith('/user/self'));
                    return candidate ? String(candidate.split('/user/')[1] || '').trim() : '';
                }
                """
            )
        except Exception:
            ssr_sec_user_id = ""
        if ssr_sec_user_id:
            sec_user_id = str(ssr_sec_user_id or "").strip()

        username = ""
        for candidate in [
            await page.title(),
            expected_username,
        ]:
            text = str(candidate or "").strip()
            if not text:
                continue
            username = re.sub(r"\s*-\s*抖音.*$", "", text).strip()
            if username:
                break

        douyin_id = ""
        for pattern in [
            r"抖音号[：:\s]*([A-Za-z0-9._-]+)",
            r"抖音ID[：:\s]*([A-Za-z0-9._-]+)",
        ]:
            match = re.search(pattern, body_text, flags=re.I)
            if match:
                douyin_id = str(match.group(1) or "").strip()
                if douyin_id:
                    break

        region = ""
        for pattern in [
            r"IP属地[：:\s]*([^\s，。；、/]{2,20})",
            r"(?:地区|所在地)[：:\s]*([^\s，。；、/]{2,20})",
        ]:
            match = re.search(pattern, body_text)
            if match:
                region = str(match.group(1) or "").strip()
                if region:
                    break

        avatar_url = ""
        try:
            avatar_url = await page.evaluate(
                """
                () => {
                    const candidates = Array.from(document.querySelectorAll('img')).map((img) => {
                        const src = String(img.currentSrc || img.src || '').trim();
                        const alt = String(img.alt || '').trim();
                        const className = String(img.className || '').toLowerCase();
                        const width = Number(img.naturalWidth || img.width || 0);
                        const height = Number(img.naturalHeight || img.height || 0);
                        return { src, alt, className, width, height };
                    });
                    const picked = candidates.find((item) => {
                        if (!item.src) return false;
                        if (item.width < 64 || item.height < 64) return false;
                        return item.className.includes('avatar')
                            || item.alt.includes('头像')
                            || item.alt.includes('profile')
                            || item.alt.includes('用户');
                    }) || candidates.find((item) => item.src && item.width >= 64 && item.height >= 64);
                    return picked ? picked.src : '';
                }
                """
            )
        except Exception:
            avatar_url = ""

        profile_metrics: Dict[str, str] = {}
        try:
            profile_metrics = await page.evaluate(
                """
                () => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const getText = (selector) => {
                        const node = document.querySelector(selector);
                        return normalize(node?.textContent || '');
                    };

                    const readMetric = (hook, fallbackIndex) => {
                        const block = document.querySelector(`[data-e2e="${hook}"]`);
                        if (block) {
                            const parts = Array.from(block.querySelectorAll('div, span'))
                                .map((node) => normalize(node.textContent || ''))
                                .filter(Boolean);
                            if (parts.length >= 2) {
                                return { label: parts[0], value: parts[1] };
                            }
                        }
                        const blocks = Array.from(document.querySelectorAll('.cuA7Ana_ .Q1A_pjwq'));
                        const fallback = blocks[fallbackIndex];
                        if (!fallback) return { label: '', value: '' };
                        const parts = Array.from(fallback.querySelectorAll('div, span'))
                            .map((node) => normalize(node.textContent || ''))
                            .filter(Boolean);
                        return {
                            label: parts[0] || '',
                            value: parts[1] || '',
                        };
                    };

                    const metaText = normalize(getText('[data-e2e="user-info"] .eTvkFY8I') || '');
                    const signatureText = normalize(getText('[data-e2e="user-info"] .lFECd241') || '');

                    const followMetric = readMetric('user-info-follow', 0);
                    const fansMetric = readMetric('user-info-fans', 1);
                    const likedMetric = readMetric('user-info-like', 2);

                    return {
                        username: getText('[data-e2e="user-info"] h1') || getText('[data-e2e="user-info"] .GMEdHsXq'),
                        follow_count_text: followMetric.value || '',
                        fans_count_text: fansMetric.value || '',
                        liked_count_text: likedMetric.value || '',
                        meta_text: metaText,
                        signature: signatureText.replace(/更多$/, '').trim(),
                    };
                }
                """
            )
        except Exception:
            profile_metrics = {}

        if not username:
            username = str(profile_metrics.get("username", "") or "").strip()

        follow_count_text = str(profile_metrics.get("follow_count_text", "") or "").strip()
        fans_count_text = str(profile_metrics.get("fans_count_text", "") or "").strip()
        liked_count_text = str(profile_metrics.get("liked_count_text", "") or "").strip()
        meta_text = str(profile_metrics.get("meta_text", "") or "").strip()
        bio = str(profile_metrics.get("signature", "") or "").strip()

        city = ""
        if meta_text:
            meta_parts = [part.strip() for part in re.split(r"[·•・]", meta_text) if part.strip()]
            for part in meta_parts:
                if not city and re.search(r"[省市区县州盟旗镇乡村]|\b[A-Z][a-z]+\b", part):
                    if not part.startswith("抖音号") and not part.startswith("IP属地"):
                        city = part
                if not region and part.startswith("IP属地"):
                    region = part.split("：", 1)[-1].split(":", 1)[-1].strip()

        gender = ""
        for token in ["男", "女"]:
            if token in meta_text:
                gender = token
                break

        return {
            "username": username or expected_username,
            "douyin_id": douyin_id,
            "region": region,
            "city": city,
            "gender": gender,
            "follow_count_text": follow_count_text,
            "fans_count_text": fans_count_text,
            "liked_count_text": liked_count_text,
            "bio": bio,
            "meta_text": meta_text,
            "profile_url": current_url or fallback_url,
            "sec_user_id": sec_user_id,
            "avatar_url": str(avatar_url or "").strip(),
        }

    async def scrape_profile_summary(
        self,
        profile_url: str,
        expected_username: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        profile_url = str(profile_url or "").strip()
        if not profile_url:
            raise ValueError("缺少用户主页地址")

        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, f"[抖音主页] 打开用户主页：{expected_username or profile_url}")
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            return await self._extract_profile_summary_from_page(
                page,
                expected_username=expected_username,
                fallback_url=profile_url,
            )
        finally:
            await page.close()

    async def scrape_profile_videos(
        self,
        profile_url: str,
        max_videos: int = 10,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        profile_url = str(profile_url or "").strip()
        max_videos = max(1, min(int(max_videos or 10), 20))
        if not profile_url:
            raise ValueError("缺少同行主页地址")

        page = await self._new_page(logger=logger)
        try:
            aweme_post_future = asyncio.get_running_loop().create_future()

            async def handle_aweme_post_response(response):
                if aweme_post_future.done():
                    return
                if "/aweme/v1/web/aweme/post/" not in str(response.url or ""):
                    return
                if int(response.status or 0) != 200:
                    return
                try:
                    aweme_post_future.set_result(await response.json())
                except Exception as exc:
                    aweme_post_future.set_exception(exc)

            page.on("response", lambda response: asyncio.create_task(handle_aweme_post_response(response)))

            self._emit(logger, f"[抖音同行监控] 打开同行主页：{profile_url}")
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3500)
            await self._raise_if_login_intercept(page)

            summary = await self._extract_profile_summary_from_page(
                page,
                fallback_url=profile_url,
            )

            stable_rounds = 0
            last_count = 0
            for round_index in range(10):
                try:
                    raw_count = await page.locator('a[href*="/video/"]').count()
                except Exception:
                    raw_count = 0
                if raw_count >= max_videos:
                    break
                if raw_count == last_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                    last_count = raw_count
                if stable_rounds >= 3:
                    break
                await page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.9, 900));")
                await page.wait_for_timeout(1000)
                self._emit(logger, f"[抖音同行监控] 主页滚动第 {round_index + 1} 轮，当前识别 {raw_count} 条视频")

            raw_rows = await page.evaluate(
                """
                (limit) => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const isDuration = (value) => /^\\d{1,2}:\\d{2}(?::\\d{2})?$/.test(normalize(value));
                    const isCountText = (value) => /^(\\d+(?:\\.\\d+)?[万千kKwW+]*|\\d+)$/.test(
                        normalize(value).replace(/,/g, '').replace(/\\+/g, '')
                    );
                    const isDateText = (value) => /^(?:\\d{4}[./-]\\d{1,2}[./-]\\d{1,2}|\\d{1,2}[./-]\\d{1,2})(?:\\s+\\d{1,2}:\\d{2})?$/.test(
                        normalize(value)
                    );
                    const splitLeadingCount = (value) => {
                        const text = normalize(value);
                        const match = text.match(/^(\\d+(?:\\.\\d+)?[万千kKwW+]*)(?:\\s+)(.+)$/);
                        if (!match) {
                            return { countText: '', titleText: text };
                        }
                        const countText = normalize(match[1]);
                        const titleText = normalize(match[2]);
                        if (!isCountText(countText) || !titleText) {
                            return { countText: '', titleText: text };
                        }
                        return { countText, titleText };
                    };
                    const collectCountTexts = (root) => {
                        const selectors = ['.BgCg_ebQ', '[class*="BgCg_"]', '[class*="count"]', 'span', 'strong', 'div'];
                        const nodes = Array.from(root.querySelectorAll(selectors.join(',')));
                        const values = [];
                        const seen = new Set();
                        for (const node of nodes) {
                            const text = normalize(node.textContent || '');
                            if (!text || text.length > 12 || isDuration(text) || !isCountText(text) || seen.has(text)) continue;
                            const rect = typeof node.getBoundingClientRect === 'function' ? node.getBoundingClientRect() : null;
                            if (rect && rect.width === 0 && rect.height === 0) continue;
                            seen.add(text);
                            values.push(text);
                            if (values.length >= 4) break;
                        }
                        return values;
                    };
                    const rows = [];
                    const seen = new Set();
                    const anchors = Array.from(document.querySelectorAll('a[href*="/video/"]'));
                    for (const anchor of anchors) {
                        const href = String(anchor.href || '').split('?')[0];
                        if (!href || seen.has(href)) continue;
                        const card = anchor.closest('li, article, [data-e2e*="user-post"], .xgplayer, .swiper-slide, div') || anchor;
                        const img = anchor.querySelector('img') || card.querySelector('img');
                        const titleNode = anchor.querySelector('[title]') || card.querySelector('[title]');
                        const labelText = normalize(anchor.getAttribute('aria-label') || titleNode?.getAttribute?.('title') || '');
                        const text = normalize(anchor.innerText || card.innerText || '');
                        const lines = text.split(/\\n+/).map((line) => normalize(line)).filter(Boolean);
                        const rawTitle = labelText
                            || lines.find((line) => line && !isDuration(line))
                            || `视频 ${rows.length + 1}`;
                        const { countText: inlineCountText, titleText } = splitLeadingCount(rawTitle);
                        const countTexts = collectCountTexts(card);
                        if (inlineCountText && !countTexts.includes(inlineCountText)) {
                            countTexts.unshift(inlineCountText);
                        }
                        const fallbackCountTexts = lines.filter((line) => isCountText(line) && !isDuration(line));
                        for (const item of fallbackCountTexts) {
                            if (!countTexts.includes(item)) countTexts.push(item);
                        }
                        const likeText = countTexts[0] || '';
                        const commentText = countTexts[1] || '';
                        const publishText = lines.find((line) => isDateText(line)) || '';
                        rows.push({
                            url: href,
                            title: titleText || rawTitle,
                            cover_image: normalize(img?.currentSrc || img?.src || ''),
                            likes_text: likeText,
                            comments_text: commentText,
                            publish_text: publishText,
                            raw_text: text,
                            video_order: rows.length + 1,
                        });
                        seen.add(href);
                        if (rows.length >= limit) break;
                    }
                    return rows;
                }
                """,
                max_videos,
            )

            aweme_post_payload: Dict = {}
            if aweme_post_future.done():
                try:
                    aweme_post_payload = aweme_post_future.result()
                except Exception:
                    aweme_post_payload = {}
            else:
                try:
                    aweme_post_payload = await asyncio.wait_for(asyncio.shield(aweme_post_future), timeout=2.5)
                except Exception:
                    aweme_post_payload = {}

            def pick_cover_url(payload: Dict) -> str:
                if not isinstance(payload, dict):
                    return ""
                candidates = [
                    payload.get("cover"),
                    payload.get("origin_cover"),
                    payload.get("dynamic_cover"),
                ]
                for candidate in candidates:
                    if not isinstance(candidate, dict):
                        continue
                    url_list = candidate.get("url_list") or []
                    if isinstance(url_list, list):
                        for url in url_list:
                            text = str(url or "").strip()
                            if text:
                                return text
                return ""

            api_video_map: Dict[str, Dict] = {}
            api_aweme_list = aweme_post_payload.get("aweme_list", []) if isinstance(aweme_post_payload, dict) else []
            for order_index, aweme in enumerate(api_aweme_list or [], start=1):
                if not isinstance(aweme, dict):
                    continue
                aweme_id = str(aweme.get("aweme_id", "") or "").strip()
                if not aweme_id:
                    continue
                author_info = aweme.get("author") if isinstance(aweme.get("author"), dict) else {}
                statistics = aweme.get("statistics") if isinstance(aweme.get("statistics"), dict) else {}
                video_info = aweme.get("video") if isinstance(aweme.get("video"), dict) else {}
                digg_count = int(statistics.get("digg_count", 0) or 0)
                comment_count = int(statistics.get("comment_count", 0) or 0)
                play_count = int(statistics.get("play_count", 0) or 0)
                publish_ts = int(aweme.get("create_time", 0) or 0)
                api_video_map[aweme_id] = {
                    "title": str(aweme.get("desc", "") or "").strip(),
                    "author": str(author_info.get("nickname", "") or "").strip(),
                    "cover_image": pick_cover_url(video_info),
                    "likes": digg_count,
                    "likes_text": format_compact_count(digg_count) if digg_count > 0 else "",
                    "comments": comment_count,
                    "comments_text": format_compact_count(comment_count) if comment_count > 0 else "",
                    "play_count": play_count,
                    "play_text": format_compact_count(play_count) if play_count > 0 else "",
                    "publish_time": format_unix_timestamp_text(publish_ts),
                    "publish_time_text": format_unix_timestamp_text(publish_ts),
                    "publish_timestamp": publish_ts,
                    "video_order": order_index,
                }

            raw_video_map: Dict[str, Dict] = {}
            for item in raw_rows or []:
                if not isinstance(item, dict):
                    continue
                url = str(item.get("url", "") or "").strip()
                aweme_id = extract_aweme_id(url)
                if not url or not aweme_id or aweme_id in raw_video_map:
                    continue
                raw_video_map[aweme_id] = item

            videos: List[Dict] = []
            source_aweme_ids = list(api_video_map.keys()) if api_video_map else list(raw_video_map.keys())
            for index, aweme_id in enumerate(source_aweme_ids, start=1):
                api_item = api_video_map.get(aweme_id, {})
                raw_item = raw_video_map.get(aweme_id, {})
                url = str(raw_item.get("url", "") or "").strip()
                if not url:
                    url = f"https://www.douyin.com/video/{aweme_id}"
                if not url or not aweme_id:
                    continue
                videos.append(
                    {
                        "platform": "douyin",
                        "aweme_id": aweme_id,
                        "url": url,
                        "title": str(api_item.get("title", "") or raw_item.get("title", "") or "").strip() or f"视频 {index}",
                        "author": str(api_item.get("author", "") or summary.get("username", "") or "").strip(),
                        "cover_image": str(api_item.get("cover_image", "") or raw_item.get("cover_image", "") or "").strip(),
                        "likes": int(api_item.get("likes", 0) or parse_count_text(raw_item.get("likes_text", ""))),
                        "likes_text": str(api_item.get("likes_text", "") or raw_item.get("likes_text", "") or "").strip(),
                        "comments": int(api_item.get("comments", 0) or parse_count_text(raw_item.get("comments_text", ""))),
                        "comments_text": str(api_item.get("comments_text", "") or raw_item.get("comments_text", "") or "").strip(),
                        "play_count": int(api_item.get("play_count", 0) or parse_count_text(raw_item.get("likes_text", ""))),
                        "play_text": str(api_item.get("play_text", "") or raw_item.get("likes_text", "") or "").strip(),
                        "publish_time": str(api_item.get("publish_time", "") or raw_item.get("publish_text", "") or "").strip(),
                        "publish_time_text": str(api_item.get("publish_time_text", "") or raw_item.get("publish_text", "") or "").strip(),
                        "video_order": int(api_item.get("video_order", raw_item.get("video_order", index)) or index),
                        "raw_text": str(raw_item.get("raw_text", "") or "").strip(),
                    }
                )

            return {
                "profile": summary,
                "videos": videos,
            }
        finally:
            await page.close()

    async def scrape_self_videos(
        self,
        max_videos: int = 12,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        return await self.scrape_profile_videos(
            "https://www.douyin.com/user/self?from_tab_name=main",
            max_videos=max_videos,
            logger=logger,
        )

    async def list_chat_groups(
        self,
        group_keyword: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> List[Dict]:
        keyword = str(group_keyword or "").strip()
        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, "[抖音群成员] 打开消息页识别群聊：https://www.douyin.com/chat")
            await page.goto("https://www.douyin.com/chat", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)
            await self._raise_if_login_intercept(page)

            conversations = await page.evaluate(
                r"""
                (keyword) => {
                    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                    const rows = [];
                    for (const node of document.querySelectorAll('[data-e2e="conversation-item"].conversationConversationItemwrapper')) {
                        const text = normalize(node.innerText || node.textContent || '');
                        if (!text) continue;
                        const name = normalize(node.querySelector('.conversationConversationItemtitle')?.textContent || '');
                        if (!name) continue;
                        const preview = normalize(node.querySelector('.ConversationItemHinttextBox')?.textContent || '');
                        const hasGroupIcon = Boolean(node.querySelector('img.commonConversationIconnoDrag'));
                        const hasUserAvatar = Boolean(node.querySelector('.commonIMAvataravatarContainer'));
                        const hasMuteIcon = Boolean(node.querySelector('.ConversationItemTagNextToTitlemuteIcon'));
                        const senderPrefixInPreview = /^[^：:\n]{1,24}[：:]/.test(preview);
                        const joinGroupHint = /加入了群聊|通过.+加入了群聊|新成员可查看历史消息/.test(preview || text);
                        const nameLooksLikeGroup = /群|群聊|交流群|社群|分群/.test(name);
                        const isGroup = Boolean(
                            nameLooksLikeGroup
                            || joinGroupHint
                            || senderPrefixInPreview
                            || (hasGroupIcon && !hasUserAvatar)
                            || (hasMuteIcon && hasGroupIcon)
                        );
                        if (!isGroup) continue;
                        if (keyword && !name.includes(keyword) && !text.includes(keyword)) continue;
                        rows.push({
                            name,
                            preview,
                            has_group_icon: hasGroupIcon,
                            has_mute_icon: hasMuteIcon,
                        });
                    }
                    const unique = [];
                    const seen = new Set();
                    for (const row of rows) {
                        const key = `${row.name}|${row.preview}`;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        unique.push(row);
                    }
                    return unique;
                }
                """,
                keyword,
            )
            return [row for row in (conversations or []) if isinstance(row, dict) and str(row.get("name", "")).strip()]
        finally:
            await page.close()

    async def collect_stranger_private_messages(
        self,
        max_conversations: int = 100,
        should_stop: Optional[Callable[[], bool]] = None,
        logger: Optional[Callable[[str, str], None]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[Dict]:
        limit = max(1, min(int(max_conversations or 100), 100))
        page = await self._new_page(logger=logger)
        try:
            async def open_or_refresh_chat_page(is_retry: bool = False):
                if is_retry:
                    self._emit(logger, "[抖音私信引流] 未找到入口，刷新消息页后再重试一次", "warning")
                    await page.reload(wait_until="domcontentloaded", timeout=60000)
                else:
                    self._emit(logger, "[抖音私信引流] 打开消息页：https://www.douyin.com/chat")
                    await page.goto("https://www.douyin.com/chat", wait_until="domcontentloaded", timeout=60000)
                try:
                    await page.wait_for_load_state("networkidle", timeout=10000)
                    self._emit(
                        logger,
                        "[抖音私信引流] 消息页首屏网络请求已基本稳定"
                        if not is_retry
                        else "[抖音私信引流] 刷新后的消息页网络请求已基本稳定",
                    )
                except Exception:
                    self._emit(
                        logger,
                        "[抖音私信引流] 等待消息页网络稳定超时，继续检查会话列表"
                        if not is_retry
                        else "[抖音私信引流] 等待刷新后的消息页网络稳定超时，继续检查会话列表",
                        "warning",
                    )

                initial_wait_ms = random.randint(9000, 15000)
                self._emit(
                    logger,
                    (
                        f"[抖音私信引流] 消息页已打开，等待 {initial_wait_ms / 1000:.1f} 秒让会话列表和“陌生人消息”入口刷新出来"
                        if not is_retry
                        else f"[抖音私信引流] 消息页已刷新，等待 {initial_wait_ms / 1000:.1f} 秒让会话列表和“陌生人消息”入口重新刷新出来"
                    ),
                )
                await page.wait_for_timeout(initial_wait_ms)
                await self._raise_if_login_intercept(page)

            async def capture_chat_sidebar_debug(target_page: Page) -> Dict:
                try:
                    return await target_page.evaluate(
                        r"""
                        () => {
                            const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                            const readText = (node) => normalize(
                                node?.innerText
                                || node?.textContent
                                || node?.getAttribute?.('aria-label')
                                || node?.getAttribute?.('title')
                                || ''
                            );
                            const isVisible = (node) => {
                                if (!node) return false;
                                const style = window.getComputedStyle(node);
                                const rect = node.getBoundingClientRect();
                                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                            };

                            const conversationRows = Array.from(
                                document.querySelectorAll('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]')
                            ).filter((node) => isVisible(node));

                            const sampleTitles = conversationRows
                                .map((node) => {
                                    const title = normalize(
                                        node.querySelector('.conversationConversationItemtitle, [class*="Itemtitle"], [class*="Boxtitle"]')?.textContent || ''
                                    );
                                    return title || readText(node).split('\n')[0];
                                })
                                .filter(Boolean)
                                .slice(0, 12);

                            const exactStrangerNodes = Array.from(
                                document.querySelectorAll('div, span, p, pre, a, button')
                            ).filter((node) => {
                                const text = readText(node);
                                return text === '陌生人消息';
                            });

                            return {
                                body_has_stranger_text: normalize(document.body?.innerText || '').includes('陌生人消息'),
                                conversation_count: conversationRows.length,
                                sample_titles: sampleTitles,
                                stranger_title_count: exactStrangerNodes.length,
                            };
                        }
                        """
                    )
                except Exception:
                    return {
                        "body_has_stranger_text": False,
                        "conversation_count": 0,
                        "sample_titles": [],
                        "stranger_title_count": 0,
                    }

            def summarize_sidebar_debug(snapshot: Dict) -> str:
                if not isinstance(snapshot, dict):
                    return "无可用页面快照"
                sample_titles = [str(item).strip() for item in snapshot.get("sample_titles", []) if str(item).strip()]
                sample_text = " / ".join(sample_titles[:6]) if sample_titles else "无"
                return (
                    f"可见会话 {int(snapshot.get('conversation_count', 0) or 0)} 个，"
                    f"页面含“陌生人消息”文本={bool(snapshot.get('body_has_stranger_text'))}，"
                    f"精确标题节点 {int(snapshot.get('stranger_title_count', 0) or 0)} 个，"
                    f"样本标题：{sample_text}"
                )

            async def wait_for_chat_sidebar_ready(target_page: Page) -> Dict:
                last_snapshot: Dict = {}
                last_signature = ""
                stable_rounds = 0
                for _ in range(12):
                    snapshot = await capture_chat_sidebar_debug(target_page)
                    last_snapshot = snapshot
                    sample_titles = snapshot.get("sample_titles", []) if isinstance(snapshot.get("sample_titles", []), list) else []
                    signature = "|".join(str(item).strip() for item in sample_titles[:6])
                    if int(snapshot.get("conversation_count", 0) or 0) > 0 and signature:
                        if signature == last_signature:
                            stable_rounds += 1
                        else:
                            stable_rounds = 0
                        if stable_rounds >= 1:
                            return snapshot
                    last_signature = signature
                    await target_page.wait_for_timeout(1000)
                return last_snapshot

            async def is_stranger_panel_visible(target_page: Page) -> bool:
                try:
                    return bool(
                        await target_page.evaluate(
                            r"""
                            () => {
                                const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                                const isVisible = (node) => {
                                    if (!node) return false;
                                    const style = window.getComputedStyle(node);
                                    const rect = node.getBoundingClientRect();
                                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                                };
                                const legacyRoots = Array.from(document.querySelectorAll('.conversationStrangerConversationListtransition'));
                                if (legacyRoots.some((root) => {
                                    const title = normalize(root.querySelector('.conversationStrangerConversationListtitle')?.textContent || '');
                                    return title.includes('陌生人消息') && isVisible(root);
                                })) {
                                    return true;
                                }

                                const entrySelector = '.conversationStrangerBoxwrapper, .conversationStrangerBoxrowArea2, [class*="conversationStrangerBox"], [data-e2e="conversation-item"]';
                                const titleNodes = Array.from(document.querySelectorAll('div, span, p, pre, a, button'))
                                    .filter((node) => normalize(node.textContent || node.innerText || '') === '陌生人消息' && isVisible(node));
                                return titleNodes.some((titleNode) => {
                                    if (titleNode.closest(entrySelector)) return false;
                                    let current = titleNode.parentElement;
                                    for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
                                        if (!isVisible(current)) continue;
                                        const hasConversationItems = Boolean(
                                            current.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]')
                                        );
                                        const hasBackButton = Boolean(
                                            current.querySelector('svg, [class*="back"], [aria-label*="返回"], [aria-label*="back"]')
                                        );
                                        if (hasConversationItems && (hasBackButton || depth <= 2)) return true;
                                    }
                                    return false;
                                });
                            }
                            """
                        )
                    )
                except Exception:
                    return False

            async def enter_stranger_message_panel(target_page: Page) -> tuple[bool, str]:
                if await is_stranger_panel_visible(target_page):
                    return True, ""
                last_debug = await capture_chat_sidebar_debug(target_page)
                for attempt in range(1, 7):
                    click_result = await target_page.evaluate(
                        r"""
                        () => {
                            const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                            const readText = (node) => normalize(
                                node?.innerText
                                || node?.textContent
                                || node?.getAttribute?.('aria-label')
                                || node?.getAttribute?.('title')
                                || ''
                            );
                            const isVisible = (node) => {
                                if (!node) return false;
                                const style = window.getComputedStyle(node);
                                const rect = node.getBoundingClientRect();
                                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                            };
                            const candidates = [];
                            const seen = new Set();
                            const entrySelector = '.conversationStrangerBoxwrapper, .conversationStrangerBoxrowArea2, [class*="conversationStrangerBox"]';

                            const pushCandidate = (node, score, reason) => {
                                if (!node) return;
                                let clickable = node.closest('.conversationStrangerBoxrowArea2')
                                    || node.closest('[class*="conversationStrangerBox"]')
                                    || node.closest('[role="button"], button, a, [data-e2e="conversation-item"], [class*="rowArea"], [class*="wrapper"], [class*="item"]')
                                    || node;
                                if (!clickable) return;
                                const rect = clickable.getBoundingClientRect();
                                if (rect.width < 40 || rect.height < 20) return;
                                const key = `${Math.round(rect.left)}|${Math.round(rect.top)}|${Math.round(rect.width)}|${Math.round(rect.height)}|${reason}`;
                                if (seen.has(key)) return;
                                seen.add(key);
                                const classText = `${node.className || ''} ${clickable.className || ''}`;
                                if (!isVisible(node) && !isVisible(clickable)) return;
                                if ((rect.width > window.innerWidth * 0.95) && (rect.height > window.innerHeight * 0.95)) score -= 200;
                                if (rect.left < window.innerWidth * 0.45) score += 10;
                                if (/conversationstranger|stranger/i.test(classText)) score += 80;
                                if (/conversation|item|wrapper|rowarea|title/i.test(classText)) score += 15;
                                candidates.push({
                                    node: clickable,
                                    score,
                                    reason,
                                    text: readText(clickable),
                                    classText,
                                });
                            };

                            for (const node of document.querySelectorAll('div, span, p, pre, a, button')) {
                                const text = readText(node);
                                if (text !== '陌生人消息') continue;
                                const wrapper = node.closest(entrySelector);
                                if (wrapper) {
                                    pushCandidate(wrapper, 220, 'entry-title-wrapper');
                                } else {
                                    pushCandidate(node, 120, 'entry-title-node');
                                }
                            }

                            for (const node of document.querySelectorAll('.conversationStrangerBoxrowArea2')) {
                                const title = normalize(node.querySelector('.conversationStrangerBoxtitle')?.textContent || '');
                                const preview = normalize(node.querySelector('.ConversationItemHinttextBox')?.textContent || '');
                                if (title.includes('陌生人消息')) {
                                    pushCandidate(node, 100 + (preview ? 5 : 0), 'legacy-row-area');
                                }
                            }

                            for (const node of document.querySelectorAll('[class*="conversationStrangerBox"]')) {
                                const text = readText(node);
                                if (text.includes('陌生人消息')) {
                                    const title = normalize(node.querySelector('.conversationStrangerBoxtitle')?.textContent || '');
                                    pushCandidate(node, title.includes('陌生人消息') ? 80 : 50, 'legacy-stranger-box');
                                }
                            }

                            candidates.sort((a, b) => b.score - a.score);
                            const best = candidates[0] || null;
                            if (!best) {
                                return {
                                    clicked: false,
                                    candidate_count: 0,
                                    sample_candidates: [],
                                };
                            }

                            best.node.scrollIntoView({ block: 'center' });
                            best.node.click();
                            return {
                                clicked: true,
                                candidate_count: candidates.length,
                                selected_reason: best.reason,
                                selected_text: best.text.slice(0, 80),
                                sample_candidates: candidates.slice(0, 5).map((item) => ({
                                    reason: item.reason,
                                    score: item.score,
                                    text: item.text.slice(0, 80),
                                })),
                            };
                        }
                        """
                    )
                    if bool(click_result.get("clicked")):
                        const_reason = str(click_result.get("selected_reason", "") or "").strip()
                        const_text = str(click_result.get("selected_text", "") or "").strip()
                        self._emit(
                            logger,
                            f"[抖音私信引流] 已点击陌生人消息入口：{const_reason or '-'} / {const_text or '-'}",
                        )
                        await target_page.wait_for_timeout(2200)
                        for _ in range(10):
                            if await is_stranger_panel_visible(target_page):
                                return True, ""
                            await target_page.wait_for_timeout(500)
                    last_debug = await capture_chat_sidebar_debug(target_page)
                    if attempt in {1, 3, 6}:
                        self._emit(
                            logger,
                            f"[抖音私信引流] 第 {attempt} 次查找“陌生人消息”入口未成功，{summarize_sidebar_debug(last_debug)}",
                            "warning",
                        )
                    await target_page.wait_for_timeout(1000)
                return False, f"未找到“陌生人消息”入口，{summarize_sidebar_debug(last_debug)}"

            async def ensure_stranger_message_panel(target_page: Page) -> tuple[bool, str]:
                if await is_stranger_panel_visible(target_page):
                    return True, ""
                return await enter_stranger_message_panel(target_page)

            async def read_visible_direct_conversations(target_page: Page) -> List[Dict]:
                return await target_page.evaluate(
                    r"""
                    () => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const isGroupLikeConversation = (name, preview, text, node) => {
                            const normalizedName = normalize(name);
                            const normalizedPreview = normalize(preview);
                            const normalizedText = normalize(text);
                            const hasGroupIcon = Boolean(node?.querySelector('img.commonConversationIconnoDrag'));
                            const hasUserAvatar = Boolean(node?.querySelector('.commonIMAvataravatarContainer'));
                            const hasMuteIcon = Boolean(node?.querySelector('.ConversationItemTagNextToTitlemuteIcon'));
                            const senderPrefixInPreview = /^[^：:\n]{1,24}[：:]/.test(normalizedPreview);
                            const joinGroupHint = /加入了群聊|通过.+加入了群聊|新成员可查看历史消息|群聊已满员|查看和管理\s*群成员|本群|置顶公告/.test(normalizedPreview || normalizedText);
                            const nameLooksLikeGroup = /群|群聊|交流群|社群|分群/.test(normalizedName);
                            return Boolean(
                                nameLooksLikeGroup
                                || joinGroupHint
                                || senderPrefixInPreview
                                || (hasGroupIcon && !hasUserAvatar)
                                || (hasMuteIcon && hasGroupIcon)
                            );
                        };
                        const toAbsoluteUserUrl = (value) => {
                            const raw = String(value || '').trim();
                            if (!raw) return '';
                            if (raw.startsWith('http://') || raw.startsWith('https://')) return raw.split('?')[0];
                            if (raw.startsWith('//')) return `https:${raw}`.split('?')[0];
                            if (raw.startsWith('/')) return `https://www.douyin.com${raw}`.split('?')[0];
                            return '';
                        };
                        const isVisible = (node) => {
                            if (!node) return false;
                            const style = window.getComputedStyle(node);
                            const rect = node.getBoundingClientRect();
                            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                        };
                        const findRoot = () => {
                            const entrySelector = '.conversationStrangerBoxwrapper, .conversationStrangerBoxrowArea2, [class*="conversationStrangerBox"], [data-e2e="conversation-item"]';
                            const titleNodes = Array.from(document.querySelectorAll('div, span, p, pre, a, button'))
                                .filter((node) => normalize(node.textContent || node.innerText || '') === '陌生人消息');
                            for (const titleNode of titleNodes) {
                                if (titleNode.closest(entrySelector)) continue;
                                let current = titleNode.parentElement;
                                for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
                                    if (!isVisible(current)) continue;
                                    const hasConversationItems = Boolean(
                                        current.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]')
                                    );
                                    if (!hasConversationItems) continue;
                                    const hasBackButton = Boolean(
                                        current.querySelector('svg, [class*="back"], [aria-label*="返回"], [aria-label*="back"]')
                                    );
                                    if (hasBackButton || depth <= 2) return current;
                                }
                            }
                            return null;
                        };
                        const root = findRoot();
                        if (!root) return [];
                        const rows = [];
                        for (const node of root.querySelectorAll('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]')) {
                            const text = normalize(node.innerText || node.textContent || '');
                            if (!text) continue;
                            const name = normalize(node.querySelector('.conversationConversationItemtitle')?.textContent || '');
                            if (!name) continue;
                            const preview = normalize(node.querySelector('.ConversationItemHinttextBox')?.textContent || '');
                            if (isGroupLikeConversation(name, preview, text, node)) continue;
                            const timeText = normalize(node.querySelector('.ConversationItemTagNextToTitletimeStr')?.textContent || '');
                            const avatar = String(node.querySelector('img')?.src || '').split('?')[0];
                            const indexText = normalize(node.closest('[data-index]')?.getAttribute('data-index') || '');
                            const unreadNode = node.querySelector('.ConversationItemUnReadCountdigitsNumberPop, .semi-badge-count, .ConversationItemUnReadCountmutedUnreadBadge');
                            const unreadText = normalize(unreadNode?.textContent || '');
                            const unreadDigits = unreadText.match(/\d+/);
                            const unreadCount = unreadDigits ? Number(unreadDigits[0] || '0') : (unreadNode ? 1 : 0);

                            const allHints = [];
                            const pushHint = (value) => {
                                const normalized = normalize(value);
                                if (normalized && !allHints.includes(normalized)) allHints.push(normalized);
                            };
                            pushHint(node.getAttribute('data-id'));
                            pushHint(node.getAttribute('data-conversation-id'));
                            pushHint(node.getAttribute('data-key'));
                            pushHint(node.getAttribute('data-im-id'));
                            pushHint(indexText ? `index:${indexText}` : '');
                            const dataset = node.dataset || {};
                            for (const key of Object.keys(dataset)) {
                                if (/id|conversation|session|chat/i.test(key)) pushHint(dataset[key]);
                            }

                            const profileUrl = Array.from(node.querySelectorAll('a[href*="/user/"]'))
                                .map((anchor) => toAbsoluteUserUrl(anchor.getAttribute('href') || ''))
                                .find((href) => href.includes('/user/')) || '';
                            const conversationId = allHints[0] || profileUrl || `${name}|${preview}|${avatar}`;
                            rows.push({
                                conversation_key: conversationId,
                                conversation_id: allHints[0] || '',
                                stranger_index: indexText,
                                username: name,
                                preview_text: preview || text,
                                avatar_url: avatar,
                                profile_url: profileUrl,
                                unread_count: Number.isFinite(unreadCount) ? unreadCount : 0,
                                is_unread: Boolean(unreadNode) && (Number.isFinite(unreadCount) ? unreadCount : 0) > 0,
                                time_text: timeText,
                                conversation_source: 'stranger_messages',
                            });
                        }
                        const unique = [];
                        const seen = new Set();
                        for (const row of rows) {
                            const key = `${row.conversation_key}|${row.username}|${row.preview_text}`;
                            if (seen.has(key)) continue;
                            seen.add(key);
                            unique.push(row);
                        }
                        return unique;
                    }
                    """
                )

            async def open_direct_conversation(target_page: Page, row: Dict) -> tuple[bool, str]:
                ready, reason = await ensure_stranger_message_panel(target_page)
                if not ready:
                    return False, reason or "陌生人消息面板不可用"
                conversation_key = str(row.get("conversation_key", "") or "").strip()
                stranger_index = str(row.get("stranger_index", "") or "").strip()
                username = str(row.get("username", "") or "").strip()
                preview_text = str(row.get("preview_text", "") or "").strip()

                clicked = await target_page.evaluate(
                    r"""
                    (payload) => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const isVisible = (node) => {
                            if (!node) return false;
                            const style = window.getComputedStyle(node);
                            const rect = node.getBoundingClientRect();
                            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                        };
                        const findRoot = () => {
                            const entrySelector = '.conversationStrangerBoxwrapper, .conversationStrangerBoxrowArea2, [class*="conversationStrangerBox"], [data-e2e="conversation-item"]';
                            const titleNode = Array.from(document.querySelectorAll('div, span, p, pre, a, button'))
                                .find((node) => normalize(node.textContent || node.innerText || '') === '陌生人消息');
                            if (!titleNode) return null;
                            if (titleNode.closest(entrySelector)) return null;
                            let current = titleNode.parentElement;
                            for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
                                if (!isVisible(current)) continue;
                                const hasConversationItems = Boolean(
                                    current.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]')
                                );
                                if (!hasConversationItems) continue;
                                const hasBackButton = Boolean(
                                    current.querySelector('svg, [class*="back"], [aria-label*="返回"], [aria-label*="back"]')
                                );
                                if (hasBackButton || depth <= 2) return current;
                            }
                            return null;
                        };
                        const root = findRoot();
                        if (!root) return false;
                        const key = normalize(payload?.conversation_key || '');
                        const indexText = normalize(payload?.stranger_index || '');
                        const name = normalize(payload?.username || '');
                        const preview = normalize(payload?.preview_text || '');
                        const rows = Array.from(root.querySelectorAll('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]'));
                        const scoreNode = (node) => {
                            let score = 0;
                            const text = normalize(node.innerText || node.textContent || '');
                            const title = normalize(node.querySelector('.conversationConversationItemtitle')?.textContent || '');
                            const hint = normalize(node.querySelector('.ConversationItemHinttextBox')?.textContent || '');
                            const nodeIndex = normalize(node.closest('[data-index]')?.getAttribute('data-index') || '');
                            const dataset = node.dataset || {};
                            const hints = [
                                node.getAttribute('data-id'),
                                node.getAttribute('data-conversation-id'),
                                node.getAttribute('data-key'),
                                node.getAttribute('data-im-id'),
                                nodeIndex ? `index:${nodeIndex}` : '',
                            ];
                            for (const datasetKey of Object.keys(dataset)) {
                                if (/id|conversation|session|chat/i.test(datasetKey)) hints.push(dataset[datasetKey]);
                            }
                            if (key && hints.some((value) => normalize(value) === key)) score += 10;
                            if (indexText && nodeIndex === indexText) score += 8;
                            if (name && title === name) score += 6;
                            if (preview && hint === preview) score += 3;
                            if (preview && text.includes(preview)) score += 1;
                            return score;
                        };
                        const target = rows
                            .map((node) => ({ node, score: scoreNode(node) }))
                            .sort((a, b) => b.score - a.score)[0];
                        if (!target || target.score <= 0) return false;
                        const node = target.node;
                        node.scrollIntoView({ block: 'center' });
                        const clickable = node.querySelector('.conversationConversationItemrowArea2')
                            || node.querySelector('.conversationConversationItemtitle')
                            || node;
                        clickable.click();
                        return true;
                    }
                    """,
                    {
                        "conversation_key": conversation_key,
                        "stranger_index": stranger_index,
                        "username": username,
                        "preview_text": preview_text,
                    },
                )
                if not clicked:
                    return False, "未找到目标会话"
                await target_page.wait_for_timeout(1200)
                return True, ""

            async def extract_opened_conversation(target_page: Page) -> Dict:
                return await target_page.evaluate(
                    r"""
                    () => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const toAbsoluteUserUrl = (value) => {
                            const raw = String(value || '').trim();
                            if (!raw) return '';
                            if (raw.startsWith('http://') || raw.startsWith('https://')) return raw.split('?')[0];
                            if (raw.startsWith('//')) return `https:${raw}`.split('?')[0];
                            if (raw.startsWith('/')) return `https://www.douyin.com${raw}`.split('?')[0];
                            return '';
                        };
                        const readProfileUrl = () => {
                            const panel = document.querySelector('.RightPanel') || document.body;
                            const nodes = [panel, ...Array.from(panel.querySelectorAll('*'))];
                            for (const node of nodes) {
                                const href = toAbsoluteUserUrl(node?.getAttribute?.('href') || '');
                                if (href.includes('/user/')) return href;
                                const dataset = node?.dataset || {};
                                for (const key of Object.keys(dataset)) {
                                    const value = toAbsoluteUserUrl(dataset[key]);
                                    if (value.includes('/user/')) return value;
                                }
                            }
                            return '';
                        };
                        const headerName = normalize(
                            document.querySelector('.RightPanelHeadertitle, .RightPanelHeader .semi-typography, .semi-typography')?.textContent || ''
                        );
                        const headerAvatar = String(
                            document.querySelector('.RightPanelHeader img, .RightPanel img, .chatHeader img')?.src || ''
                        ).split('?')[0];
                        const panel = document.querySelector('.RightPanel') || document.body;
                        const panelRect = panel.getBoundingClientRect();
                        const centerX = panelRect.left + panelRect.width / 2;
                        const items = [];
                        for (const bubble of document.querySelectorAll('[data-e2e="msg-item-content"]')) {
                            const text = normalize(bubble.textContent || '');
                            if (!text) continue;
                            const rect = bubble.getBoundingClientRect();
                            const bubbleCenterX = rect.left + rect.width / 2;
                            items.push({
                                text,
                                is_incoming: bubbleCenterX < centerX,
                            });
                        }
                        const incoming = items.filter((item) => item.is_incoming);
                        const latestIncoming = incoming.length ? incoming[incoming.length - 1].text : (items.length ? items[items.length - 1].text : '');
                        return {
                            username: headerName,
                            avatar_url: headerAvatar,
                            profile_url: readProfileUrl(),
                            incoming_message: latestIncoming,
                        };
                    }
                    """
                )

            async def scroll_conversation_sidebar(target_page: Page) -> bool:
                ready, _ = await ensure_stranger_message_panel(target_page)
                if not ready:
                    return False
                return bool(
                    await target_page.evaluate(
                        r"""
                        () => {
                            const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                            const isVisible = (node) => {
                                if (!node) return false;
                                const style = window.getComputedStyle(node);
                                const rect = node.getBoundingClientRect();
                                return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                            };
                            const entrySelector = '.conversationStrangerBoxwrapper, .conversationStrangerBoxrowArea2, [class*="conversationStrangerBox"], [data-e2e="conversation-item"]';
                            const titleNode = Array.from(document.querySelectorAll('div, span, p, pre, a, button'))
                                .find((node) => normalize(node.textContent || node.innerText || '') === '陌生人消息');
                            if (!titleNode || titleNode.closest(entrySelector)) return false;
                            let root = null;
                            let current = titleNode.parentElement;
                            for (let depth = 0; current && depth < 8; depth += 1, current = current.parentElement) {
                                if (!isVisible(current)) continue;
                                const hasConversationItems = Boolean(
                                    current.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]')
                                );
                                if (!hasConversationItems) continue;
                                const hasBackButton = Boolean(
                                    current.querySelector('svg, [class*="back"], [aria-label*="返回"], [aria-label*="back"]')
                                );
                                if (hasBackButton || depth <= 2) {
                                    root = current;
                                    break;
                                }
                            }
                            if (!root) return false;
                            const first = root.querySelector('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]');
                            let scroller = first ? first.parentElement : root.querySelector('.conversationStrangerConversationListlistWrapper');
                            while (scroller && scroller !== root) {
                                if (scroller.scrollHeight > scroller.clientHeight + 80) break;
                                scroller = scroller.parentElement;
                            }
                            if (!scroller || scroller === root || scroller.scrollHeight <= scroller.clientHeight + 80) {
                                scroller = root.querySelector('.conversationStrangerConversationListlistWrapper');
                            }
                            if (!scroller || scroller.scrollHeight <= scroller.clientHeight + 80) return false;
                            const before = scroller.scrollTop;
                            scroller.scrollTop += Math.max(scroller.clientHeight * 0.85, 420);
                            return scroller.scrollTop > before;
                        }
                        """
                    )
                )

            async def prepare_and_enter_stranger_panel(is_retry: bool = False) -> tuple[bool, str]:
                await open_or_refresh_chat_page(is_retry=is_retry)
                sidebar_snapshot = await wait_for_chat_sidebar_ready(page)
                self._emit(
                    logger,
                    (
                        f"[抖音私信引流] 会话列表准备完成，{summarize_sidebar_debug(sidebar_snapshot)}"
                        if not is_retry
                        else f"[抖音私信引流] 刷新后会话列表准备完成，{summarize_sidebar_debug(sidebar_snapshot)}"
                    ),
                )
                return await enter_stranger_message_panel(page)

            entered, reason = await prepare_and_enter_stranger_panel(is_retry=False)
            if not entered:
                self._emit(logger, f"[抖音私信引流] 首次进入陌生人消息失败：{reason}", "warning")
                entered, reason = await prepare_and_enter_stranger_panel(is_retry=True)
            if not entered:
                self._emit(logger, f"[抖音私信引流] 进入陌生人消息失败：{reason}", "warning")
                return []

            collected_map: Dict[str, Dict] = {}
            stagnant_rounds = 0
            while len(collected_map) < limit and stagnant_rounds < 4:
                if should_stop and should_stop():
                    break
                visible_rows = await read_visible_direct_conversations(page)
                before_count = len(collected_map)
                for row in visible_rows or []:
                    if not isinstance(row, dict):
                        continue
                    key = str(row.get("conversation_key", "") or row.get("profile_url", "") or row.get("username", "")).strip()
                    if not key or key in collected_map:
                        continue
                    collected_map[key] = row
                    if len(collected_map) >= limit:
                        break
                stagnant_rounds = stagnant_rounds + 1 if len(collected_map) == before_count else 0
                if len(collected_map) >= limit:
                    break
                moved = await scroll_conversation_sidebar(page)
                if not moved:
                    break
                await page.wait_for_timeout(1200)

            candidates = list(collected_map.values())[:limit]
            results: List[Dict] = []
            total = len(candidates)
            for index, row in enumerate(candidates, start=1):
                if should_stop and should_stop():
                    break
                username = str(row.get("username", "") or "").strip()
                merged = {
                    **row,
                    "incoming_message": str(
                        row.get("incoming_message", "")
                        or row.get("preview_text", "")
                        or ""
                    ).strip(),
                    "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                if not str(merged.get("username", "") or "").strip():
                    merged["username"] = username
                results.append(merged)
                if progress_callback:
                    try:
                        progress_callback(len(results), total, str(merged.get("username", "") or username))
                    except Exception:
                        pass
                self._emit(
                    logger,
                    f"[抖音私信引流] 已采集 {len(results)}/{total}：{merged.get('username') or username or '-'}",
                    "info",
                )

            self._emit(logger, f"[抖音私信引流] 共提取 {len(results)} 条陌生人私信", "success")
            return results
        finally:
            await page.close()

    async def collect_chat_page_private_messages(
        self,
        max_conversations: int = 100,
        should_stop: Optional[Callable[[], bool]] = None,
        logger: Optional[Callable[[str, str], None]] = None,
        progress_callback: Optional[Callable[[int, int, str], None]] = None,
    ) -> List[Dict]:
        limit = max(1, min(int(max_conversations or 100), 100))
        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, "[抖音私信聚合] 打开当前消息页：https://www.douyin.com/chat")
            await page.goto("https://www.douyin.com/chat", wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
                self._emit(logger, "[抖音私信聚合] 当前消息页首屏网络请求已基本稳定")
            except Exception:
                self._emit(logger, "[抖音私信聚合] 等待当前消息页网络稳定超时，继续检查会话列表", "warning")
            await self._raise_if_login_intercept(page)
            try:
                await page.wait_for_function(
                    """() => document.querySelectorAll('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]').length > 0""",
                    timeout=6000,
                )
                self._emit(logger, "[抖音私信聚合] 当前消息页会话列表已出现，继续读取")
            except Exception:
                self._emit(logger, "[抖音私信聚合] 等待会话列表出现超时，继续尝试读取当前页", "warning")
            await page.wait_for_timeout(600)

            async def read_visible_chat_conversations(target_page: Page) -> List[Dict]:
                return await target_page.evaluate(
                    r"""
                    () => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const isVisible = (node) => {
                            if (!node) return false;
                            const style = window.getComputedStyle(node);
                            const rect = node.getBoundingClientRect();
                            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                        };
                        const toAbsoluteUserUrl = (value) => {
                            const raw = String(value || '').trim();
                            if (!raw) return '';
                            if (raw.startsWith('http://') || raw.startsWith('https://')) return raw.split('?')[0];
                            if (raw.startsWith('//')) return `https:${raw}`.split('?')[0];
                            if (raw.startsWith('/')) return `https://www.douyin.com${raw}`.split('?')[0];
                            return '';
                        };
                        const isGroupLikeConversation = (name, preview, text, node) => {
                            const normalizedName = normalize(name);
                            const normalizedPreview = normalize(preview);
                            const normalizedText = normalize(text);
                            const hasGroupIcon = Boolean(node?.querySelector('img.commonConversationIconnoDrag'));
                            const hasUserAvatar = Boolean(node?.querySelector('.commonIMAvataravatarContainer'));
                            const hasMuteIcon = Boolean(node?.querySelector('.ConversationItemTagNextToTitlemuteIcon'));
                            const senderPrefixInPreview = /^[^：:\n]{1,24}[：:]/.test(normalizedPreview);
                            const joinGroupHint = /加入了群聊|通过.+加入了群聊|新成员可查看历史消息|群聊已满员|查看和管理\s*群成员|本群|置顶公告/.test(normalizedPreview || normalizedText);
                            const nameLooksLikeGroup = /群|群聊|交流群|社群|分群/.test(normalizedName);
                            return Boolean(
                                nameLooksLikeGroup
                                || joinGroupHint
                                || senderPrefixInPreview
                                || (hasGroupIcon && !hasUserAvatar)
                                || (hasMuteIcon && hasGroupIcon)
                            );
                        };

                        const rows = [];
                        for (const node of document.querySelectorAll('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]')) {
                            if (!isVisible(node)) continue;
                            const text = normalize(node.innerText || node.textContent || '');
                            if (!text) continue;
                            const name = normalize(node.querySelector('.conversationConversationItemtitle')?.textContent || '');
                            if (!name) continue;
                            const preview = normalize(node.querySelector('.ConversationItemHinttextBox')?.textContent || '');
                            if (isGroupLikeConversation(name, preview, text, node)) continue;
                            const timeText = normalize(node.querySelector('.ConversationItemTagNextToTitletimeStr')?.textContent || '');
                            const avatar = String(node.querySelector('img')?.src || '').split('?')[0];
                            const indexText = normalize(node.closest('[data-index]')?.getAttribute('data-index') || '');
                            const unreadNode = node.querySelector('.ConversationItemUnReadCountdigitsNumberPop, .semi-badge-count, .ConversationItemUnReadCountmutedUnreadBadge');
                            const unreadText = normalize(unreadNode?.textContent || '');
                            const unreadDigits = unreadText.match(/\d+/);
                            const unreadCount = unreadDigits ? Number(unreadDigits[0] || '0') : (unreadNode ? 1 : 0);

                            const profileUrl = Array.from(node.querySelectorAll('a[href*="/user/"]'))
                                .map((anchor) => toAbsoluteUserUrl(anchor.getAttribute('href') || ''))
                                .find((href) => href.includes('/user/')) || '';

                            const conversationId = normalize(
                                node.getAttribute('data-id')
                                || node.getAttribute('data-conversation-id')
                                || node.getAttribute('data-key')
                                || node.getAttribute('data-im-id')
                                || profileUrl
                                || name
                            );

                            rows.push({
                                conversation_key: conversationId,
                                conversation_id: conversationId,
                                stranger_index: indexText,
                                username: name,
                                preview_text: preview || text,
                                incoming_message: preview || text,
                                avatar_url: avatar,
                                profile_url: profileUrl,
                                unread_count: Number.isFinite(unreadCount) ? unreadCount : 0,
                                is_unread: Boolean(unreadNode) && (Number.isFinite(unreadCount) ? unreadCount : 0) > 0,
                                time_text: timeText,
                                conversation_source: 'chat_inbox',
                            });
                        }

                        const unique = [];
                        const seen = new Set();
                        for (const row of rows) {
                            const key = `${row.conversation_key}|${row.username}`;
                            if (seen.has(key)) continue;
                            seen.add(key);
                            unique.push(row);
                        }
                        return unique;
                    }
                    """
                )

            async def scroll_conversation_sidebar(target_page: Page) -> bool:
                return bool(
                    await target_page.evaluate(
                        r"""
                        () => {
                            const selectors = [
                                '.semi-navigation-list',
                                '.os-viewport',
                                '.conversationListwrapper',
                                '.conversationConversationListwrapper',
                            ];
                            let scroller = null;
                            for (const selector of selectors) {
                                const node = document.querySelector(selector);
                                if (node && node.scrollHeight > node.clientHeight + 8) {
                                    scroller = node;
                                    break;
                                }
                            }
                            if (!scroller) {
                                const rows = Array.from(document.querySelectorAll('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]'));
                                scroller = rows[0]?.parentElement || document.scrollingElement || document.documentElement;
                            }
                            if (!scroller) return false;
                            const before = scroller.scrollTop;
                            scroller.scrollTop = Math.min(scroller.scrollTop + Math.max(420, scroller.clientHeight * 0.8), scroller.scrollHeight);
                            return Math.abs(scroller.scrollTop - before) > 4;
                        }
                        """
                    )
                )

            collected_map: Dict[str, Dict] = {}
            stagnant_rounds = 0
            while len(collected_map) < limit and stagnant_rounds < 4:
                if should_stop and should_stop():
                    break
                visible_rows = await read_visible_chat_conversations(page)
                before_count = len(collected_map)
                for row in visible_rows or []:
                    if not isinstance(row, dict):
                        continue
                    key = str(row.get("conversation_key", "") or row.get("profile_url", "") or row.get("username", "")).strip()
                    if not key or key in collected_map:
                        continue
                    collected_map[key] = row
                    if len(collected_map) >= limit:
                        break
                stagnant_rounds = stagnant_rounds + 1 if len(collected_map) == before_count else 0
                if len(collected_map) >= limit:
                    break
                moved = await scroll_conversation_sidebar(page)
                if not moved:
                    break
                await page.wait_for_timeout(1200)

            candidates = list(collected_map.values())[:limit]
            results: List[Dict] = []
            total = len(candidates)
            for index, row in enumerate(candidates, start=1):
                if should_stop and should_stop():
                    break
                username = str(row.get("username", "") or "").strip()
                merged = {
                    **row,
                    "incoming_message": str(
                        row.get("incoming_message", "")
                        or row.get("preview_text", "")
                        or ""
                    ).strip(),
                    "collected_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                }
                if not str(merged.get("username", "") or "").strip():
                    merged["username"] = username
                results.append(merged)
                if progress_callback:
                    try:
                        progress_callback(len(results), total, str(merged.get("username", "") or username))
                    except Exception:
                        pass
                self._emit(
                    logger,
                    f"[抖音私信聚合] 已采集 {len(results)}/{total}：{merged.get('username') or username or '-'}",
                    "info",
                )

            if results and not (should_stop and should_stop()):
                self._emit(
                    logger,
                    f"[抖音私信聚合] 开始同步 {len(results)} 条会话详情，页面会展示最近一次同步后的消息内容",
                    "info",
                )
            for index, row in enumerate(results, start=1):
                if should_stop and should_stop():
                    break
                username = str(row.get("username", "") or f"会话 {index}").strip()
                try:
                    opened, reason = await self.open_chat_page_conversation(page, row)
                    if not opened:
                        self._emit(
                            logger,
                            f"[抖音私信聚合] 同步会话详情失败：{username}，原因：{reason or '未找到目标会话'}",
                            "warning",
                        )
                        continue
                    await page.wait_for_timeout(900)
                    detail = await self.extract_chat_page_conversation_detail(page)
                    detail_messages = detail.get("messages", []) if isinstance(detail, dict) else []
                    if not isinstance(detail_messages, list) or not detail_messages:
                        self._emit(
                            logger,
                            f"[抖音私信聚合] 会话 {username} 未读取到右侧消息气泡，保留列表预览",
                            "warning",
                        )
                        continue
                    row.update(
                        {
                            "username": str(detail.get("username", "") or row.get("username", "") or "").strip(),
                            "avatar_url": str(
                                detail.get("avatar_url", "")
                                or row.get("avatar_url", "")
                                or row.get("avatar", "")
                                or ""
                            ).strip(),
                            "profile_url": str(detail.get("profile_url", "") or row.get("profile_url", "") or "").strip(),
                            "incoming_message": str(
                                detail.get("incoming_message", "")
                                or row.get("incoming_message", "")
                                or row.get("preview_text", "")
                                or ""
                            ).strip(),
                            "reply_message": str(
                                detail.get("reply_message", "") or row.get("reply_message", "") or ""
                            ).strip(),
                            "messages": detail_messages,
                            "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                            "is_partial": False,
                        }
                    )
                    self._emit(
                        logger,
                        f"[抖音私信聚合] 已同步 {index}/{len(results)}：{username}",
                        "success",
                    )
                except Exception as exc:
                    self._emit(
                        logger,
                        f"[抖音私信聚合] 同步会话详情失败：{username}，原因：{exc}",
                        "warning",
                    )

            self._emit(logger, f"[抖音私信聚合] 共提取 {len(results)} 条当前消息会话", "success")
            return results
        finally:
            await page.close()

    async def collect_chat_page_conversation_detail(
        self,
        row: Dict,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        payload = row if isinstance(row, dict) else {}
        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, "[抖音私信聚合] 打开当前消息页读取会话详情：https://www.douyin.com/chat")
            await page.goto("https://www.douyin.com/chat", wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass

            initial_wait_ms = random.randint(9000, 13000)
            self._emit(
                logger,
                f"[抖音私信聚合] 会话详情页已打开，等待 {initial_wait_ms / 1000:.1f} 秒让会话列表刷新出来",
            )
            await page.wait_for_timeout(initial_wait_ms)
            await self._raise_if_login_intercept(page)

            async def open_chat_conversation(target_page: Page, target_row: Dict) -> tuple[bool, str]:
                conversation_key = str(target_row.get("conversation_key", "") or target_row.get("conversation_id", "") or "").strip()
                stranger_index = str(target_row.get("stranger_index", "") or "").strip()
                username = str(target_row.get("username", "") or "").strip()
                preview_text = str(
                    target_row.get("preview_text", "")
                    or target_row.get("incoming_message", "")
                    or ""
                ).strip()
                clicked = await target_page.evaluate(
                    r"""
                    (payload) => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const toAbsoluteUserUrl = (value) => {
                            const raw = String(value || '').trim();
                            if (!raw) return '';
                            if (raw.startsWith('http://') || raw.startsWith('https://')) return raw.split('?')[0];
                            if (raw.startsWith('//')) return `https:${raw}`.split('?')[0];
                            if (raw.startsWith('/')) return `https://www.douyin.com${raw}`.split('?')[0];
                            return '';
                        };
                        const conversationKey = normalize(payload?.conversation_key || '');
                        const strangerIndex = normalize(payload?.stranger_index || '');
                        const username = normalize(payload?.username || '');
                        const profileUrlNeedle = normalize(payload?.profile_url || '');
                        const preview = normalize(payload?.preview_text || '');
                        const rows = Array.from(document.querySelectorAll('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]'));
                        if (!rows.length) return false;
                        const scoreNode = (node) => {
                            const title = normalize(node.querySelector('.conversationConversationItemtitle')?.textContent || '');
                            const hint = normalize(node.querySelector('.ConversationItemHinttextBox')?.textContent || '');
                            const nodeIndex = normalize(node.closest('[data-index]')?.getAttribute('data-index') || '');
                            const profileUrl = Array.from(node.querySelectorAll('a[href*="/user/"]'))
                                .map((anchor) => toAbsoluteUserUrl(anchor.getAttribute('href') || ''))
                                .find((href) => href.includes('/user/')) || '';
                            const attrs = [
                                node.getAttribute('data-id'),
                                node.getAttribute('data-conversation-id'),
                                node.getAttribute('data-key'),
                                node.getAttribute('data-im-id'),
                                profileUrl,
                                nodeIndex ? `index:${nodeIndex}` : '',
                            ].map(normalize).filter(Boolean);
                            let score = 0;
                        if (conversationKey && attrs.includes(conversationKey)) score += 12;
                        if (profileUrlNeedle && attrs.includes(profileUrlNeedle)) score += 12;
                        if (strangerIndex && nodeIndex === strangerIndex) score += 8;
                        if (username && title === username) score += 6;
                        if (username && title.includes(username)) score += 4;
                        if (preview && hint === preview) score += 4;
                        if (preview && hint.includes(preview)) score += 2;
                        if (preview && normalize(node.innerText || node.textContent || '').includes(preview)) score += 1;
                        if (username && normalize(node.innerText || node.textContent || '').includes(username)) score += 1;
                        return score;
                    };
                        const target = rows
                            .map((node) => ({ node, score: scoreNode(node) }))
                            .sort((a, b) => b.score - a.score)[0];
                        if (!target || target.score <= 0) return false;
                        const node = target.node;
                        node.scrollIntoView({ block: 'center' });
                        const clickable = node.querySelector('.conversationConversationItemrowArea2')
                            || node.querySelector('.conversationConversationItemtitle')
                            || node;
                        clickable.click();
                        return true;
                    }
                    """,
                {
                    "conversation_key": conversation_key,
                    "stranger_index": stranger_index,
                    "username": username,
                    "profile_url": profile_url,
                    "preview_text": preview_text,
                },
            )
                if not clicked:
                    return False, "未找到目标会话"
                await target_page.wait_for_timeout(1200)
                return True, ""

            async def extract_chat_conversation_detail(target_page: Page) -> Dict:
                return await target_page.evaluate(
                    r"""
                    () => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const toAbsoluteUserUrl = (value) => {
                            const raw = String(value || '').trim();
                            if (!raw) return '';
                            if (raw.startsWith('http://') || raw.startsWith('https://')) return raw.split('?')[0];
                            if (raw.startsWith('//')) return `https:${raw}`.split('?')[0];
                            if (raw.startsWith('/')) return `https://www.douyin.com${raw}`.split('?')[0];
                            return '';
                        };
                        const panel = document.querySelector('.RightPanel') || document.body;
                        const headerName = normalize(
                            document.querySelector('.RightPanelHeadertitle, .RightPanelHeader .semi-typography, .semi-typography')?.textContent || ''
                        );
                        const headerAvatar = String(
                            document.querySelector('.RightPanelHeader img, .RightPanel img, .chatHeader img')?.src || ''
                        ).split('?')[0];
                        const readProfileUrl = () => {
                            const nodes = [panel, ...Array.from(panel.querySelectorAll('*'))];
                            for (const node of nodes) {
                                const href = toAbsoluteUserUrl(node?.getAttribute?.('href') || '');
                                if (href.includes('/user/')) return href;
                                const dataset = node?.dataset || {};
                                for (const key of Object.keys(dataset)) {
                                    const value = toAbsoluteUserUrl(dataset[key]);
                                    if (value.includes('/user/')) return value;
                                }
                            }
                            return '';
                        };
                        const panelRect = panel.getBoundingClientRect();
                        const centerX = panelRect.left + panelRect.width / 2;
                        const messages = [];
                        const bubbles = Array.from(document.querySelectorAll('[data-e2e="msg-item-content"]'));
                        for (const bubble of bubbles) {
                            const text = normalize(bubble.textContent || '');
                            if (!text) continue;
                            const rect = bubble.getBoundingClientRect();
                            const bubbleCenterX = rect.left + rect.width / 2;
                            const direction = bubbleCenterX < centerX ? 'incoming' : 'outgoing';
                            let timeText = '';
                            const container = bubble.closest('[class*="message"], [class*="msg"], li, div');
                            if (container) {
                                const timeCandidate = Array.from(container.querySelectorAll('time, [class*="time"], [data-e2e*="time"], .semi-typography'))
                                    .map((node) => normalize(node.textContent || ''))
                                    .find((value) => value && value !== text && /(\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}|昨天|前天|周.|刚刚)/.test(value));
                                timeText = timeCandidate || '';
                            }
                            messages.push({
                                direction,
                                text,
                                time_text: timeText,
                                label: direction === 'incoming' ? (headerName || '对方') : '我',
                            });
                        }
                        const incoming = messages.filter((item) => item.direction === 'incoming');
                        const outgoing = messages.filter((item) => item.direction === 'outgoing');
                        return {
                            username: headerName,
                            avatar_url: headerAvatar,
                            profile_url: readProfileUrl(),
                            incoming_message: incoming.length ? incoming[incoming.length - 1].text : '',
                            reply_message: outgoing.length ? outgoing[outgoing.length - 1].text : '',
                            messages,
                        };
                    }
                    """
                )

            opened, reason = await open_chat_conversation(page, payload)
            if not opened:
                raise RuntimeError(reason or "未找到目标会话")
            try:
                await page.wait_for_function(
                    """() => Boolean(document.querySelector('[data-e2e=\"msg-item-content\"]'))""",
                    timeout=15000,
                )
            except Exception:
                try:
                    await page.wait_for_function(
                        """() => Boolean(document.querySelector('.RightPanelHeadertitle, .RightPanelHeader'))""",
                        timeout=5000,
                    )
                except Exception:
                    pass
            await page.wait_for_timeout(1200)
            detail = await extract_chat_conversation_detail(page)
            messages = detail.get("messages", []) if isinstance(detail, dict) else []
            if not isinstance(messages, list) or not messages:
                raise RuntimeError("已打开会话，但没有读取到右侧消息气泡")
            self._emit(
                logger,
                f"[抖音私信聚合] 已读取会话详情：{str(detail.get('username', '') or payload.get('username', '') or '-').strip()}，共 {len(messages)} 条气泡",
                "success",
            )
            return {
                "conversation_key": str(payload.get("conversation_key", "") or payload.get("conversation_id", "") or "").strip(),
                "username": str(detail.get("username", "") or payload.get("username", "") or "").strip(),
                "avatar_url": str(detail.get("avatar_url", "") or payload.get("avatar_url", "") or payload.get("avatar", "") or "").strip(),
                "profile_url": str(detail.get("profile_url", "") or payload.get("profile_url", "") or "").strip(),
                "incoming_message": str(detail.get("incoming_message", "") or payload.get("incoming_message", "") or payload.get("preview_text", "") or "").strip(),
                "reply_message": str(detail.get("reply_message", "") or "").strip(),
                "messages": detail.get("messages", []),
                "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
            }
        finally:
            await page.close()

    async def send_chat_page_conversation_message(
        self,
        row: Dict,
        message: str,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        payload = row if isinstance(row, dict) else {}
        message = str(message or "").strip()
        message_parts = [
            part.strip()
            for part in message.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if str(part or "").strip()
        ]
        if not message_parts:
            raise ValueError("私信内容不能为空")

        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, "[抖音私信聚合] 打开当前消息页发送消息：https://www.douyin.com/chat")
            await page.goto("https://www.douyin.com/chat", wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=10000)
            except Exception:
                pass
            initial_wait_ms = random.randint(9000, 13000)
            await page.wait_for_timeout(initial_wait_ms)
            await self._raise_if_login_intercept(page)

            async def open_chat_conversation(target_page: Page, target_row: Dict) -> tuple[bool, str]:
                conversation_key = str(target_row.get("conversation_key", "") or target_row.get("conversation_id", "") or "").strip()
                stranger_index = str(target_row.get("stranger_index", "") or "").strip()
                username = str(target_row.get("username", "") or "").strip()
                preview_text = str(
                    target_row.get("preview_text", "")
                    or target_row.get("incoming_message", "")
                    or ""
                ).strip()
                clicked = await target_page.evaluate(
                    r"""
                    (payload) => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const toAbsoluteUserUrl = (value) => {
                            const raw = String(value || '').trim();
                            if (!raw) return '';
                            if (raw.startsWith('http://') || raw.startsWith('https://')) return raw.split('?')[0];
                            if (raw.startsWith('//')) return `https:${raw}`.split('?')[0];
                            if (raw.startsWith('/')) return `https://www.douyin.com${raw}`.split('?')[0];
                            return '';
                        };
                        const conversationKey = normalize(payload?.conversation_key || '');
                        const strangerIndex = normalize(payload?.stranger_index || '');
                        const username = normalize(payload?.username || '');
                        const preview = normalize(payload?.preview_text || '');
                        const rows = Array.from(document.querySelectorAll('[data-e2e="conversation-item"], [class*="conversationConversationItemwrapper"]'));
                        if (!rows.length) return false;
                        const scoreNode = (node) => {
                            const title = normalize(node.querySelector('.conversationConversationItemtitle')?.textContent || '');
                            const hint = normalize(node.querySelector('.ConversationItemHinttextBox')?.textContent || '');
                            const nodeIndex = normalize(node.closest('[data-index]')?.getAttribute('data-index') || '');
                            const profileUrl = Array.from(node.querySelectorAll('a[href*="/user/"]'))
                                .map((anchor) => toAbsoluteUserUrl(anchor.getAttribute('href') || ''))
                                .find((href) => href.includes('/user/')) || '';
                            const attrs = [
                                node.getAttribute('data-id'),
                                node.getAttribute('data-conversation-id'),
                                node.getAttribute('data-key'),
                                node.getAttribute('data-im-id'),
                                profileUrl,
                                nodeIndex ? `index:${nodeIndex}` : '',
                            ].map(normalize).filter(Boolean);
                            let score = 0;
                            if (conversationKey && attrs.includes(conversationKey)) score += 12;
                            if (strangerIndex && nodeIndex === strangerIndex) score += 8;
                            if (username && title === username) score += 6;
                            if (preview && hint === preview) score += 4;
                            if (preview && hint.includes(preview)) score += 2;
                            if (username && normalize(node.innerText || node.textContent || '').includes(username)) score += 1;
                            return score;
                        };
                        const target = rows
                            .map((node) => ({ node, score: scoreNode(node) }))
                            .sort((a, b) => b.score - a.score)[0];
                        if (!target || target.score <= 0) return false;
                        const node = target.node;
                        node.scrollIntoView({ block: 'center' });
                        const clickable = node.querySelector('.conversationConversationItemrowArea2')
                            || node.querySelector('.conversationConversationItemtitle')
                            || node;
                        clickable.click();
                        return true;
                    }
                    """,
                    {
                        "conversation_key": conversation_key,
                        "stranger_index": stranger_index,
                        "username": username,
                        "preview_text": preview_text,
                    },
                )
                if not clicked:
                    return False, "未找到目标会话"
                await target_page.wait_for_timeout(1200)
                return True, ""

            async def extract_chat_conversation_detail(target_page: Page) -> Dict:
                return await target_page.evaluate(
                    r"""
                    () => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const toAbsoluteUserUrl = (value) => {
                            const raw = String(value || '').trim();
                            if (!raw) return '';
                            if (raw.startsWith('http://') || raw.startsWith('https://')) return raw.split('?')[0];
                            if (raw.startsWith('//')) return `https:${raw}`.split('?')[0];
                            if (raw.startsWith('/')) return `https://www.douyin.com${raw}`.split('?')[0];
                            return '';
                        };
                        const panel = document.querySelector('.RightPanel') || document.body;
                        const headerName = normalize(
                            document.querySelector('.RightPanelHeadertitle, .RightPanelHeader .semi-typography, .semi-typography')?.textContent || ''
                        );
                        const headerAvatar = String(
                            document.querySelector('.RightPanelHeader img, .RightPanel img, .chatHeader img')?.src || ''
                        ).split('?')[0];
                        const readProfileUrl = () => {
                            const nodes = [panel, ...Array.from(panel.querySelectorAll('*'))];
                            for (const node of nodes) {
                                const href = toAbsoluteUserUrl(node?.getAttribute?.('href') || '');
                                if (href.includes('/user/')) return href;
                                const dataset = node?.dataset || {};
                                for (const key of Object.keys(dataset)) {
                                    const value = toAbsoluteUserUrl(dataset[key]);
                                    if (value.includes('/user/')) return value;
                                }
                            }
                            return '';
                        };
                        const panelRect = panel.getBoundingClientRect();
                        const centerX = panelRect.left + panelRect.width / 2;
                        const messages = [];
                        const bubbles = Array.from(document.querySelectorAll('[data-e2e="msg-item-content"]'));
                        for (const bubble of bubbles) {
                            const text = normalize(bubble.textContent || '');
                            if (!text) continue;
                            const rect = bubble.getBoundingClientRect();
                            const bubbleCenterX = rect.left + rect.width / 2;
                            const direction = bubbleCenterX < centerX ? 'incoming' : 'outgoing';
                            let timeText = '';
                            const container = bubble.closest('[class*="message"], [class*="msg"], li, div');
                            if (container) {
                                const timeCandidate = Array.from(container.querySelectorAll('time, [class*="time"], [data-e2e*="time"], .semi-typography'))
                                    .map((node) => normalize(node.textContent || ''))
                                    .find((value) => value && value !== text && /(\d{1,2}:\d{2}|\d{4}-\d{2}-\d{2}|昨天|前天|周.|刚刚)/.test(value));
                                timeText = timeCandidate || '';
                            }
                            messages.push({
                                direction,
                                text,
                                time_text: timeText,
                                label: direction === 'incoming' ? (headerName || '对方') : '我',
                            });
                        }
                        const incoming = messages.filter((item) => item.direction === 'incoming');
                        const outgoing = messages.filter((item) => item.direction === 'outgoing');
                        return {
                            username: headerName,
                            avatar_url: headerAvatar,
                            profile_url: readProfileUrl(),
                            incoming_message: incoming.length ? incoming[incoming.length - 1].text : '',
                            reply_message: outgoing.length ? outgoing[outgoing.length - 1].text : '',
                            messages,
                        };
                    }
                    """
                )

            opened, reason = await open_chat_conversation(page, payload)
            if not opened:
                raise RuntimeError(reason or "未找到目标会话")

            input_box = page.locator(
                'div[data-e2e="msg-input"] [contenteditable="true"], .public-DraftEditor-content[contenteditable="true"]'
            ).first
            await input_box.wait_for(state="visible", timeout=15000)
            send_button = page.locator(".e2e-send-msg-btn, [class*='send-msg-btn'], span.e2e-send-msg-btn").first
            await send_button.wait_for(state="visible", timeout=15000)

            expected_username = str(payload.get("username", "") or payload.get("conversation_key", "") or "-").strip()
            sent_messages = []
            total_parts = len(message_parts)
            for index, message_part in enumerate(message_parts, start=1):
                await input_box.click()
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await page.keyboard.type(message_part, delay=35)
                await send_button.click(timeout=10000)
                await page.wait_for_timeout(1200)
                delivered = await page.evaluate(
                    """
                    (sentText) => {
                        const needle = String(sentText || '').trim();
                        if (!needle) return false;
                        const texts = Array.from(document.querySelectorAll('[data-e2e="msg-item-content"]'))
                            .map((node) => (node.textContent || '').trim())
                            .filter(Boolean);
                        if (texts.some((text) => text.includes(needle))) return true;
                        const editor = document.querySelector(
                            'div[data-e2e="msg-input"] [contenteditable="true"][role="textbox"]'
                        );
                        const editorText = (editor?.textContent || '').trim();
                        return editorText.length === 0;
                    }
                    """,
                    message_part,
                )
                if not delivered:
                    raise RuntimeError(f"第 {index} 条消息发送按钮已点击，但未确认消息已发出")
                sent_messages.append(message_part)
                self._emit(
                    logger,
                    f"[抖音私信聚合] 已发送第 {index}/{total_parts} 条：{expected_username}",
                    "success",
                )
                if index < total_parts:
                    await page.wait_for_timeout(700)

            await page.wait_for_timeout(1200)
            detail = await extract_chat_conversation_detail(page)
            self._emit(
                logger,
                f"[抖音私信聚合] 已完成发送，共 {len(sent_messages)} 条：{expected_username}",
                "success",
            )
            return {
                "success": True,
                "conversation_key": str(payload.get("conversation_key", "") or payload.get("conversation_id", "") or "").strip(),
                "username": str(detail.get("username", "") or payload.get("username", "") or "").strip(),
                "avatar_url": str(detail.get("avatar_url", "") or payload.get("avatar_url", "") or payload.get("avatar", "") or "").strip(),
                "profile_url": str(detail.get("profile_url", "") or payload.get("profile_url", "") or "").strip(),
                "message": message,
                "messages_sent": sent_messages,
                "message_count": len(sent_messages),
                "detail": {
                    "conversation_key": str(payload.get("conversation_key", "") or payload.get("conversation_id", "") or "").strip(),
                    "username": str(detail.get("username", "") or payload.get("username", "") or "").strip(),
                    "avatar_url": str(detail.get("avatar_url", "") or payload.get("avatar_url", "") or payload.get("avatar", "") or "").strip(),
                    "profile_url": str(detail.get("profile_url", "") or payload.get("profile_url", "") or "").strip(),
                    "incoming_message": str(detail.get("incoming_message", "") or payload.get("incoming_message", "") or payload.get("preview_text", "") or "").strip(),
                    "reply_message": str(detail.get("reply_message", "") or "").strip(),
                    "messages": detail.get("messages", []),
                    "fetched_at": datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                },
            }
        finally:
            await page.close()

    async def collect_chat_group_members(
        self,
        group_keyword: str = "",
        max_groups: int = 5,
        max_members_per_group: int = 50,
        selected_groups: Optional[List[str]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> List[Dict]:
        return await self.collect_chat_group_members_v2(
            group_keyword=group_keyword,
            max_groups=max_groups,
            max_members_per_group=max_members_per_group,
            selected_groups=selected_groups,
            should_stop=should_stop,
            logger=logger,
        )

    async def collect_chat_group_members_v2(
        self,
        group_keyword: str = "",
        max_groups: int = 5,
        max_members_per_group: int = 50,
        selected_groups: Optional[List[str]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> List[Dict]:
        keyword = str(group_keyword or "").strip()
        max_groups = max(1, int(max_groups or 1))
        max_members_per_group = max(1, int(max_members_per_group or 1))
        selected_group_names = [str(item or "").strip() for item in (selected_groups or []) if str(item or "").strip()]

        async def wait_for_group_header(target_page: Page, group_name: str, timeout_ms: int = 6000) -> bool:
            deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000.0)
            while asyncio.get_event_loop().time() < deadline:
                try:
                    titles = await target_page.locator(".RightPanelHeadertitle").all_inner_texts()
                    if any(group_name in str(title or "").strip() for title in titles):
                        return True
                except Exception:
                    pass
                await target_page.wait_for_timeout(250)
            return False

        async def open_group_chat(target_page: Page, group_name: str) -> tuple[bool, str]:
            conversation = (
                target_page.locator('[data-e2e="conversation-item"].conversationConversationItemwrapper')
                .filter(has_text=group_name)
                .first
            )
            try:
                await conversation.wait_for(state="visible", timeout=5000)
            except Exception:
                return False, "群会话未出现在当前列表视口中"

            try:
                await conversation.scroll_into_view_if_needed()
            except Exception:
                pass

            click_errors = []
            for locator_getter, label in [
                (lambda: conversation.locator(".conversationConversationItemtitle").first, "group-title"),
                (lambda: conversation.locator(".conversationConversationItemrowArea2").first, "group-row"),
                (lambda: conversation, "group-card"),
            ]:
                locator = locator_getter()
                try:
                    await locator.click(timeout=4000)
                    await target_page.wait_for_timeout(1200)
                    if await wait_for_group_header(target_page, group_name, timeout_ms=3000):
                        return True, ""
                except Exception as exc:
                    click_errors.append(f"{label}:{exc}")

            clicked = await target_page.evaluate(
                r"""
                (groupName) => {
                    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                    const rows = Array.from(
                        document.querySelectorAll('[data-e2e="conversation-item"].conversationConversationItemwrapper')
                    );
                    const target = rows.find((node) => {
                        const title = normalize(
                            node.querySelector('.conversationConversationItemtitle')?.textContent || ''
                        );
                        return title === groupName;
                    }) || rows.find((node) => normalize(node.innerText || node.textContent || '').includes(groupName));
                    if (!target) return false;
                    target.scrollIntoView({ block: 'center' });
                    const inner = target.querySelector('.conversationConversationItemrowArea2')
                        || target.querySelector('.conversationConversationItemtitle')
                        || target;
                    inner.click();
                    return true;
                }
                """,
                group_name,
            )
            if clicked:
                await target_page.wait_for_timeout(1200)
                if await wait_for_group_header(target_page, group_name, timeout_ms=3000):
                    return True, ""

            return False, "; ".join(click_errors) if click_errors else "点击后右侧标题未切换到目标群聊"

        async def open_group_member_panel(target_page: Page, group_name: str) -> tuple[bool, str]:
            if "/chat" not in target_page.url:
                await target_page.goto("https://www.douyin.com/chat", wait_until="domcontentloaded", timeout=60000)
                await target_page.wait_for_timeout(3000)

            opened, open_message = await open_group_chat(target_page, group_name)
            if not opened:
                return False, f"点击群会话失败：{open_message}"

            header_click_errors = []
            header_clicked = False
            for locator, label in [
                (target_page.locator(".RightPanelHeadersummatyBox").first, "header-summary"),
                (target_page.locator(".RightPanelHeadertitleContainer").first, "header-title-wrap"),
                (target_page.locator(".RightPanelHeadertitle").first, "header-title"),
                (target_page.locator("#ei-conversation-header-info").first, "header-info"),
                (target_page.locator(".RightPanelHeaderinfoContainer").first, "header-info-wrap"),
            ]:
                try:
                    await locator.wait_for(state="visible", timeout=3000)
                    await locator.click(timeout=3000)
                    header_clicked = True
                    break
                except Exception as exc:
                    header_click_errors.append(f"{label}:{exc}")
                try:
                    await locator.click(timeout=3000, force=True)
                    header_clicked = True
                    break
                except Exception as exc:
                    header_click_errors.append(f"{label}(force):{exc}")
            if not header_clicked:
                dom_clicked = await target_page.evaluate(
                    r"""
                    (groupName) => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const title = Array.from(document.querySelectorAll('.RightPanelHeadertitle'))
                            .find((node) => normalize(node.textContent || '').includes(groupName));
                        const target = title?.closest('.RightPanelHeadersummatyBox')
                            || title?.closest('.RightPanelHeadertitleContainer')
                            || title
                            || document.querySelector('.RightPanelHeadersummatyBox')
                            || document.querySelector('.RightPanelHeadertitleContainer')
                            || document.querySelector('#ei-conversation-header-info');
                        if (!target) return false;
                        target.scrollIntoView({ block: 'center' });
                        const rect = target.getBoundingClientRect();
                        const x = rect.left + Math.min(rect.width / 2, Math.max(rect.width - 12, 12));
                        const y = rect.top + rect.height / 2;
                        for (const type of ['pointerdown', 'mousedown', 'mouseup', 'click']) {
                            target.dispatchEvent(new MouseEvent(type, {
                                bubbles: true,
                                cancelable: true,
                                composed: true,
                                clientX: x,
                                clientY: y,
                                button: 0,
                            }));
                        }
                        return true;
                    }
                    """,
                    group_name,
                )
                if dom_clicked:
                    header_clicked = True
            if not header_clicked:
                return False, f"未能打开群信息侧栏：{'; '.join(header_click_errors)}"

            try:
                await target_page.locator(".conversationConversationInfotransitionWrapper").first.wait_for(
                    state="visible",
                    timeout=5000,
                )
            except Exception:
                return False, "点击群头部后未出现群信息侧栏"

            detail_click_errors = []
            detail_opened = False
            for locator, label in [
                (target_page.locator(".GroupParticipantsoverviewcountContainer").first, "member-count"),
                (target_page.locator(".GroupParticipantsoverviewheaderArea").first, "member-header"),
                (target_page.locator(".GroupParticipantsoverviewcontainer").first, "member-overview"),
            ]:
                try:
                    await locator.wait_for(state="visible", timeout=3000)
                    await locator.click(timeout=3000)
                    detail_opened = True
                    break
                except Exception as exc:
                    detail_click_errors.append(f"{label}:{exc}")
            if not detail_opened:
                return False, f"未能展开群成员明细：{'; '.join(detail_click_errors)}"

            try:
                await target_page.wait_for_selector(".GroupParticipantsdetailparticipantsArea .detailitemitem", timeout=5000)
                return True, ""
            except Exception:
                return False, "点击成员入口后未出现群成员明细列表"

        async def collect_visible_group_members(target_page: Page) -> List[Dict]:
            return await target_page.evaluate(
                r"""
                () => {
                    const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                    const toAbsoluteUserUrl = (value) => {
                        const raw = String(value || '').trim();
                        if (!raw) return '';
                        if (raw.startsWith('http://') || raw.startsWith('https://')) return raw.split('?')[0];
                        if (raw.startsWith('//')) return `https:${raw}`.split('?')[0];
                        if (raw.startsWith('/')) return `https://www.douyin.com${raw}`.split('?')[0];
                        return '';
                    };
                    const readProfileUrl = (item) => {
                        const nodes = [item, ...Array.from(item.querySelectorAll('*'))];
                        for (const node of nodes) {
                            const href = toAbsoluteUserUrl(node?.getAttribute?.('href') || '');
                            if (href.includes('/user/')) return href;
                            const dataset = node?.dataset || {};
                            for (const key of Object.keys(dataset)) {
                                const value = toAbsoluteUserUrl(dataset[key]);
                                if (value.includes('/user/')) return value;
                            }
                            for (const attr of Array.from(node?.attributes || [])) {
                                const value = toAbsoluteUserUrl(attr?.value || '');
                                if (value.includes('/user/')) return value;
                            }
                        }
                        return '';
                    };
                    const readSecUserId = (profileUrl) => {
                        const match = String(profileUrl || '').match(/\/user\/([^/?#]+)/i);
                        return match ? normalize(match[1] || '') : '';
                    };
                    const rows = [];
                    for (const item of document.querySelectorAll('.GroupParticipantsdetailparticipantsArea .detailitemitem')) {
                        const rect = item.getBoundingClientRect();
                        if (rect.width < 20 || rect.height < 20 || rect.bottom < -10) continue;
                        const username = normalize(item.querySelector('.detailitemname')?.textContent || '');
                        if (!username || username.length > 40) continue;
                        const avatar = String(item.querySelector('img')?.src || '').split('?')[0];
                        const role = Array.from(item.querySelectorAll('.detailtagsbaseTag'))
                            .map((node) => normalize(node.textContent || ''))
                            .filter(Boolean)
                            .join('/');
                        const container = item.closest('[data-index]');
                        const domIndex = normalize(container?.getAttribute('data-index') || '');
                        const profileUrl = readProfileUrl(item);
                        rows.push({
                            username,
                            avatar,
                            role,
                            dom_index: domIndex,
                            profile_url: profileUrl,
                            sec_user_id: readSecUserId(profileUrl),
                        });
                    }
                    const unique = [];
                    const seen = new Set();
                    for (const row of rows) {
                        const key = `${row.username}|${row.avatar}`;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        unique.push(row);
                    }
                    return unique;
                }
                """
            )

        async def scroll_group_member_panel(target_page: Page) -> bool:
            return bool(
                await target_page.evaluate(
                    r"""
                    () => {
                        const area = document.querySelector('.GroupParticipantsdetailparticipantsArea');
                        if (!area) return false;
                        const candidates = [area, ...Array.from(area.querySelectorAll('*'))]
                            .filter((node) => node.scrollHeight > node.clientHeight + 80);
                        candidates.sort((a, b) => b.clientHeight - a.clientHeight);
                        const target = candidates[0];
                        if (!target) return false;
                        target.scrollTop += Math.max(target.clientHeight * 0.85, 420);
                        return true;
                    }
                    """
                )
            )

        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, "[抖音群成员] 打开消息页：https://www.douyin.com/chat")
            await page.goto("https://www.douyin.com/chat", wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)
            await self._raise_if_login_intercept(page)

            candidate_groups = await self.list_chat_groups(group_keyword=keyword, logger=None)
            if selected_group_names:
                selected_name_set = set(selected_group_names)
                candidate_groups = [
                    row for row in candidate_groups
                    if str(row.get("name", "")).strip() in selected_name_set
                ]
            candidate_groups = candidate_groups[:max_groups]

            if not candidate_groups:
                raise RuntimeError("未识别到可用群聊，请先确认消息页存在群聊，或填写群名关键词缩小范围")

            results: List[Dict] = []
            seen_group_profiles = set()

            for group in candidate_groups:
                if should_stop and should_stop():
                    self._emit(logger, "[抖音群成员] 收到停止信号，结束当前提取", "warning")
                    break

                group_name = str(group.get("name", "") or "").strip()
                if not group_name:
                    continue

                self._emit(logger, f"[抖音群成员] 准备进入群聊：{group_name}")
                panel_opened, panel_message = await open_group_member_panel(page, group_name)
                if not panel_opened:
                    self._emit(logger, f"[抖音群成员] 群 {group_name} 打开失败：{panel_message}", "warning")
                    continue

                raw_members: List[Dict] = []
                stagnation = 0
                last_count = 0
                max_scroll_rounds = max(20, min(120, (max_members_per_group * 2) + 10))
                for _ in range(max_scroll_rounds):
                    visible_members = await collect_visible_group_members(page)
                    for member in visible_members or []:
                        if isinstance(member, dict):
                            raw_members.append(member)

                    deduped_member_count = len({
                        f"{str(item.get('username', '')).strip()}|{str(item.get('avatar', '')).strip()}"
                        for item in raw_members if isinstance(item, dict)
                    })
                    if deduped_member_count >= max_members_per_group:
                        break

                    if deduped_member_count == last_count:
                        stagnation += 1
                    else:
                        stagnation = 0
                    last_count = deduped_member_count
                    if stagnation >= 3:
                        break

                    if not await scroll_group_member_panel(page):
                        break
                    await page.wait_for_timeout(900)

                unique_members: List[Dict] = []
                seen_member_keys = set()
                for member in raw_members:
                    username = str(member.get("username", "") or "").strip()
                    avatar = str(member.get("avatar", "") or "").strip()
                    if not username:
                        continue
                    key = f"{username}|{avatar}"
                    if key in seen_member_keys:
                        continue
                    seen_member_keys.add(key)
                    unique_members.append(
                        {
                            "username": username,
                            "avatar": avatar,
                            "role": str(member.get("role", "") or "").strip(),
                            "dom_index": str(member.get("dom_index", "") or "").strip(),
                            "profile_url": str(member.get("profile_url", "") or "").strip(),
                            "sec_user_id": str(member.get("sec_user_id", "") or "").strip(),
                        }
                    )
                    if len(unique_members) >= max_members_per_group:
                        break

                if not unique_members:
                    self._emit(logger, f"[抖音群成员] 群 {group_name} 未提取到成员列表", "warning")
                    continue

                self._emit(logger, f"[抖音群成员] 群 {group_name} 识别到 {len(unique_members)} 个候选成员")

                direct_profile_count = 0
                for member in unique_members:
                    if should_stop and should_stop():
                        self._emit(logger, "[抖音群成员] 收到停止信号，结束当前提取", "warning")
                        break

                    dedupe_key = (
                        f"{group_name}|{str(member.get('username', '')).strip()}|"
                        f"{str(member.get('avatar', '')).strip()}"
                    )
                    if dedupe_key in seen_group_profiles:
                        continue
                    seen_group_profiles.add(dedupe_key)

                    profile_url = str(member.get("profile_url", "") or "").strip()
                    if profile_url:
                        direct_profile_count += 1

                    results.append(
                        {
                            "group_name": group_name,
                            "group_preview": str(group.get("preview", "") or ""),
                            "username": str(member.get("username", "") or "").strip(),
                            "role": str(member.get("role", "") or "").strip(),
                            "douyin_id": "",
                            "region": "",
                            "profile_url": profile_url,
                            "sec_user_id": str(member.get("sec_user_id", "") or "").strip(),
                        }
                    )

                missing_profile_count = max(0, len(unique_members) - direct_profile_count)
                self._emit(
                    logger,
                    f"[抖音群成员] 群 {group_name} 成员列表直接解析到 {direct_profile_count} 个主页链接，"
                    f"仍有 {missing_profile_count} 人未取到主页链接",
                    "info" if missing_profile_count == 0 else "warning",
                )

            self._emit(logger, f"[抖音群成员] 共提取 {len(results)} 位群成员", "success")
            return results
        finally:
            await page.close()

    async def send_private_message(
        self,
        profile_url: str,
        message: str,
        expected_username: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        profile_url = str(profile_url or "").strip()
        message = str(message or "").strip()
        message_parts = [
            part.strip()
            for part in message.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if str(part or "").strip()
        ]
        if not profile_url:
            raise ValueError("缺少用户主页地址")
        if not message_parts:
            raise ValueError("私信内容不能为空")

        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, f"[抖音私信] 打开主页：{expected_username or profile_url}")
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            try:
                await page.wait_for_load_state("networkidle", timeout=8000)
            except Exception:
                pass
            await self._raise_if_profile_unavailable(page)
            await self._raise_if_login_intercept(page)
            await self._wait_for_profile_action_area_ready(
                page,
                logger=logger,
                log_prefix="[抖音私信]",
                timeout_ms=20000,
            )

            dialog_selector = '[data-e2e="im-dialog"], div[data-e2e="msg-input"], #messageContent'
            button_selectors = [
                'button.semi-button.semi-button-secondary:has-text("私信")',
                'button.semi-button:has-text("私信")',
                'button:has-text("私信")',
                'button.semi-button.semi-button-secondary:has(span.semi-button-content:has-text("私信"))',
                'button.semi-button:has(span.semi-button-content:has-text("私信"))',
                'button:has(> span.semi-button-content:has-text("私信"))',
            ]

            dialog_opened = False
            dialog_locator = page.locator(dialog_selector).first
            private_button_count = await page.evaluate(
                r"""
                () => Array.from(document.querySelectorAll('button.semi-button, button'))
                    .filter((node) => {
                        const text = (node.textContent || '').replace(/\s+/g, ' ').trim();
                        return text === '私信';
                    }).length
                """
            )
            self._emit(logger, f"[抖音私信] 当前页面检测到 {private_button_count} 个匹配主页私信按钮的元素")
            stable_button_candidate = await self._wait_for_private_message_button_ready(
                page,
                button_selectors,
                logger=logger,
                log_prefix="[抖音私信]",
                timeout_ms=20000,
            )
            stable_button_selector = str(stable_button_candidate.get("selector", "") or button_selectors[0])
            stable_button_index = int(stable_button_candidate.get("index", 0) or 0)
            stable_button_marker = str(stable_button_candidate.get("marker", "") or "")
            if stable_button_marker:
                for attempt in range(1, 7):
                    if attempt > 1:
                        dom_candidate = await self._mark_dom_private_message_button(page)
                        if dom_candidate.get("found") and dom_candidate.get("marker"):
                            stable_button_marker = str(dom_candidate.get("marker", "") or stable_button_marker)
                        else:
                            self._emit(
                                logger,
                                f"[抖音私信] DOM 候选按钮第 {attempt}/6 次重试前未重新定位到按钮，继续使用上一次标记：{self._format_private_button_debug(dom_candidate.get('debug'))}",
                                "warning",
                            )
                    click_method = ["normal", "force", "coordinate", "native", "force", "coordinate"][attempt - 1]
                    clicked = await self._click_marked_private_message_button(
                        page,
                        stable_button_marker,
                        logger=logger,
                        log_prefix="[抖音私信]",
                        click_method=click_method,
                    )
                    if not clicked:
                        await page.wait_for_timeout(1200)
                        continue
                    await page.wait_for_timeout(1200)
                    try:
                        await dialog_locator.wait_for(state="visible", timeout=4000)
                        dialog_opened = True
                        self._emit(logger, f"[抖音私信] 已通过 DOM 候选私信按钮打开私信面板，第 {attempt}/6 次，方式 {click_method}")
                        break
                    except Exception:
                        if attempt < 6:
                            self._emit(logger, f"[抖音私信] DOM 候选按钮第 {attempt}/6 次点击后弹层仍未出现，继续换方式重试", "warning")
                            await page.wait_for_timeout(1800)
            ordered_button_selectors = [stable_button_selector] + [
                selector for selector in button_selectors if selector != stable_button_selector
            ]
            for selector in ordered_button_selectors:
                if dialog_opened:
                    break
                try:
                    for attempt in range(1, 5):
                        candidate = await self._find_visible_private_message_button(page, selector)
                        button_index = int(candidate.get("index", -1) or -1)
                        if selector == stable_button_selector and attempt == 1 and button_index < 0:
                            button_index = stable_button_index
                        if button_index < 0:
                            if attempt >= 4:
                                raise RuntimeError(str(candidate.get("reason", "") or f"{selector} 当前没有可见按钮"))
                            await page.wait_for_timeout(1200)
                            continue
                        button = page.locator(selector).nth(button_index)
                        try:
                            await button.scroll_into_view_if_needed()
                        except Exception:
                            pass
                        self._emit(
                            logger,
                            f"[抖音私信] 尝试点击私信按钮 selector：{selector}，命中第 {button_index + 1} 个匹配元素，第 {attempt}/4 次",
                        )
                        try:
                            if attempt in {2, 4}:
                                await button.click(timeout=5000, force=True)
                            else:
                                await button.click(timeout=5000)
                        except Exception as click_exc:
                            if attempt >= 4:
                                raise click_exc
                            await page.wait_for_timeout(1200)
                            continue
                        await page.wait_for_timeout(1200)
                        try:
                            await dialog_locator.wait_for(state="visible", timeout=3200)
                            dialog_opened = True
                            self._emit(
                                logger,
                                f"[抖音私信] 已通过 selector 点击私信按钮：{selector}（命中第 {button_index + 1} 个匹配元素，第 {attempt}/4 次）",
                            )
                            break
                        except Exception:
                            if attempt < 4:
                                self._emit(
                                    logger,
                                    f"[抖音私信] 第 {attempt}/4 次点击后私信弹层仍未出现，等待后继续重试",
                                    "warning",
                                )
                                await page.wait_for_timeout(1800)
                    if dialog_opened:
                        break
                except Exception:
                    continue

            if not dialog_opened:
                self._emit(logger, "[抖音私信] 常规 selector 未拉起私信弹层，尝试 DOM 兜底点击", "warning")
                fallback_methods = ["normal", "force", "coordinate", "native", "force", "coordinate"]
                for attempt, click_method in enumerate(fallback_methods, start=1):
                    dom_candidate = await self._mark_dom_private_message_button(page)
                    marker = str(dom_candidate.get("marker", "") or "")
                    if not dom_candidate.get("found") or not marker:
                        self._emit(
                            logger,
                            f"[抖音私信] DOM 兜底第 {attempt}/{len(fallback_methods)} 次未找到可用私信按钮：{self._format_private_button_debug(dom_candidate.get('debug'))}",
                            "warning",
                        )
                        await page.wait_for_timeout(1500)
                        continue
                    clicked = await self._click_marked_private_message_button(
                        page,
                        marker,
                        logger=logger,
                        log_prefix="[抖音私信]",
                        click_method=click_method,
                    )
                    if not clicked:
                        await page.wait_for_timeout(1200)
                        continue
                    self._emit(logger, f"[抖音私信] DOM 兜底点击已触发，第 {attempt}/{len(fallback_methods)} 次，方式 {click_method}")
                    await page.wait_for_timeout(1500)
                    try:
                        await dialog_locator.wait_for(state="visible", timeout=5000)
                        dialog_opened = True
                        break
                    except Exception:
                        dialog_opened = False
                        if attempt < len(fallback_methods):
                            self._emit(logger, f"[抖音私信] DOM 兜底第 {attempt}/{len(fallback_methods)} 次后弹层仍未出现，继续换方式重试", "warning")
                            await page.wait_for_timeout(1800)

            if not dialog_opened:
                await self._raise_if_profile_unavailable(page)
                login_prompt_visible = await page.evaluate(
                    """
                    () => {
                        const text = (document.body?.innerText || '').trim();
                        return text.includes('扫码登录') || text.includes('验证码登录') || text.includes('登录后');
                    }
                    """
                )
                if login_prompt_visible:
                    raise RuntimeError("点击私信后页面仍处于登录拦截状态，请先确认该抖音账号已登录")
                raise RuntimeError("未能点击出私信窗口，请确认该主页存在可用的私信按钮")

            self._emit(logger, f"[抖音私信] 已打开私信面板：{expected_username or profile_url}")

            dialog = dialog_locator
            await dialog.wait_for(state="visible", timeout=10000)
            await page.wait_for_timeout(1800)

            input_box = page.locator(
                'div[data-e2e="msg-input"] [contenteditable="true"], .public-DraftEditor-content[contenteditable="true"]'
            ).first
            send_button = page.locator(".e2e-send-msg-btn, [class*='send-msg-btn'], span.e2e-send-msg-btn").first
            await self._wait_for_message_composer_ready(
                page,
                input_box,
                send_button,
                dialog_locator=dialog,
                logger=logger,
                log_prefix="[抖音私信]",
                timeout_ms=15000,
            )
            sent_messages = []
            total_parts = len(message_parts)
            for index, message_part in enumerate(message_parts, start=1):
                await self._wait_for_message_composer_ready(
                    page,
                    input_box,
                    send_button,
                    dialog_locator=dialog,
                    logger=logger,
                    log_prefix="[抖音私信]",
                    timeout_ms=15000,
                )
                await self._focus_message_editor(
                    page,
                    input_box,
                    send_button,
                    dialog_locator=dialog,
                    logger=logger,
                    log_prefix="[抖音私信]",
                )
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await page.keyboard.type(message_part, delay=35)
                await send_button.click(timeout=10000)
                await page.wait_for_timeout(1200)

                delivered = await page.evaluate(
                    """
                    (sentText) => {
                        const needle = String(sentText || '').trim();
                        if (!needle) return false;
                        const texts = Array.from(document.querySelectorAll('[data-e2e="msg-item-content"]'))
                            .map((node) => (node.textContent || '').trim())
                            .filter(Boolean);
                        if (texts.some((text) => text.includes(needle))) return true;
                        const editor = document.querySelector(
                            'div[data-e2e="msg-input"] [contenteditable="true"][role="textbox"]'
                        );
                        const editorText = (editor?.textContent || '').trim();
                        return editorText.length === 0;
                    }
                    """,
                    message_part,
                )
                if not delivered:
                    raise RuntimeError(f"第 {index} 条消息发送按钮已点击，但未确认消息已发出")
                sent_messages.append(message_part)
                self._emit(
                    logger,
                    f"[抖音私信] 已发送第 {index}/{total_parts} 条：{expected_username or profile_url}",
                    "success",
                )
                if index < total_parts:
                    await page.wait_for_timeout(700)

            self._emit(
                logger,
                f"[抖音私信] 已完成发送，共 {len(sent_messages)} 条：{expected_username or profile_url}",
                "success",
            )
            return {
                "success": True,
                "profile_url": profile_url,
                "username": expected_username,
                "message": message,
                "messages": sent_messages,
                "message_count": len(sent_messages),
            }
        finally:
            await page.close()

    async def send_stranger_private_message(
        self,
        row: Dict,
        message: str,
        logger: Optional[Callable[[str, str], None]] = None,
        page: Optional[Page] = None,
    ) -> Dict:
        payload = row if isinstance(row, dict) else {}
        expected_username = str(payload.get("username", "") or "").strip()
        conversation_key = str(payload.get("conversation_key", "") or "").strip()
        stranger_index = str(payload.get("stranger_index", "") or "").strip()
        preview_text = str(payload.get("preview_text", "") or "").strip()
        message = str(message or "").strip()
        message_parts = [
            part.strip()
            for part in message.replace("\r\n", "\n").replace("\r", "\n").split("\n")
            if str(part or "").strip()
        ]
        if not message_parts:
            raise ValueError("私信内容不能为空")
        if not any([conversation_key, stranger_index, expected_username, preview_text]):
            raise ValueError("缺少陌生人会话标识，无法发送")

        owns_page = page is None
        page = page or await self._new_page(logger=logger)
        try:
            async def is_stranger_panel_visible(target_page: Page) -> bool:
                try:
                    return bool(
                        await target_page.evaluate(
                            r"""
                            () => {
                                const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                                const isVisible = (node) => {
                                    if (!node) return false;
                                    const style = window.getComputedStyle(node);
                                    const rect = node.getBoundingClientRect();
                                    return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                                };
                                const legacyRoots = Array.from(document.querySelectorAll('.conversationStrangerConversationListtransition'));
                                return legacyRoots.some((root) => {
                                    const title = normalize(root.querySelector('.conversationStrangerConversationListtitle')?.textContent || '');
                                    return title.includes('陌生人消息') && isVisible(root);
                                });
                            }
                            """
                        )
                    )
                except Exception:
                    return False

            async def enter_stranger_message_panel(target_page: Page) -> tuple[bool, str]:
                if await is_stranger_panel_visible(target_page):
                    return True, ""
                clicked = await target_page.evaluate(
                    r"""
                    () => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const readText = (node) => normalize(
                            node?.innerText
                            || node?.textContent
                            || node?.getAttribute?.('aria-label')
                            || node?.getAttribute?.('title')
                            || ''
                        );
                        const isVisible = (node) => {
                            if (!node) return false;
                            const style = window.getComputedStyle(node);
                            const rect = node.getBoundingClientRect();
                            return style.display !== 'none' && style.visibility !== 'hidden' && rect.width > 0 && rect.height > 0;
                        };
                        const candidates = [];
                        const seen = new Set();
                        const pushCandidate = (node, score) => {
                            if (!node) return;
                            const clickable = node.closest('.conversationStrangerBoxrowArea2')
                                || node.closest('[class*="conversationStrangerBox"]')
                                || node;
                            const rect = clickable.getBoundingClientRect();
                            if (!isVisible(clickable) || rect.width < 40 || rect.height < 20) return;
                            const key = `${Math.round(rect.left)}|${Math.round(rect.top)}|${Math.round(rect.width)}|${Math.round(rect.height)}`;
                            if (seen.has(key)) return;
                            seen.add(key);
                            candidates.push({ node: clickable, score, text: readText(clickable) });
                        };

                        for (const node of document.querySelectorAll('.conversationStrangerBoxrowArea2')) {
                            const title = normalize(node.querySelector('.conversationStrangerBoxtitle')?.textContent || '');
                            const preview = normalize(node.querySelector('.ConversationItemHinttextBox')?.textContent || '');
                            if (title.includes('陌生人消息')) pushCandidate(node, 120 + (preview ? 5 : 0));
                        }
                        for (const node of document.querySelectorAll('[class*="conversationStrangerBox"]')) {
                            const text = readText(node);
                            if (text.includes('陌生人消息')) pushCandidate(node, 100);
                        }
                        for (const node of document.querySelectorAll('div, span, p, pre, a, button')) {
                            const text = readText(node);
                            if (text === '陌生人消息') pushCandidate(node, 160);
                        }

                        candidates.sort((a, b) => b.score - a.score);
                        const best = candidates[0];
                        if (!best) return { clicked: false, text: '' };
                        best.node.scrollIntoView({ block: 'center' });
                        best.node.click();
                        return { clicked: true, text: best.text.slice(0, 80) };
                    }
                    """
                )
                if not bool(clicked.get("clicked")):
                    return False, "未找到“陌生人消息”入口"
                self._emit(logger, f"[抖音陌生人私信] 已点击陌生人消息入口：{str(clicked.get('text', '') or '').strip() or '-'}")
                await target_page.wait_for_timeout(2200)
                for _ in range(10):
                    if await is_stranger_panel_visible(target_page):
                        return True, ""
                    await target_page.wait_for_timeout(500)
                return False, "点击后未进入陌生人消息列表"

            async def ensure_chat_page_and_panel(target_page: Page) -> None:
                current_url = str(target_page.url or "").strip()
                if "douyin.com/chat" not in current_url:
                    self._emit(logger, "[抖音陌生人私信] 打开消息页：https://www.douyin.com/chat")
                    await target_page.goto("https://www.douyin.com/chat", wait_until="domcontentloaded", timeout=60000)
                    try:
                        await target_page.wait_for_load_state("networkidle", timeout=10000)
                    except Exception:
                        pass
                    await target_page.wait_for_timeout(random.randint(5000, 9000))
                    await self._raise_if_login_intercept(target_page)

                ready, reason = await enter_stranger_message_panel(target_page)
                if not ready:
                    raise RuntimeError(reason or "陌生人消息面板不可用")

            await ensure_chat_page_and_panel(page)

            input_box = page.locator(
                'div[data-e2e="msg-input"] [contenteditable="true"], div[data-e2e="msg-input"] [role="textbox"], .public-DraftEditor-content[contenteditable="true"]'
            ).first

            async def wait_for_target_conversation_ready(target_page: Page, timeout_ms: int = 5000) -> bool:
                deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000)
                while asyncio.get_event_loop().time() < deadline:
                    title_match = False
                    try:
                        titles = await target_page.locator(".RightPanelHeadertitle").all_inner_texts()
                        title_match = any(
                            expected_username and expected_username in str(title or "").strip()
                            for title in titles
                        )
                    except Exception:
                        pass
                    if title_match:
                        try:
                            await input_box.wait_for(state="visible", timeout=500)
                            return True
                        except Exception:
                            pass
                    await target_page.wait_for_timeout(250)
                return False

            async def click_target_conversation(target_page: Page) -> tuple[bool, str]:
                root = target_page.locator(".conversationStrangerConversationListtransition").first
                try:
                    await root.wait_for(state="visible", timeout=5000)
                except Exception:
                    return False, "陌生人消息列表未出现在当前视口中"

                candidates = []
                if expected_username:
                    candidates.append(
                        (
                            root.locator('[data-e2e="conversation-item"]').filter(has_text=expected_username).first,
                            f"username:{expected_username}",
                        )
                    )
                if preview_text:
                    candidates.append(
                        (
                            root.locator('[data-e2e="conversation-item"]').filter(has_text=preview_text).first,
                            "preview-text",
                        )
                    )
                if stranger_index:
                    candidates.append(
                        (
                            root.locator(f'[data-index="{stranger_index}"] [data-e2e="conversation-item"]').first,
                            f"index:{stranger_index}",
                        )
                    )
                if conversation_key:
                    candidates.append(
                        (
                            root.locator('[data-e2e="conversation-item"]').filter(has_text=conversation_key).first,
                            "conversation-key",
                        )
                    )

                click_errors = []
                for conversation, label in candidates:
                    try:
                        await conversation.wait_for(state="visible", timeout=3000)
                    except Exception:
                        click_errors.append(f"{label}:not-visible")
                        continue

                    try:
                        await conversation.scroll_into_view_if_needed()
                    except Exception:
                        pass

                    for locator_getter, click_label in [
                        (lambda c=conversation: c.locator(".conversationConversationItemtitle").first, "title"),
                        (lambda c=conversation: c.locator(".conversationConversationItemrowArea2").first, "row"),
                        (lambda c=conversation: c.locator(".conversationConversationItemrowArea1").first, "avatar"),
                        (lambda c=conversation: c, "card"),
                    ]:
                        locator = locator_getter()
                        try:
                            await locator.click(timeout=3000, force=True)
                            await target_page.wait_for_timeout(1200)
                            if await wait_for_target_conversation_ready(target_page, timeout_ms=3000):
                                self._emit(
                                    logger,
                                    f"[抖音陌生人私信] 已打开目标会话：{expected_username or label or '-'}（{label}/{click_label}）",
                                )
                                return True, ""
                        except Exception as exc:
                            click_errors.append(f"{label}/{click_label}:{exc}")

                clicked = await target_page.evaluate(
                    r"""
                    (payload) => {
                        const normalize = (value) => String(value || '').replace(/\s+/g, ' ').trim();
                        const rows = Array.from(document.querySelectorAll('.conversationStrangerConversationListtransition [data-e2e="conversation-item"]'));
                        const target = rows.find((node) => {
                            const title = normalize(node.querySelector('.conversationConversationItemtitle')?.textContent || '');
                            const hint = normalize(node.querySelector('.ConversationItemHinttextBox')?.textContent || '');
                            const indexText = normalize(node.closest('[data-index]')?.getAttribute('data-index') || '');
                            if (payload.index && indexText === payload.index) return true;
                            if (payload.username && title === payload.username) return true;
                            if (payload.preview && hint === payload.preview) return true;
                            return false;
                        });
                        if (!target) return false;
                        target.scrollIntoView({ block: 'center' });
                        target.dispatchEvent(new MouseEvent('mousedown', { bubbles: true }));
                        target.dispatchEvent(new MouseEvent('mouseup', { bubbles: true }));
                        target.dispatchEvent(new MouseEvent('click', { bubbles: true }));
                        return true;
                    }
                    """,
                    {
                        "index": stranger_index,
                        "username": expected_username,
                        "preview": preview_text,
                    },
                )
                if clicked:
                    await target_page.wait_for_timeout(1200)
                    if await wait_for_target_conversation_ready(target_page, timeout_ms=3000):
                        self._emit(logger, f"[抖音陌生人私信] 已打开目标会话：{expected_username or stranger_index or '-'}（dom-fallback）")
                        return True, ""

                return False, "; ".join(click_errors) if click_errors else "点击后右侧会话未切换"

            opened = False
            for attempt in range(1, 4):
                clicked, reason = await click_target_conversation(page)
                if not clicked:
                    raise RuntimeError(reason or "未找到目标会话")
                try:
                    await input_box.wait_for(state="visible", timeout=5000)
                    opened = True
                    break
                except Exception:
                    self._emit(
                        logger,
                        f"[抖音陌生人私信] 第 {attempt} 次点击会话后右侧输入框仍未出现，准备重试",
                        "warning",
                    )
                    await page.wait_for_timeout(1200)
            if not opened:
                raise RuntimeError("已点击陌生人会话，但右侧发送输入框未出现")

            send_button = page.locator(".e2e-send-msg-btn, [class*='send-msg-btn'], span.e2e-send-msg-btn").first
            await send_button.wait_for(state="visible", timeout=15000)

            sent_messages = []
            total_parts = len(message_parts)
            for index, message_part in enumerate(message_parts, start=1):
                await input_box.click()
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await page.keyboard.type(message_part, delay=35)
                await send_button.click(timeout=10000)
                await page.wait_for_timeout(1200)

                delivered = await page.evaluate(
                    """
                    (sentText) => {
                        const needle = String(sentText || '').trim();
                        if (!needle) return false;
                        const texts = Array.from(document.querySelectorAll('[data-e2e="msg-item-content"]'))
                            .map((node) => (node.textContent || '').trim())
                            .filter(Boolean);
                        if (texts.some((text) => text.includes(needle))) return true;

                        const editor = document.querySelector(
                            'div[data-e2e="msg-input"] [contenteditable="true"][role="textbox"]'
                        );
                        const editorText = (editor?.textContent || '').trim();
                        return editorText.length === 0;
                    }
                    """,
                    message_part,
                )
                if not delivered:
                    raise RuntimeError(f"第 {index} 条消息发送按钮已点击，但未确认消息已发出")
                sent_messages.append(message_part)
                self._emit(
                    logger,
                    f"[抖音陌生人私信] 已发送第 {index}/{total_parts} 条：{expected_username or conversation_key or '-'}",
                    "success",
                )
                if index < total_parts:
                    await page.wait_for_timeout(700)

            self._emit(
                logger,
                f"[抖音陌生人私信] 已完成发送，共 {len(sent_messages)} 条：{expected_username or conversation_key or '-'}",
                "success",
            )
            return {
                "success": True,
                "conversation_key": conversation_key,
                "username": expected_username,
                "message": message,
                "messages": sent_messages,
                "message_count": len(sent_messages),
            }
        finally:
            if owns_page:
                await page.close()

    async def follow_user_and_find_first_post(
        self,
        profile_url: str,
        expected_username: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        profile_url = str(profile_url or "").strip()
        if not profile_url:
            raise ValueError("缺少用户主页地址")

        page = await self._new_page(logger=logger)
        video_url = ""
        follow_clicked = False
        already_following = False
        try:
            self._emit(logger, f"[抖音关注评论] 打开主页：{expected_username or profile_url}")
            await page.goto(profile_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4500)
            await self._raise_if_login_intercept(page)

            follow_state = await page.evaluate(
                """
                () => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, '').trim();
                    const isVisible = (node) => {
                        if (!node || typeof node.getBoundingClientRect !== 'function') return false;
                        const style = window.getComputedStyle(node);
                        if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            return false;
                        }
                        const rect = node.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const nodes = Array.from(document.querySelectorAll('button, [role="button"]')).filter(isVisible);
                    const readText = (node) => normalize(node.innerText || node.textContent || '');
                    const followNode = nodes.find((node) => {
                        const text = readText(node);
                        if (!text) return false;
                        if (text.includes('已关注') || text.includes('互相关注')) return false;
                        return text === '关注' || text === '回关' || text.endsWith('关注');
                    });
                    if (followNode) {
                        const label = readText(followNode);
                        followNode.click();
                        return { action: 'clicked', label };
                    }
                    const alreadyNode = nodes.find((node) => {
                        const text = readText(node);
                        return text.includes('已关注') || text.includes('互相关注');
                    });
                    if (alreadyNode) {
                        return { action: 'already_following', label: readText(alreadyNode) };
                    }
                    return { action: 'not_found', label: '' };
                }
                """
            )
            follow_action = str((follow_state or {}).get("action", "") or "").strip()
            follow_label = str((follow_state or {}).get("label", "") or "").strip()
            if follow_action == "clicked":
                follow_clicked = True
                self._emit(logger, f"[抖音关注评论] 已点击关注按钮：{follow_label or '关注'}", "success")
                await page.wait_for_timeout(1800)
            elif follow_action == "already_following":
                already_following = True
                self._emit(logger, "[抖音关注评论] 当前账号已处于关注状态", "info")
            else:
                raise RuntimeError("未找到可点击的关注按钮，请确认该主页允许关注")

            work_state = await page.evaluate(
                """
                () => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const normalizeHref = (href) => String(href || '').split('?')[0];
                    const isVisible = (node) => {
                        if (!node || typeof node.getBoundingClientRect !== 'function') return false;
                        const style = window.getComputedStyle(node);
                        if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                            return false;
                        }
                        const rect = node.getBoundingClientRect();
                        return rect.width > 0 && rect.height > 0;
                    };
                    const emptyTexts = ['暂无内容', '该账号还未发布过作品哦', '该账号还未发布过作品哦～'];
                    const emptyDetected = Array.from(document.querySelectorAll('div, p, span'))
                        .some((node) => {
                            if (!isVisible(node)) return false;
                            const text = normalize(node.textContent || '');
                            return !!text && emptyTexts.some((item) => text.includes(item));
                        });
                    const anchors = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'));
                    const rows = [];
                    const seen = new Set();
                    for (const anchor of anchors) {
                        if (!isVisible(anchor)) continue;
                        const href = normalizeHref(anchor.href || '');
                        if (!/\\/(video|note)\\/\\d+/i.test(href)) continue;
                        if (anchor.closest('footer, contentinfo, article')) continue;
                        const item = anchor.closest('li, [role="listitem"], listitem');
                        if (!item || !isVisible(item)) continue;
                        if (item.closest('footer, contentinfo, article')) continue;
                        const key = href;
                        if (seen.has(key)) continue;
                        seen.add(key);
                        rows.push({
                            href,
                            text: normalize(item.textContent || anchor.textContent || ''),
                        });
                    }
                    return {
                        emptyDetected,
                        firstWorkUrl: rows[0]?.href || '',
                        works: rows.slice(0, 10),
                    };
                }
                """
            )
            if not (work_state or {}).get("firstWorkUrl"):
                for _ in range(4):
                    await page.wait_for_timeout(700)
                    work_state = await page.evaluate(
                        """
                        () => {
                            const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                            const normalizeHref = (href) => String(href || '').split('?')[0];
                            const isVisible = (node) => {
                                if (!node || typeof node.getBoundingClientRect !== 'function') return false;
                                const style = window.getComputedStyle(node);
                                if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                                    return false;
                                }
                                const rect = node.getBoundingClientRect();
                                return rect.width > 0 && rect.height > 0;
                            };
                            const anchors = Array.from(document.querySelectorAll('a[href*="/video/"], a[href*="/note/"]'));
                            for (const anchor of anchors) {
                                if (!isVisible(anchor)) continue;
                                const href = normalizeHref(anchor.href || '');
                                if (!/\\/(video|note)\\/\\d+/i.test(href)) continue;
                                if (anchor.closest('footer, contentinfo, article')) continue;
                                const item = anchor.closest('li, [role="listitem"], listitem');
                                if (!item || !isVisible(item)) continue;
                                if (item.closest('footer, contentinfo, article')) continue;
                                return { firstWorkUrl: href };
                            }
                            return { firstWorkUrl: '' };
                        }
                        """
                    )
                    if (work_state or {}).get("firstWorkUrl"):
                        break
            video_url = str((work_state or {}).get("firstWorkUrl", "") or "").strip()
        finally:
            await page.close()

        if not video_url:
            summary = "已关注，主页无作品，已跳过评论" if follow_clicked else "已是关注状态，主页无作品，已跳过评论"
            self._emit(logger, f"[抖音关注评论] {expected_username or profile_url} 无可评论作品，已跳过评论", "warning")
            return {
                "success": True,
                "profile_url": profile_url,
                "username": expected_username,
                "followed": follow_clicked,
                "already_following": already_following,
                "has_posts": False,
                "commented": False,
                "video_url": "",
                "summary": summary,
            }
        return {
            "success": True,
            "profile_url": profile_url,
            "username": expected_username,
            "followed": follow_clicked,
            "already_following": already_following,
            "has_posts": True,
            "commented": False,
            "video_url": video_url,
            "summary": "已关注并找到首个作品" if follow_clicked else "已是关注状态，并找到首个作品",
        }

    async def follow_user_and_comment_first_post(
        self,
        profile_url: str,
        comment_text: str,
        expected_username: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        profile_url = str(profile_url or "").strip()
        comment_text = str(comment_text or "").strip()
        if not profile_url:
            raise ValueError("缺少用户主页地址")
        if not comment_text:
            raise ValueError("评论内容不能为空")

        profile_result = await self.follow_user_and_find_first_post(
            profile_url,
            expected_username=expected_username,
            logger=logger,
        )
        video_url = str(profile_result.get("video_url", "") or "").strip()
        if not video_url:
            return profile_result

        await self.send_video_comment(
            video_url,
            comment_text,
            expected_title=f"{expected_username or '该用户'}的首个作品",
            logger=logger,
        )
        summary = "已关注并完成首作品评论" if profile_result.get("followed") else "已是关注状态，并完成首作品评论"
        self._emit(logger, f"[抖音关注评论] 完成：{expected_username or profile_url}，作品 {video_url}", "success")
        return {
            **profile_result,
            "success": True,
            "profile_url": profile_url,
            "username": expected_username,
            "has_posts": True,
            "commented": True,
            "video_url": video_url,
            "summary": summary,
        }

    async def send_video_comment(
        self,
        video_url: str,
        comment_text: str,
        expected_title: str = "",
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> Dict:
        video_url = str(video_url or "").strip()
        comment_text = str(comment_text or "").strip()
        if not video_url:
            raise ValueError("缺少作品地址")
        if not comment_text:
            raise ValueError("评论内容不能为空")

        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, f"[抖音视频评论] 打开视频：{expected_title or video_url}")
            await page.goto(video_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            await self._raise_if_login_intercept(page)
            await self._like_current_work_if_needed(page, logger=logger)

            await self._ensure_comment_panel_ready(page, logger=logger)
            input_locator = await self._ensure_comment_input_ready(page, logger=logger)
            await self._focus_comment_editor(page, input_locator, logger=logger)
            input_locator = await self._ensure_comment_input_ready(page, logger=logger)

            editor_meta = {"tag": "", "isContentEditable": False}
            try:
                editor_meta = await input_locator.evaluate(
                    """
                    (el) => ({
                        tag: String(el?.tagName || '').toLowerCase(),
                        isContentEditable: !!el?.isContentEditable,
                    })
                    """
                )
            except Exception:
                editor_meta = {"tag": "", "isContentEditable": False}

            tag_name = str((editor_meta or {}).get("tag", "") or "").lower()
            is_contenteditable = bool((editor_meta or {}).get("isContentEditable"))

            if tag_name in {"input", "textarea"} and not is_contenteditable:
                try:
                    await input_locator.fill("", timeout=5000)
                except Exception:
                    pass
                await input_locator.type(comment_text, delay=35, timeout=10000)
            else:
                await input_locator.click(timeout=5000, force=True)
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")
                await page.keyboard.type(comment_text, delay=35)

            self._emit(logger, f"[抖音视频评论] 已输入评论内容：{comment_text[:30]}")
            await self._submit_comment_and_confirm(
                page,
                input_locator,
                [comment_text],
                logger=logger,
                action_label="视频评论",
            )

            self._emit(logger, f"[抖音视频评论] 已发送：{expected_title or video_url}", "success")
            return {
                "success": True,
                "video_url": video_url,
                "comment_text": comment_text,
                "title": expected_title,
            }
        finally:
            await page.close()

    async def _pick_visible_mention_candidate(
        self,
        page: Page,
        expected_username: str,
        logger: Optional[Callable[[str, str], None]] = None,
        timeout_ms: int = 6000,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> str:
        expected_username = str(expected_username or "").strip()
        deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000.0)
        candidate_payload: List[Dict] = []
        fallback_payload: List[Dict] = []
        last_state: Dict[str, object] = {
            "root_visible": False,
            "root_count": 0,
            "row_count": 0,
            "labels": [],
            "editor_text": "",
            "candidate_debug": [],
            "root_htmls": [],
            "chosen_root_index": -1,
        }

        async def read_locator_candidates() -> Dict[str, object]:
            root_selector = '[class*="atBox-inner-container"], [class*="atBox-inner"]'
            root_locator = page.locator(root_selector)
            root_total = await root_locator.count()
            visible_root_count = 0
            best_root_index = -1
            best_root_row_count = 0
            best_root_rows: List[Dict] = []
            root_htmls: List[str] = []

            for root_index in range(min(root_total, 8)):
                root = root_locator.nth(root_index)
                try:
                    root_visible = await root.is_visible()
                except Exception:
                    root_visible = False
                if not root_visible:
                    continue
                visible_root_count += 1
                try:
                    root_info = await root.evaluate(
                        """
                        (node) => {
                            const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                            const isVisible = (el) => {
                                if (!el || typeof el.getBoundingClientRect !== 'function') return false;
                                const style = window.getComputedStyle(el);
                                if (!style || style.display === 'none' || style.visibility === 'hidden' || style.opacity === '0') {
                                    return false;
                                }
                                const rect = el.getBoundingClientRect();
                                return rect.width > 0 && rect.height > 0;
                            };
                            const directChildren = Array.from(node.children || []).filter((child) => child.tagName === 'DIV');
                            const childRows = directChildren.map((child, index) => {
                                const childVisible = isVisible(child) || Array.from(child.querySelectorAll('*')).some(isVisible);
                                const labelNode = child.querySelector('.eRbIZXG4, [class*="eRbIZXG4"]');
                                const label = normalize(labelNode?.textContent || '');
                                const text = normalize(child.innerText || child.textContent || '');
                                return {
                                    index,
                                    id: String(child.id || '').trim(),
                                    label: label || text,
                                    text,
                                    html: String(child.outerHTML || ''),
                                    visible: childVisible,
                                };
                            });
                            return {
                                html: String(node.outerHTML || ''),
                                rows: childRows,
                            };
                        }
                        """
                    )
                except Exception:
                    root_info = {
                        "html": "",
                        "rows": [],
                    }
                root_info = root_info if isinstance(root_info, dict) else {"html": "", "rows": []}
                root_htmls.append(str(root_info.get("html", "") or ""))
                current_rows = [
                    row for row in (root_info.get("rows", []) or [])
                    if isinstance(row, dict) and row.get("visible") and str(row.get("label", "") or "").strip()
                ]
                if len(current_rows) > best_root_row_count:
                    best_root_row_count = len(current_rows)
                    best_root_index = root_index
                    best_root_rows = current_rows

            candidates = best_root_rows
            for row in candidates:
                label = str(row.get("label", "") or "").strip()
                row["exact"] = bool(expected_username) and label == expected_username
                row["prefix"] = bool(expected_username) and label.startswith(expected_username)
                row["root_index"] = best_root_index

            editor_snapshot = await self._read_comment_submission_snapshot(page)
            editor_text = str(editor_snapshot.get("editor_text", "") or "").strip()

            return {
                "root_visible": visible_root_count > 0,
                "root_count": visible_root_count,
                "row_count": len(candidates),
                "labels": [str(row.get("label", "") or "").strip() for row in candidates[:8]],
                "editor_text": editor_text,
                "candidates": candidates,
                "candidate_debug": candidates[:20],
                "root_htmls": root_htmls,
                "chosen_root_index": best_root_index,
            }

        while asyncio.get_event_loop().time() < deadline:
            self._raise_if_should_stop(should_stop, logger=logger)
            try:
                snapshot = await read_locator_candidates()
                if isinstance(snapshot, dict):
                    last_state = snapshot
                    candidate_payload = snapshot.get("candidates", []) or []
                else:
                    candidate_payload = []
            except Exception:
                candidate_payload = []

            if candidate_payload:
                if any(row.get("exact") or row.get("prefix") for row in candidate_payload):
                    break
                fallback_payload = candidate_payload
            await page.wait_for_timeout(180)
        if not candidate_payload and fallback_payload:
            candidate_payload = fallback_payload
        elif candidate_payload and not any(row.get("exact") or row.get("prefix") for row in candidate_payload) and fallback_payload:
            candidate_payload = fallback_payload
        if not candidate_payload and fallback_payload:
            candidate_payload = fallback_payload
        if not candidate_payload:
            labels = [str(item or "").strip() for item in (last_state.get("labels", []) or []) if str(item or "").strip()]
            editor_text = str(last_state.get("editor_text", "") or "").strip()
            candidate_debug = last_state.get("candidate_debug", []) or []
            self._emit(
                logger,
                f"[抖音评论@客户] 候选子div详情：{candidate_debug if candidate_debug else '[]'}",
                "warning",
            )
            for root_index, root_html in enumerate(last_state.get("root_htmls", []) or []):
                if not str(root_html or "").strip():
                    continue
                self._emit(
                    logger,
                    f"[抖音评论@客户] 候选容器完整HTML #{root_index}：{str(root_html or '').strip()}",
                    "warning",
                )
            self._emit(
                logger,
                f"[抖音评论@客户] 候选诊断：输入 @{expected_username} 后，候选容器{'已出现' if last_state.get('root_visible') else '未出现'}，"
                f"可见容器 {int(last_state.get('root_count', 0) or 0)} 个，候选 {int(last_state.get('row_count', 0) or 0)} 个，"
                f"候选样本={labels if labels else '[]'}，编辑框内容={editor_text or '空'}",
                "warning",
            )
            raise RuntimeError(f"输入 @{expected_username} 后没有出现可点击的候选用户")

        exact_or_prefix = next(
            (row for row in candidate_payload if row.get("exact") or row.get("prefix")),
            None,
        )
        chosen = exact_or_prefix or candidate_payload[0]
        if not exact_or_prefix:
            labels = [str(item.get("label", "") or "").strip() for item in candidate_payload[:8] if str(item.get("label", "") or "").strip()]
            self._emit(
                logger,
                f"[抖音评论@客户] 等待 {int(timeout_ms)}ms 后仍未出现匹配 @{expected_username} 的候选，按页面首项直接点击。当前候选={labels if labels else '[]'}",
                "warning",
            )
        elif not chosen.get("exact"):
            labels = [str(item.get("label", "") or "").strip() for item in candidate_payload[:8] if str(item.get("label", "") or "").strip()]
            self._emit(
                logger,
                f"[抖音评论@客户] 未找到完全同名候选，已命中前缀匹配 @{expected_username}。当前候选={labels if labels else '[]'}",
                "info",
            )

        clicked_label = ""
        target_id = str(chosen.get("id", "") or "").strip()
        target_index = max(0, int(chosen.get("index", 0) or 0))
        root_index = max(0, int(chosen.get("root_index", last_state.get("chosen_root_index", 0)) or 0))
        root_locator = page.locator('[class*="atBox-inner-container"], [class*="atBox-inner"]').nth(root_index)
        try:
            clicked_label = await root_locator.evaluate(
                """
                (node, payload) => {
                    const normalize = (value) => String(value || '').replace(/\\s+/g, ' ').trim();
                    const directChildren = Array.from(node.children || []).filter((child) => child.tagName === 'DIV');
                    const target = directChildren.find((child) => String(child.id || '').trim() === String(payload.targetId || '').trim())
                        || directChildren[Number(payload.targetIndex) || 0];
                    if (!target) return '';
                    const clickable = target.querySelector('.OToQiXBr') || target.querySelector('.zJiGLhJ6') || target;
                    clickable.dispatchEvent(new MouseEvent('mousedown', { bubbles: true, cancelable: true, composed: true, button: 0 }));
                    clickable.dispatchEvent(new MouseEvent('mouseup', { bubbles: true, cancelable: true, composed: true, button: 0 }));
                    clickable.dispatchEvent(new MouseEvent('click', { bubbles: true, cancelable: true, composed: true, button: 0 }));
                    const labelNode = target.querySelector('.eRbIZXG4, [class*="eRbIZXG4"]');
                    return normalize(labelNode?.textContent || target.innerText || target.textContent || '');
                }
                """,
                {"targetId": target_id, "targetIndex": target_index},
            )
            await page.wait_for_timeout(240)
        except Exception:
            try:
                fallback_locator = root_locator.locator(":scope > div").nth(target_index)
                await fallback_locator.click(timeout=5000, force=True)
                await page.wait_for_timeout(240)
                clicked_label = str(chosen.get("label", "") or "").strip()
            except Exception:
                clicked_label = ""
        clicked_label = str(clicked_label or chosen.get("label", "") or "").strip()
        if not clicked_label:
            raise RuntimeError(f"候选用户 @{expected_username} 已识别，但点击失败")

        self._emit(logger, f"[抖音评论@客户] 已选择候选用户：{clicked_label}", "info")
        return clicked_label

    async def _wait_for_mention_commit(
        self,
        page: Page,
        before_editor_text: str,
        expected_username: str,
        selected_label: str,
        logger: Optional[Callable[[str, str], None]] = None,
        timeout_ms: int = 5000,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> str:
        before_text = re.sub(r"\s+", " ", str(before_editor_text or "")).strip()
        expected_name = re.sub(r"\s+", " ", str(expected_username or "")).strip()
        selected_name = re.sub(r"\s+", " ", str(selected_label or "")).strip()
        deadline = asyncio.get_event_loop().time() + (timeout_ms / 1000.0)
        last_editor_text = ""
        last_suggestion_visible = False

        while asyncio.get_event_loop().time() < deadline:
            self._raise_if_should_stop(should_stop, logger=logger)
            snapshot = await self._read_comment_submission_snapshot(page)
            editor_text = re.sub(r"\s+", " ", str(snapshot.get("editor_text", "") or "")).strip()
            suggestion_visible = bool(snapshot.get("suggestion_visible"))
            last_editor_text = editor_text
            last_suggestion_visible = suggestion_visible

            label_inserted = bool(selected_name) and f"@{selected_name}" in editor_text
            text_changed = editor_text != before_text
            search_query_lingering = bool(expected_name) and f"@{expected_name}" in editor_text and not label_inserted
            if label_inserted and text_changed and not suggestion_visible and not search_query_lingering:
                self._emit(
                    logger,
                    f"[抖音评论@客户] 已确认 @{selected_name} 写入评论框，继续处理下一个用户",
                    "info",
                )
                return editor_text
            await page.wait_for_timeout(180)

        self._emit(
            logger,
            f"[抖音评论@客户] 候选点击后未稳定写入评论框：期望=@{expected_name or selected_name}，"
            f"实际编辑框={last_editor_text or '空'}，候选框{'仍可见' if last_suggestion_visible else '已隐藏'}",
            "warning",
        )
        raise RuntimeError(f"选择候选 @{selected_name or expected_name} 后，评论框未完成插入")

    async def _rollback_failed_mention_input(
        self,
        page: Page,
        input_locator,
        failed_username: str,
        logger: Optional[Callable[[str, str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> None:
        expected_name = re.sub(r"\s+", " ", str(failed_username or "")).strip()
        for attempt in range(2):
            self._raise_if_should_stop(should_stop, logger=logger)
            try:
                await input_locator.click(timeout=3000, force=True)
            except Exception:
                pass
            try:
                await page.keyboard.press("Control+Z")
            except Exception:
                return
            await page.wait_for_timeout(220)
            snapshot = await self._read_comment_submission_snapshot(page)
            editor_text = re.sub(r"\s+", " ", str(snapshot.get("editor_text", "") or "")).strip()
            if not expected_name or f"@{expected_name}" not in editor_text:
                self._emit(
                    logger,
                    f"[抖音评论@客户] 已回滚失败输入 @{expected_name or failed_username}，继续后续客户",
                    "info",
                )
                return
        self._emit(
            logger,
            f"[抖音评论@客户] 回滚失败输入 @{expected_name or failed_username} 未完全确认，继续尝试后续客户",
            "warning",
        )

    async def send_video_comment_mentions(
        self,
        video_url: str,
        mention_usernames: List[str],
        expected_title: str = "",
        max_mentions: int = 50,
        logger: Optional[Callable[[str, str], None]] = None,
        should_stop: Optional[Callable[[], bool]] = None,
    ) -> Dict:
        video_url = str(video_url or "").strip()
        if not video_url:
            raise ValueError("缺少作品地址")

        deduped_names: List[str] = []
        seen_names = set()
        for raw_name in mention_usernames or []:
            username = str(raw_name or "").strip()
            normalized = re.sub(r"\s+", " ", username)
            if not normalized or normalized in seen_names:
                continue
            seen_names.add(normalized)
            deduped_names.append(normalized)
            if len(deduped_names) >= max(1, min(int(max_mentions or 50), 50)):
                break

        if not deduped_names:
            raise ValueError("至少需要一个可用的精准客户昵称")

        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, f"[抖音评论@客户] 打开视频：{expected_title or video_url}")
            await page.goto(video_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(4000)
            await self._raise_if_login_intercept(page)

            await self._ensure_comment_panel_ready(page, logger=logger)
            input_locator = await self._ensure_comment_input_ready(page, logger=logger)
            await self._focus_comment_editor(page, input_locator, logger=logger)
            input_locator = await self._ensure_comment_input_ready(page, logger=logger)

            editor_meta = {"tag": "", "isContentEditable": False}
            try:
                editor_meta = await input_locator.evaluate(
                    """
                    (el) => ({
                        tag: String(el?.tagName || '').toLowerCase(),
                        isContentEditable: !!el?.isContentEditable,
                    })
                    """
                )
            except Exception:
                editor_meta = {"tag": "", "isContentEditable": False}

            tag_name = str((editor_meta or {}).get("tag", "") or "").lower()
            is_contenteditable = bool((editor_meta or {}).get("isContentEditable"))

            if tag_name in {"input", "textarea"} and not is_contenteditable:
                try:
                    await input_locator.fill("", timeout=5000)
                except Exception:
                    pass
            else:
                await input_locator.click(timeout=5000, force=True)
                await page.keyboard.press("Control+A")
                await page.keyboard.press("Backspace")

            selected_mentions: List[str] = []
            successful_mentions: List[Dict[str, str]] = []
            failed_mentions: List[Dict[str, str]] = []
            for username in deduped_names:
                self._raise_if_should_stop(should_stop, logger=logger)
                try:
                    before_snapshot = await self._read_comment_submission_snapshot(page)
                    before_editor_text = str(before_snapshot.get("editor_text", "") or "")
                    await input_locator.click(timeout=5000, force=True)
                    await self._move_comment_caret_to_end(input_locator, logger=logger)
                    await page.keyboard.type(f"@{username}", delay=55)
                    self._emit(logger, f"[抖音评论@客户] 已输入 @{username}，等待候选列表出现", "info")
                    await page.wait_for_timeout(1000)
                    selected_label = await self._pick_visible_mention_candidate(
                        page,
                        username,
                        logger=logger,
                        should_stop=should_stop,
                    )
                    await self._wait_for_mention_commit(
                        page,
                        before_editor_text=before_editor_text,
                        expected_username=username,
                        selected_label=selected_label,
                        logger=logger,
                        should_stop=should_stop,
                    )
                    selected_label = str(selected_label or username).strip() or username
                    selected_mentions.append(selected_label)
                    await self._move_comment_caret_to_end(input_locator, logger=logger)
                    successful_mentions.append(
                        {
                            "requested_username": username,
                            "selected_label": selected_label,
                        }
                    )
                    await page.keyboard.press("Space")
                    await page.wait_for_timeout(160)
                    await self._move_comment_caret_to_end(input_locator, logger=logger)
                except DouyinMentionCommentStopped:
                    raise
                except Exception as exc:
                    failed_mentions.append(
                        {
                            "requested_username": username,
                            "error": str(exc),
                        }
                    )
                    self._emit(
                        logger,
                        f"[抖音评论@客户] 已跳过 @{username}：{exc}",
                        "warning",
                    )
                    await self._rollback_failed_mention_input(
                        page,
                        input_locator,
                        username,
                        logger=logger,
                        should_stop=should_stop,
                    )
                    continue

            if not selected_mentions:
                failure_summary = "；".join(
                    f"@{item.get('requested_username', '')}：{item.get('error', '')}"
                    for item in failed_mentions[:5]
                    if str(item.get("requested_username", "") or "").strip()
                ).strip()
                raise RuntimeError(f"没有可发送的 @ 用户，失败明细：{failure_summary or '全部候选失败'}")

            composed_text = " ".join(f"@{label}" for label in selected_mentions).strip()
            self._emit(
                logger,
                f"[抖音评论@客户] 已整理成功 {len(selected_mentions)} 个 @ 用户"
                + (f"，跳过 {len(failed_mentions)} 个失败用户" if failed_mentions else "")
                + f"：{composed_text[:80]}",
                "info",
            )
            expected_tokens = [f"@{label}" for label in selected_mentions if str(label or "").strip()]
            await self._submit_comment_and_confirm(
                page,
                input_locator,
                expected_tokens or [composed_text],
                logger=logger,
                action_label="评论@客户",
            )

            self._emit(
                logger,
                f"[抖音评论@客户] 已发送：{expected_title or video_url}，本次 @ {len(selected_mentions)} 人",
                "success",
            )
            return {
                "success": True,
                "video_url": video_url,
                "title": expected_title,
                "selected_mentions": selected_mentions,
                "successful_mentions": successful_mentions,
                "failed_mentions": failed_mentions,
                "comment_text": composed_text,
            }
        finally:
            await page.close()

    async def scrape_search_results(
        self,
        keyword: str,
        max_results: int = 50,
        logger: Optional[Callable[[str, str], None]] = None,
    ) -> List[Dict]:
        page = await self._new_page(logger=logger)
        try:
            url = f"https://www.douyin.com/search/{quote(keyword)}?type=video"
            self._emit(logger, f"[抖音搜索] 打开搜索页：{keyword}")
            await page.goto(url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(3000)
            await page.wait_for_selector('a[href*="/video/"]', timeout=30000)

            max_scroll_rounds = 18
            stable_rounds = 0
            last_count = 0

            for round_index in range(max_scroll_rounds):
                results = await page.evaluate(
                    """
                    () => {
                        const isDuration = (text) => /^\\d{1,2}:\\d{2}(?::\\d{2})?$/.test(text);
                        const isCountText = (text) => /^(\\d+(?:\\.\\d+)?[涓囧崈]?|\\d+)$/.test(text.replace(/,/g, '').replace(/\\+/g, ''));
                        const cards = Array.from(document.querySelectorAll('.search-result-card'));
                        const rows = [];
                        for (const card of cards) {
                            const link = card.querySelector('a[href*="/video/"]');
                            if (!link || !link.href) continue;
                            const text = (link.innerText || card.innerText || '').trim();
                            if (!text) continue;
                            const lines = text.split(/\\n+/).map((line) => line.trim()).filter(Boolean);
                            const authorIndex = lines.findIndex((line) => line.startsWith('@'));
                            if (authorIndex <= 0) continue;

                            const title = lines[authorIndex - 1] || '';
                            const author = (lines[authorIndex] || '').replace(/^@+/, '').trim();
                            const publishTime = lines[authorIndex + 1] || '';

                            const countTexts = [];
                            let likeText = '';
                            let commentText = '';
                            let duration = '';
                            for (let i = authorIndex - 2; i >= 0; i--) {
                                const line = lines[i];
                                if (isCountText(line)) {
                                    countTexts.push(line);
                                    if (!likeText) {
                                        likeText = line;
                                        continue;
                                    }
                                    if (!commentText) {
                                        commentText = line;
                                        continue;
                                    }
                                    continue;
                                }
                                if (!duration && isDuration(line)) {
                                    duration = line;
                                }
                            }

                            rows.push({
                                url: link.href,
                                title,
                                author,
                                likes_text: likeText,
                                comments_text: commentText,
                                count_texts: countTexts,
                                publish_time: publishTime,
                                duration,
                            });
                        }
                        return rows;
                    }
                    """
                )

                if len(results) >= max_results:
                    break

                if len(results) == last_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                    last_count = len(results)

                if stable_rounds >= 4:
                    break

                await page.evaluate("window.scrollBy(0, Math.max(window.innerHeight * 0.9, 900));")
                await page.wait_for_timeout(1200)
                self._emit(logger, f"[抖音搜索] 滚动第 {round_index + 1} 轮，当前 {len(results)} 条")

            raw_results = await page.evaluate(
                """
                    () => {
                        const isDuration = (text) => /^\\d{1,2}:\\d{2}(?::\\d{2})?$/.test(text);
                        const isCountText = (text) => /^(\\d+(?:\\.\\d+)?[涓囧崈]?|\\d+)$/.test(text.replace(/,/g, '').replace(/\\+/g, ''));
                    const cards = Array.from(document.querySelectorAll('.search-result-card'));
                    const rows = [];
                    for (const card of cards) {
                        const link = card.querySelector('a[href*="/video/"]');
                        if (!link || !link.href) continue;
                        const text = (link.innerText || card.innerText || '').trim();
                        if (!text) continue;
                        const lines = text.split(/\\n+/).map((line) => line.trim()).filter(Boolean);
                        const authorIndex = lines.findIndex((line) => line.startsWith('@'));
                        if (authorIndex <= 0) continue;

                        const title = lines[authorIndex - 1] || '';
                        const author = (lines[authorIndex] || '').replace(/^@+/, '').trim();
                        const publishTime = lines[authorIndex + 1] || '';
                        const coverImg = card.querySelector('img');
                        const coverImage = (coverImg?.currentSrc || coverImg?.src || '').trim();

                        const countTexts = [];
                        let likeText = '';
                        let commentText = '';
                        let duration = '';
                        for (let i = authorIndex - 2; i >= 0; i--) {
                            const line = lines[i];
                            if (isCountText(line)) {
                                countTexts.push(line);
                                if (!likeText) {
                                    likeText = line;
                                    continue;
                                }
                                if (!commentText) {
                                    commentText = line;
                                    continue;
                                }
                                continue;
                            }
                            if (!duration && isDuration(line)) {
                                duration = line;
                            }
                        }

                        rows.push({
                            url: link.href,
                            title,
                            author,
                            cover_image: coverImage,
                            likes_text: likeText,
                            comments_text: commentText,
                            count_texts: countTexts,
                            publish_time: publishTime,
                            duration,
                        });
                    }
                    return rows;
                }
                """
            )

            deduped: List[Dict] = []
            seen = set()
            for item in raw_results:
                aweme_id = extract_aweme_id(item.get("url", ""))
                key = aweme_id or item.get("url", "")
                if not key or key in seen:
                    continue
                seen.add(key)
                deduped.append(
                    {
                        "platform": "douyin",
                        "aweme_id": aweme_id,
                        "url": item.get("url", ""),
                        "title": item.get("title", ""),
                        "author": item.get("author", ""),
                        "cover_image": item.get("cover_image", ""),
                        "likes": parse_count_text(item.get("likes_text", "")),
                        "likes_text": item.get("likes_text", ""),
                        "comments": parse_count_text(item.get("comments_text", "")),
                        "comments_text": item.get("comments_text", ""),
                        "publish_time": item.get("publish_time", ""),
                        "duration": item.get("duration", ""),
                        "collects": 0,
                    }
                )
                if len(deduped) >= max_results:
                    break

            return deduped
        finally:
            await page.close()

    async def scrape_video_comments(
        self,
        video_url: str,
        max_comments: int = 80,
        max_scroll_rounds: int = 18,
        logger: Optional[Callable[[str, str], None]] = None,
        progress_callback: Optional[Callable[[Dict], None]] = None,
    ) -> List[Dict]:
        page = await self._new_page(logger=logger)
        try:
            self._emit(logger, f"[抖音评论] 打开视频：{video_url}")
            await page.goto(video_url, wait_until="domcontentloaded", timeout=60000)
            await page.wait_for_timeout(5000)
            await page.wait_for_selector(".comment-mainContent", timeout=30000)
            if progress_callback:
                try:
                    progress_callback(
                        {
                            "phase": "opening",
                            "collected_comments": 0,
                            "visible_comments": 0,
                            "scroll_round": 0,
                            "scroll_round_limit": max(1, min(int(max_scroll_rounds or 18), 300)),
                            "last_message": "评论区已打开，正在开始滚动采集。",
                        }
                    )
                except Exception:
                    pass

            max_scroll_rounds = max(1, min(int(max_scroll_rounds or 18), 300))
            stable_rounds = 0
            last_count = 0

            for round_index in range(max_scroll_rounds):
                current_count = await page.evaluate(
                    """
                    () => {
                        return Array.from(document.querySelectorAll('.comment-item-info-wrap'))
                            .filter((el) => !el.closest('.replyContainer')).length;
                    }
                    """
                )
                if progress_callback:
                    try:
                        progress_callback(
                            {
                                "phase": "scrolling",
                                "collected_comments": min(int(current_count or 0), int(max_comments or 0)),
                                "visible_comments": int(current_count or 0),
                                "scroll_round": round_index + 1,
                                "scroll_round_limit": max_scroll_rounds,
                                "last_message": f"正在滚动评论区，第 {round_index + 1} 轮，当前可见 {current_count} 条评论。",
                            }
                        )
                    except Exception:
                        pass

                if current_count >= max_comments:
                    break

                if current_count == last_count:
                    stable_rounds += 1
                else:
                    stable_rounds = 0
                    last_count = current_count

                if stable_rounds >= 4:
                    break

                await page.evaluate(
                    """
                    () => {
                        const main = document.querySelector('.comment-mainContent');
                        if (main) {
                            let scroller = main;
                            while (scroller && scroller.parentElement) {
                                if (scroller.scrollHeight > scroller.clientHeight + 60) break;
                                scroller = scroller.parentElement;
                            }
                            if (scroller && scroller.scrollHeight > scroller.clientHeight + 60) {
                                scroller.scrollTop += Math.max(scroller.clientHeight * 0.9, 700);
                            } else {
                                window.scrollBy(0, 900);
                            }
                        } else {
                            window.scrollBy(0, 900);
                        }
                    }
                    """
                )
                await page.wait_for_timeout(1200)
                self._emit(logger, f"[抖音评论] 滚动第 {round_index + 1} 轮，当前 {current_count} 条")

            raw_comments = await page.evaluate(
                """
                () => {
                    const rows = [];
                    const normalizeText = (text) => (text || '').replace(/\\s+/g, ' ').trim();
                    const isVisible = (el) => {
                        if (!el || !(el instanceof Element)) return false;
                        const style = window.getComputedStyle(el);
                        if (style.display === 'none' || style.visibility === 'hidden' || Number(style.opacity || 1) === 0) {
                            return false;
                        }
                        return Boolean(el.getClientRects().length || el.offsetWidth || el.offsetHeight);
                    };
                    const isActionText = (text) => {
                        const value = normalizeText(text);
                        if (!value) return true;
                        if (/^\\.{2,}$/.test(value)) return true;
                        const exactActions = ['回复', '分享', '举报', '删除', '置顶', '取消置顶', '作者赞过'];
                        if (exactActions.includes(value)) return true;
                        if ((value.startsWith('展开') || value.startsWith('收起')) && (value.includes('回复') || value.includes('回覆') || value.includes('条'))) return true;
                        if (value.startsWith('加载中') || value.startsWith('正在加载') || value.startsWith('没有更多')) return true;
                        return false;
                    };
                    const isMetaText = (text) => {
                        const value = normalizeText(text);
                        if (!value || value.length > 60) return false;
                        const timeWords = ['刚刚', '分钟前', '小时前', '天前', '周前', '月前', '年前', '昨天', '今天', '前天'];
                        if (timeWords.some((word) => value.includes(word))) return true;
                        if (/\\d{4}[-/.]\\d{1,2}([-/.]\\d{1,2})?/.test(value)) return true;
                        if (value.includes('年') && value.includes('月') && /\\d/.test(value)) return true;
                        return false;
                    };
                    const getText = (el) => normalizeText(el?.innerText || el?.textContent || '');
                    const firstUsefulText = (elements, username) => {
                        for (const el of elements) {
                            if (!el || !isVisible(el)) continue;
                            const text = getText(el);
                            if (!text || text === username || isActionText(text) || isMetaText(text)) continue;
                            return text;
                        }
                        return '';
                    };
                    const extractContentByOrder = (itemRoot, authorLink, username) => {
                        const texts = [];
                        let afterAuthor = false;
                        const walker = document.createTreeWalker(itemRoot, NodeFilter.SHOW_TEXT);
                        while (walker.nextNode()) {
                            const node = walker.currentNode;
                            const parent = node.parentElement;
                            if (!parent || !isVisible(parent)) continue;
                            if (parent.closest('svg, style, script, noscript')) continue;
                            const text = normalizeText(node.nodeValue);
                            if (!text) continue;
                            if (!afterAuthor) {
                                if (authorLink.contains(parent) || parent === authorLink) {
                                    afterAuthor = true;
                                }
                                continue;
                            }
                            if (parent.closest('a[href*="/user/"]')) continue;
                            if (text === username || isActionText(text)) {
                                if (texts.length) break;
                                continue;
                            }
                            if (isMetaText(text)) {
                                if (texts.length) break;
                                continue;
                            }
                            texts.push(text);
                        }
                        return normalizeText(texts.join(' '));
                    };
                    const extractMetaText = (itemRoot, root) => {
                        const fixedMeta = itemRoot.querySelector('.vo4kEeuY, .fJhvAqos, [class*="comment-item-time"], [class*="time"]');
                        const fixedText = getText(fixedMeta);
                        if (isMetaText(fixedText)) return fixedText;
                        const textEls = Array.from(itemRoot.querySelectorAll('span, div, p'));
                        for (const el of textEls) {
                            if (!isVisible(el)) continue;
                            const text = getText(el);
                            if (isMetaText(text)) return text;
                        }
                        return getText(root.querySelector('[class*="time"]'));
                    };
                    const itemSet = new Set();
                    const stableItems = Array.from(document.querySelectorAll('[data-e2e="comment-item"]'));
                    for (const item of stableItems) {
                        if (!item.closest('.replyContainer') && item.querySelector('a[href*="/user/"]')) {
                            itemSet.add(item);
                        }
                    }
                    for (const infoWrap of Array.from(document.querySelectorAll('.comment-item-info-wrap'))) {
                        if (infoWrap.closest('.replyContainer')) continue;
                        const item = infoWrap.closest('[data-e2e="comment-item"], .comment-item, li, [data-e2e*="comment"]') || infoWrap.parentElement;
                        if (item && item.querySelector('a[href*="/user/"]')) itemSet.add(item);
                    }
                    if (!itemSet.size) {
                        for (const link of Array.from(document.querySelectorAll('a[href*="/user/"]'))) {
                            const item = link.closest('[data-e2e*="comment"], li, article, [role="listitem"]');
                            if (item && !item.closest('.replyContainer')) itemSet.add(item);
                        }
                    }
                    const items = Array.from(itemSet);

                    for (const itemRoot of items) {
                        const root = itemRoot.querySelector('.comment-item-info-wrap')?.parentElement || itemRoot;
                        if (!root) continue;

                        const authorLink =
                            itemRoot.querySelector('.comment-item-info-wrap a[href*="/user/"]') ||
                            itemRoot.querySelector('a[href*="/user/"]');
                        if (!authorLink || !authorLink.href) continue;

                        const username = (authorLink.innerText || '').trim();
                        const profileUrl = authorLink.href;
                        if (!username) continue;
                        const container = itemRoot;
                        const contentEl =
                            itemRoot.querySelector('.gD6hDm2O .JrWL1Ykc') ||
                            itemRoot.querySelector('.gD6hDm2O') ||
                            root.querySelector('.C7LroK_h .WFJiGxr7') ||
                            root.querySelector('.C7LroK_h') ||
                            itemRoot.querySelector('[class*="comment-content"]') ||
                            root.querySelector('[class*="comment-content"]');
                        const avatarImg = container.querySelector('span[data-e2e="live-avatar"] img, .avatar-component-avatar-container img, img[alt*="头像"]');
                        const statsEl =
                            itemRoot.querySelector('.vXZJEXVc') ||
                            itemRoot.querySelector('.comment-item-stats-container') ||
                            root.querySelector('.vXZJEXVc') ||
                            root.querySelector('.comment-item-stats-container') ||
                            itemRoot.querySelector('[class*="stats"]') ||
                            root.querySelector('[class*="stats"]');
                        const content = firstUsefulText([contentEl], username) || extractContentByOrder(itemRoot, authorLink, username);
                        const rawMetaText = extractMetaText(itemRoot, root);
                        const metaParts = rawMetaText.split(/[·•・]/).map((part) => part.trim()).filter(Boolean);
                        const commentTime = metaParts.length ? metaParts[0] : rawMetaText;
                        const location = metaParts.length > 1 ? metaParts.slice(1).join(' · ') : '';
                        const statsLines = (statsEl?.innerText || '')
                            .split(/\\n+/)
                            .map((part) => part.replace(/\\s+/g, ' ').trim())
                            .filter(Boolean);
                        const statNumbers = statsLines.filter((part) => /\\d/.test(part));
                        const likeText = statNumbers[0] || '';
                        const replyText = statNumbers[1] || '';

                        rows.push({
                            username,
                            profile_url: profileUrl,
                            content,
                            comment_time: commentTime,
                            location,
                            avatar_url: (avatarImg?.getAttribute('src') || avatarImg?.src || '').trim(),
                            like_text: likeText,
                            reply_text: replyText,
                        });
                    }
                    return rows;
                }
                """
            )

            deduped: List[Dict] = []
            seen = set()
            for item in raw_comments:
                username = str(item.get("username", "")).strip()
                profile_url = str(item.get("profile_url", "")).strip()
                content = str(item.get("content", "")).strip()
                comment_time = str(item.get("comment_time", "")).strip()
                avatar_url = str(item.get("avatar_url", "")).strip()
                location = str(item.get("location", "")).strip()
                time_parts = [
                    part.strip()
                    for part in comment_time.replace("•", "·").replace("・", "·").split("·")
                    if part.strip()
                ]
                if not location and len(time_parts) > 1:
                    location = " · ".join(time_parts[1:])
                if not username or not profile_url or not content:
                    continue
                user_id = extract_sec_user_id(profile_url)
                key = f"{user_id}|{content}|{comment_time}"
                if key in seen:
                    continue
                seen.add(key)
                deduped.append(
                    {
                        "comment_index": len(deduped) + 1,
                        "comment_id": "",
                        "username": username,
                        "user_id": user_id,
                        "user_xsec_token": "",
                        "platform": "douyin",
                        "content": content,
                        "comment": content,
                        "comment_time": comment_time,
                        "region": location,
                        "location": location,
                        "ip_location": location,
                        "like_count": parse_count_text(item.get("like_text", "")),
                        "reply_count": parse_count_text(item.get("reply_text", "")),
                        "profile_url": profile_url,
                        "avatar_url": avatar_url,
                    }
                )
                if len(deduped) >= max_comments:
                    break
            if progress_callback:
                try:
                    progress_callback(
                        {
                            "phase": "extracting",
                            "collected_comments": len(deduped),
                            "visible_comments": len(raw_comments),
                            "scroll_round": max_scroll_rounds,
                            "scroll_round_limit": max_scroll_rounds,
                            "last_message": f"评论抓取结束，已整理 {len(deduped)} 条一级评论。",
                        }
                    )
                except Exception:
                    pass

            self._emit(logger, f"[抖音评论] 共提取 {len(deduped)} 条一级评论用户", "success")
            return deduped
        finally:
            await page.close()
