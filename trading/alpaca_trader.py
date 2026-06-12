"""
Alpaca Paper Trader — mirrors PaperTrader but tracks USD trades.
All trades stored in the same DB with source='us_paper'.
Balance is calculated independently from INR trades.
"""

import sqlite3, json, threading
from datetime import datetime, timezone, timedelta
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import US_CAPITAL, US_MAX_POSITIONS, US_MAX_POSITION_PCT, SLIPPAGE_PCT, ALPACA_PAPER
from data.database import get_conn

_A  = lambda n: f"\033[{n}m"
_R  = _A("0");  _BL = _A("1;94");  _G = _A("1;92");  _RD = _A("1;91");  _CY = _A("1;96")

_IST = timezone(timedelta(hours=5, minutes=30))

def _is_dst():
    from datetime import date as _date
    utc = datetime.now(timezone.utc)
    y = utc.year
    mar = datetime(y, 3, 1, tzinfo=timezone.utc)
    nov = datetime(y, 11, 1, tzinfo=timezone.utc)
    dst_start = mar + timedelta(days=(6 - mar.weekday()) % 7) + timedelta(weeks=1, hours=7)
    dst_end   = nov + timedelta(days=(6 - nov.weekday()) % 7) + timedelta(hours=6)
    return dst_start <= utc < dst_end

_ET = lambda: timezone(timedelta(hours=-4 if _is_dst() else -5))

def _now_ist_str():
    # Store timestamps in ET so that date() queries align with the US trading day
    return datetime.now(_ET()).strftime("%Y-%m-%d %H:%M:%S")


class AlpacaTrader:
    """USD paper trader — same interface as PaperTrader, stores source='us_paper'."""

    SOURCE = 'us_paper'

    def __init__(self):
        self._lock          = threading.Lock()
        self.balance        = self._get_balance()
        self.open_positions = self._load_open_positions()
        self._session_high  = self.balance

    # ── Balance ───────────────────────────────────────────────────────────────

    def _get_balance(self):
        conn = get_conn()
        c    = conn.cursor()
        c.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status='closed' AND source=?", (self.SOURCE,))
        realized = c.fetchone()[0] or 0.0
        c.execute("SELECT COALESCE(SUM(capital_used), 0) FROM trades WHERE status='open' AND source=?", (self.SOURCE,))
        open_capital = c.fetchone()[0] or 0.0
        conn.close()
        return round(US_CAPITAL + realized - open_capital, 2)

    def _load_open_positions(self):
        conn = get_conn()
        c    = conn.cursor()
        c.execute("SELECT * FROM trades WHERE status='open' AND source=?", (self.SOURCE,))
        rows = c.fetchall()
        conn.close()
        cols = ['id','symbol','entry_time','exit_time','entry_price','exit_price',
                'quantity','capital_used','pnl','pnl_pct','stop_loss','target',
                'exit_reason','signals','status','source']
        return [dict(zip(cols, r)) for r in rows]

    # ── Buy / Sell ────────────────────────────────────────────────────────────

    def can_buy(self):
        return len(self.open_positions) < US_MAX_POSITIONS

    def get_position(self, symbol):
        for p in self.open_positions:
            if p['symbol'] == symbol:
                return p
        return None

    def buy(self, symbol, price, stop_loss, target, signals, reason, confidence, lot_size=1):
        if not self.can_buy():
            return None, "Max US positions reached"
        if self.get_position(symbol):
            return None, "Already in US position"
        if price <= 0:
            return None, "Invalid price"

        # Apply slippage in paper mode
        if ALPACA_PAPER:
            price = round(price * (1 + SLIPPAGE_PCT), 4)

        min_lot   = max(1, int(lot_size or 1))
        alloc     = self.balance * US_MAX_POSITION_PCT
        quantity  = (int(alloc / price) // min_lot) * min_lot

        if quantity < min_lot:
            one_lot_cost = price * min_lot
            if one_lot_cost <= self.balance * 0.10:
                quantity = min_lot
            else:
                return None, f"Skip: ${price:.2f} × {min_lot} = ${one_lot_cost:.0f} > 10% balance"

        capital_used = round(quantity * price, 2)
        if capital_used > self.balance or capital_used < 1:
            return None, "Insufficient USD capital"

        now = _now_ist_str()
        conn = get_conn()
        c    = conn.cursor()
        c.execute('''
            INSERT INTO trades (symbol, entry_time, entry_price, quantity, capital_used,
                stop_loss, target, signals, status, source)
            OUTPUT INSERTED.id
            VALUES (?,?,?,?,?,?,?,?,?,?)
        ''', (symbol, now, price, quantity, capital_used,
              stop_loss, target,
              json.dumps({'reasons': reason, 'confidence': confidence, 'signals': signals}),
              'open', self.SOURCE))
        trade_id = c.fetchone()[0]
        conn.commit()
        conn.close()

        position = {
            'id': trade_id, 'symbol': symbol, 'entry_time': now,
            'entry_price': price, 'quantity': quantity, 'capital_used': capital_used,
            'stop_loss': stop_loss, 'target': target, 'status': 'open'
        }
        self.open_positions.append(position)
        self.balance = self._get_balance()
        if self.balance > self._session_high:
            self._session_high = self.balance
        print(f"{_BL}[US BUY]  {symbol} @ ${price:.2f} × {quantity}  SL:${stop_loss:.2f}  TGT:${target:.2f}  ${capital_used:.0f}  Bal:${self.balance:.2f}{_R}")
        return position, None

    def sell(self, position, current_price, reason):
        if not any(p['id'] == position['id'] for p in self.open_positions):
            return 0.0, 0.0

        entry   = position['entry_price']
        qty     = position['quantity']
        pnl     = round((current_price - entry) * qty, 2)
        pnl_pct = round(((current_price - entry) / entry) * 100, 2)
        now     = _now_ist_str()

        conn = get_conn()
        c    = conn.cursor()
        c.execute('''
            UPDATE trades SET exit_time=?, exit_price=?, pnl=?, pnl_pct=?, exit_reason=?, status='closed'
            WHERE id=?
        ''', (now, current_price, pnl, pnl_pct, reason, position['id']))
        conn.commit()
        conn.close()

        self.open_positions = [p for p in self.open_positions if p['id'] != position['id']]
        self.balance        = self._get_balance()
        if self.balance > self._session_high:
            self._session_high = self.balance

        result = 'PROFIT' if pnl >= 0 else 'LOSS'
        _c = _G if pnl >= 0 else _RD
        print(f"{_c}[US SELL] {position['symbol']} @ ${current_price:.2f}  P&L: ${pnl:+.2f} ({pnl_pct:+.1f}%)  {result}  {reason}{_R}")
        return pnl, pnl_pct

    def get_drawdown_pct(self):
        if self._session_high <= 0:
            return 0.0
        return max(0.0, (self._session_high - self.balance) / self._session_high)

    def get_deployed_pct(self):
        deployed = sum(p['capital_used'] for p in self.open_positions)
        total = self.balance + deployed
        return (deployed / total) if total > 0 else 0.0

    # ── Stats ─────────────────────────────────────────────────────────────────

    def reload(self):
        """Reload balance and positions from DB (used by multi-process Telegram monitor)."""
        self.balance        = self._get_balance()
        self.open_positions = self._load_open_positions()

    def get_daily_stats(self, date_str=None):
        if not date_str:
            date_str = datetime.now(_ET()).strftime("%Y-%m-%d")  # use ET so day aligns with NYSE session
        conn = get_conn()
        c    = conn.cursor()
        c.execute(
            "SELECT pnl, symbol, entry_price, exit_price, pnl_pct FROM trades "
            "WHERE status='closed' AND source=? AND TRY_CAST(TRY_CAST(exit_time AS DATETIME2) AS DATE)=?",
            (self.SOURCE, date_str)
        )
        rows = c.fetchall()
        c.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND source=?", (self.SOURCE,))
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND source=? AND pnl > 0", (self.SOURCE,))
        wins_total = c.fetchone()[0]
        conn.close()

        day_pnl  = sum(r[0] for r in rows)
        wins     = sum(1 for r in rows if r[0] > 0)
        losses   = len(rows) - wins
        win_rate = (wins_total / total * 100) if total > 0 else 0
        best     = max(rows, key=lambda x: x[0], default=(0,'N/A',0,0,0))
        worst    = min(rows, key=lambda x: x[0], default=(0,'N/A',0,0,0))

        trade_list = [
            {'symbol': r[1], 'entry': r[2], 'exit': r[3], 'pnl': r[0], 'pnl_pct': r[4]}
            for r in rows
        ]
        return {
            'date': date_str, 'trades': len(rows), 'wins': wins, 'losses': losses,
            'day_pnl': round(day_pnl, 2), 'balance': self.balance,
            'best': f"{best[1]} ${best[0]:+.2f}", 'worst': f"{worst[1]} ${worst[0]:+.2f}",
            'win_rate': round(win_rate, 1), 'total_trades': total,
            'trade_list': trade_list,
        }

    def auto_reload(self):
        """US bot doesn't auto-reload — return False always."""
        return False, self.balance, 0.0
