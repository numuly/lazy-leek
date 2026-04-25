"""
screener.py - A股低估值筛选引擎
重构评分体系：价值分（PB×PE）× 技术分（超跌+横盘+粘合）
识别从高位腰斩后底部横盘、均线纠缠的低估值标的
"""

import os
import sys
import time
from typing import List, Dict, Any, Optional

sys.path.insert(0, os.path.dirname(__file__))
from data_sources import get_tx_quote, get_tx_monthly, sf, sf_kline

# 优先从池管理器加载，失败则用内置默认名单
try:
    from pool_manager import get_watchlist_for_screener
    _DEFAULT_WATCHLIST = None  # 延迟加载
except ImportError:
    _DEFAULT_WATCHLIST = [
        # 科技/高端制造
        ('sz002415', '海康威视',  '科技'),
        ('sh600584', '长电科技',  '科技'),
        ('sh601012', '隆基绿能',  '光伏'),
        ('sz000661', '长春高新',  '生物医药'),
        ('sh600276', '恒瑞医药',  '医药'),
        ('sz300274', '阳光电源',  '光伏'),
        ('sh600031', '三一重工',  '机械'),
        ('sz300760', '迈瑞医疗',  '医疗器械'),
        # 消费
        ('sz000858', '五粮液',    '消费'),
        ('sh600887', '伊利股份',  '消费'),
        # 银行
        ('sh601328', '交通银行',  '银行'),
        ('sh601658', '邮储银行',  '银行'),
        ('sh600015', '华夏银行',  '银行'),
        ('sh600000', '浦发银行',  '银行'),
        ('sh601166', '兴业银行',  '银行'),
        ('sz000001', '平安银行',  '银行'),
        # 地产/基建
        ('sh601668', '中国建筑',  '基建'),
        ('sh600048', '保利发展',  '地产'),
        ('sz000002', '万科A',    '地产'),
        # 能源/材料
        ('sh600028', '中国石化',  '能源'),
        ('sh600019', '宝钢股份',  '钢铁'),
        ('sh600585', '海螺水泥',  '材料'),
    ]


# ─────────────────────────────────────────
# 评分函数
# ─────────────────────────────────────────

def value_score(pb: Optional[float], pe: Optional[float]) -> float:
    """
    价值分：PB权重60%，PE权重40%
    PB越低越好（0.3以下最优），PE越低越好（5以下最优）
    分数范围 0~10
    """
    if pb is None or pe is None or pe <= 0:
        return 0.0

    # PB 评分：0.3以下满分10，0.7以上开始衰减，1.0以上0分
    if pb <= 0.3:
        pb_sc = 10.0
    elif pb <= 0.7:
        pb_sc = max(0, 10 - (pb - 0.3) * 25)  # 0.3→0.7 线性 10→0
    elif pb <= 1.0:
        pb_sc = max(0, 5 - (pb - 0.7) * 10)   # 0.7→1.0 线性 5→0
    else:
        pb_sc = 0.0

    # PE 评分：5以下满分10，20以上开始衰减，50以上0分
    if pe <= 5:
        pe_sc = 10.0
    elif pe <= 20:
        pe_sc = max(0, 10 - (pe - 5) * (5/15))  # 线性 10→5
    elif pe <= 50:
        pe_sc = max(0, 5 - (pe - 20) * (5/30))  # 线性 5→0
    else:
        pe_sc = 0.0

    return pb_sc * 0.6 + pe_sc * 0.4


def tech_score(
    drawdown_from_high: float,
    amplitude_12m: float,
    ma_divergence: float,
) -> float:
    """
    技术分重构：寻找超跌、底部横盘、均线粘合的股票（满分10分）

    - drawdown_from_high: 较历史高点回撤幅度（负数，如 -50.0 表示跌了50%）
    - amplitude_12m:      近12个月的价格振幅（(最高-最低)/最低，百分比）
    - ma_divergence:      月均线发散程度（max(MA)/min(MA)-1，越小越粘合）
    """
    score = 0.0

    # 1. 深度回撤（寻找"被洗盘/挤泡沫"的标的）— 权重 4 分
    if drawdown_from_high < -50.0:
        score += 4.0
    elif drawdown_from_high < -40.0:
        score += 3.0
    elif drawdown_from_high < -30.0:
        score += 2.0
    elif drawdown_from_high < -20.0:
        score += 1.0

    # 2. 长期底部横盘（近12个月振幅极小）— 权重 3 分
    if amplitude_12m <= 0 or amplitude_12m > 500:
        pass  # 数据异常，不加分
    elif amplitude_12m < 20.0:
        score += 3.0
    elif amplitude_12m >= 20.0 and amplitude_12m < 35.0:
        score += 2.0
    elif amplitude_12m >= 35.0 and amplitude_12m < 50.0:
        score += 1.0

    # 3. 均线粘合度（MA5/MA10/MA20 距离极近，准备变盘）— 权重 3 分
    if ma_divergence >= 0 and ma_divergence < 0.05:
        score += 3.0
    elif ma_divergence >= 0.05 and ma_divergence < 0.10:
        score += 2.0
    elif ma_divergence >= 0.10 and ma_divergence < 0.20:
        score += 1.0

    return score


# ─────────────────────────────────────────
# 单只股票分析
# ─────────────────────────────────────────

def analyze_stock(symbol: str, name: str) -> Optional[Dict[str, Any]]:
    """
    对单只股票进行完整分析，返回新评分体系下的指标
    """
    quote = get_tx_quote(symbol)
    if not quote or not quote.get('price'):
        return None

    try:
        monthly = get_tx_monthly(symbol, 120)
    except Exception:
        monthly = []

    price  = quote['price']
    pb     = quote.get('pb')
    pe     = quote.get('pe')
    prev   = quote.get('prev_close')
    chg_pct = ((price - prev) / prev * 100) if prev else 0.0

    # ── 技术指标计算（新体系） ──
    if len(monthly) >= 20:
        closes_all = [sf_kline(k[2]) for k in monthly if sf_kline(k[2]) is not None]
        highs_all  = [sf_kline(k[3]) for k in monthly if sf_kline(k[3]) is not None]

        # 高/低价合法性校验：逐月检查，高<低时交换（前复权极端值可能导致字段异常）
        def _valid_high_low(hi, lo):
            if hi is None or lo is None:
                return None, None
            if hi < lo:
                return lo, hi
            return hi, lo

        lows_12m_raw  = [(sf_kline(k[4]), sf_kline(k[3])) for k in monthly[:12]]
        lows_12m  = []
        highs_12m = []
        for lo, hi in lows_12m_raw:
            v_hi, v_lo = _valid_high_low(hi, lo)
            if v_lo is not None:
                lows_12m.append(v_lo)
            if v_hi is not None:
                highs_12m.append(v_hi)

        if len(closes_all) < 20:
            return _insufficient_data(symbol, name, quote)

        # 均线计算（MA5 / MA10 / MA20）
        closes_5  = closes_all[:5]
        closes_10 = closes_all[:10]
        closes_20 = closes_all[:20]

        ma5  = sum(closes_5)  / 5
        ma10 = sum(closes_10) / 10
        ma20 = sum(closes_20) / 20

        # 均线粘合度：max(MA) / min(MA) - 1
        mas = [ma5, ma10, ma20]
        ma_divergence = (max(mas) - min(mas)) / min(mas) if min(mas) else 999.0

        # 历史高点评撤（近60个月/5年高点）
        period_high = max(highs_all[:60]) if len(highs_all) >= 60 else max(highs_all)
        drawdown_from_high = (price - period_high) / period_high * 100 if period_high else 0.0

        # 近12个月振幅
        if lows_12m and highs_12m:
            lowest_12m  = min(lows_12m)
            highest_12m = max(highs_12m)
            if lowest_12m > 0 and highest_12m >= lowest_12m:
                amplitude_12m = (highest_12m - lowest_12m) / lowest_12m * 100
            else:
                amplitude_12m = 999.0
        else:
            amplitude_12m = 999.0

        # 额外：12个月初始价格（用于计算12个月涨跌幅）
        if len(closes_all) >= 12:
            close_12m_ago = closes_all[11]
        else:
            close_12m_ago = closes_all[-1]
        chg_12m = (price - close_12m_ago) / close_12m_ago * 100 if close_12m_ago else 0.0

    else:
        return _insufficient_data(symbol, name, quote)

    # ── 评分 ──
    v_sc  = value_score(pb, pe)
    t_sc  = tech_score(drawdown_from_high, amplitude_12m, ma_divergence)
    total = v_sc * 0.6 + t_sc * 0.4

    return {
        'symbol':     symbol,
        'name':       name or quote.get('name', ''),
        'code':       quote.get('code', ''),
        'price':      price,
        'prev_close': prev,
        'chg_pct':    round(chg_pct, 2),
        'pb':         pb,
        'pe':         pe,
        'mktcap':     quote.get('mktcap', 0),
        # 新增技术指标
        'drawdown':   round(drawdown_from_high, 2),   # 较历史高点回撤%
        'amplitude':  round(amplitude_12m, 2),         # 近12月振幅%
        'ma_div':     round(ma_divergence, 4),          # 均线粘合度
        'ma5':        round(ma5, 2),
        'ma10':       round(ma10, 2),
        'ma20':       round(ma20, 2),
        'chg_12m':    round(chg_12m, 2),
        # 评分
        'value_score': round(v_sc, 2),
        'tech_score':  round(t_sc, 2),
        'total_score': round(total, 2),
        'monthly_cnt': len(monthly),
    }


def _insufficient_data(symbol: str, name: str, quote: dict) -> dict:
    """数据不足时返回默认值，避免崩溃"""
    return {
        'symbol': symbol, 'name': name or quote.get('name', ''),
        'code': quote.get('code', ''),
        'price': quote.get('price', 0),
        'prev_close': quote.get('prev_close'),
        'chg_pct': 0.0,
        'pb': quote.get('pb'), 'pe': quote.get('pe'),
        'mktcap': quote.get('mktcap', 0),
        'drawdown': 0.0, 'amplitude': 999.0, 'ma_div': 999.0,
        'ma5': 0, 'ma10': 0, 'ma20': 0, 'chg_12m': 0.0,
        'value_score': 0.0, 'tech_score': 0.0,
        'total_score': 0.0, 'monthly_cnt': 0,
    }


# ─────────────────────────────────────────
# 批量筛选
# ─────────────────────────────────────────

def run_screener(
    watchlist: List[tuple] = None,
    top_n: int = 10,
    min_pb: float = 0.0,
    max_pb: float = 99.0,
    max_pe: float = 99.0,
) -> List[Dict[str, Any]]:
    """
    运行筛选器
    """
    if watchlist is None:
        if _DEFAULT_WATCHLIST is not None:
            watchlist = _DEFAULT_WATCHLIST
        else:
            watchlist = get_watchlist_for_screener()

    results = []
    seen = set()
    for sym, name, sector in watchlist:
        if sym in seen:
            continue
        seen.add(sym)
        try:
            r = analyze_stock(sym, name)
            if not r:
                print(f"  ⚠ {sym} 获取数据失败")
                continue
            r['sector'] = sector

            # 基础过滤
            if r['pb'] is not None and (r['pb'] < min_pb or r['pb'] > max_pb):
                continue
            if r['pe'] is not None and r['pe'] > max_pe:
                continue

            results.append(r)
            print(f"  ✓ {name}({sym}) "
                  f"价值={r['value_score']:.1f} "
                  f"技术={r['tech_score']:.1f} "
                  f"回撤={r['drawdown']:.0f}% "
                  f"振幅={r['amplitude']:.0f}% "
                  f"粘合={r['ma_div']:.2f} "
                  f"综合={r['total_score']:.1f}")
        except Exception as e:
            print(f"  ✗ {sym} 分析出错: {e}")
        time.sleep(0.08)

    results.sort(key=lambda x: x['total_score'], reverse=True)
    return results[:top_n]


# ─────────────────────────────────────────
# 信号摘要（新）
# ─────────────────────────────────────────

def get_signal_tag(r: Dict[str, Any]) -> List[str]:
    """基于新指标体系生成信号标签"""
    tags = []
    pb = r.get('pb')
    # 估值信号
    if pb is not None and pb < 0.4:
        tags.append('🟢极低PB')
    elif pb is not None and pb < 0.6:
        tags.append('🟡低估PB')

    # 回撤信号
    dd = r.get('drawdown', 0)
    if dd < -50:
        tags.append('💥腰斩级回撤')
    elif dd < -35:
        tags.append('📉深度回调')

    # 振幅信号（横盘）
    amp = r.get('amplitude', 999)
    if amp < 20:
        tags.append('📊窄幅横盘')
    elif amp < 35:
        tags.append('📐轻度波动')

    # 均线粘合信号
    div = r.get('ma_div', 999)
    if div < 0.05:
        tags.append('🔗均线高度粘合')
    elif div < 0.10:
        tags.append('🔗均线初步粘合')

    # 12个月趋势
    chg = r.get('chg_12m', 0)
    if chg > 0:
        tags.append('📈近12月上涨')
    else:
        tags.append('📉近12月下跌')

    return tags


# ─────────────────────────────────────────
# 测试入口
# ─────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 65)
    print("  A股低估值筛选器 - 海康形态重构版")
    print("=" * 65)

    results = run_screener(top_n=10)
    print(f"\n共筛选出 {len(results)} 只股票\n")

    print(f"{'代码':<8} {'名称':<8} {'PB':>4} {'PE':>5} "
          f"{'回撤':>7} {'振幅':>5} {'粘合':>6} {'综合':>4}  {'信号'}")
    print("-" * 75)
    for r in results:
        tags = ' '.join(get_signal_tag(r))
        print(f"{r['code']:<8} {r['name']:<8} "
              f"{r['pb']:>4.2f} {r['pe']:>5.1f} "
              f"{r['drawdown']:>6.0f}% {r['amplitude']:>5.0f}% "
              f"{r['ma_div']:>6.2f} {r['total_score']:>4.1f}  {tags}")
