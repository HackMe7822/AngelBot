import pandas as pd
import ta
import sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

def compute_indicators(df):
    if df is None or len(df) < 30:
        return None

    df = df.copy()
    close = df['Close']
    high  = df['High']
    low   = df['Low']
    vol   = df['Volume']

    # RSI
    df['rsi'] = ta.momentum.RSIIndicator(close, window=14).rsi()

    # MACD
    macd = ta.trend.MACD(close)
    df['macd']        = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_diff']   = macd.macd_diff()

    # Bollinger Bands
    bb = ta.volatility.BollingerBands(close, window=20)
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_lower'] = bb.bollinger_lband()
    df['bb_mid']   = bb.bollinger_mavg()
    df['bb_pct']   = bb.bollinger_pband()

    # ATR (for stop-loss sizing)
    df['atr'] = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

    # EMAs
    df['ema20']  = ta.trend.EMAIndicator(close, window=20).ema_indicator()
    df['ema50']  = ta.trend.EMAIndicator(close, window=50).ema_indicator()
    df['ema200'] = ta.trend.EMAIndicator(close, window=200).ema_indicator()

    # Volume SMA
    df['vol_sma20'] = vol.rolling(window=20).mean()
    df['vol_ratio'] = vol / df['vol_sma20']

    return df.dropna(subset=['rsi','macd','atr'])

def generate_signals(df):
    if df is None or len(df) < 2:
        return None

    latest = df.iloc[-1]
    prev   = df.iloc[-2]
    signals = {}
    score   = 0
    reasons = []

    # RSI signal
    rsi = latest['rsi']
    if rsi < 35:
        signals['rsi'] = 'buy'
        score += 2
        reasons.append(f"RSI oversold ({rsi:.0f})")
    elif rsi < 45:
        signals['rsi'] = 'weak_buy'
        score += 1
        reasons.append(f"RSI low ({rsi:.0f})")
    elif rsi > 70:
        signals['rsi'] = 'sell'
        score -= 2
    else:
        signals['rsi'] = 'neutral'

    # MACD crossover
    if prev['macd_diff'] < 0 and latest['macd_diff'] > 0:
        signals['macd'] = 'buy'
        score += 2
        reasons.append("MACD bullish crossover")
    elif latest['macd_diff'] > 0:
        signals['macd'] = 'weak_buy'
        score += 1
        reasons.append("MACD positive")
    elif latest['macd_diff'] < 0:
        signals['macd'] = 'sell'
        score -= 1
    else:
        signals['macd'] = 'neutral'

    # Bollinger Band
    bb_pct = latest['bb_pct']
    if bb_pct < 0.2:
        signals['bollinger'] = 'buy'
        score += 2
        reasons.append("Price near lower Bollinger Band")
    elif bb_pct > 0.8:
        signals['bollinger'] = 'sell'
        score -= 1
    else:
        signals['bollinger'] = 'neutral'

    # EMA trend
    price = latest['Close']
    if price > latest['ema20'] > latest['ema50']:
        signals['ema'] = 'buy'
        score += 1
        reasons.append("Price above EMA20 > EMA50 (uptrend)")
    elif price < latest['ema20'] < latest['ema50']:
        signals['ema'] = 'sell'
        score -= 1
    else:
        signals['ema'] = 'neutral'

    # Volume spike
    vol_ratio = latest['vol_ratio']
    if vol_ratio > 1.8:
        signals['volume'] = 'spike'
        score += 1
        reasons.append(f"Volume spike {vol_ratio:.1f}x average")
    else:
        signals['volume'] = 'normal'

    return {
        'score': score,
        'signals': signals,
        'reasons': reasons,
        'price': price,
        'atr': latest['atr'],
        'rsi': rsi,
    }

def compute_indicators_intraday(df):
    """Indicators tuned for 5-minute candles."""
    if df is None or len(df) < 30:
        return None
    df = df.copy()
    close = df['Close']
    high  = df['High']
    low   = df['Low']
    vol   = df['Volume']

    df['rsi']  = ta.momentum.RSIIndicator(close, window=14).rsi()

    macd = ta.trend.MACD(close, window_slow=26, window_fast=12, window_sign=9)
    df['macd']        = macd.macd()
    df['macd_signal'] = macd.macd_signal()
    df['macd_diff']   = macd.macd_diff()

    bb = ta.volatility.BollingerBands(close, window=20)
    df['bb_upper'] = bb.bollinger_hband()
    df['bb_lower'] = bb.bollinger_lband()
    df['bb_pct']   = bb.bollinger_pband()

    df['ema9']  = ta.trend.EMAIndicator(close, window=9).ema_indicator()
    df['ema21'] = ta.trend.EMAIndicator(close, window=21).ema_indicator()

    df['atr'] = ta.volatility.AverageTrueRange(high, low, close, window=14).average_true_range()

    df['vol_sma20'] = vol.rolling(window=20).mean()
    df['vol_ratio'] = vol / df['vol_sma20']

    # VWAP — resets each calendar day
    df['_typical'] = (high + low + close) / 3
    df['_tp_vol']  = df['_typical'] * vol
    df['_date']    = df.index.date
    df['_cumvol']  = df.groupby('_date')['Volume'].transform(lambda x: x.cumsum())
    df['_cumtpvol']= df.groupby('_date')['_tp_vol'].transform(lambda x: x.cumsum())
    df['vwap']     = df['_cumtpvol'] / df['_cumvol']

    return df.dropna(subset=['rsi', 'macd', 'ema9', 'ema21', 'vwap'])


def generate_signals_intraday(df):
    """Signal generation for 5-minute intraday data with VWAP."""
    if df is None or len(df) < 2:
        return None

    latest  = df.iloc[-1]
    prev    = df.iloc[-2]
    signals = {}
    score   = 0
    reasons = []

    rsi = latest['rsi']
    if rsi < 40:
        signals['rsi'] = 'buy'
        score += 2
        reasons.append(f"RSI oversold ({rsi:.0f})")
    elif rsi < 50:
        signals['rsi'] = 'weak_buy'
        score += 1
        reasons.append(f"RSI low ({rsi:.0f})")
    elif rsi > 65:
        signals['rsi'] = 'sell'
        score -= 2
    else:
        signals['rsi'] = 'neutral'

    if prev['macd_diff'] < 0 and latest['macd_diff'] > 0:
        signals['macd'] = 'buy'
        score += 2
        reasons.append("MACD bullish crossover")
    elif latest['macd_diff'] > 0:
        signals['macd'] = 'weak_buy'
        score += 1
        reasons.append("MACD positive")
    elif latest['macd_diff'] < 0:
        signals['macd'] = 'sell'
        score -= 1
    else:
        signals['macd'] = 'neutral'

    bb_pct = latest['bb_pct']
    if bb_pct < 0.25:
        signals['bollinger'] = 'buy'
        score += 2
        reasons.append("Price near lower BB")
    elif bb_pct > 0.75:
        signals['bollinger'] = 'sell'
        score -= 1
    else:
        signals['bollinger'] = 'neutral'

    price = latest['Close']
    if price > latest['ema9'] > latest['ema21']:
        signals['ema'] = 'buy'
        score += 1
        reasons.append("Price > EMA9 > EMA21")
    elif price < latest['ema9'] < latest['ema21']:
        signals['ema'] = 'sell'
        score -= 1
    else:
        signals['ema'] = 'neutral'

    vol_ratio = latest['vol_ratio']
    if vol_ratio > 1.5:
        signals['volume'] = 'spike'
        score += 1
        reasons.append(f"Volume spike {vol_ratio:.1f}x")
    else:
        signals['volume'] = 'normal'

    # VWAP — most important intraday signal
    vwap = latest['vwap']
    if price > vwap * 1.001:        # price at least 0.1% above VWAP
        signals['vwap'] = 'buy'
        score += 2
        reasons.append(f"Price above VWAP (₹{vwap:.2f})")
    elif price < vwap * 0.999:
        signals['vwap'] = 'sell'
        score -= 1
    else:
        signals['vwap'] = 'neutral'

    return {
        'score':      score,
        'signals':    signals,
        'reasons':    reasons,
        'price':      price,
        'atr':        latest['atr'],
        'rsi':        rsi,
        'vwap':       vwap,
        'vol_ratio':  float(latest.get('vol_ratio', 0) or 0),
    }


def _price_decimals(price):
    """Decimal places that preserve enough precision for SL/TGT at this price level."""
    if price < 0.001:  return 8
    if price < 0.01:   return 7
    if price < 0.1:    return 6
    if price < 1.0:    return 5
    if price < 10.0:   return 4
    if price < 100.0:  return 3
    return 2

def calc_stop_loss(price, atr=None, volatility_factor=0.8, sl_pct=None):
    if sl_pct is None:
        from config import SCALP_SL_PCT
        sl_pct = SCALP_SL_PCT
    return round(price * (1 - sl_pct), _price_decimals(price))

def calc_target(price, atr=None, reward_factor=1.2, target_pct=None):
    if target_pct is None:
        from config import SCALP_TARGET_PCT
        target_pct = SCALP_TARGET_PCT
    return round(price * (1 + target_pct), _price_decimals(price))

if __name__ == "__main__":
    import sys
    sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
    from data.fetcher import get_historical
    df = get_historical("RELIANCE", period="6mo")
    df = compute_indicators(df)
    result = generate_signals(df)
    print(f"Score: {result['score']}")
    print(f"Price: ₹{result['price']:.2f}")
    print(f"Signals: {result['signals']}")
    print(f"Reasons: {result['reasons']}")
    sl = calc_stop_loss(result['price'], result['atr'])
    tgt = calc_target(result['price'], result['atr'])
    print(f"Stop-Loss: ₹{sl:.2f}  |  Target: ₹{tgt:.2f}")
