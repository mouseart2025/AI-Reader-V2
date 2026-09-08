#!/usr/bin/env python3
"""demo 五本重跑:检测停滞并自动续跑(供定时任务调用)。

背景:2026-09-07 五本重跑到 07:16 集体卡死 —— 进程存活(端口 200)、
task.status 仍是 running,但协程挂死,8 小时零产出无人察觉。

停滞判据(两条,命中任一即停滞):
  1. 该本最后一章落库时间距今 > STALL_MINUTES(默认 45min,单章 3-9min)
  2. 最新任务状态不是 'running'(paused/cancelled/… 说明没有协程在跑)

⚠️ 三个踩过的坑,改本脚本前务必读:
  A. **实例启动会全局暂停任务**。每个 uvicorn 启动时跑 recover_stale_tasks()
     (src/db/analysis_task_store.py:196),SQL 是
         UPDATE analysis_tasks SET status='paused' WHERE status='running'
     **不带 novel_id 过滤**,而五本共用同一个 SQLite —— 于是第 N 本实例启动
     会把前 N-1 本刚触发的任务打成 paused。所以必须"全部 kill → 全部清任务
     → 全部拉起就绪 → 统一触发",不能逐本串行;并且**能不重启就不重启**。
  B. **清任务要同时清 paused**。API 判活是 `status IN ('running','paused')`
     (analysis_task_store.py:59),只清 running 会让 paused 任务继续挡住
     新任务 → HTTP 409 "already has an active task"。
  C. **别只看心跳**。updated_at 会被与产出无关的操作刷新(pause/resume、
     本脚本自己清任务),只看心跳会把彻底卡死的实例判成健康。

用法:
    python scripts/auto_resume_rerun.py              # 检测并自动恢复
    python scripts/auto_resume_rerun.py --dry-run    # 只报告不动作
    python scripts/auto_resume_rerun.py --stall-min 60
"""
from __future__ import annotations

import argparse
import datetime as dt
import json
import os
import signal
import sqlite3
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
SCRATCH = "~/.ai-reader-v2-rerun-20260906"
MODEL = "qwen-plus"

# port, slug, novel_id, total_chapters
NOVELS = [
    (8100, "sanguo",   "b1287ef6-c215-4bd2-842c-cb04aec5eb70", 120),
    (8101, "shuihu",   "4ac43c73-f67b-427c-8d6d-e766a1423977", 121),
    (8102, "honglou",  "c384901a-8b71-437a-af35-b5ec1c56c696", 122),
    (8103, "xiyouji",  "3b2ef56c-1a55-466a-a7d1-34272446a198", 100),
    (8104, "fengshen", "53013970-effd-4f50-aef7-728ca13de69a", 90),
]

STALL_MINUTES = 45

# ── 章节级重试计数 ──
# 某些章(如 shuihu 的 ch1「序章」)在任务里直接报错、永远产不出 fact。
# 若每次都从它续跑:任务秒退 → 下轮判停滞 → 重启实例 → 触发坑 A,把其他
# 四本一起暂停。于是"一本卡死"拖垮全部五本。这里记录每章尝试次数,
# 超过上限就跳过它,去补后面的大段缺口。
MAX_CHAPTER_ATTEMPTS = 2
STATE_PATH = Path(os.path.expanduser(SCRATCH)) / "resume_state.json"


def _db() -> Path:
    return Path(os.path.expanduser(SCRATCH)) / "data.db"


def _conn() -> sqlite3.Connection:
    return sqlite3.connect(f"file:{_db()}?mode=ro", uri=True)


def _load_state() -> dict:
    try:
        return json.loads(STATE_PATH.read_text())
    except Exception:
        return {}


def _save_state(s: dict) -> None:
    try:
        STATE_PATH.write_text(json.dumps(s, indent=2, ensure_ascii=False))
    except Exception as e:
        print(f"    (warn) 无法写 {STATE_PATH}: {e}")


def novel_state(conn: sqlite3.Connection, slug: str, nid: str,
                skip: set[int] | None = None) -> dict:
    done = sorted(
        r[0] for r in conn.execute(
            """SELECT ch.chapter_num FROM chapter_facts cf
               JOIN chapters ch ON ch.id = cf.chapter_id
               WHERE cf.novel_id=? AND cf.llm_model=?""", (nid, MODEL)
        )
    )
    last = conn.execute(
        """SELECT MAX(cf.extracted_at) FROM chapter_facts cf
           WHERE cf.novel_id=? AND cf.llm_model=?""", (nid, MODEL)
    ).fetchone()[0]
    task = conn.execute(
        """SELECT status, current_chapter, updated_at, created_at
           FROM analysis_tasks WHERE novel_id=? ORDER BY created_at DESC LIMIT 1""",
        (nid,)
    ).fetchone()
    # ── 缺口识别:必须按"缺口段"而不是 max(done)+1 ──
    # 失败被跳过的章会留下空洞(honglou 缺 45-58 而 59 已跑完、shuihu 缺 ch1),
    # 用 max(done)+1 会把这些空洞永久跳过;而 force=true 会把范围内已完成的
    # 章重跑一遍(shuihu 若从 ch1 跑到 121 要白白重跑 43 章)。
    # 因此:算出全部缺口,取**第一个连续缺口段**作为本次续跑区间。
    all_nums = sorted(
        r[0] for r in conn.execute(
            "SELECT chapter_num FROM chapters WHERE novel_id=?", (nid,))
    )
    done_set = set(done)
    missing = [n for n in all_nums if n not in done_set]
    if skip:
        missing = [n for n in missing if n not in skip]
    if missing:
        seg_start = seg_end = missing[0]
        for n in missing[1:]:
            if n == seg_end + 1:
                seg_end = n
            else:
                break
    else:
        seg_start = seg_end = None
    return {
        "slug": slug,
        "done": len(done),
        "missing": len(missing),
        "missing_list": missing,
        "next_chapter": seg_start,
        "seg_end": seg_end,
        "last_at": last,
        "task_status": task[0] if task else "-",
        "cursor": task[1] if task else "-",
        "task_updated": task[2] if task else None,
        "task_created": task[3] if task else None,
    }


def _kill_port(port: int) -> None:
    try:
        out = subprocess.run(["lsof", "-ti", f":{port}"],
                             capture_output=True, text=True, timeout=15).stdout.split()
    except Exception:
        return
    for pid in out:
        try:
            os.kill(int(pid), signal.SIGKILL)
            print(f"    killed pid {pid} (port {port})")
        except Exception:
            pass


def _clear_active(nid: str) -> None:
    """把该本的活跃任务置为 cancelled(坑 B:必须同时清 paused)。"""
    c = sqlite3.connect(str(_db()))
    cur = c.execute(
        "UPDATE analysis_tasks SET status='cancelled' "
        "WHERE novel_id=? AND status IN ('running','paused')",
        (nid,),
    )
    c.commit()
    c.close()
    if cur.rowcount:
        print(f"    cleared {cur.rowcount} active task(s)")


def _start_instance(port: int) -> None:
    env = dict(os.environ)
    env["AI_READER_DATA_DIR"] = os.path.expanduser(SCRATCH)
    env["AI_READER_FORCE_DB_KEY"] = "1"
    # ⚠️ 不要丢到 DEVNULL:实例日志是事后诊断"任务 completed_with_errors /
    # 协程挂死"唯一的现场证据。
    log_dir = BACKEND / "audit_reports"
    log_dir.mkdir(parents=True, exist_ok=True)
    log_path = log_dir / f"rerun_{port}.log"
    logf = open(log_path, "a")
    subprocess.Popen(
        ["uv", "run", "uvicorn", "src.api.main:app",
         "--host", "127.0.0.1", "--port", str(port)],
        cwd=str(BACKEND), env=env, start_new_session=True,
        stdout=logf, stderr=subprocess.STDOUT,
    )
    print(f"    日志 → {log_path}")


def _wait_ready(port: int, timeout: int = 150) -> bool:
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            with urllib.request.urlopen(
                f"http://127.0.0.1:{port}/api/settings", timeout=5
            ) as r:
                if r.status == 200:
                    return True
        except Exception:
            pass
        time.sleep(5)
    return False


def _trigger(port: int, nid: str, start: int, end: int) -> str | None:
    body = json.dumps({"chapter_start": start, "chapter_end": end,
                       "force": True}).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{port}/api/novels/{nid}/analyze",
        data=body, headers={"Content-Type": "application/json"},
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            return json.loads(r.read().decode()).get("task_id")
    except urllib.error.HTTPError as e:
        return f"HTTP {e.code}: {e.read().decode()[:120]}"
    except Exception as e:
        return f"ERR {type(e).__name__}: {e}"


def _ok(tid) -> bool:
    return bool(tid) and not str(tid).startswith(("HTTP", "ERR"))


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--stall-min", type=int, default=STALL_MINUTES)
    args = ap.parse_args()

    if not _db().exists():
        print(f"❌ 找不到 {_db()}", file=sys.stderr)
        sys.exit(1)

    conn = _conn()
    # ⚠️ SQLite 的 datetime('now') 写入的是 UTC,而 dt.datetime.now() 是本地时间。
    # 混用会凭空多出 8 小时(UTC+8),把正常跑着的实例误判为"卡死"。
    now = dt.datetime.now(dt.UTC).replace(tzinfo=None)
    print(f"[{now:%Y-%m-%d %H:%M:%S}] 停滞判据 > {args.stall_min} 分钟"
          f"{'  (dry-run)' if args.dry_run else ''}")

    state = _load_state()
    stalled, finished_books = [], []

    for port, slug, nid, total in NOVELS:
        raw = novel_state(conn, slug, nid)

        # ── 重试计数:上次触发的起始章,任务已结束却仍缺 → 记一次失败 ──
        s = state.setdefault(slug, {"attempts": {}})
        s.setdefault("attempts", {})
        last_start = s.get("last_start")
        if last_start is not None:
            ended = raw["task_status"] not in ("running", "paused")
            if ended and last_start in raw["missing_list"]:
                s["attempts"][str(last_start)] = \
                    int(s["attempts"].get(str(last_start), 0)) + 1
            s["last_start"] = None
        skip = {int(k) for k, v in s["attempts"].items()
                if int(v) >= MAX_CHAPTER_ATTEMPTS}

        st = novel_state(conn, slug, nid, skip) if skip else raw
        skipped_n = raw["missing"] - st["missing"]

        # 停滞判据:以"最后一章落库"为主(心跳会被无关操作刷新,见坑 C)
        if st["last_at"]:
            lag = (now - dt.datetime.strptime(st["last_at"],
                                              "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
            lag_s = f"{lag:.0f} 分钟前"
        else:
            lag, lag_s = float("inf"), "从未"

        running = raw["task_status"] == "running"
        finished = st["next_chapter"] is None and raw["missing"] == 0
        blocked = st["next_chapter"] is None and raw["missing"] > 0

        # ── 宽限期:刚触发的任务还没跑完第一章,别用"落章时间老"去杀它 ──
        # 一本刚从长缺口段(几十章)重新起跑时,last_at 可能是 1 小时前的,
        # 但任务本身才跑了 3 分钟。只看 last_at 会把刚救活的实例又掐死,
        # 并且 force 重跑还会退回上一个缺口段。任务创建 < stall 阈值 → 放行。
        if raw["task_created"]:
            age = (now - dt.datetime.strptime(raw["task_created"],
                                              "%Y-%m-%d %H:%M:%S")).total_seconds() / 60
        else:
            age = float("inf")
        in_grace = running and age < args.stall_min

        if finished:
            status = "完成"
            finished_books.append(slug)
        elif blocked:
            status = f"阻塞({raw['missing']}章反复失败,跳过)"
        elif not running:
            status = f"停滞(无活跃循环:{raw['task_status']})"
        elif in_grace:
            status = f"进行中(任务才起 {age:.0f} 分钟,宽限)"
        elif lag > args.stall_min:
            status = "停滞(超时无产出)"
        else:
            status = "进行中"

        gap = f"缺口{raw['missing']:>3}章" if raw["missing"] else "无缺口"
        extra = f" 跳过{skipped_n}" if skipped_n else ""
        print(f"  {slug:<10} {raw['done']:>3} 章  {gap}  "
              f"落章 {lag_s:<12} task={raw['task_status']:<22} → {status}{extra}")

        if not finished and not blocked and (not running
                                             or (lag > args.stall_min and not in_grace)):
            stalled.append((port, slug, nid, total,
                            st["next_chapter"], st["seg_end"]))
    conn.close()

    if finished_books:
        print(f"\n🎉 已完成: {', '.join(finished_books)}")
    if len(finished_books) == len(NOVELS):
        print("✅ 五本全部完成 —— 可以开始 Story 5.5 复测")

    if not stalled:
        print("\n✅ 无停滞,无需干预")
        _save_state(state)
        return

    if args.dry_run:
        print(f"\n⚠️ {len(stalled)} 本停滞(dry-run,不动作):")
        for port, slug, nid, total, start, seg_end in stalled:
            print(f"    [{slug}] port {port}, 补缺口 ch{start}-{seg_end}")
        _save_state(state)
        return

    print(f"\n⚠️ {len(stalled)} 本停滞,开始恢复:")

    # ── 阶段 1:只在实例真的死了才重启(坑 A:启动会全局暂停别人的任务)──
    needs_start = []
    for port, slug, nid, total, start, seg_end in stalled:
        if _wait_ready(port, timeout=8):
            print(f"  [{slug}] 实例 {port} 存活,直接复用(不重启)")
        else:
            print(f"  [{slug}] 实例 {port} 无响应,重启")
            _kill_port(port)
            needs_start.append((port, slug))

    if needs_start:
        time.sleep(3)
        print("\n  -- 拉起实例 --")
        for port, slug in needs_start:
            _start_instance(port)
        print("  -- 等待就绪 --")
        for port, slug in needs_start:
            if not _wait_ready(port):
                print(f"    ❌ 实例 {port} ({slug}) 未就绪")

    # ── 阶段 2:清任务 + 触发 ──
    print("\n  -- 清理活跃任务并触发续跑 --")
    for port, slug, nid, total, start, seg_end in stalled:
        _clear_active(nid)
        # 只跑到缺口段末尾而非 total —— 越过它会 force 重跑已完成的章。
        tid = _trigger(port, nid, start, seg_end)
        if _ok(tid):
            print(f"    ✅ [{slug}] ch{start}-{seg_end}, task={tid}")
            state.setdefault(slug, {"attempts": {}})["last_start"] = start
        else:
            print(f"    ❌ [{slug}] ch{start}-{seg_end} 触发失败: {tid}")
            # 触发失败也算一次尝试,避免同一章无限重试
            a = state.setdefault(slug, {"attempts": {}}).setdefault("attempts", {})
            a[str(start)] = int(a.get(str(start), 0)) + 1

    # ── 阶段 3:修复"被别人启动连坐暂停"的非停滞本(坑 A 的兜底)──
    conn2 = _conn()
    for port, slug, nid, total in NOVELS:
        if any(sl == slug for _, sl, _, _, _, _ in stalled):
            continue
        st = novel_state(conn2, slug, nid)
        if st["next_chapter"] and st["task_status"] != "running":
            print(f"    ↩ [{slug}] 被连坐暂停,重新触发 ch{st['next_chapter']}-{st['seg_end']}")
            _clear_active(nid)
            tid = _trigger(port, nid, st["next_chapter"], st["seg_end"])
            print(f"       {'✅' if _ok(tid) else '❌'} {tid}")
    conn2.close()

    _save_state(state)


if __name__ == "__main__":
    main()
