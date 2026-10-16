# -*- coding: utf-8 -*-
"""端到端验证：走「直接选文件」同一条链路（upload-folder，multipart）。"""
import io, json, os, sys, time, glob
import urllib.request

NO_PROXY = urllib.request.build_opener(urllib.request.ProxyHandler({}))
BASE = 'http://127.0.0.1:5000'

# ⚠️ 该目录名含非 UTF-8 原编码的乱码字符，硬写路径必失败 ⇒ 用 glob 按文件名特征定位
_hits = glob.glob(r'D:\AgentSense\handoff_20260909_tmp\_machine_local'
                  r'\tradingagents\research_reports\**\*.pdf', recursive=True)
_c = [p for p in _hits if '沥青裂解价差高位' in p and os.path.getsize(p) < 20 * 1048576]
assert _c, 'not found; candidates=%d' % len(_hits)
SRC = _c[0]
assert os.path.isfile(SRC), 'source not found: %s' % SRC
size = os.path.getsize(SRC)
print('源文件 %.2f MB' % (size / 1048576.0))
print('  ', os.path.basename(SRC))

BOUND = '----agentsenseE2E%d' % int(time.time())
name = 'e2e_folderfix_%d.pdf' % int(time.time())


def part(field, filename, data):
    b = io.BytesIO()
    b.write(('--%s\r\n' % BOUND).encode())
    b.write(('Content-Disposition: form-data; name="%s"; filename="%s"\r\n'
             % (field, filename)).encode('utf-8'))
    b.write(b'Content-Type: application/pdf\r\n\r\n')
    b.write(data)
    b.write(b'\r\n')
    return b.getvalue()


body = part('files', name, io.open(SRC, 'rb').read())
body += ('--%s--\r\n' % BOUND).encode()

req = urllib.request.Request(BASE + '/api/research/upload-folder', data=body, method='POST')
req.add_header('Content-Type', 'multipart/form-data; boundary=%s' % BOUND)
try:
    r = NO_PROXY.open(req, timeout=120)
    d = json.loads(r.read().decode('utf-8'))
    print('HTTP', r.status, json.dumps(d, ensure_ascii=False)[:300])
except urllib.error.HTTPError as e:
    print('HTTP', e.code, e.read().decode('utf-8', 'ignore')[:400])
    sys.exit(1)
except Exception as e:
    print('ERR', type(e).__name__, e)
    sys.exit(1)

print('E2E_MARKER=%s' % name)
