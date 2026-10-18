#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_jihai8.py — 从即嗨体育(jihai8)爬取某天指定联赛比赛 JSON
==============================================================
用法: python fetch_jihai8.py

结果保存到  data/<DATE>/<联赛>_<主队>VS<客队>.json

数据源（全部来自 jihai8，不再依赖 titan007）:
  - 比赛列表: https://api-live.jihai8.com/newapi/football/schedule?date=YYYY-MM-DD  (JSON)
  - 亚盘:     https://www.jihai8.com/zq/asia_<比赛ID>.html     (node 解码 NUXT)
  - 欧赔:     https://www.jihai8.com/zq/europe_<比赛ID>.html   (node 解码 NUXT)

DATE 自动取：10 点前取前一天（凌晨场属前一天足球日），10 点后取当天。
联赛范围在 FIVE_LEAGUES 配置。
"""

import re
import os
import json
import sys
import time
import random
import argparse
import subprocess
from datetime import datetime, timedelta, timezone

import requests

# Windows 控制台默认 GBK，无法打印 emoji/部分中文，统一转 UTF-8
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# ==================== 配置区（按需修改） ====================
# 目标日期：10 点前取前一天（凌晨场属于前一天的「足球日」），10 点后取当天。
# 两个分支都返回 'YYYY-MM-DD' 字符串。
if datetime.now().hour < 10:
    DATE = (datetime.now() - timedelta(days=1)).strftime('%Y-%m-%d')
else:
    DATE = datetime.now().strftime('%Y-%m-%d')

# 时间窗口（带方向）：
#   正数 N : 只抓「当前时间 +N 小时」内即将开赛的比赛
#   负数 -N: 只抓「当前时间 -N 小时」内已开赛/进行中的比赛
#   0      : 不限制（抓该足球日全部比赛）
TIME_WINDOW_HOURS = 1

# 请求限速：任意 60 秒内最多发出的请求数（含比赛列表/亚盘/欧赔）。
# 触发上限会自动等待，避免被 jihai8 限流。设为 0 表示不限制。
MAX_REQUESTS_PER_MIN = 20

# 联赛 leagueId -> 简称（jihai8 的 ID 体系）
FIVE_LEAGUES = {
    36: '英超',
    31: '西甲',
    8:  '德甲',
    34: '意甲',
    11: '法甲',

    37: '英冠',
    33: '西乙',
    9:  '德乙',
    # 12: '法乙',
    # 40: '意乙',
    # 17: '荷乙',

    5:  '比甲',
    6:  '波兰超',
    23: '葡超',
    16: '荷甲',
    30: '土超',
    22: '挪超',
    26: '瑞典超',
    7:  '丹麦超',
    3:  '奥甲',

    4:  '巴西甲',
    21: '美职业',
    2:  '阿甲',
    140: '墨西联',

    25: '日职联',
    15: '韩K联',
    284: '日职乙',
    700: '泰超',
    273: '澳超',
    #
    103: '欧冠杯',
    72:  '日联杯',
    192: '亚冠精英',
    350: '亚冠联2',
    250: '哥伦甲秋',
    113: '欧罗巴杯',


    # 1864: '欧国联',
}
# ============================================================

BASE_DIR = os.path.dirname(os.path.abspath(__file__))
OUT_DIR = os.path.join(BASE_DIR, 'data', DATE)

HEADERS = {
    'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 '
                  '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
    'Accept': 'application/json,text/html,application/xhtml+xml,*/*;q=0.8',
    'Accept-Language': 'zh-CN,zh;q=0.9,en;q=0.8',
    'Referer': 'https://www.jihai8.com/',
}

# 欧赔公司 companyId -> 项目公司名（只保留 euro_asian_task.py 引擎用到的 5 家）
EURO_COMPANY_MAP = {
    '115': '威*',        # William Hill
    '82':  '立*',        # Ladbrokes
    '104': 'Interwet*',  # Interwetten
    '177': '平博',       # Pinnacle
    '2':   '必发',       # Betfair（休眠，Rule 23）
}

# jihai8 亚盘公司 companyId -> 项目公司名
# 输出「并集」8 家：asian_handicap.py 用 7 家（含 Crow*/Interwet*），
# euro_asian_task.py 用 6 家（含平博），各引擎自行筛选
JIAH8_ASIA_ID_MAP = {
    1: '澳*',
    17: '明*',
    48: '港马*',
    8: '36*',
    12: '易*',
    47: '平博',
    19: 'Interwet*',
    3: 'Crow*',    # ＳＢ(皇冠)
}


def filter_companies(companies, wanted):
    """只保留 wanted 列表里的公司（保证各引擎的表决阈值正确）"""
    return {k: v for k, v in companies.items() if k in wanted}


_SH_TZ = timezone(timedelta(hours=8))   # 上海时区 UTC+8


def parse_iso_utc(iso):
    """解析 ISO 时间（如 '2026-09-20T13:00:00.000+0000'），返回 aware datetime，失败 None"""
    try:
        dt = datetime.fromisoformat(str(iso).replace('Z', '+00:00'))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except Exception:
        return None


def utc_iso_to_cst_str(iso):
    """ISO(UTC) -> 北京时间显示字符串 'MM-DD HH:MM'"""
    dt = parse_iso_utc(iso)
    return dt.astimezone(_SH_TZ).strftime('%m-%d %H:%M') if dt else ''


def within_time_window(time_utc, hours, now=None):
    """判断比赛开赛时间是否落在时间窗口内（带方向）：
      hours > 0 : 开赛时间在「当前时间 + hours 小时」内（未来即将开始）
      hours < 0 : 开赛时间在「当前时间 - |hours| 小时」内（过去刚开赛/进行中）
      hours = 0 : 不限制
    time_utc 为 jihai8 的 ISO UTC 开赛时间。"""
    if not hours:
        return True
    kick = parse_iso_utc(time_utc)
    if kick is None:
        return True          # 解析不出时间 → 保留（不误杀）
    now = now or datetime.now(_SH_TZ)
    dh = (kick - now).total_seconds() / 3600.0      # 正数=未来，负数=过去
    if hours > 0:
        return 0 <= dh <= hours                     # 未来 hours 小时内
    return hours <= dh <= 0                         # 过去 |hours| 小时内


# jihai8 matchState -> 中文状态
JIAH8_STATE_LABEL = {
    0: '未开赛', 1: '上半场', 2: '中场', 3: '下半场', 4: '加时',
    5: '加时', 6: '点球', 7: '点球', -1: '完场',
    -10: '延期', -11: '取消', -14: '推迟',
}


def empty_state_factors():
    """state_factors 占位（与 ea_template.json 一致，可用 fetch_prefill.py 填充）"""
    return {
        'home_win_streak': 0, 'home_loss_streak': 0,
        'away_win_streak': 0, 'away_loss_streak': 0,
        'home_form_score': 0.5, 'away_form_score': 0.5,
        'home_form_detail': '', 'away_form_detail': '',
        'home_ppg': None, 'away_away_ppg': None,
    }


def prefill_file(filepath):
    """调用 fetch_prefill.py 填充单个文件的 state_factors，返回是否成功"""
    script = os.path.join(BASE_DIR, 'fetch_prefill.py')
    cmd = [sys.executable, script, '--input', filepath]
    env = dict(os.environ, PYTHONIOENCODING='utf-8')
    try:
        r = subprocess.run(cmd, capture_output=True, text=True,
                           encoding='utf-8', errors='replace', env=env, timeout=180)
        if r.returncode == 0:
            print(f'  ✅ prefill 完成 {os.path.basename(filepath)}')
            return True
        msg = (r.stderr or r.stdout).strip()
        print(f'  ⚠️ prefill 失败 {os.path.basename(filepath)}: {msg[-120:]}')
        return False
    except Exception as e:
        print(f'  ⚠️ prefill 异常 {os.path.basename(filepath)}: {e}')
        return False


# ==================== 反爬：延时 + 重试 ====================
def sleep_rand(a=6.0, b=10.0):
    time.sleep(a + random.random() * (b - a))


# 请求时间戳队列（全局限速用）
_request_times = []


def rate_limit():
    """全局限速：保证任意 60 秒内请求数不超过 MAX_REQUESTS_PER_MIN，超了就等待。"""
    if not MAX_REQUESTS_PER_MIN or MAX_REQUESTS_PER_MIN <= 0:
        return
    now = time.time()
    while _request_times and now - _request_times[0] >= 60:
        _request_times.pop(0)
    if len(_request_times) >= MAX_REQUESTS_PER_MIN:
        wait = 60 - (now - _request_times[0]) + 0.5
        if wait > 0:
            print(f'  [限速] 已达 {MAX_REQUESTS_PER_MIN} 次/分钟上限，等待 {wait:.0f}s ...')
            time.sleep(wait)
        now = time.time()
        while _request_times and now - _request_times[0] >= 60:
            _request_times.pop(0)
    _request_times.append(now)


def _run_node(script_name, match_id):
    """调用 node 脚本抓取 jihai8 页面（NUXT 编码，需 JS 解码），返回 dict 或 None"""
    rate_limit()
    script = os.path.join(BASE_DIR, script_name)
    try:
        r = subprocess.run(['node', script, str(match_id)], capture_output=True, text=True,
                           encoding='utf-8', errors='replace', timeout=60)
        if r.returncode != 0:
            print(f'  [jihai8 失败] {script_name}: {(r.stderr or r.stdout).strip()[-100:]}')
            return None
        return json.loads(r.stdout)
    except Exception as e:
        print(f'  [jihai8 异常] {script_name}: {e}')
        return None


def jihai8_api(url, tries=3):
    """请求 jihai8 的 JSON 接口（带重试），返回 dict 或 None"""
    rate_limit()
    for i in range(tries):
        try:
            r = requests.get(url, headers=HEADERS, timeout=30)
            if r.status_code in (403, 429) or r.status_code >= 500:
                wait = (i + 1) * 2
                print(f'  [重试] HTTP {r.status_code}，{wait}s 后重试（{i + 1}/{tries}）')
                time.sleep(wait)
                continue
            r.raise_for_status()
            return r.json()
        except Exception as e:
            if i == tries - 1:
                print(f'  [jihai8 接口失败] {url}: {str(e)[:80]}')
                return None
            time.sleep((i + 1) * 2)
    return None


# ==================== 1. 比赛列表 ====================
def parse_match_list(date):
    """从 jihai8 赛程 JSON 接口解析比赛列表（该接口按「足球日」分组，跨天凌晨场属前一天）。
    返回 [{match_id, date, league_id, league, home, away, score, time, time_utc, state}]"""
    data = jihai8_api(f'https://api-live.jihai8.com/newapi/football/schedule?date={date}')
    if not data:
        return []
    matches = []
    for m in data.get('data', {}).get('matchs', []):
        lid = m.get('leagueId')
        if lid not in FIVE_LEAGUES:
            continue
        st = m.get('matchState')
        mt = m.get('matchTime') or ''
        score = ''
        if m.get('homeScore') is not None and st is not None and st != 0:
            score = f"{m.get('homeScore')}-{m.get('awayScore')}"
        matches.append({
            'match_id': str(m.get('matchId')),
            'date': date,                       # 该场所属「足球日」
            'league_id': lid,
            'league': FIVE_LEAGUES.get(lid, str(lid)),
            'home': m.get('homeTeam', ''),
            'away': m.get('awayTeam', ''),
            'score': score,
            'time': utc_iso_to_cst_str(mt),     # 北京时间显示（如 "09-20 21:00"）
            'time_utc': mt,                     # ISO UTC，用于时间窗口判断
            'state': JIAH8_STATE_LABEL.get(st, f'状态{st}'),
        })
    return matches


# ==================== 2. 欧赔解析 ====================
def fetch_jihai8_euro_all(match_id):
    """调 node 脚本抓 jihai8 欧赔页，返回完整对象（含比赛信息），失败返回 None"""
    return _run_node('fetch_jihai8_euro.js', match_id)


def _map_euro_companies(companies):
    """jihai8 欧赔公司列表 -> {项目公司名: 赔率字段}"""
    out = {}
    for c in companies:
        name = EURO_COMPANY_MAP.get(str(c.get('companyId')))
        if not name:
            continue
        try:
            h0, d0, a0 = float(c['firstHomeWin']), float(c['firstStandOff']), float(c['firstGuestWin'])
            h1, d1, a1 = float(c['homeWin']), float(c['standOff']), float(c['guestWin'])
        except (KeyError, TypeError, ValueError):
            continue
        if h1 <= 1 or d1 <= 1 or a1 <= 1:
            continue
        out[name] = {
            'home_odds': h1, 'draw_odds': d1, 'away_odds': a1,
            'home_change': round(h1 - h0, 2),
            'draw_change': round(d1 - d0, 2),
            'away_change': round(a1 - a0, 2),
        }
    return out


def parse_euro(match_id):
    """从 jihai8 欧赔页解析，返回 euro_companies"""
    obj = fetch_jihai8_euro_all(match_id)
    return _map_euro_companies(obj.get('companies', [])) if obj else {}


# ==================== 3. 亚盘解析 ====================
def parse_asian_ex(match_id):
    """解析亚盘，返回 (asian_companies, jihai8_meta)。
    jihai8_meta 含 matchState 等比赛状态（抓取失败时为 None）。"""
    # 1. 先试 jihai8（不被限流）
    obj = fetch_jihai8_asia_all(match_id)
    if obj and obj.get('companies'):
        comps = _map_jihai8_companies(obj['companies'])
        if comps:
            return comps, obj

    # 2. jihai8 无数据/失败 → 说明原因（现只依赖 jihai8，无备用源）
    if obj is not None and not obj.get('companies'):
        st = obj.get('matchState')
        if st in (1, 2):
            print('  [提示] jihai8 上半场/中场不提供「全场」分公司数据，暂无可用数据')
        else:
            print('  [提示] jihai8 该场无分公司数据')
    else:
        print('  [提示] jihai8 亚盘抓取失败')
    return {}, None


def parse_asian(match_id):
    """解析亚盘，返回 asian_companies"""
    return parse_asian_ex(match_id)[0]


def fetch_jihai8_asia_all(match_id):
    """调 node 脚本抓 jihai8 亚盘页，返回完整对象（含比赛状态与公司盘口），失败返回 None"""
    return _run_node('fetch_jihai8_asian.js', match_id)


def _map_jihai8_companies(companies):
    """jihai8 公司列表 -> {项目公司名: 盘口字段}"""
    out = {}
    for c in companies:
        name = JIAH8_ASIA_ID_MAP.get(c.get('companyId'))
        if not name:
            continue
        try:
            goal = c.get('goal', 0)
            first_goal = c.get('firstGoal', goal)
            out[name] = {
                'handicap': goal,
                'home_water': round(c['homeOdds'], 2),
                'away_water': round(c['awayOdds'], 2),
                'h_change': round(c['homeOdds'] - c['firstHomeOdds'], 2),
                'a_change': round(c['awayOdds'] - c['firstAwayOdds'], 2),
                'trend': '升盘' if abs(goal) > abs(first_goal) else ('降盘' if abs(goal) < abs(first_goal) else '持平'),
            }
        except (KeyError, TypeError):
            continue
    return out


def parse_asian_jihai8(match_id):
    """用 jihai8 亚盘页解析（两站同源，比赛 ID/公司 ID 一致），返回 asian_companies"""
    obj = fetch_jihai8_asia_all(match_id)
    if not obj:
        return {}
    return _map_jihai8_companies(obj.get('companies', []))


# ==================== 主流程 ====================
def scrape_5leagues(prefill=False, with_euro=True):
    """批量爬取指定联赛（DATE 默认系统当天、FIVE_LEAGUES 在顶部配置区），返回 [(data_dict, path), ...]
    prefill=True   时每场会调 fetch_prefill.py 填充 state_factors 并重新读取。
    with_euro=False 时跳过欧赔抓取（纯亚盘分析用，可省一半请求、防限流）。"""
    # 时间窗口开启时，扫描「前/中/后」三天：
    # 数据源用「足球日」分组，次日凌晨的比赛算在前一天，窗口可能横跨相邻足球日
    dates = [DATE]
    if TIME_WINDOW_HOURS:
        base = datetime.strptime(DATE, '%Y-%m-%d')
        dates = [(base + timedelta(days=off)).strftime('%Y-%m-%d') for off in (-1, 0, 1)]

    matches = []
    for d in dates:
        matches.extend(parse_match_list(d))

    # 去重
    seen, uniq = set(), []
    for m in matches:
        if m['match_id'] not in seen:
            seen.add(m['match_id'])
            uniq.append(m)

    # 时间窗口过滤：正数=未来 N 小时内开赛；负数=过去 N 小时内开赛
    if TIME_WINDOW_HOURS:
        now = datetime.now(_SH_TZ)
        kept = [m for m in uniq
                if within_time_window(m.get('time_utc'), TIME_WINDOW_HOURS, now)]
        wdesc = (f'未来 {TIME_WINDOW_HOURS}h 内开赛' if TIME_WINDOW_HOURS > 0
                 else f'过去 {abs(TIME_WINDOW_HOURS)}h 内开赛')
        print(f'\n⏰ 时间窗口（{wdesc}，当前 {now:%Y-%m-%d %H:%M} 北京时间）: '
              f'{len(uniq)} → {len(kept)} 场（已扫描 {dates}）')
        uniq = kept

    print(f'\n共 {len(uniq)} 场五大联赛比赛:')
    for m in uniq:
        print(f"  [{m['state']}] {m['league']} {m['home']} vs {m['away']} ({m['score']}) {m['time']} id={m['match_id']}")

    if not uniq:
        return []

    os.makedirs(OUT_DIR, exist_ok=True)
    ok = fail = 0
    results = []
    for m in uniq:
        print(f"\n[抓取] {m['league']} {m['home']} vs {m['away']} (id={m['match_id']})")
        try:
            if with_euro:
                sleep_rand()
                euro = parse_euro(m['match_id'])
            else:
                euro = {}
            sleep_rand()
            asian, jihai8_meta = parse_asian_ex(m['match_id'])
            # 亚盘为空（多为抓取失败/被限流）→ 不保存半成品，两个引擎都用不了
            if not asian:
                fail += 1
                print('  ⏭️  亚盘为空，跳过保存')
                continue
            # 开赛时间直接用 jihai8 的 ISO UTC（列表接口已提供）
            mt_utc = (jihai8_meta or {}).get('matchTime') or m.get('time_utc')
            result = {
                'id': 'R' + m['match_id'],
                'date': m.get('date', DATE),   # 该场的「足球日」（跨天凌晨场为前一天）
                'league': m['league'],
                'home_team': m['home'],
                'away_team': m['away'],
                'match_time': m['time'],
                'match_time_utc': mt_utc,
                'match_state': m['state'],
                'score': m['score'],
                'asian_companies': asian,
                'euro_companies': euro,
                'state_factors': empty_state_factors(),
            }
            fname = f"{m['league']}_{m['home']}VS{m['away']}.json"
            fname = re.sub(r'[\\/:*?"<>|]', '', fname)
            path = os.path.join(OUT_DIR, fname)
            with open(path, 'w', encoding='utf-8') as f:
                json.dump(result, f, ensure_ascii=False, indent=2)
            results.append((result, path))
            ok += 1
            print(f"  ✅ -> {fname}（亚盘{len(asian)}家 / 欧赔{len(euro)}家）")
        except Exception as e:
            fail += 1
            print(f'  ❌ {e}')

    print(f'\n完成：成功 {ok} / 失败 {fail}，输出目录 {OUT_DIR}')

    # 可选：填充 state_factors（每场调 fetch_prefill.py，然后重新读取）
    if prefill:
        print(f'\n[prefill] 填充 state_factors（共 {len(results)} 场）...')
        filled = []
        for result, path in results:
            prefill_file(path)
            with open(path, encoding='utf-8') as f:
                result = json.load(f)
            filled.append((result, path))
        results = filled
        print('prefill 完成')
    return results


def main():
    parser = argparse.ArgumentParser(description='球探网五大联赛爬虫（日期/联赛在顶部配置区硬编码）')
    parser.add_argument('--prefill', action='store_true', help='爬完后调用 fetch_prefill.py 填充 state_factors')
    args = parser.parse_args()

    print(f'[球探] 爬取 {DATE} 五大联赛（完场 + 未开始）...')
    scrape_5leagues(prefill=args.prefill)


if __name__ == '__main__':
    main()
