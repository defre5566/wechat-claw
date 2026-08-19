# wechat-claw

微信主动推送体系：微信 ← iLink Bot API ← wechat-agent-sdk ← **opencode 对话 agent**。
消息收发、定时推送、权限确认全走微信，agent 可主动关心、可干活，部署者以"对话"管理自己的数字助理。

## 特性

- **微信直连**：iLink Bot API 直连，扫码即用，无需服务器/公网域名
- **5 小时会话窗**：同号延续，超时自动归档
- **微信确认权限**：高危操作（写文件/发文件）发微信问"允许/拒绝"，30s 无回复默认拒绝
- **主动推送**：HTTP 入口 `/push`（token 鉴权）→ 队列 → 分流 direct / file / agent
- **通用调度引擎**：模块自描述（module.json 声明调度/重试），bridge 零业务知识
- **自描述模块**：新模块写好 worker + module.json 即自动接入，bridge 零改动
- **基础设施配置化**：`<项目根>/.config/config.yaml` 覆盖用户段默认值（运行参数 bridge 内置），不配也能跑
- **web 向导 + 管理后台**：初始化向导（体检/opencode/装配/配置/扫码登录/拉起）+ 管理后台（主题/用户与助理/模块管理，管理密码保护）
- **SDK vendor 快照**：wechat-agent-sdk 0.2.1 全量进仓库，生产补丁预打，安装即修复
- **Windows 服务零依赖**：NSSM 2.24 二进制随仓库 vendor，nssm 服务化免安装

## 架构

```
微信 ← iLink Bot API ← wechat-agent-sdk transport ← opencode ACP 子进程
                              ↑                          ↑
                    推送入口 /push（9898）        消息处理 / 权限确认
                              ↑
               ┌──────────────┴───────────────┐
               │ bridge/（基础设施，零业务知识）│
               │  main · push_server · scheduler · session │
               └──────────────┬───────────────┘
                              │ build_index() 读
               ┌──────────────┴───────────────┐
               │ modules/（业务模块，自描述）  │
               │  <模块> + module.json + common │
               └──────────────────────────────┘
```

分层原则：**bridge** = 基础设施（调度/推送/会话/权限，不认识任何模块业务）；**modules** = 业务（worker + 自描述配置）；**common** = 数据公共库（任务解析/天气/防重 IO/日志/加解密）。

## 前置依赖

| 依赖 | 版本 | 说明 |
|---|---|---|
| Python | >= 3.11 | 运行环境 |
| opencode | 最新 | 对话 agent 运行时（`acp.command` 可配路径）；**web 初始化向导自动检测安装** |

## 安装方式

### 方式 A：源码部署（当前推荐）

```bash
git clone <仓库地址> wechat-claw && cd wechat-claw
python3 -m venv .venv
.venv/bin/pip install -r requirements.txt
.venv/bin/pip install -e ./vendor/wechat_agent_sdk   # vendor 快照（补丁已预打）
.venv/bin/python patches/apply_patches.py --check-only  # 期望全部 [SKIP] 已打
```

守护进程与扫码登录见下方"基础设置"与 [docs/开发文档-03](docs/开发文档-03-操作手册.md)（Linux systemd / macOS launchd / Windows nssm）。

### 方式 B：可执行文件

下载对应平台可执行文件（Release 附件）→ 放到任意目录 → 运行 → **web 初始化向导**完成
环境装配（opencode 安装 / 配置生成 / 扫码登录 / 拉起服务）。

- Linux/macOS：`./wechat-claw`；Windows：`wechat-claw.exe`
- 用户数据（`.config/`）落在**可执行文件所在目录**，备份 = 打包该目录
- opencode 不捆绑：向导引导安装官方版本
- 构建：`.venv/bin/python scripts/build.py`（PyInstaller，不能交叉编译，各平台各自构建）

## 基础设置（两种安装方式共用）

### 1. 配置文件

复制 [config.yaml.example](config.yaml.example) 到项目根 `.config/config.yaml`。**不放置 = 用户段全部使用内置默认值**，本步可选；或用 `web/start.sh`（Linux/macOS）`web/start.bat`（Windows）启动初始化向导自动生成。

### 2. 登录（扫码）

- **主路径**：web 初始化向导扫码登录（`web/start.sh` 启动，第 5 步）
- **当前可用（过渡）**：在项目根执行一次（token 保存后长期有效，失效时重跑）：

```bash
.venv/bin/python - <<'EOF'
import asyncio
from wechat_agent_sdk.transport import WeChatTransport
asyncio.run(WeChatTransport(account_id="default").login_terminal())
EOF
```

### 3. 对话 agent（opencode ACP）

| 文件 | 作用 |
|---|---|
| [AGENTS.md](AGENTS.md) | 对话 agent 系统提示**模板**（身份/守则/安全红线占位符） |
| [AGENTS-example.md](AGENTS-example.md) | 填好默认中立人设的**参考实例**，照此客制化自己的 AGENTS.md |
| [opencode.jsonc.example](opencode.jsonc.example) | 对话 agent **权限配置**（deny 四项 token/密钥），复制为项目根 `opencode.jsonc` 生效 |

### 4. 守护进程

- Linux：systemd user 服务（`wechat-bridge.service`，Restart=on-failure + StartLimit）
- macOS：launchd plist
- Windows：nssm 服务化（`vendor/nssm/` 已随仓库自带，无需另行安装）

三平台完整配置见 [docs/开发文档-03](docs/开发文档-03-操作手册.md)。

### 5. 验证

- 日志出现 `[weixin] 已连接 iLink` + `[acp] Connection initialized` + `[push] HTTP 入口`
- 微信发一条消息，agent 正常回复

## 安全模型

- **deny 四项**：对话 agent 不可读 `modules/**/token`、`agent-SDK/push_token`、`anniversaries.json.enc`、`.config/crypto.key`、`.config/admin.password`（opencode.jsonc.example 默认配置）
- **文件发送三级**：个人目录直发 / 其余路径微信确认（30s 无回复拒绝）/ token 密钥类硬拒（bridge/paths.py 单点执行，路径规范化防 `../` 与符号链接绕过）
- **/push 鉴权**：Bearer token sha256 命中模块索引哈希放行，401 记日志（IP + token 前 4 位）
- **资源保护**：/push body 上限 100MB（413）、队列有界（满 503）
- **S1 防护**：会话失效 → critical 日志 + 非零退出 → systemd StartLimit 终止循环重启
- **隐私数据**：AES-GCM 加密存储（common/crypto.py），密钥 chmod 600 + deny

## 模块开发

- 标准骨架：`modules/<name>/<name>_worker.py` + `module.json`（调度/重试自声明）+ `规范.md`
- 铁律摘要：业务知识只进 worker + module.json；调度以 module.json 为唯一事实源；
  跨模块数据只走 common；失败 return 1（scheduler 感知补发）；测试用 `--dry-run`
- 完整规范见 [docs/开发文档-04](docs/开发文档-04-模块开发规范.md)

## 目录结构

```
├── AGENTS.md / AGENTS-example.md   # 对话 agent 系统提示模板 / 实例
├── opencode.jsonc.example          # 对话 agent 权限配置示例
├── config.yaml.example             # 用户配置示例（运行参数 bridge 内置）
├── bridge/                         # 基础设施：main / push_server / scheduler / session / state
├── modules/                        # 业务模块 + common（数据公共库）+ register.py + registry_index.py
├── vendor/wechat_agent_sdk/        # wechat-agent-sdk 0.2.1 全量快照（补丁预打）
├── vendor/nssm/                    # NSSM 2.24 二进制（Windows 服务化，public domain）
├── web/                            # 初始化向导 + 管理后台（launcher/wizard/handlers/static）
│   └── static/cities.json           # 城市库（省市区 + 拼音 + 中心坐标，来源 xiangyuecn/AreaCity-JsSpider-StatsGov，MIT）
├── .config/                        # 用户配置目录（部署后生成，备份 = 打包此目录）
├── patches/apply_patches.py        # SDK 补丁（生产 pip 安装重打用；分发侧作校验器）
├── docs/                           # 开发文档 01-04
└── tests/                          # 纯函数回归（pytest）
```

## 文档导航

| 文档 | 内容 |
|---|---|
| [开发文档-01](docs/开发文档-01-总览与设计.md) | 架构总览 / 进程端口 / 调度机制 / 权限体系 / 演进记录 |
| [开发文档-02](docs/开发文档-02-组件参考.md) | SDK 补丁 / bridge 四大块 / common / /push 协议 |
| [开发文档-03](docs/开发文档-03-操作手册.md) | 从零复现 / systemd·launchd·nssm / 加模块 / 运维排障 |
| [开发文档-04](docs/开发文档-04-模块开发规范.md) | 模块标准骨架 / module.json 格式 / 铁律 / common 边界 |

## 规划中

- **web 初始化向导与管理后台**：已实现（web/，`web/start.sh` / `web/start.bat` 启动）；P1 管理后台增强（模块源下载、日志可视化、schema 表单）规划中
  - 城市库与定位授权已实现：`web/static/cities.json`（省市区三级 + 拼音 + 中心坐标，天气按区级坐标查询、定位按最近区县匹配；数据源自 [xiangyuecn/AreaCity-JsSpider-StatsGov](https://github.com/xiangyuecn/AreaCity-JsSpider-StatsGov)（MIT，原始数据民政部/国家地名信息库/统计局/高德/腾讯，2026-04-03 版），仅此文件为第三方派生数据）
- **可执行文件打包**：单文件产物 + 向导装配（README 方式 B）