# ACC Telemetry 发版与自动更新操作指南（SOP）

本项目用 **Tauri v2 + GitHub Actions + tauri-plugin-updater** 实现自动打包与静默更新。
每次发新版按本文顺序执行即可。

流程口诀：**改版本 → 写日志 → 推代码 → 打标签 → 点发布**

---

## 0. 先了解这套流水线

| 环节         | 具体事实（与本仓库一致）                                                                                       |
| ---------- | -------------------------------------------------------------------------------------------------- |
| 触发条件       | 推送形如 `v*` 的 tag（如 `v0.3.2`）才会触发 [`.github/workflows/release.yml`](.github/workflows/release.yml)   |
| 构建机器       | `windows-latest`（仅 Windows 安装包）                                                                    |
| 构建顺序       | 装 Node 20 / Rust / Python 3.10 → 用 PyInstaller 打包 Python sidecar → `tauri-apps/tauri-action` 编译安装包 |
| 产物         | NSIS `.exe` 安装包、MSI、各自对应的 `.sig` 签名文件，以及 `latest.json`                                             |
| Release 状态 | 自动创建为 **Draft（草稿）**，名字形如 `ACC Telemetry v0.3.2`                                                    |
| 更新检查地址     | `https://github.com/hello-0202/acc-telemetry/releases/latest/download/latest.json`                 |
| 更新器约束      | `requireSignedVersion: true`，`installMode: passive` —— 没有有效签名的包会被客户端拒绝                             |

也就是说：**tag 一推，云端自动打包；但只有你手动点 Publish，老用户才查得到更新。**

---

## 1. 前置检查

- [ ] 本地代码已测试通过，工作区没有未提交的严重问题。
- [ ] GitHub 仓库 `Settings → Secrets and variables → Actions` 里仍有：
  
      `TAURI_SIGNING_PRIVATE_KEY`、`TAURI_SIGNING_PRIVATE_KEY_PASSWORD`。
- [ ] 本地私钥（`~/.tauri/*.key` 之类）的密码与上面 Secrets 中的密码一致。
- [ ] 你有推送 `main` 和推送 tag 的权限。

---

## 2. 标准发版 5 步

### 第 1 步：改版本号（两处必须一致）

版本号遵循 [语义化版本](https://semver.org/lang/zh-CN/)：修 bug 升第三位，加功能升第二位并把第三位归零，破坏性改动升第一位。

需要同时修改，且**两处完全相同**：

1. [`package.json`](package.json) 的 `"version"`
2. [`src-tauri/tauri.conf.json`](src-tauri/tauri.conf.json) 的 `"version"`

例如 `0.3.1` → `0.3.2`：

```json
"version": "0.3.2"
```

改完用一条命令核对（两行应输出同一个版本号）：

```bash
node -p "require('./package.json').version"; node -p "require('./src-tauri/tauri.conf.json').version"
```

> ⚠️ `tauri.conf.json` 的版本号决定安装包里的版本，tag 名（`v0.3.2`）必须与它对应；
> 两者不一致时，tauri-action 会报错或产生版本对不上的包。

### 第 2 步：更新 CHANGELOG

打开 [`CHANGELOG.md`](CHANGELOG.md)，在 `## [Unreleased]` 下方新增本版小节，沿用现有中文小标题风格：

```markdown
## [0.3.2] - 2026-10-27

**新增**

- 新增了 xxx 功能

**修复**

- 修复了 xxx 问题
```

同时把顶部「当前仓库开发版本」和 `[Unreleased]` 里有必要的内容整理一下。

### 第 3 步：提交并推送源码

```bash
git add package.json src-tauri/tauri.conf.json CHANGELOG.md
git commit -m "chore: bump version to 0.3.2"
git push origin main
```

先推代码，确保云端有与 tag 对应的最新源码。

### 第 4 步：打 tag 并推送（触发 CI）

```bash
git tag v0.3.2
git push origin v0.3.2
```

v1.0.0

chore(release): v1.0.0

推送 tag 后，去 GitHub 仓库的 **Actions → Release** 页面，等待工作流跑完（绿色对勾 ✅）。
一次完整构建通常要十几分钟（含 Rust 编译和 PyInstaller 打包）。

### 第 5 步：发布 Draft Release

1. 进入仓库 **Releases** 页面，找到 `ACC Telemetry v0.3.2` 的 **Draft**。
2. 点右上角 ✏️，把更新说明补上（中英文皆可，可摘要 CHANGELOG）。
3. 确认没有勾选 **Pre-release**。
4. 点绿色 **Publish release**。

> ⚠️ **最关键的一步就是点击 Publish。**
> 草稿或预发布不会出现在 `releases/latest`，老版本客户端将永远提示「已是最新版本」。

### 第 6 步：验证更新链路（可选但强烈建议）

```bash
curl -L https://github.com/hello-0202/acc-telemetry/releases/latest/download/latest.json
```

返回的 `version` 应为 `0.3.2`；然后再用上一个版本的老客户端点一次「检查更新」确认能识别到。

---

## 3. 常见问题与急救

### 3.1 Tag 打错了 / 代码没改完就打了 tag

删掉 tag，修好代码后重新打：

```bash
# 删除本地 tag
git tag -d v0.3.2
# 删除远端 tag
git push origin :refs/tags/v0.3.2
# 修复代码、提交后重新打
git tag v0.3.2
git push origin v0.3.2
```

同时把 GitHub 上残留的 Draft Release 删除或改名，避免和重新构建的产物混淆。
若已发布（Published）且已有用户更新，**不要再复用同一版本号**，直接发 `0.3.3`。

### 3.2 发布后客户端仍提示「已是最新版本」

按顺序排查：

1. Release 是否停在 **Draft**（未点 Publish）。
2. 是否误勾了 **Pre-release** —— 默认更新地址只认最新正式版。
3. `latest.json` 的 `version` 是否真的更新了（用上面的 `curl`，加 `-H 'Cache-Control: no-cache'` 或浏览器 Ctrl+F5）。
4. 客户端是否被 `requireSignedVersion: true` 拒收：确认 CI 用的签名密钥与 `tauri.conf.json` 里的 `pubkey` 匹配。

### 3.3 GitHub Actions 报错 `Wrong password for that key`

本地私钥密码与 GitHub Secrets 中的 `TAURI_SIGNING_PRIVATE_KEY_PASSWORD` 不一致。
去 `Settings → Secrets` 重新设置正确的密码后，删除并重推 tag 重新构建。

### 3.4 构建失败在 Python sidecar 步骤

CI 会执行：

```bash
cd python-server
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt pyinstaller
.venv/Scripts/python -m PyInstaller telemetry_core.spec --noconfirm --workpath build_new --distpath dist_new
cp dist_new/telemetry_core.exe ../src-tauri/binaries/telemetry_core-x86_64-pc-windows-msvc.exe
```

先在本地跑一遍同样的命令能通过，再发版。

### 3.5 版本号回退/重复

不要手工把版本改回旧值（例如当前已发布过 `0.3.1`，就不能再用 `0.3.1` 发包）。
每次发版只做「旧版本 +1」，并在发版前用第 1 步的核对命令确认两处一致。

---

## 4. 发版检查清单（可复制）

```
[ ] package.json 与 tauri.conf.json 版本号一致，且大于上一版
[ ] CHANGELOG.md 已新增本版条目
[ ] 已提交并推送到 main
[ ] git tag vX.Y.Z 已创建并推送到远端
[ ] Actions Release 工作流成功
[ ] Draft Release 说明已填写、未勾 Pre-release
[ ] 已点击 Publish release
[ ] latest.json 中的 version 正确
[ ] 老版本客户端能检测到更新
```

| 类型 (Type)      | 中文含义           | 典型使用场景                                                                                                                              |
| -------------- | -------------- | ----------------------------------------------------------------------------------------------------------------------------------- |
| **`feat`**     | 新功能            | 添加用户注册模块、新增 API 接口[](https://raw.githubusercontent.com/jnMetaCode/superpowers-zh/HEAD/skills/chinese-commit-conventions/SKILL.md#1) |
| **`fix`**      | 修复缺陷 / Bug     | 修复登录页白屏、解决兼容性问题[](https://raw.githubusercontent.com/jnMetaCode/superpowers-zh/HEAD/skills/chinese-commit-conventions/SKILL.md#1)    |
| **`docs`**     | 文档变更           | 更新 README、API 接口文档、教程[](https://www.conventionalcommits.org/zh-hans/v1.0.0/)                                                        |
| **`style`**    | 代码格式（不影响逻辑）    | 调整缩进、补充分号、删除多余空行[](https://www.conventionalcommits.org/zh-hans/v1.0.0/)                                                             |
| **`refactor`** | 代码重构（非新功能、非修复） | 拆分过长的服务类、重命名变量，不改变外部行为[](https://www.conventionalcommits.org/zh-hans/v1.0.0/)                                                       |
| **`perf`**     | 性能优化           | 优化首页列表查询速度、减少内存占用[](https://www.conventionalcommits.org/zh-hans/v1.0.0/)                                                            |
| **`test`**     | 测试相关           | 补充用户模块单元测试、修改测试用例[](https://www.conventionalcommits.org/zh-hans/v1.0.0/)                                                            |
| **`build`**    | 构建系统或依赖变更      | 升级 webpack 版本、修改 npm 依赖库[](https://www.conventionalcommits.org/zh-hans/v1.0.0/)                                                     |
| **`ci`**       | CI/CD 流水线变更    | 修改 GitHub Actions、Travis 或 Jenkins 配置[](https://www.conventionalcommits.org/zh-hans/v1.0.0/)                                        |
| **`chore`**    | 日常维护 / 杂项      | 构建流程、工具配置的调整，或不归属以上类型的修改[](https://www.conventionalcommits.org/zh-hans/v1.0.0/)                                                     |
| **`revert`**   | 回滚提交           | 撤销之前某次特定的 commit                                                                                                                    |
| **`security`** | 安全修复           | 修复 XSS 漏洞、补丁安全缺陷（部分团队扩展使用）                                                                                                          |

### 💡 补充说明

- **破坏性变更（BREAKING CHANGE）**：它不是一个独立的 `type`，而是一种**标记**。你可以在任意类型的提交中，通过 `!` 符号或脚注中的 `BREAKING CHANGE:` 来标注破坏性 API 变更。例如：`feat!: 移除旧的登录接口`。[](https://www.conventionalcommits.org/zh-hans/v1.0.0/)
