"""
data_sources.py - A股数据源模块
数据源：腾讯证券（实时行情+月线）+ 新浪财经（基本面）
"""

import urllib.request
import json
from typing import Optional, List, Dict, Any


# ─────────────────────────────────────────
# 工具函数
# ─────────────────────────────────────────

def sf(value: str) -> Optional[float]:
    """安全转 float，失败返回 None。腾讯接口 0 表示无数据，仅用于实时行情字段。
    注意：不应用于 pct/chg 等可为 0 的字段。"""
    try:
        f = float(value)
        return None if f == 0 else f
    except (TypeError, ValueError):
        return None


def sf_safe(value: str) -> Optional[float]:
    """安全转 float，仅 parse 失败返回 None。0 是合法值（用于涨跌幅/涨跌额）。"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def sf_kline(value: str) -> Optional[float]:
    """安全转 float，仅 None 表示解析失败。
    K 线前复权数据中 0 可能为 API 空值，也可能为合法的极低调整价。
    阈值 0.001：A股最小价格单位 0.01，前复权后低于 0.001 视为无效数据。"""
    try:
        f = float(value)
        return f if f >= 0.001 else None
    except (TypeError, ValueError):
        return None


def sf_pb(value: str) -> Optional[float]:
    """安全转 float（PB专用，0 视为有效值，因破净股 PB 可为 0）"""
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def ua_headers(ref: str = 'https://finance.qq.com') -> Dict[str, str]:
    return {
        'User-Agent': 'Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36',
        'Referer': ref,
    }


# ─────────────────────────────────────────
# 腾讯证券接口（已验证可用）
# ─────────────────────────────────────────

def get_tx_quote(symbol: str) -> Optional[Dict[str, Any]]:
    """
    获取单只股票实时行情（腾讯 qt.gtimg.cn）
    symbol格式: 'sh600519' 或 'sz000001'
    返回字段: name, code, price, prev_close, pe, pb, mktcap(亿), volume(手)
    """
    url = f'https://qt.gtimg.cn/q={symbol}'
    try:
        req = urllib.request.Request(url, headers=ua_headers())
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode('gbk', errors='replace')
    except Exception as e:
        print(f"  ⚠ {symbol} 网络请求失败: {e}")
        return None

    if 'pv_none_match' in raw:
        return None

    fields = raw.split('"')[1].split('~')
    if len(fields) < 50:
        return None

    return {
        'symbol': symbol,
        'name':   fields[1],
        'code':   fields[2],
        'price':  sf(fields[3]),
        'prev_close': sf(fields[4]),
        'open':   sf(fields[5]),
        'volume': sf(fields[6]),   # 成交量（手）
        'pe':     sf(fields[39]),
        'pb':     sf_pb(fields[46]),
        'mktcap': sf(fields[45]),  # 总市值（亿元）
    }


def get_tx_monthly(symbol: str, count: int = 120) -> List[List[str]]:
    """
    获取月线数据（前复权，最多 count 个月）
    symbol格式: 'sh600519'
    返回: [[日期, 开, 收, 高, 低, 量, 涨幅], ...]  按日期降序（最新在前），兼容[:N]取最近N个月
    """
    url = (f'https://web.ifzq.gtimg.cn/appstock/app/fqkline/get'
           f'?_var=kline_m&param={symbol},month,,,{count},qfq&r=0.1')
    try:
        req = urllib.request.Request(url, headers=ua_headers('https://finance.qq.com'))
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode('utf-8')
    except Exception as e:
        print(f"  ⚠ {symbol} 月线请求失败: {e}")
        return []

    data = json.loads(raw.split('=', 1)[1])
    klines = list(data['data'][symbol].values())[0]
    klines.sort(key=lambda k: k[0], reverse=True)
    return klines


# ─────────────────────────────────────────
# 新浪财经接口（已验证可用）


# ─────────────────────────────────────────
# 新浪财经接口
# ─────────────────────────────────────────

def get_sina_market_overview() -> Optional[Dict[str, Any]]:
    """
    获取大盘指数概况（上证/深证/创业板）
    新浪大盘接口字段以逗号分隔，字段顺序: 名称,价格,涨跌额,涨跌幅,成交量,成交额
    """
    url = 'https://hq.sinajs.cn/list=s_sh000001,s_sz399001,s_sz399006'
    try:
        req = urllib.request.Request(url, headers={
            'User-Agent': 'Mozilla/5.0',
            'Referer': 'https://finance.sina.com.cn',
        })
        resp = urllib.request.urlopen(req, timeout=10)
        raw = resp.read().decode('gbk', errors='replace')
    except Exception as e:
        print(f"  ⚠ 大盘数据请求失败: {e}")
        return None

    result = {}
    for line in raw.strip().split('\n'):
        if '=' not in line:
            continue
        key = line.split('=')[0].split('_')[-1]
        fields = line.split('"')[1].split(',')
        if len(fields) < 4:
            continue
        result[key] = {
            'name':   fields[0],
            'price':  sf(fields[1]),
            'chg':    sf_safe(fields[2]),
            'pct':    sf_safe(fields[3]),
            'volume': sf_safe(fields[4]) if len(fields) > 4 else None,
            'amount': sf_safe(fields[5]) if len(fields) > 5 else None,
        }
    return result


# ─────────────────────────────────────────
# 测试入口
# ─────────────────────────────────────────

if __name__ == '__main__':
    print("=" * 55)
    print("  数据源接口测试")
    print("=" * 55)

    # 测试实时行情
    q = get_tx_quote('sh600519')
    print(f"\n[实时行情] 贵州茅台(sh600519)")
    print(f"  名称: {q['name']}")
    print(f"  现价: {q['price']}")
    print(f"  PE:   {q['pe']}")
    print(f"  PB:   {q['pb']}")
    print(f"  市值: {q['mktcap']} 亿")

    # 测试月线
    mk = get_tx_monthly('sh600519', 6)
    print(f"\n[月线] 近6个月")
    for k in mk[:6]:
        print(f"  {k[0]}  收 {k[2]}  最高 {k[3]}  最低 {k[4]}")

    # 测试大盘
    ov = get_sina_market_overview()
    print(f"\n[大盘]")
    for k, v in ov.items():
        print(f"  {v['name']}: {v['price']} ({v['pct']:+.2f}%)")
