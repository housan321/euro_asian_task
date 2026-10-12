#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
亚盘专属分析工具 — Asian Handicap Analysis Engine
==================================================
输入 7 家公司亚盘数据 → 4层规则引擎 → 结构化分析报告
支持：单场分析 / 全量回测 / 战绩统计

规则来源：MEMORY.md + RULES.md（2026-05-24版）
"""

import json
import csv
import os
import re
import sys
import argparse
from datetime import datetime, timedelta, timezone
from collections import Counter

# ============================================================
# 全局常量（调整阈值在此修改）
# ============================================================

# 规则阈值
RULE22_THRESHOLD = 0.08       # Rule 22：主水降幅 ≥ 8 点触发
RULE6_THRESHOLD = 1.10        # Rule 6：明*主水 ≥ 1.10
RULE11_THRESHOLD = 0.10       # Rule 11：港马*水位变化 ≥ 10 点
OBS_E_THRESHOLD = 0.25        # 观察E：单家反向 ≥ 25 点
DEEP_HANDICAP = 1.0           # 深盘阈值
SUPER_DEEP_HANDICAP = 1.5     # 超级深盘阈值
SHALLOW_HANDICAP_MAX = 0.25   # 浅盘上限（平手/平半）
LOW_WATER = 0.85              # 低水阈值
HIGH_WATER = 1.10             # 高水阈值
HKJC_EXTREME_LOW = 0.80       # 港马*极低水
HKJC_EXTREME_HIGH = 1.10      # 港马*极高水

# 7 家公司列表
COMPANIES = ["澳*", "明*", "港马*", "36*", "Interwet*", "易*", "Crow*"]

# CSV 路径
CSV_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ah_matches.csv")
TEMPLATE_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data", "ah_template.json")

# CSV 表头
CSV_HEADERS = [
    "id", "date", "league", "home_team", "away_team",
    "handicap_consensus", "direction", "stars",
    "rules_triggered", "recommendation",
    "actual_result", "actual_score", "ah_result", "notes"
]


# ============================================================
# 工具函数
# ============================================================

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


def load_json(path):
    """加载 JSON 文件"""
    with open(path, "r", encoding="utf-8") as f:
        return json.load(f)


def get_consensus_handicap(companies):
    """取多数公司一致的盘口值"""
    handicaps = [c["handicap"] for c in companies.values()]
    counter = Counter(handicaps)
    return counter.most_common(1)[0][0]


def parse_match_name(match_str):
    """从 '浦和红钻 vs 川崎前锋' 提取主客队名"""
    parts = match_str.split(" vs ")
    if len(parts) == 2:
        return parts[0].strip(), parts[1].strip()
    return "", ""


# ============================================================
# 第2层：单家公司方向判定
# ============================================================

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

    # 水位无变化 → 分歧
    if abs(h_ch) < 0.005 and abs(a_ch) < 0.005:
        return "分歧"

    home_votes = 0
    away_votes = 0

    if hcp > 0:
        # 主队让球
        if h_ch < -0.005:  # 主水↓ → 看好主
            home_votes += 1
        if h_ch > 0.005:   # 主水↑ → 看衰主
            away_votes += 1
        if a_ch < -0.005:  # 客水↓ → 看衰主
            away_votes += 1
        if a_ch > 0.005:   # 客水↑ → 看好主
            home_votes += 1
    elif hcp < 0:
        # 客队让球
        if a_ch < -0.005:  # 客水↓ → 看好客
            away_votes += 1
        if a_ch > 0.005:   # 客水↑ → 看衰客
            home_votes += 1
        if h_ch < -0.005:  # 主水↓ → 看衰客
            home_votes += 1
        if h_ch > 0.005:   # 主水↑ → 看好客
            away_votes += 1
    else:
        # 平手盘 → 哪边降水看好哪边
        if h_ch < a_ch:
            return "主"
        elif a_ch < h_ch:
            return "客"
        else:
            return "分歧"

    if home_votes > away_votes:
        return "主"
    elif away_votes > home_votes:
        return "客"
    else:
        return "分歧"


# ============================================================
# 第1层：独立致命信号
# ============================================================

def check_rule6(data):
    """Rule 6：明*主水 ≥ 1.10 → 极强看衰主队"""
    ming = data["companies"].get("明*")
    if ming and ming["home_water"] >= RULE6_THRESHOLD:
        return {
            "triggered": True,
            "rule": "Rule 6",
            "direction": "客",
            "detail": f"明*主水 {ming['home_water']:.2f} ≥ {RULE6_THRESHOLD} → 看衰主队",
            "weight": 2
        }
    return None


def check_rule22(data):
    """Rule 22：≥3 家公司主水降 ≥ 8 点 → 真实看好主队"""
    drops = []
    for name, c in data["companies"].items():
        h_ch = c.get("h_change", 0)
        if h_ch <= -RULE22_THRESHOLD:
            drops.append((name, h_ch))

    if len(drops) >= 3:
        detail = "、".join([f"{n}{ch*100:+.0f}点" for n, ch in drops])
        return {
            "triggered": True,
            "rule": "Rule 22",
            "direction": "主",
            "detail": f"{len(drops)}家公司主水降≥8点：{detail} → 真实看好主队",
            "weight": 2
        }
    return None


def check_obs_e(data):
    """观察E：单家公司水位反向变动 ≥ 25 点 → 知情资金信号"""
    # 先统计多数方向
    directions = []
    for name, c in data["companies"].items():
        d = company_direction(c)
        if d != "分歧":
            directions.append(d)

    if not directions:
        return None

    majority = Counter(directions).most_common(1)[0][0]

    # 找反向极端公司
    for name, c in data["companies"].items():
        h_ch = c.get("h_change", 0)
        a_ch = c.get("a_change", 0)
        d = company_direction(c)

        if d == majority or d == "分歧":
            continue

        if majority == "主":
            if h_ch >= OBS_E_THRESHOLD:
                return {
                    "triggered": True,
                    "rule": "观察E",
                    "direction": "客",
                    "detail": f"{name}主水升{h_ch*100:+.0f}点（反向≥25点）→ 知情资金看客",
                    "weight": 1
                }
        else:
            if h_ch <= -OBS_E_THRESHOLD:
                return {
                    "triggered": True,
                    "rule": "观察E",
                    "direction": "主",
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
    a_ch = hkjc.get("a_change", 0)

    signals = []
    if h_ch >= RULE11_THRESHOLD:
        signals.append({
            "triggered": True,
            "rule": "Rule 11",
            "direction": "客",
            "detail": f"港马*主水升{h_ch*100:+.0f}点(≥10点) → 看衰让球方",
            "weight": 1
        })
    elif h_ch <= -RULE11_THRESHOLD:
        signals.append({
            "triggered": True,
            "rule": "Rule 11",
            "direction": "主",
            "detail": f"港马*主水降{h_ch*100:+.0f}点(≥10点) → 看好让球方",
            "weight": 1
        })

    return signals if signals else None


# ============================================================
# 第2层：多数表决
# ============================================================

def majority_vote(data):
    """7 家公司多数表决"""
    directions = {}
    for name, c in data["companies"].items():
        directions[name] = company_direction(c)

    counts = Counter(directions.values())
    home_votes = counts.get("主", 0)
    away_votes = counts.get("客", 0)
    split = counts.get("分歧", 0)

    total_decided = home_votes + away_votes

    # 判定方向和基础信心
    if total_decided == 0:
        return {"direction": "观望", "stars": 0, "detail": "7家全部无方向", "ratio": "0:0"}

    if home_votes >= 6:
        stars = 4
    elif home_votes == 5:
        stars = 3
    elif home_votes == 4:
        stars = 2
    else:
        stars = 1

    if home_votes > away_votes:
        direction = "主"
    elif away_votes > home_votes:
        direction = "客"
        # 对称计算
        if away_votes >= 6:
            stars = 4
        elif away_votes == 5:
            stars = 3
        elif away_votes == 4:
            stars = 2
        else:
            stars = 1
    else:
        direction = "观望"

    ratio = f"{home_votes}:{away_votes}"
    if split > 0:
        ratio += f"（分歧{split}）"

    return {
        "direction": direction,
        "stars": stars,
        "detail": f"表决 {ratio} → {'看好' + direction if direction != '观望' else '无共识'}",
        "ratio": ratio,
        "home_votes": home_votes,
        "away_votes": away_votes
    }


# ============================================================
# 第3层：盘口修正
# ============================================================

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
    same_trend = 0
    trends = []
    for name in COMPANIES:
        c = data["companies"].get(name)
        if c:
            t = c.get("trend", "持平")
            trends.append(t)

    trend_counter = Counter(trends)
    max_trend = trend_counter.most_common(1)[0]
    if max_trend[0] != "持平" and max_trend[1] >= 4:
        same_trend = max_trend[1]

    # 条件2：港马*极低/极高水
    hkjc = data["companies"].get("港马*")
    hkjc_extreme = False
    if hkjc:
        hw = hkjc["home_water"]
        if hw <= HKJC_EXTREME_LOW or hw >= HKJC_EXTREME_HIGH:
            hkjc_extreme = True

    # 条件3：明*主水 ≥ 1.10（Rule 6 联动）
    ming_high = False
    ming = data["companies"].get("明*")
    if ming and ming["home_water"] >= RULE6_THRESHOLD:
        ming_high = True

    # 判定是否触发
    triggers = sum([1 for x in [same_trend >= 4, hkjc_extreme, ming_high] if x])

    if triggers == 0:
        return None

    # ⚠️ 例外：Rule 22 触发时，全部同向是真实信号而非陷阱
    # R63案例：7家集体降盘降水 + Rule 22触发 = 真实看好，不取反
    rule22_triggered = check_rule22(data)
    if rule22_triggered:
        return {
            "triggered": True,
            "effect": "部分触发但同向（Rule 22覆盖）→ 不取反",
            "detail": f"Rule 22触发 + {triggers}项Rule 21条件 = 真实看好信号，非陷阱",
            "direction": None  # 不取反
        }

    # ⚠️ 例外：多数表决 ≥5家同向时，Rule 21 不取反
    # R76/R78/R79：多数 5:2+/7:0 才是真实信号，取反导致误判
    mv_home = majority_result.get("home_votes", 0)
    mv_away = majority_result.get("away_votes", 0)
    if max(mv_home, mv_away) >= 5:
        return {
            "triggered": True,
            "effect": "多数≥5家同向（Rule 21不取反）→ 信任多数",
            "detail": f"多数{max(mv_home, mv_away)}家同向 + {triggers}项Rule 21条件 = 多数信号更强，不取反",
            "direction": None  # 不取反
        }

    is_shallow = 0 <= abs(handicap) <= SHALLOW_HANDICAP_MAX
    is_deep = abs(handicap) >= DEEP_HANDICAP

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


# ============================================================
# 第4层：深盘特殊规则
# ============================================================

def check_rule9(data, handicap, majority_result=None):
    """Rule 9：深盘诱盘识别"""
    if abs(handicap) < SUPER_DEEP_HANDICAP:
        return None

    # 多数表决 ≥5家同向 → 深盘真实强弱差距，非诱盘
    if majority_result:
        mv_home = majority_result.get("home_votes", 0)
        mv_away = majority_result.get("away_votes", 0)
        if max(mv_home, mv_away) >= 5:
            return None

    # 集体升盘？
    upgrades = sum(1 for c in data["companies"].values()
                   if c.get("trend") == "升盘")

    # 港马*升水？
    hkjc = data["companies"].get("港马*")
    hkjc_up = hkjc and hkjc.get("h_change", 0) >= 0.03 if hkjc else False

    if upgrades >= 4 and hkjc_up:
        return {
            "triggered": True,
            "effect": "诱盘 → 反向操作",
            "detail": f"深盘{handicap:.2f} + {upgrades}家升盘 + 港马*升水 → 诱盘信号"
        }
    return None


def check_rule16(data, handicap):
    """Rule 16：深盘+主水全破1.0 = 大热倒灶"""
    if abs(handicap) < DEEP_HANDICAP:
        return None

    broken = sum(1 for c in data["companies"].values()
                 if c["home_water"] >= 1.0)
    downgrades = sum(1 for c in data["companies"].values()
                     if c.get("trend") == "降盘")

    if broken >= 4 and downgrades >= 1:
        return {
            "triggered": True,
            "effect": "大热倒灶 → 降级观望",
            "detail": f"深盘{handicap:.2f} + {broken}家主水破1.0 + {downgrades}家降盘 → 大热倒灶"
        }
    return None


def check_rule17(data, handicap, majority_result=None):
    """Rule 17：深盘隐性大热倒灶"""
    # 此规则需要欧赔客胜分歧数据，亚盘独立模式仅做标记
    if abs(handicap) < SUPER_DEEP_HANDICAP:
        return None
    # 多数表决 ≥5家同向（71%）→ 深盘一致信号，不降信心
    # 原阈值6过于严格，R76截图数据5:1实际赢盘，5家已足够
    if majority_result:
        mv_home = majority_result.get("home_votes", 0)
        mv_away = majority_result.get("away_votes", 0)
        if max(mv_home, mv_away) >= 5:
            return None
    # 标记需要欧赔验证
    return {
        "triggered": True,
        "effect": "降低信心，需欧赔客胜分歧确认",
        "detail": f"深盘{handicap:.2f} → 两盘一致时注意客胜异常分歧"
    }


# ============================================================
# 信心度计算
# ============================================================

def calculate_final_stars(majority, layer1_signals, layer3, layer4, handicap):
    """
    最终信心度计算：
    基础 = 第2层表决
    × 第3层修正
    × 第4层修正
    + 第1层加分
    """
    stars = majority["stars"]

    # 第1层加分
    l1_bonus = 0
    l1_signals_list = [s for s in layer1_signals if s]
    # 展开 Rule 11 的列表
    flat_l1 = []
    for s in l1_signals_list:
        if isinstance(s, list):
            flat_l1.extend(s)
        else:
            flat_l1.append(s)
    l1_bonus = sum(s.get("weight", 0) for s in flat_l1) * 0.5
    stars += l1_bonus

    # 第3层修正
    if layer3:
        if layer3.get("effect", "").startswith("方向取反"):
            stars = max(0, stars - 3)
        elif "降级" in layer3.get("effect", ""):
            stars = max(0, stars - 2)

    # 第4层修正
    for l4 in layer4:
        if l4 is None:
            continue
        effect = l4.get("effect", "")
        if "大热倒灶" in effect:
            stars = max(0, stars - 2)
        elif "诱盘" in effect:
            stars = max(0, stars - 3)
        elif "降低信心" in effect:
            stars = max(0, stars - 1)

    # 限制范围
    stars = max(0.5, min(5.0, stars))
    return round(stars * 2) / 2  # 精度 0.5


def stars_to_display(stars):
    """星级 → 显示文字"""
    full = int(stars)
    half = (stars - full) >= 0.5
    s = "⭐" * full
    if half:
        s += "½"
    return f"{s}（{stars}星）"


def stars_to_recommendation(stars):
    """星级 → 注码建议"""
    if stars >= 4.5:
        return "中等"
    elif stars >= 3.5:
        return "小-中"
    elif stars >= 2.5:
        return "小注"
    elif stars >= 1.5:
        return "极小注"
    else:
        return "观望"


# ============================================================
# 主分析函数
# ============================================================

def analyze_match(data):
    """对一场比赛执行完整 4 层分析"""
    match = data.get("match", f"{data.get('home_team', '?')} vs {data.get('away_team', '?')}")
    league = data.get("league", "?")
    companies = data["companies"]

    handicap = get_consensus_handicap(companies)

    # === 第1层：独立致命信号 ===
    l1_rule6 = check_rule6(data)
    l1_rule22 = check_rule22(data)
    l1_obs_e = check_obs_e(data)
    l1_rule11 = check_rule11(data)

    l1_signals = [l1_rule6, l1_rule22, l1_obs_e]
    if l1_rule11:
        l1_signals.extend(l1_rule11)

    # === 第2层：多数表决 ===
    majority = majority_vote(data)

    # === 第3层：盘口修正 ===
    l3_rule19 = check_rule19(handicap)
    l3_rule21 = check_rule21(data, majority, handicap)

    # === 第4层：深盘特殊 ===
    l4_rule9 = check_rule9(data, handicap, majority)
    l4_rule16 = check_rule16(data, handicap)
    l4_rule17 = check_rule17(data, handicap, majority)
    l4_signals = [l4_rule9, l4_rule16, l4_rule17]

    # === 信心度计算 ===
    final_stars = calculate_final_stars(
        majority,
        l1_signals,
        l3_rule21,  # 第3层主要取 Rule 21
        l4_signals,
        handicap
    )

    # === 最终方向 ===
    # 第1层覆盖
    l1_direction = None
    for s in l1_signals:
        if s and s.get("triggered") and s.get("direction"):
            l1_direction = s["direction"]
            break

    # Rule 21 取反（仅当 direction="反"）
    if l3_rule21 and l3_rule21.get("direction") == "反":
        if majority["direction"] == "主":
            l1_direction = "客"
        elif majority["direction"] == "客":
            l1_direction = "主"

    final_direction = l1_direction or majority["direction"]

    # === 弱信号保护：≤2星强制观望 ===
    # R78教训：4:3微弱多数 + 无强规则修正 → 2星 → 实际错误
    # ≤2星的信号可靠性不足以支撑投注，强制降为观望
    if final_stars <= 2.0:
        final_direction = "观望"

    # === 双选 ===
    if final_direction == "主":
        double_pick = "主不败"
    elif final_direction == "客":
        double_pick = "客不败"
    elif l3_rule21:
        double_pick = "分胜负" if abs(handicap) >= DEEP_HANDICAP else "观望"
    else:
        double_pick = "观望"

    # === 亚盘推荐（用户偏好：正数=主让球，如 0.5=主让半球 → "主 -0.50"）===
    if final_direction == "主":
        if handicap > 0:
            ah_rec = f"主 -{handicap:.2f}"
        elif handicap < 0:
            ah_rec = f"主 +{-handicap:.2f}"
        else:
            ah_rec = "主 平手"
    elif final_direction == "客":
        if handicap < 0:
            ah_rec = f"客 -{-handicap:.2f}"
        elif handicap > 0:
            ah_rec = f"客 +{handicap:.2f}"
        else:
            ah_rec = "客 平手"
    else:
        ah_rec = "观望"

    # 盘口显示格式
    if handicap > 0:
        ah_format = f"主 -{handicap:.2f}"
    elif handicap < 0:
        ah_format = f"主 +{-handicap:.2f}"
    else:
        ah_format = "平手"

    return {
        "match": match,
        "league": league,
        "handicap": handicap,
        "ah_format": ah_format,
        "direction": final_direction,
        "stars": final_stars,
        "double_pick": double_pick,
        "ah_rec": ah_rec,
        "l1_signals": l1_signals,
        "l3_rule19": l3_rule19,
        "l3_rule21": l3_rule21,
        "l4_signals": l4_signals,
        "majority": majority,
        "companies": companies
    }


# ============================================================
# 输出格式化
# ============================================================

def generate_report(result):
    """生成 Markdown 格式分析报告"""
    lines = []
    lines.append("═" * 55)
    lines.append(f"  📊 {result['match']} 亚盘分析")
    lines.append("═" * 55)
    lines.append("")

    # 核心结果
    team_side = "主队" if result["direction"] == "主" else ("客队" if result["direction"] == "客" else "观望")
    lines.append(f"  方向：{team_side}")
    lines.append(f"  盘口：{result['ah_format']}")
    lines.append(f"  信心：{stars_to_display(result['stars'])}")
    lines.append(f"  注码：{stars_to_recommendation(result['stars'])}")
    lines.append("")

    # 规则触发
    all_rules = []
    # 第1层
    for s in result["l1_signals"]:
        if s and s.get("triggered"):
            all_rules.append(("✅", s["rule"], s["detail"]))
    # 第3层
    if result["l3_rule19"]:
        all_rules.append(("⚠️", "Rule 19", result["l3_rule19"]["detail"]))
    if result["l3_rule21"]:
        all_rules.append(("⚠️", "Rule 21", result["l3_rule21"]["detail"]))
    # 第4层
    for s in result["l4_signals"]:
        if s and s.get("triggered"):
            all_rules.append(("⚠️", s.get("rule", "Rule ?"), s["detail"]))

    # 多数表决
    all_rules.append(("ℹ️", "多数表决", result["majority"]["detail"]))

    if all_rules:
        lines.append("【规则触发】")
        for icon, rule, detail in all_rules:
            lines.append(f"  {icon} {rule}：{detail}")
        lines.append("")

    # 7 家公司明细
    lines.append(f"【{len(result['companies'])} 家公司明细】")
    lines.append(f"  {'公司':<12} {'盘口':>6}  {'主水':>8}  {'客水':>8}  {'方向':<6}")
    lines.append("  " + "─" * 42)
    for name in COMPANIES:
        c = result["companies"].get(name)
        if c:
            hcp = c["handicap"]
            hw = c["home_water"]
            aw = c["away_water"]
            h_ch = c.get("h_change", 0)
            dir_label = company_direction(c)
            # 水位变化标注
            hw_str = f"{hw:.2f}"
            if abs(h_ch) >= 0.03:
                arrow = "↓" if h_ch < 0 else "↑"
                hw_str += f"{arrow}{abs(h_ch)*100:.0f}"
            lines.append(f"  {name:<12} {hcp:>6.2f}  {hw_str:>8}  {aw:>8.2f}  {dir_label:<6}")
    lines.append("")

    # 最终推荐
    lines.append("【最终推荐】")
    lines.append(f"  双选　：{result['double_pick']} {stars_to_display(result['stars'])}")
    if result["direction"] != "观望":
        lines.append(f"  亚盘　：{result['ah_rec']} {stars_to_display(result['stars'])}")
    else:
        lines.append(f"  亚盘　：观望（不投）")
    lines.append("")

    return "\n".join(lines)


# ============================================================
# CSV 操作
# ============================================================

def init_csv():
    """初始化 CSV（仅当不存在时）"""
    if not os.path.exists(CSV_PATH):
        with open(CSV_PATH, "w", newline="", encoding="utf-8-sig") as f:
            writer = csv.writer(f)
            writer.writerow(CSV_HEADERS)
        print(f"✅ 已创建 {CSV_PATH}")


def append_to_csv(result, data, notes=""):
    """追加一条分析记录到 CSV"""
    init_csv()

    # 规则名称汇总
    rules = []
    for s in result["l1_signals"]:
        if s and s.get("triggered"):
            rules.append(s["rule"])
    if result["l3_rule19"]:
        rules.append("Rule 19")
    if result["l3_rule21"]:
        rules.append("Rule 21")
    for s in result["l4_signals"]:
        if s and s.get("triggered"):
            rules.append(s.get("rule", "?"))

    row = [
        data.get("id", ""),
        # date 列 = 比赛日期 + 开赛时间，转为上海时区
        shanghai_datetime(data.get("date", ""), data.get("match_time", ""),
                          data.get("match_time_utc")),
        result["league"],
        data.get("home_team", ""),
        data.get("away_team", ""),
        result["handicap"],
        result["direction"],
        result["stars"],
        ", ".join(rules),
        f"{result['ah_rec']} {stars_to_display(result['stars'])}",
        "",  # actual_result（赛后填）
        "",  # actual_score
        "",  # ah_result
        notes
    ]

    with open(CSV_PATH, "a", newline="", encoding="utf-8-sig") as f:
        writer = csv.writer(f)
        writer.writerow(row)

    print(f"✅ 已追加到 {CSV_PATH}")


# ============================================================
# 回测模式
# ============================================================

def backtest():
    """读取 CSV 中所有记录，统计命中率"""
    if not os.path.exists(CSV_PATH):
        print("❌ CSV 文件不存在，无法回测")
        return

    with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total = len(rows)
    finished = [r for r in rows if r.get("actual_result", "").strip()]
    pending = total - len(finished)

    print("=" * 60)
    print("  📊 亚盘规则回测报告")
    print("=" * 60)
    print(f"  总记录：{total} 场")
    print(f"  已赛完：{len(finished)} 场")
    print(f"  待赛　：{pending} 场")
    print()

    if not finished:
        print("  ⚠️ 暂无完赛数据，请先填写 actual_result 和 ah_result 字段")
        return

    # 亚盘命中率
    ah_results = []
    for r in finished:
        ar = r.get("ah_result", "").strip()
        if ar:
            ah_results.append(ar)

    if ah_results:
        wins = sum(1 for ar in ah_results if ar in ("赢", "赢半"))
        losses = sum(1 for ar in ah_results if ar in ("输", "输半"))
        walks = sum(1 for ar in ah_results if ar == "走")
        total_ah = len(ah_results)
        win_rate = wins / total_ah * 100 if total_ah > 0 else 0

        print("  【亚盘投注】")
        print(f"    总投注：{total_ah} 场")
        print(f"    赢　　：{wins} 场")
        print(f"    输　　：{losses} 场")
        print(f"    走盘　：{walks} 场")
        print(f"    命中率：{win_rate:.1f}%")
        print()

    # 方向 vs 结果
    correct = 0
    wrong = 0
    for r in finished:
        direction = r.get("direction", "")
        result = r.get("actual_result", "")
        if direction == "主" and "主胜" in result:
            correct += 1
        elif direction == "客" and "客胜" in result:
            correct += 1
        elif direction == "观望":
            pass  # 观望不算
        elif direction and result:
            wrong += 1

    if correct + wrong > 0:
        dir_rate = correct / (correct + wrong) * 100
        print(f"  【方向准确率】{correct}/{correct + wrong} = {dir_rate:.1f}%")
        print()

    # 按联赛
    league_stats = {}
    for r in finished:
        league = r.get("league", "?")
        if league not in league_stats:
            league_stats[league] = {"total": 0, "wins": 0}
        league_stats[league]["total"] += 1
        ar = r.get("ah_result", "").strip()
        if ar in ("赢", "赢半"):
            league_stats[league]["wins"] += 1

    if league_stats:
        print("  【按联赛命中率】")
        for league, stats in sorted(league_stats.items(), key=lambda x: -x[1]["wins"]/max(x[1]["total"],1)):
            rate = stats["wins"] / stats["total"] * 100 if stats["total"] > 0 else 0
            print(f"    {league:<12} {stats['wins']}/{stats['total']} = {rate:.1f}%")
        print()

    # 按信心度
    stars_stats = {}
    for r in finished:
        stars = r.get("stars", "0")
        if stars not in stars_stats:
            stars_stats[stars] = {"total": 0, "wins": 0}
        stars_stats[stars]["total"] += 1
        ar = r.get("ah_result", "").strip()
        if ar in ("赢", "赢半"):
            stars_stats[stars]["wins"] += 1

    if stars_stats:
        print("  【按信心度命中率】")
        for stars in sorted(stars_stats.keys(), key=float):
            stats = stars_stats[stars]
            rate = stats["wins"] / stats["total"] * 100 if stats["total"] > 0 else 0
            bar = "█" * int(rate / 10) + "░" * (10 - int(rate / 10))
            print(f"    {stars}星  {stats['wins']:>2}/{stats['total']:<2} {bar} {rate:.1f}%")
        print()

    # 按规则触发
    rule_stats = {}
    for r in finished:
        rules_str = r.get("rules_triggered", "")
        if not rules_str:
            continue
        for rule in rules_str.split(", "):
            rule = rule.strip()
            if not rule:
                continue
            if rule not in rule_stats:
                rule_stats[rule] = {"total": 0, "wins": 0}
            rule_stats[rule]["total"] += 1
            ar = r.get("ah_result", "").strip()
            if ar in ("赢", "赢半"):
                rule_stats[rule]["wins"] += 1

    if rule_stats:
        print("  【按规则触发命中率】")
        for rule, stats in sorted(rule_stats.items(), key=lambda x: -x[1]["total"]):
            rate = stats["wins"] / stats["total"] * 100 if stats["total"] > 0 else 0
            print(f"    {rule:<20} {stats['wins']}/{stats['total']} = {rate:.1f}%")
        print()


def show_stats():
    """显示战绩统计"""
    if not os.path.exists(CSV_PATH):
        print("❌ CSV 文件不存在")
        return

    with open(CSV_PATH, "r", encoding="utf-8-sig") as f:
        reader = csv.DictReader(f)
        rows = list(reader)

    total = len(rows)
    if total == 0:
        print("📊 暂无数据")
        return

    # 统计有结果（已赛完）的场次
    finished = [r for r in rows if r.get("actual_result", "").strip()]
    finishing_rate = len(finished) / total * 100 if total > 0 else 0

    print("=" * 60)
    print("  📊 亚盘分析战绩统计")
    print("=" * 60)
    print(f"  总场次：{total}")
    print(f"  已赛完：{len(finished)}（{finishing_rate:.1f}%）")
    print()

    # 按联赛统计
    leagues = Counter(r["league"] for r in rows)
    print("  【按联赛分布】")
    for league, count in leagues.most_common():
        print(f"    {league:<12} {count} 场")
    print()

    # 按信心度统计
    stars_dist = Counter(str(r["stars"]) for r in rows)
    print("  【信心度分布】")
    for s in sorted(stars_dist.keys()):
        print(f"    {s} 星：{stars_dist[s]} 场")
    print()

    if len(finished) > 0:
        # 有结果的可统计命中率
        wins = [r for r in finished if r.get("ah_result", "") in ("赢", "赢半")]
        win_rate = len(wins) / len(finished) * 100
        print(f"  【亚盘命中率】{len(wins)}/{len(finished)} = {win_rate:.1f}%")
        print()


# ============================================================
# 命令行入口
# ============================================================

# 比赛状态（jihai8 matchState）
STATE_LABEL = {
    0: "未开赛", 1: "上半场", 2: "中场", 3: "下半场", 4: "加时",
    5: "加时", 6: "点球", 7: "点球", -1: "完场",
    -10: "延期", -11: "取消", -14: "推迟",
}


def missing_companies(companies):
    """返回 COMPANIES 里缺失的公司名列表（7 家固定，缺一不可）"""
    return [c for c in COMPANIES if c not in companies]


def require_all_companies(companies):
    """校验 7 家公司是否齐备。不齐则打印提示并返回 False。"""
    missing = missing_companies(companies)
    if not missing:
        return True
    have = [c for c in COMPANIES if c in companies]
    print(f"❌ 亚盘公司不齐，缺少 {len(missing)} 家：{'、'.join(missing)}")
    print(f"   已有 {len(have)} 家：{'、'.join(have) if have else '（无）'}")
    print(f"   本引擎要求 7 家固定公司缺一不可：{'、'.join(COMPANIES)}")
    print("   → 已终止分析（缺公司会导致表决阈值失真）")
    return False


def run_scraped(match_arg, save=True):
    """爬取单场亚盘（来自 jihai8）并分析。
    该页面状态感知：赛前=赛前盘，赛中=实时滚球盘，赛后=收盘盘。"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fetch_match_jihai8 import parse_match_id
    import fetch_jihai8 as S

    match_id = parse_match_id(match_arg)
    print(f"📡 抓取比赛 {match_id} 亚盘数据 ...")

    # 1. 亚盘：来自 jihai8（含比赛状态）
    obj = S.fetch_jihai8_asia_all(match_id)
    state = None
    match_time = ""
    match_time_utc = None
    if obj and obj.get("companies"):
        companies = S._map_jihai8_companies(obj["companies"])
        league = obj.get("league", "")
        home, away = obj.get("homeTeam", ""), obj.get("awayTeam", "")
        match_time_utc = obj.get("matchTime")          # jihai8 的 UTC 时间
        date = (match_time_utc or "")[:10]
        state = obj.get("matchState")
    else:
        companies = S.parse_asian(match_id)      # 再试一次 jihai8
        # 比赛信息从 jihai8 欧赔页取
        euro_obj = S.fetch_jihai8_euro_all(match_id)
        if euro_obj:
            league = euro_obj.get("league", "")
            home, away = euro_obj.get("homeTeam", ""), euro_obj.get("awayTeam", "")
            mt = euro_obj.get("matchTime") or ""
            date = mt[:10]
            match_time_utc = mt
            state = euro_obj.get("matchState")
        else:
            league = home = away = date = ""

    # 只保留本引擎的 7 家，并校验是否齐备（缺一不可）
    companies = S.filter_companies(companies, COMPANIES)
    if not require_all_companies(companies):
        return

    data = {
        "id": "R" + match_id,
        "date": date,
        "match_time": match_time,
        "match_time_utc": match_time_utc,
        "league": league,
        "home_team": home,
        "away_team": away,
        "match": f"{home or '?'} vs {away or '?'}",
        "companies": companies,
    }

    # 2. 提示当前是赛前盘还是滚球盘
    label = STATE_LABEL.get(state, f"状态{state}") if state is not None else "未知"
    is_live = state in (1, 2, 3, 4, 5, 6, 7)
    print(f"  比赛状态：{label}" + ("（实时滚球盘）" if is_live else "") + f"，亚盘 {len(companies)} 家")
    print(f"  公司：{'、'.join(companies.keys())}")

    # 3. 分析
    result = analyze_match(data)
    print(generate_report(result))

    if save:
        append_to_csv(result, data)


def run_batch(save=True):
    """批量爬取五大联赛并逐场做亚盘分析（日期/联赛在 fetch_jihai8.py 顶部配置）"""
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    from fetch_jihai8 import scrape_5leagues

    # 亚盘分析不需要 state_factors，也不需要欧赔 → 跳过欧赔可省一半请求
    results = scrape_5leagues(prefill=False, with_euro=False)
    if not results:
        print("无比赛可分析。")
        return

    print(f"\n开始逐场亚盘分析（共 {len(results)} 场）...")
    ok = skip = 0
    for data, path in results:
        print(f"\n{'=' * 60}")
        print(f"{data.get('league')} {data.get('home_team')} vs {data.get('away_team')}")
        print('=' * 60)

        asian_data = {
            "id": data.get("id", ""),
            "date": data.get("date", ""),
            "match_time": data.get("match_time", ""),
            "match_time_utc": data.get("match_time_utc"),
            "league": data.get("league", ""),
            "home_team": data.get("home_team", ""),
            "away_team": data.get("away_team", ""),
            "match": f"{data.get('home_team', '?')} vs {data.get('away_team', '?')}",
            "companies": {k: v for k, v in data.get("asian_companies", {}).items()
                          if k in COMPANIES},
        }
        if not require_all_companies(asian_data["companies"]):
            skip += 1
            continue

        result = analyze_match(asian_data)
        print(generate_report(result))
        if save:
            append_to_csv(result, asian_data)
        ok += 1

    print(f"\n✅ 批量分析完成：成功 {ok} 场，跳过 {skip} 场（7 家公司不齐）")


def main():
    parser = argparse.ArgumentParser(
        description="亚盘专属分析工具 v1.3",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
示例：
  python asian_handicap.py 2929683                              # 爬取+分析（支持滚球）
  python asian_handicap.py https://www.jihai8.com/zq/asia_2929683.html
  python asian_handicap.py --batch                             # 批量爬五大联赛+逐场分析
  python asian_handicap.py --input data/ah_template.json
  python asian_handicap.py --backtest
  python asian_handicap.py --stats
  python asian_handicap.py --template
        """
    )
    parser.add_argument("match", nargs="?", help="比赛 ID 或球探 URL（自动爬取亚盘并分析，赛中即滚球盘）")
    parser.add_argument("--input", "-i", help="JSON 输入文件路径")
    parser.add_argument("--batch", action="store_true", help="批量爬取五大联赛并逐场分析（日期/联赛在 fetch_jihai8.py 顶部配置）")
    parser.add_argument("--backtest", "-b", action="store_true", help="回测模式")
    parser.add_argument("--stats", "-s", action="store_true", help="战绩统计")
    parser.add_argument("--template", "-t", action="store_true", help="打印输入模板")
    parser.add_argument("--no-save", action="store_true", help="不保存到 CSV")

    args = parser.parse_args()

    if args.template:
        print("📋 输入模板：")
        print(f"  模板文件：{TEMPLATE_PATH}")
        if os.path.exists(TEMPLATE_PATH):
            with open(TEMPLATE_PATH, "r", encoding="utf-8") as f:
                print(f.read())
        return

    if args.backtest:
        backtest()
        return

    if args.stats:
        show_stats()
        return

    # 批量：爬取五大联赛 + 逐场亚盘分析
    if args.batch:
        run_batch(save=not args.no_save)
        return

    # 传入比赛 ID/URL → 先爬取（jihai8 优先），再分析
    if args.match:
        run_scraped(args.match, save=not args.no_save)
        return

    if args.input:
        if not os.path.exists(args.input):
            print(f"❌ 文件不存在：{args.input}")
            return

        data = load_json(args.input)
        # 兼容两种字段名：老模板用 companies，爬虫输出用 asian_companies
        if "companies" not in data and "asian_companies" in data:
            data["companies"] = data["asian_companies"]
        if not data.get("match"):
            data["match"] = f"{data.get('home_team', '?')} vs {data.get('away_team', '?')}"
        # 只保留本引擎的 7 家（爬虫输出可能含平博等其它公司）
        data["companies"] = {k: v for k, v in data.get("companies", {}).items() if k in COMPANIES}
        if not require_all_companies(data["companies"]):
            return
        result = analyze_match(data)

        # 输出报告
        print(generate_report(result))

        # 保存到 CSV
        if not args.no_save:
            append_to_csv(result, data)
        return

    # 无参数 → 交互模式
    print("📊 亚盘专属分析工具 v1.3")
    print()
    print("使用方法：")
    print("  python asian_handicap.py <比赛ID或URL>            爬取+分析（赛中即滚球盘）")
    print("  python asian_handicap.py --batch                   批量爬五大联赛+逐场分析")
    print("  python asian_handicap.py --input data/xxx.json    分析已有 JSON")
    print("  python asian_handicap.py --backtest               回测")
    print("  python asian_handicap.py --stats                  战绩统计")
    print("  python asian_handicap.py --template               查看输入模板")
    print()
    print(f"📁 数据存储：{CSV_PATH}")
    print(f"📋 输入模板：{TEMPLATE_PATH}")


if __name__ == "__main__":
    main()
