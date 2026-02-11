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
#  MAIN
# ═══════════════════════════════════════════════════════════════════════════════
def save(data, filename):
    path = os.path.join(os.path.dirname(os.path.abspath(__file__)), filename)
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)
    count = len(data["elements"])
    print(f"  ✓ {filename} ({count} elements)")


def main():
    targets = sys.argv[1:] if len(sys.argv) > 1 else ["bookshelf", "reading"]

    print("Generating AI Reader V2 wireframes...")
    for target in targets:
        if target == "bookshelf":
            save(build_bookshelf(), "01-bookshelf.excalidraw")
        elif target == "reading":
            save(build_reading(), "02-reading.excalidraw")
        else:
            print(f"  ? Unknown target: {target}")
    print("Done.")


if __name__ == "__main__":
    main()
