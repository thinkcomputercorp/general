# usage: cap.py reporter volume first_page outname  -> writes text with [*page] markers
import json, re, sys, html, subprocess
get = lambda u: subprocess.run(["curl", "-sS", "-f", u], capture_output=True, check=True).stdout
rep, vol, page, out = sys.argv[1:5]
meta = json.loads(get(f'https://static.case.law/{rep}/{vol}/CasesMetadata.json'))
c = next((c for c in meta if c['first_page'] == page), None)
if not c:
    cands = [m for m in meta if int(m['first_page']) <= int(page) <= int(m['last_page'])]
    c = cands[0] if cands else None
if not c:
    sys.exit('not found')
fn = c['file_name']
h = get(f'https://static.case.law/{rep}/{vol}/html/{fn}.html').decode()
h = re.sub(r'<a[^>]*id="p(\d+)"[^>]*>.*?</a>', lambda m: f' [*{m.group(1)}] ', h, flags=re.S)
h = re.sub(r'<(p|h\d|div|blockquote|aside|section)[^>]*>', '\n', h)
t = html.unescape(re.sub(r'<[^>]+>', '', h))
t = re.sub(r'\n\s*\n+', '\n', t)
open(out, 'w').write(c['name_abbreviation'] + ' | ' + c['decision_date'] + ' | ' + json.dumps(c['citations']) + '\n' + t)
print(out, c['name_abbreviation'], c['decision_date'], c['first_page'], c['last_page'], len(t))
