#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
update_team_map.py — 用 teams_name_map_footystats.csv 更新 team_name_map.json
=============================================================================
CSV 列: 联赛名(全称), 联赛ID(赛季ID), 球队ID, 球队中文名

脚本流程:
  1. 读 CSV，得到 {联赛短名: (赛季ID, {球队ID: 中文名})}
  2. 按赛季ID 调 FootyStats league-teams，得到 {球队ID: 英文名(cleanName)}
  3. 用球队ID 精确匹配，生成 {中文名: 英文名} 写入 team_name_map.json

新赛季运行时更新 CSV 后，执行: python update_team_map.py
"""

import csv
import json
import os
import sys
import time
import urllib.request
import urllib.parse

try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

# ==================== 配置区 ====================
API_KEY = "633379bdd5c4c3eb26919d8570866801e1c07f399197ba8c5311446b8ea77a49"
BASE_URL = "https://api.football-data-api.com"
BASE_DIR = os.path.dirname(os.path.abspath(__file__))
CSV_PATH = os.path.join(BASE_DIR, "teams_name_map_footystats.csv")
TEAM_MAP_PATH = os.path.join(BASE_DIR, "team_name_map.json")

# 联赛全称 -> 短名（team_name_map.json 的键用短名，与爬虫输出的联赛短名一致）
FULL_TO_SHORT = {
    "巴西甲级联赛": "巴甲", "比利时甲级联赛": "比甲", "波兰超级联赛": "波超",
    "丹麦超级联赛": "丹超", "德国甲级联赛": "德甲", "德国乙级联赛": "德乙",
    "法国甲级联赛": "法甲", "法国乙级联赛": "法乙", "芬兰超级联赛": "芬超",
    "哥伦比亚甲级联赛": "哥甲", "荷兰甲级联赛": "荷甲", "荷兰乙级联赛": "荷乙",
    "墨西哥超级联赛": "墨超", "葡萄牙超级联赛": "葡超", "苏格兰超级联赛": "苏超",
    "土耳其超级联赛": "土超", "西班牙甲级联赛": "西甲", "西班牙乙级联赛": "西乙",
    "意大利甲级联赛": "意甲", "意大利乙级联赛": "意乙", "英格兰超级联赛": "英超",
    "英格兰冠军联赛": "英冠", "智利甲级联赛": "智甲", "阿根廷甲级联赛": "阿甲",
    "美国职业足球大联盟": "美职联", "澳大利亚超级联赛": "澳超",
    "瑞士超级联赛": "瑞士超", "中超": "中超",
}
# ================================================


def api_get(endpoint, params=None):
    p = {"key": API_KEY}
    if params:
        p.update(params)
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        print(f"  [HTTP {e.code}] {endpoint}: {e.read().decode('utf-8', errors='replace')[:150]}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"  [请求失败] {endpoint}: {e}", file=sys.stderr)
        return {}


def main():
    if not os.path.exists(CSV_PATH):
        print(f"[错误] CSV 不存在: {CSV_PATH}", file=sys.stderr)
        sys.exit(1)

    # 1. 读 CSV
    csv_data = {}  # {短名: (赛季ID, {球队ID: 中文名})}
    with open(CSV_PATH, encoding="utf-8-sig") as f:
        for row in csv.DictReader(f):
            full = (row.get("联赛名") or "").strip()
            short = FULL_TO_SHORT.get(full)
            season_id = (row.get("联赛ID") or "").strip()
            team_id = (row.get("球队ID") or "").strip()
            cn = (row.get("球队中文名") or "").strip()
            if not short or not season_id or not team_id or not cn:
                continue
            d = csv_data.setdefault(short, [season_id, {}])
            d[1][int(team_id)] = cn

    print(f"📡 从 CSV 读到 {len(csv_data)} 个联赛")

    # 2. 逐联赛抓英文名，按球队ID匹配
    result = {}
    for short, (season_id, id_to_cn) in csv_data.items():
        time.sleep(0.3)
        ft = api_get("league-teams", {"season_id": str(season_id)})
        teams = ft.get("data", [])
        if not teams:
            print(f"  ⚠️ {short}: 无球队数据（season_id={season_id}），跳过")
            continue
        id_to_en = {}
        for t in teams:
            en = t.get("cleanName") or t.get("name") or ""
            if en and t.get("id") is not None:
                id_to_en[int(t.get("id"))] = en

        mapping = {}
        missed = 0
        for tid, cn in id_to_cn.items():
            en = id_to_en.get(tid)
            if en:
                mapping[cn] = en
            else:
                missed += 1
        result[short] = mapping
        print(f"  ✅ {short}: {len(mapping)} 队" + (f"，{missed} 队ID未匹配" if missed else ""))

    # 3. 写回（保留旧映射里 CSV 未覆盖的联赛）
    old = {}
    if os.path.exists(TEAM_MAP_PATH):
        with open(TEAM_MAP_PATH, encoding="utf-8") as f:
            old = json.load(f)
    for k, v in old.items():
        if k not in result:
            result[k] = v

    with open(TEAM_MAP_PATH, "w", encoding="utf-8") as f:
        json.dump(result, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已更新 {TEAM_MAP_PATH}")


if __name__ == "__main__":
    main()
