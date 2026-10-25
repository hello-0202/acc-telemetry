<div align="center">
  <img src="./logo.png" width="120" alt="ACC Telemetry Logo" />
  <h1>ACC Telemetry</h1>
  <p><strong>A lightweight, local-first telemetry tool for Assetto Corsa Competizione.</strong></p>
  <p><strong>一款面向 Assetto Corsa Competizione 的轻量级本地遥测工具。</strong></p>

  [![Tauri](https://img.shields.io/badge/Tauri-v2.0-blue?logo=tauri&style=flat-square)](#)
  [![Vue3](https://img.shields.io/badge/Vue-3.x-42b883?logo=vue.js&style=flat-square)](#)
  [![Python](https://img.shields.io/badge/Python-3.10+-yellow?logo=python&style=flat-square)](#)
  [![License](https://img.shields.io/badge/License-MIT-green.svg?style=flat-square)](#)
</div>

---

## 📖 About (关于项目)

**English:**
ACC Telemetry is a lightweight, local-first telemetry tool for Assetto Corsa Competizione. It reads ACC's shared memory directly, records lap times and pedal inputs into a local SQLite database, and needs little setup. The aim is to keep things simple so you can review your driving data without extra fuss.

**中文:**
ACC Telemetry 是一款面向 Assetto Corsa Competizione 的轻量级本地遥测工具。它直接读取 ACC 的共享内存，将圈速与踏板数据记录到本地 SQLite 数据库，配置简单。目标是把事情做简单，方便你回看自己的驾驶数据。

### ✨ Key Features (核心特性)
-  **Real-time Dashboard**: Live HUD for secondary screens. (副屏实时数据面板)
-  **Lap Recording**: Auto-detects lap boundaries and saves to local SQLite. (自动切分圈次并保存到本地数据库)
-  **Data Visualization**: Charts for lap comparison and track heatmaps. (多圈对比曲线与赛道热力图)

---

## 📂 Project Structure (项目结构)

```
acc-telemetry/
├── src/                    # Vue 3 前端（实时面板 / 历史 / 设置）
├── src-tauri/              # Tauri (Rust) 外壳，负责拉起 Python sidecar
│   ├── src/                # Rust 源码（main.rs / lib.rs）
│   ├── capabilities/       # Tauri 权限声明
│   ├── icons/              # 应用图标
│   ├── binaries/           # 打包产物 telemetry_core.exe（gitignore，由 CI 生成）
│   ├── Cargo.toml          # Rust 依赖与版本号
│   └── tauri.conf.json     # Tauri 打包配置（版本号真源）
├── python-server/          # 后端：读 ACC 共享内存 + WebSocket/REST + SQLite
│   ├── server.py           # 后端入口（FastAPI 生命周期）
│   ├── acc_telemetry/      # 业务包（provider / repository / service）
│   ├── backend/            # 生产者-消费者运行时（collector → bus → recorder）
│   ├── pyACCSharedMemory/  # 第三方：ACC 共享内存 Python 绑定（vendored，见其 License.txt）
│   ├── tools/              # 赛道地图采集脚本
│   ├── telemetry_core.spec # PyInstaller 打包配置（生成 sidecar）
│   └── requirements.txt    # Python 运行依赖
├── .github/workflows/      # CI：打 v* tag 自动构建并出 Release 草稿
├── docs/                   # 设计说明与前后端数据契约文档
├── public/                 # 前端静态资源
├── 资料/                    # 参考资料（ACC 共享内存文档、分析笔记等，不参与构建）
├── index.html              # Vite 入口
├── package.json            # 前端依赖与版本号
├── vite.config.js          # Vite 配置
├── LICENSE                 # MIT 协议
├── CHANGELOG.md            # 版本变更记录
└── README.md
```

> **Note / 说明**：`资料/` 文件夹包含项目参考资料（如 ACC 共享内存官方文档、功能分析），仅用于阅读，**不是运行或构建所必需**，已随仓库保留。`pyACCSharedMemory/` 为 vendored 的第三方依赖。

---

## 🚀 Quick Start (快速开始)

### For Players (普通玩家)

Download the release build, launch it, and start ACC. The tool reads the shared memory automatically and shows a live dashboard on a secondary screen; lap and pedal data are saved locally with zero configuration.

下载发布版安装包，启动后开启 ACC 即可。工具会自动读取共享内存并在副屏展示实时数据，圈速与踏板数据本地保存，无需任何配置。

### For Developers (开发者)

环境要求 / Prerequisites: **Node.js 18+**, **Rust (Tauri v2 toolchain)**, **Python 3.10+**.

```bash

```

---

## 🔒 Data & Privacy (数据与隐私)
所有遥测数据均存储在**本地 SQLite 数据库**中，不会上传任何服务器。你可以随时在设置中查看、导出或删除记录。

All telemetry data is stored in a **local SQLite database** and never uploaded to any server. You can view, export, or delete your records anytime from Settings.

---

## 📝 License (协议)
本项目以 **MIT License** 开源，详见 [LICENSE](./LICENSE) 文件。

This project is open-sourced under the **MIT License** — see the [LICENSE](./LICENSE) file.

---

<div align="center">
  <i>Built with ❤️ for the Sim Racing Community.</i>
</div>
