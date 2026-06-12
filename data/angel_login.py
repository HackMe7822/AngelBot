import pyotp
import sys
import os
import time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))
from config import ANGEL_API_KEY, ANGEL_CLIENT_ID, ANGEL_PIN, ANGEL_TOTP_SECRET
from SmartApi import SmartConnect

_smart_api   = None
_login_time  = None
_feed_token  = None   # required by SmartWebSocketV2
_auth_token  = None   # JWT token required by SmartWebSocketV2
_SESSION_TTL = 8 * 3600   # Angel One tokens expire at midnight IST; refresh every 8h

def get_api():
    global _smart_api, _login_time
    if _smart_api and _login_time and (time.time() - _login_time) < _SESSION_TTL:
        return _smart_api
    _smart_api  = login()
    _login_time = time.time()
    return _smart_api

def force_relogin():
    """Force a fresh login — call when Angel One returns AG8001 Invalid Token."""
    global _smart_api, _login_time, _feed_token, _auth_token
    _smart_api  = None
    _login_time = None
    _feed_token = None
    _auth_token = None
    return get_api()

def get_feed_token():
    get_api()
    return _feed_token

def get_auth_token():
    get_api()
    return _auth_token

def login():
    global _feed_token, _auth_token
    totp = pyotp.TOTP(ANGEL_TOTP_SECRET).now()
    smart = SmartConnect(api_key=ANGEL_API_KEY)
    data = smart.generateSession(ANGEL_CLIENT_ID, ANGEL_PIN, totp)
    if data['status']:
        _feed_token = data['data'].get('feedToken')
        _auth_token = data['data'].get('jwtToken')
        print(f"Login successful. Welcome {data['data']['name']}")
        return smart
    else:
        print(f"Login failed: {data['message']}")
        return None

if __name__ == "__main__":
    api = login()
    if api:
        profile = api.getProfile(api.refresh_token)
        print(f"Client: {profile['data']['name']}")
        print(f"Email:  {profile['data']['email']}")
        print(f"Broker: {profile['data']['broker']}")
