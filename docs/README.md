# 文档索引

> 本目录只保留**现在还会用到**的资料。每个文件在下面标了状态和用途，
> 先看状态再决定要不要读。
>
> 最后整理：2026-11-07

## 状态说明

| 标记 | 含义 |
|---|---|
| ✅ 现行 | 与当前代码一致，改代码时要同步改它 |
| 📚 参考 | 外部规范 / 领域笔记，一般不用改 |
| 🗄 历史 | 已完成任务的记录，状态可能过期，只在追溯「为什么」时看 |
| ⚠️ 构建依赖 | 打包会直接读取，**不要删、不要挪位置** |

---

## 当前文档

| 文件 | 状态 | 用途 |
|---|---|---|
| [`ENGINEERING-NOTES.md`](./ENGINEERING-NOTES.md) | ✅ 现行 | 跨模块踩坑笔记：架构、打包、圈次判定、赛道图、连接层、调试。**改代码前先扫一遍** |
| [`backend-data-contract.md`](./backend-data-contract.md) | ✅ 现行 | 前端 ↔ 后端的 WebSocket `frame` / REST / SQLite 数据契约 |
| [`theme-state-contract.md`](./theme-state-contract.md) | ✅ 现行 | 主题 `state` 契约可读版（字段来源 / 单位 / 语义）+ 主题包格式 |
| [`theme-authoring.md`](./theme-authoring.md) | ✅ 现行 | 写/改主题的教程与症状对照表 |
| [`tyre-wear-model.md`](./tyre-wear-model.md) | ✅ 现行 | **轮胎磨损估算模型**：公式、参数、自校准、局限、给评审的问题 |
| [`HANDOFF.md`](./HANDOFF.md) | 🗄 历史 | 主题化主线的交接记录与真机待验清单；进度部分停留在 2026-10-28，以 `git log` 为准 |
| [`AI_PARSING_NOTES.md`](./AI_PARSING_NOTES.md) | 📚 参考 | ACC `fastlane.ai` 二进制格式逆向笔记（赛道几何解析的实现依据） |
| [`ACCSharedMemoryDocumentationV1.8.12.pdf`](./ACCSharedMemoryDocumentationV1.8.12.pdf) | 📚 参考 | Kunos 官方共享内存字段文档（判断字段可用性的第一依据） |
| [`theme-template/`](./theme-template/) | ⚠️ 构建依赖 | 悬浮窗主题最小模板；`public/templates/theme-template.zip` 由它打包，`docs` 是源 |
| [`theme-template-monitor/`](./theme-template-monitor/) | ⚠️ 构建依赖 | 主面板主题最小模板；同上 |

> 模板目录的内容不要随手改：改完要重新打 `public/templates/*.zip`
> （见 `ENGINEERING-NOTES.md` 第 10 节「主题模板包」），否则设置页「导出模板」导出的还是旧版。

---

## 2026-11-07 清理记录

删除了两份已完成/被取代的资料（仍在 git 历史里，需要时可用
`git show HEAD:docs/HANDOFF-P3-theme-packages.md` 取回）：

| 已删除 | 原因 |
|---|---|
| `HANDOFF-P3-theme-packages.md` | P3 主题包任务（0~7）已全部完成并提交，属于过程性交接记录；实现结论已沉淀进 `theme-authoring.md`、`theme-state-contract.md` 和代码 |
| `MAP_RENDERING_NOTES.md` | 描述的是已不存在的离线绘图脚本（`make_monza_map.py` / `monza_centerline.csv`），且关键结论（法线方向、左右边界、弯心检测）已并入 `ENGINEERING-NOTES.md` 第 4 节 |

同时修正了其它文档里指向这两份文件的失效链接。
