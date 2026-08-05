"""Auto-login to XHS via Playwright browser — captures full cookies including web_session"""
import os, sys, time
sys.path.insert(0, os.path.dirname(__file__))

# Generate QR login via API
from apis.xhs_pc_login_apis import XHSLoginApi
login = XHSLoginApi()

print('[1/3] Generating QR code...')
cookies = login.generate_init_cookies()
success, msg, qr_data = login.generate_qrcode(cookies)
if not success:
    print(f'FAILED: {msg}')
    exit(1)

cookies = qr_data['cookies']
qr_url = qr_data['qr_url']
print(f'QR URL: {qr_url}')
login.show_qrcode_image(qr_url)

print('[2/3] Waiting for scan... (open the QR image and scan with XHS app)')
while True:
    success, msg, cookies = login.check_qrcode_status(qr_data['qr_id'], qr_data['code'], cookies)
    if success:
        print(f'Scanned! {msg}')
        break
    if 'expired' in msg.lower() or '过期' in msg:
        print('QR expired, restarting...')
        exit(1)
    time.sleep(2)

print('[3/3] Verifying session...')
success, user_info, cookies = login.get_user_info(cookies)
if success:
    print(f'User: {user_info.get("nickname", "?")} (RedID: {user_info.get("red_id", "?")})')

cookies_str = login.cookies_to_str(cookies)

# Save to .env
env_path = os.path.join(os.path.dirname(__file__), ".env")
with open(env_path, "w", encoding="utf-8") as f:
    f.write(f"COOKIES={cookies_str}\n")

print(f'\nCookies saved to .env ({len(cookies_str)} chars)')
print(f'Has web_session: {"web_session" in cookies_str}')
print(f'Has a1: {"a1=" in cookies_str}')
print('\nCookie keys:', [k for k in cookies.keys()])
