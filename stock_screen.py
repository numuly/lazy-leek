#!/usr/bin/env python3
"""
stock_screen.py - A股低估值筛选系统 统一入口
用法:
  python3 stock_screen.py [--top N] [--no-history]        # 日常筛选
  python3 stock_screen.py --maintain                       # 周度池维护
"""

import os
import sys
import argparse

PROJECT_ROOT = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(PROJECT_ROOT, 'modules'))

from modules.screener import run_screener
from modules.reporter import generate_report
from modules.pool_manager import (
    get_full_market_symbols,
    run_cleanup_cycle,
    load_pool,
    record_scores,
    get_watchlist_for_screener,
    clean_stale_history,
)


def daily_run(args):
    """日常筛选：Core + Radar 池全量评分 → 报告"""
    print("=" * 60)
    print("  A股低估值筛选系统")
    print("=" * 60)

    print("\n[1/2] 全量评分...")
    all_results = run_screener(top_n=999, months=args.months)
    print(f"  → 评分 {len(all_results)} 只股票")
    record_scores(all_results)
    clean_stale_history()

    core_symbols = {s['symbol'] for s in load_pool('core')}

    print("\n[2/2] 生成报告...")
    paths = generate_report(all_results, top_n=args.top, core_symbols=core_symbols,
                             skip_history=args.no_history, months=args.months)
    print(f"  → JSON: {paths['json_path']}")
    print(f"  → Markdown: {paths['md_path']}")

    print("\nOK")


def maintain_pools(args):
    """周度维护：全市场扫描 → 补入 Radar → 晋升 Core → 淘汰"""
    print("=" * 60)
    print("  池维护模式 — 全市场扫描 + 淘汰 + 补入 + 晋升")
    print("=" * 60)

    core  = load_pool('core')
    radar = load_pool('radar')
    print(f"\n维护前: Core={len(core)}只  Radar={len(radar)}只")

    # 1. 全市场扫描（HS300 + ZZ500）
    print("\n[1/3] 全市场扫描（HS300 + ZZ500）...")
    symbols = get_full_market_symbols()
    print(f"  候选范围: {len(symbols)} 只")

    known = {}
    for pool_name in ['core', 'radar']:
        for s in load_pool(pool_name):
            known[s['symbol']] = (s['name'], s.get('sector', '其他'))

    watchlist = [(sym, *known.get(sym, ('', '其他'))) for sym in symbols]
    candidates = run_screener(watchlist=watchlist, top_n=30, months=args.months)

    # 2. 剔除无数据的股票
    valid_candidates = [c for c in candidates if c.get('total_score', 0) > 0]
    print(f"  有效候选: {len(valid_candidates)} 只")

    if valid_candidates:
        record_scores(valid_candidates)

    if not valid_candidates:
        print("  → 无有效候选，跳过补入")
        return

    print(f"\n  全市场 Top 5:")
    for i, c in enumerate(valid_candidates[:5], 1):
        print(f"    {i}. {c['name']}({c['code']}) 综合={c['total_score']:.1f}")

    # 3. 执行维护周期（淘汰 + 补入 + 晋升）
    print("\n[2/3] 执行维护周期...")
    result = run_cleanup_cycle(candidates=valid_candidates, months=args.months)

    for action, symbols in result.items():
        if symbols:
            print(f"  {action}: {symbols}")

    core  = load_pool('core')
    radar = load_pool('radar')
    print(f"\n[3/3] 维护后: Core={len(core)}只  Radar={len(radar)}只")

    if core:
        print("  Core:")
        for s in core:
            print(f"    {s['symbol']} {s['name']} ({s['sector']})")

    print(f"\n  淘汰={result.get('remove_completely', [])}")
    print(f"  降级={result.get('demote_to_radar', [])}")
    print(f"  晋升={result.get('promote_to_core', [])}")
    print(f"  补入Radar={result.get('feed_radar', 0)}只")
    trimmed = result.get('trimmed', [])
    if trimmed:
        print(f"  Radar超限裁剪={trimmed}")

    print("\nOK")


def main():
    parser = argparse.ArgumentParser(description='A股低估值筛选系统')
    parser.add_argument('--top', type=int, default=10, help='返回 Top N（默认10）')
    parser.add_argument('--months', type=int, default=12, choices=range(1, 121),
                        help='评测周期（月），1~120，默认12。控制振幅窗口和涨跌幅计算')
    parser.add_argument('--no-history', action='store_true', help='跳过历史记录')
    parser.add_argument('--maintain', action='store_true', help='周度池维护模式（扫描全市场+淘汰+补入+晋升）')
    args = parser.parse_args()

    if args.maintain:
        maintain_pools(args)
    else:
        daily_run(args)


if __name__ == '__main__':
    main()
