"""
reporter.py - 报告生成与持久化模块
输出格式：JSON（数据持久化）+ Markdown（可读报告）
"""

import os
import json
import glob
from datetime import datetime, timedelta
from typing import List, Dict, Any

import sys
sys.path.insert(0, os.path.dirname(__file__))
from screener import run_screener, get_signal_tag


# ─────────────────────────────────────────
# 路径配置
# ─────────────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
OUTPUT_DIR   = os.path.join(PROJECT_ROOT, 'output')
DATA_DIR     = os.path.join(PROJECT_ROOT, 'data')


# ─────────────────────────────────────────
# 报告生成
# ─────────────────────────────────────────

def generate_report(
    results: List[Dict[str, Any]],
    top_n: int = 10,
    core_symbols: set = None,
    output_dir: str = OUTPUT_DIR,
    skip_history: bool = False,
    months: int = 12,
) -> Dict[str, str]:
    """
    生成完整报告（JSON + Markdown）
    results:    全部股票的完整评分列表（已排序）
    top_n:      主表格显示 Top N
    core_symbols: Core 池股票 symbol 集合，用于标记池归属
    """
    if core_symbols is None:
        core_symbols = set()
    os.makedirs(output_dir, exist_ok=True)
    os.makedirs(DATA_DIR, exist_ok=True)

    # 清理 30 天前的旧报告
    _cleanup_old_reports(output_dir, keep_days=30)

    now = datetime.now()
    date_str = now.strftime('%Y%m%d')
    time_str = now.strftime('%Y-%m-%d %H:%M')
    filename_prefix = f"{date_str}"

    # ── 1. JSON 完整数据 ──
    json_path = os.path.join(output_dir, f"{filename_prefix}_report.json")
    report_data = {
        'generated_at': time_str,
        'total_stocks': len(results),
        'stocks': results,
    }
    with open(json_path, 'w', encoding='utf-8') as f:
        json.dump(report_data, f, ensure_ascii=False, indent=2)

    # ── 2. 追加到历史数据 ──
    if not skip_history:
        history_path = os.path.join(DATA_DIR, 'history.json')
        history = []
        if os.path.exists(history_path):
            with open(history_path, 'r', encoding='utf-8') as f:
                history = json.load(f)
        history.append({
            'date': date_str,
            'time': time_str,
            'total': len(results),
            'top5': results[:5],
        })
        # 只保留最近 90 天
        history = history[-90:]
        with open(history_path, 'w', encoding='utf-8') as f:
            json.dump(history, f, ensure_ascii=False, indent=2)

    # ── 3. Markdown 可读报告 ──
    md_path = os.path.join(output_dir, f"{filename_prefix}_report.md")
    _render_markdown(md_path, results, top_n, core_symbols, time_str, months)

    return {
        'json_path': json_path,
        'md_path': md_path,
    }


def _cleanup_old_reports(output_dir: str, keep_days: int = 30):
    """删除 output/ 目录下超过 keep_days 天的旧报告"""
    cutoff = datetime.now() - timedelta(days=keep_days)
    for pattern in ['*_report.json', '*_report.md']:
        for path in glob.glob(os.path.join(output_dir, pattern)):
            try:
                mtime = datetime.fromtimestamp(os.path.getmtime(path))
                if mtime < cutoff:
                    os.remove(path)
            except OSError:
                pass


def _render_markdown(path: str, results: List[Dict[str, Any]], top_n: int,
                     core_symbols: set, time_str: str, months: int = 12):
    """渲染 Markdown 报告"""
    m = months
    core_set = core_symbols or set()
    with open(path, 'w', encoding='utf-8') as f:
        f.write(f"# 📊 A股低估值精选报告\n\n")
        f.write(f"> 生成时间：{time_str}\n\n")

        # 大盘概览
        f.write("## 📈 大盘概况\n\n")
        try:
            from data_sources import get_sina_market_overview
            ov = get_sina_market_overview()
            if ov:
                for k, v in ov.items():
                    pct = v.get('pct', 0) or 0
                    price = v.get('price') or 0
                    arrow = '▲' if pct > 0 else '▼' if pct < 0 else '─'
                    f.write(f"- **{v['name']}**: {price:.2f}  {arrow} {pct:+.2f}%\n")
            else:
                f.write("- （大盘数据暂不可用）\n")
        except Exception:
            f.write("- （大盘数据暂不可用）\n")
        f.write("\n")

        # Top N 表格
        top = results[:top_n]
        f.write(f"## 🏆 低估值 Top {len(top)}\n\n")
        f.write(f"| 排名 | 池 | 代码 | 名称 | 现价 | PB | PE | 回撤 | {m}月振幅 | 粘合度 | 综合分 | 信号 |\n")
        f.write(f"|------|-----|------|------|------|----|----|------|------|--------|--------|------|\n")
        for i, r in enumerate(top, 1):
            tags = ' '.join(get_signal_tag(r))
            dd   = f"{r.get('drawdown', 0):.0f}%"
            amp  = f"{r.get('amplitude', 0):.0f}%"
            div  = f"{r.get('ma_div', 0):.2f}"
            pool = 'Core' if r['symbol'] in core_set else 'Radar'
            f.write(f"| {i} | {pool} | {r['code']} | {r['name']} | {r.get('price', 0):.2f} | "
                    f"{r.get('pb') or 0:.2f} | {r.get('pe') or 0:.1f} | {dd} | {amp} | {div} | "
                    f"**{r['total_score']:.1f}** | {tags} |\n")

        # Top 3 详情
        f.write("\n## 🔍 Top 3 详细分析\n\n")
        for i, r in enumerate(top[:3], 1):
            f.write(f"### {i}. {r['name']}（{r['code']}）\n\n")
            f.write(f"- **现价**: {r.get('price', 0):.2f}元\n")
            f.write(f"- **PB**: {r.get('pb') or 0:.2f}  |  **PE**: {r.get('pe') or 0:.1f}\n")
            f.write(f"- **市值**: {r.get('mktcap', 0):.0f}亿元\n")
            f.write(f"- **MA5**: {r.get('ma5', 0):.2f}  |  **MA10**: {r.get('ma10', 0):.2f}  |  **MA20**: {r.get('ma20', 0):.2f}\n")
            f.write(f"- **较历史高点回撤**: {r.get('drawdown', 0):+.1f}%\n")
            f.write(f"- **近{m}月振幅**: {r.get('amplitude', 0):+.1f}%\n")
            f.write(f"- **均线粘合度**: {r.get('ma_div', 0):.2%}\n")
            f.write(f"- **近{m}月涨跌**: {r.get('chg_Nm', 0):+.1f}%\n")
            f.write(f"- **价值分**: {r['value_score']:.1f}  |  **技术分**: {r['tech_score']:.1f}  |  **综合分**: {r['total_score']:.1f}\n")
            tags = ' '.join(get_signal_tag(r))
            f.write(f"- **信号**: {tags}\n\n")

        # 全池跟踪表
        f.write("## 📋 双池全量跟踪\n\n")
        f.write(f"| # | 池 | 代码 | 名称 | 现价 | PB | PE | 回撤 | 振幅 | 粘合 | 评分 |\n")
        f.write(f"|---|-----|------|------|-----|----|----|------|------|------|------|\n")
        for i, r in enumerate(results, 1):
            pool = 'Core' if r['symbol'] in core_set else 'Radar'
            f.write(f"| {i} | {pool} | {r['code']} | {r['name']} | "
                    f"{r.get('price', 0):.2f} | {r.get('pb') or 0:.2f} | {r.get('pe') or 0:.1f} | "
                    f"{r.get('drawdown', 0):.0f}% | {r.get('amplitude', 0):.0f}% | "
                    f"{r.get('ma_div', 0):.2f} | **{r['total_score']:.1f}** |\n")

        # 风险提示
        f.write("---\n\n")
        f.write("> ⚠️ *本报告仅供参考，不构成投资建议。股票投资有风险，入市需谨慎。*\n")


# ─────────────────────────────────────────
# 主程序入口
# ─────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 55)
    print("  报告生成测试")
    print("=" * 55)

    print("\n正在运行筛选器...")
    results = run_screener(top_n=999)
    print(f"\n筛选完成，共 {len(results)} 只股票")

    print("\n正在生成报告...")
    paths = generate_report(results, top_n=10)
    print(f"\n✓ JSON 报告: {paths['json_path']}")
    print(f"✓ Markdown 报告: {paths['md_path']}")
