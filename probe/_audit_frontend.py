import io,re
s=io.open('web_template.html',encoding='utf-8').read()
print('文件大小(KB)', round(len(s.encode('utf-8'))/1024))
print('内联 style= 次数', len(re.findall(r'\bstyle="',s)))
print('!important 次数', s.count('!important'))
print('hex 颜色字面量个数', len(re.findall(r'#[0-9a-fA-F]{6}\b',s)))
print('rgba( 次数', s.count('rgba('))
print('class="card" 次数', len(re.findall(r'class="card"',s)))
print('class="expander" 次数', len(re.findall(r'class="expander"',s)))
print('font-size 去重值', sorted(set(re.findall(r'font-size:/s*([0-9.]+px|[a-z0-9.]+rem|var/([^)]*/))',s))))
print('border-radius 去重', sorted(set(re.findall(r'border-radius:/s*([0-9.]+px|var/([^)]*/)|50%)',s))))
print('<table 个数', s.count('<table'))
print('<h3 个数', s.count('<h3'))
print('内联 onclick 次数', len(re.findall(r'onclick="',s)))
TABS=['run','research','quant','fund','sentiment','foreign','warehouse']
pos=[(t,s.find('id="tab-%s"'%t)) for t in TABS]
for k,(t,i) in enumerate(pos):
    j=pos[k+1][1] if k+1<len(pos) else s.find('</main>')
    seg=s[i:j]
    print('tab-%-10s cards=%-3d h3=%-3d tables=%-3d bytes=%d' % (t, len(re.findall(r'class="card"',seg)), seg.count('<h3'), seg.count('<table'), len(seg)))
