# Online 前端视图拆分说明

本文记录 `static/index.html` 拆分后的结构、当前完成状态，以及后续新增或迁移页面时的做法。

## 当前状态

本次拆分的目标是先把超大的 `index.html` 拆成“壳 + 视图碎片 + 视图脚本”，让后续开发不再继续往主 HTML 里堆大块业务页面。

已经完成：

- 主样式从 `static/index.html` 移到 `static/css/index.css`。
- 新增视图加载器 `static/js/view-loader.js`。
- 新增视图注册表 `static/js/view-registry.js`。
- `static/index.html` 继续保留登录、导航、全局弹窗和部分尚未拆出的隐藏工作台。
- 已拆出的 HTML 视图放在 `static/views/`。
- 已拆出的视图脚本放在 `static/js/views/`。

当前已拆出的视图：

| 视图名 | HTML | JS |
| --- | --- | --- |
| `skill-store` | `static/views/skill-store.html` | 继续使用全局 `static/js/skill.js` |
| `publish` | `static/views/publish.html` | 继续使用全局 `static/js/publish.js` |
| `assets` | `static/views/assets.html` | 继续使用全局 `static/js/publish.js` |
| `scheduled-tasks` | `static/views/scheduled-tasks.html` | 继续使用全局 `static/js/scheduled-tasks.js` |
| `billing` | `static/views/billing.html` | `static/js/views/billing.js` |
| `logs` | `static/views/logs.html` | `static/js/views/logs.js` |
| `production` | `static/views/production.html` | `static/js/views/production.js` |
| `sys-config` | `static/views/sys-config.html` | `static/js/views/sysconfig.js` |
| `agent` | `static/views/agent.html` | `static/js/views/agent.js` |

## 加载流程

导航仍然通过 `data-view="<view>"` 工作，例如：

```html
<button type="button" class="nav-left-item" data-view="publish">发布中心</button>
```

点击导航后，`static/js/init.js` 调用：

```js
showAppView('publish')
```

`showAppView` 会先调用：

```js
window.ensureLobsterViewLoaded(view)
```

加载器逻辑：

1. 如果页面里已经有 `#content-<view>`，直接使用现有 DOM。
2. 如果没有现有 DOM，则从 `view-registry.js` 查注册信息。
3. 按注册信息 fetch 对应 HTML。
4. 把 HTML 插入 `.dashboard-main`。
5. 如配置了 `css` 或 `scripts`，按需加载。
6. 再执行 `init.js` 里原有的视图初始化逻辑，例如 `initPublishView()`、`loadBillingView()`。

这个兼容逻辑很重要：它允许一个页面先保持在 `index.html` 里，等稳定后再迁到 `static/views/`，不会一次性大爆改。

## 注册一个新视图

新增一个视图时，先创建 HTML：

```html
<!-- static/views/example.html -->
<div id="content-example" class="content-block">
  <div class="card">
    <h3>示例页面</h3>
  </div>
</div>
```

然后在 `static/js/view-registry.js` 注册：

```js
window.registerLobsterView('example', {
  html: '/static/views/example.html',
  scripts: '/static/js/views/example.js?v=20260528-view-split',
  init: 'initExampleView'
});
```

如果该视图脚本仍然是全局加载的，可以先只注册 HTML：

```js
window.registerLobsterView('example', {
  html: '/static/views/example.html'
});
```

再在 `static/js/init.js` 的 `runAppViewInit(view)` 中调用初始化函数：

```js
if (view === 'example' && typeof initExampleView === 'function') initExampleView();
```

## 迁移旧页面的推荐步骤

推荐小步迁移，避免一次拆太多导致按钮失效。

1. 先确认页面根节点是 `id="content-<view>"` 且带 `content-block`。
2. 把整块 DOM 移到 `static/views/<view>.html`。
3. 在 `static/js/view-registry.js` 注册 HTML。
4. 如果页面 JS 原来依赖“页面加载时 DOM 已存在”，先改成进入视图时再绑定。
5. 绑定函数要做幂等保护，避免重复进入页面重复绑定事件。
6. 跑 `node --check` 和浏览器冒烟测试。

事件绑定推荐写法：

```js
function initExampleView() {
  var btn = document.getElementById('exampleBtn');
  if (btn && !btn._exampleBound) {
    btn._exampleBound = true;
    btn.addEventListener('click', function() {
      // ...
    });
  }
}
```

不推荐在脚本顶层直接绑定懒加载视图里的 DOM：

```js
// 不推荐：视图 HTML 还没 fetch 进来时，这里拿不到按钮
document.getElementById('exampleBtn').addEventListener('click', handler);
```

## 什么时候拆 JS

可以先拆 HTML，再拆 JS。

适合先保留全局 JS 的情况：

- 脚本被多个页面共用。
- 脚本里还有很多全局弹窗、全局状态。
- 刚迁移，风险较高，需要先保证页面能打开。

适合迁到 `static/js/views/` 的情况：

- 只服务单个视图。
- 已有明确的 `initXxxView()`、`loadXxxView()`。
- 顶层 DOM 绑定已改成懒加载后初始化。

## 本次已做的绑定兼容

`skill.js` 已处理：

- 技能商店 Tab。
- 添加 MCP 弹窗入口。
- 刷新按钮。
- MCP Registry 搜索。
- OpenClaw 微信扫码入口。

`publish.js` 已处理：

- 发布中心 Tab。
- 发布账号类型 Tab。
- 添加账号弹窗入口。
- 发布/素材刷新按钮。
- 素材库搜索、筛选、上传、保存 URL、备选组弹窗。
- 账号详情和定时发布相关按钮。

`scheduled-tasks.js` 目前已经有 `initScheduledTasksView()`，所以先只拆 HTML，脚本继续全局加载。

## 验证方式

基础检查：

```powershell
node --check static/js/init.js
node --check static/js/skill.js
node --check static/js/publish.js
node --check static/js/scheduled-tasks.js
node --check static/js/view-loader.js
node --check static/js/view-registry.js
node --check static/js/views/billing.js
node --check static/js/views/logs.js
node --check static/js/views/production.js
node --check static/js/views/sysconfig.js
node --check static/js/views/agent.js
git diff --check
```

静态访问检查：

```powershell
python -m http.server 8765 --bind 127.0.0.1
```

然后访问：

```text
http://127.0.0.1:8765/static/index.html
```

重点切换这些视图：

- 技能商店
- 发布中心
- 素材库
- 定时任务
- 消费记录
- 日志
- 系统配置
- 代理商

静态服务下接口 404 是正常的，因为没有启动后端；这里主要验证 HTML、CSS、JS 和视图切换是否正常。

## 后续建议

下一阶段建议继续拆：

- `publish.js` 按账号、素材、发布记录、账号详情拆模块。
- `scheduled-tasks.js` 按参数表单、任务列表、执行记录、发布草稿拆模块。
- `index.html` 里剩余隐藏工作台逐步迁到 `static/views/`。
- 全局弹窗可以先保留，等对应业务视图稳定后再迁移。
- `init.js` 最终只保留登录、导航、用户状态、视图初始化分发。

