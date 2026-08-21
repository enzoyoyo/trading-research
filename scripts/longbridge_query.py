#!/usr/bin/env python3
"""Longbridge 行情查询 CLI - 统一入口"""

import os
import sys
import json
import argparse
import subprocess
import shutil
from contextlib import contextmanager
from pathlib import Path
from datetime import datetime


@contextmanager
def suppress_stdout_fd():
    """Keep SDK initialization banners from corrupting --json stdout."""
    try:
        stdout_fd = sys.stdout.fileno()
    except (AttributeError, OSError):
        yield
        return
    saved_fd = os.dup(stdout_fd)
    try:
        sys.stdout.flush()
        with open(os.devnull, 'w', encoding='utf-8') as devnull:
            os.dup2(devnull.fileno(), stdout_fd)
            yield
    finally:
        sys.stdout.flush()
        os.dup2(saved_fd, stdout_fd)
        os.close(saved_fd)

# 加载 Longbridge 凭证（标准化路径）
# 注意：env 文件用 LONGBRIDGE_* 前缀，SDK 期望 LONGPORT_*
env_file = Path(os.path.expanduser('~/.config/longbridge/.env'))
if env_file.exists():
    for line in env_file.read_text().splitlines():
        line = line.strip()
        if '=' in line and not line.startswith('#'):
            k, v = line.split('=', 1)
            k = k.replace('export ', '').strip()
            v = v.strip().strip('"').strip("'")
            os.environ[k] = v
    # 映射 LONGBRIDGE_* → LONGPORT_*（SDK 期望）
    for prefix in ('APP_KEY', 'APP_SECRET', 'ACCESS_TOKEN'):
        src = f'LONGBRIDGE_{prefix}'
        dst = f'LONGPORT_{prefix}'
        if src in os.environ and dst not in os.environ:
            os.environ[dst] = os.environ[src]

try:
    from longport.openapi import Config, QuoteContext, Period, AdjustType
    SDK_AVAILABLE = True
except ModuleNotFoundError:
    Config = QuoteContext = Period = AdjustType = None  # type: ignore[assignment]
    SDK_AVAILABLE = False


def get_ctx():
    """获取 Longbridge QuoteContext；SDK 不可用或连不上时交给 CLI fallback。"""
    if not SDK_AVAILABLE:
        return None
    try:
        config = Config.from_env()
        return QuoteContext(config)
    except Exception:
        return None


def longbridge_bin():
    """Resolve the local LongBridge CLI for SDK-free read-only fallback."""
    candidate = os.environ.get('LONGBRIDGE_BIN') or shutil.which('longbridge')
    if not candidate:
        raise RuntimeError('LongBridge CLI not found; install `longbridge`, add it to PATH, or set LONGBRIDGE_BIN')
    return candidate


def run_cli_json(args, timeout=180):
    """Run LongBridge CLI read-only command and parse JSON output."""
    proc = subprocess.run(
        [longbridge_bin(), *args],
        text=True,
        capture_output=True,
        timeout=timeout,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"longbridge CLI {' '.join(args)} failed rc={proc.returncode}: {proc.stderr[-500:]}")
    try:
        return json.loads(proc.stdout or 'null')
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"longbridge CLI JSON parse failed: {exc}; stdout_tail={proc.stdout[-500:]}") from exc


def normalize_cli_candle(row):
    """Normalize CLI kline rows to this script's candle contract."""
    return {
        'time': str(row.get('time') or row.get('timestamp') or row.get('date')),
        'open': float(row['open']),
        'high': float(row['high']),
        'low': float(row['low']),
        'close': float(row['close']),
        'volume': int(row.get('volume') or 0),
    }


def _to_float(value):
    """Coerce a CLI JSON scalar (often a numeric string, sometimes '' or None) to float."""
    if value is None or value == '':
        return None
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


def normalize_cli_quote(row):
    """Normalize one `longbridge quote --format json` row to the SDK quote contract.

    The CLI's raw field names (last/change_percentage/prev_close, verified
    2026-07-26 via `longbridge quote --format json`) do not match either the
    longport SDK's attribute names or this script's own SDK-path output
    schema (price/change_pct/...). Without this normalization, downstream
    consumers that read item.get("price") (record_due_results.py) silently
    treat a live CLI-fallback quote the same as a genuinely missing one —
    audit finding quote-tier-schema-mismatch.
    """
    price = _to_float(row.get('last'))
    prev = _to_float(row.get('prev_close'))
    if prev is None:
        prev = price
    pct = round((price - prev) / prev * 100, 2) if price is not None and prev else 0
    item = {
        'symbol': row.get('symbol'),
        'price': price,
        'prev_close': prev,
        'change_pct': pct,
        'high': _to_float(row.get('high')),
        'low': _to_float(row.get('low')),
        'volume': int(row.get('volume') or 0),
    }
    pre_market = row.get('pre_market')
    if isinstance(pre_market, dict):
        pre_last = _to_float(pre_market.get('last'))
        if pre_last is not None:
            item['pre_market'] = pre_last
    post_market = row.get('post_market')
    if isinstance(post_market, dict):
        post_last = _to_float(post_market.get('last'))
        if post_last is not None:
            item['post_market'] = post_last
    return item


def cmd_quote(ctx, symbols):
    """实时行情"""
    if ctx is None:
        data = run_cli_json(['quote', *symbols, '--format', 'json'])
        rows = data if isinstance(data, list) else [data]
        return [normalize_cli_quote(row) for row in rows if isinstance(row, dict)]
    try:
        quotes = ctx.quote(symbols)
    except Exception:
        data = run_cli_json(['quote', *symbols, '--format', 'json'])
        rows = data if isinstance(data, list) else [data]
        return [normalize_cli_quote(row) for row in rows if isinstance(row, dict)]
    results = []
    for q in quotes:
        price = float(q.last_done)
        prev = float(q.prev_close) if q.prev_close else price
        pct = (price - prev) / prev * 100 if prev else 0
        item = {
            'symbol': q.symbol,
            'price': price,
            'prev_close': prev,
            'change_pct': round(pct, 2),
            'high': float(q.high) if q.high else None,
            'low': float(q.low) if q.low else None,
            'volume': int(q.volume) if q.volume else 0,
        }
        if q.pre_market_quote:
            item['pre_market'] = float(q.pre_market_quote.last_done)
        if q.post_market_quote:
            item['post_market'] = float(q.post_market_quote.last_done)
        results.append(item)
    return results


def cmd_candle(ctx, symbol, period='day', count=5):
    """K线数据"""
    if ctx is None:
        rows = run_cli_json(['kline', symbol, '--period', period, '--count', str(count), '--format', 'json'])
        if not isinstance(rows, list):
            raise RuntimeError(f'longbridge CLI returned non-list kline payload for {symbol}')
        return [normalize_cli_candle(row) for row in rows if isinstance(row, dict)]
    period_map = {
        '1m': Period.Min_1, '5m': Period.Min_5,
        '15m': Period.Min_15, '30m': Period.Min_30,
        '60m': Period.Min_60, 'day': Period.Day,
        'week': Period.Week, 'month': Period.Month,
    }
    p = period_map.get(period, Period.Day)
    try:
        candles = ctx.candlesticks(symbol, p, count, AdjustType.NoAdjust)
    except Exception:
        rows = run_cli_json(['kline', symbol, '--period', period, '--count', str(count), '--format', 'json'])
        if not isinstance(rows, list):
            raise RuntimeError(f'longbridge CLI returned non-list kline payload for {symbol}')
        return [normalize_cli_candle(row) for row in rows if isinstance(row, dict)]
    return [{
        'time': str(c.timestamp),
        'open': float(c.open),
        'high': float(c.high),
        'low': float(c.low),
        'close': float(c.close),
        'volume': int(c.volume),
    } for c in candles]


def cmd_session(ctx):
    """交易时段"""
    if ctx is None:
        raise RuntimeError('session requires longport SDK; CLI fallback only supports quote/candle')
    markets = ['US', 'CN', 'HK']
    results = []
    for m in markets:
        try:
            sessions = ctx.trading_session(m)
            for s in sessions:
                results.append({
                    'market': m,
                    'session': str(s),
                })
        except Exception:
            results.append({'market': m, 'session': 'unknown'})
    return results


def cmd_depth(ctx, symbol, count=5):
    """盘口深度"""
    if ctx is None:
        raise RuntimeError('depth requires longport SDK; CLI fallback only supports quote/candle')
    depth = ctx.depth(symbol)
    asks = [{'price': float(a.price), 'volume': int(a.volume)} for a in depth.asks[:count]]
    bids = [{'price': float(b.price), 'volume': int(b.volume)} for b in depth.bids[:count]]
    return {'symbol': symbol, 'asks': asks, 'bids': bids}


def main():
    parser = argparse.ArgumentParser(description='Longbridge 行情查询')
    parser.add_argument('command', choices=['quote', 'candle', 'session', 'depth'],
                        help='查询类型')
    parser.add_argument('symbols', nargs='*', help='股票代码 (如 TSLA.US 600519.SH)')
    parser.add_argument('--period', default='day', help='K线周期 (1m/5m/15m/30m/60m/day/week/month)')
    parser.add_argument('--count', type=int, default=5, help='K线数量')
    parser.add_argument('--json', action='store_true', help='JSON输出')

    args = parser.parse_args()

    try:
        with suppress_stdout_fd():
            ctx = get_ctx()

        if args.command == 'quote':
            if not args.symbols:
                args.symbols = ['TSLA.US', 'BABA.US', 'GOOGL.US', 'PLTR.US']
            result = cmd_quote(ctx, args.symbols)
        elif args.command == 'candle':
            if not args.symbols:
                print('请指定股票代码', file=sys.stderr)
                sys.exit(1)
            result = cmd_candle(ctx, args.symbols[0], args.period, args.count)
        elif args.command == 'session':
            result = cmd_session(ctx)
        elif args.command == 'depth':
            if not args.symbols:
                args.symbols = ['TSLA.US']
            result = cmd_depth(ctx, args.symbols[0], args.count)

        if args.json:
            print(json.dumps(result, ensure_ascii=False, indent=2))
        else:
            # 人类可读格式
            if args.command == 'quote':
                for q in result:
                    pct = f'{q["change_pct"]:+.2f}%'
                    pre = f' pre={q["pre_market"]}' if q.get('pre_market') else ''
                    post = f' post={q["post_market"]}' if q.get('post_market') else ''
                    print(f'{q["symbol"]}: {q["price"]} ({pct}) H={q["high"]} L={q["low"]} Vol={q["volume"]}{pre}{post}')
            elif args.command == 'candle':
                for c in result:
                    print(f'{c["time"]} O={c["open"]} H={c["high"]} L={c["low"]} C={c["close"]} V={c["volume"]}')
            else:
                print(json.dumps(result, ensure_ascii=False, indent=2))

    except Exception as e:
        print(f'❌ {e}', file=sys.stderr)
        sys.exit(1)


if __name__ == '__main__':
    main()
