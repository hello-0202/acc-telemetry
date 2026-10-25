# ACC 遥测后端（asyncio 生产者-消费者）

单进程全异步结构，采集 / 记录 / 广播三条路径彻底解耦。

```
采集 Task (100Hz)
  ├─ latest_buffer.append(frame)               → 广播 Task (50Hz) → WebSocket 帧
  ├─ frame_queue.put_nowait(FramePacket)       → 记录 Task → LapProcessor + SQLite
  └─ （一次性事件走 event_queue）               → 广播 Task → session/lap/trackmap/...
```

## 目录

| 文件 | 职责 |
|---|---|
| `bus.py` | 有界队列 `frame_queue(2000)` + 环形缓冲 `latest_buffer(50)` + `event_queue` + 统计 |
| `collector/reader.py` | 采集 Task：管理共享内存开关、读 Snapshot、生成 frame、非阻塞分发 |
| `storage/recorder.py` | 记录 Task：圈次识别、PB、落库、自动建图，单写者 |
| `storage/writer.py` | 通用的「批量帧写库」示例（本应用不用） |
| `api/broadcaster.py` | 维护 WebSocket 连接，推最新帧 + 一次性事件，超时踢人 |
| `api/routes.py` | `/health`、`/ws/telemetry`（start/stop 命令） |
| `api/server.py` | 装配 FastAPI 生命周期，启动/停止三个 Task |
| `main.py` | 可选启动入口 |

历史 REST（`/api/sessions*`、`/api/config`）复用 `acc_telemetry.api` 下的路由。

## 三条硬约束

1. 采集端只用 `put_nowait`，队列满丢帧并计数，绝不 `await put`
2. 只有 `Recorder` 写 SQLite，通过 `asyncio.to_thread` 执行阻塞写
3. 广播按连接 `create_task` 发送，单连接超时 1s 后从 `connections` 移除

## 生命周期

- 启动：record Task → broadcast Task → collect Task（下游先就绪）
- 停止：`stop_event.set()` → 采集退出 → 记录排空队列 → 广播退出
- WebSocket 断开最后一个连接时，自动结束当前记录会话

## 健康指标

`GET /health` 在原有字段外增加：

```json
{
  "connections": 1,
  "queueSize": 0,
  "queueMax": 2000,
  "bufferSize": 50,
  "producedFrames": 0,
  "droppedFrames": 0,
  "writtenFrames": 0,
  "lapsRecorded": 0
}
```

`droppedFrames` 持续增长说明下游跟不上（一般是磁盘写入过慢），
需要检查 `Recorder` 的落库耗时或调大队列。

## 打包

`server.py` 已经改为导入 `backend.api.server.app`：

```bash
cd python-server
pyinstaller telemetry_core.spec
copy dist\telemetry_core.exe ..\src-tauri\binaries\telemetry_core-x86_64-pc-windows-msvc.exe
```
