#!/usr/bin/env bash
# snapshot-repo-stats.sh — 记录仓库的"社区资产"基线（star / fork / watcher / 下载 / issue）
#
# 用途：任何不可逆操作（改可见性、迁移、改名）之前先跑一次，事后才能说清损失。
# 背景：2026-10-11 为 ARR 匿名期把公开仓私有化，没量基线，导致 star 数永久不可考。
#
# 用法：
#   ./scripts/snapshot-repo-stats.sh                  # 默认 mouseart2025/AI-Reader-V2
#   ./scripts/snapshot-repo-stats.sh owner/repo       # 指定仓库
#   OUT_DIR=/tmp ./scripts/snapshot-repo-stats.sh     # 指定输出目录
#
# 输出：<OUT_DIR>/repo-stats-YYYY-MM-DD.json（默认 OUT_DIR=docs，注意 docs/ 在本仓被 gitignore）
#       归档基线请显式指定内部仓：OUT_DIR=../ai-reader-internal/metrics ./scripts/snapshot-repo-stats.sh
# 依赖：gh（已登录，需 repo 读权限）
set -euo pipefail

REPO="${1:-mouseart2025/AI-Reader-V2}"
OUT_DIR="${OUT_DIR:-$(cd "$(dirname "$0")/.." && pwd)/docs}"
STAMP="$(date +%Y-%m-%d)"
OUT="$OUT_DIR/repo-stats-$STAMP.json"

command -v gh >/dev/null || { echo "❌ 需要 gh CLI（brew install gh）"; exit 1; }
gh auth status >/dev/null 2>&1 || { echo "❌ gh 未登录：gh auth login"; exit 1; }

mkdir -p "$OUT_DIR"

echo "📊 采集 $REPO ..."

repo_json="$(gh api "repos/$REPO")"
issues_count="$(gh api "search/issues?q=repo:$REPO+type:issue" --jq '.total_count' 2>/dev/null || echo null)"
open_issues="$(gh api "search/issues?q=repo:$REPO+type:issue+state:open" --jq '.total_count' 2>/dev/null || echo null)"
prs_count="$(gh api "search/issues?q=repo:$REPO+type:pr" --jq '.total_count' 2>/dev/null || echo null)"

# 每个 release 的资产下载量（判断真实使用规模的最佳代理，比 star 稳）
releases_json="$(
  gh api "repos/$REPO/releases?per_page=100" --paginate 2>/dev/null \
    | python3 -c '
import json, sys
items = []
decoder = json.JSONDecoder()
buf = sys.stdin.read().strip()
# --paginate 会拼接多个 JSON 数组，逐段解析
while buf:
    obj, idx = decoder.raw_decode(buf)
    items.extend(obj)
    buf = buf[idx:].lstrip()
out = []
for r in items:
    out.append({
        "tag": r.get("tag_name"),
        "published_at": r.get("published_at"),
        "assets": [
            {"name": a.get("name"), "downloads": a.get("download_count"), "size": a.get("size")}
            for a in (r.get("assets") or [])
        ],
        "total_downloads": sum((a.get("download_count") or 0) for a in (r.get("assets") or [])),
    })
print(json.dumps(out, ensure_ascii=False))
'
)"

python3 - "$repo_json" "$releases_json" "$issues_count" "$open_issues" "$prs_count" "$REPO" "$STAMP" > "$OUT" <<'PY'
import json, sys

repo = json.loads(sys.argv[1])
releases = json.loads(sys.argv[2])
snapshot = {
    "repo": sys.argv[6],
    "date": sys.argv[7],
    "visibility": repo.get("visibility"),
    "stars": repo.get("stargazers_count"),
    "forks": repo.get("forks_count"),
    "network": repo.get("network_count"),
    "watchers": repo.get("subscribers_count"),
    "open_issues_count": repo.get("open_issues_count"),
    "issues_total": json.loads(sys.argv[3]) if sys.argv[3] != "null" else None,
    "issues_open": json.loads(sys.argv[4]) if sys.argv[4] != "null" else None,
    "prs_total": json.loads(sys.argv[5]) if sys.argv[5] != "null" else None,
    "created_at": repo.get("created_at"),
    "pushed_at": repo.get("pushed_at"),
    "releases": releases,
    "total_release_downloads": sum(r["total_downloads"] for r in releases),
}
print(json.dumps(snapshot, ensure_ascii=False, indent=2))
PY

python3 - "$OUT" <<'PY'
import json, sys
d = json.load(open(sys.argv[1]))
print()
print(f"  visibility : {d['visibility']}")
print(f"  stars      : {d['stars']}")
print(f"  forks      : {d['forks']}   network: {d['network']}")
print(f"  watchers   : {d['watchers']}")
print(f"  issues     : {d['issues_total']} (open {d['issues_open']})   PRs: {d['prs_total']}")
print(f"  releases   : {len(d['releases'])}，累计下载 {d['total_release_downloads']}")
print(f"\n✅ 写入 {sys.argv[1]}")
PY
