#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
scheduler.py — 定时调度器（配合 GitHub Actions 轮询）
=====================================================
每次运行：
  1. 读状态文件，取出「今天已分析过的（比赛ID + 时点）」
  2. 取比赛列表（本地缓存，只在每天 11:00 / 18:00 之后刷新一次）
  3. 找出到达任一分析时点、且该时点尚未分析的比赛
     时点由 WINDOWS 配置：T-15（开赛前 15 分钟）、T+2（开赛 2 分钟后）
  4. 逐场分析（prefill 用临时目录，不写 data/<日期>/ 目录）
  5. 结果追加到 ea_matches.csv（notes 记录是 T-15 还是 T+2），并更新状态文件

设计说明：
  - GitHub Actions 单 job 最长 6 小时，足球日却横跨约 19 小时，
    所以无法「11点拉列表后一直等到各场 T-30 分钟」，只能靠定时轮询。
  - GitHub 的 cron 本身不可靠（会延迟/跳过/限流），所以每次运行**内部循环
    RUN_MINUTES 分钟**、每 CHECK_INTERVAL 秒检查一次，一次触发就能覆盖一段时间。
  - 去重靠 data/scheduler_state.json（键为「比赛ID@时点」，每天重置）。
"""

import json
import os
import sys
import time
import shutil
import tempfile
from datetime import datetime, timedelta, timezone

# Windows 控制台默认 GBK，统一转 UTF-8
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)

import fetch_jihai8 as S
from fetch_match_jihai8 import scrape_match
from euro_asian_task import analyze_euro_asian, generate_euro_asian_report, append_to_csv

# ==================== 配置区 ====================
# 分析时点：(距开赛分钟数, 标签)。正数=赛前，负数=赛后。
#   T-15 : 开赛前 15 分钟（赛前盘）
#   T+2  : 开赛 2 分钟后（此时 jihai8 页面已冻结为「收盘盘」）
WINDOWS = [(15, 'T-15'), (-2, 'T+2')]
# 容差（分钟），窗口 = [目标-TOL, 目标+2]。
# 须 >= 外部定时器的触发间隔，否则窗口可能被"跳过"。
# 触发间隔 15 分钟 → 窗口取 16 分钟宽（14~32）以保证必命中。
TOLERANCE_MIN = 14
STATE_PATH = os.path.join(BASE_DIR, 'data', 'scheduler_state.json')
CACHE_PATH = os.path.join(BASE_DIR, 'data', 'schedule_cache.json')
# 赛程列表的刷新时间点（北京时间，小时）。jihai8 一般每天 11:00 更新当天赛程，
# 下午 18:00 再刷新一次以防变动。cron 每 10 分钟跑，会在这些点之后不久刷新缓存。
REFRESH_HOURS = (11, 18)

# 单次运行的内部循环时长（分钟）。设为 0 = 不做循环，每次触发只检查一轮，
# 触发频率完全交给外部定时器（cron-job.org，每 15 分钟）。
# 这样 runner 占用最少（每次约 1 分钟）。
# 若想要「一次运行覆盖更久」（应对触发不稳），可改成略小于触发间隔的值，如 14。
RUN_MINUTES = 0
CHECK_INTERVAL = 300    # 循环内每次检查的间隔（秒），仅 RUN_MINUTES>0 时生效
# ================================================


def load_state():
    if not os.path.exists(STATE_PATH):
        return {}
    try:
        with open(STATE_PATH, encoding='utf-8') as f:
            return json.load(f)
    except Exception:
        return {}


def save_state(state):
    os.makedirs(os.path.dirname(STATE_PATH), exist_ok=True)
    with open(STATE_PATH, 'w', encoding='utf-8') as f:
        json.dump(state, f, ensure_ascii=False, indent=2)


def last_refresh_point(now):
    """返回 <= now 的最近一个刷新时间点（REFRESH_HOURS 中的某个整点）"""
    for h in sorted(REFRESH_HOURS, reverse=True):
        t = now.replace(hour=h, minute=0, second=0, microsecond=0)
        if t <= now:
            return t
    # 今天的刷新点都还没到 → 用昨天的最后一个刷新点
    y = now - timedelta(days=1)
    return y.replace(hour=max(REFRESH_HOURS), minute=0, second=0, microsecond=0)


def collect_matches(force_refresh=False):
    """赛程列表：读本地缓存，只在「固定刷新点（11:00 / 18:00 北京时间）之后」才重新抓取。
    缓存覆盖「今天 + 明天」两个足球日，即每天只向 jihai8 抓 2 次列表。"""
    now = datetime.now(S._SH_TZ)
    need = last_refresh_point(now)

    if not force_refresh:
        try:
            with open(CACHE_PATH, encoding='utf-8') as f:
                c = json.load(f)
            fetched = S.parse_iso_utc(c.get('fetched_at'))
            if fetched is not None and fetched >= need:
                ms = c.get('matches', [])
                print(f"  [缓存] 赛程列表抓取于 {fetched.astimezone(S._SH_TZ):%m-%d %H:%M}"
                      f"（≥ 最近刷新点 {need:%m-%d %H:%M}），共 {len(ms)} 场候选")
                return ms
        except Exception:
            pass

    # 缓存缺失 / 早于最近刷新点 → 重新抓取
    today = now.strftime('%Y-%m-%d')
    tomorrow = (now + timedelta(days=1)).strftime('%Y-%m-%d')
    matches = []
    for d in (today, tomorrow):
        matches.extend(S.parse_match_list(d))
    seen, uniq = set(), []
    for m in matches:
        if m['match_id'] not in seen:
            seen.add(m['match_id'])
            uniq.append(m)

    os.makedirs(os.path.dirname(CACHE_PATH), exist_ok=True)
    with open(CACHE_PATH, 'w', encoding='utf-8') as f:
        json.dump({'fetched_at': now.astimezone(timezone.utc).strftime('%Y-%m-%dT%H:%M:%S+0000'),
                   'matches': uniq}, f, ensure_ascii=False, indent=2)
    print(f"  [抓取] 赛程列表已刷新（{today} + {tomorrow}，共 {len(uniq)} 场候选）")
    return uniq


def find_due(matches, done, now):
    """找出到达任一分析时点、且该时点尚未分析过的比赛。
    返回 [(match, 标签, 距开赛分钟数), ...]"""
    due = []
    for m in matches:
        mid = m['match_id']
        kick = S.parse_iso_utc(m.get('time_utc'))
        if kick is None:
            continue
        mins = (kick - now).total_seconds() / 60.0      # 距开赛还有多少分钟（负=已开赛）
        for target, label in WINDOWS:
            if f'{mid}@{label}' in done:
                continue
            lo, hi = target - TOLERANCE_MIN, target + 2
            if lo <= mins <= hi:
                due.append((m, label, mins))
    return due


def analyze_one(mid, label=''):
    """抓取一场并分析：prefill 用临时目录（不留 data/<日期>/ 文件），返回 True/False"""
    tmpdir = tempfile.mkdtemp(prefix='sched_')
    try:
        data, _ = scrape_match(mid, prefill=True, save_json=True, out_dir=tmpdir)
        if data is None:
            print('  ⚠️ 未获取到比赛数据，跳过')
            return False
        if not data.get('asian_companies'):
            print('  ⚠️ 未获取到亚盘数据（可能被限流），跳过')
            return False
        result = analyze_euro_asian(data)
        print(generate_euro_asian_report(result))
        append_to_csv(result, data, notes=label)   # 追加到 ea_matches.csv，notes 记录分析时点
        return True
    finally:
        shutil.rmtree(tmpdir, ignore_errors=True)


def run_once():
    """一趟检查：读状态 → 找到点比赛 → 分析 → 保存状态。返回本次分析的数量。"""
    now = datetime.now(S._SH_TZ)
    today = now.strftime('%Y-%m-%d')

    # 1. 状态（换天则重置）
    state = load_state()
    if state.get('date') != today:
        state = {'date': today, 'analyzed': []}
    done = set(state.get('analyzed', []))

    # 2. 比赛列表（走缓存）
    matches = collect_matches()

    # 3. 找出到点的比赛
    due = find_due(matches, done, now)

    if not due:
        print(f"[{now:%Y-%m-%d %H:%M}] 无到点比赛（候选 {len(matches)} 场，今日已分析 {len(done)} 项）")
        save_state(state)
        return 0

    print(f"[{now:%Y-%m-%d %H:%M}] 到点 {len(due)} 项：")
    for m, label, mins in due:
        rel = f"距开赛还有 {mins:.0f} 分钟" if mins >= 0 else f"已开赛 {-mins:.0f} 分钟"
        print(f"  [{label}] {m['league']} {m['home']} vs {m['away']}  {m['time']}（{rel}）id={m['match_id']}")
    print()

    ok = 0
    for m, label, mins in due:
        mid = m['match_id']
        print(f"{'=' * 60}")
        print(f"分析 [{label}] [{m['league']}] {m['home']} vs {m['away']}  (id={mid})")
        print('=' * 60)
        try:
            if analyze_one(mid, label):
                done.add(f'{mid}@{label}')
                ok += 1
                print(f"  ✅ 已追加到 ea_matches.csv（notes={label}）\n")
        except Exception as e:
            print(f"  ❌ {mid}: {e}\n")

    # 4. 保存状态
    state['analyzed'] = sorted(done)
    save_state(state)
    print(f"本轮完成：分析 {ok}/{len(due)} 项，今日累计 {len(done)} 项")
    return ok


def main():
    """RUN_MINUTES > 0 时内部循环该时长、每 CHECK_INTERVAL 秒检查一次；
    RUN_MINUTES = 0 时只检查一轮就退出（触发频率交给外部定时器）。"""
    run_loop = bool(RUN_MINUTES and RUN_MINUTES > 0)
    end = time.time() + RUN_MINUTES * 60 if run_loop else 0
    total = 0
    round_no = 0
    if run_loop:
        print(f"=== 调度器启动：内部循环 {RUN_MINUTES} 分钟，每 {CHECK_INTERVAL}s 检查一次 ===")
    else:
        print("=== 调度器启动：单次检查（触发频率由外部定时器决定）===")
    while True:
        round_no += 1
        print(f"\n--- 第 {round_no} 次检查 ---")
        try:
            total += run_once()
        except Exception as e:
            print(f"  ❌ 本轮出错: {e}")
        if not run_loop or time.time() >= end:
            break
        time.sleep(CHECK_INTERVAL)
    print(f"\n=== 运行结束：共 {round_no} 次检查，累计分析 {total} 项 ===")


if __name__ == '__main__':
    main()
