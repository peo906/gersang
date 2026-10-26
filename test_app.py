import tempfile
import threading
import unittest
import copy
from pathlib import Path
from unittest.mock import patch

from collector import Collector, parse_market, parse_recipe, populate_durations, search_names, same_name, COMPAT_HANJA, MAX_SEARCH_NAMES
from optimizer import solve, compare_sale
from app import recent_history


def item(name, offers=(), recipes=()):
    return {'name': name, 'offers': [{'price': p, 'quantity': q} for p, q in offers], 'recipes': list(recipes)}


def recipe(materials, fee=0, output=1):
    return {'materials': materials, 'fee': fee, 'output': output}


class CostTests(unittest.TestCase):
    def test_missing_material_uses_latest_reference_without_quantity_limit(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'재료': 30}, 10)]),
                       '재료': item('재료')}}
        d['items']['재료']['fallbackQuote'] = {'price': 900, 'quantity': 1, 'seller': '기록'}
        r = solve(d, '완제품', 1, {}, {'재료': 'buy'})
        self.assertEqual(r['total'], 27010)
        self.assertEqual(r['buys'][0]['quantity'], 30)
        self.assertTrue(r['estimated'])
        self.assertTrue(r['plan']['재료']['estimated'])
        self.assertEqual(solve(d, '완제품', 1, {'재료': 29})['total'], 910)

    def test_reference_does_not_replace_existing_market_quantity(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'재료': 30})]),
                       '재료': item('재료', [(100, 1)])}}
        d['items']['재료']['fallbackQuote'] = {'price': 900}
        r = solve(d, '완제품', 1, {})
        self.assertEqual(r['total'], 100 + 29 * 900)
        self.assertEqual(r['plan']['재료']['buyCurrent'], 1)
        self.assertEqual(r['plan']['재료']['buyEstimated'], 29)

    def test_cheaper_reference_never_displaces_current_stock(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'재료': 100})]),
                       '재료': item('재료', [(1000, 47)])}}
        d['items']['재료']['fallbackQuote'] = {'price': 100}
        r = solve(d, '완제품', 1, {})
        self.assertEqual(r['total'], 47 * 1000 + 53 * 100)
        self.assertEqual(r['plan']['재료']['buyEstimated'], 53)
        self.assertEqual(solve(d, '완제품', 1, {'재료': 53})['total'], 47000)
        self.assertFalse(solve(d, '완제품', 1, {'재료': 53})['estimated'])

    def test_infeasible_result_identifies_partial_shortage(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'유명계석': 100, '충분한재료': 2})]),
                       '유명계석': item('유명계석', [(1300000, 47)]),
                       '충분한재료': item('충분한재료', [(100, 50)])}}
        r = solve(d, '완제품', 1, {}, {'유명계석': 'buy', '충분한재료': 'buy'})
        self.assertEqual(set(r['shortages']), {'유명계석'})
        self.assertEqual(r['shortages']['유명계석']['missing'], 53)
        self.assertEqual(r['shortages']['유명계석']['needed'], 100)
        self.assertEqual(r['shortages']['유명계석']['available'], 47)

    def test_archived_price_is_estimate_and_never_overrides_current_or_manual(self):
        i = item('완제품')
        i.update(marketAt='current-check', archivedCheckedAt='archive-check',
                 archivedQuote={'price': 200, 'sampleCount': 2, 'observedAt': 'archive-check'})
        r = compare_sale(i, 2, 100)
        self.assertEqual((r['source'], r['price'], r['profit'], r['sampleCount']), ('archived', 200, 300, 2))
        self.assertEqual(r['observedAt'], 'archive-check')
        i['offers'] = [{'price': 300, 'quantity': 1}]
        self.assertEqual(compare_sale(i, 1, 100)['source'], 'cached')
        self.assertEqual(compare_sale(i, 1, 100, 500)['source'], 'manual')

    def test_explicit_buy_and_craft_modes(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'중간': 3}, 7)]),
                       '중간': item('중간', [(20, 3)], [recipe({'철': 1}, 1)]),
                       '철': item('철', [(10, 5)])}}
        bought = solve(d, '완제품', 1, {}, {'중간': 'buy'})
        crafted = solve(d, '완제품', 1, {}, {'중간': 'craft'})
        self.assertEqual(bought['total'], 67)
        self.assertEqual(bought['plan']['중간']['buy'], 3)
        self.assertEqual(bought['plan']['중간']['craft'], 0)
        self.assertEqual(bought['plan']['철']['needed'], 0)
        self.assertEqual(crafted['total'], 40)
        self.assertEqual(crafted['plan']['중간']['buy'], 0)
        self.assertEqual(crafted['plan']['중간']['craft'], 3)

    def test_forced_craft_does_not_fall_back_to_buy(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'중간': 1})]),
                       '중간': item('중간', [(20, 3)], [recipe({'철': 1})]),
                       '철': item('철')}}
        self.assertFalse(solve(d, '완제품', 1, {}, {'중간': 'craft'})['complete'])

    def test_sale_comparison(self):
        i = item('완제품', [(1900000000, 1), (2000000000, 2)])
        i['marketAt'] = 'saved'
        r = compare_sale(i, 2, 3300000000)
        self.assertEqual((r['price'], r['revenue'], r['profit']), (1900000000, 3800000000, 500000000))
        self.assertEqual(r['source'], 'cached')
        self.assertEqual(compare_sale(i, 1, 2000000000, 1800000000)['profit'], -200000000)
        self.assertFalse(compare_sale(item('완제품'), 1, 1)['available'])
        self.assertIsNone(compare_sale(i, 1, None)['profit'])

    def test_invalid_calculation_mode_rejected(self):
        with self.assertRaises(ValueError):
            solve({'items': {}}, '완제품', 1, {}, {'중간': 'invalid'})
    def test_price_depth(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'철': 30})]),
                       '철': item('철', [(100, 1), (110, 9), (120, 50)])}}
        self.assertEqual(solve(d, '완제품', 1, {})['total'], 3490)

    def test_partial_buy_partial_craft(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'중간': 3}, 7)]),
                       '중간': item('중간', [(2, 1), (100, 20)], [recipe({'철': 1}, 1)]),
                       '철': item('철', [(10, 5)])}}
        r = solve(d, '완제품', 1, {})
        self.assertEqual(r['total'], 31)
        self.assertEqual(sum(b['quantity'] for b in r['buys'] if b['name'] == '중간'), 1)

    def test_shared_market_and_stock_are_not_duplicated(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'가': 1, '나': 1})]),
                       '가': item('가', recipes=[recipe({'철': 1})]),
                       '나': item('나', recipes=[recipe({'철': 1})]),
                       '철': item('철', [(1, 1), (100, 1)])}}
        self.assertEqual(solve(d, '완제품', 1, {})['total'], 101)
        r = solve(d, '완제품', 1, {'철': 1})
        self.assertEqual(r['total'], 1)
        self.assertEqual(r['ownedUsed'], {'철': 1})

    def test_global_choice_preserves_shared_resource(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'가': 1, '나': 1})]),
                       '가': item('가', [(10, 1)], [recipe({'철': 1})]),
                       '나': item('나', [(1000, 1)], [recipe({'철': 1})]),
                       '철': item('철', [(1, 1)])}}
        self.assertEqual(solve(d, '완제품', 1, {})['total'], 11)

    def test_missing_stock_does_not_mean_free(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'철': 2})]), '철': item('철', [(1, 1)])}}
        self.assertFalse(solve(d, '완제품', 1, {})['complete'])

    def test_batch_and_fees(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'철': 2}, 10, 3)]), '철': item('철', [(5, 10)])}}
        r = solve(d, '완제품', 4, {})
        self.assertEqual(r['total'], 40)
        self.assertEqual(r['fees'], 20)

    def test_alternative_recipe(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'철': 2}, 2), recipe({'동': 1}, 4)]),
                       '철': item('철', [(10, 2)]), '동': item('동', [(5, 1)])}}
        self.assertEqual(solve(d, '완제품', 1, {})['total'], 9)

    def test_owned_target_is_not_counted_as_new_production(self):
        d = {'items': {'완제품': item('완제품', recipes=[recipe({'철': 1}, 9)]), '철': item('철', [(3, 1)])}}
        self.assertEqual(solve(d, '완제품', 1, {'완제품': 1})['total'], 12)

    def test_bad_quantity_rejected(self):
        with self.assertRaises(ValueError):
            solve({'items': {}}, '철', -1, {})


class ParserTests(unittest.TestCase):
    def test_existing_recipe_text_populates_duration_without_lookup(self):
        cached = {'recipes': [{'method': '제조 공방\n제작비\n300,000,000\n소요 기간\n3일'}]}
        self.assertTrue(populate_durations(cached))
        self.assertEqual(cached['recipes'][0]['durationDays'], 3)
        self.assertFalse(populate_durations(cached))
        cached = {'recipes': [{'method': '제조 공방 소요기간 | 5일', 'durationDays': None}]}
        self.assertTrue(populate_durations(cached))
        self.assertEqual(cached['recipes'][0]['durationDays'], 5)
    def test_optional_crafting_duration(self):
        raw = {'title': '완제품', 'selected': 'NPC 제작비 100 소요기간 | 5일',
               'rows': [{'name': '재료', 'count': '1', 'mode': 'single'}]}
        self.assertEqual(parse_recipe(raw, '완제품')[0]['durationDays'], 5)
        raw['selected'] = 'NPC 제작비 100'
        self.assertIsNone(parse_recipe(raw, '완제품')[0]['durationDays'])
    def test_observed_recipe_shape(self):
        raw = {'title': '철괴리의 인형', 'selected': '선택됨\n장과로\nNPC\n신선곡\n제작비\n100,000,000',
               'rows': [{'name': '바람의속성석', 'count': '15', 'mode': 'manual'},
                        {'name': '야차의 부서진 창', 'count': '30', 'mode': 'single'}]}
        r, children = parse_recipe(raw, '철괴리의 인형')
        self.assertEqual(r['fee'], 100000000)
        self.assertEqual(r['materials']['야차의부서진창'], 30)
        self.assertTrue(children['바람의속성석']['craftable'])
        self.assertFalse(children['야차의부서진창']['craftable'])
        raw['rows'][0]['count'] = ''
        with self.assertRaises(ValueError):
            parse_recipe(raw, '철괴리의 인형')

    def test_observed_market_shape(self):
        raw = {'body': '청룡 서버', 'rows': [{'name': '야차의 부서진 창', 'quantity': '55',
               'price': '790,000냥', 'seller': '언스', 'listed': '2026-10-22T16:10:43'}],
               'links': ['https://geota.co.kr/gersang/yukeuijeon/4?page=3']}
        rows, last = parse_market(raw, '야차의 부서진 창', 1)
        self.assertEqual((rows[0]['quantity'], rows[0]['price'], last), (55, 790000, 3))
        raw['rows'][0]['price'] = '79만'
        with self.assertRaises(ValueError):
            parse_market(raw, '야차의 부서진 창', 1)

    def test_observed_free_npc_recipe(self):
        raw = {'title': '상급정수', 'selected': '선택됨\n마사무네\nNPC\n일본 다카야마 근처',
               'rows': [{'name': '중급정수', 'count': '10', 'mode': 'manual'}]}
        self.assertEqual(parse_recipe(raw, '상급정수')[0]['fee'], 0)
        raw['selected'] += '\n제작비\n알 수 없음'
        with self.assertRaises(ValueError):
            parse_recipe(raw, '상급정수')

    def test_observed_artisan_output(self):
        raw = {'title': '오색가루', 'selected': '선택됨\n연금술사\n장인기술\n제작비\n500,000\n산출량\n10개',
               'rows': [{'name': '늪지석균', 'count': '10', 'mode': 'single'}]}
        self.assertEqual(parse_recipe(raw, '오색가루')[0]['output'], 10)

    def test_empty_market_requires_explicit_empty_message(self):
        with self.assertRaises(ValueError):
            parse_market({'body': '청룡 서버', 'rows': [], 'links': []}, '철', 1)
        self.assertEqual(parse_market({'body': '청룡 검색 결과가 없습니다', 'rows': [], 'links': []}, '철', 1), ([], 1))

    def test_rate_limit_and_cancellation(self):
        class Clock:
            value = 100
            def time(self): return self.value
            def wait(self, seconds): self.value += seconds
        clock = Clock(); stop = threading.Event()
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder, patch('collector.ROOT', Path(folder)), patch('collector.time.time', clock.time):
            c = Collector(lambda _: None, stop)
            c.mark_finished()
            with patch.object(stop, 'wait', clock.wait):
                c.pace()
            self.assertEqual(clock.value, 101.5)
            stop.set()
            with self.assertRaises(InterruptedError):
                c.pace()


class CacheScopeTests(unittest.TestCase):
    def test_explicit_reference_lookup_works_with_partial_current_stock(self):
        d = self.make().collect('완제품')
        c = self.make()
        with patch.object(c, 'read_material_fallback', return_value={'fallbackQuote': {'price': 900}, 'fallbackCheckedAt': 'checked'}) as reference:
            updated = c.collect('완제품', 'fallback', d, '단일재', {'quantity': 30})
        reference.assert_called_once_with('단일재')
        self.assertEqual(updated['items']['단일재']['offers'][0]['quantity'], 50)
        self.assertEqual(updated['items']['단일재']['fallbackQuote']['price'], 900)

    def test_next_page_fills_quantity_and_stops_without_archive(self):
        c = self.make()
        requests = []
        def market(name, page_number=1):
            requests.append((name, page_number))
            quantity = 500 if name == '중간재' else 47 if page_number == 1 else 60
            return {'offers': [{'price': 100 + page_number, 'quantity': quantity, 'seller': str(page_number), 'listed': str(page_number), 'page': page_number}],
                    'marketAt': 'saved', 'marketScope': 'first-page', 'marketPageCount': page_number,
                    'marketLastPage': 3 if name == '단일재' else 1}
        with patch.object(c, 'read_market', side_effect=market), patch.object(c, 'read_material_fallback') as reference:
            d = c.collect('완제품', query={'quantity': 50})
        self.assertEqual([p for n, p in requests if n == '단일재'], [1, 2])
        reference.assert_not_called()
        self.assertEqual(sum(o['quantity'] for o in d['items']['단일재']['offers']), 107)
        self.assertEqual(d['items']['단일재']['marketPageCount'], 2)
        self.calls.clear()
        self.make().collect('완제품', query={'quantity': 50})
        self.assertEqual(self.calls, [])

    def test_archive_is_only_used_after_last_current_page(self):
        c = self.make()
        requests = []
        def market(name, page_number=1):
            requests.append((name, page_number))
            quantity = 500 if name == '중간재' else 47 if page_number == 1 else 20
            return {'offers': [{'price': 100, 'quantity': quantity, 'seller': str(page_number), 'listed': str(page_number), 'page': page_number}],
                    'marketAt': 'saved', 'marketScope': 'first-page', 'marketPageCount': page_number,
                    'marketLastPage': 2 if name == '단일재' else 1}
        reference = {'fallbackQuote': {'price': 10, 'quantity': 1}, 'fallbackCheckedAt': 'checked'}
        with patch.object(c, 'read_market', side_effect=market), patch.object(c, 'read_material_fallback', return_value=reference) as fallback:
            d = c.collect('완제품', query={'quantity': 50})
        fallback.assert_called_once_with('단일재')
        self.assertEqual([p for n, p in requests if n == '단일재'], [1, 2])
        r = solve(d, '완제품', 50, {})
        self.assertEqual(r['plan']['단일재']['buyCurrent'], 67)
        self.assertEqual(r['plan']['단일재']['buyEstimated'], 33)

    def test_owned_stock_prevents_unnecessary_next_page(self):
        c = self.make()
        def market(name, page_number=1):
            self.assertEqual(page_number, 1)
            return {'offers': [{'price': 100, 'quantity': 500 if name == '중간재' else 47}],
                    'marketAt': 'saved', 'marketScope': 'first-page', 'marketPageCount': 1, 'marketLastPage': 10}
        with patch.object(c, 'read_market', side_effect=market), patch.object(c, 'read_material_fallback') as reference:
            c.collect('완제품', query={'quantity': 50, 'owned': {'단일재': 53}})
        reference.assert_not_called()
    def test_material_reference_is_first_record_not_minimum_or_average(self):
        c = Collector(lambda _: None, threading.Event())
        rows = [{'price': 900, 'quantity': 1}, {'price': 100, 'quantity': 999}]
        with patch.object(c, 'read_archived_market', return_value=rows):
            r = c.read_material_fallback('재료')
        self.assertEqual(r['fallbackQuote']['price'], 900)
        self.assertEqual(r['fallbackQuote']['quantity'], 1)

    def setUp(self):
        self.folder = tempfile.TemporaryDirectory(dir=Path(__file__).parent)
        self.root_patch = patch('collector.ROOT', Path(self.folder.name))
        self.root_patch.start()
        self.calls = []
        calls = self.calls
        class Fake(Collector):
            def read_recipe(self, name):
                calls.append(('recipe', name))
                child = {'완제품': {'중간재': True, '단일재': False},
                         '중간재': {'아래재료': True},
                         '아래재료': {'상황버섯': False}}[name]
                return {'name': name, 'recipes': [recipe({n: 2 for n in child})],
                        'children': {n: {'name': n, 'craftable': c} for n, c in child.items()}, 'recipeAt': 'saved'}
            def read_market(self, name):
                calls.append(('market', name))
                return {'offers': [{'price': 10, 'quantity': 50}], 'marketAt': 'saved', 'marketScope': 'first-page'}
            def open_browser(self):
                raise AssertionError('Cache tests must not open a browser')
        self.make = lambda: Fake(lambda _: None, threading.Event())

    def tearDown(self):
        self.root_patch.stop()
        self.folder.cleanup()

    def test_initial_search_only_direct_materials(self):
        d = self.make().collect('완제품')
        self.assertEqual(set(d['items']), {'완제품', '중간재', '단일재'})
        self.assertEqual(self.calls, [('recipe', '완제품'), ('market', '중간재'), ('market', '단일재')])
        self.assertTrue(d['items']['중간재']['craftable'])
        self.assertEqual(d['items']['중간재']['recipes'], [])

    def test_second_search_has_zero_external_requests(self):
        self.make().collect('완제품')
        self.calls.clear()
        d = self.make().collect('완제품')
        self.assertEqual(self.calls, [])
        self.assertEqual(d['items']['중간재']['marketAt'], 'saved')

    def test_material_fallback_is_saved_and_reused(self):
        c = self.make()
        market = c.read_market
        def read(name):
            result = market(name)
            if name == '단일재':
                result['offers'] = []
            return result
        reference = {'fallbackQuote': {'price': 900, 'quantity': 1}, 'fallbackCheckedAt': 'checked'}
        with patch.object(c, 'read_market', side_effect=read), patch.object(c, 'read_material_fallback', return_value=reference) as fallback:
            d = c.collect('완제품')
        fallback.assert_called_once_with('단일재')
        self.assertEqual(d['items']['단일재']['fallbackQuote']['price'], 900)
        self.calls.clear()
        cached = self.make().collect('완제품')
        self.assertEqual(self.calls, [])
        self.assertEqual(cached['items']['단일재']['fallbackQuote']['price'], 900)

    def test_expand_only_one_requested_level_then_reuse(self):
        d = self.make().collect('완제품')
        self.calls.clear()
        expanded = self.make().collect('완제품', 'expand', d, '중간재')
        self.assertEqual(self.calls, [('recipe', '중간재'), ('market', '아래재료')])
        self.assertNotIn('상황버섯', expanded['items'])
        self.calls.clear()
        self.make().collect('완제품', 'expand', d, '중간재')
        self.assertEqual(self.calls, [])

    def test_refresh_prices_only_current_materials(self):
        d = self.make().collect('완제품')
        self.make().collect('완제품', 'expand', d, '중간재')
        self.calls.clear()
        self.make().collect('완제품', 'market', d)
        self.assertEqual(self.calls, [('market', '중간재'), ('market', '단일재')])

    def test_recipe_refresh_does_not_refresh_saved_prices(self):
        d = self.make().collect('완제품')
        self.calls.clear()
        self.make().collect('완제품', 'recipe', d)
        self.assertEqual(self.calls, [('recipe', '완제품')])

    def test_finished_quote_is_only_fetched_on_explicit_request(self):
        d = self.make().collect('완제품')
        self.calls.clear()
        quoted = self.make().collect('완제품', 'finished', d)
        self.assertEqual(self.calls, [('market', '완제품')])
        self.assertEqual(quoted['items']['완제품']['offers'][0]['price'], 10)
        self.calls.clear()
        self.make().collect('완제품')
        self.assertEqual(self.calls, [])

    def test_empty_current_uses_unweighted_first_page_average(self):
        c = self.make()
        current = {'offers': [], 'marketAt': 'checked', 'marketScope': 'first-page'}
        rows = [{'price': 100, 'quantity': 1}, {'price': 301, 'quantity': 999}]
        with patch.object(c, 'read_market', return_value=current), patch.object(c, 'read_archived_market', return_value=rows) as archived:
            result = c.read_finished_quote('완제품')
        archived.assert_called_once_with('완제품')
        self.assertEqual(result['offers'], [])
        self.assertEqual(result['archivedQuote']['price'], 201)
        self.assertEqual(result['archivedQuote']['sampleCount'], 2)

    def test_current_offers_skip_archive(self):
        c = self.make()
        with patch.object(c, 'read_archived_market') as archived:
            result = c.read_finished_quote('완제품')
        archived.assert_not_called()
        self.assertIsNone(result['archivedQuote'])

    def test_empty_archive_does_not_become_zero_price(self):
        c = self.make()
        with patch.object(c, 'read_market', return_value={'offers': [], 'marketAt': 'checked'}), patch.object(c, 'read_archived_market', return_value=[]):
            result = c.read_finished_quote('완제품')
        r = compare_sale(result, 1, 100)
        self.assertFalse(r['available'])
        self.assertTrue(r['archiveChecked'])

    def test_access_error_does_not_trigger_archive_retry(self):
        c = self.make()
        with patch.object(c, 'read_market', side_effect=ValueError('403')), patch.object(c, 'read_archived_market') as archived:
            with self.assertRaises(ValueError):
                c.read_finished_quote('완제품')
        archived.assert_not_called()

    def test_archive_url_is_latest_first_page_without_price_sort(self):
        from urllib.parse import urlsplit, parse_qs
        visited = []
        class Page:
            def evaluate(self, _):
                return {'body': '청룡 서버', 'rows': [{'name': '완제품', 'quantity': '1', 'price': '200냥', 'seller': '판매자', 'listed': 'now'}], 'links': ['https://geota.co.kr/gersang/yukeuijeon/4?page=99']}
            def goto(self, url): visited.append(url)
        c = Collector(lambda _: None, threading.Event())
        with patch.object(c, 'page_for_request', return_value=Page()), patch.object(c, 'visit', side_effect=lambda page, url, *_: visited.append(url)):
            c.read_archived_market('완제품')
        query = parse_qs(urlsplit(visited[0]).query)
        self.assertEqual(query['searchType'], ['archived'])
        self.assertEqual(query['orderDirection'], ['desc'])
        self.assertEqual(query['page'], ['1'])
        self.assertNotIn('orderType', query)
        self.assertEqual(len(visited), 2)

    def test_market_reads_one_page_even_when_more_exist(self):
        from urllib.parse import urlsplit, parse_qs
        visited = []
        raw = {'body': '청룡 서버', 'rows': [{'name': '단일재', 'quantity': '2', 'price': '10냥', 'seller': '판매자', 'listed': 'now'}],
               'links': ['https://geota.co.kr/gersang/yukeuijeon/4?page=30']}
        class Page:
            def evaluate(self, _): return raw
            def goto(self, url): visited.append(url)
        c = Collector(lambda _: None, threading.Event())
        with patch.object(c, 'page_for_request', return_value=Page()), patch.object(c, 'visit', side_effect=lambda page, url, *_: visited.append(url)):
            r = c.read_market('단일재')
        self.assertEqual(len(visited), 2)
        query = parse_qs(urlsplit(visited[0]).query)
        self.assertEqual(query['orderType'], ['price'])
        self.assertEqual(query['orderDirection'], ['asc'])
        self.assertEqual(query['page'], ['1'])
        self.assertEqual(visited[1], 'about:blank')
        self.assertEqual(r['marketScope'], 'first-page')

    def test_spacing_retry_and_next_page_reuse_successful_name(self):
        from urllib.parse import urlsplit, parse_qs
        raw_empty = {'body': '청룡 검색 결과가 없습니다', 'rows': [], 'links': []}
        raw_found = {'body': '청룡 서버', 'rows': [{'name': '철괴리의인형', 'quantity': '1', 'price': '100냥', 'seller': '판매자', 'listed': 'now'}], 'links': ['https://geota.co.kr/gersang/yukeuijeon/4?page=2']}
        page = unittest.mock.Mock()
        page.evaluate.side_effect = [raw_empty, raw_found, raw_found]
        c = Collector(lambda _: None, threading.Event())
        with patch.object(c, 'page_for_request', return_value=page), patch.object(c, 'visit') as visit:
            first = c.read_market('철괴리의 인형')
            c.read_market('철괴리의 인형', 2)
        queries = [parse_qs(urlsplit(call.args[1]).query) for call in visit.call_args_list]
        self.assertEqual([q['itemName'][0] for q in queries], ['철괴리의 인형', '철괴리의인형', '철괴리의인형'])
        self.assertEqual(queries[2]['page'], ['2'])
        self.assertEqual(first['marketSearchName'], '철괴리의인형')

    def test_spacing_does_not_retry_found_or_already_compact_queries(self):
        found = {'body': '청룡 서버', 'rows': [{'name': '철괴리의인형', 'quantity': '1', 'price': '100냥', 'seller': '판매자', 'listed': 'now'}], 'links': []}
        empty = {'body': '청룡 검색 결과가 없습니다', 'rows': [], 'links': []}
        for name, raw in [('철괴리의 인형', found), ('철괴리의인형', empty)]:
            page = unittest.mock.Mock()
            page.evaluate.return_value = raw
            c = Collector(lambda _: None, threading.Event())
            with patch.object(c, 'page_for_request', return_value=page), patch.object(c, 'visit') as visit:
                c.read_market(name)
            self.assertEqual(visit.call_count, 1)

    def test_spacing_archive_retry_is_bounded_and_keeps_latest_order(self):
        from urllib.parse import urlsplit, parse_qs
        page = unittest.mock.Mock()
        page.evaluate.return_value = {'body': '청룡 검색 결과가 없습니다', 'rows': [], 'links': []}
        c = Collector(lambda _: None, threading.Event())
        with patch.object(c, 'page_for_request', return_value=page), patch.object(c, 'visit') as visit:
            self.assertEqual(c.read_archived_market('철괴리의 인형'), [])
        self.assertEqual(visit.call_count, 2)
        for call in visit.call_args_list:
            query = parse_qs(urlsplit(call.args[1]).query)
            self.assertEqual(query['searchType'], ['archived'])
            self.assertEqual(query['orderDirection'], ['desc'])

    def test_spacing_does_not_retry_access_or_parser_errors(self):
        for fail_visit in [True, False]:
            page = unittest.mock.Mock()
            page.evaluate.return_value = {'body': '청룡 서버', 'rows': [], 'links': []}
            c = Collector(lambda _: None, threading.Event())
            with patch.object(c, 'page_for_request', return_value=page), patch.object(c, 'visit', side_effect=ValueError('403') if fail_visit else None) as visit:
                with self.assertRaises(ValueError):
                    c.read_market('철괴리의 인형')
            self.assertEqual(visit.call_count, 1)

    def test_recipe_404_retries_without_spaces(self):
        from urllib.parse import unquote
        page = unittest.mock.Mock()
        page.evaluate.return_value = {'title': '철괴리의인형', 'rows': [{'name': '재료', 'count': '1', 'mode': 'single'}],
                                     'methods': ['NPC 제작비 100'], 'selected': 'NPC 제작비 100', 'body': '필요 재료'}
        c = Collector(lambda _: None, threading.Event())
        with patch.object(c, 'page_for_request', return_value=page), patch.object(c, 'visit', side_effect=[False, True]) as visit:
            result = c.read_recipe('철괴리의 인형')
        self.assertEqual(visit.call_count, 2)
        self.assertTrue(unquote(visit.call_args_list[1].args[1]).endswith('/철괴리의인형'))
        self.assertEqual(result['recipes'][0]['fee'], 100)


class HistoryTests(unittest.TestCase):
    def test_general_compatibility_hanja_candidates_and_combinations(self):
        self.assertEqual(list(search_names('金龍')), ['金龍', '金龍', '金龍', '金龍'])
        reverse = list(search_names('金龍'))
        self.assertEqual(reverse[0], '金龍')
        self.assertIn('金龍', reverse)
        self.assertTrue(same_name('金龍', '金龍'))
        base, variants = next((base, variants) for base, variants in COMPAT_HANJA.items()
                              if any(ord(c) > 0xFFFF for c in variants))
        supplementary = next(c for c in variants if ord(c) > 0xFFFF)
        self.assertIn(supplementary, search_names(base))
        self.assertIn(base, search_names(supplementary))
        self.assertEqual(list(search_names('풍수화토(風水火土)')), ['풍수화토(風水火土)'])

    def test_general_hanja_query_count_is_bounded(self):
        self.assertEqual(len(search_names('金' * 100)), MAX_SEARCH_NAMES)
        self.assertEqual(len(search_names('金 ' * 50)), MAX_SEARCH_NAMES)
        page = unittest.mock.Mock()
        page.evaluate.return_value = {'body': '청룡 검색 결과가 없습니다', 'rows': [], 'links': []}
        c = Collector(lambda _: None, threading.Event())
        with patch.object(c, 'page_for_request', return_value=page), patch.object(c, 'visit') as visit, patch.object(c, 'mark_finished'):
            self.assertEqual(c.read_archived_market('金' * 100), [])
        self.assertEqual(visit.call_count, MAX_SEARCH_NAMES)

    def test_general_hanja_listing_matches_without_changing_database_key(self):
        page = unittest.mock.Mock()
        found = {'body': '청룡 서버', 'rows': [{'name': '金龍', 'quantity': '1',
                 'price': '100냥', 'seller': '판매자', 'listed': 'now'}], 'links': []}
        page.evaluate.side_effect = [{'body': '청룡 검색 결과가 없습니다', 'rows': [], 'links': []}, found]
        c = Collector(lambda _: None, threading.Event())
        with patch.object(c, 'page_for_request', return_value=page), patch.object(c, 'visit') as visit, patch.object(c, 'mark_finished'):
            result = c.read_market('金龍')
        self.assertEqual(visit.call_count, 2)
        self.assertEqual(result['offers'][0]['price'], 100)
        self.assertIn(('金龍', 'synchronized'), c.market_names)

    def test_hanja_does_not_retry_access_rejection(self):
        c = Collector(lambda _: None, threading.Event())
        with patch.object(c, 'page_for_request'), patch.object(c, 'visit', side_effect=ValueError('403')) as visit:
            with self.assertRaisesRegex(ValueError, '403'):
                c.read_market('金龍')
        self.assertEqual(visit.call_count, 1)

    def test_thunder_alias_candidates_are_bounded_and_preserve_original(self):
        self.assertEqual(list(search_names('정기의구슬조각(雷)')),
                         ['정기의구슬조각(雷)', '정기의구슬조각(雷)'])
        self.assertEqual(list(search_names('정기의 구슬조각(雷)')),
                         ['정기의 구슬조각(雷)', '정기의구슬조각(雷)',
                          '정기의 구슬조각(雷)', '정기의구슬조각(雷)'])
        self.assertTrue(same_name('정기의구슬조각(雷)', '정기의 구슬조각(雷)'))
        self.assertFalse(same_name('정기의구슬조각(雷)', '정기의구슬조각(火)'))

    def test_thunder_alias_retry_accepts_listing_and_reuses_next_page(self):
        from urllib.parse import urlsplit, parse_qs
        for requested, listed in [('정기의구슬조각(雷)', '정기의구슬조각(雷)'),
                                  ('정기의구슬조각(雷)', '정기의구슬조각(雷)')]:
            page = unittest.mock.Mock()
            found = {'body': '청룡 서버', 'rows': [{'name': listed, 'quantity': '47',
                     'price': '100냥', 'seller': '판매자', 'listed': 'now'}], 'links': []}
            page.evaluate.side_effect = [{'body': '청룡 검색 결과가 없습니다', 'rows': [], 'links': []}, found, found]
            c = Collector(lambda _: None, threading.Event())
            with patch.object(c, 'page_for_request', return_value=page), patch.object(c, 'visit') as visit, patch.object(c, 'mark_finished'):
                first = c.read_market(requested)
                c.read_market(requested, 2)
            queries = [parse_qs(urlsplit(call.args[1]).query) for call in visit.call_args_list]
            self.assertEqual([q['itemName'][0] for q in queries], [requested, listed, listed])
            self.assertEqual(first['offers'][0]['quantity'], 47)
            self.assertEqual(first['marketSearchName'], listed)

    def test_thunder_alias_archive_and_recipe(self):
        from urllib.parse import urlsplit, parse_qs
        page = unittest.mock.Mock()
        page.evaluate.return_value = {'body': '청룡 검색 결과가 없습니다', 'rows': [], 'links': []}
        c = Collector(lambda _: None, threading.Event())
        with patch.object(c, 'page_for_request', return_value=page), patch.object(c, 'visit') as visit, patch.object(c, 'mark_finished'):
            self.assertEqual(c.read_archived_market('정기의구슬조각(雷)'), [])
        self.assertEqual(visit.call_count, 2)
        for call in visit.call_args_list:
            query = parse_qs(urlsplit(call.args[1]).query)
            self.assertEqual(query['searchType'], ['archived'])
            self.assertEqual(query['orderDirection'], ['desc'])
        page.evaluate.return_value = {'title': '정기의구슬조각(雷)',
            'rows': [{'name': '재료', 'count': '1', 'mode': 'single'}],
            'methods': ['NPC 제작비 100'], 'selected': 'NPC 제작비 100', 'body': '필요 재료'}
        with patch.object(c, 'page_for_request', return_value=page), patch.object(c, 'visit', side_effect=[False, True]) as visit, patch.object(c, 'mark_finished'):
            result = c.read_recipe('정기의구슬조각(雷)')
        self.assertEqual(visit.call_count, 2)
        self.assertEqual(result['recipes'][0]['fee'], 100)

    def test_latest_five_unique_items(self):
        history = []
        for n in range(7):
            history = recent_history(history, '아이템 ' + str(n))
        self.assertEqual(history, ['아이템 6', '아이템 5', '아이템 4', '아이템 3', '아이템 2'])
        history = recent_history(history, ' 아이템4 ')
        self.assertEqual(history, ['아이템4', '아이템 6', '아이템 5', '아이템 3', '아이템 2'])


if __name__ == '__main__':
    unittest.main()
