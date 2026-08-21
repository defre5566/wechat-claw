# vendor/wechat_agent_sdk · 版本与补丁记录

| 项 | 值 |
|---|---|
| 上游项目 | wechat-agent-sdk（PyPI: wechat-agent-sdk） |
| 上游版本 | **0.2.1**（vendor/wechat_agent_sdk/pyproject.toml） |
| vendor 形态 | 全量快照（`pip install -e .` 可直装 + patches 双形态同步） |
| 快照日期 | 2026-08（随项目仓库同步维护） |

## 补丁清单（patches/apply_patches.py，共 10 组）

site-packages 形态（用户机 `pip install wechat-agent-sdk` 后）由
`patches/apply_patches.py` 逐组打上；vendor 快照已预打。升级上游 SDK 时：

1. 以版本 `git diff 上游0.2.1 → 新版本` 评估行文变化（anchor = 字符串匹配，
   上游一动即 `unknown` 失败，属预期机制）
2. 改动后运行 `patches/apply_patches.py --check-only`（vendor + site-packages 双核对）

| # | 文件 | 内容 | 说明 |
|---|---|---|---|
| ① | media/cdn.py | aes_key_b64 格式 B（hex→base64） | 微信端解密必需 |
| ② | transport.py | _build_media_item 加 file_size | 发文件缺长度 |
| ②b | transport.py | file_item 加 len | 同上 |
| ③ | media/cdn.py | download URL quote 转义 | 下载链接带特殊字符 |
| ④ | media/cdn.py | import quote | ③ 的符号依赖 |
| ⑤ | account/storage.py | accounts.json chmod 0600 | 敏感凭据收紧权限 |
| ⑥ | account/storage.py | import os | ⑦ 的符号依赖 |
| ⑦ | account/storage.py | 存储目录经 WECHAT_AGENT_SDK_STATE_DIR 重定向 | 收敛到数据根 |
| ⑧ | transport.py | 公共 restore_token() | bridge 不再摸私有成员（F6.5） |
| ⑨⑩ | account/storage.py | accounts.json AES-GCM 静态加密 + 明文兼容 | F6.2 第三层（密钥 = WECHAT_AGENT_SDK_KEY_FILE） |

## 环境变量契约（bridge.config 启动时 setdefault）

- `WECHAT_AGENT_SDK_STATE_DIR` → 数据根 agent-SDK/（账号状态收敛）
- `WECHAT_AGENT_SDK_KEY_FILE` → 数据根 .config/crypto.key（存储加密）

升级 SDK 时保持这两个契约不变；由此处驱动统一改动点。