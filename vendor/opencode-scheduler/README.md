# opencode-scheduler（vendor）

**opencode agent 任务的定时执行 supervisor**（agent 型长任务的执行器）。

## 来源与授权

- 源码来自生产库部署机 `~/.config/opencode/scheduler/supervisor.pl`
  （"opencode-scheduler supervisor v1"，自研 perl 脚本，MIT 随项目分发）
- 生产验证形态：systemd user timer（如 `opencode-job-politics-*.timer`，每天 08:25）
  触发 supervisor → 执行 `opencode run --title <slug> -- <prompt>` → 记录 runs/locks

## 依赖

- **perl 标准库**（JSON::PP / File::Basename / File::Path / POSIX / Time::HiRes）
- Linux / macOS 自带 perl，零额外依赖
- Windows 无 perl：需安装 Strawberry Perl（一次性），或用计划任务调 perl

## 架构

```
systemd timer（OnCalendar，由 bridge/opencode_jobs.py 生成）
  → /usr/bin/perl supervisor.pl <job.json>
    → 锁防重（同任务不并发）
    → 更新 job 状态（lastRunStatus）
    → fork 执行 opencode run -- prompt（超时 SIGTERM→SIGKILL，超时记 124）
    → 写 runs/<slug>.jsonl 运行记录 → 清锁
```

## job 定义（JSON）

```json
{
  "name": "任务名",
  "slug": "<模块名>-<任务名>",
  "scopeId": "wechat-claw",
  "schedule": "50 8 * * *",
  "timeoutSeconds": 1800,
  "workdir": "/path/to/project",
  "run": {"title": "<slug>", "prompt": "..."},
  "invocation": {"command": "/usr/bin/opencode", "args": ["run", "--title", "<slug>", "--", "<prompt>"]}
}
```

job 生命周期（install/uninstall/list）由 `bridge/opencode_jobs.py` 管理，
scopeId 固定 `wechat-claw`、slug 前缀 `<模块名>-`（定稿命名），
模块可选携带 `job.template.json`（agent 型长任务模板）登记为正式 job。

## 运行时目录（不进仓库，部署时生成）

`~/.config/opencode/scheduler/scopes/wechat-claw/{jobs,runs,locks}/`
