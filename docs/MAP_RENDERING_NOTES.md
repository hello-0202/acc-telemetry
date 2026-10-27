# Monza 赛道地图绘制说明

> 输入：`fastlane.ai` 解析出的 1152 个 AI 样条点 + 每点左右边界距离
> 工具：Python 3.14 + numpy + matplotlib（无 GIS / 无 shapely）
> 脚本：[make_monza_map.py](make_monza_map.py)
> 输出：`monza_track_map.png` / `.svg`、`monza_centerline.csv`

---

## 1. 整体流程

```
解析二进制
  → 取 1152 个世界坐标 (x, y, z)
  → 投影到 2D：(x, -z)，y 作为海拔
  → 求闭合环的切线和法线
  → 用左/右边距生成真实赛道边界，填充成路面
  → 叠加 AI 行车线
  → 曲率分析找弯角 → 标注弯名
  → 画起终点线、方向箭头、比例尺
  → 底部加海拔剖面
  → 输出 PNG + SVG + CSV
```

坐标单位统一为米，因此所有几何（路宽、比例尺）都可以按真实尺寸画。

---

## 2. 坐标变换

```python
X = pts[:, 0]
Y = -pts[:, 2]      # 注意取反
alt = pts[:, 1]     # Y 向上，作为海拔
P = np.column_stack([X, Y])
```

- `Y = -z` 是为了让地图朝向和真实 Monza 一致（主直道在左、Parabolica 在下方）。
- 不取反会得到镜像图，形状仍然像，但左右弯会反过来。
- 判断朝向是否正确的方法：看弯角顺序是否和真实赛道一致（Rettifilo → Curva Grande → Roggia → Lesmo → Ascari → Parabolica）。

---

## 3. 生成赛道带状面（ribbon）

普通赛道图常画成等宽线条，但这份文件里第二段已经给了每个点到左/右边缘的真实距离，所以直接还原真实路面：

```python
# 闭合环切线：把首尾各补一个点，再用中心差分
T = np.gradient(np.vstack([P[-1], P, P[0]]), axis=0)[1:-1]
T /= np.linalg.norm(T, axis=1, keepdims=True)

# 左法线（逆时针旋转 90°）
N = np.column_stack([-T[:, 1], T[:, 0]])

# f1 = left_edge_m，f2 = right_edge_m（解析笔记第二段）
left  = P + N * wl[:, None]
right = P - N * wr[:, None]
ribbon = np.vstack([left, right[::-1]])
```

然后：

```python
ax.add_patch(Polygon(ribbon, closed=True,
                     facecolor="#D3CCBC", edgecolor="#BDB5A3",
                     linewidth=0.6, zorder=1))
```

要点：

- 左右边距不相等，所以路面是**不对称**的——AI 线在弯心贴内侧，这正是真实情况。
- 法线用闭合环差分，首尾不会出现断口。
- 不要用 `linewidth` 来近似路宽：线宽单位是 point，和数据坐标不是一回事。用 offset 多边形才能保证 1 m = 1 m。
- 极端急弯（曲率半径 < 路宽）处多边形理论上可能自交；本例没有明显问题，必要时可以按段切分或做裁剪。

---

## 4. AI 行车线

在路面上再叠一条深色线：

```python
line = np.vstack([P, P[:1]])
ax.plot(line[:, 0], line[:, 1],
        color="#1C1F24", linewidth=1.9, solid_capstyle="round", zorder=3)
```

- 线比路面细，路面负责“赛道感”，线负责“AI 线”这个主题。
- 首尾补一个点，闭合环不开口。

---

## 5. 弯角识别与标注

不手工数点，用三点法算曲率，取峰值聚类：

```python
def curvature(i):
    a = P[i] - P[(i - 3) % n]
    b = P[(i + 3) % n] - P[i]
    return a[0] * b[1] - a[1] * b[0]   # 叉积，正负代表左右

curv = np.array([curvature(i) for i in range(n)])
```

- 平滑后按 `|curv|` 排序；
- 相邻峰值距离小于 15 个点就视为同一个弯；
- 得到 Rettifilo、Curva Grande、Roggia、Lesmo 1/2、Ascari、Parabolica 的位置；
- 再人工对照真实赛道弯序命名。

标注方式：

```python
ax.scatter([X[i]], [Y[i]], s=9, color=ACCENT, ...)
ax.annotate("T11  Parabolica", xy=(X[i], Y[i]),
            xytext=(X[i] + dx, Y[i] + dy),
            arrowprops=dict(arrowstyle="-", color=MUTED, lw=0.7))
```

- 每个弯的标签偏移 `(dx, dy)` 是手工微调的，避免压到赛道；
- 用细引线连到弯心；
- 标签文字用墨色，只有小圆点用强调色。

---

## 6. 起终点线

样条点 0 就是起终点线：

```python
sx, sy = X[0], Y[0]
nx, ny = N[0]
ax.plot([sx - nx * 9, sx + nx * 9],
        [sy - ny * 9, sy + ny * 9],
        color=ACCENT, linewidth=3.2)
```

- 沿法线方向画 18 m 的短线，横跨路面；
- 红色是最重要的强调色，整张图只有起终点、弯心点和标注线用它。

---

## 7. 方向箭头

在几个代表性位置（直道、弯前）沿切线画小箭头：

```python
k = 300
ax.annotate("", xy=(X[k] + T[k,0]*26, Y[k] + T[k,1]*26),
                xytext=(X[k] - T[k,0]*26, Y[k] - T[k,1]*26),
                arrowprops=dict(arrowstyle="-|>", color=MUTED, lw=1.1))
```

用来提示 lap 方向，同时不抢视觉重心。

---

## 8. 比例尺

直接在数据坐标里画：

```python
ax.plot([x0, x0 + 200], [y0, y0], color=INK, lw=2.2)
```

因为 `ax.set_aspect("equal")`，200 个数据单位就是 200 m，不需要换算。

---

## 9. 海拔剖面

底部单独一个子图，横轴是累计里程，纵轴是海拔：

```python
s = np.append(dist, total)
e = np.append(alt, alt[0])
ax2.fill_between(s, e, e.min() - 1.2, color=TEAL, alpha=0.10)
ax2.plot(s, e, color=TEAL, linewidth=1.4)
ax2.axvline(0, color=ACCENT, linewidth=1.6)
```

- 用单色（teal）线 + 极浅填充，避免和地图抢色；
- 起终点位置画一条红色竖线，和地图呼应。

---

## 10. 版式与视觉规范

| 项 | 做法 | 原因 |
|---|---|---|
| 画布 | `9.2 × 13.2 in @200 dpi`，暖白底 `#FAF8F4` | 纵向赛道，留白充足 |
| 主图/剖面 | `GridSpec` 高度比 `5.6 : 1` | 地图是主体，剖面是补充 |
| 纵横比 | `ax.set_aspect("equal")` | 弯角形状不变形，比例尺才有效 |
| 坐标轴 | 全部关掉 | 地图不需要刻度网格 |
| 颜色 | 一个深色墨、一个暖灰路面、一个红色强调、一个 teal 剖面 | 颜色少，信息层级清楚 |
| 文字 | 标题 30 pt、副标题 8.6 pt、弯名 8 pt | 远看有标题，近看有细节 |
| 图例 | 不单独做图例 | 弯名直接标在图上，起终点直接写文字 |
| 数据说明 | 放在赛道内侧空白区 | 平衡画面，顺手解释数据来源 |

标题区右侧放一行等宽小字：

```
5,759 m   1,152 spline pts   format v8
```

---

## 11. 输出

```python
fig.savefig("monza_track_map.png", dpi=200)
fig.savefig("monza_track_map.svg")
```

- PNG 用于直接查看；
- SVG 是矢量，放大到任意尺寸都不会糊；
- 同时把解析后的所有字段写进 `monza_centerline.csv`，方便二次处理。

运行方式：

```bash
python make_monza_map.py \
  "<path>/fastlane.ai" \
  "<output-dir>"
```

---

## 12. 换到其它赛道要改什么

1. **坐标朝向**：先按 `(x, -z)` 画，再看弯角顺序是否合理；不对就试 `(x, z)`、`(-x, -z)`。
2. **弯角位置**：曲率峰值会自动出来，但弯名要人工对照真实赛道。
3. **标签偏移**：每个赛道的空白区域不同，`(dx, dy)` 需要微调。
4. **比例尺位置**：挪到画面里没有赛道的地方。
5. **标题/统计**：从 header 和总长度自动生成，不要写死。

---

## 13. 关键教训

- **别用线宽画路宽**：数据坐标和 point 单位不一致，比例会随画布尺寸变化。
- **路面来自真实边距**：第二段的 `left_edge_m` / `right_edge_m` 是这张图质感的来源，等宽线会丢掉这个信息。
- **地图不需要坐标轴**：关掉网格和刻度，加比例尺就够了。
- **颜色只做强调**：整张图只有红色起终点 + 红色弯心点，其余都是灰阶，读者不会迷路。
- **一定要渲染出来看**：多边形的接缝、标签碰撞、比例尺压线，只有看图才能发现。
