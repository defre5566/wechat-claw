"""wechat-claw bridge：基础设施（微信 → opencode 桥接 + 主动推送底座）。

四大块：
- main.py        入口编排（组装 + 消息循环 + 后台协程）
- push_server.py HTTP 推送入口（鉴权 / 校验 / 入队）
- scheduler.py   通用调度引擎（读 registry index，不认识模块业务）
- session.py     会话与消息面（会话管理 / 权限门 / ACP 修复 / 媒体）
"""