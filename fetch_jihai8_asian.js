#!/usr/bin/env node
/**
 * fetch_jihai8_asian.js — jihai8(即嗨体育) 亚盘抓取（赛前 + 滚球通用）
 * ==================================================================
 * 该页面是「状态感知」的：赛前返回赛前盘，赛中返回实时滚球盘，赛后返回收盘盘。
 *
 * 用法: node fetch_jihai8_asian.js <match_id>
 * 输出: JSON 对象 { matchState, homeTeam, awayTeam, league, matchTime,
 *                  homeScore, awayScore, companies: [...] }
 *   matchState: 0=未开始 1=上半场 2=中场 3=下半场 5=加时 ... -1=完场
 */

const id = process.argv[2];
const UA = 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 ' +
           '(KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36';
const HEADERS = { 'User-Agent': UA, 'Referer': 'https://www.jihai8.com/' };

// 带重试的抓取（jihai8 偶发 502/403，重试可恢复）
async function fetchHtml(url, tries = 3) {
  for (let i = 0; i < tries; i++) {
    let r;
    try {
      r = await fetch(url, { headers: HEADERS });
    } catch (e) {
      if (i === tries - 1) throw e;
      await new Promise(s => setTimeout(s, (i + 1) * 2000));
      continue;
    }
    if (r.status === 403 || r.status === 429 || r.status >= 500) {
      const wait = (i + 1) * 2000;
      console.error(`  [重试] HTTP ${r.status}，${wait}ms 后重试（${i + 1}/${tries}）`);
      if (i === tries - 1) { console.error('HTTP ' + r.status); process.exit(1); }
      await new Promise(s => setTimeout(s, wait));
      continue;
    }
    if (!r.ok) { console.error('HTTP ' + r.status); process.exit(1); }
    return r.text();
  }
  console.error('多次重试仍失败'); process.exit(1);
}

async function main() {
  const html = await fetchHtml(`https://www.jihai8.com/zq/asia_${id}.html`);

  const m = html.match(/window\.__NUXT__\s*=\s*(.*?)(?:<\/script>|$)/s);
  if (!m) { console.error('未找到 __NUXT__'); process.exit(1); }

  let result;
  eval(`result = ${m[1].trim()};`);
  const asias = (result.data && result.data[0] && result.data[0].asias) || [];
  const mi = (result.state && result.state.matchinfo) || {};

  const companies = [];
  for (const c of asias) {
    const o = (c.odds || []).find(x => x.show === 1) || (c.odds && c.odds[0]);
    if (!o) continue;
    companies.push({
      companyId: c.companyId,
      goal: o.goal,
      homeOdds: o.homeOdds,
      awayOdds: o.awayOdds,
      firstGoal: o.firstGoal,
      firstHomeOdds: o.firstHomeOdds,
      firstAwayOdds: o.firstAwayOdds,
      updateTime: o.updateTime,
    });
  }

  console.log(JSON.stringify({
    matchState: mi.matchState,
    homeTeam: mi.homeTeam,
    awayTeam: mi.guestTeam,
    league: mi.sclassName,
    matchTime: mi.matchTime,
    homeScore: mi.homeScore,
    awayScore: mi.guestScore,
    companies,
  }));
}

main().catch(e => { console.error(e.message); process.exit(1); });
