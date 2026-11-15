"""รวบรวมชื่อไทยของปลาที่พบในประเทศไทย
1) รายชื่อปลาที่มีบันทึกในไทยจาก GBIF
2) ชื่อไทย/ชื่อท้องถิ่นจาก thbif.onep.go.th (ข้อมูลกรมประมง)
3) ตัวที่ไม่มีชื่อไทย -> ค้นชื่อวิทยาศาสตร์ใน Wikidata/วิกิพีเดียไทย
ผลลัพธ์: data/fish_th.json และ data/build_log.txt
"""
import json, os, re, time, html, urllib.request, urllib.parse
from concurrent.futures import ThreadPoolExecutor

LOG = []
def log(*a):
    s = ' '.join(str(x) for x in a); print(s, flush=True); LOG.append(s)

UA = {'User-Agent': 'fishing-app-thai-names/1.0 (github.com/ItsPerth888/fishing-app)'}
def get(url, tries=3, timeout=30):
    for i in range(tries):
        try:
            req = urllib.request.Request(url, headers=UA)
            with urllib.request.urlopen(req, timeout=timeout) as r:
                return r.read().decode('utf-8', 'replace')
        except Exception as e:
            err = e; time.sleep(1.5 * (i + 1))
    raise err

# ---------- 1) GBIF: ปลาที่มีบันทึกในไทย ----------
def gbif_species():
    keys = {}
    # หา "อันดับ" (order) ของปลาทั้งหมดที่มีบันทึกในไทย (GBIF ใหม่ ปลากระดูกแข็งอาจไม่มี class)
    taxa = []
    FISHY = re.compile(r'Actinopter|Teleostei|Elasmobranch|Holocephal|Chondrichthy|Sarcopteryg|Petromyzon|Myxini|Cephalaspidomorph|Dipnoi|Dipneusti', re.I)
    TETRA = re.compile(r'Aves|Mammalia|Amphibia|Squamata|Testudines|Crocodylia|Reptilia|Ascidiacea|Thaliacea|Leptocardii|Appendicularia', re.I)
    j = json.loads(get('https://api.gbif.org/v1/occurrence/search?country=TH&taxonKey=44&limit=0&facet=orderKey&facetLimit=400'))
    for c in (j.get('facets') or [{}])[0].get('counts', []):
        try:
            d = json.loads(get('https://api.gbif.org/v1/species/' + c['name']))
        except Exception:
            continue
        cls = d.get('class') or ''
        keep = bool(FISHY.search(cls)) or (not cls and not TETRA.search(json.dumps(d.get('higherClassificationMap', {}))))
        if keep:
            taxa.append(c['name']); log('fish order', d.get('canonicalName'), '| class:', cls or '-', '|', c['count'])
    log('fish orders:', len(taxa))
    for taxon in taxa:
        off = 0
        while True:
            u = ('https://api.gbif.org/v1/occurrence/search?country=TH&taxonKey=%s&limit=0'
                 '&facet=speciesKey&facetLimit=1000&facetOffset=%d' % (taxon, off))
            j = json.loads(get(u))
            counts = (j.get('facets') or [{}])[0].get('counts', [])
            for c in counts: keys[c['name']] = c['count']
            log('GBIF taxon', taxon, 'offset', off, '->', len(counts))
            if len(counts) < 1000: break
            off += 1000
    log('GBIF species total:', len(keys))
    def detail(k):
        try:
            s = json.loads(get('https://api.gbif.org/v1/species/' + k))
            return {'key': int(k), 'sci': s.get('canonicalName') or s.get('scientificName'),
                    'family': s.get('family'), 'n': keys[k]}
        except Exception as e:
            return None
    with ThreadPoolExecutor(10) as ex:
        out = [d for d in ex.map(detail, keys) if d and d['sci']]
    return out

# ---------- 2) thbif ----------
LABELS = ('ชื่อวิทยาศาสตร์', 'Status', 'Synonyms', 'ชื่อสามัญ', 'ชื่อไทย', 'ชื่อท้องถิ่น', 'ปีที่ตีพิมพ์',
          'อาณาจักร', 'ไฟลัม', 'ชั้น', 'อันดับ', 'วงศ์', 'สกุล', 'ที่มา', 'วันที่')
def page_text(h):
    h = re.sub(r'(?is)<(script|style).*?</\1>', ' ', h)
    h = re.sub(r'(?i)<br\s*/?>|</(li|p|div|h\d|dd|dt|tr|ul)>', '\n', h)
    t = html.unescape(re.sub(r'<[^>]+>', ' ', h))
    return [re.sub(r'\s+', ' ', l).strip() for l in t.split('\n') if l.strip()]
def grab(lines, label):
    out = []
    for i, l in enumerate(lines):
        if l.startswith(label):
            rest = l.split(':', 1)[1] if ':' in l else ''
            rest = rest.strip(' :')
            seg = [rest] if rest else []
            for m in lines[i + 1:i + 15]:
                if any(m.startswith(x) for x in LABELS): break
                seg.append(m)
            for s in seg:
                for part in re.split(r'\s+-\s+|^-\s*|,|;', s):
                    p = part.strip(' -:')
                    if p and p not in out and len(p) < 60: out.append(p)
            break
    return out
SAMPLES = []
def thbif(sci):
    url = 'https://thbif.onep.go.th/taxons/taxon_detail/' + urllib.parse.quote(sci)
    try:
        lines = page_text(get(url, tries=2, timeout=25))
    except Exception as e:
        return None, 'err:' + type(e).__name__
    th = [x for x in grab(lines, 'ชื่อไทย') if re.search('[ก-๙]', x)]
    loc = grab(lines, 'ชื่อท้องถิ่น')
    if len(SAMPLES) < 3 and (th or loc): SAMPLES.append((sci, th, loc))
    return {'th': th, 'local': loc}, 'ok'

# ---------- 3) Wikidata / วิกิพีเดียไทย ----------
def wikidata(names):
    found = {}
    for i in range(0, len(names), 150):
        part = names[i:i + 150]
        vals = ' '.join('"%s"' % n.replace('"', '') for n in part)
        q = ('SELECT ?name ?th ?cn WHERE { VALUES ?name { %s } ?item wdt:P225 ?name . '
             'OPTIONAL { ?a schema:about ?item ; schema:isPartOf <https://th.wikipedia.org/> ; schema:name ?th . } '
             'OPTIONAL { ?item wdt:P1843 ?cn . FILTER(LANG(?cn)="th") } }') % vals
        u = 'https://query.wikidata.org/sparql?format=json&query=' + urllib.parse.quote(q)
        try:
            j = json.loads(get(u, timeout=60))
            for b in j['results']['bindings']:
                n = b['name']['value']
                for k in ('th', 'cn'):
                    if k in b:
                        v = b[k]['value']
                        found.setdefault(n, [])
                        if v not in found[n]: found[n].append(v)
        except Exception as e:
            log('wikidata batch error', i, type(e).__name__)
        time.sleep(1)
    return found

BUDGET = 270 * 60  # วินาที: หยุดเองก่อนหมดเวลาของ GitHub แล้วรอบหน้าทำต่อ
def save(sp, done):
    out = [{'k': s['key'], 's': s['sci'], 'f': s['family'], 'n': s['n'], 't': s.get('th', []), 'l': s.get('local', []),
            'src': s.get('src', ''), 'd': 1 if s.get('done') else 0, 'r': s.get('retry', 0)} for s in sp]
    json.dump(out, open('data/fish_th.json', 'w', encoding='utf-8'), ensure_ascii=False, separators=(',', ':'))
    if done:
        if os.path.exists('data/INCOMPLETE'): os.remove('data/INCOMPLETE')
    else:
        open('data/INCOMPLETE', 'w').write('resume\n')

def main():
    os.makedirs('data', exist_ok=True)
    t0 = time.time()
    # ผลจากรอบก่อน (ทำต่อจากที่ค้าง)
    prev = {}
    try:
        for x in json.load(open('data/fish_th.json', encoding='utf-8')):
            prev[x['k']] = x
    except Exception:
        pass
    log('resume: already done', sum(1 for x in prev.values() if x.get('d')))
    sp = gbif_species()
    sp.sort(key=lambda s: -s['n'])  # ปลาที่พบบ่อยก่อน
    log('species with names:', len(sp))
    for s in sp:
        p = prev.get(s['key'])
        if p and p.get('d'): s.update(th=p['t'], local=p['l'], src=p['src'], done=True)
        elif p: s['retry'] = p.get('r', 0)
    todo = [s for s in sp if not s.get('done')]
    log('todo thbif:', len(todo))
    stats = {'ok': 0, 'err': 0, 'named': 0}
    def one(s):
        if time.time() - t0 > BUDGET: return s, None, 'skip'
        r, st = thbif(s['sci']); time.sleep(0.2)
        return s, r, st
    finished = True
    from concurrent.futures import as_completed
    with ThreadPoolExecutor(8) as ex:
        futs = [ex.submit(one, s) for s in todo]
        for i, f in enumerate(as_completed(futs), 1):
            s, r, st = f.result()
            if st == 'skip': finished = False; continue
            if st == 'ok':
                stats['ok'] += 1
                s.update(th=r['th'], local=r['local'], src='thbif' if (r['th'] or r['local']) else '', done=True)
                if r['th']: stats['named'] += 1
            else:
                stats['err'] += 1
                s.update(th=[], local=[], src='', done=s.get('retry', 0) >= 1)  # error ครั้งที่ 2 ถือว่าจบ
                s['retry'] = s.get('retry', 0) + 1
            if i % 200 == 0:
                save(sp, False); log('progress', i, '/', len(todo), stats, '%.0fs' % (time.time() - t0))
    log('thbif:', stats, 'finished' if finished else 'stopped by time budget')
    for x in SAMPLES: log('sample', x)
    # วิกิ: เฉพาะตัวที่ยังไม่มีชื่อไทย
    missing = [s['sci'] for s in sp if s.get('done') and not s.get('th') and 'wiki' not in s.get('src', '')]
    log('no Thai name -> wikidata:', len(missing))
    wd = wikidata(missing) if missing and time.time() - t0 < BUDGET + 600 else {}
    for s in sp:
        if not s.get('th') and s['sci'] in wd:
            s['th'] = wd[s['sci']]; s['src'] = (s.get('src', '') + '+wiki').strip('+')
    all_done = finished and all(s.get('done') for s in sp)
    save(sp, all_done)
    log('with Thai name:', sum(1 for s in sp if s.get('th')), 'of', len(sp), '| done', sum(1 for s in sp if s.get('done')),
        '| complete' if all_done else '| INCOMPLETE (next run continues)', '| time %.0fs' % (time.time() - t0))

try:
    main()
finally:
    import os; os.makedirs('data', exist_ok=True)
    open('data/build_log.txt', 'w', encoding='utf-8').write('\n'.join(LOG) + '\n')
