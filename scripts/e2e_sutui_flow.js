/**
 * E2E：打开龙虾 → 登录(单机版) → 智能对话列能力 → 速推生成视频 → 发布管理
 * 需先启动本机后端: python3 -m backend.run（在线版）
 * 运行: node scripts/e2e_sutui_flow.js
 */
const { chromium } = require('playwright-core');
const BASE = process.env.LOBSTER_BASE || 'http://127.0.0.1:8000';

async function main() {
  const browser = await chromium.launch({ headless: true });
  const page = await browser.newPage();
  const log = (msg, extra) => console.log('[E2E]', msg, extra !== undefined ? extra : '');

  try {
    await page.goto(BASE, { waitUntil: 'domcontentloaded', timeout: 15000 });
    await page.waitForTimeout(1500);

    const authPanel = page.locator('#authPanel');
    const loginForm = page.locator('#loginForm');
    const formVisible = await loginForm.isVisible();

    if (await authPanel.isVisible() && formVisible) {
      log('单机版登录页，使用账号密码登录');
      await page.fill('input[name=username]', 'user@lobster.local');
      await page.fill('input[name=password]', 'lobster123');
      await page.locator('#loginForm button[type=submit]').click();
      await page.waitForTimeout(2500);
    } else if (await authPanel.isVisible()) {
      log('当前为在线版(二维码登录)，无法自动登录，跳过');
      await browser.close();
      process.exit(1);
    }

    const dash = page.locator('#dashboard');
    await dash.waitFor({ state: 'visible', timeout: 5000 }).catch(() => {});
    if (!(await dash.isVisible())) {
      log('登录后未看到仪表盘');
      await browser.close();
      process.exit(2);
    }
    log('已进入仪表盘');

    // 智能对话
    await page.click('div.nav-left-item[data-view=chat]');
    await page.waitForTimeout(800);
    const chatInput = page.locator('#chatInput');
    await chatInput.waitFor({ state: 'visible', timeout: 3000 });

    // 1) 列出能力
    log('发送：列出当前可用的能力');
    await chatInput.fill('列出当前可用的能力');
    await page.locator('#chatSendBtn').click();
    await page.waitForTimeout(10000);
    const reply1 = await page.locator('#chatMessages .chat-msg.assistant').last().innerText().catch(() => '');
    log('列能力回复长度:', reply1.length);
    if (reply1.length > 0) log('回复摘要:', reply1.substring(0, 350));

    // 2) 速推生成视频
    log('发送：用速推生成 5 秒测试视频');
    await chatInput.fill('用速推生成一个 5 秒的测试视频，提示词：一只猫在草地上跑');
    await page.locator('#chatSendBtn').click();
    await page.waitForTimeout(25000);
    const reply2 = await page.locator('#chatMessages .chat-msg.assistant').last().innerText().catch(() => '');
    log('生成视频回复长度:', reply2.length);
    if (reply2.length > 0) log('回复摘要:', reply2.substring(0, 450));

    // 3) 发布管理
    log('进入发布管理');
    await page.click('div.nav-left-item[data-view=publish]');
    await page.waitForTimeout(1500);
    const publishArea = page.locator('#content-publish');
    await publishArea.waitFor({ state: 'visible', timeout: 3000 }).catch(() => {});
    const hasPub = await publishArea.isVisible();
    log('发布管理区域可见:', hasPub);
    const pubText = await publishArea.innerText().catch(() => '');
    log('发布管理内容摘要:', pubText.substring(0, 350));

    log('E2E 流程结束');
  } catch (e) {
    log('错误', e.message);
    process.exit(3);
  } finally {
    await browser.close();
  }
}

main();
