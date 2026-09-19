#!/bin/bash
# scripts/check-repo-boundary.sh
# 仓库边界检查:禁止内部研究/试验产物被跟踪进公开仓库。
# 内部内容的 canonical 位置是 ai-reader-internal 私有仓库(镜像相同相对路径)。
# 本地与 CI(.github/workflows/repo-hygiene.yml)共用此脚本。

set -euo pipefail
cd "$(dirname "$0")/.."

# 禁止被 git 跟踪的模式(git ls-files 输出上的正则,每行一个)
FORBIDDEN_PATTERNS=(
  '^_bmad'                          # BMAD 规划/故事工件
  '^docs/'                          # 研究分析文档(docs/analysis 等)
  '^interaction-design/'            # 交互设计稿
  '^PRD-v[0-9]'                     # PRD 草稿
  '研究报告.*\.md$'                  # 根目录中文研究报告
  '^LLM驱动.*\.md$'
  '^AI驱动.*\.md$'
  '^自动文学制图学.*\.md$'
  '^marketing/'                     # 营销系统(含真实配置风险)
  '^backend/audit_reports/'         # 质量审计报告
  '^backend/data/hierarchy_validation/(backups|history|candidates)/'  # 层级试验快照/史
  '^backend/data/review/archive/'   # 标注评审归档
  '^scripts/qa-review/'             # 跨模型 QA 试验
  '^scripts/paper-shots'            # 论文截图/E2E 工作区
  'evolution_journal\.jsonl$'       # 自进化试验日志(本地保留,同步 internal)
  '^session-.*\.zip$'               # CLI 会话备份
  '\.bak'                           # 本地备份
)

fail=0
for pat in "${FORBIDDEN_PATTERNS[@]}"; do
  matches=$(git ls-files | grep -E "$pat" || true)
  if [ -n "$matches" ]; then
    echo "❌ 边界违规 (pattern: $pat):"
    echo "$matches" | sed 's/^/     /'
    fail=1
  fi
done

if [ "$fail" -ne 0 ]; then
  echo ""
  echo "以上文件属于内部产物,应迁移到 ai-reader-internal 私有仓库(镜像相同相对路径),"
  echo "并从本仓库 git rm。规则详见 CLAUDE.md「仓库边界与推送规则」。"
  exit 1
fi
echo "✅ 仓库边界检查通过:公开仓库无内部产物被跟踪"
