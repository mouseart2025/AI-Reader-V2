# Story N30.5: 人物轨迹动画增强

Status: draft

## Story

As a 用户,
I want 播放人物轨迹时看到发光的动态拖尾效果和章节进度条联动，而不是现在简单的静态折线,
So that 人物旅程的叙事感更强烈，我能清楚看到角色在哪个章节到达了哪个地点。

## Background

当前轨迹系统的基础已经完整：
1. 后端 `visualization_service.py` 从 `characters[].locations_in_chapter` 聚合轨迹数据
2. 前端 `MapPage.tsx` 有完整的播放/暂停控制（800ms 间隔 setInterval）
3. `NovelMap.tsx` 渲染为 Cardinal 曲线 + 黄色圆点

但视觉效果还很基础：
- 全程静态折线，没有动态行进感
- 没有章节标注，无法对应"第X章到了Y地"
- 没有动画过渡（点突然出现，不是逐步延伸）
- 播放速度不可调

## Acceptance Criteria

1. **AC-1**: 轨迹线改为渐进式绘制——播放时路径从起点逐步延伸到当前位置，而不是一次性显示全部
2. **AC-2**: 当前位置标记为发光脉冲点（CSS animation: scale pulse + box-shadow glow），颜色 #f59e0b
3. **AC-3**: 已经过的路径段显示为实线（opacity 0.85），未经过的路径段显示为虚线（opacity 0.2）
4. **AC-4**: 每个轨迹点显示章节编号标注（小号文字，格式"Ch.12"），hover 时显示完整信息（"第12章：xxx章节名"）
5. **AC-5**: 播放速度可调：慢速(1200ms)、正常(800ms)、快速(400ms)，三档切换按钮
6. **AC-6**: 播放时地图自动平移（pan）到当前位置附近，确保当前轨迹点始终在视口内
7. **AC-7**: 轨迹路径的 stroke-width 在缩放时做 counter-scale，保持视觉一致性
8. **AC-8**: 地点停留时长通过圆点大小体现——停留章节数越多，圆点越大（r = 4 + stayCount * 1.5, max 12）

## Tasks / Subtasks

- [ ] Task 1: 渐进式路径绘制
  - [ ] 1.1 将轨迹 `<path>` 分为两段：已播放段（`visibleTrajectory.slice(0, playIndex+1)`）和未播放段（完整路径）
  - [ ] 1.2 已播放段：实线，stroke-opacity 0.85，stroke="#f59e0b"
  - [ ] 1.3 未播放段（背景）：虚线 stroke-dasharray="8,6"，stroke-opacity 0.2，stroke="#f59e0b"
  - [ ] 1.4 路径切换时使用 `stroke-dashoffset` CSS transition 实现流畅延伸动画

- [ ] Task 2: 发光脉冲当前位置标记
  - [ ] 2.1 当前位置绘制一个 `<circle>` + 外圈 `<circle>` 组合
  - [ ] 2.2 内圈：r=6, fill="#f59e0b", stroke="#fff"
  - [ ] 2.3 外圈：r=14, fill="none", stroke="#f59e0b", opacity 随 CSS animation 脉冲 (0.6→0.1→0.6, 1.5s infinite)
  - [ ] 2.4 添加 SVG `<animate>` 或 CSS `@keyframes` 实现脉冲效果

- [ ] Task 3: 章节标注
  - [ ] 3.1 每个轨迹圆点旁添加小号文字 `<text>` 显示 "Ch.{chapter_index}"
  - [ ] 3.2 文字样式：fontSize=9, fill=currentColor, opacity=0.6, pointer-events="none"
  - [ ] 3.3 连续相同地点只显示第一次的章节号（避免重叠）
  - [ ] 3.4 Hover 时通过 `<title>` 元素显示完整信息

- [ ] Task 4: 播放速度控制
  - [ ] 4.1 MapPage 右侧面板"人物轨迹"区域新增速度切换按钮组（三个按钮：x0.5 / x1 / x2）
  - [ ] 4.2 速度映射：x0.5=1200ms, x1=800ms, x2=400ms
  - [ ] 4.3 切换速度时如果正在播放，动态更新 setInterval 间隔

- [ ] Task 5: 自动平移跟随
  - [ ] 5.1 播放时每次 playIndex 变化，计算当前轨迹点在视口中的位置
  - [ ] 5.2 如果当前点距视口边缘 < 20% 视口宽度，执行平移使其回到视口中心
  - [ ] 5.3 平移使用 D3 zoom.translateTo 过渡动画（duration=300ms）
  - [ ] 5.4 手动拖拽地图时临时禁用自动跟随（直到下一次播放开始）

- [ ] Task 6: 停留时长可视化
  - [ ] 6.1 利用已有的 `stayDurations` Map（MapPage 中已计算）
  - [ ] 6.2 将 stayDurations 传入 NovelMap 作为新 prop
  - [ ] 6.3 轨迹圆点半径 = `4 + Math.min(stayDurations.get(loc) ?? 0, 5) * 1.5`
  - [ ] 6.4 圆点 hover tooltip 显示 "在此停留 N 章"

## Dev Notes

### 渐进式路径实现策略

不使用 `stroke-dashoffset` 动画（性能不佳），改为直接维护两个 `<path>` 元素：

```typescript
// Background: full path, dashed, low opacity
trajG.append("path")
  .attr("d", lineGen(allCoords)!)
  .attr("stroke-dasharray", "8,6")
  .attr("stroke-opacity", 0.2)

// Foreground: visible path up to playIndex
trajG.append("path")
  .attr("d", lineGen(visibleCoords)!)
  .attr("stroke-opacity", 0.85)
```

每次 playIndex 变化时更新 foreground path 的 `d` 属性。D3 的 Cardinal 曲线会自动产生平滑的路径延伸效果。

### 脉冲动画

使用 SVG `<animate>` 而非 CSS animation，因为 SVG transform 内的元素 CSS animation 支持不一致：

```typescript
pulseCircle
  .append("animate")
  .attr("attributeName", "r")
  .attr("values", "10;16;10")
  .attr("dur", "1.5s")
  .attr("repeatCount", "indefinite")

pulseCircle
  .append("animate")
  .attr("attributeName", "opacity")
  .attr("values", "0.6;0.1;0.6")
  .attr("dur", "1.5s")
  .attr("repeatCount", "indefinite")
```

### Counter-scale stroke-width

轨迹路径的 stroke-width 需要在 zoom callback 中做反向缩放，与标签 counter-scale 相同逻辑：

```typescript
// In zoom callback
const k = transform.k
svg.select("#trajectory path").attr("stroke-width", 3 / k)
svg.select("#trajectory circle").attr("r", d => d.baseR / k)
```

### 数据流

```
MapPage: trajectories[person] → selectedTrajectory → visibleTrajectory (slice by playIndex)
         stayDurations (Map<string, number>)
         playSpeed (800 | 1200 | 400)
    ↓
NovelMap props: trajectoryPoints, stayDurations
    ↓
NovelMap: render #trajectory group (two paths + circles + labels + pulse marker)
```
