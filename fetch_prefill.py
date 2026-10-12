#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
fetch_prefill.py — 赛前基本面自动填充工具（football-data-api.com / FootyStats）
============================================================================
从 football-data-api.com 抓取球队近期状态，填充 JSON 里的 state_factors:
  home_win_streak / home_loss_streak   主队连胜/连败
  away_win_streak / away_loss_streak   客队连胜/连败
  home_form_score / away_form_score    近5场加权评分 (0~1)
  home_form_detail / away_form_detail  近5场文字 (如 "近5场3W1D1L")
  home_ppg                             主队主场场均积分
  away_away_ppg                        客队客场场均积分

用法:
  python fetch_prefill.py --api-key YOUR_KEY --input data/xxx.json
  python fetch_prefill.py --api-key YOUR_KEY --input data/xxx.json --dry-run

说明:
  - 联赛用「赛季 ID」(season_id) 定位，每个赛季变化，脚本自动从 league-list 找最新赛季。
  - 爬虫用中文队名，football-data-api.com 用英文队名，通过 team_name_map.json 做映射。
"""

import argparse
import json
import os
import sys
import time
import urllib.request
import urllib.parse
from datetime import datetime, timezone

# Windows 控制台默认 GBK，统一转 UTF-8
try:
    sys.stdout.reconfigure(encoding='utf-8')
    sys.stderr.reconfigure(encoding='utf-8')
except Exception:
    pass

BASE_URL = "https://api.football-data-api.com"
TEAM_MAP_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "team_name_map.json")

# football-data-api.com API key（写死）
API_KEY = "633379bdd5c4c3eb26919d8570866801e1c07f399197ba8c5311446b8ea77a49"

# 中文联赛名（短名 + 全称） -> FootyStats 联赛英文名
LEAGUE_MAP = {
    "英超": "England Premier League", "英格兰超级联赛": "England Premier League",
    "英冠": "England Championship", "英格兰冠军联赛": "England Championship",
    "英甲": "England EFL League One", "英乙": "England EFL League Two",
    "西甲": "Spain La Liga", "西班牙甲级联赛": "Spain La Liga",
    "西乙": "Spain Segunda División", "西班牙乙级联赛": "Spain Segunda División",
    "德甲": "Germany Bundesliga", "德国甲级联赛": "Germany Bundesliga",
    "德乙": "Germany 2. Bundesliga", "德国乙级联赛": "Germany 2. Bundesliga",
    "意甲": "Italy Serie A", "意大利甲级联赛": "Italy Serie A",
    "意乙": "Italy Serie B", "意大利乙级联赛": "Italy Serie B",
    "法甲": "France Ligue 1", "法国甲级联赛": "France Ligue 1",
    "法乙": "France Ligue 2", "法国乙级联赛": "France Ligue 2",
    "荷甲": "Netherlands Eredivisie", "荷兰甲级联赛": "Netherlands Eredivisie",
    "荷乙": "Netherlands Eerste Divisie", "荷兰乙级联赛": "Netherlands Eerste Divisie",
    "葡超": "Portugal Liga NOS", "葡萄牙超级联赛": "Portugal Liga NOS",
    "土超": "Turkey Süper Lig", "土耳其超级联赛": "Turkey Süper Lig",
    "苏超": "Scotland Premiership", "苏格兰超级联赛": "Scotland Premiership",
    "比甲": "Belgium Pro League", "比利时甲级联赛": "Belgium Pro League",
    "芬超": "Finland Veikkausliiga", "芬兰超级联赛": "Finland Veikkausliiga",
    "丹超": "Denmark Superliga", "丹麦超级联赛": "Denmark Superliga",
    "波超": "Poland Ekstraklasa", "波兰超级联赛": "Poland Ekstraklasa",
    "瑞士超": "Switzerland Super League", "瑞士超级联赛": "Switzerland Super League",
    "中超": "China Chinese Super League",
    "巴甲": "Brazil Serie A", "巴西甲级联赛": "Brazil Serie A",
    "阿甲": "Argentina Primera División", "阿根廷甲级联赛": "Argentina Primera División",
    "哥甲": "Colombia Categoria Primera A", "哥伦比亚甲级联赛": "Colombia Categoria Primera A",
    "智甲": "Chile Primera División", "智利甲级联赛": "Chile Primera División",
    "墨西联": "Mexico Liga MX", "墨西哥超级联赛": "Mexico Liga MX",
    "美职业": "USA MLS", "美国职业足球大联盟": "USA MLS",
    "澳超": "Australia A-League", "澳大利亚超级联赛": "Australia A-League",
    "欧冠": "Europe UEFA Champions League",
    "欧联": "Europe UEFA Europa League",
    "欧协联": "Europe UEFA Europa Conference League",
}

# 联赛全称 -> 短名（用于 team_name_map.json 的键查找，键用的是短名）
LEAGUE_SHORT = {
    "英格兰超级联赛": "英超", "英格兰冠军联赛": "英冠",
    "西班牙甲级联赛": "西甲", "西班牙乙级联赛": "西乙",
    "德国甲级联赛": "德甲", "德国乙级联赛": "德乙",
    "意大利甲级联赛": "意甲", "意大利乙级联赛": "意乙",
    "法国甲级联赛": "法甲", "法国乙级联赛": "法乙",
    "荷兰甲级联赛": "荷甲", "荷兰乙级联赛": "荷乙",
    "葡萄牙超级联赛": "葡超", "土耳其超级联赛": "土超",
    "苏格兰超级联赛": "苏超", "比利时甲级联赛": "比甲",
    "芬兰超级联赛": "芬超", "丹麦超级联赛": "丹超",
    "波兰超级联赛": "波超", "瑞士超级联赛": "瑞士超",
    "巴西甲级联赛": "巴甲", "阿根廷甲级联赛": "阿甲",
    "哥伦比亚甲级联赛": "哥甲", "智利甲级联赛": "智甲",
    "墨西哥超级联赛": "墨超", "美国职业足球大联盟": "美职联",
    "澳大利亚超级联赛": "澳超",
}


# ─────────────────────── HTTP 请求 ───────────────────────
def api_get(endpoint, params=None):
    """调用 football-data-api.com，返回解析后的 dict"""
    p = {"key": API_KEY}
    if params:
        p.update(params)
    url = f"{BASE_URL}/{endpoint}?" + urllib.parse.urlencode(p)
    req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            return json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        body = e.read().decode("utf-8", errors="replace")
        print(f"  [HTTP {e.code}] {endpoint}: {body[:200]}", file=sys.stderr)
        return {}
    except Exception as e:
        print(f"  [请求失败] {endpoint}: {e}", file=sys.stderr)
        return {}


# ─────────────────────── 赛季 ID ───────────────────────
def find_season_id(league_en, season_id_override=None):
    """从 league-list 找到某联赛的最新赛季 ID（year 字段如 20262027，取最大）"""
    if season_id_override:
        return int(season_id_override)
    d = api_get("league-list", {"chosen_leagues_only": "true"})
    for lg in d.get("data", []):
        if lg.get("name") == league_en:
            seasons = lg.get("season", [])
            if seasons:
                seasons_sorted = sorted(seasons, key=lambda s: str(s.get("year", "")), reverse=True)
                sid = seasons_sorted[0]["id"]
                year = seasons_sorted[0].get("year")
                print(f"  联赛 {league_en} -> 最新赛季 season_id={sid} ({year})")
                return sid
    return None


# ─────────────────────── 队名映射 ───────────────────────
def load_team_map():
    if not os.path.exists(TEAM_MAP_PATH):
        return {}
    with open(TEAM_MAP_PATH, encoding="utf-8") as f:
        return json.load(f)


def resolve_team_en(team_map, league_cn, team_cn):
    """中文队名 -> 英文队名（找不到返回 None）"""
    m = team_map.get(league_cn, {})
    return m.get(team_cn) or m.get(team_cn.strip())


# ─────────────────────── 球队数据 ───────────────────────
def find_team_in_table(table, en_name):
    """在积分榜里按英文名模糊匹配球队"""
    if not en_name:
        return None
    en = en_name.lower().strip()
    for t in table:
        name = (t.get("name") or "").lower()
        clean = (t.get("cleanName") or "").lower()
        short = (t.get("shortHand") or "").lower().replace("-", " ")
        if en in name or en in clean or en in short or name in en or clean in en or en in short:
            return t
    return None


def parse_match_date(date_str):
    """'2026-09-20' -> Unix 时间戳（UTC 0 点），失败返回 0"""
    try:
        return int(datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=timezone.utc).timestamp())
    except Exception:
        return 0


def compute_form(team_id, matches, before_ts=0):
    """根据该队比赛列表计算 (win_streak, loss_streak, form_score, form_detail)"""
    team_matches = [
        m for m in matches
        if (m.get("homeID") == team_id or m.get("awayID") == team_id)
        and m.get("status") == "complete"
        and (before_ts == 0 or int(m.get("date_unix") or 0) < before_ts)
    ]
    team_matches.sort(key=lambda m: int(m.get("date_unix") or 0), reverse=True)

    results = []
    for m in team_matches[:5]:
        hg = int(m.get("homeGoalCount") or 0)
        ag = int(m.get("awayGoalCount") or 0)
        if m.get("homeID") == team_id:
            results.append("W" if hg > ag else ("D" if hg == ag else "L"))
        else:
            results.append("W" if ag > hg else ("D" if ag == hg else "L"))

    win_streak = 0
    for r in results:
        if r == "W":
            win_streak += 1
        else:
            break
    loss_streak = 0
    for r in results:
        if r == "L":
            loss_streak += 1
        else:
            break

    n = len(results)
    wins = results.count("W")
    draws = results.count("D")
    losses = results.count("L")
    form_score = round((wins * 3 + draws) / (n * 3), 2) if n else 0.5
    form_detail = f"近{n}场{wins}W{draws}D{losses}L" if n else "无数据"
    return win_streak, loss_streak, form_score, form_detail


# ─────────────────────── 主流程 ───────────────────────
def main():
    parser = argparse.ArgumentParser(description="赛前基本面填充 — football-data-api.com")
    parser.add_argument("--input", required=True, help="输入 JSON 文件路径")
    parser.add_argument("--output", default=None, help="输出路径，默认覆盖原文件")
    parser.add_argument("--season-id", default=None, help="指定赛季 ID，默认自动找最新赛季")
    parser.add_argument("--dry-run", action="store_true", help="预览结果，不写文件")
    args = parser.parse_args()

    if not os.path.exists(args.input):
        print(f"[错误] 文件不存在: {args.input}", file=sys.stderr)
        sys.exit(1)
    with open(args.input, encoding="utf-8") as f:
        template = json.load(f)

    league_cn = template.get("league", "")
    home_cn = template.get("home_team", "")
    away_cn = template.get("away_team", "")

    if not league_cn or not home_cn or not away_cn:
        print("[错误] JSON 缺少 league / home_team / away_team", file=sys.stderr)
        sys.exit(1)

    league_en = LEAGUE_MAP.get(league_cn)
    if not league_en:
        print(f"[警告] 未知联赛 '{league_cn}'，不在 LEAGUE_MAP 中，跳过填充", file=sys.stderr)
        sys.exit(1)

    print(f"📡 联赛: {league_cn} ({league_en})")
    print(f"   主队: {home_cn}  |  客队: {away_cn}")

    # 1. 找赛季 ID
    season_id = find_season_id(league_en, args.season_id)
    if not season_id:
        print("[错误] 未找到该联赛的赛季", file=sys.stderr)
        sys.exit(1)
    time.sleep(0.5)

    # 2. 积分榜（拿球队 ID、英文名、主客 W/D/L）
    tables = api_get("league-tables", {"season_id": str(season_id)})
    table = (tables.get("data") or {}).get("all_matches_table_overall") or []
    if not table:
        print("[错误] 积分榜为空", file=sys.stderr)
        sys.exit(1)
    time.sleep(0.5)

    # 3. 队名映射（联赛名规范化为短名，team_name_map.json 的键是短名）
    team_map = load_team_map()
    league_short = LEAGUE_SHORT.get(league_cn, league_cn)
    home_en = resolve_team_en(team_map, league_short, home_cn)
    away_en = resolve_team_en(team_map, league_short, away_cn)
    if not home_en:
        print(f"[警告] 队名映射缺失: {home_cn}，请在 team_name_map.json 补充", file=sys.stderr)
    if not away_en:
        print(f"[警告] 队名映射缺失: {away_cn}，请在 team_name_map.json 补充", file=sys.stderr)
    print(f"   映射: {home_cn}->{home_en}  |  {away_cn}->{away_en}")

    # 4. 赛程（算连胜/连败/近5场）
    matches_data = api_get("league-matches", {"season_id": str(season_id)})
    matches = matches_data.get("data", [])
    time.sleep(0.5)

    before_ts = parse_match_date(template.get("date", ""))

    # 找球队并算状态
    def get_state(cn, en):
        t = find_team_in_table(table, en) if en else None
        if not t:
            return None
        tid = t.get("id")
        # PPG
        wh = int(t.get("seasonWins_home") or 0)
        dh = int(t.get("seasonDraws_home") or 0)
        lh = int(t.get("seasonLosses_home") or 0)
        wa = int(t.get("seasonWins_away") or 0)
        da = int(t.get("seasonDraws_away") or 0)
        la = int(t.get("seasonLosses_away") or 0)
        home_m = wh + dh + lh
        away_m = wa + da + la
        home_ppg = round((wh * 3 + dh) / home_m, 2) if home_m else None
        away_ppg = round((wa * 3 + da) / away_m, 2) if away_m else None
        # 连胜/连败/近5场
        ws, ls, form_score, form_detail = compute_form(tid, matches, before_ts)
        return {
            "win_streak": ws, "loss_streak": ls,
            "form_score": form_score, "form_detail": form_detail,
            "ppg": home_ppg, "away_ppg": away_ppg,
        }

    home_state = get_state(home_cn, home_en)
    away_state = get_state(away_cn, away_en)

    sf_new = {
        "home_win_streak": (home_state or {}).get("win_streak", 0),
        "home_loss_streak": (home_state or {}).get("loss_streak", 0),
        "away_win_streak": (away_state or {}).get("win_streak", 0),
        "away_loss_streak": (away_state or {}).get("loss_streak", 0),
        "home_form_score": (home_state or {}).get("form_score", 0.5),
        "away_form_score": (away_state or {}).get("form_score", 0.5),
        "home_form_detail": (home_state or {}).get("form_detail", "无数据"),
        "away_form_detail": (away_state or {}).get("form_detail", "无数据"),
        "home_ppg": (home_state or {}).get("ppg"),
        "away_away_ppg": (away_state or {}).get("away_ppg"),
    }

    print("\n─" * 50)
    print("✅ 填充结果 (state_factors):")
    for k, v in sf_new.items():
        print(f"  {k:<22} = {v}")
    print("─" * 50)

    if args.dry_run:
        print("\n[dry-run] 未写文件")
        return

    template["state_factors"] = sf_new
    output_path = args.output or args.input
    with open(output_path, "w", encoding="utf-8") as f:
        json.dump(template, f, ensure_ascii=False, indent=2)
    print(f"\n💾 已写入: {output_path}")


if __name__ == "__main__":
    main()
