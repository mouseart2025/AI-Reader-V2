#!/bin/bash
# Epic 6 demo 五本全量重跑驱动(对 scratch 后端 :8100,顺序执行)。
# 每本:POST force 分析 → 轮询完成 → 缓冲等待后台任务(实体消解/层级重建)
# → 记录 world_structures 更新时间。日志:backend/audit_reports/epic6_rerun.log
set -u
BASE=http://localhost:8100
LOG="$(dirname "$0")/../audit_reports/epic6_rerun.log"
mkdir -p "$(dirname "$LOG")"

# slug:novel_id:总章数(顺序 = 执行顺序;西游已于 pilot 后首轮完成)
NOVELS=(
  "shuihu:4ac43c73-f67b-427c-8d6d-e766a1423977:121"
  "sanguo:b1287ef6-c215-4bd2-842c-cb04aec5eb70:120"
  "honglou:c384901a-8b71-437a-af35-b5ec1c56c696:122"
  "fengshen:53013970-effd-4f50-aef7-728ca13de69a:90"
)

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

for entry in "${NOVELS[@]}"; do
  slug="${entry%%:*}"; rest="${entry#*:}"; nid="${rest%%:*}"; total="${rest##*:}"
  log "=== $slug ($nid) 开始 force 全量分析 ($total 章) ==="
  resp=$(curl -s -X POST "$BASE/api/novels/$nid/analyze" \
    -H 'Content-Type: application/json' -d '{"force":true}')
  log "analyze resp: $resp"
  echo "$resp" | grep -q '"task_id"' || { log "$slug 启动失败,跳过"; continue; }

  # 轮询直至 completed/failed(每 60s,单本无硬超时)
  while true; do
    sleep 60
    st=$(curl -s "$BASE/api/novels/$nid/analysis/latest")
    status=$(echo "$st" | python3 -c "import sys,json;print(json.load(sys.stdin).get('task',{}).get('status','?'))" 2>/dev/null || echo "?")
    cur=$(echo "$st" | python3 -c "import sys,json;print(json.load(sys.stdin).get('task',{}).get('current_chapter','?'))" 2>/dev/null || echo "?")
    if [ "$status" = "completed" ] || [ "$status" = "failed" ]; then
      log "$slug 分析结束: status=$status"
      break
    fi
    log "$slug 进度: $cur/$total ($status)"
  done
  [ "$status" = "completed" ] || { log "$slug 失败,终止后续"; exit 1; }

  # 缓冲等待后台任务(空间补全/实体消解/层级重建)
  log "$slug 等待后台任务 15 分钟..."
  sleep 900
  log "=== $slug 完成 ==="
done

log "全部五本重跑完成"
