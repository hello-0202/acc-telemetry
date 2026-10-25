# ACC `fastlane.ai` 解析笔记（Monza 实例）

> 目的：记录这份文件的完整二进制布局、逆向过程、验证方法，以及可直接交给 Claude Code 复用的解析方案。
> 文件：`...\AC2\Content\Cache\monza\fastlane.ai`（2,588,352 B）

---

## 0. 结论速查

| 区段 | 偏移 | 大小 | 内容 |
|---|---|---|---|
| Header | `0` | 16 B | `int32 version=8`, `int32 count=1152`, `int64 reserved=0` |
| 第一段：样条点 | `16` | `count × 36` | 位置 + 累计里程 + 索引 |
| 第二段：逐点属性 | `16 + 36·count` | `20 + count × 80` | 曲率半径、左右边界、段长、方向 |
| 第三段：辅助索引 | `36 + 36·count`... 见下 | 剩余 ~2.45 MB | 整数索引表，**未解码** |

Monza 实际值：`count = 1152`，第一段结束 `41488`，第二段记录起点 `41508`，第二段结束 `133668`，第三段 `133668 … EOF`（2,454,684 B）。

> 不要硬编码 41488 / 41508 / 133668。这些都是 `count` 推出来的，换赛道就变：
> `sec2 = 16 + 36*n`，`records2 = sec2 + 20`，`sec3 = sec2 + 20 + 80*n`。

---

## 1. 二进制布局

### 1.1 Header（16 B，小端）

```c
int32 version;      // 0x00，本文件 = 8
int32 point_count;  // 0x04，本文件 = 1152
int64 reserved;     // 0x08，恒为 0
```

### 1.2 第一段：主样条点（每条 36 B，起始 0x10）

```c
struct SplinePoint {   // 36 B
    double x;          // +0   世界坐标 X，米
    double y;          // +8   世界坐标 Y，米，向上（海拔）
    double z;          // +16  世界坐标 Z，米
    double distance;   // +24  从点 0 起的累计弧长，米
    int32  index;      // +32  点序号，0 … count-1
};
```

第 `i` 条偏移 = `16 + 36 * i`。

**重要特征**：这些 `double` 是从 `float32` 提升来的，低 29 位尾数为 0，所以每 8 字节的**低 3 字节几乎总是 `00 00 00`**。扫描文件时可以用这个特征快速定位坐标。

### 1.3 第二段：逐点属性（每条 80 B，起始 `sec2 + 20`）

段头 20 B：

```c
int32 count;        // sec2 + 0，等于 point_count
char  zero[16];     // sec2 + 4，全 0
```

之后是 `count` 条、每条 **20 个 float32**：

| 字段 | 索引 | 含义 | 置信度 |
|---|---|---|---|
| `radius_m` | f0 | 曲率半径（米）。直道很大，弯心很小 | ✅ |
| `left_edge_m` | f1 | 到左侧赛道边缘的垂直距离（米） | ✅ |
| `right_edge_m` | f2 | 到右侧赛道边缘的垂直距离（米） | ✅ |
| `left_edge_dup` | f3 | 与 f1 完全相同 | ✅ |
| `right_edge_dup` | f4 | 与 f2 完全相同 | ✅ |
| — | f5–f9 | 疑似局部坐标系 / 旋转分量 | ❓ |
| `segment_length_m` | f10 | **上一采样点 → 本点**的欧氏距离（点 0 = 0） | ✅ |
| `dir_x/y/z` | f11–f13 | 近似单位方向向量（x/z 与出射弦一致，y 略偏） | ⚠ |
| — | f14 | 通常为 0 | ❓ |
| — | f15 | 小量 | ❓ |
| — | f16–f19 | 填充 / 未使用 | ❓ |

第 `i` 条偏移 = `sec2 + 20 + 80 * i`。

**左右边界的定义**：相对行进方向（索引递增方向）。`left_edge_m + right_edge_m = 当地路宽`。用这两项可以重建真实赛道轮廓。

### 1.4 第三段：辅助索引（`sec3 = sec2 + 20 + 80*n` 起）

- Monza：偏移 `133668`，长度 `2,454,684 B`。
- 开头有 4 个 float（本文件为 `1391.7, -749.2, 0, -1473.7`）、`int32 10`、`float 10.0`。
- 之后是海量 `int32`，取值几乎都在 `0 … count-1`。
- 推测是 AI 邻接表 / 查找表 / 空间分区索引，**结构未解码，解析时请直接跳过**。

---

## 2. 逆向思路（可复用到其它 `.ai`）

1. **看大小与文件头**
   - `ls -l` 得 2,588,352。
   - 前 16 字节 hex dump：`08 00 00 00`、`80 04 00 00`。`0x480 = 1152`，立刻怀疑是 `version + count`。

2. **找第一段的步长**
   - 位置值是 `float32 → double`，用"低 3 字节为 0"作签名扫描，发现坐标三元组按 **36 字节**重复。
   - 用 `count = 1152` 反推第一段结束：`16 + 36×1152 = 41488`。
   - 验证：
     - 第 `i` 条的 `index` 字段严格等于 `i`；
     - `distance` 单调递增；
     - `distance` 差分 ≈ 5 m；
     - 最后累计里程 ≈ 5750 m，首尾闭环距离仅 4 m。

3. **在 41488 找到第二段**
   - 该处又是一个 `int32 1152`，说明下一段也按点数组织。
   - 段头不是 16 B，而是 **20 B**（`count + 16 B 零`）。这是最容易踩的坑。
   - 用"长零串"找步长：从 41508 起，每 **80 字节**出现一次 16 字节零串，共 1152 次 → 每条 80 B = 20 × float32。
   - 段尾：`41508 + 80×1152 = 133668`，与下一段起点吻合。

4. **语义靠交叉验证，不靠猜**
   - **f1 + f2**：每点求和稳定在 8.6–15.1 m，明显是路宽；再检查弯道——AI 线在**弯心更靠近内侧**（左弯时 f1 小、右弯时 f2 小），由此确定 f1=左、f2=右。
   - **f10**：`f10[i]` 与 `|P[i] - P[i-1]|` 完全吻合（`f10[0]=0`），所以是**入段长度**，不是出段。
   - **f0**：Rettifilo ≈ 22.5 m、Roggia ≈ 36.9 m、Lesmo ≈ 62–67 m、Parabolica ≈ 89 m，和弯角曲率半径吻合。
   - **f3 / f4**：逐点与 f1 / f2 完全相等 → 重复字段。
   - **f11–f13**：整体是单位向量，x/z 与出射弦方向一致。
   - **f5–f9**：数值形如 `(0.01, -1, -0.01, 1, 0.004)`，像旋转矩阵/局部坐标系，未能确认。

5. **几何总校验**
   - 全圈长度 5758.86 m（官方 5791 m，AI 线切弯所以略短）。
   - 曲率峰值位置对应 Monza 的弯角顺序：Rettifilo → Curva Grande → Roggia → Lesmo 1/2 → Ascari → Parabolica。
   - 用 f1/f2 重建左右边界后闭合、无翻转，说明左右定义正确。

6. **剩余数据**
   - 从 133668 到 EOF 没有任何 16 字节零串，说明是稠密数据。
   - 末尾出现 `1016, 1019, 1015, 1020, 1014, 1021, 1013, 1022` 这类成对小整数，全部 ≤ 1151 → 点索引表。
   - 未继续解码，绘图与导出都跳过。

---

## 3. 参考解析器（Python，已按此逻辑验证）

```python
import struct


def parse_fastlane_ai(path):
    data = open(path, "rb").read()

    version, n = struct.unpack_from("<2i", data, 0)
    assert version == 8, version
    assert 0 < n < 1_000_000, n

    # ---- 第一段：样条点 ----
    points = []
    off = 16
    for i in range(n):
        x, y, z, dist = struct.unpack_from("<4d", data, off)
        idx = struct.unpack_from("<i", data, off + 32)[0]
        assert idx == i, (i, idx)
        points.append({
            "index": idx,
            "position": {"x": x, "y": y, "z": z},
            "distance_m": dist,
        })
        off += 36

    # ---- 第二段：逐点属性 ----
    sec2 = off                       # 16 + 36*n
    assert struct.unpack_from("<i", data, sec2)[0] == n
    rec = sec2 + 20                  # 段头是 20 B，不是 16 B

    attributes = []
    for i in range(n):
        f = struct.unpack_from("<20f", data, rec + 80 * i)
        attributes.append({
            "radius_m": f[0],
            "left_edge_m": f[1],
            "right_edge_m": f[2],
            "left_edge_dup": f[3],
            "right_edge_dup": f[4],
            "orientation_raw": list(f[5:10]),
            "segment_length_m": f[10],       # i-1 -> i
            "direction": {"x": f[11], "y": f[12], "z": f[13]},
            "unknown_raw": list(f[14:20]),
        })

    sec3 = rec + 80 * n              # 第三段起点，未解码
    return {
        "version": version,
        "count": n,
        "points": points,
        "attributes": attributes,
        "aux_offset": sec3,
    }
```

---

## 4. 校验清单

解析完至少跑这些检查（换赛道也适用）：

- [ ] `version` 与 `count` 合理；
- [ ] 每条 `index == i`；
- [ ] `distance` 严格递增，最后一条 ≈ 赛道长度；
- [ ] `|P[i] - P[i-1]|` ≈ `segment_length_m`；
- [ ] 首尾闭环距离 < 20 m（样条是闭环，但首尾点不一定完全重合）；
- [ ] 每点 `left_edge_m + right_edge_m` 在合理路宽范围（Monza 8.6–15.1 m）；
- [ ] 弯心处半径明显变小；
- [ ] 画出来形状能和真实赛道对上；
- [ ] `sec3 <= filesize`（如果超出，说明版本或字段假设错了）。

Monza 基准值（可用于回归测试）：

```
count          = 1152
lap length     ≈ 5758.86 m
closure gap    ≈ 4.03 m
width          = 8.64 … 15.06 m（均值 10.33）
elevation      = -13.93 … -1.07 m
Rettifilo r    ≈ 22.5 m
Roggia r       ≈ 36.9 m
Lesmo 1 r      ≈ 66.5 m
Lesmo 2 r      ≈ 61.9 m
Parabolica r   ≈ 89.2 m
```

---

## 5. 踩坑记录

1. **第二段段头是 20 B**（`count` + 16 B 零）。按 16 B 读会让所有 float 错位 4 字节。
2. **`segment_length_m` 是入段**（`i-1 → i`），不是出段；点 0 的值为 0。
3. **坐标是 `float64`，但精度只有 `float32`**（低 29 位为 0）。不要期望亚毫米精度。
4. **这是 AI 行车线，不是赛道中心线**。画图时不要标成 "centerline"，它会在弯心贴内线。
5. **点 0 = 起终点线**（样条从计时线开始）。这不是文件里的元数据，是从行为推断的。
6. **世界坐标不是 GPS**。X/Z 是赛道局部坐标（米），Y 向上；转经纬度需要赛道原点和朝向。
7. **坐标轴方向**：直接画 `x, z` 会得到镜像图；用 `x, -z` 才和 Monza 真实朝向一致。换赛道时同样要用弯角形状校验。
8. **第三段没解码**。不要假设里面没有有用数据；目前只知道是 0…n-1 的整数索引。
9. **所有多字节都是小端**（x86）。
10. **偏移全部由 count 推导**，不要复制 Monza 的 41488 / 41508 / 133668 到别的文件。

---

## 6. 直接交给 Claude Code 的提示词

```text
解析 Assetto Corsa Competizione 的 fastlane.ai（二进制、小端）。

已知格式：
- Header 16 B: int32 version, int32 count, int64 reserved(0)
- 第一段 @16，count 条 × 36 B: float64 x, y, z, distance; int32 index
- 第二段 @16+36*count，段头 20 B (int32 count + 16 B 零)，之后 count 条 × 80 B:
  20 × float32，其中 f0=曲率半径(m)，f1=左边距(m)，f2=右边距(m)，
  f3/f4=重复的左右边距，f5-f9=未知旋转分量，f10=入段长度(m)，
  f11-f13=方向向量，f14-f19=未使用/未知
- 第三段 @16+36*count+20+80*count，整数索引表，跳过不解析

要求：
1. 偏移必须从 count 推导，不要硬编码。
2. 解析前校验 version/count；解析中校验 index 连续、distance 递增。
3. 输出 JSON 或 CSV；字段名使用 radius_m / left_edge_m / right_edge_m / width_m /
   segment_length_m / dir_x / dir_y / dir_z。
4. 用 lap_length、闭环距离、路宽做合理性校验，结果打印出来。
5. 地图用 (x, -z) 绘制，Y 为海拔；不要把它当赛道中心线。
```

---

## 7. 仍未确认的部分

- `orientation_raw`（f5–f9）的确切含义；
- `unknown_raw`（f14、f15、f16–f19）；
- 第三段索引表的结构与用途；
- 文件头 `version` 不同版本之间是否字段有变化（本文件为 8）。
