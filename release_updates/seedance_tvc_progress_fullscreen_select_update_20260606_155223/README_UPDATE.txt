创意分镜视频更新包
生成时间：2026-06-06 15:52:23

更新内容：
1. 创意分镜视频任务查询接口增加统一进度字段，前端结果页显示进度条。
2. 视频结果放大查看改为自定义全屏容器，增加“按 Esc 退出全屏”和退出按钮。
3. 生成模型等设置下拉改为自定义下拉菜单，展开列表不再使用 Windows 原生灰色 option 菜单。
4. 更新 OpenMind / 云雾 / Comfly 多渠道视频策略相关客户端适配。

覆盖方式：
将本包内文件按目录覆盖到客户端安装目录或项目根目录对应位置，重启客户端后生效。

包含文件：
CLIENT_CODE_VERSION.json
static/client_version.json
static/index.html
static/css/index.css
static/js/comfly-seedance-tvc-studio.js
backend/app/api/comfly_seedance_tvc.py
backend/app/services/comfly_seedance_tvc_job_store.py
skills/comfly_seedance_tvc_video/scripts/comfly_seedance_storyboard_pipeline.py
