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

# ⚠️ 关键闸门:没有 web_session 就不是有效登录态。
#    旧版会照写 .env 并打印"Cookies saved",把风控拦截伪装成成功(见引擎里的 471 注释),
#    于是后续采集全部报"未登录",却看着像登录过了。
if 'web_session' not in cookies:
    err = getattr(login, 'last_login_error', '') or '未知原因'
    print()
    print('=' * 60)
    print('❌ 登录失败:拿到的 cookie 里没有 web_session')
    print(f'   原因: {err}')
    print(f'   已拒绝写入 .env(避免把无效登录态当成有效)')
    print(f'   cookie keys: {[k for k in cookies.keys()]}')
    print('=' * 60)
    sys.exit(2)

env_path = os.path.join(os.path.dirname(__file__), ".env")
with open(env_path, "w", encoding="utf-8") as f:
    f.write(f"COOKIES={cookies_str}\n")

print(f'\nCookies saved to .env ({len(cookies_str)} chars)')
print(f'Has web_session: True')
print(f'Has a1: {"a1=" in cookies_str}')
print('\nCookie keys:', [k for k in cookies.keys()])
