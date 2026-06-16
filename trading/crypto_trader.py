"""
Crypto Paper Trader — mirrors AlpacaTrader but for 24/7 crypto simulation.
All trades stored in DB with source='crypto_paper'.
Binance is ONLY used as a price/data source — no real orders are ever placed.
"""

import json, threading
from datetime import datetime, timezone, timedelta
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import (CRYPTO_CAPITAL, CRYPTO_MAX_POSITIONS, CRYPTO_MAX_POSITION_PCT,
                    CRYPTO_MIN_TRADE_USD, SLIPPAGE_PCT, BINANCE_PAPER)
from data.database import get_conn

_A  = lambda n: f"\033[{n}m"
_R  = _A("0");  _MG = _A("1;95");  _G = _A("1;92");  _RD = _A("1;91")

_IST = timezone(timedelta(hours=5, minutes=30))


def _now_ist_str():
    return datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")


class CryptoTrader:
    """
    USD paper trader for crypto — same interface as PaperTrader and AlpacaTrader.
    source='crypto_paper' keeps all crypto trades isolated in the shared DB.
    Supports fractional quantities (crypto is divisible — e.g. 0.001 BTC).
    """

    SOURCE = 'crypto_paper'

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
        return round(CRYPTO_CAPITAL + realized - open_capital, 6)

    def _load_open_positions(self):
        conn = get_conn()
        c    = conn.cursor()
        c.execute("SELECT * FROM trades WHERE status='open' AND source=?", (self.SOURCE,))
        rows = c.fetchall()
        conn.close()
        cols = ['id', 'symbol', 'entry_time', 'exit_time', 'entry_price', 'exit_price',
                'quantity', 'capital_used', 'pnl', 'pnl_pct', 'stop_loss', 'target',
                'exit_reason', 'signals', 'status', 'source']
        return [dict(zip(cols, r)) for r in rows]

    # ── Buy / Sell ────────────────────────────────────────────────────────────

    def can_buy(self):
        return len(self.open_positions) < CRYPTO_MAX_POSITIONS

    def get_position(self, symbol):
        for p in self.open_positions:
            if p['symbol'] == symbol:
                return p
        return None

    def buy(self, symbol, price, stop_loss, target, signals, reason, confidence, lot_size=None):
        """
        Place a simulated crypto buy.
        Crypto has no lot size — quantity is calculated as fractional units.
        e.g. BTCUSDT at $65,000 with $50 allocation → 0.000769 BTC
        """
        if not self.can_buy():
            return None, "Max crypto positions reached"
        if self.get_position(symbol):
            return None, "Already in crypto position"
        if price <= 0:
            return None, "Invalid price"

        # Adaptive sizing:
        # - Normal: 5% of balance per trade
        # - Small accounts: minimum $CRYPTO_MIN_TRADE_USD (Binance floor ~$5)
        # - Hard cap: never more than 50% of balance in a single trade
        alloc        = self.balance * CRYPTO_MAX_POSITION_PCT   # 5% target
        alloc        = max(alloc, CRYPTO_MIN_TRADE_USD)          # at least $5 (Binance minimum)
        alloc        = min(alloc, self.balance * 0.5)            # never exceed 50% of balance
        capital_used = round(alloc, 6)
        if capital_used < CRYPTO_MIN_TRADE_USD:
            return None, f"Balance too low for minimum trade (need ${CRYPTO_MIN_TRADE_USD:.2f}, have ${self.balance:.2f})"

        # Apply slippage in paper mode
        if BINANCE_PAPER:
            price = round(price * (1 + SLIPPAGE_PCT), 8)

        # Fractional quantity — crypto allows any precision
        quantity = round(capital_used / price, 8)
        if quantity <= 0:
            return None, "Quantity too small"

        now  = _now_ist_str()
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
        pv = self.balance + sum(p['capital_used'] for p in self.open_positions)
        if pv > self._session_high:
            self._session_high = pv
        print(f"{_MG}[CRYPTO BUY]  {symbol} @ ${price:.4f} × {quantity:.6f}  "
              f"SL:${stop_loss:.4f}  TGT:${target:.4f}  ${capital_used:.2f}  Bal:${self.balance:.2f}{_R}")
        return position, None

    def sell(self, position, current_price, reason):
        if not any(p['id'] == position['id'] for p in self.open_positions):
            return 0.0, 0.0

        entry   = position['entry_price']
        qty     = position['quantity']
        pnl     = round((current_price - entry) * qty, 6)
        pnl_pct = round(((current_price - entry) / entry) * 100, 4)
        now     = _now_ist_str()

        conn = get_conn()
        c    = conn.cursor()
        c.execute(
            "UPDATE trades SET exit_time=?, exit_price=?, pnl=?, pnl_pct=?, exit_reason=?, status=? WHERE id=?",
            (now, current_price, pnl, pnl_pct, reason, 'closed', position['id'])
        )
        conn.commit()
        conn.close()

        self.open_positions = [p for p in self.open_positions if p['id'] != position['id']]
        self.balance        = self._get_balance()
        pv = self.balance + sum(p['capital_used'] for p in self.open_positions)
        if pv > self._session_high:
            self._session_high = pv

        result = 'PROFIT' if pnl >= 0 else 'LOSS'
        _c = _G if pnl >= 0 else _RD
        print(f"{_c}[CRYPTO SELL] {position['symbol']} @ ${current_price:.4f}  "
              f"P&L: ${pnl:+.4f} ({pnl_pct:+.2f}%)  {result}  {reason}{_R}")
        return pnl, pnl_pct

    def get_drawdown_pct(self):
        portfolio_value = self.balance + sum(p['capital_used'] for p in self.open_positions)
        if self._session_high <= 0:
            return 0.0
        return max(0.0, (self._session_high - portfolio_value) / self._session_high)

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
            date_str = datetime.now(_IST).strftime("%Y-%m-%d")
        conn = get_conn()
        c    = conn.cursor()
        # Use entry_time date (IST) to classify trades. Crypto high-liq window runs until
        # 1 AM IST — exits after midnight IST would land on the next day's exit_time date,
        # causing yesterday's late trades to inflate today's cap. entry_time is always set
        # when the position opens, so it cleanly maps to the IST session day.
        c.execute(
            "SELECT pnl, symbol, entry_price, exit_price, pnl_pct FROM trades "
            "WHERE status='closed' AND source=? AND TRY_CAST(TRY_CAST(entry_time AS DATETIME2) AS DATE)=?",
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
        best     = max(rows, key=lambda x: x[0], default=(0, 'N/A', 0, 0, 0))
        worst    = min(rows, key=lambda x: x[0], default=(0, 'N/A', 0, 0, 0))

        trade_list = [
            {'symbol': r[1], 'entry': r[2], 'exit': r[3], 'pnl': r[0], 'pnl_pct': r[4]}
            for r in rows
        ]
        return {
            'date': date_str, 'trades': len(rows), 'wins': wins, 'losses': losses,
            'day_pnl': round(day_pnl, 4), 'balance': self.balance,
            'best':  f"{best[1]}  ${best[0]:+.4f}",
            'worst': f"{worst[1]}  ${worst[0]:+.4f}",
            'win_rate': round(win_rate, 1), 'total_trades': total,
            'trade_list': trade_list,
        }

    def auto_reload(self):
        """Crypto bot doesn't auto-reload — return False always."""
        return False, self.balance, 0.0
