"""
pool_manager.py - 三梯队池管理系统
Core (<20) / Radar (<50) / Inlet (动态补充)
自动淘汰 + 行业平衡 + 全市场扫描（可选）
"""

import os
import json
from datetime import datetime
from typing import List, Dict, Any

# ─────────────────────────────────────────
# 路径配置
# ─────────────────────────────────────────

PROJECT_ROOT = os.path.dirname(os.path.dirname(__file__))
POOL_DIR = os.path.join(PROJECT_ROOT, 'data', 'pools')
POOL_FILES = {
    'core':   os.path.join(POOL_DIR, 'core_pool.json'),
    'radar':  os.path.join(POOL_DIR, 'radar_pool.json'),
    'history': os.path.join(POOL_DIR, 'score_history.json'),
}


# ─────────────────────────────────────────
# 行业分类（细分到二级）
# ─────────────────────────────────────────

# 全局行业上限（Core 池中单一父行业不超过 25%）
GLOBAL_SECTOR_CAP = 0.25


# ─────────────────────────────────────────
# 初始化 / 加载 / 保存
# ─────────────────────────────────────────

def _ensure_dir():
    os.makedirs(POOL_DIR, exist_ok=True)


def load_pool(pool_name: str) -> List[Dict[str, str]]:
    """加载指定池，返回 [(symbol, name, sector), ...]"""
    _ensure_dir()
    path = POOL_FILES.get(pool_name)
    if not path or not os.path.exists(path):
        return []
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_pool(pool_name: str, stocks: List[Dict[str, str]]):
    """保存池到文件"""
    _ensure_dir()
    path = POOL_FILES.get(pool_name)
    if not path:
        return
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(stocks, f, ensure_ascii=False, indent=2)


# ─────────────────────────────────────────
# 行业平衡检测
# ─────────────────────────────────────────

def enforce_sector_cap(
    candidates: List[Dict[str, str]],
    current_pool: List[Dict[str, str]],
    max_ratio: float = GLOBAL_SECTOR_CAP,
) -> List[Dict[str, str]]:
    """
    对候选人执行行业上限裁剪
    如果某行业在 (候选+当前池) 中超过 max_ratio，踢出该行业的候选人
    """
    # 合并计数
    sector_count: Dict[str, int] = {}
    for s in current_pool:
        p = s.get('parent_sector', s.get('sector', '其他'))
        sector_count[p] = sector_count.get(p, 0) + 1

    total = len(current_pool) + len(candidates)
    max_count = int(total * max_ratio)

    filtered = []
    for c in candidates:
        p = c.get('parent_sector', c.get('sector', '其他'))
        if sector_count.get(p, 0) < max_count:
            filtered.append(c)
            sector_count[p] = sector_count.get(p, 0) + 1
    return filtered


# ─────────────────────────────────────────
# 淘汰机制
# ─────────────────────────────────────────

def load_score_history() -> Dict[str, List[Dict]]:
    """加载历史评分记录 {symbol: [{date, score, drawdown, ma_div}, ...]}"""
    _ensure_dir()
    path = POOL_FILES['history']
    if not os.path.exists(path):
        return {}
    with open(path, 'r', encoding='utf-8') as f:
        return json.load(f)


def save_score_history(history: Dict[str, List[Dict]]):
    _ensure_dir()
    path = POOL_FILES['history']
    with open(path, 'w', encoding='utf-8') as f:
        json.dump(history, f, ensure_ascii=False, indent=2)


def record_scores(results: List[Dict[str, Any]]):
    """
    记录当日评分到历史，供淘汰逻辑使用
    """
    history = load_score_history()
    today = datetime.now().strftime('%Y%m%d')

    for r in results:
        sym = r.get('symbol')
        if not sym:
            continue
        entry = {
            'date':       today,
            'total_score': r.get('total_score', 0),
            'tech_score':  r.get('tech_score', 0),
            'value_score': r.get('value_score', 0),
            'drawdown':   r.get('drawdown', 0),
            'amplitude':  r.get('amplitude', 999),
            'ma_div':     r.get('ma_div', 999),
            'months':     r.get('months', 12),
        }
        if sym not in history:
            history[sym] = []
        # 只保留最近 90 天
        history[sym].append(entry)
        history[sym] = history[sym][-90:]

    save_score_history(history)


def clean_stale_history():
    """删除不在 Core/Radar 任何池中的僵尸股票评分历史"""
    _ensure_dir()
    history = load_score_history()
    if not history:
        return 0

    core  = {s['symbol'] for s in load_pool('core')}
    radar = {s['symbol'] for s in load_pool('radar')}
    active = core | radar

    stale = [sym for sym in history if sym not in active]
    for sym in stale:
        del history[sym]

    if stale:
        save_score_history(history)
    return len(stale)


def get_bottom_stagnant(symbols: List[str], lookback_days: int = 90, months: int = 12) -> List[str]:
    """
    淘汰条件A：连续 30 天(记录日) total_score < 5.0 → 淘汰
    在 lookback_days 窗口内，检查与当前 months 同配置的最长连续低分记录
    """
    history = load_score_history()
    cutoff = datetime.now().timestamp() - lookback_days * 24 * 3600

    stagnant = []
    for sym in symbols:
        entries = history.get(sym, [])
        if len(entries) < 30:
            continue

        recent = sorted(
            [e for e in entries
             if datetime.strptime(e['date'], '%Y%m%d').timestamp() > cutoff
             and e.get('months', 12) == months],
            key=lambda e: e['date']
        )

        if not recent:
            continue

        max_streak = cur_streak = 0
        for e in recent:
            if e.get('total_score', 0) < 5.0:
                cur_streak += 1
                if cur_streak > max_streak:
                    max_streak = cur_streak
            else:
                cur_streak = 0

        if max_streak >= 30:
            stagnant.append(sym)

    return stagnant


def get_broken_out(symbols: List[str], drawup_threshold: float = -10.0, months: int = 12) -> List[str]:
    """
    淘汰条件B：股票已涨离底部（回撤收窄到阈值以内），不再属于"超跌横盘"形态
    返回应移入 Radar 的 symbol 列表
    只统计与当前 months 同配置的条目
    """
    history = load_score_history()

    broken = []
    for sym in symbols:
        entries = history.get(sym, [])
        if not entries:
            continue
        same_period = [e for e in entries if e.get('months', 12) == months]
        recent = same_period[-5:] if len(same_period) >= 5 else same_period
        if not recent:
            continue
        if all(e.get('drawdown', -999.0) > drawup_threshold for e in recent):
            broken.append(sym)
    return broken


# ─────────────────────────────────────────
# 漏斗晋升逻辑
# ─────────────────────────────────────────

def feed_radar(candidates: List[Dict[str, Any]], limit: int = 5, max_radar: int = 50) -> int:
    """从全市场扫描结果中取 top N 补入 Radar（跳过已在 Core/Radar 的，不超过 max_radar）"""
    _ensure_dir()
    core_pool = load_pool('core')
    radar_pool = load_pool('radar')
    core_symbols = {s['symbol'] for s in core_pool}
    radar_symbols = {s['symbol'] for s in radar_pool}

    available_slots = max(0, max_radar - len(radar_pool))
    if available_slots == 0:
        return 0

    new_entries = [
        c for c in candidates
        if c.get('symbol') not in core_symbols
        and c.get('symbol') not in radar_symbols
    ]
    new_entries.sort(key=lambda x: x.get('total_score', 0), reverse=True)

    added = 0
    for c in new_entries[:min(limit, available_slots)]:
        radar_pool.append({
            'symbol': c.get('symbol', ''),
            'name': c.get('name') or '',
            'sector': c.get('sector') or '其他',
            'parent_sector': c.get('sector') or '其他',
        })
        added += 1

    save_pool('radar', radar_pool)
    return added


def trim_radar(max_radar: int = 50) -> List[str]:
    """当 Radar 超出上限时，移除评分最低的股票（优先保留有评分历史的）"""
    _ensure_dir()
    radar_pool = load_pool('radar')
    if len(radar_pool) <= max_radar:
        return []

    history = load_score_history()

    def get_latest_score(sym: str) -> float:
        entries = history.get(sym, [])
        return entries[-1].get('total_score', 0) if entries else 0.0

    # 评分高的在前，同等分数有新历史的优先
    radar_pool.sort(key=lambda s: (-get_latest_score(s['symbol']),
                                   -(1 if history.get(s['symbol']) else 0)))

    trimmed = radar_pool[max_radar:]
    radar_pool = radar_pool[:max_radar]
    save_pool('radar', radar_pool)
    return [s['symbol'] for s in trimmed]


def promote_radar_to_core(max_promote: int = 3, max_core: int = 20) -> List[str]:
    """从 Radar 池中取评分最高的晋升 Core（遵守行业上限）"""
    _ensure_dir()
    history = load_score_history()
    core_pool = load_pool('core')
    radar_pool = load_pool('radar')

    slots = max_core - len(core_pool)
    if slots <= 0:
        return []

    radar_scores = []
    for s in radar_pool:
        entries = history.get(s['symbol'], [])
        if entries:
            latest = entries[-1]
            radar_scores.append({
                **s,
                'total_score': latest.get('total_score', 0),
                'tech_score': latest.get('tech_score', 0),
                'value_score': latest.get('value_score', 0),
            })

    if not radar_scores:
        return []

    radar_scores.sort(key=lambda x: x.get('total_score', 0), reverse=True)

    candidates_dict = [
        {'symbol': s['symbol'], 'name': s['name'],
         'sector': s.get('sector', '其他'),
         'parent_sector': s.get('parent_sector', s.get('sector', '其他'))}
        for s in radar_scores
    ]
    filtered = enforce_sector_cap(candidates_dict, core_pool)

    promote_count = min(len(filtered), slots, max_promote)
    promoted = [c['symbol'] for c in filtered[:promote_count]]
    promoted_set = set(promoted)

    for sym in promoted:
        entry = next((s for s in radar_pool if s['symbol'] == sym), None)
        if entry:
            core_pool.append(entry)
    radar_pool = [s for s in radar_pool if s['symbol'] not in promoted_set]

    save_pool('core', core_pool)
    save_pool('radar', radar_pool)
    return promoted


def run_cleanup_cycle(
    max_core: int = 20,
    drawup_threshold: float = -10.0,
    candidates: List[Dict[str, Any]] = None,
    months: int = 12,
) -> Dict[str, List[str]]:
    """
    执行完整维护周期（每周调用一次）
    - 淘汰：Core 降 Radar、Radar 长期垫底删除
    - 补入：从全市场扫描结果取 top N 补入 Radar
    - 晋升：高分 Radar 晋升 Core
    candidates: 全市场扫描结果（不传则只做淘汰）
    months: 与当前 --months 保持一致，避免不同评测周期的评分交叉污染
    返回 {action: [symbols]}
    """
    core_pool = load_pool('core')
    radar_pool = load_pool('radar')
    core_symbols = [s['symbol'] for s in core_pool]
    radar_symbols = [s['symbol'] for s in radar_pool]

    result = {
        'promote_to_core':  [],
        'demote_to_radar':  [],
        'remove_completely': [],
        'feed_radar':       0,
        'trimmed':          [],
    }

    # 1. 已脱离底部的 Core 成员 → 降入 Radar
    broken = get_broken_out(core_symbols, drawup_threshold, months)
    if broken:
        result['demote_to_radar'].extend(broken)
        demoted = [s for s in core_pool if s['symbol'] in broken]
        core_pool = [s for s in core_pool if s['symbol'] not in broken]
        radar_pool = radar_pool + demoted
        seen = set()
        unique_radar = []
        for s in radar_pool:
            if s['symbol'] not in seen:
                seen.add(s['symbol'])
                unique_radar.append(s)
        radar_pool = unique_radar

    # 2. 长期底部震荡的 Radar 成员 → 淘汰
    radar_symbols = [s['symbol'] for s in radar_pool]
    stagnant_radar = get_bottom_stagnant(radar_symbols, months=months)
    if stagnant_radar:
        result['remove_completely'].extend(stagnant_radar)
        radar_pool = [s for s in radar_pool if s['symbol'] not in stagnant_radar]

    save_pool('core', core_pool)
    save_pool('radar', radar_pool)

    # 3. 全市场扫描补入 Radar（仅在提供 candidates 时执行）
    if candidates:
        result['feed_radar'] = feed_radar(candidates)
        promoted = promote_radar_to_core()
        result['promote_to_core'].extend(promoted)
        result['trimmed'] = trim_radar()
        clean_stale_history()

    return result


# ─────────────────────────────────────────
# 全市场扫描（沪深300 / 中证500）
# ─────────────────────────────────────────

def get_hs300_symbols() -> List[str]:
    """获取沪深300成分股（静态列表，约300只）"""
    HS300_SAMPLE = [
        'sh600519','sh601318','sh600036','sh600276','sh601166','sh600016',
        'sh601328','sh601398','sh601939','sh600000','sh601288','sh601012',
        'sh600585','sh601668','sh601186','sh601117','sh600048','sh601077',
        'sh601825','sh600015','sh601998','sh600031','sh600028','sh600019',
        'sh601169','sh600926','sh600050','sh601688','sh600690',
        'sh600809','sz000858','sh600887','sh601211','sh600030','sh601888',
        'sh600309','sh601857','sh600547','sh600588','sh600570',
        'sh601360','sh600893','sh600150','sh601800','sh600170','sh600406',
        'sh603259','sh600521','sh600760','sh601225','sz002415','sz002594',
        'sz002714','sz300760','sz300059','sz300122','sz300274','sz002475',
        'sz300015','sz002371','sz300124','sz300496','sz300037','sz002456',
        'sz300033','sz002049','sz300408','sz002410','sz002230','sz300223',
        'sh688041','sh688012','sh688036','sh688111','sh688981','sh688599',
    ]
    return list(set(HS300_SAMPLE))


def get_zz500_symbols() -> List[str]:
    """获取中证500成分股（静态演示列表，约500只）"""
    ZZ500_SAMPLE = [
        'sh600322','sh600330','sh600406','sh600416','sh600452','sh600487',
        'sh600498','sh600522','sh600525','sh600535','sh600570','sh600588',
        'sh600622','sh600673','sh600685','sh600703','sh600729','sh600763',
        'sh600779','sh600801','sh600823','sh600837','sh600873','sh600881',
        'sh600887','sh600893','sh600901','sh600909','sh600926','sh600970',
        'sh600989','sh600998','sh600100','sh601001','sh601006','sh601009',
        'sh601038','sh601066','sh601108','sh601138','sh601155','sh601168',
        'sh601186','sh601198','sh601211','sh601216','sh601225','sh601236',
        'sh601288','sh601311','sh601319','sh601336','sh601360','sh601390',
        'sh601601','sh601618','sh601628','sh601658','sh601669','sh601688',
        'sh601698','sh601728','sh601766','sh601788','sh601799','sh601816',
        'sh601838','sh601878','sh601881','sh601888','sh601899','sh601901',
        'sh601919','sh601939','sh601985','sh601988','sh601989','sh601998',
        'sh603160','sh603259','sh603288','sh603501','sh603799','sh603986',
        'sz000001','sz000002','sz000063','sz000066','sz000100','sz000150',
        'sz000166','sz000333','sz000338','sz000425','sz000501','sz000538',
        'sz000568','sz000596','sz000651','sz000661','sz000708','sz000709',
        'sz000768','sz000858','sz000876','sz000877','sz000895','sz000938',
        'sz000951','sz000959','sz001965','sz002007','sz002008','sz002027',
        'sz002044','sz002049','sz002120','sz002142','sz002153','sz002179',
        'sz002202','sz002230','sz002236','sz002252','sz002304','sz002311',
        'sz002371','sz002385','sz002410','sz002415','sz002422','sz002430',
        'sz002460','sz002475','sz002493','sz002555','sz002594','sz002601',
        'sz002624','sz002673','sz002714','sz002736','sz002739','sz002773',
        'sz002821','sz002827','sz002836','sz002841','sz002867','sz002916',
        'sz002920','sz002925','sz002938','sz002939','sz002945','sz002968',
        'sz002992','sz003816','sz300003','sz300015','sz300033','sz300059',
        'sz300122','sz300124','sz300142','sz300223','sz300274','sz300347',
        'sz300408','sz300496','sz300529','sz300595','sz300601','sz300628',
        'sz300662','sz300750','sz300760','sz300896','sz300957','sz300982',
    ]
    return list(set(ZZ500_SAMPLE))


def get_full_market_symbols() -> List[str]:
    """获取全市场候选（沪深300 + 中证500 去重）"""
    hs = set(get_hs300_symbols())
    zz = set(get_zz500_symbols())
    return list(hs | zz)


# ─────────────────────────────────────────
# 初始化默认 Core 池
# ─────────────────────────────────────────

def init_default_core_pool():
    """首次初始化默认 Core 池（不多于 20 只）"""
    _ensure_dir()
    if os.path.exists(POOL_FILES['core']) and load_pool('core'):
        return  # 已有池，不覆盖

    DEFAULT_CORE = [
        {'symbol': 'sh601328', 'name': '交通银行',  'sector': '银行',  'parent_sector': '银行'},
        {'symbol': 'sh600015', 'name': '华夏银行',  'sector': '银行',  'parent_sector': '银行'},
        {'symbol': 'sz000001', 'name': '平安银行',  'sector': '银行',  'parent_sector': '银行'},
        {'symbol': 'sh601668', 'name': '中国建筑',  'sector': '基建',  'parent_sector': '基建'},
        {'symbol': 'sh600048', 'name': '保利发展',  'sector': '地产',  'parent_sector': '地产'},
        {'symbol': 'sz000002', 'name': '万科A',    'sector': '地产',  'parent_sector': '地产'},
        {'symbol': 'sz002415', 'name': '海康威视',  'sector': '科技',  'parent_sector': '科技'},
        {'symbol': 'sh600584', 'name': '长电科技',  'sector': '半导体','parent_sector': '科技'},
        {'symbol': 'sh601012', 'name': '隆基绿能',  'sector': '光伏',  'parent_sector': '能源'},
        {'symbol': 'sz300274', 'name': '阳光电源',  'sector': '光伏',  'parent_sector': '能源'},
        {'symbol': 'sh600031', 'name': '三一重工',  'sector': '机械',  'parent_sector': '制造'},
        {'symbol': 'sh600276', 'name': '恒瑞医药',  'sector': '医药',  'parent_sector': '医药'},
        {'symbol': 'sz300760', 'name': '迈瑞医疗',  'sector': '医疗器械','parent_sector': '医药'},
        {'symbol': 'sz000858', 'name': '五粮液',   'sector': '白酒',  'parent_sector': '消费'},
        {'symbol': 'sh600887', 'name': '伊利股份',  'sector': '食品',  'parent_sector': '消费'},
        {'symbol': 'sh600585', 'name': '海螺水泥',  'sector': '水泥',  'parent_sector': '材料'},
        {'symbol': 'sh600019', 'name': '宝钢股份',  'sector': '钢铁',  'parent_sector': '材料'},
        {'symbol': 'sh600028', 'name': '中国石化',  'sector': '石化',  'parent_sector': '能源'},
        {'symbol': 'sh600547', 'name': '山东黄金',  'sector': '黄金',  'parent_sector': '材料'},
        {'symbol': 'sz000661', 'name': '长春高新',  'sector': '生物制药','parent_sector': '医药'},
    ]
    save_pool('core', DEFAULT_CORE)


def init_default_radar_pool():
    """初始化 Radar 池（观察标的）"""
    _ensure_dir()
    if os.path.exists(POOL_FILES['radar']) and load_pool('radar'):
        return

    DEFAULT_RADAR = [
        {'symbol': 'sh601658', 'name': '邮储银行',   'sector': '银行',  'parent_sector': '银行'},
        {'symbol': 'sh601998', 'name': '中信银行',   'sector': '银行',  'parent_sector': '银行'},
        {'symbol': 'sh601166', 'name': '兴业银行',   'sector': '银行',  'parent_sector': '银行'},
        {'symbol': 'sh600000', 'name': '浦发银行',   'sector': '银行',  'parent_sector': '银行'},
        {'symbol': 'sh601077', 'name': '渝农商行',   'sector': '银行',  'parent_sector': '银行'},
        {'symbol': 'sh601825', 'name': '沪农商行',   'sector': '银行',  'parent_sector': '银行'},
        {'symbol': 'sh601186', 'name': '中国铁建',   'sector': '基建',  'parent_sector': '基建'},
        {'symbol': 'sh601669', 'name': '中国电建',   'sector': '基建',  'parent_sector': '基建'},
        {'symbol': 'sh600325', 'name': '华发股份',   'sector': '地产',  'parent_sector': '地产'},
        {'symbol': 'sh601117', 'name': '中国化学',   'sector': '化工',  'parent_sector': '材料'},
        {'symbol': 'sh600522', 'name': '中天科技',   'sector': '通信',  'parent_sector': '科技'},
        {'symbol': 'sz002839', 'name': '张家港行',   'sector': '银行',  'parent_sector': '银行'},
        {'symbol': 'sh600570', 'name': '恒生电子',   'sector': '软件',  'parent_sector': '科技'},
        {'symbol': 'sh600406', 'name': '国电南瑞',   'sector': '电气',  'parent_sector': '制造'},
    ]
    save_pool('radar', DEFAULT_RADAR)


# ─────────────────────────────────────────
# 获取当前周期可用的完整候选列表
# ─────────────────────────────────────────

def get_watchlist_for_screener() -> List[tuple]:
    """
    整合 Core + Radar 池，返回 screener.run_screener 所需格式
    返回: [(symbol, name, sector), ...]
    """
    init_default_core_pool()
    init_default_radar_pool()

    core  = load_pool('core')
    radar = load_pool('radar')

    result = []
    seen = set()

    for s in core + radar:
        sym = s['symbol']
        if sym in seen:
            continue
        seen.add(sym)
        result.append((sym, s['name'], s.get('sector', '其他')))

    return result


# ─────────────────────────────────────────
# 测试入口
# ─────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 55)
    print("  三梯队池管理 - 测试")
    print("=" * 55)

    init_default_core_pool()
    init_default_radar_pool()

    core  = load_pool('core')
    radar = load_pool('radar')

    print(f"\nCore 池: {len(core)} 只")
    for s in core:
        print(f"  {s['symbol']} {s['name']} ({s['sector']})")

    print(f"\nRadar 池: {len(radar)} 只\n")
