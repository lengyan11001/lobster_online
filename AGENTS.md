# 全局规则（必须遵守）

## 发布 / 部署规则（用户反复强调过，不要再问、不要再犯）
- 改完代码只做这三步就停：本地跑验证 → `git commit` → `git push`。
- **禁止自动执行**下列动作，**必须等用户明确说「发」「发布」「部署」「OTA」**：
  - 打客户端 OTA 包、写/改 manifest、上传 TOS/CDN（`scripts/pack_client_code_ota.py`、`scripts/publish_client_code_ota_to_server.py`）
  - 上传服务端代码到生产、重启服务（`systemctl restart lobster-*`）
  - 任何把产物上传到 TOS / OSS / CDN 的动作
- 用户说「发」时，才按既有流程执行，并做发布前后校验（公网 manifest = 服务端 manifest = 兜底副本 sha256 = CDN 下载 sha256；TOS HEAD 200 + 长度一致）。
- 一旦误发（未经允许发出去了）：立刻列出「发了什么 + 回滚点（备份路径 / 上一个版本号）」，等用户决定，自己不要再动手。

## 关键位置
- 客户端仓库：`D:\lobster_online`（remote `ssh.github.com:443/lengyan11001/lobster_online.git`，main）
- 服务端仓库：`D:\lobster_server`（生产 `ubuntu@42.194.209.150:/opt/lobster-server`，部署备份在 `/opt/lobster-server/backups/`）
- 客户端代码 OTA manifest：`https://bhzn.top/client/client-code/manifest.json`
- 诊断包下载：`cd C:\Documents\New project\start_entry_sandbox; python .\fetch_diag.py <diag_id> user_<uid>`

## 发布节奏（用户明确批评过「改一点就发正式环境」）
- 生产环境不是测试环境：**同一个小需求不要边改边发**，攒齐一批、本地/沙箱验证通过、用户点头后再发一次。
- 发布前必须齐活四样，缺一样不发：
  1) 本地验证证据（复现步骤 + 修复前后结果 / 测试输出）
  2) 变更清单（改了哪些文件、影响哪些用户/设备）
  3) 回滚点（备份绝对路径、上一版本号与 sha256）
  4) 发布后校验计划（manifest/接口/关键路径实测）
- 探索期就该在本地/沙箱里改（服务端可临时改文件重启本机服务，客户端可只推代码），**不要动生产**。
- 生产发布、生产数据库改动、OTA 下发：只在用户说「发/部署」之后执行；执行时一次说清「发了什么 + 校验结果 + 回滚方式」。
