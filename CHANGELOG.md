# Changelog

wechat-claw 发布变更记录。格式约定：`## [版本] - 日期` 二级标题节，release note
由 release CI 按 tag 版本号自动提取对应节（提取失败则发布中止）。

## [0.1.6] - 2026-09-01

> 首个正式版

### Added

- 全索引化架构：模块指引不再常驻 system prompt，索引位置表按需装载；配套模糊索引
  兜底与人设档位阶梯（会话越深层次越足）——真实对话回复从 73~204s 压回 ~20s 档
- 推送独立渲染：提醒/早报走一次性会话（单轮无工具、人设驱动语气、段落结构保留、
  防发疯保险丝），与入站对话彻底解耦
- 调度自适应降级：agent 调度载体失败自动切换 bridge 内置调度，引擎告警三层直达微信
- 模块兼容门禁 bridge_compat：跨基线模块启用被拦并明示原因（强制声明）
- 渲染模型可切换：acp.model 配置，web 高级设置下拉（动态候选 + 缓存）
- web 初始化向导全流程（体检/opencode 安装/装配/配置生成/扫码登录/拉起服务）+
  Windows 一键清洁安装脚本 clean-install.ps1
- agent job 调度跨平台化：systemd / launchd / schtasks 精确定时器 + 执行器 Python 化；
  Windows 自启动真开关（UAC 提权升级系统服务）
- exe 打包 + 首启种子化：Windows 产物捆绑 opencode，零外部安装
- 模块源拉取零 git 化（GitHub ZIP 归档）+ 国内镜像轮询
- schema 表单全类型渲染：choice chips / number / boolean 胶囊 / tags

### Fixed

- issue #4~#13：兼容门禁、自适应降级、模块设置渲染（choice/number 静默丢弃）、
  高级设置弹窗组名错位（保存从未生效的陈旧 bug）、动态卡片事件委托、worker 路径
  大小写回退、logs_tail 未定义等
- agent job：模板级业务开关（briefing_on 关闭不再空跑）、启停登记错误浮出
- tier 生成管线：staging 越权写入隔离与快照恢复、跨设备 EXDEV、预算放宽与语义保全
- opencode 安装/捆绑：多镜像轮询、装后验证防假成功、静默拉起、zip 精确解压
- exe 升级路径崩溃（sys.executable str 误调）+ 版本比较元组化（0.1.10 不再误判）
- 向导交互实测闭环：二维码流程、安装状态实时、城市库三级联动、版本检测首屏卡死

### Changed

- 版本号收敛：唯一真源 bridge/config.py VERSION，发版只改一处
- 直发目录默认收紧：未配置免确认目录时文件发送走微信确认；modules_data 模块产物直发
- 模块启停联动：AGENTS/索引位置表插拔、启停响应统一浮出 job 登记错误

### Security

- SDK 登录凭证静态加密（AES-GCM，密钥经环境注入）与旧明文兼容读回转密
- 拒发目录收敛（accounts.json / agent-SDK / SDK 凭证目录）+ 路径规范化硬拒
- 测试防线：登录态目录隔离 + conftest 全局哨兵（防测试删改真实登录数据）

### ⚠️ 升级注意

- 本版本启用模块兼容门禁，**必须同步更新模块库**（module.json 声明 bridge_compat），
  否则已启用模块会被拦/停摆
- 数据备份 = 打包平台数据根目录（Windows `%LOCALAPPDATA%\wechat-claw` /
  Linux `~/.local/share/wechat-claw` / macOS `~/Library/Application Support/wechat-claw`）