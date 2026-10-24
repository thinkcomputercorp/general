# usage: cls.py COURT|all 'raw query' [n]  -> CourtListener v4 opinion search with snippets
import json, subprocess, sys, re
court, q = sys.argv[1], sys.argv[2]
n = int(sys.argv[3]) if len(sys.argv) > 3 else 8
args = ['curl', '-sS', '-G', 'https://www.courtlistener.com/api/rest/v4/search/', '--data-urlencode', f'q={q}',
        '--data-urlencode', 'type=o', '--data-urlencode', 'highlight=on']
if court != 'all': args += ['--data-urlencode', f'court={court}']
d = json.loads(subprocess.run(args, capture_output=True, text=True).stdout)
if 'results' not in d: print(d); sys.exit()
print('count', d.get('count'))
for r in d['results'][:n]:
    snip = ' | '.join(re.sub(r'\s+', ' ', o.get('snippet') or '') for o in r.get('opinions', []))[:400]
    print('-', r['caseName'][:80], r['dateFiled'], r.get('citation'), 'cl', r['cluster_id'], '\n   ', snip)
