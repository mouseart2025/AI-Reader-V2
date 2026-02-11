#!/usr/bin/env python3
"""Generate Excalidraw wireframes for AI Reader V2 interaction design.

Usage:
    python generate_wireframes.py          # Generate all pages
    python generate_wireframes.py bookshelf  # Generate bookshelf only
    python generate_wireframes.py reading    # Generate reading only
"""

import json
import sys
import os

# ── Color Constants ──────────────────────────────────────────────────────────
C_BLACK = "#1e1e1e"
C_DARK = "#343a40"
C_GRAY = "#868e96"
C_LIGHT_GRAY = "#adb5bd"
C_BORDER = "#dee2e6"
C_BORDER_MED = "#ced4da"
C_BG = "#f8f9fa"
C_BG_WHITE = "#ffffff"
C_BLUE = "#4a90d9"
C_GREEN = "#40c057"
C_RED = "#e03131"
C_ORANGE_ANNO = "#e67700"   # annotation color
C_WHITE = "#ffffff"
# Entity type colors
C_CHAR = "#4a90d9"      # Character - blue
C_LOC = "#40c057"       # Location - green
C_ITEM = "#e8590c"      # Item - orange
C_ORG = "#7950f2"       # Organization - purple
C_CONCEPT = "#868e96"   # Concept - gray


class ExcalidrawBuilder:
    """Helper to build Excalidraw JSON files programmatically."""

    def __init__(self):
        self.elements = []
        self._id = 0
        self._seed = 100000

    def _nid(self):
        self._id += 1
        return f"el_{self._id:04d}"

    def _nseed(self):
        self._seed += 1
        return self._seed

    def _base(self, **kw):
        return {
            "angle": 0,
            "strokeColor": kw.get("color", C_BLACK),
            "backgroundColor": kw.get("bg", "transparent"),
            "fillStyle": "solid",
            "strokeWidth": kw.get("sw", 1),
            "strokeStyle": kw.get("ss", "solid"),
            "roughness": kw.get("rough", 1),
            "opacity": kw.get("opacity", 100),
            "groupIds": kw.get("gids", []),
            "frameId": None,
            "roundness": kw.get("rnd", {"type": 3}),
            "seed": self._nseed(),
            "version": 1,
            "versionNonce": self._nseed(),
            "isDeleted": False,
            "boundElements": kw.get("bound", None),
            "updated": 1700000000000,
            "link": None,
            "locked": False,
        }

    def rect(self, x, y, w, h, **kw):
        el = {
            "id": self._nid(), "type": "rectangle",
            "x": x, "y": y, "width": w, "height": h,
            **self._base(**kw),
        }
        self.elements.append(el)
        return el["id"]

    def text(self, x, y, s, fs=16, **kw):
        cn = sum(1 for c in s if ord(c) > 127)
        asc = len(s) - cn
        w = cn * fs + asc * fs * 0.6
        h = fs * 1.25
        el = {
            "id": self._nid(), "type": "text",
            "x": x, "y": y, "width": max(w, 10), "height": h,
            **self._base(rnd=None, **kw),
            "text": s, "fontSize": fs,
            "fontFamily": kw.get("ff", 2),
            "textAlign": kw.get("ta", "left"),
            "verticalAlign": "top",
            "containerId": None,
            "originalText": s, "lineHeight": 1.25,
        }
        self.elements.append(el)
        return el["id"]

    def line(self, x1, y1, x2, y2, **kw):
        el = {
            "id": self._nid(), "type": "line",
            "x": x1, "y": y1,
            "width": abs(x2 - x1) or 1, "height": abs(y2 - y1) or 1,
            **self._base(rnd={"type": 2}, **kw),
            "points": [[0, 0], [x2 - x1, y2 - y1]],
            "lastCommittedPoint": None,
            "startBinding": None, "endBinding": None,
            "startArrowhead": None, "endArrowhead": None,
        }
        self.elements.append(el)
        return el["id"]

    def arrow(self, x1, y1, x2, y2, **kw):
        el = {
            "id": self._nid(), "type": "arrow",
            "x": x1, "y": y1,
            "width": abs(x2 - x1) or 1, "height": abs(y2 - y1) or 1,
            **self._base(rnd={"type": 2}, **kw),
            "points": [[0, 0], [x2 - x1, y2 - y1]],
            "lastCommittedPoint": None,
            "startBinding": None, "endBinding": None,
            "startArrowhead": None, "endArrowhead": "arrow",
        }
        self.elements.append(el)
        return el["id"]

    def diamond(self, x, y, w, h, **kw):
        el = {
            "id": self._nid(), "type": "diamond",
            "x": x, "y": y, "width": w, "height": h,
            **self._base(**kw),
        }
        self.elements.append(el)
        return el["id"]

    def ellipse(self, x, y, w, h, **kw):
        el = {
            "id": self._nid(), "type": "ellipse",
            "x": x, "y": y, "width": w, "height": h,
            **self._base(rnd={"type": 2}, **kw),
        }
        self.elements.append(el)
        return el["id"]

    def build(self):
        return {
            "type": "excalidraw",
            "version": 2,
            "source": "https://excalidraw.com",
            "elements": self.elements,
            "appState": {
                "gridSize": None,
                "viewBackgroundColor": "#ffffff",
            },
            "files": {},
        }


# ─── Helper: draw the novel-page top nav bar ────────────────────────────────
def draw_top_nav(b, x, y, w, active_tab=None):
    """Draw the in-novel top navigation bar with tab strip."""
    b.rect(x, y, w, 48, bg=C_BG, color=C_BORDER)
    # Back arrow + novel title
    b.text(x + 16, y + 13, "←", fs=20, color=C_GRAY)
    b.text(x + 48, y + 14, "凡人修仙传", fs=18)
    # Tab bar
    tabs = ["阅读", "关系图", "世界地图", "时间线", "势力图", "百科", "分析"]
    tx = x + 260
    for tab in tabs:
        is_active = (tab == active_tab)
        tc = C_BLUE if is_active else C_GRAY
        b.text(tx, y + 15, tab, fs=14, color=tc)
        if is_active:
            tw = len(tab) * 14
            b.line(tx, y + 46, tx + tw, y + 46, color=C_BLUE, sw=2)
        tx += len(tab) * 14 + 30
    # Right icons
    b.text(x + w - 60, y + 14, "🔍", fs=18)
    b.text(x + w - 30, y + 14, "⚙", fs=18, color=C_GRAY)


# ─── Helper: draw bottom Q&A bar ────────────────────────────────────────────
def draw_qa_bar(b, x, y, w):
    """Draw the persistent bottom Q&A input bar."""
    b.rect(x, y, w, 48, bg=C_BG_WHITE, color=C_BORDER)
    b.rect(x + 16, y + 8, w - 120, 32, bg=C_BG, color=C_BORDER_MED, rnd={"type": 3})
    b.text(x + 30, y + 15, "输入问题，按 Enter 发送... (⌘K)", fs=14, color=C_LIGHT_GRAY)
    b.text(x + w - 90, y + 13, "发送", fs=14, color=C_BLUE)
    b.text(x + w - 40, y + 10, "⤢", fs=20, color=C_GRAY)


# ═══════════════════════════════════════════════════════════════════════════════
#  BOOKSHELF PAGE
# ═══════════════════════════════════════════════════════════════════════════════
def build_bookshelf():
    b = ExcalidrawBuilder()
    W, H = 1440, 900  # viewport

    # ── Section 1: Normal State ──────────────────────────────────────────────
    b.text(50, 15, "1. 书架页 — 正常状态（已有小说）", fs=24, color=C_ORANGE_ANNO)

    ox, oy = 50, 50  # origin
    b.rect(ox, oy, W, H, color=C_DARK, sw=2)

    # Top nav bar
    b.rect(ox, oy, W, 56, bg=C_BG, color=C_BORDER)
    b.text(ox + 30, oy + 15, "📖 AI Reader", fs=20)
    b.rect(ox + 480, oy + 10, 400, 36, bg=C_BG_WHITE, color=C_BORDER_MED)
    b.text(ox + 500, oy + 18, "搜索小说...", fs=14, color=C_LIGHT_GRAY)
    b.text(ox + W - 50, oy + 15, "⚙", fs=22, color=C_GRAY)

    # Content header
    b.text(ox + 40, oy + 80, "我的书架", fs=24)
    b.rect(ox + W - 200, oy + 73, 160, 42, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
    b.text(ox + W - 185, oy + 83, "+ 上传小说", fs=16, color=C_WHITE)

    # ── Card 1: Analyzing ──
    cx, cy = ox + 40, oy + 140
    cw, ch = 410, 290
    b.rect(cx, cy, cw, ch, bg=C_BG_WHITE, color=C_BORDER, rnd={"type": 3})
    b.rect(cx + 20, cy + 20, 110, 150, bg="#e9ecef", color=C_BORDER)
    b.text(cx + 45, cy + 80, "封面", fs=16, color=C_LIGHT_GRAY)
    b.text(cx + 150, cy + 25, "凡人修仙传", fs=20)
    b.text(cx + 150, cy + 55, "忘语", fs=14, color=C_GRAY)
    b.text(cx + 150, cy + 90, "● 分析中", fs=14, color=C_BLUE)
    b.text(cx + 150, cy + 115, "120 / 2451 章", fs=14, color=C_GRAY)
    b.rect(cx + 20, cy + 200, 370, 8, bg="#e9ecef", color="transparent")
    b.rect(cx + 20, cy + 200, 18, 8, bg=C_BLUE, color="transparent")
    b.text(cx + 20, cy + 220, "上次阅读: 第120章 · 3天前", fs=12, color=C_LIGHT_GRAY)
    b.text(cx + 370, cy + 22, "⋯", fs=22, color=C_GRAY)

    # ── Card 2: Complete ──
    cx2 = cx + cw + 30
    b.rect(cx2, cy, cw, ch, bg=C_BG_WHITE, color=C_BORDER, rnd={"type": 3})
    b.rect(cx2 + 20, cy + 20, 110, 150, bg="#e9ecef", color=C_BORDER)
    b.text(cx2 + 45, cy + 80, "封面", fs=16, color=C_LIGHT_GRAY)
    b.text(cx2 + 150, cy + 25, "平凡的世界", fs=20)
    b.text(cx2 + 150, cy + 55, "路遥", fs=14, color=C_GRAY)
    b.text(cx2 + 150, cy + 90, "✓ 分析完成", fs=14, color=C_GREEN)
    b.text(cx2 + 150, cy + 115, "162 / 162 章", fs=14, color=C_GRAY)
    b.rect(cx2 + 20, cy + 200, 370, 8, bg="#e9ecef", color="transparent")
    b.rect(cx2 + 20, cy + 200, 370, 8, bg=C_GREEN, color="transparent")
    b.text(cx2 + 20, cy + 220, "上次阅读: 第98章 · 1天前", fs=12, color=C_LIGHT_GRAY)
    b.text(cx2 + 370, cy + 22, "⋯", fs=22, color=C_GRAY)

    # ── Card 3: Add New (dashed) ──
    cx3 = cx2 + cw + 30
    b.rect(cx3, cy, cw, ch, color=C_BORDER_MED, ss="dashed", rnd={"type": 3})
    b.text(cx3 + 185, cy + 100, "+", fs=48, color=C_BORDER_MED)
    b.text(cx3 + 150, cy + 180, "上传新小说", fs=16, color=C_LIGHT_GRAY)

    # ── Annotations ──
    ax = ox + W + 60
    b.text(ax, oy + 140, "交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(ax, oy + 175, "· 点击卡片 → 进入阅读页 /novel/:id/read", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 200, "· 点击 ⋯ 或右键 → 弹出操作菜单", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 225, "  （继续阅读 / 重新分析 / 删除小说）", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 255, "· 搜索栏按小说名 / 作者筛选", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 280, "· ⚙ → 跳转 /settings", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 310, "· 「+ 上传小说」→ 弹出上传对话框", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 340, "· 虚线卡片与上传按钮功能相同", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 375, "· 分析进度条实时更新 (WebSocket)", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 400, "· 删除操作需二次确认", fs=14, color=C_ORANGE_ANNO)
    b.arrow(ax - 10, oy + 180, ox + W + 10, oy + 280, color=C_ORANGE_ANNO, ss="dashed")

    # ── Section 2: Empty State ────────────────────────────────────────────────
    s2y = oy + H + 100
    b.text(50, s2y - 35, "2. 书架页 — 空状态（首次使用 / 无小说）", fs=24, color=C_ORANGE_ANNO)
    b.rect(ox, s2y, W, 600, color=C_DARK, sw=2)

    # Nav bar
    b.rect(ox, s2y, W, 56, bg=C_BG, color=C_BORDER)
    b.text(ox + 30, s2y + 15, "📖 AI Reader", fs=20)
    b.text(ox + W - 50, s2y + 15, "⚙", fs=22, color=C_GRAY)

    # Centered empty state
    ec = ox + W // 2  # center x
    b.text(ec - 20, s2y + 160, "📚", fs=48)
    b.text(ec - 90, s2y + 240, "还没有导入小说", fs=22, color=C_GRAY)
    b.text(ec - 200, s2y + 280, "上传你的第一本小说，开始智能阅读之旅", fs=16, color=C_LIGHT_GRAY)
    b.rect(ec - 80, s2y + 330, 180, 48, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
    b.text(ec - 50, s2y + 342, "上传小说", fs=18, color=C_WHITE)

    # ── Section 3: Upload Dialog ──────────────────────────────────────────────
    s3y = s2y + 700
    b.text(50, s3y - 35, "3. 上传小说对话框", fs=24, color=C_ORANGE_ANNO)

    dx, dy = 200, s3y
    dw, dh = 1000, 780
    b.rect(dx, dy, dw, dh, bg=C_BG_WHITE, color=C_DARK, sw=2, rnd={"type": 3})

    # Dialog header
    b.text(dx + 40, dy + 22, "上传小说", fs=22)
    b.text(dx + dw - 50, dy + 22, "✕", fs=22, color=C_GRAY)
    b.line(dx + 30, dy + 58, dx + dw - 30, dy + 58, color=C_BORDER)

    # Drop zone
    b.rect(dx + 40, dy + 78, dw - 80, 150, color=C_BLUE, ss="dashed", rnd={"type": 3})
    b.text(dx + 280, dy + 120, "将 .txt 或 .md 文件拖放到此处", fs=16, color=C_GRAY)
    b.text(dx + 360, dy + 150, "或 点击选择文件", fs=14, color=C_BLUE)
    b.text(dx + 410, dy + 180, "最大 50MB", fs=12, color=C_LIGHT_GRAY)

    # Separator
    b.line(dx + 30, dy + 248, dx + dw - 30, dy + 248, color=C_BORDER)

    # Metadata section
    b.text(dx + 40, dy + 265, "小说信息（自动识别，可手动修改）", fs=16, color=C_GRAY)

    b.text(dx + 40, dy + 300, "小说名称", fs=13, color=C_GRAY)
    b.rect(dx + 40, dy + 320, 430, 36, bg=C_BG_WHITE, color=C_BORDER_MED, rnd={"type": 3})
    b.text(dx + 55, dy + 328, "凡人修仙传", fs=14)

    b.text(dx + 510, dy + 300, "作者", fs=13, color=C_GRAY)
    b.rect(dx + 510, dy + 320, 430, 36, bg=C_BG_WHITE, color=C_BORDER_MED, rnd={"type": 3})
    b.text(dx + 525, dy + 328, "忘语", fs=14)

    # Separator
    b.line(dx + 30, dy + 378, dx + dw - 30, dy + 378, color=C_BORDER)

    # Chapter split preview
    b.text(dx + 40, dy + 395, "章节切分预览", fs=16, color=C_GRAY)
    b.text(dx + 500, dy + 397, "✓ 检测到 2451 章", fs=14, color=C_GREEN)

    b.rect(dx + 40, dy + 425, dw - 80, 240, bg=C_BG, color=C_BORDER, rnd={"type": 3})
    chapters = [
        "第一章  穷山僻壤",
        "第二章  墨大夫",
        "第三章  七玄门",
        "第四章  无名功法",
        "第五章  苦练功法",
        "...",
        "第二千四百五十一章  大结局",
    ]
    for i, ch in enumerate(chapters):
        c = C_LIGHT_GRAY if ch == "..." else C_BLACK
        b.text(dx + 65, dy + 440 + i * 28, ch, fs=14, color=c)

    b.text(dx + 580, dy + 640, "章节切分有误？手动调整 →", fs=13, color=C_BLUE)

    # Action buttons
    b.rect(dx + dw - 290, dy + dh - 70, 120, 44, color=C_BORDER_MED, rnd={"type": 3})
    b.text(dx + dw - 262, dy + dh - 58, "取消", fs=16, color=C_GRAY)
    b.rect(dx + dw - 150, dy + dh - 70, 120, 44, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
    b.text(dx + dw - 130, dy + dh - 58, "开始导入", fs=16, color=C_WHITE)

    # Upload annotations
    uax = dx + dw + 50
    b.text(uax, dy + 80, "上传流程", fs=18, color=C_ORANGE_ANNO)
    b.text(uax, dy + 115, "1. 拖放或点击选择文件", fs=14, color=C_ORANGE_ANNO)
    b.text(uax, dy + 140, "2. 自动检测编码 (UTF-8/GBK)", fs=14, color=C_ORANGE_ANNO)
    b.text(uax, dy + 165, "3. 自动识别小说名 + 作者", fs=14, color=C_ORANGE_ANNO)
    b.text(uax, dy + 190, "4. 5级优先级模式匹配章节切分", fs=14, color=C_ORANGE_ANNO)
    b.text(uax, dy + 215, "5. 用户确认/调整后开始导入", fs=14, color=C_ORANGE_ANNO)
    b.text(uax, dy + 255, "文件限制", fs=18, color=C_ORANGE_ANNO)
    b.text(uax, dy + 285, "· 格式: .txt / .md", fs=14, color=C_ORANGE_ANNO)
    b.text(uax, dy + 310, "· 大小: 最大 50MB", fs=14, color=C_ORANGE_ANNO)
    b.text(uax, dy + 335, "· 编码: UTF-8 / GBK 自动检测", fs=14, color=C_ORANGE_ANNO)

    # ── Section 4: First-time Experience ──────────────────────────────────────
    s4y = s3y + dh + 100
    b.text(50, s4y - 35, "4. 首次使用引导（Overlay）", fs=24, color=C_ORANGE_ANNO)

    fx, fy = 200, s4y
    fw, fh = 1000, 420
    b.rect(fx, fy, fw, fh, bg=C_BG_WHITE, color=C_DARK, sw=2, rnd={"type": 3})

    b.text(fx + 40, fy + 25, "欢迎使用 AI Reader", fs=22)
    b.text(fx + 40, fy + 60, "在开始之前，请确认以下服务已就绪：", fs=16, color=C_GRAY)

    # Checklist
    items = [
        ("✓", "Ollama 服务", "已检测到，运行中", C_GREEN),
        ("✓", "Qwen 2.5 模型", "已安装 (7B)", C_GREEN),
        ("✕", "Embedding 模型", "未检测到", C_RED),
    ]
    for i, (icon, name, status, clr) in enumerate(items):
        iy = fy + 110 + i * 45
        b.text(fx + 60, iy, f"{icon}  {name}", fs=16, color=clr)
        b.text(fx + 300, iy, status, fs=14, color=clr)
        if clr == C_RED:
            b.rect(fx + 500, iy - 4, 120, 32, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
            b.text(fx + 520, iy + 2, "一键安装", fs=14, color=C_WHITE)

    b.line(fx + 30, fy + 260, fx + fw - 30, fy + 260, color=C_BORDER)
    b.text(fx + 40, fy + 280, "系统状态: 部分就绪", fs=16, color=C_ORANGE_ANNO)
    b.text(fx + 40, fy + 310, "请安装缺失的模型后继续使用。", fs=14, color=C_GRAY)

    b.rect(fx + fw - 200, fy + fh - 65, 160, 44, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
    b.text(fx + fw - 175, fy + fh - 53, "开始使用", fs=16, color=C_WHITE)

    # Annotations
    fax = fx + fw + 50
    b.text(fax, fy + 50, "引导逻辑", fs=18, color=C_ORANGE_ANNO)
    b.text(fax, fy + 85, "· 仅在首次打开时显示", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, fy + 110, "· 自动检测本地 Ollama 服务", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, fy + 135, "· 自动检测已安装模型", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, fy + 160, "· 缺失组件提供一键安装", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, fy + 185, "· 全部就绪后可直接跳过", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, fy + 210, "· 也可从设置页重新触发检测", fs=14, color=C_ORANGE_ANNO)

    # ── Section 5: Card Hover Menu ────────────────────────────────────────────
    s5y = s4y + fh + 100
    b.text(50, s5y - 35, "5. 小说卡片 Hover / 右键菜单", fs=24, color=C_ORANGE_ANNO)

    # Single card (hover border)
    hx, hy = 50, s5y
    b.rect(hx, hy, 410, 290, bg=C_BG_WHITE, color=C_BLUE, sw=2, rnd={"type": 3})
    b.rect(hx + 20, hy + 20, 110, 150, bg="#e9ecef", color=C_BORDER)
    b.text(hx + 45, hy + 80, "封面", fs=16, color=C_LIGHT_GRAY)
    b.text(hx + 150, hy + 25, "凡人修仙传", fs=20)
    b.text(hx + 150, hy + 55, "忘语", fs=14, color=C_GRAY)
    b.text(hx + 370, hy + 22, "⋯", fs=22, color=C_GRAY)

    # Dropdown menu
    mx, my = hx + 290, hy + 50
    b.rect(mx, my, 150, 120, bg=C_BG_WHITE, color=C_BORDER, rnd={"type": 3})
    b.text(mx + 20, my + 15, "继续阅读", fs=14)
    b.line(mx + 10, my + 40, mx + 140, my + 40, color="#f1f3f5")
    b.text(mx + 20, my + 52, "重新分析", fs=14)
    b.line(mx + 10, my + 77, mx + 140, my + 77, color="#f1f3f5")
    b.text(mx + 20, my + 90, "删除小说", fs=14, color=C_RED)

    b.text(hx + 500, hy + 50, "点击 ⋯ 或右键卡片弹出", fs=14, color=C_ORANGE_ANNO)
    b.text(hx + 500, hy + 80, "「删除小说」需二次确认对话框", fs=14, color=C_ORANGE_ANNO)
    b.text(hx + 500, hy + 110, "hover 时卡片边框变为蓝色", fs=14, color=C_ORANGE_ANNO)

    # ── Section 6: Duplicate Detection ────────────────────────────────────────
    s6y = s5y + 370
    b.text(50, s6y - 35, "6. 重复小说检测对话框", fs=24, color=C_ORANGE_ANNO)

    ddx, ddy = 200, s6y
    ddw, ddh = 1000, 270
    b.rect(ddx, ddy, ddw, ddh, bg=C_BG_WHITE, color=C_DARK, sw=2, rnd={"type": 3})
    b.text(ddx + 40, ddy + 22, "检测到相似小说", fs=20)
    b.text(ddx + 40, ddy + 55, "书架中已有一本名为「凡人修仙传」的小说，是否为同一本？", fs=14, color=C_GRAY)

    # Comparison cards
    b.rect(ddx + 40, ddy + 90, 430, 80, bg=C_BG, color=C_BORDER, rnd={"type": 3})
    b.text(ddx + 60, ddy + 100, "已有版本", fs=13, color=C_GRAY)
    b.text(ddx + 60, ddy + 122, "凡人修仙传 · 2451章 · 12.3MB", fs=14)

    b.rect(ddx + 510, ddy + 90, 430, 80, bg=C_BG, color=C_BORDER, rnd={"type": 3})
    b.text(ddx + 530, ddy + 100, "新上传版本", fs=13, color=C_GRAY)
    b.text(ddx + 530, ddy + 122, "凡人修仙传 · 2460章 · 12.8MB", fs=14)

    # Buttons
    b.rect(ddx + 470, ddy + 200, 100, 40, color=C_BORDER_MED, rnd={"type": 3})
    b.text(ddx + 497, ddy + 210, "覆盖", fs=15, color=C_GRAY)
    b.rect(ddx + 590, ddy + 200, 130, 40, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
    b.text(ddx + 612, ddy + 210, "另存一份", fs=15, color=C_WHITE)
    b.rect(ddx + 740, ddy + 200, 100, 40, color=C_BORDER_MED, rnd={"type": 3})
    b.text(ddx + 767, ddy + 210, "取消", fs=15, color=C_GRAY)

    return b.build()


# ═══════════════════════════════════════════════════════════════════════════════
#  READING PAGE
# ═══════════════════════════════════════════════════════════════════════════════
def build_reading():
    b = ExcalidrawBuilder()
    W, H = 1440, 900
    SIDEBAR_W = 260
    DRAWER_W = 420

    # ── Section 1: Normal Reading View ────────────────────────────────────────
    b.text(50, 15, "1. 阅读页 — 正常阅读状态", fs=24, color=C_ORANGE_ANNO)

    ox, oy = 50, 50
    b.rect(ox, oy, W, H, color=C_DARK, sw=2)

    # Top nav with tabs
    draw_top_nav(b, ox, oy, W, active_tab="阅读")

    # Left sidebar: chapter list
    sb_x = ox
    sb_y = oy + 48
    sb_h = H - 48 - 48  # minus top nav and bottom qa bar
    b.rect(sb_x, sb_y, SIDEBAR_W, sb_h, bg=C_BG, color=C_BORDER)
    b.text(sb_x + 16, sb_y + 14, "章节目录", fs=16)
    b.text(sb_x + SIDEBAR_W - 30, sb_y + 16, "«", fs=16, color=C_GRAY)
    b.rect(sb_x + 12, sb_y + 45, SIDEBAR_W - 24, 30, bg=C_BG_WHITE, color=C_BORDER_MED, rnd={"type": 3})
    b.text(sb_x + 24, sb_y + 52, "搜索章节...", fs=12, color=C_LIGHT_GRAY)

    # Chapter tree (hierarchical: volume > chapter, with collapse/expand)
    # ── Volume 1: expanded ──
    iy = sb_y + 90
    b.rect(sb_x + 8, iy - 2, SIDEBAR_W - 16, 26, bg="#edf2f7", color="transparent", rnd={"type": 3})
    b.text(sb_x + 16, iy + 2, "▼", fs=11, color=C_DARK)
    b.text(sb_x + 32, iy, "第一卷 少年韩立", fs=14, color=C_DARK)
    b.text(sb_x + SIDEBAR_W - 55, iy + 3, "5/5 ✓", fs=10, color=C_GREEN)

    # Chapter items under Volume 1 (indented)
    ch_v1 = [
        ("✓", "第一章 穷山僻壤", False, C_GREEN),
        ("✓", "第二章 墨大夫", True, C_BLUE),  # current reading
        ("✓", "第三章 七玄门", False, C_GREEN),
        ("✓", "第四章 无名功法", False, C_GREEN),
        ("✓", "第五章 苦练功法", False, C_GREEN),
    ]
    for i, (icon, name, is_active, clr) in enumerate(ch_v1):
        cy = iy + 30 + i * 28
        if is_active:
            b.rect(sb_x + 8, cy - 3, SIDEBAR_W - 16, 24, bg="#e7f0fd", color="transparent", rnd={"type": 3})
        b.text(sb_x + 38, cy, f"{icon} {name}", fs=12, color=clr if not is_active else C_BLUE)

    # ── Volume 2: expanded, partially analyzed ──
    iy2 = iy + 30 + len(ch_v1) * 28 + 10
    b.rect(sb_x + 8, iy2 - 2, SIDEBAR_W - 16, 26, bg="#edf2f7", color="transparent", rnd={"type": 3})
    b.text(sb_x + 16, iy2 + 2, "▼", fs=11, color=C_DARK)
    b.text(sb_x + 32, iy2, "第二卷 七玄门岁月", fs=14, color=C_DARK)
    b.text(sb_x + SIDEBAR_W - 55, iy2 + 3, "3/8 ✓", fs=10, color=C_BLUE)

    ch_v2 = [
        ("✓", "第六章 入门考核", False, C_GREEN),
        ("✓", "第七章 内门弟子", False, C_GREEN),
        ("✓", "第八章 灵药园", False, C_GREEN),
        ("●", "第九章 偷学功法", False, C_BLUE),
        ("○", "第十章 夜间修炼", False, C_LIGHT_GRAY),
    ]
    for i, (icon, name, is_active, clr) in enumerate(ch_v2):
        cy = iy2 + 30 + i * 28
        b.text(sb_x + 38, cy, f"{icon} {name}", fs=12, color=clr)

    # ── Volume 3: collapsed ──
    iy3 = iy2 + 30 + len(ch_v2) * 28 + 10
    b.rect(sb_x + 8, iy3 - 2, SIDEBAR_W - 16, 26, bg="#edf2f7", color="transparent", rnd={"type": 3})
    b.text(sb_x + 16, iy3 + 2, "▶", fs=11, color=C_DARK)
    b.text(sb_x + 32, iy3, "第三卷 血色试炼", fs=14, color=C_GRAY)
    b.text(sb_x + SIDEBAR_W - 55, iy3 + 3, "0/10", fs=10, color=C_LIGHT_GRAY)

    # ── Volume 4: collapsed ──
    iy4 = iy3 + 32
    b.rect(sb_x + 8, iy4 - 2, SIDEBAR_W - 16, 26, bg="#edf2f7", color="transparent", rnd={"type": 3})
    b.text(sb_x + 16, iy4 + 2, "▶", fs=11, color=C_DARK)
    b.text(sb_x + 32, iy4, "第四卷 黄枫谷", fs=14, color=C_GRAY)
    b.text(sb_x + SIDEBAR_W - 55, iy4 + 3, "0/12", fs=10, color=C_LIGHT_GRAY)

    # ── ... more volumes ──
    b.text(sb_x + 100, iy4 + 40, "...", fs=14, color=C_LIGHT_GRAY)

    # Sidebar legend
    ly = sb_y + sb_h - 80
    b.line(sb_x + 12, ly, sb_x + SIDEBAR_W - 12, ly, color=C_BORDER)
    b.text(sb_x + 16, ly + 10, "✓ 已分析  ● 分析中  ○ 未分析", fs=11, color=C_GRAY)
    b.text(sb_x + 16, ly + 30, "▼ 展开  ▶ 折叠  点击卷名切换", fs=11, color=C_GRAY)

    # Main reading area
    rd_x = ox + SIDEBAR_W
    rd_y = oy + 48
    rd_w = W - SIDEBAR_W
    rd_h = H - 48 - 48
    b.rect(rd_x, rd_y, rd_w, rd_h, bg=C_BG_WHITE, color=C_BORDER)

    # Chapter title
    b.text(rd_x + 60, rd_y + 30, "第二章 墨大夫", fs=26)
    b.line(rd_x + 40, rd_y + 70, rd_x + rd_w - 40, rd_y + 70, color=C_BORDER)

    # Sample text with entity highlights
    tx = rd_x + 60
    ty = rd_y + 90
    lh = 36  # line height

    # Line 1
    b.text(tx, ty, "韩立", fs=16, color=C_CHAR)
    b.text(tx + 36, ty, "跟着", fs=16)
    b.text(tx + 72, ty, "墨大夫", fs=16, color=C_CHAR)
    b.text(tx + 124, ty, "来到了", fs=16)
    b.text(tx + 176, ty, "七玄门", fs=16, color=C_ORG)
    b.text(tx + 228, ty, "的山脚下。这里是", fs=16)
    b.text(tx + 360, ty, "落云山", fs=16, color=C_LOC)
    b.text(tx + 412, ty, "的腹地，", fs=16)

    # Line 2
    ty2 = ty + lh
    b.text(tx, ty2, "山间雾气缭绕，偶尔可见几只灵鹤飞过。", fs=16)

    # Line 3
    ty3 = ty2 + lh
    b.text(tx, ty3, "墨大夫", fs=16, color=C_CHAR)
    b.text(tx + 52, ty3, "告诉", fs=16)
    b.text(tx + 88, ty3, "韩立", fs=16, color=C_CHAR)
    b.text(tx + 124, ty3, "，修行之路的第一步是", fs=16)
    b.text(tx + 302, ty3, "筑基", fs=16, color=C_CONCEPT)
    b.text(tx + 338, ty3, "。", fs=16)

    # Line 4
    ty4 = ty3 + lh
    b.text(tx, ty4, "需要服用", fs=16)
    b.text(tx + 68, ty4, "筑基丹", fs=16, color=C_ITEM)
    b.text(tx + 120, ty4, "才能打通经脉，感应天地灵气......", fs=16)

    # More text placeholder
    b.text(tx, ty4 + lh * 2, "......", fs=16, color=C_LIGHT_GRAY)
    b.text(tx, ty4 + lh * 3, "(更多章节正文)", fs=14, color=C_LIGHT_GRAY)

    # Entity color legend (bottom of reading area)
    lg_y = rd_y + rd_h - 50
    b.line(rd_x + 40, lg_y, rd_x + rd_w - 40, lg_y, color=C_BORDER)
    lg_items = [
        ("● 人物", C_CHAR), ("● 地点", C_LOC), ("● 物品", C_ITEM),
        ("● 组织", C_ORG), ("● 概念", C_CONCEPT),
    ]
    lgx = rd_x + 60
    for label, clr in lg_items:
        b.text(lgx, lg_y + 12, label, fs=12, color=clr)
        lgx += 90

    # Bottom Q&A bar
    draw_qa_bar(b, ox, oy + H - 48, W)

    # ── Annotations for Section 1 ──
    ax = ox + W + 60
    b.text(ax, oy + 50, "阅读页交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(ax, oy + 85, "实体高亮", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 110, "· 点击实体名 → 打开实体卡片抽屉 (右侧)", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 135, "· 点击概念名 → 弹出概念浮层 (Popover)", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 160, "· 颜色按实体类型编码（见图例）", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 195, "章节侧栏（多级折叠）", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 220, "· 支持 卷 > 章 多级层级结构", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 245, "· 点击 ▼/▶ 或卷名展开/折叠", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 270, "· 卷标题栏显示分析进度 (N/M ✓)", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 295, "· 章节缩进显示，点击章名跳转阅读", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 320, "· 当前章高亮，自动展开所在卷", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 345, "· « 按钮可折叠整个侧栏", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 370, "· 无卷结构的小说退化为平铺列表", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 405, "导航", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 430, "· ← → 快捷键切换上下章", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 455, "· 顶部 Tab 栏切换视图", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 480, "· ← 返回书架", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 515, "底部问答栏", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 540, "· 常驻底部，⌘K 快捷聚焦", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 565, "· 回车发送，流式返回答案", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 590, "· ⤢ 展开为浮动面板 (见 Section 4)", fs=14, color=C_ORANGE_ANNO)

    # ── Section 2: Entity Card Drawer ─────────────────────────────────────────
    s2y = oy + H + 100
    b.text(50, s2y - 35, "2. 实体卡片抽屉（右侧 Drawer，420px）", fs=24, color=C_ORANGE_ANNO)
    b.text(50, s2y - 5, "点击实体名后从右侧滑出，半透明遮罩覆盖主内容", fs=14, color=C_ORANGE_ANNO)

    # Show the page frame with drawer
    dy2 = s2y + 20
    b.rect(ox, dy2, W, H, color=C_DARK, sw=2)

    # Top nav
    draw_top_nav(b, ox, dy2, W, active_tab="阅读")

    # Semi-transparent overlay (represented as light rect)
    b.rect(ox, dy2 + 48, W - DRAWER_W, H - 48, bg="#000000", color="transparent", opacity=15)

    # Drawer
    drw_x = ox + W - DRAWER_W
    drw_y = dy2 + 48
    drw_h = H - 48
    b.rect(drw_x, drw_y, DRAWER_W, drw_h, bg=C_BG_WHITE, color=C_BORDER, sw=2)

    # Drawer header
    b.text(drw_x + 16, drw_y + 14, "← 返回", fs=13, color=C_BLUE)
    b.text(drw_x + DRAWER_W - 35, drw_y + 12, "✕", fs=18, color=C_GRAY)

    # Breadcrumb
    b.text(drw_x + 16, drw_y + 42, "韩立", fs=12, color=C_BLUE)
    b.text(drw_x + 55, drw_y + 42, "> 墨大夫", fs=12, color=C_BLUE)
    b.text(drw_x + 130, drw_y + 42, "> 当前", fs=12, color=C_GRAY)

    b.line(drw_x + 10, drw_y + 62, drw_x + DRAWER_W - 10, drw_y + 62, color=C_BORDER)

    # Character card content
    cdy = drw_y + 75
    b.ellipse(drw_x + 16, cdy, 60, 60, bg="#e9ecef", color=C_BORDER)
    b.text(drw_x + 30, cdy + 18, "头像", fs=13, color=C_LIGHT_GRAY)
    b.text(drw_x + 90, cdy + 5, "韩立", fs=22)
    b.rect(drw_x + 90, cdy + 35, 40, 20, bg="#e7f0fd", color=C_CHAR, rnd={"type": 3})
    b.text(drw_x + 95, cdy + 38, "人物", fs=11, color=C_CHAR)
    b.text(drw_x + 140, cdy + 38, "出场 892 章（基于已分析的 120 章）", fs=11, color=C_GRAY)

    # Aliases
    ady = cdy + 70
    b.text(drw_x + 16, ady, "别名", fs=13, color=C_GRAY)
    b.text(drw_x + 16, ady + 22, "韩小子、韩道友、韩前辈", fs=14)

    # Description
    ddy = ady + 55
    b.text(drw_x + 16, ddy, "简介", fs=13, color=C_GRAY)
    b.text(drw_x + 16, ddy + 22, "凡人修仙传主角。原为贫苦农家子弟，", fs=13)
    b.text(drw_x + 16, ddy + 42, "被墨大夫收入七玄门，走上修仙之路。", fs=13)
    b.text(drw_x + 16, ddy + 62, "性格谨慎务实，善于隐忍。", fs=13)

    # Relationships
    rly = ddy + 100
    b.text(drw_x + 16, rly, "关系", fs=13, color=C_GRAY)
    rels = [
        ("墨大夫", "师父", "第1-10章"),
        ("南宫婉", "道侣", "第45章起"),
        ("厉飞雨", "好友", "第23章起"),
        ("张铁", "同门", "第3-15章"),
    ]
    for i, (name, rel, ch) in enumerate(rels):
        ry = rly + 25 + i * 28
        b.text(drw_x + 16, ry, name, fs=14, color=C_CHAR)
        b.text(drw_x + 100, ry, f"— {rel}", fs=14, color=C_GRAY)
        b.text(drw_x + 220, ry, ch, fs=12, color=C_LIGHT_GRAY)

    # Appearances
    apy = rly + 145
    b.text(drw_x + 16, apy, "出场章节", fs=13, color=C_GRAY)
    b.text(drw_x + 16, apy + 22, "第1章  第2章  第3章  第5章  第7章", fs=13, color=C_BLUE)
    b.text(drw_x + 16, apy + 44, "第10章  第12章  ... 共 892 章 ▸ 查看全部", fs=13, color=C_BLUE)

    # Drawer annotations
    dax = ox + W + 60
    b.text(dax, s2y + 50, "抽屉交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 85, "· 点击实体名打开，从右侧滑入 (300ms)", fs=14, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 110, "· 宽度 420px，遮罩点击关闭", fs=14, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 135, "· Esc 关闭抽屉", fs=14, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 170, "卡片内跳转", fs=16, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 195, "· 点击关系中的人物名 → 替换卡片内容", fs=14, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 220, "· 面包屑导航可回退（最多 10 层）", fs=14, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 245, "· 点击章节号 → 跳转到该章阅读", fs=14, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 280, "消歧", fs=16, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 305, "· 同名多实体 → 弹出消歧选择面板", fs=14, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 330, "· 选择后打开对应实体卡片", fs=14, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 365, "四种实体卡片", fs=16, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 390, "· 人物: 别名/简介/关系/出场章节", fs=14, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 415, "· 地点: 描述/层级/关联人物/事件", fs=14, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 440, "· 物品: 描述/拥有者/出场章节", fs=14, color=C_ORANGE_ANNO)
    b.text(dax, s2y + 465, "· 组织: 描述/成员/关联地点/事件", fs=14, color=C_ORANGE_ANNO)

    # ── Section 3: Concept Popover ────────────────────────────────────────────
    s3y = s2y + H + 120
    b.text(50, s3y - 35, "3. 概念浮层（Popover）", fs=24, color=C_ORANGE_ANNO)
    b.text(50, s3y - 5, "点击灰色概念高亮后在附近弹出，轻量展示", fs=14, color=C_ORANGE_ANNO)

    # Show a small context: some text + popover
    # Context text
    ctx_x, ctx_y = 100, s3y + 20
    b.text(ctx_x, ctx_y, "...修行之路的第一步是", fs=16)
    b.text(ctx_x + 176, ctx_y, "筑基", fs=16, color=C_CONCEPT)
    b.text(ctx_x + 210, ctx_y, "。需要服用", fs=16)
    b.text(ctx_x + 298, ctx_y, "筑基丹", fs=16, color=C_ITEM)
    b.text(ctx_x + 350, ctx_y, "才能......", fs=16)

    # Popover card (attached to "筑基")
    px, py = ctx_x + 140, ctx_y + 30
    pw, ph = 320, 200
    b.rect(px, py, pw, ph, bg=C_BG_WHITE, color=C_BORDER, sw=2, rnd={"type": 3})

    # Popover content
    b.rect(px + 12, py + 12, 40, 20, bg="#f1f3f5", color=C_CONCEPT, rnd={"type": 3})
    b.text(px + 17, py + 15, "概念", fs=11, color=C_CONCEPT)
    b.text(px + 62, py + 12, "筑基", fs=18)

    b.line(px + 10, py + 42, px + pw - 10, py + 42, color=C_BORDER)

    b.text(px + 12, py + 52, "修仙体系中的第一个大境界。凡人通过", fs=13)
    b.text(px + 12, py + 72, "服用筑基丹或自行感悟突破，正式踏入", fs=13)
    b.text(px + 12, py + 92, "修仙之路。筑基之后方可修炼法术。", fs=13)

    b.text(px + 12, py + 125, "首次出现: 第2章", fs=12, color=C_GRAY)
    b.text(px + 12, py + 145, "相关: 筑基丹、灵根、炼气期", fs=12, color=C_BLUE)

    b.line(px + 10, py + 168, px + pw - 10, py + 168, color=C_BORDER)
    b.text(px + 12, py + 175, "查看百科词条 →", fs=13, color=C_BLUE)

    # Arrow pointing to the trigger word
    b.arrow(px + 40, py, ctx_x + 195, ctx_y + 20, color=C_ORANGE_ANNO, ss="dashed")

    # Annotations
    pax = px + pw + 60
    b.text(pax, py, "浮层交互说明", fs=18, color=C_ORANGE_ANNO)
    b.text(pax, py + 30, "· 点击概念高亮弹出（非 hover）", fs=14, color=C_ORANGE_ANNO)
    b.text(pax, py + 55, "· 点击浮层外部关闭", fs=14, color=C_ORANGE_ANNO)
    b.text(pax, py + 80, "· 宽度 320px，定位在触发词附近", fs=14, color=C_ORANGE_ANNO)
    b.text(pax, py + 105, "· 「查看百科词条」→ 跳转百科页", fs=14, color=C_ORANGE_ANNO)
    b.text(pax, py + 130, "· 相关概念可点击，替换浮层内容", fs=14, color=C_ORANGE_ANNO)
    b.text(pax, py + 165, "与实体卡片的区别", fs=16, color=C_ORANGE_ANNO)
    b.text(pax, py + 190, "· 概念 = 轻量浮层 (Popover)", fs=14, color=C_ORANGE_ANNO)
    b.text(pax, py + 215, "· 人物/地点/物品/组织 = 右侧抽屉", fs=14, color=C_ORANGE_ANNO)

    # ── Section 4: Q&A Floating Panel ─────────────────────────────────────────
    s4y = s3y + 320
    b.text(50, s4y - 35, "4. 问答浮动面板（从底部展开，占 50% 高度）", fs=24, color=C_ORANGE_ANNO)

    # Page frame with floating panel
    p4x, p4y = 50, s4y
    b.rect(p4x, p4y, W, H, color=C_DARK, sw=2)

    # Top nav
    draw_top_nav(b, p4x, p4y, W, active_tab="阅读")

    # Reading area (dimmed / behind)
    b.rect(p4x, p4y + 48, W, H // 2 - 48, bg=C_BG, color=C_BORDER, opacity=50)
    b.text(p4x + 320, p4y + 200, "(阅读内容，被面板遮挡部分)", fs=14, color=C_LIGHT_GRAY)

    # Floating panel
    fp_y = p4y + H // 2
    fp_h = H // 2
    b.rect(p4x, fp_y, W, fp_h, bg=C_BG_WHITE, color=C_BORDER, sw=2)

    # Panel header
    b.text(p4x + 20, fp_y + 10, "智能问答", fs=16)
    b.text(p4x + W - 120, fp_y + 12, "全屏模式", fs=13, color=C_BLUE)
    b.text(p4x + W - 35, fp_y + 10, "✕", fs=18, color=C_GRAY)
    b.line(p4x + 10, fp_y + 38, p4x + W - 10, fp_y + 38, color=C_BORDER)

    # Chat messages
    msg_x = p4x + 40
    # User message
    b.text(msg_x + 800, fp_y + 55, "韩立和墨大夫是什么关系？", fs=14, ta="right", color=C_BLUE)
    b.rect(msg_x + 620, fp_y + 50, 380, 28, bg="#e7f0fd", color="transparent", rnd={"type": 3}, opacity=40)

    # AI response
    b.text(msg_x, fp_y + 95, "根据小说内容，韩立和墨大夫的关系是：", fs=14)
    b.text(msg_x, fp_y + 120, "墨大夫是韩立的启蒙师父。在第一章中，墨大夫", fs=14)
    b.text(msg_x, fp_y + 145, "收韩立为徒，带他进入七玄门......", fs=14)
    b.text(msg_x, fp_y + 180, "来源: 第1章、第2章、第5章", fs=12, color=C_BLUE)

    # Another user message
    b.text(msg_x + 800, fp_y + 215, "后来墨大夫怎么样了？", fs=14, ta="right", color=C_BLUE)
    b.rect(msg_x + 680, fp_y + 210, 320, 28, bg="#e7f0fd", color="transparent", rnd={"type": 3}, opacity=40)

    # AI response (streaming indicator)
    b.text(msg_x, fp_y + 255, "墨大夫后来在七玄门的一次冲突中...", fs=14)
    b.text(msg_x, fp_y + 280, "▍", fs=14, color=C_BLUE)  # cursor

    # Panel input area
    inp_y = p4y + H - 60
    b.rect(p4x + 20, inp_y, W - 40, 44, bg=C_BG, color=C_BORDER_MED, rnd={"type": 3})
    b.text(p4x + 40, inp_y + 12, "继续提问...", fs=14, color=C_LIGHT_GRAY)
    b.text(p4x + W - 80, inp_y + 12, "发送", fs=14, color=C_BLUE)

    # Panel annotations
    fax = p4x + W + 60
    b.text(fax, s4y + 20, "浮动面板交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(fax, s4y + 55, "· 点击底栏 ⤢ 或 Enter 展开", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, s4y + 80, "· 占屏幕下半部 50%，可拖拽调整", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, s4y + 105, "· 上半部分仍显示阅读内容（不可交互）", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, s4y + 130, "· ✕ 关闭面板回到底栏模式", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, s4y + 155, "· 「全屏模式」→ 跳转 /novel/:id/chat", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, s4y + 190, "问答特性", fs=16, color=C_ORANGE_ANNO)
    b.text(fax, s4y + 215, "· 流式输出（逐字显现）", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, s4y + 240, "· 答案中的实体名可点击", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, s4y + 265, "· 来源章节号可跳转阅读", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, s4y + 290, "· 浮动面板与全屏页共享对话上下文", fs=14, color=C_ORANGE_ANNO)
    b.text(fax, s4y + 315, "· 「基于已分析的 X 章内容」标注", fs=14, color=C_ORANGE_ANNO)

    # ── Section 5: Page Navigation Flow ───────────────────────────────────────
    s5y = s4y + H + 100
    b.text(50, s5y - 35, "5. 页面导航结构", fs=24, color=C_ORANGE_ANNO)

    # Draw a flow diagram
    # Bookshelf → Novel internal pages
    bx, by = 100, s5y + 30
    b.rect(bx, by, 130, 50, bg="#e7f0fd", color=C_BLUE, rnd={"type": 3})
    b.text(bx + 30, by + 15, "书架 /", fs=16, color=C_BLUE)

    b.arrow(bx + 130, by + 25, bx + 200, by + 25, color=C_DARK)
    b.text(bx + 140, by - 5, "点击小说", fs=12, color=C_GRAY)

    # Tab pages
    tab_pages = [
        ("阅读", "/novel/:id/read"),
        ("关系图", "/novel/:id/graph"),
        ("世界地图", "/novel/:id/map"),
        ("时间线", "/novel/:id/timeline"),
        ("势力图", "/novel/:id/factions"),
        ("百科", "/novel/:id/encyclopedia"),
        ("分析", "/novel/:id/analysis"),
    ]
    tpx = bx + 210
    for i, (name, path) in enumerate(tab_pages):
        tpy = by - 80 + i * 45
        active = (i == 0)
        bc = C_BLUE if active else C_BORDER
        bgc = "#e7f0fd" if active else C_BG
        b.rect(tpx, tpy, 300, 35, bg=bgc, color=bc, rnd={"type": 3})
        b.text(tpx + 10, tpy + 8, f"{name}  {path}", fs=13, color=C_BLUE if active else C_GRAY)

    # Arrow from bookshelf to all tabs
    for i in range(len(tab_pages)):
        tpy = by - 80 + i * 45 + 17
        b.arrow(bx + 200, by + 25, tpx, tpy, color=C_BORDER, ss="dashed", opacity=30)

    # ← back arrow
    b.arrow(tpx, by + 17, bx + 130, by + 17, color=C_GRAY, ss="dashed")
    b.text(tpx - 55, by - 5, "← 返回", fs=11, color=C_GRAY)

    # Q&A entry
    qa_x = tpx + 350
    b.rect(qa_x, by - 20, 200, 35, bg="#fff3e0", color="#e8590c", rnd={"type": 3})
    b.text(qa_x + 10, by - 12, "问答浮动面板", fs=14, color="#e8590c")
    b.text(qa_x + 10, by + 25, "↕ 常驻底栏，所有", fs=12, color=C_GRAY)
    b.text(qa_x + 10, by + 43, "小说内页面可用", fs=12, color=C_GRAY)

    b.rect(qa_x, by + 80, 200, 35, bg="#fff3e0", color="#e8590c", rnd={"type": 3})
    b.text(qa_x + 10, by + 88, "问答全屏 /chat", fs=14, color="#e8590c")
    b.arrow(qa_x + 100, by + 15, qa_x + 100, by + 80, color="#e8590c", ss="dashed")
    b.text(qa_x + 110, by + 52, "展开", fs=11, color="#e8590c")

    # Settings
    b.rect(qa_x, by + 160, 200, 35, bg=C_BG, color=C_GRAY, rnd={"type": 3})
    b.text(qa_x + 10, by + 168, "设置 /settings", fs=14, color=C_GRAY)
    b.text(qa_x + 10, by + 205, "从顶栏 ⚙ 图标进入", fs=12, color=C_GRAY)

    # Entity card drawer note
    b.rect(qa_x, by + 250, 200, 35, bg=C_BG, color=C_GRAY, rnd={"type": 3})
    b.text(qa_x + 10, by + 258, "实体卡片抽屉", fs=14, color=C_GRAY)
    b.text(qa_x + 10, by + 295, "所有页面中点击实体名", fs=12, color=C_GRAY)
    b.text(qa_x + 10, by + 313, "均可打开，覆盖在当前页上", fs=12, color=C_GRAY)

    return b.build()


# ═══════════════════════════════════════════════════════════════════════════════
#  RELATIONSHIP GRAPH PAGE
# ═══════════════════════════════════════════════════════════════════════════════
def build_graph():
    b = ExcalidrawBuilder()
    W, H = 1440, 900

    # ── Section 1: Main View ─────────────────────────────────────────────────
    b.text(50, 15, "1. 人物关系图 — 正常浏览状态", fs=24, color=C_ORANGE_ANNO)

    ox, oy = 50, 50
    b.rect(ox, oy, W, H, color=C_DARK, sw=2)

    # Top nav
    draw_top_nav(b, ox, oy, W, active_tab="关系图")

    # Chapter range slider (shared across all viz views)
    sl_y = oy + 48
    b.rect(ox, sl_y, W, 40, bg=C_BG, color=C_BORDER)
    b.text(ox + 16, sl_y + 10, "章节范围:", fs=13, color=C_GRAY)
    b.rect(ox + 110, sl_y + 13, 700, 14, bg="#e9ecef", color="transparent", rnd={"type": 3})
    b.rect(ox + 110, sl_y + 13, 340, 14, bg=C_BLUE, color="transparent", rnd={"type": 3})
    b.ellipse(ox + 108, sl_y + 9, 20, 20, bg=C_BG_WHITE, color=C_BLUE, sw=2)
    b.ellipse(ox + 448, sl_y + 9, 20, 20, bg=C_BG_WHITE, color=C_BLUE, sw=2)
    b.text(ox + 830, sl_y + 10, "第 1 章 — 第 120 章 (共 2451 章)", fs=13, color=C_GRAY)

    # Left filter panel
    fp_x, fp_y = ox, sl_y + 40
    fp_w = 240
    fp_h = H - 48 - 40 - 48
    b.rect(fp_x, fp_y, fp_w, fp_h, bg=C_BG, color=C_BORDER)
    b.text(fp_x + 16, fp_y + 12, "筛选", fs=16)
    b.text(fp_x + fp_w - 30, fp_y + 14, "«", fs=16, color=C_GRAY)

    # Filter: entity type
    fy = fp_y + 45
    b.text(fp_x + 16, fy, "实体类型", fs=13, color=C_GRAY)
    for i, (label, checked) in enumerate([("人物", True), ("智慧生物", False)]):
        ck = "☑" if checked else "☐"
        b.text(fp_x + 20, fy + 22 + i * 24, f"{ck} {label}", fs=13)

    # Filter: relationship type
    fy2 = fy + 80
    b.text(fp_x + 16, fy2, "关系类型", fs=13, color=C_GRAY)
    rels = [("亲属", True, "#e8590c"), ("师徒", True, C_BLUE), ("友好", True, C_GREEN),
            ("敌对", True, C_RED), ("恋爱", False, "#e64980"), ("组织从属", True, C_ORG)]
    for i, (label, checked, clr) in enumerate(rels):
        ck = "☑" if checked else "☐"
        b.text(fp_x + 20, fy2 + 22 + i * 24, f"{ck} {label}", fs=13, color=clr)

    # Filter: min appearance
    fy3 = fy2 + 175
    b.text(fp_x + 16, fy3, "最少出场", fs=13, color=C_GRAY)
    b.rect(fp_x + 16, fy3 + 22, 200, 10, bg="#e9ecef", color="transparent", rnd={"type": 3})
    b.rect(fp_x + 16, fy3 + 22, 60, 10, bg=C_BLUE, color="transparent", rnd={"type": 3})
    b.text(fp_x + 16, fy3 + 40, "≥ 3 章", fs=12, color=C_GRAY)

    # Filter: path finding
    fy4 = fy3 + 70
    b.line(fp_x + 12, fy4, fp_x + fp_w - 12, fy4, color=C_BORDER)
    b.text(fp_x + 16, fy4 + 10, "路径查找", fs=13, color=C_GRAY)
    b.rect(fp_x + 16, fy4 + 32, 200, 28, bg=C_BG_WHITE, color=C_BORDER_MED, rnd={"type": 3})
    b.text(fp_x + 24, fy4 + 38, "人物 A", fs=12, color=C_LIGHT_GRAY)
    b.rect(fp_x + 16, fy4 + 66, 200, 28, bg=C_BG_WHITE, color=C_BORDER_MED, rnd={"type": 3})
    b.text(fp_x + 24, fy4 + 72, "人物 B", fs=12, color=C_LIGHT_GRAY)
    b.rect(fp_x + 16, fy4 + 102, 90, 28, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
    b.text(fp_x + 26, fy4 + 108, "查找路径", fs=12, color=C_WHITE)

    # Main graph canvas
    gx = ox + fp_w
    gy = sl_y + 40
    gw = W - fp_w
    gh = H - 48 - 40 - 48
    b.rect(gx, gy, gw, gh, bg=C_BG_WHITE, color=C_BORDER)

    # Sample graph nodes
    nodes = [
        (gx + 500, gy + 200, 50, "韩立", C_CHAR, 20),
        (gx + 280, gy + 140, 35, "墨大夫", C_CHAR, 14),
        (gx + 700, gy + 150, 30, "南宫婉", C_CHAR, 14),
        (gx + 350, gy + 340, 28, "厉飞雨", C_CHAR, 13),
        (gx + 650, gy + 350, 28, "张铁", C_CHAR, 13),
        (gx + 180, gy + 300, 25, "李化元", C_CHAR, 12),
        (gx + 850, gy + 250, 25, "令狐冲", C_CHAR, 12),
        (gx + 500, gy + 480, 40, "七玄门", C_ORG, 16),
    ]
    for nx, ny, r, label, clr, fs_n in nodes:
        b.ellipse(nx - r, ny - r, r * 2, r * 2, bg=clr, color=clr, opacity=30)
        b.ellipse(nx - r + 4, ny - r + 4, r * 2 - 8, r * 2 - 8, bg=clr, color=clr, opacity=60)
        b.text(nx - len(label) * fs_n // 2, ny + r + 5, label, fs=fs_n, color=clr)

    # Sample edges
    edges = [
        (gx + 500, gy + 200, gx + 280, gy + 140, "师徒", C_BLUE),
        (gx + 500, gy + 200, gx + 700, gy + 150, "道侣", "#e64980"),
        (gx + 500, gy + 200, gx + 350, gy + 340, "好友", C_GREEN),
        (gx + 500, gy + 200, gx + 650, gy + 350, "同门", C_BLUE),
        (gx + 280, gy + 140, gx + 180, gy + 300, "同门", C_BLUE),
        (gx + 500, gy + 200, gx + 850, gy + 250, "敌对", C_RED),
        (gx + 350, gy + 340, gx + 500, gy + 480, "从属", C_ORG),
        (gx + 650, gy + 350, gx + 500, gy + 480, "从属", C_ORG),
    ]
    for x1, y1, x2, y2, label, clr in edges:
        b.line(x1, y1, x2, y2, color=clr, opacity=50)

    # Graph toolbar
    tb_y = gy + gh - 50
    b.rect(gx + gw // 2 - 120, tb_y, 240, 36, bg=C_BG_WHITE, color=C_BORDER, rnd={"type": 3})
    b.text(gx + gw // 2 - 105, tb_y + 8, "＋  −  ⟳  ⊞  📷", fs=16, color=C_GRAY)

    # Bottom Q&A bar
    draw_qa_bar(b, ox, oy + H - 48, W)

    # ── Annotations ──
    ax = ox + W + 60
    b.text(ax, oy + 50, "关系图交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(ax, oy + 85, "画布操作", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 110, "· 拖拽画布平移，滚轮缩放", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 135, "· 拖拽节点移动位置", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 160, "· hover 节点 → 高亮直接关系", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 185, "  其余节点/边半透明", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 215, "节点交互", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 240, "· 点击节点 → 弹出实体卡片", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 265, "· 双击节点 → 聚焦模式", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 290, "  只展示 N 跳内关系网络", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 320, "边交互", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 345, "· 点击边 → 关系详情浮层", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 370, "  关系演变链 + 关键章节", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 400, "章节范围滑块", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 425, "· 四个可视化视图共享", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 450, "· 拖拽选择范围，视图实时更新", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 480, "路径查找", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 505, "· 选中 A 后 Shift+点击 B", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 530, "· 或在筛选面板输入两个人物名", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 555, "· 高亮最短关系路径", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 590, "工具栏", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 615, "· 缩放 / 重置布局 / 适应屏幕 / 截图", fs=14, color=C_ORANGE_ANNO)

    # ── Section 2: Hover / Focus State ───────────────────────────────────────
    s2y = oy + H + 100
    b.text(50, s2y - 35, "2. 节点 Hover 高亮 + 关系详情浮层", fs=24, color=C_ORANGE_ANNO)

    # Small illustration: highlighted node
    hx, hy = 100, s2y + 20
    # Central node (highlighted)
    b.ellipse(hx + 150, hy + 80, 50, 50, bg=C_CHAR, color=C_CHAR, opacity=60)
    b.text(hx + 155, hy + 140, "韩立", fs=16, color=C_CHAR)
    # Connected nodes (visible)
    for dx, dy, name in [(0, 0, "墨大夫"), (300, -20, "南宫婉"), (80, 180, "厉飞雨")]:
        b.ellipse(hx + dx + 5, hy + dy + 5, 30, 30, bg=C_CHAR, color=C_CHAR, opacity=40)
        b.text(hx + dx - 5, hy + dy + 40, name, fs=12, color=C_CHAR)
        b.line(hx + 175, hy + 105, hx + dx + 20, hy + dy + 20, color=C_CHAR)
    # Dimmed nodes
    for dx, dy in [(350, 160), (400, 60)]:
        b.ellipse(hx + dx, hy + dy, 24, 24, bg=C_GRAY, color=C_GRAY, opacity=15)

    # Edge detail popover
    epx = hx + 500
    b.rect(epx, hy, 320, 160, bg=C_BG_WHITE, color=C_BORDER, sw=2, rnd={"type": 3})
    b.text(epx + 16, hy + 12, "韩立 — 墨大夫", fs=16)
    b.text(epx + 16, hy + 38, "关系演变:", fs=13, color=C_GRAY)
    b.text(epx + 16, hy + 60, "第1章  收为弟子（师徒）", fs=13)
    b.text(epx + 16, hy + 82, "第50章  墨大夫阵亡", fs=13, color=C_RED)
    b.text(epx + 16, hy + 104, "互动章节: 1, 2, 3, 5, 10, 45, 50", fs=12, color=C_BLUE)
    b.text(epx + 16, hy + 130, "点击章节号可跳转阅读 →", fs=12, color=C_BLUE)

    return b.build()


# ═══════════════════════════════════════════════════════════════════════════════
#  WORLD MAP PAGE
# ═══════════════════════════════════════════════════════════════════════════════
def build_map():
    b = ExcalidrawBuilder()
    W, H = 1440, 900

    # ── Section 1: Spatial Map View ──────────────────────────────────────────
    b.text(50, 15, "1. 世界地图 — 空间地图视图（默认）", fs=24, color=C_ORANGE_ANNO)

    ox, oy = 50, 50
    b.rect(ox, oy, W, H, color=C_DARK, sw=2)
    draw_top_nav(b, ox, oy, W, active_tab="世界地图")

    # Chapter range slider
    sl_y = oy + 48
    b.rect(ox, sl_y, W, 40, bg=C_BG, color=C_BORDER)
    b.text(ox + 16, sl_y + 10, "章节范围:", fs=13, color=C_GRAY)
    b.rect(ox + 110, sl_y + 13, 700, 14, bg="#e9ecef", color="transparent", rnd={"type": 3})
    b.rect(ox + 110, sl_y + 13, 340, 14, bg=C_BLUE, color="transparent", rnd={"type": 3})
    b.ellipse(ox + 108, sl_y + 9, 20, 20, bg=C_BG_WHITE, color=C_BLUE, sw=2)
    b.ellipse(ox + 448, sl_y + 9, 20, 20, bg=C_BG_WHITE, color=C_BLUE, sw=2)
    b.text(ox + 830, sl_y + 10, "第 1 章 — 第 120 章", fs=13, color=C_GRAY)

    # View toggle tabs (spatial | hierarchy)
    vt_y = sl_y + 40
    b.rect(ox, vt_y, W, 32, bg=C_BG, color=C_BORDER)
    b.text(ox + 16, vt_y + 7, "空间地图", fs=14, color=C_BLUE)
    b.line(ox + 16, vt_y + 28, ox + 90, vt_y + 28, color=C_BLUE, sw=2)
    b.text(ox + 110, vt_y + 7, "层级地图", fs=14, color=C_GRAY)

    # Right filter panel (this time on the right)
    fp_w = 260
    fp_x = ox + W - fp_w
    fp_y = vt_y + 32
    fp_h = H - 48 - 40 - 32 - 48
    b.rect(fp_x, fp_y, fp_w, fp_h, bg=C_BG, color=C_BORDER)
    b.text(fp_x + 16, fp_y + 12, "筛选 / 轨迹", fs=16)

    # Filter: location type
    ffy = fp_y + 45
    b.text(fp_x + 16, ffy, "地点类型", fs=13, color=C_GRAY)
    for i, (label, checked) in enumerate([("国家/区域", True), ("城市", True),
                                           ("山脉/水域", True), ("门派", True), ("建筑", False)]):
        ck = "☑" if checked else "☐"
        b.text(fp_x + 20, ffy + 22 + i * 22, f"{ck} {label}", fs=12)

    # Filter: trajectory
    ffy2 = ffy + 140
    b.line(fp_x + 10, ffy2, fp_x + fp_w - 10, ffy2, color=C_BORDER)
    b.text(fp_x + 16, ffy2 + 10, "人物轨迹", fs=13, color=C_GRAY)
    b.rect(fp_x + 16, ffy2 + 32, fp_w - 32, 28, bg=C_BG_WHITE, color=C_BORDER_MED, rnd={"type": 3})
    b.text(fp_x + 24, ffy2 + 38, "搜索人物...", fs=12, color=C_LIGHT_GRAY)
    b.text(fp_x + 20, ffy2 + 70, "☑ 韩立", fs=12, color=C_CHAR)
    b.rect(fp_x + 100, ffy2 + 68, 14, 14, bg=C_CHAR, color=C_CHAR)
    b.text(fp_x + 20, ffy2 + 92, "☐ 南宫婉", fs=12, color=C_GRAY)
    b.rect(fp_x + fp_w - 100, ffy2 + 100, 80, 28, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
    b.text(fp_x + fp_w - 90, ffy2 + 106, "▶ 播放", fs=12, color=C_WHITE)

    # Filter: heatmap toggle
    ffy3 = ffy2 + 145
    b.line(fp_x + 10, ffy3, fp_x + fp_w - 10, ffy3, color=C_BORDER)
    b.text(fp_x + 16, ffy3 + 10, "叠加层", fs=13, color=C_GRAY)
    b.text(fp_x + 20, ffy3 + 34, "☐ 提及频率热力图", fs=12)
    b.text(fp_x + 20, ffy3 + 56, "☐ 最少提及 ≥ 2 章", fs=12)

    # Main map canvas
    mx = ox
    my = vt_y + 32
    mw = W - fp_w
    mh = H - 48 - 40 - 32 - 48
    b.rect(mx, my, mw, mh, bg="#faf5e8", color=C_BORDER)  # parchment bg

    # Map content: regions and locations
    # Large region: 越国
    b.rect(mx + 100, my + 60, 500, 400, bg="#e8f0e8", color="#a0c0a0", ss="dashed", opacity=30, rnd={"type": 3})
    b.text(mx + 280, my + 70, "越 国", fs=28, color="#6b8e6b", opacity=50)

    # Sub-region: 太南山脉
    b.rect(mx + 130, my + 120, 260, 280, bg="#d4e8d4", color="#7ca07c", ss="dashed", opacity=25, rnd={"type": 3})
    b.text(mx + 190, my + 130, "太南山脉", fs=16, color="#5a7a5a", opacity=60)

    # Location nodes
    locs = [
        (mx + 200, my + 200, "⛰ 七玄门", 20, "#2b8a3e"),
        (mx + 270, my + 280, "🌿 药园", 14, "#2b8a3e"),
        (mx + 180, my + 320, "🏛 藏经阁", 12, "#2b8a3e"),
        (mx + 340, my + 180, "⛰ 黄枫谷", 16, "#2b8a3e"),
        (mx + 480, my + 150, "🏰 越国王城", 18, "#e8590c"),
        (mx + 600, my + 300, "🏪 坊市", 14, "#e8590c"),
        (mx + 700, my + 180, "⛰ 落云山", 14, "#2b8a3e"),
    ]
    for lx, ly, label, fs_l, clr in locs:
        b.text(lx, ly, label, fs=fs_l, color=clr)

    # Trajectory line (韩立)
    traj_points = [
        (mx + 200, my + 215),   # 七玄门
        (mx + 270, my + 290),   # 药园
        (mx + 200, my + 215),   # 回七玄门
        (mx + 480, my + 165),   # 越国王城
        (mx + 600, my + 310),   # 坊市
    ]
    for i in range(len(traj_points) - 1):
        x1, y1 = traj_points[i]
        x2, y2 = traj_points[i + 1]
        b.arrow(x1, y1, x2, y2, color=C_CHAR, opacity=70)
    b.text(mx + 250, my + 245, "①", fs=11, color=C_CHAR)
    b.text(mx + 235, my + 260, "②", fs=11, color=C_CHAR)
    b.text(mx + 350, my + 210, "③", fs=11, color=C_CHAR)
    b.text(mx + 540, my + 230, "④", fs=11, color=C_CHAR)

    # Zoom level indicator
    b.rect(mx + 16, my + mh - 50, 180, 36, bg=C_BG_WHITE, color=C_BORDER, rnd={"type": 3})
    b.text(mx + 28, my + mh - 42, "缩放级别: 3 / 5", fs=13, color=C_GRAY)

    # Map toolbar
    tb_y = my + mh - 50
    b.rect(mx + mw // 2 - 120, tb_y, 240, 36, bg=C_BG_WHITE, color=C_BORDER, rnd={"type": 3})
    b.text(mx + mw // 2 - 105, tb_y + 8, "＋  −  ⟳  ⊞  📷", fs=16, color=C_GRAY)

    draw_qa_bar(b, ox, oy + H - 48, W)

    # ── Annotations ──
    ax = ox + W + 60
    b.text(ax, oy + 50, "空间地图交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(ax, oy + 85, "语义缩放（5 级）", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 110, "· L1 最远: 大洲/世界轮廓", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 135, "· L2: 国家/大区域 + 主要地标", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 160, "· L3: 城市/门派 + 路线", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 185, "· L4: 建筑/街道/设施", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 210, "· L5 最近: 房间/内部结构", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 240, "· 缩放过程平滑过渡，标签逐级显现", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 275, "地图操作", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 300, "· 点击地点 → 弹出地点卡片", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 325, "· hover → 浮层: 名称/类型/提及数", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 350, "· 长按 0.5s 拖拽 → 手动调整位置", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 380, "人物轨迹", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 405, "· 右侧面板选人物，带箭头曲线", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 430, "· 渐变色体现时间方向", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 455, "· ▶ 播放按钮: 轨迹按章节动画展开", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 480, "· 停留 >N 章的地点显示大圆点", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 510, "视觉风格", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 535, "· 羊皮纸/宣纸背景", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 560, "· 手绘风格线条和区域", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 585, "· 区域半透明色块按势力区分", fs=14, color=C_ORANGE_ANNO)

    # ── Section 2: Hierarchy Map View ────────────────────────────────────────
    s2y = oy + H + 100
    b.text(50, s2y - 35, "2. 世界地图 — 层级地图视图", fs=24, color=C_ORANGE_ANNO)

    hx, hy = 50, s2y
    hw, hh = 900, 500
    b.rect(hx, hy, hw, hh, bg=C_BG_WHITE, color=C_DARK, sw=2, rnd={"type": 3})

    # Tree structure
    tree = [
        (0, "▼ 越国", 18, C_DARK),
        (1, "▼ 太南山脉", 16, C_LOC),
        (2, "▼ 七玄门", 15, C_LOC),
        (3, "药园", 14, C_LOC),
        (3, "藏经阁", 14, C_LOC),
        (3, "练功房", 14, C_LOC),
        (3, "主峰大殿", 14, C_LOC),
        (2, "▶ 黄枫谷", 15, C_GRAY),
        (1, "▶ 越国王城", 16, C_GRAY),
        (1, "▶ 落云山", 16, C_GRAY),
    ]
    ty = hy + 20
    for indent, label, fs_t, clr in tree:
        b.text(hx + 30 + indent * 28, ty, label, fs=fs_t, color=clr)
        # node size indicator
        if indent >= 2 and "▼" not in label and "▶" not in label:
            b.rect(hx + hw - 120, ty, 80, 16, bg=C_LOC, color="transparent", opacity=20, rnd={"type": 3})
            b.text(hx + hw - 115, ty + 1, "12 章", fs=11, color=C_GRAY)
        ty += 35

    # Hierarchy annotations
    hax = hx + hw + 60
    b.text(hax, hy + 20, "层级地图交互", fs=18, color=C_ORANGE_ANNO)
    b.text(hax, hy + 50, "· 双击节点展开/折叠子地点", fs=14, color=C_ORANGE_ANNO)
    b.text(hax, hy + 75, "· 点击节点弹出地点卡片", fs=14, color=C_ORANGE_ANNO)
    b.text(hax, hy + 100, "· 「在空间地图中定位」按钮", fs=14, color=C_ORANGE_ANNO)
    b.text(hax, hy + 125, "· 节点大小映射提及章节数", fs=14, color=C_ORANGE_ANNO)
    b.text(hax, hy + 150, "· 颜色按类型: 自然(绿)/城镇(橙)", fs=14, color=C_ORANGE_ANNO)
    b.text(hax, hy + 175, "  /门派(蓝)/其他(灰)", fs=14, color=C_ORANGE_ANNO)
    b.text(hax, hy + 210, "也可用 Treemap 嵌套矩形模式展示", fs=14, color=C_ORANGE_ANNO)

    return b.build()


# ═══════════════════════════════════════════════════════════════════════════════
#  TIMELINE PAGE
# ═══════════════════════════════════════════════════════════════════════════════
def build_timeline():
    b = ExcalidrawBuilder()
    W, H = 1440, 900

    b.text(50, 15, "1. 时间线 — 单轨道 + 泳道模式", fs=24, color=C_ORANGE_ANNO)

    ox, oy = 50, 50
    b.rect(ox, oy, W, H, color=C_DARK, sw=2)
    draw_top_nav(b, ox, oy, W, active_tab="时间线")

    # Chapter range slider
    sl_y = oy + 48
    b.rect(ox, sl_y, W, 40, bg=C_BG, color=C_BORDER)
    b.text(ox + 16, sl_y + 10, "章节范围:", fs=13, color=C_GRAY)
    b.rect(ox + 110, sl_y + 13, 700, 14, bg="#e9ecef", color="transparent", rnd={"type": 3})
    b.rect(ox + 110, sl_y + 13, 700, 14, bg=C_BLUE, color="transparent", rnd={"type": 3})
    b.text(ox + 830, sl_y + 10, "全部 (第 1 — 120 章)", fs=13, color=C_GRAY)

    # Left filter panel
    fp_w = 220
    fp_x = ox
    fp_y = sl_y + 40
    fp_h = H - 48 - 40 - 48
    b.rect(fp_x, fp_y, fp_w, fp_h, bg=C_BG, color=C_BORDER)
    b.text(fp_x + 16, fp_y + 12, "筛选", fs=16)

    # Event type filter
    ffy = fp_y + 42
    b.text(fp_x + 16, ffy, "事件类型", fs=13, color=C_GRAY)
    evts = [("战斗", True, C_RED), ("成长", True, C_BLUE), ("社交", True, C_GREEN),
            ("旅行", True, "#e8590c"), ("其他", False, C_GRAY)]
    for i, (label, checked, clr) in enumerate(evts):
        ck = "☑" if checked else "☐"
        b.text(fp_x + 20, ffy + 22 + i * 22, f"{ck} {label}", fs=12, color=clr)

    # Character filter
    ffy2 = ffy + 140
    b.line(fp_x + 10, ffy2, fp_x + fp_w - 10, ffy2, color=C_BORDER)
    b.text(fp_x + 16, ffy2 + 10, "涉及人物", fs=13, color=C_GRAY)
    for i, (name, checked) in enumerate([("全部", True), ("韩立", False), ("墨大夫", False)]):
        ck = "☑" if checked else "☐"
        b.text(fp_x + 20, ffy2 + 32 + i * 22, f"{ck} {name}", fs=12)

    # View mode
    ffy3 = ffy2 + 110
    b.line(fp_x + 10, ffy3, fp_x + fp_w - 10, ffy3, color=C_BORDER)
    b.text(fp_x + 16, ffy3 + 10, "视图模式", fs=13, color=C_GRAY)
    b.text(fp_x + 20, ffy3 + 34, "◉ 单轨道", fs=12, color=C_BLUE)
    b.text(fp_x + 20, ffy3 + 56, "○ 多泳道（按人物）", fs=12, color=C_GRAY)

    # Importance filter
    ffy4 = ffy3 + 85
    b.line(fp_x + 10, ffy4, fp_x + fp_w - 10, ffy4, color=C_BORDER)
    b.text(fp_x + 16, ffy4 + 10, "重要度阈值", fs=13, color=C_GRAY)
    b.rect(fp_x + 16, ffy4 + 32, 180, 10, bg="#e9ecef", color="transparent", rnd={"type": 3})
    b.rect(fp_x + 16, ffy4 + 32, 40, 10, bg=C_BLUE, color="transparent", rnd={"type": 3})
    b.text(fp_x + 16, ffy4 + 50, "≥ 低", fs=12, color=C_GRAY)

    # Main timeline canvas
    tx = ox + fp_w
    ty = sl_y + 40
    tw = W - fp_w
    th = H - 48 - 40 - 48
    b.rect(tx, ty, tw, th, bg=C_BG_WHITE, color=C_BORDER)

    # Horizontal axis
    axis_y = ty + th // 2
    b.line(tx + 40, axis_y, tx + tw - 40, axis_y, color=C_BORDER, sw=2)

    # Chapter markers
    for i in range(7):
        cx = tx + 80 + i * 150
        b.line(cx, axis_y - 5, cx, axis_y + 5, color=C_GRAY)
        ch_num = 1 + i * 20
        b.text(cx - 10, axis_y + 12, f"第{ch_num}章", fs=10, color=C_GRAY)

    # Event nodes along the axis
    events = [
        (tx + 100, axis_y - 80, 16, C_GREEN, "入门七玄门"),
        (tx + 170, axis_y + 50, 12, C_BLUE, "开始修炼"),
        (tx + 300, axis_y - 60, 20, C_RED, "七玄门之战"),
        (tx + 440, axis_y + 40, 10, "#e8590c", "前往坊市"),
        (tx + 530, axis_y - 90, 14, C_GREEN, "结识南宫婉"),
        (tx + 680, axis_y + 60, 18, C_BLUE, "筑基成功"),
        (tx + 800, axis_y - 50, 12, C_RED, "遭遇魔修"),
        (tx + 950, axis_y + 40, 10, "#e8590c", "离开越国"),
    ]
    for ex, ey, r, clr, label in events:
        b.ellipse(ex - r // 2, ey - r // 2, r, r, bg=clr, color=clr)
        b.line(ex, ey + r // 2, ex, axis_y, color=clr, opacity=30, ss="dashed")
        b.text(ex - len(label) * 6, ey - r - 16, label, fs=11, color=clr)

    # Hovered event detail popover
    hpx = tx + 270
    hpy = axis_y - 180
    b.rect(hpx, hpy, 280, 100, bg=C_BG_WHITE, color=C_BORDER, sw=2, rnd={"type": 3})
    b.text(hpx + 12, hpy + 8, "七玄门之战", fs=16, color=C_RED)
    b.text(hpx + 12, hpy + 32, "涉及: 韩立、墨大夫、魔道修士", fs=12)
    b.text(hpx + 12, hpy + 52, "地点: 七玄门", fs=12)
    b.text(hpx + 12, hpy + 72, "章节: 第 45-50 章  点击跳转 →", fs=12, color=C_BLUE)

    draw_qa_bar(b, ox, oy + H - 48, W)

    # ── Annotations ──
    ax = ox + W + 60
    b.text(ax, oy + 50, "时间线交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(ax, oy + 85, "坐标轴", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 110, "· 横轴默认: 章节编号", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 135, "· 可切换: 故事内时间", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 165, "事件节点", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 190, "· 大小 = 重要度", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 215, "· 颜色 = 类型 (战斗/成长/社交/旅行)", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 240, "· hover → 事件摘要浮层", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 265, "· 点击 → 跳转到该章节阅读", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 295, "画布操作", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 320, "· 左右拖拽平移", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 345, "· 滚轮缩放时间粒度", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 370, "· 框选区域放大", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 400, "多泳道模式", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 425, "· 每个人物一条横向轨道", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 450, "· 适合对比多人物事件节奏", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 475, "· 在筛选面板切换模式", fs=14, color=C_ORANGE_ANNO)

    # ── Section 2: Swimlane Mode ─────────────────────────────────────────────
    s2y = oy + H + 100
    b.text(50, s2y - 35, "2. 时间线 — 多泳道模式（按人物分行）", fs=24, color=C_ORANGE_ANNO)

    sx, sy = 100, s2y
    sw, sh = 1000, 300
    b.rect(sx, sy, sw, sh, bg=C_BG_WHITE, color=C_DARK, sw=2, rnd={"type": 3})

    # Swimlanes
    lanes = ["韩立", "墨大夫", "张铁"]
    lane_h = sh // len(lanes)
    for i, name in enumerate(lanes):
        ly = sy + i * lane_h
        if i > 0:
            b.line(sx, ly, sx + sw, ly, color=C_BORDER)
        b.text(sx + 12, ly + lane_h // 2 - 8, name, fs=14, color=C_CHAR)
        # Axis line
        b.line(sx + 80, ly + lane_h // 2, sx + sw - 20, ly + lane_h // 2, color="#e9ecef")
        # Sample events
        for j in range(4 + (2 - i)):
            ex = sx + 120 + j * 140 + (i * 30)
            if ex < sx + sw - 40:
                r = 8 + (j % 3) * 4
                clrs = [C_RED, C_BLUE, C_GREEN, "#e8590c", C_BLUE, C_RED]
                b.ellipse(ex - r // 2, ly + lane_h // 2 - r // 2, r, r,
                         bg=clrs[j % len(clrs)], color=clrs[j % len(clrs)])

    # Chapter axis at bottom
    b.line(sx + 80, sy + sh - 20, sx + sw - 20, sy + sh - 20, color=C_GRAY)
    for i in range(6):
        cx = sx + 120 + i * 150
        b.text(cx, sy + sh - 15, f"第{1 + i * 20}章", fs=10, color=C_GRAY)

    return b.build()


# ═══════════════════════════════════════════════════════════════════════════════
#  FACTIONS PAGE
# ═══════════════════════════════════════════════════════════════════════════════
def build_factions():
    b = ExcalidrawBuilder()
    W, H = 1440, 900

    b.text(50, 15, "1. 势力图 — 组织关系网络", fs=24, color=C_ORANGE_ANNO)

    ox, oy = 50, 50
    b.rect(ox, oy, W, H, color=C_DARK, sw=2)
    draw_top_nav(b, ox, oy, W, active_tab="势力图")

    # Chapter range slider
    sl_y = oy + 48
    b.rect(ox, sl_y, W, 40, bg=C_BG, color=C_BORDER)
    b.text(ox + 16, sl_y + 10, "章节范围:", fs=13, color=C_GRAY)
    b.rect(ox + 110, sl_y + 13, 700, 14, bg="#e9ecef", color="transparent", rnd={"type": 3})
    b.rect(ox + 110, sl_y + 13, 700, 14, bg=C_BLUE, color="transparent", rnd={"type": 3})
    b.text(ox + 830, sl_y + 10, "全部", fs=13, color=C_GRAY)

    # Left filter panel
    fp_w = 220
    fp_y = sl_y + 40
    fp_h = H - 48 - 40 - 48
    b.rect(ox, fp_y, fp_w, fp_h, bg=C_BG, color=C_BORDER)
    b.text(ox + 16, fp_y + 12, "筛选", fs=16)

    ffy = fp_y + 42
    b.text(ox + 16, ffy, "组织类型", fs=13, color=C_GRAY)
    for i, (label, checked) in enumerate([("门派", True), ("家族", True), ("国家", True), ("帮派", False)]):
        ck = "☑" if checked else "☐"
        b.text(ox + 20, ffy + 22 + i * 22, f"{ck} {label}", fs=12)

    ffy2 = ffy + 115
    b.line(ox + 10, ffy2, ox + fp_w - 10, ffy2, color=C_BORDER)
    b.text(ox + 16, ffy2 + 10, "关系类型", fs=13, color=C_GRAY)
    for i, (label, checked, clr) in enumerate([("盟友", True, C_GREEN), ("敌对", True, C_RED),
                                                ("从属", True, C_BLUE), ("竞争", True, "#e8590c")]):
        ck = "☑" if checked else "☐"
        b.text(ox + 20, ffy2 + 32 + i * 22, f"{ck} {label}", fs=12, color=clr)

    ffy3 = ffy2 + 130
    b.line(ox + 10, ffy3, ox + fp_w - 10, ffy3, color=C_BORDER)
    b.text(ox + 16, ffy3 + 10, "最少成员", fs=13, color=C_GRAY)
    b.rect(ox + 16, ffy3 + 32, 180, 10, bg="#e9ecef", color="transparent", rnd={"type": 3})
    b.rect(ox + 16, ffy3 + 32, 30, 10, bg=C_BLUE, color="transparent", rnd={"type": 3})
    b.text(ox + 16, ffy3 + 50, "≥ 2 人", fs=12, color=C_GRAY)

    # Main graph canvas
    gx = ox + fp_w
    gy = sl_y + 40
    gw = W - fp_w
    gh = H - 48 - 40 - 48
    b.rect(gx, gy, gw, gh, bg=C_BG_WHITE, color=C_BORDER)

    # Organization nodes (larger, with member count)
    orgs = [
        (gx + 450, gy + 200, 60, "七玄门", "32人", C_ORG),
        (gx + 750, gy + 180, 50, "黄枫谷", "28人", C_ORG),
        (gx + 300, gy + 400, 45, "掩月宗", "18人", C_ORG),
        (gx + 650, gy + 420, 40, "魔道", "45人", C_RED),
        (gx + 900, gy + 350, 35, "御灵宗", "15人", C_ORG),
        (gx + 200, gy + 200, 70, "越 国", "#e8590c"),
    ]
    for item in orgs:
        if len(item) == 7:
            nx, ny, r, label, members, clr = item[0], item[1], item[2], item[3], item[4], item[5]
        else:
            nx, ny, r, label, clr = item[0], item[1], item[2], item[3], item[4]
            members = None
        b.ellipse(nx - r, ny - r, r * 2, r * 2, bg=clr, color=clr, opacity=20)
        b.ellipse(nx - r + 6, ny - r + 6, r * 2 - 12, r * 2 - 12, bg=clr, color=clr, opacity=40)
        b.text(nx - len(label) * 8, ny - 8, label, fs=15, color=clr)
        if members:
            b.text(nx - 12, ny + 12, members, fs=11, color=C_GRAY)

    # Edges between orgs
    org_edges = [
        (gx + 450, gy + 200, gx + 750, gy + 180, C_GREEN, "盟友"),         # 七玄门-黄枫谷
        (gx + 450, gy + 200, gx + 300, gy + 400, C_GREEN, "盟友"),         # 七玄门-掩月宗
        (gx + 450, gy + 200, gx + 650, gy + 420, C_RED, "敌对"),           # 七玄门-魔道
        (gx + 750, gy + 180, gx + 650, gy + 420, C_RED, "敌对"),           # 黄枫谷-魔道
        (gx + 450, gy + 200, gx + 200, gy + 200, C_BLUE, "从属", "dashed"),  # 七玄门-越国
        (gx + 750, gy + 180, gx + 200, gy + 200, C_BLUE, "从属", "dashed"),
    ]
    for edge in org_edges:
        x1, y1, x2, y2, clr, label = edge[0], edge[1], edge[2], edge[3], edge[4], edge[5]
        ss = edge[6] if len(edge) > 6 else "solid"
        b.line(x1, y1, x2, y2, color=clr, opacity=60, ss=ss)
        mx, my = (x1 + x2) // 2, (y1 + y2) // 2
        b.text(mx - 10, my - 15, label, fs=10, color=clr)

    # Toolbar
    tb_y = gy + gh - 50
    b.rect(gx + gw // 2 - 120, tb_y, 240, 36, bg=C_BG_WHITE, color=C_BORDER, rnd={"type": 3})
    b.text(gx + gw // 2 - 105, tb_y + 8, "＋  −  ⟳  ⊞  📷", fs=16, color=C_GRAY)

    draw_qa_bar(b, ox, oy + H - 48, W)

    # ── Annotations ──
    ax = ox + W + 60
    b.text(ax, oy + 50, "势力图交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(ax, oy + 85, "节点", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 110, "· 大小 = 成员数量", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 135, "· 颜色 = 门派(蓝)/家族(橙)/国家(紫)", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 160, "· hover → 浮层: 成员数/据点数", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 185, "· 点击 → 组织卡片", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 210, "· 双击 → 展开内部结构", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 235, "  (内部机构 + 核心成员 + 职位)", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 270, "边", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 295, "· 盟友=绿实线 敌对=红实线", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 320, "· 从属=蓝虚线 竞争=橙虚线", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 345, "· hover → 关系详情", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 370, "· 点击 → 关系详情浮层", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 405, "视图联动", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 430, "· 点击组织 → 关系图自动筛选该组织", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 455, "· 章节范围滑块与其他视图共享", fs=14, color=C_ORANGE_ANNO)

    # ── Section 2: Expanded Org ──────────────────────────────────────────────
    s2y = oy + H + 100
    b.text(50, s2y - 35, "2. 组织内部展开视图（双击节点触发）", fs=24, color=C_ORANGE_ANNO)

    ex, ey = 100, s2y
    ew, eh = 600, 350
    b.rect(ex, ey, ew, eh, bg=C_BG_WHITE, color=C_ORG, sw=2, rnd={"type": 3})
    b.text(ex + 20, ey + 15, "七玄门 — 内部结构", fs=18, color=C_ORG)
    b.text(ex + ew - 80, ey + 18, "收起 ✕", fs=13, color=C_GRAY)
    b.line(ex + 10, ey + 45, ex + ew - 10, ey + 45, color=C_BORDER)

    # Internal departments
    depts = [("百药堂", "药修", 3), ("百锻堂", "器修", 2), ("执法堂", "战修", 5), ("外门", "杂务", 12)]
    for i, (name, desc, count) in enumerate(depts):
        dx = ex + 30 + (i % 2) * 280
        dy = ey + 60 + (i // 2) * 120
        b.rect(dx, dy, 250, 90, bg=C_BG, color=C_BORDER, rnd={"type": 3})
        b.text(dx + 12, dy + 8, name, fs=15, color=C_ORG)
        b.text(dx + 12, dy + 30, f"类型: {desc}", fs=12, color=C_GRAY)
        b.text(dx + 12, dy + 50, f"核心成员: {count} 人", fs=12, color=C_GRAY)
        b.text(dx + 12, dy + 70, "查看成员 →", fs=12, color=C_BLUE)

    return b.build()


# ═══════════════════════════════════════════════════════════════════════════════
#  CHAT (FULL SCREEN) PAGE
# ═══════════════════════════════════════════════════════════════════════════════
def build_chat():
    b = ExcalidrawBuilder()
    W, H = 1440, 900

    b.text(50, 15, "1. 问答全屏页 — 深度对话模式", fs=24, color=C_ORANGE_ANNO)

    ox, oy = 50, 50
    b.rect(ox, oy, W, H, color=C_DARK, sw=2)
    draw_top_nav(b, ox, oy, W, active_tab=None)

    # Left sidebar: conversation list
    sb_w = 280
    sb_x = ox
    sb_y = oy + 48
    sb_h = H - 48
    b.rect(sb_x, sb_y, sb_w, sb_h, bg=C_BG, color=C_BORDER)

    b.text(sb_x + 16, sb_y + 14, "对话列表", fs=16)
    b.rect(sb_x + sb_w - 80, sb_y + 10, 65, 28, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
    b.text(sb_x + sb_w - 72, sb_y + 16, "+ 新对话", fs=12, color=C_WHITE)

    # Conversation items
    convos = [
        ("韩立的师承关系", "3 天前", True),
        ("七玄门之战分析", "5 天前", False),
        ("修炼体系总结", "1 周前", False),
        ("人物关系梳理", "2 周前", False),
    ]
    for i, (title, time, is_active) in enumerate(convos):
        cy = sb_y + 55 + i * 56
        if is_active:
            b.rect(sb_x + 8, cy - 4, sb_w - 16, 50, bg="#e7f0fd", color="transparent", rnd={"type": 3})
        b.text(sb_x + 20, cy + 4, title, fs=14, color=C_BLUE if is_active else C_BLACK)
        b.text(sb_x + 20, cy + 26, time, fs=11, color=C_LIGHT_GRAY)
        b.text(sb_x + sb_w - 30, cy + 10, "⋯", fs=16, color=C_LIGHT_GRAY)

    # Export button at bottom
    b.line(sb_x + 10, sb_y + sb_h - 50, sb_x + sb_w - 10, sb_y + sb_h - 50, color=C_BORDER)
    b.text(sb_x + 16, sb_y + sb_h - 35, "📥 导出当前对话为 Markdown", fs=12, color=C_BLUE)

    # Main chat area
    ch_x = ox + sb_w
    ch_y = oy + 48
    ch_w = W - sb_w
    ch_h = H - 48
    b.rect(ch_x, ch_y, ch_w, ch_h, bg=C_BG_WHITE, color=C_BORDER)

    # Chat title
    b.text(ch_x + 30, ch_y + 14, "韩立的师承关系", fs=18)
    b.line(ch_x + 10, ch_y + 45, ch_x + ch_w - 10, ch_y + 45, color=C_BORDER)

    # Messages
    msg_x = ch_x + 40
    msg_w = ch_w - 80

    # User msg 1
    my1 = ch_y + 65
    b.rect(msg_x + msg_w - 300, my1, 290, 32, bg="#e7f0fd", color="transparent", rnd={"type": 3})
    b.text(msg_x + msg_w - 290, my1 + 7, "韩立的师傅是谁？", fs=14, color=C_BLUE)

    # AI response 1
    my2 = my1 + 50
    b.text(msg_x, my2, "韩立有两位师傅：", fs=14)
    b.text(msg_x, my2 + 26, "1.", fs=14)
    b.text(msg_x + 20, my2 + 26, "墨大夫", fs=14, color=C_CHAR)
    b.text(msg_x + 75, my2 + 26, "— 在药园传授基础药理（第 3 章起），启蒙恩师", fs=14)
    b.text(msg_x, my2 + 52, "2.", fs=14)
    b.text(msg_x + 20, my2 + 52, "李化元", fs=14, color=C_CHAR)
    b.text(msg_x + 75, my2 + 52, "— 在七玄门内门指导修炼（第 25 章起），传授御剑术", fs=14)
    b.text(msg_x, my2 + 84, "来源: ", fs=12, color=C_GRAY)
    b.text(msg_x + 42, my2 + 84, "第 3、10、25、26 章", fs=12, color=C_BLUE)

    # User msg 2
    my3 = my2 + 120
    b.rect(msg_x + msg_w - 280, my3, 270, 32, bg="#e7f0fd", color="transparent", rnd={"type": 3})
    b.text(msg_x + msg_w - 270, my3 + 7, "墨大夫后来怎么了？", fs=14, color=C_BLUE)

    # AI response 2
    my4 = my3 + 50
    b.text(msg_x, my4, "墨大夫", fs=14, color=C_CHAR)
    b.text(msg_x + 55, my4, "在第 50 章", fs=14)
    b.text(msg_x + 135, my4, "七玄门", fs=14, color=C_ORG)
    b.text(msg_x + 185, my4, "遭", fs=14)
    b.text(msg_x + 205, my4, "魔道", fs=14, color=C_RED)
    b.text(msg_x + 242, my4, "偷袭时，为掩护弟子撤退而阵亡。", fs=14)
    b.text(msg_x, my4 + 28, "来源: ", fs=12, color=C_GRAY)
    b.text(msg_x + 42, my4 + 28, "第 50 章", fs=12, color=C_BLUE)
    b.text(msg_x, my4 + 55, "基于已分析的 120 章内容", fs=11, color=C_LIGHT_GRAY)

    # Input area
    inp_y = ch_y + ch_h - 70
    b.line(ch_x + 10, inp_y, ch_x + ch_w - 10, inp_y, color=C_BORDER)
    b.rect(ch_x + 20, inp_y + 14, ch_w - 120, 40, bg=C_BG, color=C_BORDER_MED, rnd={"type": 3})
    b.text(ch_x + 40, inp_y + 24, "继续提问...", fs=14, color=C_LIGHT_GRAY)
    b.rect(ch_x + ch_w - 85, inp_y + 14, 65, 40, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
    b.text(ch_x + ch_w - 72, inp_y + 24, "发送", fs=14, color=C_WHITE)

    # ── Annotations ──
    ax = ox + W + 60
    b.text(ax, oy + 50, "全屏问答交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(ax, oy + 85, "对话管理", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 110, "· 左侧侧栏管理多个对话", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 135, "· 「+ 新对话」清空上下文开始新主题", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 160, "· ⋯ 菜单: 重命名 / 删除", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 185, "· 导出为 Markdown 文件", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 220, "答案交互", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 245, "· 流式输出（逐字显现）", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 270, "· 实体名高亮可点击 → 实体卡片", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 295, "· 章节号可点击 → 跳转阅读", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 320, "· 来源可展开查看原文片段", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 345, "· 部分分析标注「基于 X 章」", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 380, "与浮动面板关系", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 405, "· 共享对话上下文", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 430, "· 浮动面板「全屏模式」跳转到此页", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 455, "· ← 返回可回到之前的页面", fs=14, color=C_ORANGE_ANNO)

    return b.build()


# ═══════════════════════════════════════════════════════════════════════════════
#  ENCYCLOPEDIA PAGE
# ═══════════════════════════════════════════════════════════════════════════════
def build_encyclopedia():
    b = ExcalidrawBuilder()
    W, H = 1440, 900

    b.text(50, 15, "1. 百科页 — 实体索引与概念词条", fs=24, color=C_ORANGE_ANNO)

    ox, oy = 50, 50
    b.rect(ox, oy, W, H, color=C_DARK, sw=2)
    draw_top_nav(b, ox, oy, W, active_tab="百科")

    # Left category navigation
    cn_w = 240
    cn_x = ox
    cn_y = oy + 48
    cn_h = H - 48 - 48
    b.rect(cn_x, cn_y, cn_w, cn_h, bg=C_BG, color=C_BORDER)
    b.text(cn_x + 16, cn_y + 14, "分类导航", fs=16)

    # Category tree
    cats = [
        (0, "全部 (1,892)", True),
        (1, "人物 (456)", False),
        (1, "地点 (238)", False),
        (1, "物品 (312)", False),
        (1, "组织 (89)", False),
        (1, "概念 (797)", False),
        (2, "修炼体系 (45)", False),
        (2, "种族 (23)", False),
        (2, "货币/资源 (18)", False),
        (2, "功法/技能 (156)", False),
        (2, "其他 (555)", False),
    ]
    cy = cn_y + 48
    for indent, label, is_active in cats:
        if is_active:
            b.rect(cn_x + 6, cy - 3, cn_w - 12, 24, bg="#e7f0fd", color="transparent", rnd={"type": 3})
        clr = C_BLUE if is_active else (C_BLACK if indent <= 1 else C_GRAY)
        b.text(cn_x + 16 + indent * 18, cy, label, fs=13, color=clr)
        cy += 28

    # Main content area
    ct_x = ox + cn_w
    ct_y = oy + 48
    ct_w = W - cn_w
    ct_h = H - 48 - 48
    b.rect(ct_x, ct_y, ct_w, ct_h, bg=C_BG_WHITE, color=C_BORDER)

    # Search bar
    b.rect(ct_x + 20, ct_y + 14, 400, 34, bg=C_BG, color=C_BORDER_MED, rnd={"type": 3})
    b.text(ct_x + 36, ct_y + 22, "🔍 搜索词条...", fs=13, color=C_LIGHT_GRAY)
    b.text(ct_x + ct_w - 200, ct_y + 22, "排序: 名称 ▾", fs=13, color=C_GRAY)

    # Entry list
    entries = [
        ("韩立", "人物", "凡人修仙传主角，原为贫苦农家子弟", "第 1 章", C_CHAR),
        ("墨大夫", "人物", "韩立启蒙师父，七玄门药修长老", "第 1 章", C_CHAR),
        ("七玄门", "组织", "越国太南山脉修仙门派", "第 1 章", C_ORG),
        ("筑基期", "概念", "修仙第二大境界，寿命可达数百年", "第 5 章", C_CONCEPT),
        ("筑基丹", "物品", "助修士突破筑基的丹药", "第 8 章", C_ITEM),
        ("落云山", "地点", "七玄门所在山脉主峰", "第 2 章", C_LOC),
    ]
    ey = ct_y + 65
    for name, etype, desc, first_ch, clr in entries:
        b.rect(ct_x + 20, ey, ct_w - 40, 60, bg=C_BG_WHITE, color=C_BORDER, rnd={"type": 3})
        b.text(ct_x + 35, ey + 8, name, fs=16, color=clr)
        b.rect(ct_x + 35 + len(name) * 16 + 10, ey + 10, len(etype) * 13 + 14, 20,
               bg=C_BG, color=clr, rnd={"type": 3})
        b.text(ct_x + 35 + len(name) * 16 + 17, ey + 13, etype, fs=11, color=clr)
        b.text(ct_x + 35, ey + 34, desc, fs=12, color=C_GRAY)
        b.text(ct_x + ct_w - 100, ey + 8, first_ch, fs=12, color=C_LIGHT_GRAY)
        ey += 70

    # Pagination
    b.text(ct_x + ct_w // 2 - 60, ct_y + ct_h - 40, "< 1 2 3 ... 38 >", fs=14, color=C_BLUE)

    draw_qa_bar(b, ox, oy + H - 48, W)

    # ── Annotations ──
    ax = ox + W + 60
    b.text(ax, oy + 50, "百科页交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(ax, oy + 85, "分类导航", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 110, "· 点击分类筛选右侧列表", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 135, "· 概念下有子分类可展开", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 160, "· 显示各分类数量统计", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 195, "词条交互", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 220, "· 点击人物/地点/物品/组织", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 245, "  → 弹出实体卡片（右侧抽屉）", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 270, "· 点击概念词条", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 295, "  → 打开概念详情页", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 325, "概念详情", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 350, "· 定义 + 原文摘录(1-3条)", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 375, "· 首次提及章节(可跳转)", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 400, "· 关联概念(可点击)", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 425, "· 关联实体(可点击弹卡片)", fs=14, color=C_ORANGE_ANNO)

    # ── Section 2: Concept Detail View ───────────────────────────────────────
    s2y = oy + H + 100
    b.text(50, s2y - 35, "2. 概念词条详情（点击概念后展开）", fs=24, color=C_ORANGE_ANNO)

    dx, dy = 100, s2y
    dw, dh = 800, 450
    b.rect(dx, dy, dw, dh, bg=C_BG_WHITE, color=C_DARK, sw=2, rnd={"type": 3})

    b.rect(dx + 20, dy + 18, 45, 22, bg=C_BG, color=C_CONCEPT, rnd={"type": 3})
    b.text(dx + 28, dy + 21, "概念", fs=12, color=C_CONCEPT)
    b.text(dx + 80, dy + 16, "筑基期", fs=22)
    b.text(dx + 190, dy + 22, "修炼体系", fs=13, color=C_GRAY)

    b.line(dx + 15, dy + 50, dx + dw - 15, dy + 50, color=C_BORDER)

    # Definition
    b.text(dx + 20, dy + 62, "定义", fs=14, color=C_GRAY)
    b.text(dx + 20, dy + 84, "修仙第二大境界。修士在练气期巅峰服用筑基丹或凭自身悟性突破后", fs=14)
    b.text(dx + 20, dy + 106, "进入此境界，寿命可达数百年，实力远超练气期。", fs=14)
    b.text(dx + 20, dy + 128, "筑基期分为初期、中期、后期三个小境界。", fs=14)

    # First mention
    b.text(dx + 20, dy + 162, "首次提及", fs=14, color=C_GRAY)
    b.text(dx + 100, dy + 162, "第 5 章", fs=14, color=C_BLUE)

    # Quotes
    b.text(dx + 20, dy + 196, "原文摘录", fs=14, color=C_GRAY)
    b.rect(dx + 20, dy + 218, dw - 40, 50, bg=C_BG, color=C_BORDER, rnd={"type": 3})
    b.text(dx + 32, dy + 224, "第 23 章：「筑基成功后，韩立只觉体内灵力暴涨数倍，", fs=12)
    b.text(dx + 32, dy + 244, "感知范围扩大了不止一筹。」", fs=12)

    b.rect(dx + 20, dy + 278, dw - 40, 40, bg=C_BG, color=C_BORDER, rnd={"type": 3})
    b.text(dx + 32, dy + 284, "第 42 章：「筑基丹可助练气期巅峰修士强行突破，但有三成失败风险。」", fs=12)

    # Related
    b.text(dx + 20, dy + 335, "关联概念", fs=14, color=C_GRAY)
    b.text(dx + 100, dy + 335, "练气期", fs=14, color=C_CONCEPT)
    b.text(dx + 170, dy + 335, "→", fs=14, color=C_GRAY)
    b.text(dx + 195, dy + 335, "筑基期", fs=14, color=C_BLUE)
    b.text(dx + 265, dy + 335, "→", fs=14, color=C_GRAY)
    b.text(dx + 290, dy + 335, "结丹期", fs=14, color=C_CONCEPT)

    b.text(dx + 20, dy + 365, "关联实体", fs=14, color=C_GRAY)
    b.text(dx + 100, dy + 365, "韩立", fs=14, color=C_CHAR)
    b.text(dx + 150, dy + 365, "墨大夫", fs=14, color=C_CHAR)
    b.text(dx + 215, dy + 365, "筑基丹", fs=14, color=C_ITEM)

    return b.build()


# ═══════════════════════════════════════════════════════════════════════════════
#  ANALYSIS PAGE
# ═══════════════════════════════════════════════════════════════════════════════
def build_analysis():
    b = ExcalidrawBuilder()
    W, H = 1440, 900

    b.text(50, 15, "1. 分析页 — 分析管理与统计", fs=24, color=C_ORANGE_ANNO)

    ox, oy = 50, 50
    b.rect(ox, oy, W, H, color=C_DARK, sw=2)
    draw_top_nav(b, ox, oy, W, active_tab="分析")

    ct_y = oy + 48
    b.rect(ox, ct_y, W, H - 48, bg=C_BG_WHITE, color=C_BORDER)

    # Analysis status header
    b.text(ox + 40, ct_y + 20, "《凡人修仙传》 分析状态", fs=22)

    # Progress section
    py = ct_y + 65
    b.rect(ox + 40, py, W - 80, 180, bg=C_BG, color=C_BORDER, rnd={"type": 3})
    b.text(ox + 60, py + 16, "分析进度", fs=16)
    # Status badge
    b.rect(ox + 175, py + 14, 70, 24, bg="#fff3cd", color="#e8590c", rnd={"type": 3})
    b.text(ox + 183, py + 18, "分析中", fs=13, color="#e8590c")

    # Progress bar
    b.rect(ox + 60, py + 55, W - 160, 20, bg="#e9ecef", color="transparent", rnd={"type": 3})
    b.rect(ox + 60, py + 55, (W - 160) * 120 // 2451, 20, bg=C_BLUE, color="transparent", rnd={"type": 3})
    b.text(ox + 60 + (W - 160) * 120 // 2451 + 10, py + 56, "4.9%", fs=13, color=C_BLUE)

    # Stats
    b.text(ox + 60, py + 92, "当前: 第 120 / 2451 章", fs=14)
    b.text(ox + 60, py + 116, "已提取: 1,245 个实体  |  3,567 条关系  |  892 个事件", fs=14, color=C_GRAY)
    b.text(ox + 60, py + 140, "预计剩余: 约 2331 章待分析", fs=14, color=C_GRAY)

    # Action buttons
    b.rect(ox + W - 310, py + 130, 100, 36, color=C_BORDER_MED, rnd={"type": 3})
    b.text(ox + W - 288, py + 139, "暂停", fs=14, color=C_GRAY)
    b.rect(ox + W - 195, py + 130, 100, 36, color=C_RED, rnd={"type": 3})
    b.text(ox + W - 173, py + 139, "取消", fs=14, color=C_RED)

    # Chapter analysis detail
    dy = py + 200
    b.text(ox + 40, dy, "章节分析详情", fs=18)

    # Analysis mode selector
    b.rect(ox + 40, dy + 35, 160, 34, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
    b.text(ox + 55, dy + 43, "分析全部章节", fs=13, color=C_WHITE)
    b.rect(ox + 220, dy + 35, 160, 34, color=C_BORDER_MED, rnd={"type": 3})
    b.text(ox + 240, dy + 43, "指定范围分析", fs=13, color=C_GRAY)
    b.rect(ox + 400, dy + 35, 160, 34, color=C_BORDER_MED, rnd={"type": 3})
    b.text(ox + 425, dy + 43, "重新分析", fs=13, color=C_GRAY)

    # Chapter status table
    ty = dy + 85
    # Header
    b.rect(ox + 40, ty, W - 80, 30, bg=C_BG, color=C_BORDER)
    headers = [("章节", 40), ("标题", 180), ("状态", 420), ("实体数", 560), ("关系数", 660), ("操作", 770)]
    for label, hx_off in headers:
        b.text(ox + 40 + hx_off, ty + 7, label, fs=12, color=C_GRAY)

    # Rows
    rows = [
        ("第 1 章", "穷山僻壤", "✓ 已完成", C_GREEN, "23", "45", ""),
        ("第 2 章", "墨大夫", "✓ 已完成", C_GREEN, "18", "32", ""),
        ("第 3 章", "七玄门", "✓ 已完成", C_GREEN, "31", "67", ""),
        ("...", "", "", C_GRAY, "", "", ""),
        ("第 120 章", "灵药园", "● 分析中", C_BLUE, "—", "—", ""),
        ("第 121 章", "夺舍", "○ 待分析", C_LIGHT_GRAY, "—", "—", "分析"),
        ("第 122 章", "鬼灵门", "○ 待分析", C_LIGHT_GRAY, "—", "—", "分析"),
    ]
    for i, (ch, title, status, clr, ent, rel, action) in enumerate(rows):
        ry = ty + 30 + i * 32
        if i % 2 == 0:
            b.rect(ox + 40, ry, W - 80, 32, bg=C_BG, color="transparent", opacity=30)
        b.text(ox + 80, ry + 8, ch, fs=12, color=C_GRAY if ch == "..." else C_BLACK)
        b.text(ox + 220, ry + 8, title, fs=12)
        b.text(ox + 460, ry + 8, status, fs=12, color=clr)
        b.text(ox + 600, ry + 8, ent, fs=12, color=C_GRAY)
        b.text(ox + 700, ry + 8, rel, fs=12, color=C_GRAY)
        if action:
            b.text(ox + 810, ry + 8, action, fs=12, color=C_BLUE)

    # Statistics summary at bottom
    sty = ty + 30 + len(rows) * 32 + 20
    b.line(ox + 40, sty, ox + W - 40, sty, color=C_BORDER)
    b.text(ox + 40, sty + 15, "统计概览", fs=16)

    stats = [("实体总数", "1,245"), ("关系总数", "3,567"), ("事件总数", "892"),
             ("已分析章节", "120 / 2451")]
    for i, (label, val) in enumerate(stats):
        sx = ox + 60 + i * 220
        b.rect(sx, sty + 42, 180, 60, bg=C_BG, color=C_BORDER, rnd={"type": 3})
        b.text(sx + 15, sty + 52, label, fs=12, color=C_GRAY)
        b.text(sx + 15, sty + 72, val, fs=20, color=C_BLUE)

    # ── Annotations ──
    ax = ox + W + 60
    b.text(ax, oy + 50, "分析页交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(ax, oy + 85, "分析控制", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 110, "· 三种模式: 全部/指定范围/重新分析", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 135, "· 暂停: 临时中断，可恢复", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 160, "· 取消: 终止任务，数据保留", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 185, "· 进度通过 WebSocket 实时更新", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 220, "章节状态", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 245, "· ✓ 绿色=已完成  ● 蓝色=分析中", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 270, "· ● 红色=失败(可重试)  ○ 灰色=待分析", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 295, "· 单章可独立触发分析", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 330, "重新分析", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 355, "· 选择章节范围 → 确认覆盖", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 380, "· 用于更换模型后更新数据", fs=14, color=C_ORANGE_ANNO)

    return b.build()


# ═══════════════════════════════════════════════════════════════════════════════
#  SETTINGS PAGE
# ═══════════════════════════════════════════════════════════════════════════════
def build_settings():
    b = ExcalidrawBuilder()
    W, H = 1440, 900

    b.text(50, 15, "1. 设置页 — 全局配置", fs=24, color=C_ORANGE_ANNO)

    ox, oy = 50, 50
    b.rect(ox, oy, W, H, color=C_DARK, sw=2)

    # Simple top bar (no novel context)
    b.rect(ox, oy, W, 48, bg=C_BG, color=C_BORDER)
    b.text(ox + 16, oy + 13, "← 返回", fs=14, color=C_BLUE)
    b.text(ox + 100, oy + 12, "设置", fs=18)

    # Left navigation
    nav_w = 220
    nav_y = oy + 48
    nav_h = H - 48
    b.rect(ox, nav_y, nav_w, nav_h, bg=C_BG, color=C_BORDER)

    sections = [
        ("LLM 模型配置", True),
        ("阅读偏好", False),
        ("数据管理", False),
        ("关于", False),
    ]
    for i, (label, is_active) in enumerate(sections):
        sy = nav_y + 12 + i * 40
        if is_active:
            b.rect(ox + 6, sy - 2, nav_w - 12, 32, bg="#e7f0fd", color="transparent", rnd={"type": 3})
        b.text(ox + 20, sy + 5, label, fs=14, color=C_BLUE if is_active else C_GRAY)

    # Main content
    ct_x = ox + nav_w
    ct_y = oy + 48
    ct_w = W - nav_w
    ct_h = H - 48
    b.rect(ct_x, ct_y, ct_w, ct_h, bg=C_BG_WHITE, color=C_BORDER)

    b.text(ct_x + 40, ct_y + 25, "LLM 模型配置", fs=22)
    b.line(ct_x + 20, ct_y + 60, ct_x + ct_w - 20, ct_y + 60, color=C_BORDER)

    # Ollama status
    fy = ct_y + 80
    b.text(ct_x + 40, fy, "Ollama 服务状态", fs=16)
    b.rect(ct_x + 230, fy - 2, 80, 24, bg="#d3f9d8", color=C_GREEN, rnd={"type": 3})
    b.text(ct_x + 240, fy + 2, "运行中", fs=12, color=C_GREEN)
    b.text(ct_x + 40, fy + 30, "地址: http://localhost:11434", fs=13, color=C_GRAY)

    # Model selection
    fy2 = fy + 70
    b.text(ct_x + 40, fy2, "推理模型", fs=16)
    b.rect(ct_x + 40, fy2 + 28, 400, 36, bg=C_BG_WHITE, color=C_BORDER_MED, rnd={"type": 3})
    b.text(ct_x + 55, fy2 + 36, "qwen2.5:7b", fs=14)
    b.text(ct_x + 390, fy2 + 36, "▾", fs=14, color=C_GRAY)
    b.text(ct_x + 460, fy2 + 36, "4.7GB · 推荐", fs=13, color=C_GREEN)

    # Embedding model
    fy3 = fy2 + 80
    b.text(ct_x + 40, fy3, "Embedding 模型", fs=16)
    b.rect(ct_x + 40, fy3 + 28, 400, 36, bg=C_BG_WHITE, color=C_BORDER_MED, rnd={"type": 3})
    b.text(ct_x + 55, fy3 + 36, "bge-base-zh-v1.5", fs=14)
    b.text(ct_x + 390, fy3 + 36, "▾", fs=14, color=C_GRAY)

    # Temperature
    fy4 = fy3 + 80
    b.text(ct_x + 40, fy4, "Temperature", fs=16)
    b.rect(ct_x + 40, fy4 + 28, 300, 12, bg="#e9ecef", color="transparent", rnd={"type": 3})
    b.rect(ct_x + 40, fy4 + 28, 90, 12, bg=C_BLUE, color="transparent", rnd={"type": 3})
    b.text(ct_x + 350, fy4 + 25, "0.3", fs=14, color=C_BLUE)
    b.text(ct_x + 40, fy4 + 48, "较低=更准确  较高=更有创意", fs=12, color=C_GRAY)

    # Timeout
    fy5 = fy4 + 80
    b.text(ct_x + 40, fy5, "超时时间", fs=16)
    b.rect(ct_x + 40, fy5 + 28, 150, 36, bg=C_BG_WHITE, color=C_BORDER_MED, rnd={"type": 3})
    b.text(ct_x + 55, fy5 + 36, "120", fs=14)
    b.text(ct_x + 200, fy5 + 36, "秒", fs=14, color=C_GRAY)

    # Reading preferences section hint
    fy6 = fy5 + 90
    b.line(ct_x + 20, fy6, ct_x + ct_w - 20, fy6, color=C_BORDER)
    b.text(ct_x + 40, fy6 + 15, "环境检测", fs=16)
    b.rect(ct_x + 40, fy6 + 45, 140, 36, bg=C_BG, color=C_BORDER_MED, rnd={"type": 3})
    b.text(ct_x + 52, fy6 + 53, "重新检测环境", fs=13, color=C_GRAY)
    b.text(ct_x + 200, fy6 + 53, "触发首次使用引导中的环境检测流程", fs=12, color=C_GRAY)

    # Save button
    b.rect(ct_x + ct_w - 160, ct_y + ct_h - 65, 120, 40, bg=C_BLUE, color=C_BLUE, rnd={"type": 3})
    b.text(ct_x + ct_w - 140, ct_y + ct_h - 55, "保存设置", fs=14, color=C_WHITE)

    # ── Annotations ──
    ax = ox + W + 60
    b.text(ax, oy + 50, "设置页交互说明", fs=20, color=C_ORANGE_ANNO)
    b.text(ax, oy + 85, "四个设置分区", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 110, "· LLM 模型配置: 模型/参数/服务地址", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 135, "· 阅读偏好: 字号/行距/主题(亮/暗)", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 160, "· 数据管理: 清除缓存/导出数据", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 185, "· 关于: 版本信息/开源协议", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 220, "模型配置", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 245, "· 下拉框列出已安装的 Ollama 模型", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 270, "· 实时检测 Ollama 服务状态", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 295, "· 「重新检测环境」重走引导流程", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 325, "全局入口", fs=16, color=C_ORANGE_ANNO)
    b.text(ax, oy + 350, "· 从任何页面顶栏 ⚙ 进入", fs=14, color=C_ORANGE_ANNO)
    b.text(ax, oy + 375, "· ← 返回之前所在页面", fs=14, color=C_ORANGE_ANNO)

    return b.build()


# ═══════════════════════════════════════════════════════════════════════════════
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def save(data, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    count = len(data["elements"])
    print(f"  ✓ {filename} ({count} elements)")


ALL_PAGES = {
    "bookshelf":    ("01-bookshelf.excalidraw",    build_bookshelf),
    "reading":      ("02-reading.excalidraw",      build_reading),
    "graph":        ("03-graph.excalidraw",        build_graph),
    "map":          ("04-map.excalidraw",          build_map),
    "timeline":     ("05-timeline.excalidraw",     build_timeline),
    "factions":     ("06-factions.excalidraw",     build_factions),
    "chat":         ("07-chat.excalidraw",         build_chat),
    "encyclopedia": ("08-encyclopedia.excalidraw", build_encyclopedia),
    "analysis":     ("09-analysis.excalidraw",     build_analysis),
    "settings":     ("10-settings.excalidraw",     build_settings),
}


def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else list(ALL_PAGES.keys())

    print("Generating AI Reader V2 wireframes...")
    for target in targets:
        if target in ALL_PAGES:
            filename, builder = ALL_PAGES[target]
            save(builder(), filename)
        else:
            print(f"  ? Unknown target: {target}")
            print(f"    Available: {', '.join(ALL_PAGES.keys())}")
    print("Done.")


if __name__ == "__main__":
    main()
