"""Visible browser collector. No private APIs, retries, or challenge bypass."""
import json
import re
import time
import sys
import unicodedata
from datetime import datetime, timezone
from itertools import product
from pathlib import Path
from urllib.parse import quote, urlencode
from optimizer import key, integer, solve

ROOT = Path(sys.executable).resolve().parent.parent if getattr(sys, 'frozen', False) else Path(__file__).resolve().parent
REQUEST_DELAY = 1.5
MAX_SEARCH_NAMES = 8
COMPAT_HANJA = {}
for start, end in ((0xF900, 0xFAFF), (0x2F800, 0x2FA1F)):
    for code in range(start, end + 1):
        character = chr(code)
        canonical = unicodedata.normalize('NFC', character)
        if len(canonical) == 1 and canonical != character:
            COMPAT_HANJA.setdefault(canonical, []).append(character)
RECIPE_EMPTY_TEXT = ('검색 결과가 없습니다', '아이템을 찾을 수 없습니다', '제작 정보를 찾을 수 없습니다', '제작 정보가 없습니다')

RECIPE_JS = r'''() => {
 const rows = [...document.querySelectorAll('[data-make-cost-material-row]')].map(r => ({
   name: r.querySelector('a[href^="/item/"]')?.textContent?.trim(),
   count: r.querySelector('input[data-col="count"]')?.value,
   mode: r.querySelector('[data-make-cost-inline-price]')?.getAttribute('data-make-cost-inline-price')
 }));
 const methods = [...document.querySelectorAll('button[aria-pressed]:not([aria-label])')];
 return {title: document.querySelector('h2')?.textContent?.trim(), rows,
   methods: methods.map(b=>b.innerText), selected: methods.find(b=>b.getAttribute('aria-pressed')==='true')?.innerText,
   body: document.body.innerText};
}'''

MARKET_JS = r'''() => {
 const rows = [...document.querySelectorAll('time[datetime]')].map(t => {
   let r = t.parentElement;
   while(r && r.children.length !== 5) r = r.parentElement;
   if(!r || !r.children[0].querySelector('button')) return null;
   const c=[...r.children];
   return {name:c[0].innerText.trim(), quantity:c[1].innerText.trim(),
     price:c[2].innerText.trim(), seller:c[3].innerText.trim(), listed:t.getAttribute('datetime')};
 }).filter(Boolean);
 const pages = [...document.querySelectorAll('a[href]')].filter(a=> /[?&]page=\d+/.test(a.href));
 return {rows, links:pages.map(a=>a.href), body:document.body.innerText};
}'''


def now():
    return datetime.now(timezone.utc).isoformat()


def search_names(name):
    names = dict.fromkeys([name, key(name)])
    choices = []
    for character in name:
        canonical = unicodedata.normalize('NFC', character)
        choices.append([canonical] + COMPAT_HANJA.get(canonical, []))
    # Limit combinations so names with several variant characters cannot flood the site.
    for characters in product(*choices):
        candidate = ''.join(characters)
        for spelling in (candidate, key(candidate)):
            names[spelling] = None
            if len(names) >= MAX_SEARCH_NAMES:
                return names
    return names


def same_name(left, right):
    # Normalize only comparisons; existing JSON keys must stay compatible.
    return unicodedata.normalize('NFC', key(left)) == unicodedata.normalize('NFC', key(right))


def duration_days(text):
    match = re.search(r'소요\s*기간\s*[|｜:：]?\s*(\d+)\s*일', text)
    return integer(int(match[1]), '소요기간', 0, 36500) if match else None


def populate_durations(item):
    changed = False
    for recipe in item.get('recipes', []):
        days = duration_days(recipe.get('method', ''))
        if 'durationDays' not in recipe or (days is not None and recipe['durationDays'] != days):
            recipe['durationDays'] = days
            changed = True
    return changed


def parse_recipe(raw, requested):
    if not same_name(raw.get('title') or '', requested):
        raise ValueError(f'{requested}: 제작법 제목을 확인할 수 없습니다.')
    method = raw.get('selected') or ''
    match = re.search(r'제작비\s*([\d,]+)', method)
    output_match = re.search(r'산출량\s*([\d,]+)\s*개', method)
    if not method or ('제작비' in method and not match) or not raw['rows']:
        raise ValueError(f'{requested}: 재료 또는 수수료 형식이 변경되었습니다.')
    if '산출량' in method and not output_match:
        raise ValueError(f'{requested}: 산출량을 확인할 수 없습니다.')
    materials, children = {}, {}
    for row in raw['rows']:
        if not row['name'] or not re.fullmatch(r'\d[\d,]*', row['count'] or ''):
            raise ValueError(f'{requested}: 재료 수량을 확인할 수 없습니다.')
        n = key(row['name']); count = int(row['count'].replace(',', ''))
        if count <= 0 or n in materials:
            raise ValueError(f'{requested}: 중복 또는 잘못된 재료 행입니다.')
        materials[n] = count
        children[n] = {'name': row['name'], 'craftable': row.get('mode') != 'single'}
    return {'materials': materials, 'fee': int(match[1].replace(',', '')) if match else 0,
            'output': int(output_match[1].replace(',', '')) if output_match else 1,
            'durationDays': duration_days(method),
            'method': method.replace('선택됨', '').strip()}, children


def parse_market(raw, requested, page):
    if '청룡' not in raw['body']:
        raise ValueError('청룡 서버 표시가 확인되지 않습니다.')
    offers = []
    for row in raw['rows']:
        if not same_name(row['name'], requested):
            continue
        if not re.fullmatch(r'\d[\d,]*', row['quantity']):
            raise ValueError('매물 수량 형식이 변경되었습니다.')
        match = re.fullmatch(r'([\d,]+)\s*냥', row['price'])
        if not match:
            raise ValueError('매물 가격 형식이 변경되었습니다.')
        qty, price = int(row['quantity'].replace(',', '')), int(match[1].replace(',', ''))
        if qty <= 0 or price <= 0:
            raise ValueError('유효하지 않은 매물입니다.')
        offers.append({'quantity': qty, 'price': price, 'seller': row['seller'], 'listed': row['listed'], 'page': page})
    if not raw['rows'] and '검색 결과가 없습니다' not in raw['body']:
        raise ValueError('매물 표가 확인되지 않습니다. 빈 매물로 처리하지 않습니다.')
    if raw['rows'] and not offers:
        raise ValueError('검색어와 일치하는 매물을 확인할 수 없습니다.')
    pages = [int(m.group(1)) for link in raw['links'] if (m := re.search(r'[?&]page=(\d+)', link))]
    return offers, max(pages, default=1)


class Collector:
    def __init__(self, progress, stop):
        self.progress, self.stop = progress, stop
        self.next_request = 0
        self.market_names = {}
        stamp = ROOT / 'data' / 'last-request.json'
        if stamp.exists():
            try:
                self.next_request = min(time.time() + REQUEST_DELAY, float(json.loads(stamp.read_text())['after']))
            except (ValueError, KeyError, json.JSONDecodeError):
                pass

    def check(self):
        if self.stop.is_set():
            raise InterruptedError('사용자가 조회를 중지했습니다.')

    def pace(self):
        while time.time() < self.next_request:
            self.check()
            self.stop.wait(min(.25, self.next_request - time.time()))
        self.check()

    def mark_finished(self):
        self.next_request = time.time() + REQUEST_DELAY
        (ROOT / 'data').mkdir(exist_ok=True)
        (ROOT / 'data' / 'last-request.json').write_text(json.dumps({'after': self.next_request}))

    def visit(self, page, url, label, ready, allow_not_found=False):
        self.pace()
        self.progress(label)
        try:
            response = page.goto(url, wait_until='domcontentloaded', timeout=45000)
            if allow_not_found and response and response.status == 404:
                return False
            if response and response.status >= 400:
                raise ValueError(f'사이트가 접근을 거절했습니다(HTTP {response.status}). 자동 조회를 중단했습니다.')
            page.wait_for_function(ready, timeout=20000)
            body = page.locator('body').inner_text()
            if re.search(r'just a moment|verify you are human|보안 확인|접근이 차단|access denied', body, re.I):
                raise ValueError('사이트 보안 확인 또는 접근 제한으로 중단했습니다.')
            self.check()
            return True
        finally:
            self.mark_finished()

    def open_browser(self):
        from playwright.sync_api import sync_playwright
        self.runtime = sync_playwright().start()
        try:
            self.context = self.runtime.chromium.launch_persistent_context(str(ROOT / 'data' / 'browser'),
                channel='msedge', headless=False, viewport={'width': 1280, 'height': 900})
            self.page = self.context.pages[0] if self.context.pages else self.context.new_page()
        except Exception:
            self.runtime.stop()
            self.runtime = None
            raise

    def page_for_request(self):
        if not getattr(self, 'runtime', None):
            self.open_browser()
        return self.page

    def read_recipe(self, name):
        page = self.page_for_request()
        empty_checks = json.dumps(RECIPE_EMPTY_TEXT, ensure_ascii=False)
        ready = "() => document.querySelector('input[data-col=\"count\"]') !== null || " + empty_checks + '.some(t => document.body.innerText.includes(t))'
        for search_name in search_names(name):
            found = self.visit(page, 'https://gerniverse.app/crafting/item/' + quote(search_name, safe=''),
                               f'제작법 확인 · {search_name}', ready, allow_not_found=True)
            raw = page.evaluate(RECIPE_JS) if found else None
            if raw is not None and (raw['rows'] or not any(t in raw['body'] for t in RECIPE_EMPTY_TEXT)):
                break
            page.goto('about:blank')
        else:
            raise ValueError(f'{name}: 띄어쓰기·한자 대체 표기를 확인해도 제작법이 없습니다.')
        methods = len(raw['methods'])
        if not methods:
            raise ValueError('제작법 선택 영역이 없습니다: ' + name)
        recipes, children = [], {}
        for i in range(methods):
            if i:
                self.pace()
                page.locator('button[aria-pressed]:not([aria-label])').nth(i).click()
                page.wait_for_function("i => document.querySelectorAll('button[aria-pressed]:not([aria-label])')[i]?.getAttribute('aria-pressed') === 'true'", arg=i)
                raw = page.evaluate(RECIPE_JS)
            recipe, child = parse_recipe(raw, name)
            if recipe not in recipes:
                recipes.append(recipe)
            for n, info in child.items():
                info['craftable'] = info['craftable'] or children.get(n, {}).get('craftable', False)
                children[n] = info
            self.mark_finished()
        page.goto('about:blank')
        return {'name': name, 'recipes': recipes, 'children': children, 'recipeAt': now()}

    def read_market_rows(self, name, search_type, page_number=1):
        page = self.page_for_request()
        original = self.market_names.get((key(name), search_type), name)
        for search_name in search_names(original):
            query = {'category': 'item', 'itemName': search_name, 'searchType': search_type,
                     'orderDirection': 'asc' if search_type == 'synchronized' else 'desc', 'page': page_number}
            if search_type == 'synchronized':
                query['orderType'] = 'price'
            self.visit(page, 'https://geota.co.kr/gersang/yukeuijeon/4?' + urlencode(query),
                       f'{"최저가" if search_type == "synchronized" else "전체 기록 최신순"} {page_number}페이지 · {search_name}',
                       "() => {const t=document.body.innerText; return t.includes('검색 결과가 없습니다') || document.querySelector('time[datetime]') !== null;}")
            raw = page.evaluate(MARKET_JS)
            offers, last = parse_market(raw, name, page_number)
            self.mark_finished()
            page.goto('about:blank')
            if offers:
                self.market_names[(key(name), search_type)] = search_name
                break
        return offers, last

    def read_market(self, name, page_number=1):
        offers, last = self.read_market_rows(name, 'synchronized', page_number)
        prices = [o['price'] for o in offers]
        if prices != sorted(prices):
            raise ValueError('가격 오름차순 정렬이 확인되지 않아 중단했습니다: ' + name)
        return {'offers': offers, 'marketAt': now(), 'marketScope': 'first-page',
                'marketPageCount': page_number, 'marketLastPage': last,
                'marketSearchName': self.market_names.get((key(name), 'synchronized'), name)}

    def read_archived_market(self, name):
        rows, _ = self.read_market_rows(name, 'archived')
        return rows

    def read_finished_quote(self, name):
        current = self.read_market(name)
        current.update(archivedQuote=None, archivedCheckedAt=None)
        if not current['offers']:
            rows = self.read_archived_market(name)
            observed_at = now()
            current['archivedCheckedAt'] = observed_at
            if rows:
                # Each displayed listing contributes one unit-price observation, not its stock quantity.
                count = len(rows)
                current['archivedQuote'] = {'price': (sum(r['price'] for r in rows) + count // 2) // count,
                                            'sampleCount': count, 'observedAt': observed_at,
                                            'rows': rows}
        return current

    def read_material_fallback(self, name):
        rows = self.read_archived_market(name)
        observed_at = now()
        return {'fallbackCheckedAt': observed_at,
                'fallbackQuote': dict(rows[0], observedAt=observed_at) if rows else None}

    def collect(self, target, mode='search', current=None, node=None, query=None):
        import copy
        query = query or {}
        quantity = integer(query.get('quantity', 1), '목표 수량', 1, 10000)
        path = ROOT / 'data' / 'database.json'
        if path.exists():
            database = json.loads(path.read_text(encoding='utf-8'))
            if database.get('version') != 1 or not isinstance(database.get('items'), dict):
                raise ValueError('재료 DB 형식을 확인하세요. 기존 파일은 변경하지 않았습니다.')
        else:
            database = {'version': 1, 'items': {}}
        records = database['items']
        target_key = key(target)
        expanded = {target_key}
        if mode != 'search' and current:
            expanded.update(current.get('expanded', [target_key]))
        if mode == 'expand':
            if not current or node not in current['items'] or not current['items'][node].get('craftable'):
                raise ValueError('조합 가능한 재료를 선택하세요.')
            expanded.add(node)
        data = {'target': target_key, 'items': {}, 'expanded': [], 'server': '청룡', 'started': now(),
                'scope': 'first-page', 'warnings': [
                    '재료 매물은 최저가 순으로 필요한 수량이 모일 때까지 다음 페이지를 확인합니다. 전체 매물이 부족할 때만 참고가를 적용합니다.',
                    '트리 펼치기와 비용 반영은 별개입니다. 재료별 완제품 구매·하위 재료 제작·자동 비교 선택을 따릅니다.',
                    '저장된 데이터는 자동 갱신하지 않습니다. 가격 조회 시각을 확인하고 필요한 경우 매물 가격 갱신을 누르세요.',
                    '사이트 산출량을 반영합니다. 제작 자격·성공 확률과 실제 게임 내 매물은 직접 확인하세요.']}
        items, visiting = data['items'], set()

        def persist():
            path.parent.mkdir(exist_ok=True)
            temporary = path.with_suffix('.tmp')
            temporary.write_text(json.dumps(database, ensure_ascii=False, indent=2), encoding='utf-8')
            temporary.replace(path)

        def material(name, craftable=False):
            self.check()
            n = key(name)
            if n in visiting:
                raise ValueError('순환 제작법이 있습니다: ' + name)
            if n in items:
                items[n]['craftable'] |= craftable
                return
            record = records.setdefault(n, {'name': name})
            if n in expanded:
                if not record.get('recipes') or (mode == 'recipe' and n == target_key):
                    record.update(self.read_recipe(name))
                    persist()
                if 'children' not in record:
                    raise ValueError('재료 DB에 하위 재료 표시가 없습니다. 제작법 갱신을 누르세요.')
                if populate_durations(record):
                    persist()
                data['expanded'].append(n)
            item = copy.deepcopy(record)
            item.update(name=name, craftable=craftable, recipes=record.get('recipes', []) if n in expanded else [])
            item.setdefault('offers', [])
            items[n] = item
            if n in expanded:
                visiting.add(n)
                for child in record['children'].values():
                    material(child['name'], child['craftable'])
                visiting.remove(n)

        try:
            material(target, True)
            if mode == 'finished':
                records[target_key].update(self.read_finished_quote(target))
                persist()
                items[target_key].update(copy.deepcopy(records[target_key]))
            direct = set()
            for recipe in items[target_key]['recipes']:
                direct.update(recipe['materials'])
            data['directMaterials'] = list(direct)
            for n, item in items.items():
                if n == target_key:
                    continue
                record = records[n]
                if record.get('marketSearchName'):
                    self.market_names[(key(item['name']), 'synchronized')] = record['marketSearchName']
                if mode == 'market' or record.get('marketScope') not in ('first-page', 'quantity-pages'):
                    record.update(self.read_market(item['name']))
                    record.setdefault('marketPageCount', 1)
                    record.setdefault('marketLastPage', 1)
                    record.update(fallbackQuote=None, fallbackCheckedAt=None)
                    persist()
                item.update(offers=copy.deepcopy(record['offers']), marketAt=record['marketAt'], marketScope=record['marketScope'],
                            marketPageCount=record.get('marketPageCount', 1), marketLastPage=record.get('marketLastPage'))
                item.update(fallbackQuote=copy.deepcopy(record.get('fallbackQuote')),
                            fallbackCheckedAt=record.get('fallbackCheckedAt'))

            def current_shortages():
                probe = copy.deepcopy(data)
                for item in probe['items'].values():
                    item['fallbackQuote'] = None
                for n, outputs in query.get('outputs', {}).items():
                    if n in probe['items']:
                        for recipe, output in zip(probe['items'][n]['recipes'], outputs):
                            recipe['output'] = integer(output, '제작 결과 수량', 1, 100000)
                modes = {n: query.get('modes', {}).get(n, 'buy') for n in items if n != target_key}
                result = solve(probe, target_key, quantity, query.get('owned', {}), modes)
                return result.get('shortages', {})

            if mode != 'finished':
                while True:
                    self.check()
                    shortages = current_shortages()
                    candidates = [n for n in shortages if mode != 'fallback' or n == node]
                    changed = False
                    for n in candidates:
                        record = records[n]
                        if record.get('marketLastPage') is None:
                            record.update(self.read_market(items[n]['name']))
                            record.setdefault('marketPageCount', 1)
                            record.setdefault('marketLastPage', 1)
                            changed = True
                        elif record.get('marketPageCount', 1) < record['marketLastPage']:
                            page_number = record.get('marketPageCount', 1) + 1
                            if page_number > 200:
                                raise ValueError('매물 조회가 200페이지를 넘었습니다. 불완전한 매물을 전체 매물로 간주하지 않고 중단합니다.')
                            batch = self.read_market(items[n]['name'], page_number)
                            identities = {(o.get('seller'), o.get('listed'), o['price'], o['quantity']) for o in record['offers']}
                            for offer in batch['offers']:
                                identity = (offer.get('seller'), offer.get('listed'), offer['price'], offer['quantity'])
                                if identity not in identities:
                                    record['offers'].append(offer)
                                    identities.add(identity)
                            record.update(marketPageCount=page_number, marketLastPage=batch.get('marketLastPage', page_number),
                                          marketAt=batch['marketAt'], marketScope='quantity-pages',
                                          marketSearchName=batch.get('marketSearchName', record.get('marketSearchName', items[n]['name'])))
                            changed = True
                        else:
                            continue
                        items[n].update(copy.deepcopy({k: v for k, v in record.items() if k != 'recipes'}))
                        persist()
                    if not changed:
                        break
                for n in candidates:
                    record = records[n]
                    if not record.get('fallbackCheckedAt') or (mode == 'fallback' and n == node):
                        record.update(self.read_material_fallback(items[n]['name']))
                        persist()
                    items[n].update(fallbackQuote=copy.deepcopy(record.get('fallbackQuote')),
                                    fallbackCheckedAt=record.get('fallbackCheckedAt'))
            data['finished'] = now()
            self.progress('완료 · JSON DB를 사용합니다. 요청 없이 다시 조회하지 않습니다.')
            return data
        finally:
            if getattr(self, 'runtime', None):
                self.context.close()
                self.runtime.stop()
                self.runtime = None

