import sqlite3
import json
import threading
from datetime import datetime, timezone, timedelta
import sys, os

_IST = timezone(timedelta(hours=5, minutes=30))
_A  = lambda n: f"\033[{n}m"
_R  = _A("0");  _G = _A("1;92");  _RD = _A("1;91");  _CY = _A("1;96")

def _now_ist_str():
    return datetime.now(_IST).strftime("%Y-%m-%d %H:%M:%S")
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import (CAPITAL, MAX_POSITIONS, MAX_POSITION_PCT,
                    RELOAD_THRESHOLD, RELOAD_AMOUNT, SLIPPAGE_PCT, PAPER_MODE)
from data.database import get_conn
from data.nifty_stocks import is_mutual_fund

class PaperTrader:
    def __init__(self):
        self._lock            = threading.Lock()   # prevents monitor + scan double-exit
        self.balance          = self._get_balance()
        self.open_positions   = self._load_open_positions()
        self._session_high    = self.balance       # track peak balance for drawdown check

    def _get_total_reloaded(self):
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT COALESCE(SUM(amount), 0) FROM top_ups")
        val = c.fetchone()[0]
        conn.close()
        return val or 0.0

    def _get_balance(self):
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT COALESCE(SUM(pnl), 0) FROM trades WHERE status='closed' AND (source='paper' OR source IS NULL)")
        realized = c.fetchone()[0] or 0.0
        c.execute("SELECT COALESCE(SUM(capital_used), 0) FROM trades WHERE status='open' AND (source='paper' OR source IS NULL)")
        open_capital = c.fetchone()[0] or 0.0
        conn.close()
        total_reloaded = self._get_total_reloaded()
        return round(CAPITAL + total_reloaded + realized - open_capital, 2)

    def _load_open_positions(self):
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM trades WHERE status='open' AND (source='paper' OR source IS NULL)")
        rows = c.fetchall()
        conn.close()
        cols = ['id','symbol','entry_time','exit_time','entry_price','exit_price',
                'quantity','capital_used','pnl','pnl_pct','stop_loss','target',
                'exit_reason','signals','status','source']
        return [dict(zip(cols, r)) for r in rows]

    def can_buy(self):
        return len(self.open_positions) < MAX_POSITIONS

    def get_position(self, symbol):
        for p in self.open_positions:
            if p['symbol'] == symbol:
                return p
        return None

    def buy(self, symbol, price, stop_loss, target, signals, reason, confidence, lot_size=None):
        # Hard safety block — never trade mutual funds
        if is_mutual_fund(symbol):
            print(f"SAFETY BLOCK: {symbol} identified as mutual fund — skipping")
            return None, "Mutual fund — blocked"
        if not self.can_buy():
            return None, "Max positions reached"
        if self.get_position(symbol):
            return None, "Already in position"

        # lot_size pre-fetched in parallel by caller; fallback to API call
        from data.fetcher import get_min_lot_size
        min_lot = lot_size if lot_size and lot_size > 0 else get_min_lot_size(symbol)

        # ── Dynamic sizing — auto-scales with capital ─────────────
        # Always 5% of current balance. Grows as capital grows, shrinks if losses.
        normal_alloc = self.balance * MAX_POSITION_PCT
        quantity = (int(normal_alloc / price) // min_lot) * min_lot

        if quantity < min_lot:
            # Can't fill even 1 lot with normal allocation.
            # Stretch to 1 lot only if it stays within 10% of balance — prevents
            # one expensive stock consuming a disproportionate amount of capital.
            one_lot_cost = price * min_lot
            max_fallback = self.balance * 0.10
            if one_lot_cost <= max_fallback:
                quantity = min_lot
            elif one_lot_cost <= self.balance:
                return None, f"Skip: {symbol} 1 lot = ₹{one_lot_cost:.0f} > 10% of balance ₹{self.balance:.0f}"
            else:
                return None, f"Out of range: {min_lot} share(s) of {symbol} @ ₹{price:.0f} = ₹{one_lot_cost:.0f} > balance ₹{self.balance:.0f}"

        # Apply slippage in paper mode — simulates realistic fill cost
        if PAPER_MODE:
            price = round(price * (1 + SLIPPAGE_PCT), 4)

        capital_used = round(quantity * price, 2)

        if capital_used > self.balance or capital_used < 1:
            return None, "Insufficient capital"

        now = _now_ist_str()

        conn = get_conn()
        c = conn.cursor()
        c.execute('''
            INSERT INTO trades (symbol, entry_time, entry_price, quantity, capital_used,
                stop_loss, target, signals, status)
            VALUES (?,?,?,?,?,?,?,?,?)
        ''', (symbol, now, price, quantity, capital_used,
              stop_loss, target, json.dumps({'reasons': reason, 'confidence': confidence, 'signals': signals}), 'open'))
        trade_id = c.lastrowid
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
        print(f"{_CY}[BUY]  {symbol} @ ₹{price:.2f} × {quantity} share(s)  SL:₹{stop_loss:.2f}  TGT:₹{target:.2f}  Capital:₹{capital_used:.0f}  Balance:₹{self.balance:.2f}{_R}")
        return position, None

    def sell(self, position, current_price, reason):
        # Guard: position may already be closed by the real-time monitor
        if not any(p['id'] == position['id'] for p in self.open_positions):
            return 0.0, 0.0

        entry   = position['entry_price']
        qty     = position['quantity']
        pnl     = round((current_price - entry) * qty, 2)
        pnl_pct = round(((current_price - entry) / entry) * 100, 2)
        now     = _now_ist_str()

        conn = get_conn()
        c = conn.cursor()
        c.execute('''
            UPDATE trades SET exit_time=?, exit_price=?, pnl=?, pnl_pct=?, exit_reason=?, status='closed'
            WHERE id=?
        ''', (now, current_price, pnl, pnl_pct, reason, position['id']))
        conn.commit()
        conn.close()

        self.open_positions = [p for p in self.open_positions if p['id'] != position['id']]
        self.balance = self._get_balance()
        if self.balance > self._session_high:
            self._session_high = self.balance

        result = 'PROFIT' if pnl >= 0 else 'LOSS'
        _c = _G if pnl >= 0 else _RD
        print(f"{_c}[SELL] {position['symbol']} @ ₹{current_price:.2f}  P&L: ₹{pnl:+.2f} ({pnl_pct:+.1f}%)  {result}  Reason: {reason}{_R}")
        return pnl, pnl_pct

    def partial_sell(self, position, qty_to_sell, current_price, reason, new_target=None, new_stop_loss=None):
        """Sell part of a position.
        Guard: position may already be closed by the real-time monitor. Inserts a closed trade for the sold portion
        and updates the remaining open position's quantity, target, and stop-loss as needed.
        Returns (pnl, pnl_pct) for the sold shares.
        """
        if not any(p['id'] == position['id'] for p in self.open_positions):
            return 0.0, 0.0

        entry       = position['entry_price']
        pnl         = round((current_price - entry) * qty_to_sell, 2)
        pnl_pct     = round(((current_price - entry) / entry) * 100, 2)
        remaining   = position['quantity'] - qty_to_sell
        now         = _now_ist_str()
        cap_sold    = round(qty_to_sell * entry, 2)
        cap_remain  = round(remaining  * entry, 2)

        conn = get_conn()
        c    = conn.cursor()
        # Record the sold portion as a new closed trade
        c.execute('''
            INSERT INTO trades (symbol, entry_time, exit_time, entry_price, exit_price,
                quantity, capital_used, pnl, pnl_pct, stop_loss, target,
                exit_reason, signals, status, source)
            SELECT symbol, entry_time, ?, entry_price, ?,
                ?, ?, ?, ?, stop_loss, target,
                ?, signals, 'closed', source
            FROM trades WHERE id=?
        ''', (now, current_price, qty_to_sell, cap_sold, pnl, pnl_pct, reason, position['id']))
        # Update the remaining open position
        update_sql = "UPDATE trades SET quantity=?, capital_used=?"
        params     = [remaining, cap_remain]
        if new_target is not None:
            update_sql += ", target=?"
            params.append(new_target)
        if new_stop_loss is not None:
            update_sql += ", stop_loss=?"
            params.append(new_stop_loss)
        update_sql += " WHERE id=?"
        params.append(position['id'])
        c.execute(update_sql, params)
        conn.commit()
        conn.close()

        # Sync in-memory position
        for p in self.open_positions:
            if p['id'] == position['id']:
                p['quantity']     = remaining
                p['capital_used'] = cap_remain
                if new_target is not None:
                    p['target']    = new_target
                if new_stop_loss is not None:
                    p['stop_loss'] = new_stop_loss
                break

        self.balance = self._get_balance()
        result = 'PROFIT' if pnl >= 0 else 'LOSS'
        extra = ""
        if new_target:
            extra += f"  New target: ₹{new_target:.2f}"
        if new_stop_loss:
            extra += f"  New SL (break-even): ₹{new_stop_loss:.2f}"
        print(f"[PARTIAL SELL] {position['symbol']} {qty_to_sell} share(s) @ ₹{current_price:.2f}"
              f"  P&L: ₹{pnl:+.2f} ({pnl_pct:+.1f}%)  {result}"
              f"  Remaining: {remaining} share(s){extra}")
        return pnl, pnl_pct

    def get_drawdown_pct(self):
        """Returns how far current balance has dropped from today's session high (0.0–1.0)."""
        if self._session_high <= 0:
            return 0.0
        return max(0.0, (self._session_high - self.balance) / self._session_high)

    def get_deployed_pct(self):
        """Returns fraction of balance currently deployed in open positions (0.0–1.0)."""
        deployed = sum(p['capital_used'] for p in self.open_positions)
        total = self.balance + deployed
        return (deployed / total) if total > 0 else 0.0

    def auto_reload(self):
        """
        Reload when available cash is below RELOAD_THRESHOLD (₹300) AND
        the total portfolio value (cash + capital in open positions) is also
        below the threshold. If shares are still holding enough value, wait
        for them to close before reloading.
        Returns (reloaded: bool, old_balance, amount_added).
        """
        if self.balance >= RELOAD_THRESHOLD:
            return False, self.balance, 0.0

        open_capital = sum(p['capital_used'] for p in self.open_positions)
        total_value  = self.balance + open_capital
        if total_value >= RELOAD_THRESHOLD:
            return False, self.balance, 0.0

        old_balance = self.balance
        now         = _now_ist_str()

        conn = get_conn()
        c    = conn.cursor()
        c.execute(
            "INSERT INTO top_ups (amount, reason, balance_before, time) VALUES (?,?,?,?)",
            (RELOAD_AMOUNT, "Auto-reload: balance exhausted in losses", old_balance, now)
        )
        conn.commit()
        conn.close()

        self.balance = self._get_balance()
        count = self.get_reload_stats()['count']
        print(f"[RELOAD #{count}] Balance was ₹{old_balance:.2f} → injected ₹{RELOAD_AMOUNT:.0f} → new balance ₹{self.balance:.2f}")
        return True, old_balance, RELOAD_AMOUNT

    def get_reload_stats(self):
        conn = get_conn()
        c    = conn.cursor()
        c.execute("SELECT COUNT(*), COALESCE(SUM(amount), 0) FROM top_ups")
        count, total_reloaded = c.fetchone()
        c.execute("SELECT amount, balance_before, time FROM top_ups ORDER BY time")
        history = c.fetchall()
        conn.close()
        total_invested = CAPITAL + (total_reloaded or 0.0)
        net_pnl        = round(self.balance - total_invested, 2)
        return {
            'count':          count or 0,
            'total_reloaded': total_reloaded or 0.0,
            'total_invested': total_invested,
            'net_pnl':        net_pnl,
            'history':        history,
        }

    def reload(self):
        """Reload balance and positions from DB (used by multi-process Telegram monitor)."""
        self.balance        = self._get_balance()
        self.open_positions = self._load_open_positions()

    def get_daily_stats(self, date_str=None):
        if not date_str:
            date_str = datetime.now(_IST).strftime("%Y-%m-%d")
        conn = get_conn()
        c = conn.cursor()
        c.execute(
            "SELECT pnl, symbol, entry_price, exit_price, pnl_pct FROM trades "
            "WHERE status='closed' AND (source='paper' OR source IS NULL) AND date(exit_time)=?",
            (date_str,)
        )
        rows = c.fetchall()
        c.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND (source='paper' OR source IS NULL)")
        total = c.fetchone()[0]
        c.execute("SELECT COUNT(*) FROM trades WHERE status='closed' AND (source='paper' OR source IS NULL) AND pnl > 0")
        wins_total = c.fetchone()[0]
        conn.close()

        day_pnl = sum(r[0] for r in rows)
        wins = sum(1 for r in rows if r[0] > 0)
        losses = len(rows) - wins
        win_rate = (wins_total / total * 100) if total > 0 else 0

        best = max(rows, key=lambda x: x[0], default=(0,'N/A',0,0,0))
        worst = min(rows, key=lambda x: x[0], default=(0,'N/A',0,0,0))

        trade_list = [
            {'symbol': r[1], 'entry': r[2], 'exit': r[3], 'pnl': r[0], 'pnl_pct': r[4]}
            for r in rows
        ]

        return {
            'date': date_str,
            'trades': len(rows),
            'wins': wins,
            'losses': losses,
            'day_pnl': round(day_pnl, 2),
            'balance': self.balance,
            'best': f"{best[1]} ₹{best[0]:+.2f}",
            'worst': f"{worst[1]} ₹{worst[0]:+.2f}",
            'win_rate': round(win_rate, 1),
            'total_trades': total,
            'trade_list': trade_list,
        }
