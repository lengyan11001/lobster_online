(function initLobsterI18n() {
  if (window.LobsterI18n) return;

  var DEFAULT_LANGUAGE = 'zh-CN';
  var SUPPORTED = { 'zh-CN': true, 'en-US': true };
  var STORAGE_KEY = 'lobster_ui_language';
  var USER_STORAGE_PREFIX = 'lobster_ui_language:user:';
  var currentUserId = '';
  var applying = false;
  var scheduled = false;
  var textState = new WeakMap();
  var attrState = new WeakMap();

  var GENERATED_EN = window.LobsterGeneratedEn || {};
  var CURATED_EN = {
    '首页': 'Home', '技能商店': 'Skills', '发布中心': 'Publishing', '内容记录': 'Content',
    '素材库': 'Assets', '客资线索': 'Leads', '定时任务': 'Schedules', '消费记录': 'Billing',
    '系统配置': 'System Settings', '日志': 'Logs', '个人设置': 'Personal Settings',
    'AI执行台': 'AI Console', 'AI 执行台': 'AI Console', '教程': 'Tutorial', '用户': 'User',
    '版本': 'Version', '发现新版本': 'Update Available', '点击更新并自动重启': 'Update and restart',
    '刷新页面': 'Refresh Page', '退出登录': 'Sign Out', '语言': 'Language', '中文': 'Chinese',
    '英文': 'English', '算力': 'Credits', '充值': 'Top Up', '保存': 'Save', '取消': 'Cancel',
    '关闭': 'Close', '确认': 'Confirm', '确定': 'OK', '删除': 'Delete', '编辑': 'Edit',
    '添加': 'Add', '新建': 'New', '创建': 'Create', '启用': 'Enable', '停用': 'Disable',
    '开启': 'On', '刷新': 'Refresh', '搜索': 'Search', '重试': 'Retry', '复制': 'Copy',
    '下载': 'Download', '上传': 'Upload', '导出': 'Export', '导入': 'Import', '返回': 'Back',
    '下一步': 'Next', '上一步': 'Previous', '提交': 'Submit', '查看': 'View', '详情': 'Details',
    '操作': 'Actions', '名称': 'Name', '标题': 'Title', '状态': 'Status', '时间': 'Time',
    '类型': 'Type', '来源': 'Source', '平台': 'Platform', '账号': 'Account', '设备': 'Device',
    '员工': 'Employee', '任务': 'Task', '进度': 'Progress', '结果': 'Result', '备注': 'Notes',
    '设置': 'Settings', '配置': 'Configure', '更多': 'More', '全部': 'All', '暂无数据': 'No data',
    '暂无记录': 'No records', '加载中...': 'Loading...', '正在加载...': 'Loading...',
    '男': 'Male', '女': 'Female', '是': 'Yes', '否': 'No', '无': 'None', '天': 'days',
    '张': 'images', '段': 'clips', '个': 'items', '条': 'items', '份': 'files', '页': 'pages',
    'SKU 图': 'SKU Images', 'B站': 'Bilibili', '必火': 'Bihuo', '必火智能的远景：': 'Our Vision:',
    '成为全球领先的企业AI员工解决方案服务商': 'To become a world-leading enterprise AI employee solution provider',
    '龙虾': 'Lobster', '审核稿出几次（1～10）': 'Number of review drafts (1-10)',
    '执行中': 'Running', '排队中': 'Queued', '待执行': 'Pending', '已完成': 'Completed',
    '完成': 'Completed', '失败': 'Failed', '已取消': 'Canceled', '已跳过': 'Skipped',
    '跳过': 'Skipped', '在线': 'Online', '离线': 'Offline', '未配置': 'Not configured',
    '已配置': 'Configured', '暂无任务': 'No tasks', '当前没有正在执行的任务': 'No task is currently running',
    '当前执行': 'Current Task', '停止任务': 'Stop Task', '查看详情': 'View Details',
    '开始执行': 'Start', '继续执行': 'Continue', '重新执行': 'Run Again',
    '创建员工': 'Create Employee', '员工列表': 'Employees', '平台账号': 'Platform Accounts',
    '选择设备': 'Select Device', '切换设备': 'Switch Device', '我的': 'Profile', '工作流': 'Workflow',
    '销售员工': 'Sales Employee', 'AI海外员工': 'AI Global Employee', 'AI 海外员工': 'AI Global Employee',
    '精准获客': 'Targeted Leads', '抖音私信接管': 'Douyin DM Assistant',
    '微信私信接管': 'WeChat DM Assistant', 'LinkedIn线索挖掘': 'LinkedIn Lead Mining',
    'LinkedIn 线索挖掘': 'LinkedIn Lead Mining', 'X线索采集': 'X Lead Collection',
    'X 线索采集': 'X Lead Collection', '全球获客': 'Global Leads', '新建任务': 'New Task',
    '执行记录': 'Runs', '线索工作台': 'Lead Workbench', '最终报告': 'Final Report',
    '输出详情': 'Output Details', '执行详情': 'Run Details', '任务详情': 'Task Details',
    '任务列表': 'Tasks', '历史任务': 'History', '历史记录': 'History', '候选人': 'Candidates',
    '候选人池': 'Candidate Pool', '联系方式': 'Contact', '联系方式列表': 'Contact List',
    '优先线索': 'Priority Leads', '优先跟进线索': 'Priority Leads', '公开联系方式': 'Public Contacts',
    '公司': 'Company', '角色': 'Role', '关键词': 'Keywords', '话题': 'Hashtags',
    '个人主页': 'Profile', '公司主页': 'Company Page', '目标客户画像': 'Target Profile',
    '执行摘要': 'Executive Summary', '线索概览': 'Lead Overview', '行动工作台建议': 'Action Workbench',
    '下一步动作': 'Next Actions', '数据限制': 'Data Limitations', '查看原始数据': 'View Raw Data',
    '有公开联系方式': 'With Public Contact', '建议': 'Recommendation', '姓名': 'Name',
    '角色/公司': 'Role / Company', '开场白': 'Opening Line', '关系路径': 'Relationship Map',
    'A类核心名单': 'Tier A Leads', 'B类扩展名单': 'Tier B Leads', '观察名单': 'Watch List',
    '补资料任务': 'Data Enrichment', '触达资产': 'Outreach Assets', '待补全姓名': 'Name unavailable',
    'LinkedIn 用户（待补全姓名）': 'LinkedIn User (name unavailable)', '暂无任务。': 'No tasks.',
    '请选择一个任务。': 'Select a task.',
    '暂无输出。任务执行后，每一步结果会出现在这里。': 'No output yet. Step results will appear here after execution.',
    '请选择左侧输出。': 'Select an output on the left.',
    '本轮没有可直接展示的公开联系方式。': 'No verified public contact information was found.',
    '开始一键挖掘': 'Start Mining', '自动续跑': 'Resume Automatically', '执行下一步': 'Run Next Step',
    '复制线索名单': 'Copy Lead List', '复制报告': 'Copy Report', '下载报告': 'Download Report',
    '任务已启动，正在后台执行。': 'Task started and is running in the background.',
    '任务正在自动执行，页面会持续刷新进度。': 'The task is running automatically. Progress will refresh.',
    '请输入关键词': 'Enter keywords', '请输入搜索词': 'Enter search terms', '搜索任务': 'Search tasks',
    '全部来源': 'All Sources', '全部状态': 'All Statuses', '最近更新': 'Recently Updated',
    '创建时间': 'Created At', '更新时间': 'Updated At', '手机号': 'Phone Number',
    '验证码': 'Verification Code', '密码': 'Password', '登录': 'Sign In', '注册': 'Register',
    '验证码登录': 'Code Login', '密码登录': 'Password Login', '获取短信验证码': 'Send Code',
    '登录 / 注册': 'Sign In / Register', '手机号注册和登录': 'Phone Sign In', '请稍候': 'Please wait',
    '网络连接暂时中断，请稍后重试': 'Network connection was interrupted. Please try again.',
    '页面暂时无法打开，请稍后重试': 'This page is temporarily unavailable. Please try again.',
    '重新连接': 'Reconnect', '正在连接...': 'Connecting...',
    '说明': 'Help', '返回首页': 'Back to Home', '返回技能商店': 'Back to Skills',
    '刷新记录': 'Refresh Records', '刷新状态': 'Refresh Status', '刷新草稿': 'Refresh Drafts',
    '刷新预览': 'Refresh Preview', '刷新配置': 'Refresh Configuration', '刷新记忆': 'Refresh Memory',
    '刷新模板': 'Refresh Templates', '读取上次输入': 'Load Previous Input', '保存输入': 'Save Input',
    '保存草稿': 'Save Draft', '保存配置': 'Save Configuration', '保存登录密码': 'Save Login Password',
    '保存对话路由': 'Save Chat Route', '保存系统别名': 'Save System Alias', '保存素材下载路径': 'Save Asset Path',
    '保存 Token': 'Save Token', '保存URL素材': 'Save URL Asset', '保存并授权': 'Save and Authorize',
    '随机更换': 'Generate New', '恢复默认': 'Restore Default', '清空': 'Clear', '全选': 'Select All',
    '清除本机个人配置': 'Clear Local Profile', '一键清除个人配置': 'Clear Personal Settings',
    '一键清除个人记忆': 'Clear Personal Memory', '修复运行依赖': 'Repair Runtime Dependencies',
    '安装 3D 依赖': 'Install 3D Dependencies', '切换平台': 'Switch Platform', '开始采集': 'Start Collection',
    '创建模板': 'Create Template', '创建任务': 'Create Task', '开始生成': 'Start Generation',
    '生成图片': 'Generate Image', '生成文章': 'Generate Article', 'AI 生成文章': 'Generate with AI',
    '生成抖音文案': 'Generate Douyin Copy', '生成公众号文章': 'Generate WeChat Article',
    '生成视频号文案': 'Generate Channels Copy', '提交爆款 TVC 任务': 'Submit Viral TVC Task',
    '进入完整分镜工作台': 'Open Full Storyboard Studio', '推送草稿箱': 'Push to Drafts',
    '从素材库选择': 'Choose from Assets', '手动配图': 'Manual Images', '自动配图': 'Auto Images',
    '正文编辑': 'Body Editor', '主题或想法': 'Topic or Idea', '目标读者': 'Target Audience',
    '写作风格': 'Writing Style', '排版主题': 'Layout Theme', '配图风格': 'Image Style',
    '配图比例': 'Image Ratio', '配图数量': 'Image Count', '编辑模式': 'Edit Mode',
    '微信公众号 AppID': 'WeChat Official Account AppID', '默认作者': 'Default Author', '默认主题': 'Default Theme',
    '系统配置': 'System Configuration', '登录密码': 'Login Password', '智能对话路由': 'Chat Routing',
    '本地槽位 ID': 'Local Slot ID', 'AI 能力 Token': 'AI Capability Token', '素材路径': 'Asset Path',
    '运行依赖': 'Runtime Dependencies', '账号查询': 'Account Lookup', '解析账号': 'Resolve Account',
    '作品列表': 'Video List', '拉取作品': 'Fetch Videos', '获取文案': 'Get Copy', '转写记录': 'Transcription Records',
    '重试失败项': 'Retry Failed Items', '复制全部': 'Copy All', '导出 TXT': 'Export TXT', '导出 CSV': 'Export CSV',
    '按账号采集': 'Collect by Account', '按社区采集': 'Collect by Community', '社区': 'Community',
    '公开信息采集工作台': 'Public Information Collection', '新建采集': 'New Collection', '执行过程': 'Run Progress',
    '线索名单': 'Lead List', '采集明细': 'Collection Details', '精准用户方向关键词（必填）': 'Target User Keywords (required)',
    '任务名称': 'Task Name', '开始采集': 'Start Collection', '导出 Excel': 'Export Excel', '复制名单': 'Copy List',
    '内容记录': 'Content Records', '生成素材': 'Generated Assets', '用户上传': 'User Uploads', '图片': 'Image',
    '视频': 'Video', '文案': 'Copy', '公众号文章': 'WeChat Article', '备选组': 'Candidate Group',
    '批量删除': 'Bulk Delete', '确定删除': 'Confirm Delete', '取消': 'Cancel', '网络素材URL（图片/视频链接）': 'Media URL (image/video)',
    '内容发布': 'Content Publishing', '发布账号': 'Publishing Account', '发布设置': 'Publishing Settings',
    '立即发布': 'Publish Now', '定时发布': 'Schedule Publish', '草稿': 'Draft', '已发布': 'Published',
    '全屏': 'Fullscreen', '退出全屏': 'Exit Fullscreen', '等待任务接入': 'Waiting for a task',
    '当前没有任务': 'No current task', '任务中心': 'Task Center', '任务数': 'Task Count',
    '销售员工': 'Sales Employees', '定制员工': 'Custom Employees', '系统模板': 'System Templates',
    '新增员工': 'Add Employee', '编辑员工': 'Edit Employee', '删除员工': 'Delete Employee',
    '停用员工': 'Disable Employee', '恢复员工': 'Restore Employee', '员工详情': 'Employee Details',
    '节点参数': 'Node Parameters', '开始时间': 'Start Time', '结束时间': 'End Time', '执行动作': 'Actions',
    '固定话术': 'Fixed Script', 'AI引导加绿泡泡': 'AI-guided WeChat', '是否加好友': 'Add as Friend',
    '用户启动的工作流': 'User-started Workflow', '正在执行的任务': 'Running Tasks', '任务执行详情': 'Task Execution Details',
    '执行详情': 'Execution Details', '查看详情': 'View Details', '没有正在执行的任务': 'No running tasks',
    '任务执行完成': 'Task completed', '任务执行失败': 'Task failed', '任务已停止': 'Task stopped',
    '请先选择': 'Please select first', '请输入': 'Please enter', '请选择': 'Please select',
    '加载失败': 'Load failed', '保存失败': 'Save failed', '保存成功': 'Saved successfully',
    '操作成功': 'Operation successful', '操作失败': 'Operation failed', '请求失败': 'Request failed',
    '暂无内容': 'No content', '暂无结果': 'No results', '暂无输出': 'No output', '暂无线索': 'No leads',
    '共': 'Total', '页': 'Page', '上一页': 'Previous Page', '下一页': 'Next Page', '第': 'Page',
    '条': 'items', '项': 'items', '人': 'people', '次': 'times', '张': 'images', '秒': 'sec', '分钟': 'min',
    '公开': 'Public', '私密': 'Private', '不公开': 'Unlisted', '专业清爽': 'Professional Clean',
    '极简金色': 'Minimal Gold', '暖色杂志': 'Warm Editorial', '亲切实用': 'Friendly and Practical',
    '转化导向': 'Conversion Focused', '横图': 'Landscape', '宽横图': 'Wide Landscape',
    '方图': 'Square', '竖图': 'Portrait', '高级质感': 'Premium', '戏剧张力': 'Dramatic',
    '干净产品感': 'Clean Product', '情绪化叙事': 'Emotional Narrative', '自动生成音频': 'Generate Audio',
    '只生成画面': 'Visual Only', '自动合成成片并入库': 'Merge and Save Final Video', '仅保留分段结果': 'Keep Segments Only',

    // Secondary views and runtime-generated controls.
    '请求失败': 'Request failed', '网络错误': 'Network error', '网络请求中断': 'Network request interrupted',
    '未知错误': 'Unknown error', '错误：': 'Error: ', '成功': 'Success', '失败：': 'Failed: ',
    '等待中': 'Waiting', '处理中': 'Processing', '处理完成': 'Processed', '未开始': 'Not started',
    '进行中': 'In progress', '待处理': 'To process', '已处理': 'Processed', '已登录': 'Signed in',
    '未登录': 'Not signed in', '已关闭': 'Closed', '异常': 'Error', '未知': 'Unknown',
    '保存中...': 'Saving...', '保存中…': 'Saving...', '提交中...': 'Submitting...', '提交中…': 'Submitting...',
    '删除中...': 'Deleting...', '删除中…': 'Deleting...', '上传中...': 'Uploading...', '上传中…': 'Uploading...',
    '生成中...': 'Generating...', '生成中…': 'Generating...', '处理中...': 'Processing...', '处理中…': 'Processing...',
    '加载中...': 'Loading...', '加载中…': 'Loading...', '查询中...': 'Querying...', '查询中…': 'Querying...',
    '刷新中...': 'Refreshing...', '同步中...': 'Syncing...', '发送中...': 'Sending...',
    '提交失败': 'Submission failed', '任务提交失败': 'Task submission failed', '任务失败': 'Task failed',
    '删除失败': 'Delete failed', '上传失败': 'Upload failed', '发送失败': 'Send failed',
    '生成失败': 'Generation failed', '发布失败': 'Publish failed', '查询失败': 'Query failed',
    '加载失败': 'Load failed', '同步失败': 'Sync failed', '复制失败': 'Copy failed',
    '任务已提交': 'Task submitted', '任务已提交成功，正在查询生成结果…': 'Task submitted. Checking the generated result...',
    '任务已提交，正在生成中。': 'Task submitted and is being generated.', '已保存输入。': 'Input saved.',
    '已选择': 'Selected', '未选择': 'Not selected', '已复制': 'Copied', '已添加': 'Added',
    '已生成': 'Generated', '已就绪': 'Ready', '已发布': 'Published', '已支付': 'Paid', '待支付': 'Payment pending',
    '暂无消息': 'No messages', '暂无内容': 'No content', '暂无结果': 'No results', '暂无输出': 'No output',
    '暂无模板': 'No templates', '暂无生成历史': 'No generation history', '暂无充值记录。': 'No recharge records.',
    '暂无算力变动。': 'No credit changes.', '暂无自定义配置': 'No custom configuration',
    '当前没有可展示的结果。': 'No results to display.', '当前没有可展示的执行过程。': 'No execution details to display.',
    '等待任务接入': 'Waiting for a task', '等待任务接入后，这里会出现多个执行单元的协同状态。': 'Execution units will appear here when a task is received.',
    '手机端下发语音任务后，左侧会展示多个执行任务的实时状态。': 'After a voice task is sent from mobile, live execution status appears on the left.',
    '点击右侧任务卡后，这里会显示对应任务的执行过程和结果。': 'Select a task card to view its execution details and result.',
    '当前状态': 'Current status', '任务事件': 'Task event', '执行单元': 'Execution unit', '任务头像': 'Task avatar',
    '素材结果': 'Asset result', '媒体预览': 'Media preview', '图片结果': 'Image result', '查看视频': 'View video',
    '放大查看': 'Open full size', '打开': 'Open', '预览链接': 'Preview link', '调用记录': 'Call log',
    '查询进度': 'Check progress', '复制 task_id': 'Copy task ID', '刚刚查询': 'Just checked',
    '共': 'Total', '个生成任务': ' generated tasks', '个任务': ' tasks', '个节点': ' nodes', '个下级动作': ' child actions',
    '上一页': 'Previous', '下一页': 'Next', '第': 'Page ', '页': '', '（共': ' (total ', '）': ')',
    '素材': 'Asset', '图片': 'Image', '视频': 'Video', '音频': 'Audio', '文案': 'Copy', '资料': 'Resource',
    '模板': 'Template', '模板名称': 'Template name', '使用': 'Use', '创建模板': 'Create template', '模板已创建': 'Template created',
    '模板已删除': 'Template deleted', '模板加载失败': 'Failed to load templates', '未命名': 'Untitled', '未命名模板': 'Untitled template',
    '未命名文件': 'Untitled file', '新对话': 'New chat', '删除会话': 'Delete chat', '语音任务': 'Voice task',
    '等待转入执行单元': 'Waiting for execution', '输入': 'Input', '参数': 'Parameters', '输出': 'Output', '输出文档': 'Output document',
    '查看文档': 'View document', '导出 TXT': 'Export TXT', '导出 Excel': 'Export Excel', '复制名单': 'Copy list',
    '复制全部': 'Copy all', '下载报告': 'Download report', '复制报告': 'Copy report', '开始采集': 'Start collection',
    '采集明细': 'Collection details', '运行进度': 'Run progress', '采集条件': 'Collection criteria', '未配置采集条件': 'Collection criteria not configured',
    '个人': 'People', '公司': 'Companies', '关键词': 'Keywords', '话题': 'Topics', '评论': 'Comments', '点赞': 'Likes',
    '关注': 'Follow', '私信': 'Direct messages', '账号': 'Account', '客户': 'Customers', '联系人': 'Contacts',
    'LinkedIn采集模板': 'LinkedIn collection template', '请输入': 'Please enter', '请输入关键词': 'Enter keywords',
    '请输入搜索词': 'Enter search terms', '请输入本次销售工作流从第几天开始执行（1-30）': 'Enter the start day for this sales workflow (1-30)',
    '执行天数请输入 1 到 30 的整数': 'Execution day must be an integer from 1 to 30', '请先选择账号': 'Select an account first',
    '请先选择一个账号': 'Select an account first', '请稍后重试': 'Please try again later', '请先勾选同意承诺。': 'Please check the consent box first.',
    '系统配置': 'System configuration', '保存系统别名': 'Save system alias', '随机更换': 'Generate another ID',
    '生成中': 'Generating', '检查中...': 'Checking...', '重启中…': 'Restarting...', '重启 OpenClaw': 'Restart OpenClaw',
    '运行组件': 'Runtime component', '正常': 'Healthy', '修复中...': 'Repairing...', '修复失败': 'Repair failed', '查看修复日志': 'View repair log',
    '默认下载目录': 'Default download directory', '服务器检测到这个槽位还被其他账号/设备记录使用，建议点击“随机更换”。': 'The server found this slot is used by another account or device. Generate another ID.',
    '服务器已确认当前槽位可用于当前账号。': 'The server confirmed this slot is available for this account.',
    '首次生成和随机更换都会向服务器确认唯一性。': 'The server checks uniqueness when generating or replacing the ID.',
    '加载生成历史失败': 'Failed to load generation history', '生成历史': 'Generation history', '生成任务': 'Generation task',
    '模型': 'Model', '算力': 'Credits', '查询进度': 'Check progress', '打开完整分镜工作台': 'Open full storyboard studio',
    '视频号功能敬请期待': 'Channels support coming soon', '敬请期待': 'Coming soon', '下一级': 'Next level', '添加下级': 'Add child action',
    '添加节点': 'Add node', '还没有节点，点击“添加节点”开始配置。': 'No nodes yet. Click “Add node” to configure one.',
    '当前账号没有可访问的员工模板。': 'No employee templates are available for this account.', '他人授权': 'Shared with me',
    '系统员工': 'System employee', '我的模板': 'My template', '当前员工': 'Current employee', '未启用': 'Not enabled',
    '已启用': 'Enabled', '演示': 'Demo', '销售': 'Sales', '工作节点': 'Workflow node', '编辑节点': 'Edit node',
    '删除节点': 'Delete node', '演示节点': 'Run demo', '开始时间': 'Start time', '结束时间': 'End time',
    '保存员工': 'Save employee', '员工已保存': 'Employee saved', '员工已删除': 'Employee deleted', '员工已停用': 'Employee disabled',
    '员工已恢复': 'Employee restored', '没有可用微信号通讯录，或没有匹配联系人': 'No available WeChat contacts or no matching contacts',
    '折叠': 'Collapse', '展开': 'Expand', '刷新记录': 'Refresh records', '刷新状态': 'Refresh status',
    // Keep common composite labels intact so mixed Chinese/English strings do not render as
    // "11 位Phone Number" or "图形Verification Code".
    'AI员工': 'AI Employee', 'AI员工解决方案': 'AI employee solutions', '您的私人 AI 助手': 'Your private AI assistant',
    '智能对话': 'AI chat', '智能对话、技能商店、发布中心、系统配置等功能': 'AI chat, Skills, Publishing, System configuration and more',
    '登录后可使用智能对话、技能商店、发布中心、系统配置等功能': 'After signing in, you can use AI chat, Skills, Publishing, System configuration and more',
    '11 位手机号': '11-digit phone number', '11 位中国大陆手机号': '11-digit mainland China phone number',
    '请输入 11 位中国大陆手机号': 'Enter an 11-digit mainland China phone number', '图形验证码': 'Captcha',
    '短信验证码': 'SMS verification code', '6 位短信码': '6-digit SMS code', '发短信前填写': 'Enter before sending SMS',
    '发短信前填': 'Enter before sending SMS', '点击刷新': 'Click to refresh', '换一张': 'Use another',
    '手机号注册和登录': 'Phone sign-in', '登录后可使用': 'After signing in, you can use', '数字人': 'Digital human',
    '数字人口播': 'Digital human narration', '图片数字人创建': 'Create image digital human', '视频数字人创建': 'Create video digital human',
    '声音创建': 'Create voice', '创建声音失败': 'Failed to create voice', '请填写数字人名称。': 'Enter a digital human name.',
    '请输入数字人名称': 'Enter a digital human name', '请先上传视频。': 'Upload a video first.',
    '图形验证码加载失败': 'Failed to load captcha', '验证码加载失败': 'Failed to load captcha',
    '网络连接失败，请检查后端是否已启动或网络是否正常': 'Network connection failed. Check that the backend is running and the network is available.',
    '当前未检测到本机后端地址。': 'No local backend address detected.', '未配置服务器 API_BASE': 'Server API_BASE is not configured',
    '提交后这里会自动显示任务进度和最终结果。': 'Task progress and the final result will appear here after submission.',
    '任务状态已刷新。': 'Task status refreshed.', '任务查询失败': 'Task query failed', '历史任务加载失败': 'Failed to load task history',
    '素材库暂无图片': 'No images in the asset library', '素材库图片加载失败': 'Failed to load asset library images',
    '图片暂不可预览': 'Image preview is unavailable', '没有匹配的图片': 'No matching images', '张图片，当前显示': ' images, currently showing',
    '正在加载素材库图片...': 'Loading asset library images...', '正在查询可用能力…': 'Checking available capabilities...',
    '能力列表已获取': 'Capability list loaded', '正在提交视频生成任务…': 'Submitting video generation task...',
    '正在提交图片生成任务…': 'Submitting image generation task...', '正在请求模型撰写回复…': 'Asking the model to draft a reply...',
    '可上传本地音频，或直接使用电脑麦克风录音。': 'Upload local audio or record directly with your microphone.',
    '录音时间太短，请至少录制 3 秒。': 'The recording is too short. Record at least 3 seconds.',
    '当前浏览器不支持直接录音，请改用本地音频上传。': 'This browser cannot record directly. Upload a local audio file instead.',
    '已选择本地音频，可直接提交创建声音。': 'Local audio selected. You can submit it to create a voice.',
    '请先生成': 'Generate something first', '请先添加或选择一个抖音账号。': 'Add or select a Douyin account first.',
    '同步失败': 'Sync failed', '已加入队列：': 'Added to queue: ', '任务失败：': 'Task failed: ',
    '任务提交失败，请查看下方回复': 'Task submission failed. Check the response below.', '提交未成功：': 'Submission failed: '
    ,
    // Main workspace, sidebar and conversation chrome.
    'AI调度助手': 'AI Assistant', 'IP人设定位': 'IP Persona', 'AI秘书': 'AI Secretary',
    '我的AI员工': 'My AI Employees', 'AI营销创作': 'AI Marketing', 'AI获客': 'AI Lead Generation',
    '私域销管': 'Private-domain CRM', 'AI海外平台': 'Global Platforms', '会话': 'Conversations',
    '搜索会话...': 'Search conversations...', '搜索会话': 'Search conversations', '系统任务': 'System Task',
    '需要确认': 'Confirmation Required', '完全访问': 'Full Access', '执行任务前询问': 'Ask before running tasks',
    '直接执行任务': 'Run tasks automatically', '与 H5 共享会话': 'Shared with H5', '当前会话': 'Current conversation',
    '新建会话': 'New conversation', '开始一轮新的对话': 'Start a new conversation', '随心输入': 'Type anything',
    '添加素材': 'Add assets', '对话模式': 'Chat mode', '智能对话': 'AI Chat', '默认模式': 'Default mode',
    '记忆': 'Memory', '默认记忆': 'Default memory', '系统记忆': 'System memory', '不使用资料': 'No memory',
    '同步记忆': 'Sync memory', '同步个人记忆': 'Sync personal memory', '使用说明': 'Help',
    'AI 聚合': 'AI Aggregate', '对话模型': 'Chat model', '您好，我是 AI 员工': 'Hello, I am your AI Employee',
    '您好，我是': 'Hello, I am', 'AI 员工': 'AI Employee', '我可以帮您：': 'I can help you with:',
    '文案 / 图片 / 视频': 'Copy / Images / Video', '抖音线索跟进': 'Douyin lead follow-up',
    '个微接管协同': 'WeChat customer engagement', '选择下方功能，或直接对我说出您的需求吧~': 'Choose a capability below or tell me what you need.',
    '网页应用': 'Web Apps', '移动应用': 'Mobile Apps', '小程序': 'Mini Programs', '智能体': 'Agents',
    '工作台状态': 'Workbench status', '测试模式': 'Test mode', '当前未强制校验龙虾登录态': 'Lobster sign-in is not enforced',
    '视频合成': 'Video Composition', '当前模式：视频合成': 'Current mode: Video Composition',
    '直接在下面输入一句话，我会按当前模式帮您开始创作。': 'Describe what you need below and I will create it in the current mode.',
    '进入工作台': 'Open Workbench', '精选案例': 'Featured Examples', '关闭当前模式': 'Close current mode',
    '告诉我您想做什么？我会尽力帮您完成~': 'Tell me what you want to do. I will help you complete it.',
    '告诉我您想做什么？我会先帮您理清任务，再继续生成和执行~': 'Tell me what you want to do. I will clarify the task before generating and executing it.',
    '发送消息或输入 / 选择技能': 'Send a message or choose a skill', '上传附件': 'Upload attachment',
    '深度思考': 'Deep Reasoning', '发送': 'Send', '收起导航': 'Collapse navigation', '功能导航': 'Navigation',
    '销售': 'Sales', 'AI设计图': 'AI Image Design', '数字人口播视频': 'Digital Human Video',
    '同城爆款视频': 'Local Viral Video', '公众号文章': 'WeChat Article', '抖音获客': 'Douyin Lead Generation',
    '个微': 'WeChat', 'Facebook客服': 'Facebook Support', '阿里询盘接管': 'Alibaba Inquiry Assistant',
    'LinkedIn线索挖掘': 'LinkedIn Lead Mining', 'TikTok线索采集': 'TikTok Lead Collection',
    'Reddit线索采集': 'Reddit Lead Collection', 'X线索采集': 'X Lead Collection',

    // Skill store cards and descriptions. These values can arrive from the server at runtime.
    'AI 模型能力': 'AI Model Capabilities', '图片、视频、音频创作': 'Image, video and audio creation',
    '智能视频 2.5': 'Smart Video 2.5', '智能辅助功能': 'AI-powered tools', '爆款TVC': 'Viral TVC',
    '一键生成爆款视频': 'Generate viral videos in one click', '创意分镜头视频': 'Creative Storyboard Video',
    '生成分镜视频': 'Generate storyboard videos', '模板定制': 'Template Studio', '爆款视频复刻': 'Viral Video Remix',
    '复刻视频风格': 'Recreate a video style', '智能剪辑': 'Smart Editing', '数字人口播混剪': 'Digital human video editing',
    '多段视频混剪': 'Multi-clip Video Mixer', '选段、拼接、配乐和模板成片': 'Select, combine, score and apply templates',
    '选段、拼接、配乐和模板成片': 'Select, combine, score and apply templates', '电商详情页': 'E-commerce Detail Page',
    '生成商品套图': 'Generate product image sets', '数字人口播': 'Digital Human Narration', '生成数字人口播': 'Generate digital human narration',
    '微信助手': 'WeChat Assistant', '微信通道授权': 'Authorize the WeChat channel', '写文、配图、推草稿': 'Write, illustrate and publish drafts',
    '管理本机资料': 'Manage local resources', '浏览器自动操作': 'Browser automation', '电脑自动操作': 'Computer automation',
    'YouTube 上传': 'YouTube Upload', '管理视频发布': 'Manage video publishing', '管理社媒发布': 'Manage social publishing',
    'WhatsApp 客服': 'WhatsApp Support', 'Facebook Messenger 客服': 'Facebook Messenger Support',
    'Messenger 自动回复': 'Messenger auto-reply', '企业微信自动回复': 'WeCom Auto-reply', '企微客服回复': 'WeCom customer support',
    '商品发布': 'Product Publishing', '管理店铺账号': 'Manage store accounts', '榜单、同行、记忆生成文案': 'Create copy from trends, competitors and memory',
    '画像、候选人、图谱和报告': 'Profiles, candidates, relationship maps and reports', '微信协议助手': 'WeChat Protocol Assistant',
    '本机微信连接工作台': 'Local WeChat connection workbench', 'PPT 生成': 'PPT Generation', '主题生成演示文稿': 'Generate presentations from a topic',
    '业务自动化工具': 'Business automation tools', '技能工具': 'Skill Tool', '已安装': 'Installed', '安装': 'Install',
    '打开': 'Open', '配置': 'Configure', '进入工作台': 'Open Workbench',
    '采集Reddit公开搜索、趋势、Community、Account、帖子评论数据。': 'Collect public Reddit search, trends, communities, accounts, posts and comments.',
    '采集TikTok公开视频、Account、评论数据，并按关键词分析精准用户。': 'Collect public TikTok videos, accounts and comments, then identify target users by keyword.',
    '接入阿里国际站询盘：账号扫码登录、滚动同步历史询盘、本机接管回复。': 'Connect Alibaba.com inquiries with QR sign-in, history sync and local assisted replies.',
    '使用本机 MsgHelper 识别已登录微信，执行通讯录、群发、私信等操作。': 'Use local MsgHelper to access signed-in WeChat for contacts, bulk messages and direct messages.',
    '输入视频号昵称查询账号，拉取作品并批量提取口播文案。': 'Find a Channels account by name, fetch its videos and extract narration copy in bulk.',
    '管理本机资料': 'Manage local resources', '发抖音': 'Publish to Douyin', '发小红书': 'Publish to Xiaohongshu',
    '生成商品套图': 'Generate product image sets', '榜单、同行、记忆生成文案': 'Generate copy from trends, competitors and memory',
    'IP日更文案': 'Daily IP Copy', '云端工作台': 'Cloud Workbench', '技能': 'Skills', '新会话': 'New chat',
    '高质量 3D Model': 'High-quality 3D Model', '3D 模型': '3D Model',
    '海报、详情页、朋友圈': 'Posters, detail pages and Moments', '图片、视频、音频创作': 'Image, video and audio creation',
    '生成商品套图': 'Generate product image sets', '智能视频': 'Smart Video', '模板定制': 'Template Studio',
    '视频号文案提取': 'Channels Copy Extraction', '个人记忆': 'Personal Memory', '微信协议助手': 'WeChat Protocol Assistant',
    '可配置': 'Configurable', '进入配置': 'Configure', '管理店铺账号': 'Manage Store Accounts', '去对话生成': 'Generate in Chat',
    '卸载': 'Uninstall', '即将推出': 'Coming Soon', '算力解锁': 'Unlock with Credits', '个能力': ' capabilities', '调试': 'Debug',
    '独立 OpenClaw 浏览器工作台': 'Standalone OpenClaw browser workbench', '独立 OpenClaw 电脑操作工作台': 'Standalone OpenClaw computer workbench',
    '接入阿里国际站询盘：账号登录、滚动同步历史询盘、客户档案、AI话术总结和回复辅助。': 'Connect Alibaba.com inquiries with account sign-in, history sync, customer profiles, AI summaries and reply assistance.'
  };

  var EN = Object.assign({}, GENERATED_EN, CURATED_EN);
  var ICON_EN = { '调': 'AI', '秘': 'S', '销': 'S', '日': 'D', '图': 'I', '人': 'H', '城': 'L', '镜': 'V', '文': 'W', '抖': 'D', '微': 'W', '阿': 'A' };

  var PHRASES = Object.keys(CURATED_EN).sort(function(a, b) { return b.length - a.length; });

  var ATTRS = ['placeholder', 'title', 'aria-label', 'alt'];

  function normalizeLanguage(value) { return SUPPORTED[value] ? value : DEFAULT_LANGUAGE; }
  function readStoredLanguage(userId) {
    try {
      var scoped = userId ? localStorage.getItem(USER_STORAGE_PREFIX + userId) : '';
      return normalizeLanguage(scoped || localStorage.getItem(STORAGE_KEY) || DEFAULT_LANGUAGE);
    } catch (e) { return DEFAULT_LANGUAGE; }
  }
  var language = readStoredLanguage('');

  function translated(value) {
    if (language === DEFAULT_LANGUAGE || !value) return value;
    var stringValue = String(value);
    var core = stringValue.trim();
    if (!core) return value;
    var next = EN[core];
    if (!next) {
      var countMatch = core.match(/^共\s*(\d+)\s*(条|个|项|人|次|份|页)$/);
      if (countMatch) next = countMatch[1] + ' total';
      var pageMatch = core.match(/^第\s*(\d+)\s*页$/);
      if (pageMatch) next = 'Page ' + pageMatch[1];
      var pageRangeMatch = core.match(/^第\s*(\d+)\s*\/\s*(\d+)\s*页$/);
      if (pageRangeMatch) next = 'Page ' + pageRangeMatch[1] + ' / ' + pageRangeMatch[2];
      var taskCountMatch = core.match(/^共\s*(\d+)\s*个(?:生成)?任务$/);
      if (taskCountMatch) next = taskCountMatch[1] + ' tasks';
    }
    if (next) return stringValue.slice(0, stringValue.indexOf(core)) + next + stringValue.slice(stringValue.indexOf(core) + core.length);
    // Dynamic values split UI sentences into fragments. Translate those
    // fragments with direct catalog lookups before the curated fallback.
    var generatedReplaced = stringValue.replace(/[\u4e00-\u9fff][\u4e00-\u9fffA-Za-z \t，。！？：；、（）《》【】“”‘’·+\-/%&,.!?]{0,159}/g, function(segment) {
      var leading = (segment.match(/^\s*/) || [''])[0];
      var trailing = (segment.match(/\s*$/) || [''])[0];
      var end = segment.length - trailing.length;
      var segmentCore = segment.slice(leading.length, end > leading.length ? end : segment.length);
      return leading + (GENERATED_EN[segmentCore] || EN[segmentCore] || segmentCore) + trailing;
    });
    if (generatedReplaced !== stringValue) return generatedReplaced;
    // Most secondary screens use composed labels (for example
    // "保存登录密码" or "生成公众号文章"). Replace known UI phrases from
    // longest to shortest so the whole control is translated without touching
    // free-form user content.
    var replaced = stringValue;
    PHRASES.forEach(function(phrase) {
      if (phrase.length < 2 || replaced.indexOf(phrase) < 0) return;
      // Do not replace a short phrase in the middle of a longer Chinese sentence;
      // composite labels are listed above and should be translated as a whole.
      var cursor = 0;
      var out = '';
      var hit;
      while ((hit = replaced.indexOf(phrase, cursor)) >= 0) {
        var before = hit > 0 ? replaced.charAt(hit - 1) : '';
        var after = replaced.charAt(hit + phrase.length);
        var adjacentChinese = /[\u4e00-\u9fff]/;
        if ((before && adjacentChinese.test(before)) || (after && adjacentChinese.test(after))) {
          out += replaced.slice(cursor, hit + phrase.length);
        } else {
          out += replaced.slice(cursor, hit) + EN[phrase];
        }
        cursor = hit + phrase.length;
      }
      if (cursor) replaced = out + replaced.slice(cursor);
    });
    if (replaced !== stringValue) return replaced;
    return value;
  }

  function translateTextNode(node) {
    if (!node || node.nodeType !== Node.TEXT_NODE || !node.parentElement) return;
    if (node.parentElement.closest('script,style,textarea,pre,code,[contenteditable="true"],[data-i18n-skip],.chat-msg,.chat-message,.chat-bubble,.chat-msg-body,.chat-msg-text,.li-main-text,.li-raw-details,.user-content,[data-user-content],[data-copy-text]')) return;
    var value = node.nodeValue || '';
    var state = textState.get(node);
    if (!state) state = { original: value, rendered: value };
    else if (value !== state.rendered && value !== state.original) state = { original: value, rendered: value };
    var originalCore = state.original.trim();
    var iconTranslation = node.parentElement.classList.contains('chat-sidebar-entry-icon') ? ICON_EN[originalCore] : '';
    var next = iconTranslation ? state.original.replace(originalCore, iconTranslation) : translated(state.original);
    state.rendered = next;
    textState.set(node, state);
    if (value !== next) node.nodeValue = next;
  }

  function translateElementAttrs(el) {
    if (!el || el.nodeType !== Node.ELEMENT_NODE || el.matches('[data-i18n-skip]')) return;
    var state = attrState.get(el) || {};
    ATTRS.forEach(function(attr) {
      if (!el.hasAttribute(attr)) return;
      var value = el.getAttribute(attr) || '';
      var item = state[attr];
      if (!item || (value !== item.rendered && value !== item.original)) item = { original: value, rendered: value };
      var next = translated(item.original);
      item.rendered = next;
      state[attr] = item;
      if (value !== next) el.setAttribute(attr, next);
    });
    attrState.set(el, state);
  }

  function apply(root) {
    root = root || document.body;
    if (!root) return;
    applying = true;
    try {
      translateElementAttrs(root);
      var walker = document.createTreeWalker(root, NodeFilter.SHOW_ELEMENT | NodeFilter.SHOW_TEXT);
      var node;
      while ((node = walker.nextNode())) {
        if (node.nodeType === Node.TEXT_NODE) translateTextNode(node);
        else translateElementAttrs(node);
      }
      document.documentElement.lang = language;
      document.documentElement.setAttribute('data-language', language);
      var select = document.getElementById('headerLanguageSelect');
      if (select && select.value !== language) select.value = language;
    } finally { applying = false; }
  }

  function scheduleApply() {
    if (applying || scheduled) return;
    scheduled = true;
    requestAnimationFrame(function() { scheduled = false; apply(document.body); });
  }

  function saveLocal(next) {
    try {
      localStorage.setItem(STORAGE_KEY, next);
      if (currentUserId) localStorage.setItem(USER_STORAGE_PREFIX + currentUserId, next);
    } catch (e) {}
  }

  function persistRemote(next) {
    var authToken = typeof window.getStoredAuthToken === 'function' ? window.getStoredAuthToken() : '';
    var base = String(window.__API_BASE || '').replace(/\/$/, '');
    if (!authToken || !base) return Promise.resolve(false);
    var headers = typeof window.authHeaders === 'function'
      ? window.authHeaders()
      : { 'Authorization': 'Bearer ' + authToken, 'Content-Type': 'application/json' };
    headers['Content-Type'] = 'application/json';
    return fetch(base + '/api/settings', { method: 'POST', headers: headers, body: JSON.stringify({ language: next }) })
      .then(function(resp) { return resp.ok; }).catch(function() { return false; });
  }

  function setLanguage(next, options) {
    options = options || {};
    next = normalizeLanguage(next);
    var changed = next !== language;
    language = next;
    saveLocal(next);
    apply(document.body);
    if (changed) window.dispatchEvent(new CustomEvent('lobster:languagechange', { detail: { language: next } }));
    return options.persist === false ? Promise.resolve(true) : persistRemote(next);
  }

  function syncUser(userId, serverLanguage) {
    currentUserId = String(userId || '');
    var next = SUPPORTED[serverLanguage] ? serverLanguage : readStoredLanguage(currentUserId);
    return setLanguage(next, { persist: false });
  }

  window.LobsterI18n = {
    apply: apply,
    getLanguage: function() { return language; },
    setLanguage: setLanguage,
    syncUser: syncUser,
    t: translated,
    supported: ['zh-CN', 'en-US']
  };

  var observer = new MutationObserver(function(records) {
    if (applying) return;
    for (var i = 0; i < records.length; i += 1) {
      if (records[i].type === 'characterData' || records[i].addedNodes.length) { scheduleApply(); break; }
    }
  });
  function start() {
    apply(document.body);
    observer.observe(document.body, { childList: true, subtree: true, characterData: true });
  }
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start, { once: true });
  else start();
})();
