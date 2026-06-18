import requests, re, sys, os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

_MAX_NTFY = 4000

def _strip_html(text):
    text = re.sub(r'<b>(.*?)</b>', r'\1', text, flags=re.DOTALL)
    text = re.sub(r'<i>(.*?)</i>', r'\1', text, flags=re.DOTALL)
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

def _get_config():
    topic = server = token = ''
    try:
        import config as cfg
        topic  = getattr(cfg, 'NTFY_TOPIC',  '') or ''
        server = getattr(cfg, 'NTFY_SERVER', '') or ''
        token  = getattr(cfg, 'NTFY_TOKEN',  '') or ''
    except Exception:
        pass
    topic  = os.getenv('NTFY_TOPIC',  topic).strip()
    server = os.getenv('NTFY_SERVER', server).strip() or 'https://ntfy.sh'
    token  = os.getenv('NTFY_TOKEN',  token).strip()
    return topic, server.rstrip('/'), token

def send(message, title='AngelBot', priority='default', tags=None):
    """Send a push notification via ntfy.sh (or self-hosted ntfy server)."""
    topic, server, token = _get_config()
    if not topic:
        return False
    if not _alert_enabled('NTFY_ALERTS_ENABLED'):
        return False

    plain = _strip_html(message)
    if len(plain) > _MAX_NTFY:
        plain = plain[:_MAX_NTFY - 3] + '...'

    headers = {'Title': title, 'Priority': priority}
    if tags:
        headers['Tags'] = ','.join(tags) if isinstance(tags, list) else tags
    if token:
        headers['Authorization'] = f'Bearer {token}'

    try:
        r = requests.post(
            f'{server}/{topic}',
            data=plain.encode('utf-8'),
            headers=headers,
            timeout=10
        )
        return r.status_code in (200, 201, 204)
    except Exception as e:
        print(f"ntfy error: {e}")
        return False


if __name__ == "__main__":
    ok = send(
        "AngelBot ntfy connected! Paper trading mode ON.",
        title="AngelBot Test",
        tags=["white_check_mark"]
    )
    print("ntfy test:", "OK" if ok else "FAILED — set NTFY_TOPIC in .env or Settings")
