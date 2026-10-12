#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
欧亚综合分析引擎 — Euro-Asian Combined Analysis Engine
========================================================
输入：亚盘6家 + 欧赔5家 + 状态因子 → 三步交叉验证 → 统一报告

Step 1：亚盘6家独立分析（内联，完全隔离 asian_handicap.py）
Step 2：欧赔4家独立分析（新建，必发休眠）
Step 3：两盘交叉验证 + 状态因子增强
输出：双选 + 亚盘推荐 + 单选(可选) + 星级 + 核心逻辑

规则来源：MEMORY.md + RULES.md（2026-06-03版）
"""

import json
import csv
import os
import re
import sys
import argparse
from datetime import datetime, timedelta, timezone
from collections import Counter


_SH_TZ = timezone(timedelta(hours=8))   # 上海时区 UTC+8


def shanghai_datetime(date_str, match_time, match_time_utc=None):
    """生成上海时区的比赛时间 'YYYY-MM-DD HH:MM'。
    优先用 match_time_utc（ISO，含时区标记）；否则用 date + match_time（源为 UTC）解析。"""
    dt = None
    if match_time_utc:
        try:
            dt = datetime.fromisoformat(str(match_time_utc).replace('Z', '+00:00'))
            if dt.tzinfo is None:
                dt = dt.replace(tzinfo=timezone.utc)
        except Exception:
            dt = None
    if dt is None and date_str:
        s = str(match_time or '').strip()
        try:
            y = int(date_str.split('-')[0])
            m = re.match(r'(\d{1,2})-(\d{1,2})\s+(\d{1,2}):(\d{2})', s)   # 跨天: M-D H:MM
            if m:
                mo, day, hh, mi = (int(x) for x in m.groups())
            else:
                m = re.match(r'(\d{1,2})日\s*(\d{1,2}):(\d{2})', s)        # 当日: D日H:MM
                if m:
                    day, hh, mi = (int(x) for x in m.groups())
                    mo = int(date_str.split('-')[1])
            if m:
                dt = datetime(y, mo, day, hh, mi, tzinfo=timezone.utc)
        except Exception:
            dt = None
    if dt is None:
        return date_str or ''
    return dt.astimezone(_SH_TZ).strftime('%Y-%m-%d %H:%M')


# ============================================================
# 亚盘规则引擎（内联，与 asian_handicap.py 完全隔离）
# ============================================================

# 规则阈值（与 asian_handicap.py 同步）
RULE22_THRESHOLD = 0.08
RULE6_THRESHOLD  = 1.10
RULE11_THRESHOLD = 0.10
OBS_E_THRESHOLD  = 0.25
DEEP_HANDICAP      = 1.0
SUPER_DEEP_HANDICAP = 1.5
SHALLOW_HANDICAP_MAX = 0.25
LOW_WATER  = 0.85
HIGH_WATER = 1.10
HKJC_EXTREME_LOW  = 0.80
HKJC_EXTREME_HIGH = 1.10

# 亚盘公司列表（6家，与 asian_handicap.py 的7家不同）
ASIAN_COMPANIES = ["澳*", "明*", "港马*", "36*", "易*", "平博"]


def get_consensus_handicap(companies):
    """取多数公司一致的盘口值（无公司时返回 0，避免空列表崩溃）"""
    handicaps = [c["handicap"] for c in companies.values() if isinstance(c, dict) and "handicap" in c]
    if not handicaps:
        return 0
    counter = Counter(handicaps)
    return counter.most_common(1)[0][0]


def company_direction(company_data):
    """
    判定单家公司看好方向。
    逻辑（正盘口=主让球）：
      - 主水↓ 或 客水↑ = 看好主队
      - 主水↑ 或 客水↓ = 看衰主队（看好客队）
    逻辑（负盘口=客让球）：
      - 客水↓ 或 主水↑ = 看好客队
      - 客水↑ 或 主水↓ = 看衰客队（看好主队）
    返回: "主" | "客" | "分歧"
    """
    hcp = company_data["handicap"]
    h_ch = company_data.get("h_change", 0)
    a_ch = company_data.get("a_change", 0)

    if abs(h_ch) < 0.005 and abs(a_ch) < 0.005:
        return "分歧"

    home_votes = 0
    away_votes = 0

    if hcp > 0:
        if h_ch < -0.005:  home_votes += 1
        if h_ch >  0.005:  away_votes += 1
        if a_ch < -0.005:  away_votes += 1
        if a_ch >  0.005:  home_votes += 1
    elif hcp < 0:
        if a_ch < -0.005:  away_votes += 1
        if a_ch >  0.005:  home_votes += 1
        if h_ch < -0.005:  home_votes += 1
        if h_ch >  0.005:  away_votes += 1
    else:
        if   h_ch < a_ch:  return "主"
        elif a_ch < h_ch:  return "客"
        else:               return "分歧"

    if   home_votes > away_votes:  return "主"
    elif away_votes > home_votes:  return "客"
    else:                       return "分歧"


def check_rule6(data):
    """Rule 6：明*主水 ≥ 1.10 → 极强看衰主队"""
    ming = data["companies"].get("明*")
    if ming and ming["home_water"] >= RULE6_THRESHOLD:
        return {
            "triggered": True,  "rule": "Rule 6",  "direction": "客",
            "detail": f"明*主水 {ming['home_water']:.2f} ≥ {RULE6_THRESHOLD} → 看衰主队",
            "weight": 2
        }
    return None


def check_rule22(data):
    """Rule 22：≥3 家公司主水降 ≥ 8 点 → 真实看好主队"""
    drops = [(n, c.get("h_change", 0))
             for n, c in data["companies"].items()
             if c.get("h_change", 0) <= -RULE22_THRESHOLD]
    if len(drops) >= 3:
        detail = "、".join([f"{n}{ch*100:+.0f}点" for n, ch in drops])
        return {
            "triggered": True,  "rule": "Rule 22",  "direction": "主",
            "detail": f"{len(drops)}家公司主水降≥8点：{detail} → 真实看好主队",
            "weight": 2
        }
    return None


def check_obs_e(data):
    """观察E：单家公司水位反向变动 ≥ 25 点 → 知情资金信号"""
    directions = [company_direction(c) for c in data["companies"].values()]
    directions = [d for d in directions if d != "分歧"]
    if not directions:
        return None
    majority = Counter(directions).most_common(1)[0][0]

    for name, c in data["companies"].items():
        d    = company_direction(c)
        h_ch = c.get("h_change", 0)
        if d == majority or d == "分歧":
            continue
        if majority == "主" and h_ch >= OBS_E_THRESHOLD:
            return {
                "triggered": True,  "rule": "观察E",  "direction": "客",
                "detail": f"{name}主水升{h_ch*100:+.0f}点（反向≥25点）→ 知情资金看客",
                "weight": 1
            }
        elif majority == "客" and h_ch <= -OBS_E_THRESHOLD:
            return {
                "triggered": True,  "rule": "观察E",  "direction": "主",
                "detail": f"{name}主水降{h_ch*100:+.0f}点（反向≥25点）→ 知情资金看主",
                "weight": 1
            }
    return None


def check_rule11(data):
    """Rule 11：港马*水位变化 ≥ 10 点"""
    hkjc = data["companies"].get("港马*")
    if not hkjc:
        return None
    h_ch = hkjc.get("h_change", 0)
    signals = []
    if h_ch >= RULE11_THRESHOLD:
        signals.append({
            "triggered": True,  "rule": "Rule 11",  "direction": "客",
            "detail": f"港马*主水升{h_ch*100:+.0f}点(≥10点) → 看衰让球方",
            "weight": 1
        })
    elif h_ch <= -RULE11_THRESHOLD:
        signals.append({
            "triggered": True,  "rule": "Rule 11",  "direction": "主",
            "detail": f"港马*主水降{h_ch*100:+.0f}点(≥10点) → 看好让球方",
            "weight": 1
        })
    return signals if signals else None


def majority_vote(data):
    """6 家公司多数表决"""
    directions = {n: company_direction(c) for n, c in data["companies"].items()}
    counts = Counter(directions.values())
    home_votes = counts.get("主", 0)
    away_votes = counts.get("客", 0)
    split      = counts.get("分歧", 0)

    if home_votes + away_votes == 0:
        return {"direction": "观望", "stars": 0,
                "detail": "6家全部无方向", "ratio": "0:0"}

    if   home_votes >= 5:  stars = 4
    elif home_votes == 4:  stars = 3
    elif home_votes == 3:  stars = 2
    else:                     stars = 1

    if home_votes > away_votes:
        direction = "主"
    elif away_votes > home_votes:
        direction = "客"
        if   away_votes >= 5:  stars = 4
        elif away_votes == 4:  stars = 3
        elif away_votes == 3:  stars = 2
        else:                     stars = 1
    else:
        direction = "观望"

    ratio = f"{home_votes}:{away_votes}"
    if split > 0:
        ratio += f"（分歧{split}）"
    return {
        "direction": direction,  "stars": stars,
        "detail": f"表决 {ratio} → {'看好' + direction if direction != '观望' else '无共识'}",
        "ratio": ratio,  "home_votes": home_votes,  "away_votes": away_votes
    }


def check_rule19(handicap):
    """Rule 19：浅盘禁止单选"""
    if 0 <= abs(handicap) <= SHALLOW_HANDICAP_MAX:
        return {
            "triggered": True,
            "effect": "禁止单选，强制双选'不败'",
            "detail": f"盘口 {handicap:.2f} ∈ [0, {SHALLOW_HANDICAP_MAX}] 浅盘范围 → 禁止单选"
        }
    return None


def check_rule21(data, majority_result, handicap):
    """Rule 21：过度一致性 → 大热倒灶"""
    # 条件1：4 家集体同向调整
    trends = [data["companies"][n].get("trend", "持平") for n in data["companies"]]
    if not trends:
        return None                      # 无公司数据，不触发
    trend_counter = Counter(trends)
    max_trend = trend_counter.most_common(1)[0]
    same_trend = max_trend[1] if max_trend[0] != "持平" and max_trend[1] >= 4 else 0

    # 条件2：港马*极低/极高水
    hkjc = data["companies"].get("港马*")
    hkjc_extreme = bool(hkjc and (hkjc["home_water"] <= HKJC_EXTREME_LOW or
                                     hkjc["home_water"] >= HKJC_EXTREME_HIGH))

    # 条件3：明*主水 ≥ 1.10（Rule 6 联动）
    ming_high = bool(data["companies"].get("明*") and
                       data["companies"]["明*"]["home_water"] >= RULE6_THRESHOLD)

    triggers = sum([1 for x in [same_trend >= 4, hkjc_extreme, ming_high] if x])
    if triggers == 0:
        return None

    # 例外：Rule 22 触发 → 不取反
    if check_rule22(data):
        return {
            "triggered": True,
            "effect": "部分触发但同向（Rule 22覆盖）→ 不取反",
            "detail": f"Rule 22触发 + {triggers}项Rule 21条件 = 真实看好信号，非陷阱",
            "direction": None
        }

    # 例外：多数表决 ≥5家同向 → 不取反
    mv_home = majority_result.get("home_votes", 0)
    mv_away = majority_result.get("away_votes", 0)
    if max(mv_home, mv_away) >= 5:
        return {
            "triggered": True,
            "effect": "多数≥5家同向（Rule 21不取反）→ 信任多数",
            "detail": f"多数{max(mv_home, mv_away)}家同向 + {triggers}项Rule 21条件 = 多数信号更强，不取反",
            "direction": None
        }

    is_shallow = 0 <= abs(handicap) <= SHALLOW_HANDICAP_MAX
    is_deep    = abs(handicap) >= DEEP_HANDICAP

    if is_shallow:
        return {
            "triggered": True,
            "effect": "方向取反 + 强制观望",
            "detail": f"浅盘({handicap:.2f}) + {triggers}项触发 → 大热倒灶，反向考虑对手不败",
            "direction": "反"
        }
    elif is_deep:
        return {
            "triggered": True,
            "effect": "降级 + 反向受让方",
            "detail": f"深盘({handicap:.2f}) + {triggers}项触发 → 降级处理，反向考虑受让方不败",
            "direction": "反"
        }
    return None


def check_rule9(data, handicap, majority_result=None):
    """Rule 9：深盘诱盘识别"""
    if abs(handicap) < SUPER_DEEP_HANDICAP:
        return None
    if majority_result:
        mv_home = majority_result.get("home_votes", 0)
        mv_away = majority_result.get("away_votes", 0)
        if max(mv_home, mv_away) >= 5:
            return None
    upgrades = sum(1 for c in data["companies"].values() if c.get("trend") == "升盘")
    hkjc = data["companies"].get("港马*")
    hkjc_up = bool(hkjc and hkjc.get("h_change", 0) >= 0.03)
    if upgrades >= 4 and hkjc_up:
        return {
            "triggered": True,  "effect": "诱盘 → 反向操作",
            "detail": f"深盘{handicap:.2f} + {upgrades}家升盘 + 港马*升水 → 诱盘信号"
        }
    return None


def check_rule16(data, handicap):
    """Rule 16：深盘+主水全破1.0 = 大热倒灶"""
    if abs(handicap) < DEEP_HANDICAP:
        return None
    broken     = sum(1 for c in data["companies"].values() if c["home_water"] >= 1.0)
    downgrades = sum(1 for c in data["companies"].values() if c.get("trend") == "降盘")
    if broken >= 4 and downgrades >= 1:
        return {
            "triggered": True,  "effect": "大热倒灶 → 降级观望",
            "detail": f"深盘{handicap:.2f} + {broken}家主水破1.0 + {downgrades}家降盘 → 大热倒灶"
        }
    return None


def check_rule17(data, handicap, majority_result=None):
    """Rule 17：深盘隐性大热倒灶"""
    if abs(handicap) < SUPER_DEEP_HANDICAP:
        return None
    if majority_result:
        mv_home = majority_result.get("home_votes", 0)
        mv_away = majority_result.get("away_votes", 0)
        if max(mv_home, mv_away) >= 5:
            return None
    return {
        "triggered": True,
        "effect": "降低信心，需欧赔客胜分歧确认",
        "detail": f"深盘{handicap:.2f} → 两盘一致时注意客胜异常分歧"
    }


# ============================================================
# Podos 风格规则（融入状态因子维度）
# ============================================================

def check_rule_p1(state_factors, asian_direction):
    """
    Rule-P1：状态动量背离检测
    IF 主队连胜≥3 AND 客队连败≥2 AND 亚盘方向≠主 → 触发背离警告，降级1星
    IF 客队连胜≥3 AND 主队连败≥2 AND 亚盘方向≠客 → 触发背离警告，降级1星
    """
    h_ws = state_factors.get("home_win_streak") or 0
    h_ls = state_factors.get("home_loss_streak") or 0
    a_ws = state_factors.get("away_win_streak") or 0
    a_ls = state_factors.get("away_loss_streak") or 0

    # 场景1：主队动量强势但亚盘不看主
    if h_ws >= PODOS_P1_MOMENTUM and a_ls >= 2:
        if asian_direction and asian_direction != "主":
            return {
                "triggered": True, "rule": "Rule-P1",
                "effect": "状态背离：主队动量强但亚盘方向≠主 → 降1星",
                "detail": f"主队{h_ws}连胜 + 客队{a_ls}连败 vs 亚盘方向={asian_direction} → 动量背离警告",
                "bonus": -1.0
            }

    # 场景2：客队动量强势但亚盘不看客
    if a_ws >= PODOS_P1_MOMENTUM and h_ls >= 2:
        if asian_direction and asian_direction != "客":
            return {
                "triggered": True, "rule": "Rule-P1",
                "effect": "状态背离：客队动量强但亚盘方向≠客 → 降1星",
                "detail": f"客队{a_ws}连胜 + 主队{h_ls}连败 vs 亚盘方向={asian_direction} → 动量背离警告",
                "bonus": -1.0
            }

    return None


def check_rule_p2(state_factors, asian_direction):
    """
    Rule-P2：主客场优势量化
    IF 主队主场PPG - 客队客场PPG ≥ 0.5：
        IF 亚盘方向=主 → 加成+0.5星（基本面支撑）
        IF 亚盘方向=客 → 冲突-0.3星（基本面不支持）
    """
    home_ppg = state_factors.get("home_ppg")
    away_away_ppg = state_factors.get("away_away_ppg")

    if home_ppg is None or away_away_ppg is None:
        return None

    ppg_gap = home_ppg - away_away_ppg

    if ppg_gap >= PODOS_P2_PPG_GAP:
        if asian_direction == "主":
            return {
                "triggered": True, "rule": "Rule-P2",
                "effect": "主客场优势量化支持主方向 → +0.5星",
                "detail": f"主队主场PPG {home_ppg:.2f} - 客队客场PPG {away_away_ppg:.2f} = {ppg_gap:+.2f} (≥{PODOS_P2_PPG_GAP}) → 基本面支撑",
                "bonus": +0.5
            }
        elif asian_direction == "客":
            return {
                "triggered": True, "rule": "Rule-P2",
                "effect": "主客场优势量化与方向冲突 → -0.3星",
                "detail": f"主队主场PPG {home_ppg:.2f} - 客队客场PPG {away_away_ppg:.2f} = {ppg_gap:+.2f} (≥{PODOS_P2_PPG_GAP}) → 基本面不支持客方向",
                "bonus": -0.3
            }
    elif ppg_gap <= -PODOS_P2_PPG_GAP:
        if asian_direction == "客":
            return {
                "triggered": True, "rule": "Rule-P2",
                "effect": "主客场优势量化支持客方向 → +0.5星",
                "detail": f"客队客场PPG {away_away_ppg:.2f} - 主队主场PPG {home_ppg:.2f} = {-ppg_gap:+.2f} (≥{PODOS_P2_PPG_GAP}) → 基本面支撑客方向",
                "bonus": +0.5
            }
        elif asian_direction == "主":
            return {
                "triggered": True, "rule": "Rule-P2",
                "effect": "主客场优势量化与方向冲突 → -0.3星",
                "detail": f"客队客场PPG {away_away_ppg:.2f} - 主队主场PPG {home_ppg:.2f} = {-ppg_gap:+.2f} (≥{PODOS_P2_PPG_GAP}) → 基本面不支持主方向",
                "bonus": -0.3
            }

    return None


def calculate_final_stars(majority, layer1_signals, layer3, layer4, handicap):
    """最终信心度计算"""
    stars = majority["stars"]

    # 第1层加分
    flat_l1 = []
    for s in layer1_signals:
        if isinstance(s, list):  flat_l1.extend(s)
        elif s:                 flat_l1.append(s)
    stars += sum(s.get("weight", 0) for s in flat_l1) * 0.5

    # 第3层修正
    if layer3:
        if layer3.get("effect", "").startswith("方向取反"):
            stars = max(0, stars - 3)
        elif "降级" in layer3.get("effect", ""):
            stars = max(0, stars - 2)

    # 第4层修正
    for l4 in layer4:
        if not l4:  continue
        effect = l4.get("effect", "")
        if   "大热倒灶" in effect:  stars = max(0, stars - 2)
        elif "诱盘"     in effect:  stars = max(0, stars - 3)
        elif "降低信心" in effect:  stars = max(0, stars - 1)

    stars = max(0.5, min(5.0, stars))
    return round(stars * 2) / 2


def stars_to_display(stars):
    full = int(stars)
    half = (stars - full) >= 0.5
    s = "⭐" * full
    if half:  s += "½"
    return f"{s}（{stars}星）"


def stars_to_recommendation(stars):
    if   stars >= 4.5:  return "中等"
    elif stars >= 3.5:  return "小-中"
    elif stars >= 2.5:  return "小注"
    elif stars >= 1.5:  return "极小注"
    else:                    return "观望"


# ============================================================
# 常量（欧赔 + 状态因子）
# ============================================================

# 欧赔公司（当前活跃4家，必发休眠）
EURO_COMPANIES_ACTIVE = ["威*", "立*", "Interwet*", "平博"]
EURO_COMPANIES_ALL  = EURO_COMPANIES_ACTIVE + ["必发"]

# 欧赔相关阈值
EURO_HOME_BIAS      = 0.40
EURO_STRONG_BIAS    = 0.50
EURO_EXTREME_CHANGE = 1.0
EURO_MAJORITY_MIN   = 3
EURO_STRUCTURAL_MIN  = 0.05

# 状态因子
FORM_WEIGHT      = 0.5
STREAK_THRESHOLD  = 3

# Podos 风格规则阈值
PODOS_P1_MOMENTUM = 3      # Rule-P1：连胜/连败≥3场触发状态动量
PODOS_P2_PPG_GAP  = 0.5    # Rule-P2：主客场场均积分差≥0.5触发优势量化

# CSV
CSV_PATH      = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ea_matches.csv")
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ea_template.json")

CSV_HEADERS = [
    "id", "date", "league", "home_team", "away_team",
    "handicap_consensus", "asian_direction", "euro_direction", "final_direction",
    "asian_stars", "euro_stars", "final_stars",
    "rules_triggered", "double_pick", "ah_recommendation",
    "actual_result", "actual_score", "ah_result", "notes"
]


# ============================================================
# 状态因子计算器
# ============================================================

def load_match_history(csv_path):
    """从 CSV 加载历史比赛记录"""
    if not os.path.exists(csv_path):
        return []
    with open(csv_path, "r", encoding="utf-8-sig") as f:
        return list(csv.DictReader(f))


def calculate_state_factors(home_team, away_team, league, history=None, input_factors=None):
    """
    计算球队状态因子。
    优先使用输入中提供的因子；缺失时从历史数据计算。
    返回: {
        "home_win_streak": 连胜场次,
        "home_loss_streak": 连败场次,
        "away_win_streak": 连胜场次,
        "away_loss_streak": 连败场次,
        "home_form_score": 近期表现评分(0-1),
        "away_form_score": 近期表现评分(0-1),
        "home_form_detail": "近5场3W1D1L",
        "away_form_detail": "近5场2W2D1L",
    }
    """
    if input_factors and isinstance(input_factors, dict):
        return {
            "home_win_streak":  input_factors.get("home_win_streak",  0),
            "home_loss_streak": input_factors.get("home_loss_streak", 0),
            "away_win_streak":  input_factors.get("away_win_streak",  0),
            "away_loss_streak": input_factors.get("away_loss_streak", 0),
            "home_form_score":  input_factors.get("home_form_score",  0.5),
            "away_form_score":  input_factors.get("away_form_score",  0.5),
            "home_form_detail": input_factors.get("home_form_detail", "无数据"),
            "away_form_detail": input_factors.get("away_form_detail", "无数据"),
            "home_ppg":         input_factors.get("home_ppg"),
            "away_away_ppg":    input_factors.get("away_away_ppg"),
        }

    result = {
        "home_win_streak": 0,  "home_loss_streak": 0,
        "away_win_streak": 0,  "away_loss_streak": 0,
        "home_form_score": 0.5,  "away_form_score": 0.5,
        "home_form_detail": "无数据",  "away_form_detail": "无数据",
    }
    if not history:
        return result

    def calc_team_streaks(matches, team):
        team_matches = []
        for m in matches:
            if   m.get("home_team", "") == team:  team_matches.append(("主", m.get("actual_result", "")))
            elif m.get("away_team", "") == team:  team_matches.append(("客", m.get("actual_result", "")))
        recent = team_matches[-5:] if len(team_matches) > 5 else team_matches
        recent.reverse()
        win_streak, loss_streak = 0, 0
        for location, result_text in recent:
            if   (location == "主" and "主胜" in result_text) or \
                 (location == "客" and "客胜" in result_text):
                win_streak += 1;  loss_streak = 0
            elif "主胜" in result_text or "客胜" in result_text:
                loss_streak += 1;  win_streak = 0
            else:
                win_streak = 0;  loss_streak = 0
        wins, draws, losses = 0, 0, 0
        for location, result_text in recent:
            if   (location == "主" and "主胜" in result_text) or \
                 (location == "客" and "客胜" in result_text):  wins  += 1
            elif "平" in result_text or "平局" in result_text:       draws += 1
            else:                                                      losses += 1
        form_score  = (wins * 3 + draws) / (len(recent) * 3) if recent else 0.5
        form_detail = f"近{len(recent)}场{wins}W{draws}D{losses}L"
        return win_streak, loss_streak, form_score, form_detail

    (result["home_win_streak"], result["home_loss_streak"],
     result["home_form_score"],  result["home_form_detail"]) = calc_team_streaks(history, home_team)
    (result["away_win_streak"], result["away_loss_streak"],
     result["away_form_score"],  result["away_form_detail"]) = calc_team_streaks(history, away_team)
    return result


def evaluate_state_signal(factors):
    """评估状态因子信号 — 返回方向倾向和加分"""
    signal = {"direction": None, "bonus": 0, "detail": []}
    h_ws = factors.get("home_win_streak") or 0
    h_ls = factors.get("home_loss_streak") or 0
    a_ws = factors.get("away_win_streak") or 0
    a_ls = factors.get("away_loss_streak") or 0
    h_form = factors.get("home_form_score") if factors.get("home_form_score") is not None else 0.5
    a_form = factors.get("away_form_score") if factors.get("away_form_score") is not None else 0.5

    if h_ws >= STREAK_THRESHOLD:
        signal["detail"].append(f"主队{h_ws}连胜");  signal["bonus"] += 0.3
        if signal["direction"] is None:  signal["direction"] = "主"
    if a_ls >= STREAK_THRESHOLD:
        signal["detail"].append(f"客队{a_ls}连败");  signal["bonus"] += 0.3
        if signal["direction"] is None:  signal["direction"] = "主"
    if a_ws >= STREAK_THRESHOLD:
        signal["detail"].append(f"客队{a_ws}连胜");  signal["bonus"] -= 0.3
        if signal["direction"] is None:  signal["direction"] = "客"
    if h_ls >= STREAK_THRESHOLD:
        signal["detail"].append(f"主队{h_ls}连败");  signal["bonus"] -= 0.3
        if signal["direction"] is None:  signal["direction"] = "客"

    form_diff = h_form - a_form
    if abs(form_diff) > 0.2:
        if form_diff > 0:
            signal["detail"].append(f"主队近况({h_form:.2f})明显优于客队({a_form:.2f})")
            signal["bonus"] += 0.3
            if signal["direction"] is None:  signal["direction"] = "主"
        else:
            signal["detail"].append(f"客队近况({a_form:.2f})明显优于主队({h_form:.2f})")
            signal["bonus"] -= 0.3
            if signal["direction"] is None:  signal["direction"] = "客"
    else:
        signal["detail"].append(f"两队近况接近（主{h_form:.2f} vs 客{a_form:.2f}）")
    return signal


# ============================================================
# Step 2：欧赔独立分析
# ============================================================

def implied_probability(odds):
    """计算隐含概率（扣除庄家抽水后）"""
    raw = {k: 1.0 / v if v > 1.0 else 0 for k, v in odds.items()}
    overround = sum(raw.values())
    if overround == 0:
        return {"主胜": 0, "平局": 0, "客胜": 0}, 0
    return {k: v / overround for k, v in raw.items()}, overround


def euro_company_direction(company_data):
    """
    判定单家欧赔公司看好方向。
    """
    h_ch = company_data.get("home_change", 0)
    d_ch = company_data.get("draw_change", 0)
    a_ch = company_data.get("away_change", 0)
    if max(abs(h_ch), abs(d_ch), abs(a_ch)) < 0.02:
        return None, "无变化"
    if h_ch < -EURO_STRUCTURAL_MIN and d_ch > EURO_STRUCTURAL_MIN and a_ch > EURO_STRUCTURAL_MIN:
        if d_ch > h_ch and d_ch > a_ch:
            return "分胜负", f"排除平局(主胜↓{abs(h_ch)*100:.0f} 平局↑{d_ch*100:.0f} 客胜↑{a_ch*100:.0f})"
        return "主", "结构看好主胜(一降两升)"
    if h_ch < -EURO_STRUCTURAL_MIN and a_ch > EURO_STRUCTURAL_MIN:
        return "主", f"看好主胜(主胜↓{abs(h_ch)*100:.0f} 客胜↑{a_ch*100:.0f})"
    if a_ch < -EURO_STRUCTURAL_MIN and h_ch > EURO_STRUCTURAL_MIN:
        return "客", f"看好客胜(客胜↓{abs(a_ch)*100:.0f} 主胜↑{h_ch*100:.0f})"
    if h_ch > EURO_STRUCTURAL_MIN and a_ch < -EURO_STRUCTURAL_MIN:
        return "客", f"看衰主胜(主胜↑{h_ch*100:.0f})"
    changes = {"主": h_ch, "平": d_ch, "客": a_ch}
    min_change = min(changes, key=changes.get)
    if changes[min_change] < -EURO_STRUCTURAL_MIN:
        direction_map = {"主": "主", "平": "平局", "客": "客"}
        return direction_map[min_change], "温和倾向(赔率下降)"
    return None, "方向不明"


def analyze_euro_odds(euro_companies, league=""):
    company_dirs = {}
    direction_details = {}
    all_probs = {"主胜": [], "平局": [], "客胜": []}
    active = {}
    for name in EURO_COMPANIES_ACTIVE:
        c = euro_companies.get(name)
        if c and all(k in c for k in ("home_odds", "draw_odds", "away_odds")):
            active[name] = c
            direction, detail = euro_company_direction(c)
            company_dirs[name] = direction
            direction_details[name] = detail
            odds = {"主胜": c["home_odds"], "平局": c["draw_odds"], "客胜": c["away_odds"]}
            probs, _ = implied_probability(odds)
            all_probs["主胜"].append(probs["主胜"])
            all_probs["平局"].append(probs["平局"])
            all_probs["客胜"].append(probs["客胜"])

    if not active:
        return {"direction": "分歧", "stars": 0, "detail": "无有效欧赔数据"}
    valid_dirs = [v for v in company_dirs.values() if v is not None]
    if not valid_dirs:
        return {"direction": "分歧", "stars": 0, "companies_detail": company_dirs, "detail": "全部公司无方向"}
    counts = Counter(valid_dirs)
    top_dir, top_count = counts.most_common(1)[0]
    total = len(active)
    if   top_count >= 4:  euro_stars_base = 4
    elif top_count >= 3:  euro_stars_base = 3
    elif top_count >= 2:  euro_stars_base = 2
    else:                    euro_stars_base = 1

    avg_probs = {k: sum(v)/len(v) for k, v in all_probs.items() if v}
    prob_direction = max(avg_probs, key=avg_probs.get) if avg_probs else None
    prob_ratio    = avg_probs.get(prob_direction, 0) if prob_direction else 0
    if prob_direction == top_dir and prob_ratio >= EURO_STRONG_BIAS:
        euro_stars_base = min(5, euro_stars_base + 1)
    elif prob_direction and prob_direction != top_dir:
        euro_stars_base = max(1, euro_stars_base - 1)

    rules_triggered = []
    if top_count >= len(active) and prob_ratio >= 0.60:
        rules_triggered.append({
            "rule": "Rule 13",
            "effect": "欧赔超级一致 → 可高信心",
            "detail": f"{len(active)}家全部同向 + 隐含概率{prob_ratio*100:.0f}%"
        })
        euro_stars_base = min(5, euro_stars_base + 1)
    for name, c in active.items():
        for field, label in [("home_change","主胜"), ("draw_change","平局"), ("away_change","客胜")]:
            ch = abs(c.get(field, 0))
            if ch >= EURO_EXTREME_CHANGE:
                rules_triggered.append({
                    "rule": "Rule 14",
                    "effect": f"极端异常({name} {label} 变化{ch:.2f})",
                    "detail": f"{name}{label}赔率变化{ch:.2f} ≥ 1.0"
                })
    betfair = euro_companies.get("必发")
    if betfair and any(betfair.get(k, 0) != 0 for k in ("home_change", "draw_change", "away_change")):
        rules_triggered.append({
            "rule": "Rule 23",
            "effect": "必发休眠，不参与分析",
            "detail": "非顶级联赛，必发数据以噪音为主"
        })
    return {
        "direction": top_dir,  "stars": euro_stars_base,
        "companies_detail": company_dirs,  "direction_details": direction_details,
        "majority": {"direction": top_dir, "count": top_count, "total": total,
                     "distribution": dict(counts)},
        "implied_prob": avg_probs,  "rules_triggered": rules_triggered,
        "detail": f"欧赔{top_count}/{total}看{'好' if top_dir else ''}{top_dir}，隐含概率{prob_ratio*100:.0f}%"
    }


# ============================================================
# Step 3：两盘交叉验证
# ============================================================

def cross_validate(asian_result, euro_result, state_signal=None, state_factors=None):
    asian_dir  = asian_result.get("direction", "分歧")
    euro_dir   = euro_result.get("direction", "分歧")
    asian_stars = asian_result.get("stars", 0)
    euro_stars  = euro_result.get("stars", 0)
    handicap    = asian_result.get("handicap", 0)
    base_stars  = (asian_stars + euro_stars) / 2
    detail_parts = []
    cross_signals = []

    if asian_dir == euro_dir and asian_dir not in ("分歧", "观望"):
        detail_parts.append(f"✅ 两盘一致看好{asian_dir}")
        base_stars = min(5, base_stars + 0.5)
        cross_signals.append({"type": "consistent", "detail": f"亚盘+欧赔 同向看{asian_dir}"})
    elif asian_dir in ("分歧","观望") or euro_dir in ("分歧","观望"):
        detail_parts.append("⚠️ 一盘无方向 → 以有方向盘为准")
        if   asian_dir in ("分歧","观望"):
            base_stars = euro_stars - 1
            asian_dir = euro_dir
            detail_parts.append(f"  亚盘无方向，跟随欧赔{euro_dir}")
        else:
            base_stars = asian_stars - 1
            detail_parts.append(f"  欧赔无方向，跟随亚盘{asian_dir}")
        cross_signals.append({"type": "one_side", "detail": "仅单盘有信号"})
    elif asian_dir != euro_dir:
        detail_parts.append(f"❌ 两盘背离（亚盘{asian_dir} vs 欧赔{euro_dir}）")
        base_stars = max(1, base_stars - 1.5)
        if 0 <= abs(handicap) <= 0.25:
            detail_parts.append(f"  浅盘({handicap:+.2f}) + 背离 → 高平局概率")
            if   asian_stars > euro_stars:  asian_dir = f"{asian_dir}不败"
            else:                              asian_dir = f"{euro_dir}不败"
            cross_signals.append({"type": "divergent_shallow", "detail": "浅盘背离→偏平局"})
        elif abs(handicap) >= 1.0:
            detail_parts.append(f"  深盘({handicap:+.2f}) + 背离 → 分胜负")
            cross_signals.append({"type": "divergent_deep", "detail": "深盘背离→分胜负"})
        else:
            detail_parts.append(f"  中盘({handicap:+.2f}) + 背离 → 降级观望")
            cross_signals.append({"type": "divergent_mid", "detail": "中盘背离→降级"})

    state_bonus = 0
    if state_signal:
        state_bonus = state_signal.get("bonus", 0)
        state_dir  = state_signal.get("direction")
        if   state_dir == asian_dir:
            detail_parts.append(f"📈 状态因子支持{asian_dir}方向 (+{state_bonus:.1f}星)")
            base_stars += state_bonus
        elif state_dir and asian_dir and state_dir != asian_dir:
            detail_parts.append(f"📉 状态因子与推荐方向冲突（倾向{state_dir}）({state_bonus:.1f}星)")
            base_stars += state_bonus
        else:
            detail_parts.append("ℹ️ 状态因子无明确方向倾向")
        for d in state_signal.get("detail", []):
            detail_parts.append(f"  · {d}")

    # Podos 风格规则（Rule-P1 状态动量背离 / Rule-P2 主客场优势量化）
    podos_signals = []
    if state_factors and asian_dir not in ("分歧", "观望"):
        p1 = check_rule_p1(state_factors, asian_dir)
        if p1:
            podos_signals.append(p1)
            detail_parts.append(f"🔶 [Podos-Rule-P1] {p1['effect']}")
            detail_parts.append(f"  · {p1['detail']}")
            base_stars += p1.get("bonus", 0)

        p2 = check_rule_p2(state_factors, asian_dir)
        if p2:
            podos_signals.append(p2)
            emoji = "🟢" if p2.get("bonus", 0) > 0 else "🔻"
            detail_parts.append(f"{emoji} [Podos-Rule-P2] {p2['effect']}")
            detail_parts.append(f"  · {p2['detail']}")
            base_stars += p2.get("bonus", 0)

    final_stars = max(0.5, min(5.0, base_stars))
    final_stars = round(final_stars * 2) / 2
    if final_stars <= 2.0:
        asian_dir = "观望"
    if "不败" in str(asian_dir):  final_direction = asian_dir
    elif asian_dir in ("分歧","观望"):  final_direction = "观望"
    else:                              final_direction = asian_dir
    return {
        "direction": final_direction,  "stars": final_stars,
        "asian_dir": asian_result.get("direction", "?"),
        "euro_dir":  euro_result.get("direction", "?"),
        "detail": detail_parts,  "cross_signals": cross_signals,
        "state_bonus": state_bonus,  "podos_signals": podos_signals
    }


# ============================================================
# 主分析入口
# ============================================================

def analyze_euro_asian(data):
    match  = f"{data.get('home_team', '?')} vs {data.get('away_team', '?')}"
    league = data.get("league", "?")
    asian_data = {
        "match": match,  "league": league,
        "home_team": data.get("home_team", ""),
        "away_team": data.get("away_team", ""),
        # 只保留本引擎的 6 家（爬虫会输出并集 8 家，多出的会干扰表决阈值）
        "companies":  {k: v for k, v in data.get("asian_companies", {}).items()
                       if k in ASIAN_COMPANIES}
    }
    handicap = get_consensus_handicap(asian_data["companies"])

    l1_rule6  = check_rule6(asian_data)
    l1_rule22 = check_rule22(asian_data)
    l1_obs_e  = check_obs_e(asian_data)
    l1_rule11 = check_rule11(asian_data)
    l1_signals = [l1_rule6, l1_rule22, l1_obs_e]
    if l1_rule11:
        l1_signals.extend(l1_rule11) if isinstance(l1_rule11, list) else l1_signals.append(l1_rule11)

    majority = majority_vote(asian_data)
    l3_rule19 = check_rule19(handicap)
    l3_rule21 = check_rule21(asian_data, majority, handicap)
    l4_rule9  = check_rule9(asian_data, handicap, majority)
    l4_rule16 = check_rule16(asian_data, handicap)
    l4_rule17 = check_rule17(asian_data, handicap, majority)
    l4_signals = [l4_rule9, l4_rule16, l4_rule17]

    asian_stars = calculate_final_stars(majority, l1_signals, l3_rule21, l4_signals, handicap)
    l1_direction = None
    for s in l1_signals:
        if s and s.get("triggered") and s.get("direction"):
            l1_direction = s["direction"];  break
    if l3_rule21 and l3_rule21.get("direction") == "反":
        if   majority["direction"] == "主":  l1_direction = "客"
        elif majority["direction"] == "客":  l1_direction = "主"
    asian_direction = l1_direction or majority["direction"]
    if asian_stars <= 2.0:  asian_direction = "观望"

    asian_rules = []
    for s in l1_signals:
        if s and s.get("triggered"):  asian_rules.append(s.get("rule", "?"))
    if l3_rule19:  asian_rules.append("Rule 19")
    if l3_rule21:  asian_rules.append("Rule 21")
    for s in l4_signals:
        if s and s.get("triggered"):  asian_rules.append(s.get("rule", "?"))
    asian_result = {
        "direction": asian_direction,  "stars": asian_stars,  "handicap": handicap,
        "majority": majority,  "rules_triggered": asian_rules,
        "l1_signals": l1_signals,  "l3_rule21": l3_rule21,
        "l4_signals": l4_signals,
    }

    euro_result = analyze_euro_odds(data.get("euro_companies", {}), league)

    history    = load_match_history(CSV_PATH)
    ah_csv     = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ah_matches.csv")
    ah_history = load_match_history(ah_csv)
    all_history = history + ah_history
    state_factors = calculate_state_factors(
        data.get("home_team", ""),  data.get("away_team", ""),
        league, all_history, data.get("state_factors")
    )
    state_signal = evaluate_state_signal(state_factors)
    cross_result = cross_validate(asian_result, euro_result, state_signal, state_factors)

    final_direction = cross_result["direction"]
    final_stars    = cross_result["stars"]
    if   final_direction == "主":
        double_pick = "主不败"
    elif final_direction == "客":
        double_pick = "客不败"
    elif "不败" in str(final_direction):
        double_pick = final_direction
    elif abs(handicap) >= 1.0 and any(s.get("type") == "divergent_deep" for s in cross_result.get("cross_signals", [])):
        double_pick = "分胜负"
    else:
        double_pick = "观望"

    if   final_direction == "主":
        ah_rec = f"主 -{handicap:.2f}" if handicap > 0 else (f"主 +{-handicap:.2f}" if handicap < 0 else "主 平手")
    elif final_direction == "客":
        ah_rec = f"客 -{-handicap:.2f}" if handicap < 0 else (f"客 +{handicap:.2f}" if handicap > 0 else "客 平手")
    else:
        ah_rec = "观望"
    ah_format = (f"主 -{handicap:.2f}" if handicap > 0 else
                 f"主 +{-handicap:.2f}" if handicap < 0 else "平手")

    all_rules = list(set(asian_rules + [r.get("rule","?") for r in euro_result.get("rules_triggered", [])]
                       + [s.get("rule","?") for s in cross_result.get("podos_signals", [])]))
    return {
        "match": match,  "league": league,  "handicap": handicap,  "ah_format": ah_format,
        "direction": final_direction,  "stars": final_stars,  "double_pick": double_pick,
        "ah_rec": ah_rec,  "asian_result": asian_result,  "euro_result": euro_result,
        "cross_result": cross_result,  "state_factors": state_factors,
        "state_signal": state_signal,  "all_rules": all_rules,
        "asian_companies": asian_data["companies"],
        "euro_companies": data.get("euro_companies", {}),
    }


# ============================================================
# 报告生成
# ============================================================

def generate_euro_asian_report(result):
    lines = []
    lines.append("═" * 60)
    lines.append(f"  📊 {result['match']} 欧亚综合分析")
    lines.append(f"  📅 {result['league']}")
    lines.append("═" * 60)
    lines.append("")
    lines.append("### 📊 统一推荐表")
    lines.append("")
    lines.append("| 项目 | 推荐 | 盘口 | 星级 | 核心逻辑 |")
    lines.append("|:---:|:---|:---:|:---:|:---|")
    core_logic = []
    for d in (result["cross_result"].get("detail", [])[:3]):
        clean = d.replace("✅ ","").replace("⚠️ ","").replace("❌ ","").replace("📈 ","").replace("📉 ","").replace("ℹ️ ","")
        core_logic.append(clean)
    logic_text = "；".join(core_logic[:2])
    lines.append(f"| **双选** | {result['double_pick']} | — | {stars_to_display(result['stars'])} | {logic_text} |")
    lines.append(f"| **亚盘** | {result['ah_rec']} | {result['ah_format']} | {stars_to_display(result['stars'])} | "
                 f"亚{result['asian_result']['direction']}欧{result['euro_result']['direction']}交叉 |")
    lines.append("")
    lines.append("### 🔹 Step 1: 亚盘6家独立分析")
    lines.append("")
    ar = result["asian_result"]
    lines.append(f"**方向**: {ar['direction']}　|　**信心**: {stars_to_display(ar['stars'])}　|　**盘口**: {result['ah_format']}")
    lines.append(f"**表决**: {ar['majority']['ratio']}　|　**触发规则**: {', '.join(ar['rules_triggered']) if ar['rules_triggered'] else '无'}")
    lines.append("")
    lines.append("| 公司 | 盘口 | 主水 | 客水 | 方向 |")
    lines.append("|:---|:---:|:---:|:---:|:---:|")
    for name in ASIAN_COMPANIES:
        c = result["asian_companies"].get(name)
        if c:
            hcp = c.get("handicap", 0)
            hw_str = f"{c['home_water']:.2f}"
            h_ch = c.get("h_change", 0)
            if abs(h_ch) >= 0.03:
                hw_str += f"{'↓' if h_ch < 0 else '↑'}{abs(h_ch)*100:.0f}"
            lines.append(f"| {name} | {hcp:+.2f} | {hw_str} | {c['away_water']:.2f} | {company_direction(c)} |")
    lines.append("")
    lines.append("### 🔹 Step 2: 欧赔4家独立分析")
    lines.append("")
    er = result["euro_result"]
    lines.append(f"**方向**: {er.get('direction','?')}　|　**信心**: ⭐ {er.get('stars',0)}　|　"
                 f"**多数**: {er.get('majority',{}).get('count',0)}/{er.get('majority',{}).get('total',0)}")
    probs = er.get("implied_prob", {})
    if probs:
        lines.append(f"**隐含概率**: 主{probs.get('主胜',0)*100:.0f}% / "
                     f"平{probs.get('平局',0)*100:.0f}% / "
                     f"客{probs.get('客胜',0)*100:.0f}%")
    lines.append("")
    lines.append("| 公司 | 主胜 | 平局 | 客胜 | 变化趋势 | 方向 |")
    lines.append("|:---|:---:|:---:|:---:|:---|:---:|")
    for name in EURO_COMPANIES_ACTIVE:
        c = result["euro_companies"].get(name)
        if c:
            ho, do, ao = c.get("home_odds",0), c.get("draw_odds",0), c.get("away_odds",0)
            h_ch, d_ch, a_ch = c.get("home_change",0), c.get("draw_change",0), c.get("away_change",0)
            trends = []
            for lbl, ch in [("主",h_ch),("平",d_ch),("客",a_ch)]:
                if abs(ch) >= 0.03:  trends.append(f"{lbl}{'↓' if ch<0 else '↑'}{abs(ch*100):.0f}")
            trend_str = " ".join(trends) if trends else "—"
            dir_label = er.get("companies_detail",{}).get(name, "?")
            if dir_label is None:  dir_label = "—"
            lines.append(f"| {name} | {ho:.2f} | {do:.2f} | {ao:.2f} | {trend_str} | {dir_label} |")
    if result["euro_companies"].get("必发"):
        bf = result["euro_companies"]["必发"]
        if any(abs(bf.get(k,0)) >= 0.01 for k in ("home_change","draw_change","away_change")):
            lines.append(f"| 必发 _(休眠)_ | {bf.get('home_odds',0):.2f} | "
                         f"{bf.get('draw_odds',0):.2f} | {bf.get('away_odds',0):.2f} | "
                         f"⚠️ Rule 23 休眠 | — |")
    lines.append("")
    lines.append("### 🔹 Step 3: 两盘交叉验证 + 状态因子")
    lines.append("")
    lines.append(f"**最终方向**: {result['direction']}　|　**最终信心**: {stars_to_display(result['stars'])}")
    lines.append("")
    for d in result["cross_result"].get("detail", []):
        lines.append(f"  {d}")
    lines.append("")
    sf = result["state_factors"]
    lines.append(f"| 指标 | 主队 | 客队 |")
    lines.append(f"|:---|:---:|:---:|")
    lines.append(f"| 连胜 | {sf.get('home_win_streak') or 0} | {sf.get('away_win_streak') or 0} |")
    lines.append(f"| 连败 | {sf.get('home_loss_streak') or 0} | {sf.get('away_loss_streak') or 0} |")
    lines.append(f"| 近期 | {sf.get('home_form_detail') or '?'} | {sf.get('away_form_detail') or '?'} |")
    hfs = sf.get('home_form_score')
    afs = sf.get('away_form_score')
    hfs_str = f"{hfs:.2f}" if hfs is not None else "N/A"
    afs_str = f"{afs:.2f}" if afs is not None else "N/A"
    lines.append(f"| 评分 | {hfs_str} | {afs_str} |")
    home_ppg = sf.get("home_ppg")
    away_away_ppg = sf.get("away_away_ppg")
    if home_ppg is not None and away_away_ppg is not None:
        lines.append(f"| 场均积分(PPG) | {home_ppg:.2f} | {away_away_ppg:.2f} |")
    lines.append("")
    lines.append("═" * 60)
    lines.append("### 🎯 最终推荐")
    lines.append("")
    lines.append("| 项目 | 推荐 | 星级 | 注码 |")
    lines.append(f"|:---|:---|:---:|:---:|")
    lines.append(f"| **双选** | {result['double_pick']} | {stars_to_display(result['stars'])} | {stars_to_recommendation(result['stars'])} |")
    if result["direction"] != "观望":
        lines.append(f"| **亚盘** | {result['ah_rec']} | {stars_to_display(result['stars'])} | {stars_to_recommendation(result['stars'])} |")
    else:
        lines.append(f"| **亚盘** | 观望（不投） | — | — |")
    lines.append("")
    return "\n".join(lines)


# ============================================================
# CSV 操作
# ============================================================

def init_csv():
    if not os.path.exists(CSV_PATH):
        os.makedirs(os.path.dirname(CSV_PATH), exist_ok=True)
        with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
            csv.writer(f).writerow(CSV_HEADERS)


def append_to_csv(result, data, notes=""):
    init_csv()
    row = [
        data.get("id", ""),
        # date 列 = 比赛日期 + 开赛时间，转为上海时区
        shanghai_datetime(data.get("date", ""), data.get("match_time", ""),
                          data.get("match_time_utc")),
        result["league"],
        data.get("home_team", ""),  data.get("away_team", ""),
        result["handicap"],  result["asian_result"]["direction"],
        result["euro_result"]["direction"],  result["direction"],
        result["asian_result"]["stars"],  result["euro_result"]["stars"],
        result["stars"],  ", ".join(result["all_rules"]),
        result["double_pick"],
        f"{result['ah_rec']} {stars_to_display(result['stars'])}",
        "", "", "", notes
    ]
    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        csv.writer(f).writerow(row)


# ============================================================
# 回测 + 统计
# ============================================================

def show_stats():
    if not os.path.exists(CSV_PATH):
        print("❌ 欧亚CSV不存在");  return
    with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
        rows = list(csv.DictReader(f))
    total    = len(rows)
    finished = [r for r in rows if r.get("actual_result","").strip()]
    print("=" * 60)
    print("  📊 欧亚综合分析战绩")
    print("=" * 60)
    print(f"  总场次：{total}")
    print(f"  已赛完：{len(finished)}")
    if not finished:  print("  ⚠️ 暂无完赛数据\n");  return
    wins   = sum(1 for r in finished if r.get("ah_result","") in ("赢","赢半"))
    losses = sum(1 for r in finished if r.get("ah_result","") in ("输","输半"))
    walks  = sum(1 for r in finished if r.get("ah_result","") == "走")
    print(f"\n  【亚盘投注】")
    print(f"    赢: {wins} | 输: {losses} | 走: {walks}")
    if wins + losses > 0:  print(f"    命中率: {wins/(wins+losses)*100:.1f}%\n")
    correct, wrong = 0, 0
    for r in finished:
        d = r.get("final_direction","");  res = r.get("actual_result","")
        if   d == "主" and "主胜" in res:  correct += 1
        elif d == "客" and "客胜" in res:  correct += 1
        elif d and d not in ("观望","分歧") and res:  wrong += 1
    if correct + wrong > 0:
        print(f"  【方向准确率】{correct}/{correct+wrong} = {correct/(correct+wrong)*100:.1f}%\n")


# ============================================================
# 命令行入口
# ============================================================

def main():
    parser = argparse.ArgumentParser(
        description="欧亚综合分析引擎 v1.0",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python euro_asian_task.py 3001113                                # 先爬球探单场再分析
  python euro_asian_task.py https://www.jihai8.com/zq/asia_3001113.html
  python euro_asian_task.py --input data/ea_template.json
  python euro_asian_task.py --stats
  python euro_asian_task.py --template
        """
    )
    parser.add_argument("match", nargs="?", help="比赛 ID 或球探亚盘 URL（自动爬取、填充 state_factors 再分析）")
    parser.add_argument("--input",    "-i",  help="JSON 输入文件路径（含亚盘+欧赔数据）")
    parser.add_argument("--batch",          action="store_true", help="批量爬取五大联赛并逐场分析（日期/联赛在 fetch_jihai8.py 顶部配置）")
    parser.add_argument("--stats",    "-s",  action="store_true", help="战绩统计")
    parser.add_argument("--template", "-t",  action="store_true", help="打印输入模板")
    parser.add_argument("--no-save",        action="store_true", help="不保存到 CSV")
    parser.add_argument("--no-json",        action="store_true", help="不写单场 JSON 文件（仍按 --no-save 决定是否写 CSV）")
    args = parser.parse_args()

    if args.template:
        template = {
            "id": "R86",  "date": "2026-06-03",  "league": "日职乙",
            "home_team": "主队名",  "away_team": "客队名",
            "asian_companies": {
                "澳*": {"handicap": 0.5, "home_water": 0.92, "away_water": 0.94,
                         "h_change": 0.03, "a_change": -0.02, "trend": "持平"},
                "明*": {"handicap": 0.5, "home_water": 0.88, "away_water": 0.98,
                         "h_change": -0.09, "a_change": 0.05, "trend": "持平"},
                "港马*": {"handicap": 0.5, "home_water": 0.90, "away_water": 0.96,
                          "h_change": 0.01, "a_change": -0.01, "trend": "持平"},
                "36*": {"handicap": 0.5, "home_water": 0.85, "away_water": 1.01,
                         "h_change": -0.03, "a_change": 0.04, "trend": "持平"},
                "易*": {"handicap": 0.5, "home_water": 0.89, "away_water": 0.97,
                         "h_change": -0.04, "a_change": 0.03, "trend": "持平"},
                "平博": {"handicap": 0.5, "home_water": 0.90, "away_water": 0.96,
                          "h_change": 0.00, "a_change": 0.01, "trend": "持平"}
            },
            "euro_companies": {
                "威*": {"home_odds": 1.80, "draw_odds": 3.50, "away_odds": 4.20,
                         "home_change": -0.05, "draw_change": 0.10, "away_change": 0.15},
                "立*": {"home_odds": 1.85, "draw_odds": 3.40, "away_odds": 4.10,
                         "home_change": -0.03, "draw_change": 0.08, "away_change": 0.12},
                "Interwet*": {"home_odds": 1.82, "draw_odds": 3.55, "away_odds": 4.15,
                             "home_change": -0.06, "draw_change": 0.11, "away_change": 0.14},
                "平博": {"home_odds": 1.78, "draw_odds": 3.60, "away_odds": 4.30,
                          "home_change": -0.04, "draw_change": 0.09, "away_change": 0.13},
                "必发": {"home_odds": 1.79, "draw_odds": 3.55, "away_odds": 4.25,
                          "home_change": 0.01, "draw_change": 0.02, "away_change": -0.01}
            },
            "state_factors": {
                "home_win_streak":  0,  "home_loss_streak": 0,
                "away_win_streak":  0,  "away_loss_streak": 0,
                "home_form_score":  0.5,  "away_form_score": 0.5,
                "home_form_detail": "近5场2W2D1L",
                "away_form_detail": "近5场1W1D3L",
                "home_ppg": None, "away_away_ppg": None
            }
        }
        print("📋 欧亚任务输入模板：")
        print(json.dumps(template, ensure_ascii=False, indent=2))
        return

    if args.stats:  show_stats();  return

    # 情况0：批量爬取五大联赛 + 填充 state_factors + 逐场分析
    if args.batch:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from fetch_jihai8 import scrape_5leagues
        results = scrape_5leagues(prefill=True)  # 爬取 + 自动填充 state_factors
        if not results:
            print("无比赛可分析。")
            return
        print(f"\n开始逐场分析（共 {len(results)} 场）...")
        for data, path in results:
            print(f"\n{'=' * 60}\n{data.get('league')} {data.get('home_team')} vs {data.get('away_team')}\n{'=' * 60}")
            result = analyze_euro_asian(data)
            print(generate_euro_asian_report(result))
            if not args.no_save:
                append_to_csv(result, data)
        print(f"\n✅ 批量分析完成，共 {len(results)} 场，已保存到 {CSV_PATH}")
        return

    # 情况1：传入比赛 ID/URL → 先爬取 + 自动填充 state_factors，再分析
    if args.match:
        sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        from fetch_match_jihai8 import scrape_match, parse_match_id
        match_id = parse_match_id(args.match)
        print(f"[球探] 先爬取比赛 id={match_id} ...")
        data, out_path = scrape_match(match_id,
                                      prefill=not args.no_json,
                                      save_json=not args.no_json)
        if data is None:
            print("❌ 未获取到比赛数据（jihai8 抓取失败或被限流），已终止分析。")
            return
        if out_path:
            print(f"✅ 已保存 {out_path}")

        # 亚盘数据为空（抓取失败/被限流）→ 终止
        if not data.get("asian_companies"):
            print("❌ 未获取到亚盘数据（jihai8 抓取失败或被限流），已终止分析。")
            return

        result = analyze_euro_asian(data)
        print(generate_euro_asian_report(result))
        if not args.no_save:
            append_to_csv(result, data)
            print(f"\n✅ 已保存到 {CSV_PATH}")
        return

    if args.input:
        if not os.path.exists(args.input):
            print(f"❌ 文件不存在：{args.input}");  return
        data   = json.load(open(args.input, "r", encoding="utf-8"))
        if not data.get("asian_companies"):
            print("❌ JSON 中无亚盘数据（asian_companies 为空），已终止分析。")
            return
        result = analyze_euro_asian(data)
        print(generate_euro_asian_report(result))
        if not args.no_save:
            append_to_csv(result, data)
            print(f"\n✅ 已保存到 {CSV_PATH}")
        return

    print("📊 欧亚综合分析引擎 v1.0")
    print()
    print("使用方法：")
    print("  python euro_asian_task.py <比赛ID或URL>          先爬球探单场再分析")
    print("  python euro_asian_task.py --batch                批量爬五大联赛+填充+逐场分析")
    print("  python euro_asian_task.py --input data/xxx.json   单场分析")
    print("  python euro_asian_task.py --stats                 战绩统计")
    print("  python euro_asian_task.py --template              查看输入模板")
    print()
    print(f"📁 数据存储：{CSV_PATH}")
    print(f"📋 输入模板：--template")


if __name__ == "__main__":
    main()
