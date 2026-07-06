"""
reset_portal_pass.py -- Emergency admin password reset for AngelBot portal.
Run on the server: python C:\AngelBot\reset_portal_pass.py
"""
import hashlib, getpass, sqlite3, os, sys

DB = os.path.join(os.path.dirname(__file__), "angelbot.db")

if not os.path.exists(DB):
    print(f"ERROR: Database not found at {DB}")
    sys.exit(1)

print("AngelBot Portal -- Admin Password Reset")
print("-" * 40)

username = input("Username to reset [admin]: ").strip() or "admin"
while True:
    pw = getpass.getpass("New password (min 8 chars): ")
    if len(pw) < 8:
        print("Password must be at least 8 characters.")
        continue
    pw2 = getpass.getpass("Confirm new password: ")
    if pw != pw2:
        print("Passwords do not match. Try again.")
        continue
    break

pw_hash = hashlib.sha256(pw.encode()).hexdigest()

con = sqlite3.connect(DB)
cur = con.cursor()
cur.execute("SELECT username FROM portal_users WHERE username=?", (username,))
row = cur.fetchone()
if not row:
    print(f"ERROR: User '{username}' not found in portal_users table.")
    con.close()
    sys.exit(1)

cur.execute("UPDATE portal_users SET password_hash=? WHERE username=?", (pw_hash, username))
con.commit()
con.close()

print(f"\nPassword for '{username}' updated successfully.")
print("You can now log in to the portal with the new password.")
