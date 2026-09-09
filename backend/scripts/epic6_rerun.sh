#!/bin/bash
# Epic 5/6 demo 五本全量重跑驱动(顺序执行)。
# 每本:POST force 分析 → 轮询完成 → 缓冲等待后台任务(实体消解/层级重建)
# → 记录 world_structures 更新时间。日志:backend/audit_reports/epic6_rerun.log
#
# 环境变量:
#   AI_READER_API_BASE  后端地址(默认 http://localhost:8100)
#   AI_READER_DATA_DIR  数据目录(默认 ~/.ai-reader-v2),用于记录层级更新时间
#   WAIT_SECS           后台任务缓冲等待秒数(默认 900)
#   MAX_POLLS           单本最大轮询轮数(默认 240,每轮 60s ≈ 4h)
set -u
BASE="${AI_READER_API_BASE:-http://localhost:8100}"
DATA_DIR="${AI_READER_DATA_DIR:-$HOME/.ai-reader-v2}"
WAIT_SECS="${WAIT_SECS:-900}"
MAX_POLLS="${MAX_POLLS:-240}"
LOG="$(dirname "$0")/../audit_reports/epic6_rerun.log"
mkdir -p "$(dirname "$LOG")"

# slug:novel_id:总章数(顺序 = 执行顺序)
# xiyouji 在 Epic 6 时因 pilot 已完成而跳过;Epic 5 改了 geo 管线(tier 判级 +
# Edmonds 根可达子图),而层级结果完全依赖 chapter_facts 里的票 —— 旧抽取
# (2026-04)有票地点占比过低、零证据遗留边占比过高,必须全五本重抽。
NOVELS=(
  "xiyouji:3b2ef56c-1a55-466a-a7d1-34272446a198:100"
  "shuihu:4ac43c73-f67b-427c-8d6d-e766a1423977:121"
  "sanguo:b1287ef6-c215-4bd2-842c-cb04aec5eb70:120"
  "honglou:c384901a-8b71-437a-af35-b5ec1c56c696:122"
  "fengshen:53013970-effd-4f50-aef7-728ca13de69a:90"
)

log() { echo "[$(date '+%H:%M:%S')] $*" | tee -a "$LOG"; }

log "===== 重跑开始 base=$BASE data=$DATA_DIR ====="

for entry in "${NOVELS[@]}"; do
  slug="${entry%%:*}"; rest="${entry#*:}"; nid="${rest%%:*}"; total="${rest##*:}"
  log "=== $slug ($nid) 开始 force 全量分析 ($total 章) ==="
  resp=$(curl -s -X POST "$BASE/api/novels/$nid/analyze" \
    -H 'Content-Type: application/json' -d '{"force":true}')
  log "analyze resp: $resp"
  echo "$resp" | grep -q '"task_id"' || { log "$slug 启动失败,跳过"; continue; }

  # 轮询直至 completed/failed(每 60s,MAX_POLLS 封顶防死循环)
  polls=0
  status="?"
  while [ "$polls" -lt "$MAX_POLLS" ]; do
    sleep 60
    polls=$((polls + 1))
    st=$(curl -s "$BASE/api/novels/$nid/analysis/latest")
    read -r status cur <<<"$(echo "$st" | python3 -c '
import sys, json
try:
    t = json.load(sys.stdin).get("task", {})
    print(t.get("status", "?"), t.get("current_chapter", "?"))
except Exception:
    print("? ?")
')"
    if [ "$status" = "completed" ] || [ "$status" = "failed" ]; then
      log "$slug 分析结束: status=$status (轮询 ${polls} 轮)"
      break
    fi
    log "$slug 进度: $cur/$total ($status)"
  done

  if [ "$polls" -ge "$MAX_POLLS" ]; then
    log "$slug 轮询超时(MAX_POLLS=$MAX_POLLS),终止后续"; exit 1
  fi
  [ "$status" = "completed" ] || { log "$slug 失败,终止后续"; exit 1; }

  # 缓冲等待后台任务(空间补全/实体消解/层级重建)
  log "$slug 等待后台任务 ${WAIT_SECS}s..."
  sleep "$WAIT_SECS"

  # 记录 world_structures 更新时间 —— 确认后台层级重建确实跑过,
  # 而不是拿旧的 world_structure 交付。
  ws_ts=$(python3 - "$DATA_DIR/data.db" "$nid" <<'PY'
import sqlite3, sys
db, nid = sys.argv[1], sys.argv[2]
try:
    con = sqlite3.connect(f"file:{db}?mode=ro", uri=True)
    row = con.execute(
        "SELECT updated_at FROM world_structures WHERE novel_id=?", (nid,)
    ).fetchone()
    print(row[0] if row else "MISSING")
except Exception as exc:  # noqa: BLE001
    print(f"ERR:{exc}")
PY
)
  log "$slug world_structures.updated_at = $ws_ts"
  log "=== $slug 完成 ==="
done

log "===== 全部 ${#NOVELS[@]} 本重跑完成 ====="
