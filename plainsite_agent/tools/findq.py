# findq.py casefile "fragment" : prints match with nearest preceding page marker
import re, sys
t = open(sys.argv[1]).read()
norm = lambda s: re.sub(r'\s+', ' ', s.replace('’', "'").replace('‘', "'").replace('“', '"').replace('”', '"'))
T = norm(t)
for frag in sys.argv[2:]:
    f = norm(frag)
    i = T.lower().find(f.lower())
    if i < 0:
        print('NOT FOUND:', frag); continue
    pages = re.findall(r'\[\*(\d+)\]', T[:i])
    print(f'FOUND p.{pages[-1] if pages else "first"}:', T[max(0, i-120):i+len(f)+120].replace('\n', ' '))
