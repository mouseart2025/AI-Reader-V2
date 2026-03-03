#!/bin/bash
# scripts/sync-internal.sh
# 将内部文档同步到私有备份仓库
# 同步方向：单向（AI-Reader-V2 → ai-reader-internal）

set -euo pipefail

INTERNAL_REPO="$HOME/ai-reader-internal"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# 检查私有仓库是否存在
if [ ! -d "$INTERNAL_REPO/.git" ]; then
    echo "错误: 私有仓库 $INTERNAL_REPO 不存在"
    echo "请先运行: gh repo create ai-reader-internal --private --clone"
    exit 1
fi

echo "🔄 开始同步内部文档..."
echo "   源: $PROJECT_ROOT"
echo "   目标: $INTERNAL_REPO"
echo ""

# === 必须同步的目录 ===

echo "📂 同步 _bmad/ ..."
rsync -av --delete --exclude='.git/' \
    "$PROJECT_ROOT/_bmad/" "$INTERNAL_REPO/_bmad/"

echo "📂 同步 _bmad-output/ ..."
rsync -av --delete --exclude='.git/' \
    "$PROJECT_ROOT/_bmad-output/" "$INTERNAL_REPO/_bmad-output/"

# === 可选文件和目录 ===

[ -f "$PROJECT_ROOT/PRD.md" ] && {
    echo "📄 同步 PRD.md ..."
    cp "$PROJECT_ROOT/PRD.md" "$INTERNAL_REPO/"
}

[ -f "$PROJECT_ROOT/PRD-v1.0.md" ] && {
    echo "📄 同步 PRD-v1.0.md ..."
    cp "$PROJECT_ROOT/PRD-v1.0.md" "$INTERNAL_REPO/"
}

[ -d "$PROJECT_ROOT/interaction-design" ] && {
    echo "📂 同步 interaction-design/ ..."
    rsync -av --delete \
        "$PROJECT_ROOT/interaction-design/" "$INTERNAL_REPO/interaction-design/"
}

[ -d "$PROJECT_ROOT/docs" ] && {
    echo "📂 同步 docs/ ..."
    rsync -av --delete \
        "$PROJECT_ROOT/docs/" "$INTERNAL_REPO/docs/"
}

# === CLAUDE.md 完整版备份 ===
echo "📄 备份 CLAUDE.md → CLAUDE-full.md ..."
cp "$PROJECT_ROOT/CLAUDE.md" "$INTERNAL_REPO/CLAUDE-full.md"

echo ""
echo "✅ 同步完成！请手动检查并提交："
echo "   cd $INTERNAL_REPO"
echo "   git add ."
echo "   git commit -m 'sync: $(date +%Y-%m-%d) 内部文档同步'"
echo "   git push"
