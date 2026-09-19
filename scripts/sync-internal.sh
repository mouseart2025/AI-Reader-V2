#!/bin/bash
# scripts/sync-internal.sh
# 将内部文档/试验产物同步到私有备份仓库
# 同步方向：单向（AI-Reader-V2 → ai-reader-internal）
# 边界规则见 CLAUDE.md「仓库边界与推送规则」

set -euo pipefail

INTERNAL_REPO="${AI_READER_INTERNAL:-$HOME/Baiduyun/AISoul/ai-reader-internal}"
PROJECT_ROOT="$(cd "$(dirname "$0")/.." && pwd)"

# 检查私有仓库是否存在
if [ ! -d "$INTERNAL_REPO/.git" ]; then
    echo "错误: 私有仓库 $INTERNAL_REPO 不存在"
    echo "可用 AI_READER_INTERNAL 环境变量指定路径"
    exit 1
fi

echo "🔄 开始同步内部文档..."
echo "   源: $PROJECT_ROOT"
echo "   目标: $INTERNAL_REPO"
echo ""

# === 必须同步的目录 ===

for d in _bmad _bmad-output; do
    [ -d "$PROJECT_ROOT/$d" ] && {
        echo "📂 同步 $d/ ..."
        rsync -av --delete --exclude='.git/' \
            "$PROJECT_ROOT/$d/" "$INTERNAL_REPO/$d/"
    }
done

# === 可选文件和目录 ===

for f in PRD-v0-draft.md PRD-v1.0.md; do
    [ -f "$PROJECT_ROOT/$f" ] && {
        echo "📄 同步 $f ..."
        cp "$f" "$INTERNAL_REPO/"
    }
done

for d in interaction-design docs; do
    [ -d "$PROJECT_ROOT/$d" ] && {
        echo "📂 同步 $d/ ..."
        rsync -av --delete \
            "$PROJECT_ROOT/$d/" "$INTERNAL_REPO/$d/"
    }
done

# 自进化试验日志(本地未跟踪工作副本 → internal 归档快照)
if [ -f "$PROJECT_ROOT/backend/scripts/evolve/evolution_journal.jsonl" ]; then
    echo "📄 同步 evolve/evolution_journal.jsonl ..."
    mkdir -p "$INTERNAL_REPO/backend/scripts/evolve"
    cp "$PROJECT_ROOT/backend/scripts/evolve/evolution_journal.jsonl" \
       "$INTERNAL_REPO/backend/scripts/evolve/evolution_journal.jsonl"
fi

# === CLAUDE.md 完整版备份 ===
echo "📄 备份 CLAUDE.md → CLAUDE-full.md ..."
cp "$PROJECT_ROOT/CLAUDE.md" "$INTERNAL_REPO/CLAUDE-full.md"

echo ""
echo "✅ 同步完成！请手动检查并提交："
echo "   cd $INTERNAL_REPO"
echo "   git add ."
echo "   git commit -m 'sync: $(date +%Y-%m-%d) 内部文档同步'"
echo "   (push 需用户明确指示,见 CLAUDE.md「仓库边界与推送规则」)"
