# EXE 加密包打包说明

本说明记录 `lobster_online` 本地 EXE 与加密分发包的打包入口。当前相关脚本放在 `_pack_exe_test/`，该目录被 `.gitignore` 忽略，属于制包机本地目录；正式仓库只记录流程，不提交 staging、dist、output 等产物。

## 目录与文件

| 路径 | 用途 |
|------|------|
| `_pack_exe_test/README.md` | 原始测试说明，记录普通 EXE 启动器打包方式。 |
| `_pack_exe_test/launcher.py` | EXE 启动器源码。 |
| `_pack_exe_test/build_exe.bat` | 只编译 `launcher.py`，输出普通 `lobster.exe`。 |
| `_pack_exe_test/build_encrypted_dist.py` | 复制项目、编译 `.pyc`、删除源码、编译启动器并输出加密分发 zip。 |
| `_pack_exe_test/dist/lobster.exe` | 普通 EXE 或加密流程中生成的启动器产物。 |
| `_pack_exe_test/staging/` | 加密分发包临时目录。脚本会重建，禁止手工维护为正式内容。 |
| `_pack_exe_test/output/` | 加密分发 zip 输出目录。 |

## 普通 EXE

普通 EXE 只把启动器编译成 `lobster.exe`，不处理项目源码加密。

```powershell
cd /d D:\lobster_online\_pack_exe_test
.\build_exe.bat
```

产物：

```text
D:\lobster_online\_pack_exe_test\dist\lobster.exe
```

测试方式：

1. 将 `dist\lobster.exe` 复制到 `D:\lobster_online\` 根目录。
2. 双击运行。

启动器流程：

1. 检测 `.installed` 标记文件。
2. 未安装时检查 Python 依赖。
3. 依赖不完整时调用 `install.bat`。
4. 启动前检查 OTA 代码更新。
5. 启动 Backend 8000 与 MCP 8001。
6. 自动打开浏览器。

启动器支持参数：

```powershell
lobster.exe --reinstall
```

## 加密分发包

加密分发包入口是：

```powershell
cd /d D:\lobster_online
.\python\python.exe .\_pack_exe_test\build_encrypted_dist.py
```

调试时可跳过源码编译与删除：

```powershell
cd /d D:\lobster_online
.\python\python.exe .\_pack_exe_test\build_encrypted_dist.py --skip-encrypt
```

脚本流程：

1. 清理并重建 `_pack_exe_test/staging/`。
2. 复制运行所需目录和文件到 staging。
3. 将 `backend/`、`mcp/`、`publisher/` 内的 `.py` 编译为模块级 `.pyc`。
4. 删除已编译模块的 `.py` 源码，保留入口类文件，例如 `run.py`、`__main__.py`、`__init__.py`。
5. 用 PyInstaller 编译 `launcher.py` 为 `lobster.exe`。
6. 将 staging 打成 zip，输出到 `_pack_exe_test/output/`。

输出包名格式：

```text
_pack_exe_test/output/lobster_desktop_YYYYMMDD_HHMMSS.zip
```

## 分发包内容边界

加密分发 zip 主要包含代码、启动器和安装脚本。用户侧仍需要准备或由安装流程检测的运行时包括：

| 目录 | 说明 |
|------|------|
| `python/` | 嵌入式 Python。 |
| `deps/` | ffmpeg、pip wheels 等离线依赖。 |
| `browser_chromium/` | Playwright Chromium，发布相关功能需要。 |
| `nodejs/` | Node.js 与 OpenClaw 运行环境。 |

如果目标是完整离线交付，仍需参考 `docs/生产打包流程.md` 与 `docs/离线安装依赖清单.md`，确认运行时依赖是否已经随包或随安装目录准备好。

## 注意事项

- `_pack_exe_test/` 是本地忽略目录，里面可能有 staging、历史 zip、日志、数据库或本机配置，不要整体提交。
- 加密流程不会修改原始项目文件，所有源码编译与删除只发生在 `_pack_exe_test/staging/`。
- 当前加密方式是 `.py -> .pyc` 后删除源码，并编译启动器；不是严格意义上的不可逆代码保护。
- 生成 zip 前确认 `.env`、日志、数据库、用户素材等本机数据没有被误复制。
- 只打包不等于发布。OTA 或服务器发布必须单独按发布流程执行。
