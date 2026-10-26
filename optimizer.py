"""Integer material balance: one shared inventory and one capacity per market row."""
from collections import defaultdict


def key(name):
    return ''.join(name.split())


def integer(value, label, minimum=0, maximum=10**12):
    if isinstance(value, bool) or not isinstance(value, int) or not minimum <= value <= maximum:
        raise ValueError(f'{label}: {minimum:,}~{maximum:,} 사이 정수를 입력하세요.')
    return value


def solve(data, target, quantity, owned, modes=None, sale_price=None):
    import numpy as np
    from scipy.optimize import Bounds, LinearConstraint, milp

    integer(quantity, '목표 수량', 1, 10000)
    target = key(target)
    items = data['items']
    modes = modes or {}
    if not isinstance(modes, dict) or any(n not in items or m not in ('buy', 'craft', 'auto') for n, m in modes.items()):
        raise ValueError('올바른 재료별 계산 방식을 선택하세요.')
    for n, mode in modes.items():
        if mode == 'craft' and not items[n].get('recipes'):
            raise ValueError('먼저 해당 재료의 제작법을 불러오세요: ' + items[n]['name'])
    if target not in items or not items[target].get('recipes'):
        raise ValueError('대상 아이템의 제작법이 없습니다.')
    stock = {key(k): integer(v, '보유 수량', 0, 10**9) for k, v in owned.items()}
    stock[target] = 0  # The target quantity means new production, not existing finished stock.
    names = list(items)
    index = {name: i for i, name in enumerate(names)}
    columns, costs, caps, meta = [], [], [], []
    for name, item in items.items():
        if name != target:
            offers = item.get('offers', [])
            fallback = item.get('fallbackQuote')
            if fallback:
                offers = offers + [dict(fallback, quantity=1, estimated=True)]
            for offer in offers:
                price = integer(offer['price'], '매물 단가', 1)
                count = integer(offer['quantity'], '매물 수량', 1, 10**9)
                col = np.zeros(len(names)); col[index[name]] = 1
                columns.append(col); costs.append(price)
                caps.append(0 if modes.get(name) == 'craft' else np.inf if offer.get('estimated') else count)
                meta.append(('buy', name, offer))
        for recipe in item.get('recipes', []):
            col = np.zeros(len(names))
            col[index[name]] += integer(recipe.get('output', 1), '제작 결과 수량', 1, 100000)
            if not recipe['materials']:
                raise ValueError('빈 제작법은 계산할 수 없습니다.')
            for material, count in recipe['materials'].items():
                if material not in index:
                    raise ValueError('하위 재료 데이터가 누락되었습니다.')
                col[index[material]] -= integer(count, '재료 수량', 1, 1000000)
            columns.append(col); costs.append(integer(recipe['fee'], '제작 수수료'))
            caps.append(0 if name != target and modes.get(name) == 'buy' else np.inf)
            meta.append(('craft', name, recipe))
    matrix = np.column_stack(columns)
    demand = np.array([quantity if n == target else -stock.get(n, 0) for n in names], dtype=float)
    # Keep original integer currency units; HiGHS applies its own numerical scaling.
    constraints = [LinearConstraint(matrix, demand, np.full(len(names), np.inf))]
    references = np.array([1 if kind == 'buy' and source.get('estimated') else 0 for kind, _, source in meta])
    reference_optimal = True
    if references.any():
        # Historical prices fill shortages; they must not displace available stock just because they are cheaper.
        primary = milp(references, integrality=np.ones(len(costs)), bounds=Bounds(np.zeros(len(costs)), caps),
                       constraints=constraints, options={'time_limit': 20, 'mip_rel_gap': 0})
        if primary.x is not None:
            constraints.append(LinearConstraint(references, 0, round(float(references @ primary.x))))
        reference_optimal = primary.status == 0
    result = milp(np.array(costs, dtype=float), integrality=np.ones(len(costs)),
                  bounds=Bounds(np.zeros(len(costs)), caps), constraints=constraints,
                  options={'time_limit': 20, 'mip_rel_gap': 0})
    if result.x is None:
        shortages = {}
        if result.status == 2:
            diagnostic = milp(np.r_[np.zeros(len(costs)), np.ones(len(names))],
                              integrality=np.ones(len(costs) + len(names)),
                              bounds=Bounds(np.zeros(len(costs) + len(names)), caps + [0 if n == target or modes.get(n) == 'craft' else np.inf for n in names]),
                              constraints=LinearConstraint(np.column_stack([matrix, np.eye(len(names))]), demand, np.full(len(names), np.inf)),
                              options={'time_limit': 10, 'mip_rel_gap': 0})
            if diagnostic.x is not None:
                for n, missing in zip(names, diagnostic.x[len(costs):]):
                    if n != target and missing > .5:
                        available = stock.get(n, 0) + sum(o['quantity'] for o in items[n].get('offers', []))
                        shortages[n] = {'name': items[n]['name'], 'needed': available + round(missing),
                                        'available': available, 'missing': round(missing)}
        details = ' / '.join(f'{s["name"]}: 필요 {s["needed"]:,}개 · 조회 수량 {s["available"]:,}개 · {s["missing"]:,}개 부족' for s in shortages.values())
        return {'complete': False, 'shortages': shortages,
                'message': (details + '. ' if details else '') + '부족한 재료의 다음 매물 / 참고가 조회를 눌러주세요. 다음 페이지를 먼저 확인하고 전체 매물이 부족한 경우에만 부족분을 참고가로 계산합니다.',
                'comparison': compare_sale(items[target], quantity, None, sale_price),
                'missing': [i['name'] for n, i in items.items() if n != target and not i.get('offers') and not i.get('recipes') and not stock.get(n)]}
    values = np.rint(result.x).astype(np.int64)
    if np.max(np.abs(values - result.x)) > 1e-4 or np.any(matrix @ values < demand - 1e-5):
        raise ValueError('정수 해 검증에 실패했습니다. 확정 금액을 표시하지 않습니다.')
    buys, crafts = [], []
    usage, produced, purchased, estimated = defaultdict(int), defaultdict(int), defaultdict(int), defaultdict(int)
    total = 0
    for count, cost, (kind, name, source) in zip(values, costs, meta):
        count = int(count)
        if not count:
            continue
        total += count * cost
        if kind == 'buy':
            purchased[name] += count
            if source.get('estimated'):
                estimated[name] += count
            buys.append({'name': items[name]['name'], 'quantity': count, 'price': cost,
                         'cost': count * cost, 'seller': source.get('seller', ''),
                         'listed': source.get('listed', ''), 'page': source.get('page', 1),
                         'estimated': source.get('estimated', False)})
        else:
            produced[name] += count * source.get('output', 1)
            for material, need in source['materials'].items():
                usage[material] += count * need
            crafts.append({'name': items[name]['name'], 'quantity': count, 'output': source.get('output', 1),
                           'method': source.get('method', ''), 'fee': cost, 'cost': count * cost,
                           'materials': {items[n]['name']: v * count for n, v in source['materials'].items()}})
    used = {items[n]['name']: min(stock.get(n, 0), max(0, usage[n] - purchased[n] - produced[n]))
            for n in names if n != target}
    plan = {n: {'needed': usage[n], 'buy': purchased[n], 'craft': produced[n],
                'estimated': estimated[n] > 0, 'buyCurrent': purchased[n] - estimated[n], 'buyEstimated': estimated[n],
                'owned': used.get(items[n]['name'], 0), 'mode': modes.get(n, 'auto')}
            for n in names if n != target}
    comparison = compare_sale(items[target], quantity, total, sale_price)
    optimal = result.status == 0 and reference_optimal
    return {'complete': optimal, 'feasible': True, 'total': total,
            'message': '현재 매물 우선 · 부족분만 참고가 적용 · 선택한 제작법 기준 비용' if optimal else '계산 시간 제한: 가능한 조합이며 최저비용 확정 전입니다.',
            'buys': buys, 'crafts': crafts, 'ownedUsed': {k: v for k, v in used.items() if v},
            'fees': sum(c['cost'] for c in crafts), 'plan': plan, 'comparison': comparison,
            'estimated': any(b['estimated'] for b in buys)}


def compare_sale(item, quantity, cost, manual_price=None):
    archived = item.get('archivedQuote')
    if manual_price is not None:
        price = integer(manual_price, '완제품 판매 단가', 1)
        source = 'manual'
    elif item.get('offers'):
        price = min(integer(o['price'], '완제품 매물 단가', 1) for o in item['offers'])
        source = 'cached'
    elif archived:
        price = integer(archived['price'], '과거 기록 평균 단가', 1)
        integer(archived['sampleCount'], '과거 기록 개수', 1, 10000)
        source = 'archived'
    else:
        return {'available': False, 'checked': bool(item.get('marketAt')),
                'archiveChecked': bool(item.get('archivedCheckedAt'))}
    revenue = price * quantity
    profit = revenue - cost if cost is not None else None
    return {'available': True, 'price': price, 'revenue': revenue, 'profit': profit,
            'returnPercent': profit / cost * 100 if cost else None,
            'source': source, 'observedAt': archived['observedAt'] if source == 'archived' else item.get('marketAt') if source == 'cached' else None,
            'sampleCount': archived['sampleCount'] if source == 'archived' else None}
