# wechat-claw · 开发环境说明

> 本文件只服务本仓库的开发会话（本机 opencode 加载）。
> 部署态对话 agent 与此文件无关：其指引经数据根 `instructions/` 目录由 opencode.jsonc
> 的 `instructions` 数组装载（全索引化，正文按需读，不内联）。

## 项目

wechat-claw：微信主动推送体系。bridge 引擎（消息收发/调度/推送）+ 自描述业务模块
（modules/<name>/，含 module.json 声明 + worker + agents.md 指引）。

## 结构速览

- `bridge/` — 主引擎：main（装配/入站/推送消费）、session（ACP 会话/权限确认门）、
  push_server（/push 入口）、push_render（推送单轮渲染）、scheduler、module_source
- `modules/` — register.py（模块唯一管理入口）、registry_index.py（实时索引）、
  common/（模块共享库）、modules_data/（运行时数据区）
- `web/` — 管理后台与初始化向导；agent_gen.py（人设字段 + instructions 生成）
- `vendor/wechat_agent_sdk/` — 微信 SDK（含 ACP 适配层）
- `docs/开发文档-0{1..4}` — 总览/组件/操作/模块规范
- `devlog/` — 开发日志（不入 git）；当前改造决策见 `devlog/DESIGN-DECISION-INDEXER-260827.md`

## 开发守则

- 改完必须验证：`.venv/bin/python -m pytest tests/ -q` 全绿再交付
- 小步提交；遵循现有风格（中文 docstring、logging、原子写 tmp+rename）
- devlog/ 不入库不推送；决策先落 devlog 再动代码
- 架构决策记录在 devlog，不写死在代码注释里（代码注释只讲当前行为）

## 安全红线（开发环境同样适用）

- 不读取/转发：modules/**/token、agent-SDK/push_token、anniversaries.json.enc、
  .config/crypto.key、.config/admin.password、~/.wechat-agent-sdk/accounts.json
- 本仓 opencode.jsonc 为本地私有配置（gitignore），不入库

## 当前改造上下文（260827）

全索引化路线执行中：推送独立单轮渲染（已完成）、instructions 索引生成（已完成）、
索引器 v0 与会话装配待做。模块↔indexer 接口规格为挂起议题，未经讨论不得实现。
