<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="assets/icon-dark.svg">
    <img src="assets/icon-light.svg" alt="wechat-claw" width="280">
  </picture>
</p>

<p align="center"><b><big>wechat-claw</big></b></p>

<p align="center">
  <a href="https://github.com/defre5566/wechat-claw/releases"><img src="https://img.shields.io/github/v/release/defre5566/wechat-claw" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green.svg" alt="License: MIT"></a>
  <a href="https://github.com/defre5566/wechat-claw/releases"><img src="https://img.shields.io/badge/Windows%20%7C%20macOS%20%7C%20Linux-blue" alt="Platform"></a>
  <a href="https://www.python.org/"><img src="https://img.shields.io/badge/Python-3.11%2B-green" alt="Python"></a>
</p>

---

**易用、安全、只属于你——基于微信官方 ClawBot 协议的 agent 助理，扫码即用。**

wechat-claw 把 AI 助理放进你的微信：陪你聊天，定时给你发消息，重要操作先经你同意。装它不需要服务器、不需要域名、不需要写代码：下载、扫码、开聊。

[快速开始](#快速开始) · [操作手册](docs/开发文档-03-操作手册.md) · [模块开发](docs/开发文档-04-模块开发规范.md) · [变更日志](CHANGELOG.md)

## 快速开始

1. 到 [Releases](https://github.com/defre5566/wechat-claw/releases) 下载对应平台文件：Windows `wechat-claw.exe` · Linux deb · macOS 单文件
2. 运行，浏览器打开初始化向导（`127.0.0.1:8650`）
3. 向导依次完成：环境检查 → agent 安装 → 配置生成 → 扫码登录 → 服务启动
4. 用微信扫码，给自己发一条消息，助理上线

<details>
<summary>源码部署 / 手动配置（进阶）</summary>

```bash
git clone https://github.com/defre5566/wechat-claw.git && cd wechat-claw
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e ./vendor/wechat_agent_sdk
```

守护进程与登录细节见[操作手册](docs/开发文档-03-操作手册.md)（systemd / launchd / nssm）。
</details>

## 它能做什么

- **陪你聊天**：像真人一样有来有回，会查、会算、会用工具。
- **定时推送**：到点把消息发进你的微信；你写的脚本，也能借它发。
- **权限管理**：写文件、发文件前先在微信问你，不回复就当拒绝。

让助理更懂你——易用好用的模块一键装：即插即用，支持热插拔。主体功能不依赖模块——你的助理你掌握。

## 为什么是官方通道

wechat-claw 使用微信官方开放的 Bot 接口（微信 ClawBot，底层 iLink 协议），扫码登录。代码全开源，装了什么、干了什么，你随时看得见；数据只在你自己的机器上，登录凭据加密存放。

| 接入方式 | 官方 | 需要服务器 | 需要自己开发 |
|---|---|---|---|
| 微信官方 ClawBot / iLink（本方案） | ✅ | 不需要 | 不需要 |
| 网页协议模拟 | ❌ | 视方案 | 是 |
| 客户端注入 | ❌ | Windows 主机 | 是 |
| 通用 agent 平台的微信通道 | 部分 | 视方案 | 是 |

## 给开发者

- 模块 = 一个 worker + 一份 module.json 声明（调度、重试、权限都写在声明里），核心引擎零改动
- 官方模块库一键装（现含 Planner 简报、todo）；模块包带 sha256 与 Ed25519 签名校验，防篡改
- 工程信号：实测对话回复 ~20s 档 · 36 个测试文件 · 三平台 CI · 4 册中文开发文档

## 架构

```
微信 ← 官方 ClawBot / iLink ← bridge ← agent（ACP 子进程）
                               ↑
                          modules（业务模块，声明式）
                               ↑
                          官方模块库（签名校验）
```

bridge 是基础设施：调度、推送、会话、权限，不认识任何模块业务；业务全部在模块里，以 module.json 声明接入。

## 文档

| 文档 | 内容 |
|---|---|
| [开发文档-01](docs/开发文档-01-总览与设计.md) | 架构总览 / 进程端口 / 调度机制 / 权限体系 / 演进记录 |
| [开发文档-02](docs/开发文档-02-组件参考.md) | SDK 补丁 / bridge 组件 / /push 协议 |
| [开发文档-03](docs/开发文档-03-操作手册.md) | 从零复现 / 三平台服务化 / 运维排障 |
| [开发文档-04](docs/开发文档-04-模块开发规范.md) | 模块骨架 / module.json 格式 / 开发铁律 |

## License

MIT。第三方数据：`web/static/cities.json`（省市区坐标库，MIT，原始数据源自民政部/国家地名信息库/统计局/高德/腾讯）。

本项目代码部分由 vibe coding 实现。