# Grok Account Manager

面向 **Grok Build** 的 Windows 多账号桌面小工具：管理多个 SuperGrok / xAI 登录、查看额度进度条、一键切换当前账号。

> 仓库：https://github.com/ChisaAlter/grokbuild-tools  
> 当前版本：`0.2.0`  
> 非官方工具，与 xAI 无隶属关系。

---

## 功能一览

| 功能 | 说明 |
|------|------|
| 收录账号 | 从当前 `~/.grok/auth.json` 抓取登录信息到本地仓库 |
| 新增登录 | 设备码 + **仅无痕浏览器**登录新账号并自动收录 |
| 一键切换 | 写入目标账号凭证，结束旧 Grok 进程，并用 `grok --continue` 尽量续上原项目最近会话 |
| 额度进度条 | 列表下方显示**剩余额度**比例与百分比（来自 billing 接口） |
| 对话探测 | 刷新额度时检测 Build 对话接口是否可用（✓ / ✗） |
| 定时刷新 | 顶部开关 + 间隔（分钟），自动刷新全部账号额度 |
| 在线更新 | 检查 GitHub Release/Tag，支持 git 或 zip 在线更新 |

**不会修改** Grok 的会话历史目录与 `config.toml`（只动 `auth.json` 及本工具自己的数据目录）。

---

## 环境要求

- Windows 10/11（主要支持平台）
- 已安装 [Grok Build](https://docs.x.ai) CLI（默认 `~\.grok\bin\grok.exe`）
- 新增登录需要本机 **Chrome / Edge / Firefox**（用于无痕窗口）
- 源码运行时另需 Python 3.11+

---

## 安装（推荐：安装包，只有 GUI）

从 [Releases](https://github.com/ChisaAlter/grokbuild-tools/releases) 下载：

| 文件 | 说明 |
|------|------|
| **GrokAccountManager-Setup-0.2.0.exe** | 安装程序。完成后从开始菜单或桌面快捷方式启动，**只有图形界面，不弹黑窗口** |
| **GrokAccountManager-Portable.zip** | 绿色版。解压后双击 `GrokAccountManager.exe` 即可 |

安装包与绿色版均使用 **无控制台窗口** 的打包方式（PyInstaller `--windowed`）。

### 从源码安装（开发 / 调试）

```powershell
git clone https://github.com/ChisaAlter/grokbuild-tools.git
cd grokbuild-tools
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -r requirements.txt
pip install -e .
```

**源码运行（无黑色终端）：** 双击 `start.vbs` 或 `启动.bat`（内部 `pythonw.exe`）。

调试时用带终端的方式：

```powershell
.\.venv\Scripts\Activate.ps1
python -m grok_account_manager
```

### 自行打包 Windows 安装包

需安装 [Inno Setup 6](https://jrsoftware.org/isinfo.php)，然后：

```powershell
powershell -ExecutionPolicy Bypass -File scripts\build_windows.ps1
```

产出：

- `dist\GrokAccountManager-Setup-0.2.0.exe` — 安装包  
- `dist\GrokAccountManager-Portable.zip` — 绿色版  
- `dist\GrokAccountManager\GrokAccountManager.exe` — 纯 GUI 可执行文件

---

## 使用说明

### 1. 收录已有登录

1. 先在本机正常登录 Grok Build  
2. 打开本工具 → 点 **「收录当前 Grok 登录」**  
3. 账号出现在左侧列表  

### 2. 新增账号（推荐无痕设备码）

1. 点 **「新增（登录）」**  
2. 工具会：保存当前号 → 本地 logout → 启动设备码登录  
3. **只在无痕窗口**用新邮箱完成登录（忽略普通浏览器旧登录态）  
4. 成功后左侧应出现新账号（同一邮箱只会更新凭证，不重复加行）  

### 3. 查看额度

- 列表每项下方有**进度条** = **剩余比例**  
- 文案示例：`剩余 14,255/15,000 (95%) · 已用 5%`  
- 点 **刷新全部额度** 或单个 **刷新额度**  
- 可打开顶部 **定时刷新**，填写间隔分钟数  

### 4. 切换账号

1. 选中目标账号 → **切换为当前**  
2. 工具会结束当前 Grok → 写入 `auth.json` → 在原项目目录尽量 `grok --continue`  
3. 请使用**新开的 Grok 窗口**继续对话  
4. 会话文件在 `~\.grok\sessions\`，不会因切换被删除；接不上时可用 `/resume`  

### 5. 检查更新

- 顶栏显示版本号，点 **检查更新**  
- 默认检查仓库：`ChisaAlter/grokbuild-tools`  
- 有新版本可一键更新（git 优先，否则下载 zip），完成后尝试重启应用  

---

## 数据位置

| 路径 | 说明 |
|------|------|
| `%USERPROFILE%\.grok-account-manager\accounts.json` | 多账号凭证与缓存（**明文**，勿外传） |
| `%USERPROFILE%\.grok-account-manager\settings.json` | 定时刷新等设置 |
| `%USERPROFILE%\.grok\auth.json` | Grok 当前登录（切换时覆盖） |
| `%USERPROFILE%\.grok\auth.json.bak` | 切换前备份 |
| `%USERPROFILE%\.grok\sessions\` | 会话历史（本工具不删除） |

---

## 重要说明与限制

1. **切换必须重启 Grok 进程**  
   仅改文件时，运行中的 Grok 常仍用内存里的旧登录，额度会算错号。

2. **有额度 / 有 SuperGrok 展示 ≠ 一定能 Build 对话**  
   个别账号 billing 正常，但 `cli-chat-proxy` 对话接口仍可能 403。以列表「对话✓」和终端实测为准。

3. **refresh_token 可能失效**  
   久置或多次登录后需重新「新增/收录」更新凭证。

4. **本工具非官方**  
   依赖未公开稳定接口，xAI 变更可能导致部分功能失效。

5. **安全**  
   本地明文保存 OAuth 凭证，仅建议自用电脑使用。

---

## 常见问题

**Q: 切换后还是旧号额度？**  
A: 确认用的是切换后新开的 Grok 窗口；旧终端窗口请关掉。

**Q: 新增登录没有多一行？**  
A: 若登录的是列表里已有邮箱，只会更新凭证。需换另一个邮箱才会新增。

**Q: 无痕闪一下 / 还弹普通浏览器？**  
A: 请用面板上的「再开无痕窗口」；普通窗直接关掉，以无痕 + 验证码为准。

**Q: 对话 403 但订阅还在？**  
A: 网页订阅与 Build 对话门禁可能不一致；换「对话✓」的号，或到 console.x.ai 检查该号权益。

---

## 开发与测试

```powershell
pip install -r requirements.txt
pip install -e .
pytest -q
```

主要代码：

```
src/grok_account_manager/
  app.py           # 界面
  auth_bridge.py   # auth.json 读写与切换
  quota.py         # 额度 / 对话探测
  login_flow.py    # 设备码无痕登录
  process.py       # Grok 进程管理
  updater.py       # GitHub 版本检测与更新
  store.py         # 本地仓库
```

---

## 发布新版本

1. 同步修改：
   - `src/grok_account_manager/__init__.py` → `__version__`
   - `pyproject.toml` → `version`
2. 提交并推送  
3. 创建 GitHub Release / Tag（例如 `v0.1.1`）  
4. 用户端点「检查更新」即可拉取  

---

## 许可证

MIT License — 详见 [LICENSE](LICENSE)。

---

## 免责声明

本项目为社区自用工具，与 xAI 无关。使用本工具产生的账号安全、订阅与额度问题由使用者自行承担。请遵守 xAI 服务条款。
