import json
import sqlite3
import warnings
import numpy as np
from datetime import datetime, timedelta
from sklearn.ensemble import RandomForestClassifier
from sklearn.preprocessing import StandardScaler
from sklearn.exceptions import InconsistentVersionWarning
import pickle
import os, sys

warnings.filterwarnings('ignore', category=InconsistentVersionWarning)
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from data.database import get_conn

MODEL_FILE = os.path.join(os.path.dirname(__file__), 'model.pkl')
WEIGHTS_FILE = os.path.join(os.path.dirname(__file__), 'weights.json')

# Default signal weights — updated after every training run
DEFAULT_WEIGHTS = {
    'rsi':       1.0,
    'macd':      1.0,
    'bollinger': 1.0,
    'ema':       1.0,
    'volume':    1.0,
    'vwap':      1.5,   # VWAP is the strongest intraday signal — start with higher weight
    'sentiment': 1.0,
}

_last_trained_count = 0   # track last retrain count to prevent infinite retrain on multiples


def load_weights():
    if os.path.exists(WEIGHTS_FILE):
        try:
            with open(WEIGHTS_FILE) as f:
                return json.load(f)
        except Exception:
            pass
    return DEFAULT_WEIGHTS.copy()


def save_weights(weights):
    with open(WEIGHTS_FILE, 'w') as f:
        json.dump(weights, f, indent=2)


def _load_trade_history(include_backtest=False):
    """Load trade history for ML training.
    include_backtest=True for initial pre-training (backtest + paper).
    Default: paper-only — live retrains should not learn from backtest simulations.
    """
    conn = get_conn()
    c = conn.cursor()
    if include_backtest:
        source_filter = "status='closed' AND signals IS NOT NULL"
    else:
        source_filter = "status='closed' AND signals IS NOT NULL AND (source='paper' OR source IS NULL)"
    c.execute(f"""
        SELECT symbol, entry_price, exit_price, pnl, pnl_pct, signals, entry_time, exit_time
        FROM trades
        WHERE {source_filter}
        ORDER BY exit_time
    """)
    rows = c.fetchall()
    conn.close()
    return rows


def _parse_features(signals_json):
    """Convert stored signals JSON into a feature vector."""
    try:
        data    = json.loads(signals_json)
        signals = data.get('signals', {})
        conf    = data.get('confidence', 50)

        signal_map = {'buy': 2, 'weak_buy': 1, 'neutral': 0, 'sell': -1, 'spike': 1, 'normal': 0}

        return [
            signal_map.get(signals.get('rsi',       'neutral'), 0),
            signal_map.get(signals.get('macd',      'neutral'), 0),
            signal_map.get(signals.get('bollinger', 'neutral'), 0),
            signal_map.get(signals.get('ema',       'neutral'), 0),
            signal_map.get(signals.get('volume',    'normal'),  0),
            signal_map.get(signals.get('vwap',      'neutral'), 0),
            conf / 100.0,
        ]
    except Exception:
        return [0, 0, 0, 0, 0, 0, 0.5]


def _recency_weight(exit_time_str):
    """
    Return a sample weight based on how recent the trade is.
    Recent trades reflect current market conditions — weight them more.
      <= 2 years  : 3.0  (primary)
      2 – 5 years : 2.0
      5 – 10 years: 1.0
      10+ years   : 0.5  (long-term pattern, lower weight)
    """
    try:
        exit_dt  = datetime.strptime(exit_time_str[:19], "%Y-%m-%d %H:%M:%S")
        days_old = (datetime.now() - exit_dt).days
        if days_old <= 730:
            return 3.0
        elif days_old <= 1825:
            return 2.0
        elif days_old <= 3650:
            return 1.0
        else:
            return 0.5
    except Exception:
        return 1.0


def train(min_trades=5, include_backtest=False):
    """
    Train the ML model on closed trade history with recency weighting.
    Recent trades (≤2 years) are weighted 3× vs older history.
    include_backtest=True for initial pre-training only — live retrains use paper trades only.
    Returns updated signal weights and model accuracy.
    """
    global _last_trained_count
    rows = _load_trade_history(include_backtest=include_backtest)

    if len(rows) < min_trades:
        print(f"[ML] Not enough trades to train ({len(rows)}/{min_trades}). Using default weights.")
        return load_weights(), 0.0

    print(f"[ML] Training on {len(rows)} trades (recency-weighted)...")

    X, y, sample_weights = [], [], []
    era = {'recent': 0, 'mid': 0, 'old': 0, 'ancient': 0}

    for row in rows:
        symbol, entry, exit_p, pnl, pnl_pct, signals_json, entry_t, exit_t = row
        features = _parse_features(signals_json)
        label    = 1 if pnl > 0 else 0
        weight   = _recency_weight(exit_t or entry_t or "2000-01-01")

        X.append(features)
        y.append(label)
        sample_weights.append(weight)

        if weight == 3.0:   era['recent']  += 1
        elif weight == 2.0: era['mid']     += 1
        elif weight == 1.0: era['old']     += 1
        else:               era['ancient'] += 1

    X              = np.array(X)
    y              = np.array(y)
    sample_weights = np.array(sample_weights)

    scaler   = StandardScaler()
    X_scaled = scaler.fit_transform(X)

    # class_weight='balanced' handles win/loss imbalance;
    # sample_weight handles recency — sklearn combines them multiplicatively
    model = RandomForestClassifier(n_estimators=200, random_state=42, class_weight='balanced')
    model.fit(X_scaled, y, sample_weight=sample_weights)

    accuracy    = model.score(X_scaled, y, sample_weight=sample_weights)
    importances = model.feature_importances_

    feature_names = ['rsi', 'macd', 'bollinger', 'ema', 'volume', 'vwap', 'sentiment']

    weights = {}
    for name, importance in zip(feature_names, importances[:6]):
        weights[name] = round(max(0.3, min(2.0, importance * 10)), 3)
    weights['sentiment'] = round(max(0.3, min(2.0, importances[6] * 10)), 3)

    save_weights(weights)

    with open(MODEL_FILE, 'wb') as f:
        pickle.dump({'model': model, 'scaler': scaler}, f)

    win_rate = (y.sum() / len(y)) * 100
    print(f"[ML] Training complete.")
    print(f"     Trades : {len(rows)}  |  Win rate: {win_rate:.1f}%  |  Accuracy: {accuracy*100:.1f}%")
    print(f"     History: ≤2yr={era['recent']}  2-5yr={era['mid']}  5-10yr={era['old']}  10+yr={era['ancient']}")
    print(f"     Signal weights (recency-weighted):")
    for name, w in weights.items():
        direction = "↑" if w > 1.0 else ("↓" if w < 0.8 else "→")
        print(f"       {name:12s}: {w:.3f}  {direction}")

    _last_trained_count = len(rows)
    _update_signal_performance(feature_names, importances, rows)
    return weights, accuracy


def predict(signals, confidence):
    """
    Use trained model to predict if a trade will be profitable.
    Returns probability (0-1). Falls back to confidence score if no model yet.
    """
    if not os.path.exists(MODEL_FILE):
        return confidence / 100.0

    try:
        with open(MODEL_FILE, 'rb') as f:
            saved = pickle.load(f)

        signal_map = {'buy': 2, 'weak_buy': 1, 'neutral': 0, 'sell': -1, 'spike': 1, 'normal': 0}
        features = [
            signal_map.get(signals.get('rsi',       'neutral'), 0),
            signal_map.get(signals.get('macd',      'neutral'), 0),
            signal_map.get(signals.get('bollinger', 'neutral'), 0),
            signal_map.get(signals.get('ema',       'neutral'), 0),
            signal_map.get(signals.get('volume',    'normal'),  0),
            signal_map.get(signals.get('vwap',      'neutral'), 0),
            confidence / 100.0,
        ]

        X = saved['scaler'].transform([features])
        prob = saved['model'].predict_proba(X)[0][1]  # probability of profit
        return float(prob)

    except Exception:
        return confidence / 100.0


def get_weighted_score(raw_score, signals, confidence):
    """
    Apply learned weights to the raw technical score.
    Makes the scanner smarter over time.
    """
    weights = load_weights()
    signal_values = {
        'rsi':       2 if signals.get('rsi') == 'buy' else (1 if signals.get('rsi') == 'weak_buy' else 0),
        'macd':      2 if signals.get('macd') == 'buy' else (1 if signals.get('macd') == 'weak_buy' else 0),
        'bollinger': 2 if signals.get('bollinger') == 'buy' else 0,
        'ema':       1 if signals.get('ema') == 'buy' else 0,
        'volume':    1 if signals.get('volume') == 'spike' else 0,
        'vwap':      2 if signals.get('vwap') == 'buy' else 0,
    }
    weighted = sum(signal_values[s] * weights.get(s, 1.0) for s in signal_values)
    ml_prob  = predict(signals, confidence)
    return round(weighted, 2), round(ml_prob * 100, 1)


def _update_signal_performance(feature_names, importances, rows):
    """Store signal performance stats in DB for reporting."""
    conn = get_conn()
    c    = conn.cursor()
    now  = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    for name, importance in zip(feature_names, importances):
        c.execute(
            "MERGE INTO signal_performance AS target "
            "USING (SELECT ? AS signal_name, ? AS weight, ? AS updated_at) AS src "
            "ON target.signal_name = src.signal_name "
            "WHEN MATCHED THEN UPDATE SET weight = src.weight, updated_at = src.updated_at "
            "WHEN NOT MATCHED THEN INSERT (signal_name, weight, updated_at) "
            "VALUES (src.signal_name, src.weight, src.updated_at);",
            (name, round(float(importance), 4), now)
        )
    conn.commit()
    conn.close()


def should_retrain():
    """Returns True if enough new trades have happened since last training."""
    global _last_trained_count
    rows = _load_trade_history()
    n    = len(rows)

    if not os.path.exists(MODEL_FILE):
        return n >= 5

    model_age = datetime.now() - datetime.fromtimestamp(os.path.getmtime(MODEL_FILE))

    # Retrain every 10 NEW trades since last retrain, OR every 3 days
    new_since_last = n - _last_trained_count
    return model_age > timedelta(days=3) or new_since_last >= 10


if __name__ == "__main__":
    print("=== Self-Learner Test ===\n")

    # Show current weights
    weights = load_weights()
    print("Current signal weights:")
    for k, v in weights.items():
        print(f"  {k:12s}: {v}")

    # Try training
    weights, acc = train(min_trades=1)

    # Test prediction with sample signals
    sample_signals = {'rsi': 'buy', 'macd': 'weak_buy', 'bollinger': 'neutral', 'ema': 'buy', 'volume': 'spike'}
    weighted_score, ml_prob = get_weighted_score(5, sample_signals, 68)
    print(f"\nSample trade prediction:")
    print(f"  Weighted score : {weighted_score}")
    print(f"  ML probability : {ml_prob}% chance of profit")
