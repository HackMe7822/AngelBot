import requests, re, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_MAX_WA = 4000  # CallMeBot practical limit

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

def _get_creds():
    phone = apikey = ''
    try:
        import config as cfg
        phone  = getattr(cfg, 'WHATSAPP_PHONE',   '') or ''
        apikey = getattr(cfg, 'WHATSAPP_API_KEY',  '') or ''
    except Exception:
        pass
    return (os.getenv('WHATSAPP_PHONE',   phone).strip(),
            os.getenv('WHATSAPP_API_KEY', apikey).strip())

def send(message):
    """Send a plain-text WhatsApp message via CallMeBot. HTML tags are converted/stripped."""
    phone, apikey = _get_creds()
    if not phone or not apikey:
        return False
    if not _alert_enabled('WHATSAPP_ALERTS_ENABLED'):
        return False
    plain = _strip_html(message)
    if len(plain) > _MAX_WA:
        plain = plain[:_MAX_WA - 3] + '...'
    try:
        r = requests.get(
            "https://api.callmebot.com/whatsapp.php",
            params={"phone": phone, "text": plain, "apikey": apikey},
            timeout=15
        )
        return r.status_code == 200
    except Exception as e:
        print(f"WhatsApp error: {e}")
        return False


if __name__ == "__main__":
    ok = send("AngelBot WhatsApp connected! Paper trading mode ON.")
    print("WhatsApp test:", "OK" if ok else "FAILED — check WHATSAPP_PHONE and WHATSAPP_API_KEY in .env")
