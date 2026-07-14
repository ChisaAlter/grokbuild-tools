# Grok Account Manager

桌面工具：管理多个 SuperGrok / xAI 账号，查看额度与 Token 累计消耗，一键切换 Grok Build 登录。

## 功能

- **收录**：从当前 `~/.grok/auth.json` 抓取账号到本地仓库
- **额度**：用账号 token 探测 API rate-limit（请求/Token 剩余与上限、tier）
- **Token 统计**：按账号汇总会话 `updates.jsonl` 中的用量（输入/输出/总计等）
- **一键切换**：只改写 `~/.grok/auth.json`，**不改** `sessions/`、`config.toml` 等
- **自动重启**：切换后尽量结束并重新启动 `grok`

## 安装

```powershell
cd C:\Ai\grokbuild-tools
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

## 运行

```powershell
python -m grok_account_manager
# 或
grok-account-manager
```

## 使用流程

1. 在 Grok Build 登录账号 A → 打开本工具 → **收录当前 Grok 登录**
2. 在 Grok 中 `grok login` 换账号 B → 再点收录
3. 选中账号 → **刷新额度** / **同步统计**
4. **切换为当前** → 工具写入 `auth.json` 并尝试重启 Grok

## 数据位置

| 路径 | 说明 |
|------|------|
| `%USERPROFILE%\.grok-account-manager\accounts.json` | 多账号凭证与统计（**明文**） |
| `%USERPROFILE%\.grok-account-manager\switch_log.json` | 切换时间线（用于用量归属） |
| `%USERPROFILE%\.grok\auth.json` | Grok 当前登录（切换时覆盖） |
| `%USERPROFILE%\.grok\auth.json.bak` | 切换前备份 |

## 安全说明

- 本地仓库为**明文 JSON**，请勿分享或提交到 Git。
- 日志与界面默认不展示完整 token。
- 切换只动 `auth.json`；会话历史与设置保持原样。

## 统计口径

- **累计 Token**：来自本机会话日志，按 `switch_log` 归属到账号；工具启用前的历史可能进入「未归属」。
- **当前额度窗口**：来自 API 响应头，一般是滑动窗口剩余，**不是**终身总配额。

## 测试

```powershell
pytest -q
```

## 设计文档

- Spec: `docs/superpowers/specs/2026-07-14-grok-account-manager-design.md`
- Plan: `docs/superpowers/plans/2026-07-14-grok-account-manager.md`
