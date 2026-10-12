#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""回测6场历史数据，验证截图更新后的命中率"""
import json
import sys
import os

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from asian_handicap import analyze_match, company_direction

data_dir = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
tests = [
    ("R63", "test_r63.json"),
    ("R69", "r69_lugano_basel.json"),
    ("R76", "r76_start_bodo.json"),
    ("R77", "r77_machida_urawa.json"),
    ("R78", "test_r78.json"),
    ("R79", "r79_djurgarden_bromma.json"),
]

results = []

for rid, fname in tests:
    path = os.path.join(data_dir, fname)
    data = json.load(open(path, "r", encoding="utf-8"))
    
    # 计算每家公司方向
    community = {}
    for name in data["companies"]:
        community[name] = company_direction(data["companies"][name])
    
    result = analyze_match(data)
    
    home = data["home_team"]
    away = data["away_team"]
    actual = data.get("ah_result", "?")
    score = data.get("actual_score", "?")
    hcp = result["handicap"]
    direction = result["direction"]
    
    # 拼规则列表
    rules_list = []
    for s in result.get("l1_signals", []):
        if s and s.get("rule"):
            rules_list.append(s["rule"])
    r21 = result.get("l3_rule21", {})
    if r21 and r21.get("triggered"):
        rules_list.append("Rule 21")
    for s in result.get("l4_signals", []):
        if s and s.get("rule"):
            rules_list.append(s["rule"])
    
    # 推荐信息
    ah_rec = result.get("ah_rec", "")
    
    print(f"\n{'='*60}")
    print(f"  {rid}: {home} vs {away} | 盘口={hcp:+.2f} | 比分={score}")
    print(f"  公司方向: 主{list(community.values()).count('主')} vs 客{list(community.values()).count('客')} (分歧{list(community.values()).count('分歧')})")
    print(f"  方向明细: {community}")
    mv = result["majority"]
    print(f"  多数表决: {mv['ratio']} → {mv['direction']} ⭐{mv['stars']}")
    print(f"  触发规则: {', '.join(rules_list) if rules_list else '无'}")
    print(f"  推荐方向: {direction} ⭐{result['stars']}")
    print(f"  双选: {result.get('double_pick', '?')}")
    print(f"  预设ah_result: {actual}")
    
    # 判断是否正确：用比分直接计算
    # 提取比分
    score_parts = score.split("-")
    home_goals = int(score_parts[0]) if len(score_parts) == 2 else 0
    away_goals = int(score_parts[1]) if len(score_parts) == 2 else 0
    
    # 计算亚盘结果
    if hcp > 0:
        # 主队让球：主队实际进球 vs 客队进球+盘口
        adjusted_home = home_goals
        adjusted_away = away_goals + hcp
        if adjusted_home > adjusted_away:
            real_winner = "主"
        elif adjusted_home < adjusted_away:
            real_winner = "客"
        else:
            real_winner = "走"
    elif hcp < 0:
        # 客队让球：客队实际进球 vs 主队进球+|盘口|
        adjusted_away = away_goals
        adjusted_home = home_goals + abs(hcp)
        if adjusted_away > adjusted_home:
            real_winner = "客"
        elif adjusted_away < adjusted_home:
            real_winner = "主"
        else:
            real_winner = "走"
    else:
        # 平手盘
        if home_goals > away_goals:
            real_winner = "主"
        elif away_goals > home_goals:
            real_winner = "客"
        else:
            real_winner = "走"
    
    # 判定
    if direction == "观望":
        correct = "⏸️观望"  # 中性：未下注，不计入命中率
    elif direction == real_winner:
        correct = "✅"
    elif real_winner == "走":
        correct = "⏸️走"
    else:
        correct = "❌"
    
    print(f"  亚盘实际赢家: {real_winner} (比分{home_goals}-{away_goals} 盘口{hcp:+.2f})")
    print(f"  判定: {correct}")
    
    results.append({
        "id": rid,
        "match": f"{home} vs {away}",
        "score": score,
        "handicap": f"{hcp:+.2f}",
        "direction": direction,
        "stars": result["stars"],
        "actual": actual,
        "correct": correct
    })

# 汇总
print(f"\n{'='*60}")
print(f"  📊 回测汇总 (截图真实数据)")
print(f"{'='*60}")
win = sum(1 for r in results if r["correct"] == "✅")
lose = sum(1 for r in results if r["correct"] == "❌")
watch = sum(1 for r in results if r["correct"] == "⏸️观望")
bet_total = win + lose  # 实际下注场次
print(f"  胜: {win} | 负: {lose} | 观望: {watch}")
if bet_total > 0:
    print(f"  命中率(不含观望): {win}/{bet_total} = {win/bet_total*100:.1f}%")
else:
    print(f"  命中率(不含观望): N/A (全部观望)")
print(f"  {'─'*40}")
for r in results:
    print(f"  {r['id']} {r['correct']} {r['match']} {r['score']} | 盘口{r['handicap']} | 推荐{r['direction']} ⭐{r['stars']} | 实际{r['actual']}")
