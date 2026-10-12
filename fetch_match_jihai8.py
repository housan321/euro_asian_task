#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_match_jihai8.py — 抓取单场比赛数据（即嗨体育 jihai8）
============================================================
用法:
  python fetch_match_jihai8.py 3012831
  python fetch_match_jihai8.py https://www.jihai8.com/zq/asia_3012831.html

输出 json 结构与批量脚本 fetch_jihai8.py 一致，保存到
  data/<日期>/<联赛>_<主队>VS<客队>.json

数据源（全部来自 jihai8）:
  - 亚盘: https://www.jihai8.com/zq/asia_<比赛ID>.html   (node 解码 NUXT)
  - 欧赔: https://www.jihai8.com/zq/europe_<比赛ID>.html (node 解码 NUXT)
"""

import re
import os
import sys
import json
import argparse

# Windows 控制台默认 GBK，统一转 UTF-8
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# 复用批量脚本的解析函数（全部来自 jihai8）
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, BASE_DIR)
from fetch_jihai8 import (parse_asian_ex, fetch_jihai8_euro_all, _map_euro_companies,
                          empty_state_factors, prefill_file, JIAH8_STATE_LABEL,
                          utc_iso_to_cst_str)


def parse_match_id(s):
    """从 URL 或纯数字里提取比赛 ID"""
    m = re.search(r'id=(\d+)', s)
    if m:
        return m.group(1)
    m = re.search(r'(\d{5,})', s)
    if m:
        return m.group(1)
    raise ValueError(f'无法从输入解析出比赛ID: {s}')


def scrape_match(match_id, prefill=False, save_json=True, out_dir=None):
    """爬取单场比赛（亚盘+欧赔，全部来自 jihai8），返回 (result_dict, output_path)。
    prefill=True   时还会调用 fetch_prefill.py 填充 state_factors 并重新读取。
    save_json=False 时不写盘（返回的 output_path 为 None）。
    out_dir 可指定输出目录（默认 data/<日期>/），供定时调度用临时目录。"""
    # 亚盘 + 比赛状态
    asian, asia_meta = parse_asian_ex(match_id)
    # 欧赔（顺带拿比赛信息）
    euro_obj = fetch_jihai8_euro_all(match_id)
    euro = _map_euro_companies(euro_obj.get('companies', [])) if euro_obj else {}

    meta = asia_meta or euro_obj or {}
    league = meta.get('league', '')
    home = meta.get('homeTeam', '')
    away = meta.get('awayTeam', '')
    mt_utc = meta.get('matchTime') or ''
    date = mt_utc[:10] if mt_utc else ''
    live_state = meta.get('matchState')
    hs, aw = meta.get('homeScore'), meta.get('awayScore')
    score = f'{hs}-{aw}' if (hs is not None and aw is not None and live_state not in (None, 0)) else ''
    state = JIAH8_STATE_LABEL.get(live_state, f'状态{live_state}') if live_state is not None else ''

    print(f'  比赛: {league} {home} vs {away} ({date})')

    # 三个来源都没拿到比赛信息 → 抓取失败（限流/ID 无效），不保存空文件
    if not (home or away or league or asian or euro):
        print('  ⚠️ 未能获取到比赛数据（可能被限流或比赛ID无效），跳过保存')
        return None, None

    result = {
        'id': 'R' + match_id,
        'date': date,
        'league': league,
        'home_team': home,
        'away_team': away,
        'match_time': utc_iso_to_cst_str(mt_utc),
        'match_time_utc': mt_utc or None,   # jihai8 的 ISO UTC 时间
        'match_state': state,
        'live_state': live_state,     # jihai8 matchState：1-7=滚球中，0=未开赛，-1=完场
        'score': score,
        'asian_companies': asian,
        'euro_companies': euro,
        'state_factors': empty_state_factors(),
    }

    if not save_json:
        return result, None

    date = date or 'single'
    out_dir = out_dir or os.path.join(BASE_DIR, 'data', date)
    os.makedirs(out_dir, exist_ok=True)
    fname = f"{result['league']}_{result['home_team']}VS{result['away_team']}.json"
    fname = re.sub(r'[\\/:*?"<>|]', '', fname)
    out = os.path.join(out_dir, fname)
    with open(out, 'w', encoding='utf-8') as f:
        json.dump(result, f, ensure_ascii=False, indent=2)

    if prefill:
        prefill_file(out)  # fetch_prefill.py 原地更新 state_factors
        with open(out, encoding='utf-8') as f:
            result = json.load(f)  # 重新读取，拿到填充后的 state_factors
    return result, out


def main():
    parser = argparse.ArgumentParser(description='球探网单场比赛爬虫')
    parser.add_argument('match', help='比赛 URL 或 ID')
    parser.add_argument('--prefill', action='store_true', help='爬完后调用 fetch_prefill.py 填充 state_factors')
    args = parser.parse_args()

    match_id = parse_match_id(args.match)
    print(f'[球探] 抓取单场比赛 id={match_id} ...')

    result, out = scrape_match(match_id, prefill=args.prefill)
    print(f'✅ 已保存 {out}（亚盘{len(result["asian_companies"])}家 / 欧赔{len(result["euro_companies"])}家）')
    if args.prefill:
        print(f'[prefill] 已用 fetch_prefill.py 填充 state_factors')
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    main()
