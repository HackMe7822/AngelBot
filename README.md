# AngelBot

Automated trading bot for Angel One (India NSE), Alpaca (US), and Binance (Crypto).  
Runs as four Windows services with a web portal at `https://trading.creationsit.com`.

---

## Architecture

| Service | Script | Purpose |
|---|---|---|
| AngelBot-India | `india_worker.py` | NSE/BSE live trading (Angel One) |
| AngelBot-US | `us_worker.py` | US paper/live trading (Alpaca) |
| AngelBot-Crypto | `crypto_worker.py` | Crypto paper/live trading (Binance) |
| AngelBot-Portal | `portal_worker.py` | Web portal on port 8080 |

**Stack:** Python 3.11 · SQL Server Express (instance `ANGELBOT`) · NSSM · IIS (80→8080) · Cloudflare tunnel

> **Port note:** AngelBot-Portal binds **port 8080**. If Caddy (Nextcloud/`files.creationsit.com`) is also running on this server it **must** use port **8081** — edit `Caddyfile` and change `:8080` to `:8081` before starting Caddy, then add a separate `files.creationsit.com → http://localhost:8081` ingress rule to `cloudflared`'s `config.yml`.

---

## Fresh Install

Run in **admin PowerShell**:

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
iex (irm 'https://raw.githubusercontent.com/HackMe7822/AngelBot/master/install.ps1')
```

The script installs everything automatically:
- Python 3.11, Git, NSSM
- SQL Server 2019 Express (instance: `ANGELBOT`)
- ODBC Driver 17 + Python packages
- IIS reverse proxy (port 80 → 8080)
- Cloudflare tunnel (`trading.creationsit.com`)
  - If a `cloudflared` service already exists (e.g. shared with MeshCentral/WdpMgr), it patches the existing config — no second tunnel created
  - If no cloudflared service exists, prompts for quick tunnel or named tunnel setup

After install, open the portal and go to **API Keys** to enter credentials.

---

## Update (code only)

```powershell
cd C:\AngelBot
git pull origin main
.\update.ps1   # restarts AngelBot-Portal
```

To restart all services:

```powershell
Restart-Service AngelBot-India, AngelBot-US, AngelBot-Crypto, AngelBot-Portal
```

---

## Server Migration

### Step 1 — On the OLD server (admin PowerShell)

```powershell
# Stop all services
Stop-Service AngelBot-India, AngelBot-US, AngelBot-Crypto, AngelBot-Portal -Force

# Backup SQL database (SQL Server SA can't write to C:\ root — use the MSSQL backup dir)
$bakDir = "C:\Program Files\Microsoft SQL Server\MSSQL15.ANGELBOT\MSSQL\Backup"
sqlcmd -S .\ANGELBOT -Q "BACKUP DATABASE angelbot TO DISK='$bakDir\angelbot_migration.bak' WITH FORMAT, INIT"

# Zip learning data
Compress-Archive -Path "C:\AngelBot\learning\" -DestinationPath "$bakDir\angelbot_learning.zip" -Force
```

**Copy these three files to the new server:**
| File | Contains |
|---|---|
| `C:\AngelBot\.env` | All API keys, SA password, trading config |
| `...\MSSQL\Backup\angelbot_migration.bak` | Full SQL database (trades, signals, config, users) |
| `...\MSSQL\Backup\angelbot_learning.zip` | ML learning data |

Transfer via USB, shared folder, or SCP. The `.env` is the most critical — without it you lose all API keys.

---

### Step 2 — On the NEW server (admin PowerShell)

**2a. Pre-place the `.env` before running the installer** (so it survives the git clone):

```powershell
New-Item -ItemType Directory -Force -Path "C:\AngelBot"
# Copy your .env file here now:
#   Copy-Item "\\oldserver\C$\AngelBot\.env" "C:\AngelBot\.env"
```

**2b. Run the installer:**

```powershell
Set-ExecutionPolicy Bypass -Scope Process -Force
iex (irm 'https://raw.githubusercontent.com/HackMe7822/AngelBot/master/install.ps1')
```

The installer will:
- Detect the existing `cloudflared` service and add `trading.creationsit.com` to the tunnel config automatically
- Restore your `.env` (it backs it up before any git operations)
- Install SQL Server, create a fresh `angelbot` database
- Start all four services

**2c. After the installer finishes — restore the real database:**

```powershell
# Stop services so nothing is writing to DB during restore
Stop-Service AngelBot-India, AngelBot-US, AngelBot-Crypto, AngelBot-Portal -Force

# Restore backup (place angelbot_migration.bak in the MSSQL backup dir first)
$bakDir = "C:\Program Files\Microsoft SQL Server\MSSQL15.ANGELBOT\MSSQL\Backup"
# Copy-Item "\\oldserver\..." "$bakDir\angelbot_migration.bak"
sqlcmd -S .\ANGELBOT -Q "RESTORE DATABASE angelbot FROM DISK='$bakDir\angelbot_migration.bak' WITH REPLACE"

# Restore learning data
Expand-Archive -Path "$bakDir\angelbot_learning.zip" -DestinationPath "C:\AngelBot\learning\" -Force

# Restart all services
Start-Service AngelBot-India, AngelBot-US, AngelBot-Crypto, AngelBot-Portal
```

**2d. Verify:**

```powershell
Get-Service AngelBot-India, AngelBot-US, AngelBot-Crypto, AngelBot-Portal | Select-Object Name, Status
```

All four should show `Running`. Open `https://trading.creationsit.com` — log in with your portal credentials.

---

### Cloudflare DNS

The installer runs `cloudflared tunnel route dns <tunnelId> trading.creationsit.com` automatically.  
If it doesn't (no cert.pem / not logged in), update the CNAME manually in the Cloudflare dashboard:

```
trading.creationsit.com  CNAME  f3dbfee9-854c-4f1b-9f60-76fce34c561e.cfargotunnel.com
```

---

## Configuration

All config lives in `C:\AngelBot\.env`. Edit it directly or via the portal's API Keys tab.

| Key | Description |
|---|---|
| `ANGEL_API_KEY` / `ANGEL_SECRET` | Angel One trading credentials |
| `ALPACA_KEY` / `ALPACA_SECRET` | Alpaca credentials |
| `BINANCE_KEY` / `BINANCE_SECRET` | Binance credentials |
| `PAPER_MODE` | `true` = paper trading only (never set false via portal) |
| `PORTAL_PASS` | Web portal admin password |
| `SQL_SA_PASS` | SQL Server SA password |
| `INDIA_CAPITAL` / `US_CAPITAL` / `CRYPTO_CAPITAL` | Capital per market (INR/USD) |

---

## Logs

```powershell
# Tail any service log
Get-Content C:\AngelBot\logs\AngelBot-Portal.log -Tail 50 -Wait

# All logs location
ls C:\AngelBot\logs\
```

---

## Useful Commands

```powershell
# Service status
Get-Service AngelBot-* | Select-Object Name, Status

# Restart one service
Restart-Service AngelBot-Portal

# Connect to SQL Server
sqlcmd -S .\ANGELBOT -d angelbot -E

# Check recent trades
sqlcmd -S .\ANGELBOT -d angelbot -Q "SELECT TOP 20 symbol, status, pnl, created_at FROM trades ORDER BY created_at DESC"
```
