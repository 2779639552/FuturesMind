"""Generate XHS QR code for login, save cookies to .env"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))

from apis.xhs_pc_login_apis import XHSLoginApi

print("Generating QR code for XHS login...")
login = XHSLoginApi()
try:
    cookies_str = login.qrcode_login(show_in_terminal=False)
    if cookies_str:
        env_path = os.path.join(os.path.dirname(__file__), ".env")
        with open(env_path, "w", encoding="utf-8") as f:
            f.write(f"COOKIES={cookies_str}\n")
        print(f"\nSUCCESS! Cookie saved to {env_path}")
        print(f"Cookie length: {len(cookies_str)} chars")
    else:
        print("\nFAILED: Login timed out or failed")
except Exception as e:
    print(f"\nERROR: {e}")
