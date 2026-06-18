import requests, re, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_MAX_WA = 4000  # practical limit for both providers

def _strip_html(text):
    """Convert HTML formatting to WhatsApp-compatible plain text."""
    text = re.sub(r'<b>(.*?)</b>', r'*\1*', text, flags=re.DOTALL)
    text = re.sub(r'<i>(.*?)</i>', r'_\1_', text, flags=re.DOTALL)
    text = re.sub(r'<[^>]+>', '', text)
    return (text.replace('&amp;', '&').replace('&lt;', '<')
                .replace('&gt;', '>').replace('&nbsp;', ' ')
                .replace('&#8209;', '-'))

def _alert_enabled(key):
    try:
        from data.database import get_conn
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT setting_value FROM bot_settings WHERE setting_key=?", [key])
        row = c.fetchone()
        conn.close()
        if row and row[0] is not None:
            return str(row[0]).lower() not in ('false', '0', 'no')
    except Exception:
        pass
    return True

def _get_ultramsg_creds():
    instance = token = phone = ''
    try:
        import config as cfg
        instance = getattr(cfg, 'ULTRAMSG_INSTANCE', '') or ''
        token    = getattr(cfg, 'ULTRAMSG_TOKEN',    '') or ''
        phone    = getattr(cfg, 'WHATSAPP_PHONE',    '') or ''
    except Exception:
        pass
    return (
        os.getenv('ULTRAMSG_INSTANCE', instance).strip(),
        os.getenv('ULTRAMSG_TOKEN',    token).strip(),
        os.getenv('WHATSAPP_PHONE',    phone).strip(),
    )

def _get_callmebot_creds():
    phone = apikey = ''
    try:
        import config as cfg
        phone  = getattr(cfg, 'WHATSAPP_PHONE',   '') or ''
        apikey = getattr(cfg, 'WHATSAPP_API_KEY',  '') or ''
    except Exception:
        pass
    return (os.getenv('WHATSAPP_PHONE',   phone).strip(),
            os.getenv('WHATSAPP_API_KEY', apikey).strip())

def _send_ultramsg(plain, instance, token, phone):
    """Send via UltraMsg API — works immediately after QR scan, no approval needed."""
    # UltraMsg expects phone without + but with country code, or with @c.us suffix
    to = phone.lstrip('+')
    if '@' not in to:
        to = to + '@c.us'
    try:
        r = requests.post(
            f'https://api.ultramsg.com/{instance}/messages/chat',
            data={'token': token, 'to': to, 'body': plain},
            timeout=15
        )
        result = r.json() if r.content else {}
        return result.get('sent') == 'true' or r.status_code == 200
    except Exception as e:
        print(f"UltraMsg error: {e}")
        return False

def _send_callmebot(plain, phone, apikey):
    """Send via CallMeBot — requires API key (received by WhatsApp message)."""
    try:
        r = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": phone, "text": plain, "apikey": apikey},
            timeout=15
        )
        return r.status_code == 200
    except Exception as e:
        print(f"WhatsApp (CallMeBot) error: {e}")
        return False

def send(message):
    """Send WhatsApp message — tries UltraMsg first, falls back to CallMeBot."""
    if not _alert_enabled('WHATSAPP_ALERTS_ENABLED'):
        return False
    plain = _strip_html(message)
    if len(plain) > _MAX_WA:
        plain = plain[:_MAX_WA - 3] + '...'

    # UltraMsg: preferred (works immediately after scanning QR code)
    instance, um_token, phone = _get_ultramsg_creds()
    if instance and um_token and phone:
        return _send_ultramsg(plain, instance, um_token, phone)

    # CallMeBot: fallback (requires API key approval)
    cb_phone, apikey = _get_callmebot_creds()
    if cb_phone and apikey:
        return _send_callmebot(plain, cb_phone, apikey)

    return False


if __name__ == "__main__":
    ok = send("AngelBot WhatsApp connected! Paper trading mode ON.")
    print("WhatsApp test:", "OK" if ok else "FAILED — set ULTRAMSG_INSTANCE+ULTRAMSG_TOKEN or WHATSAPP_PHONE+WHATSAPP_API_KEY in .env")
