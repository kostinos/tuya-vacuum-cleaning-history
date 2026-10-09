"""Security regressions with real integration methods and no HA/Tuya network."""
import ast
import asyncio
from collections import OrderedDict
from datetime import datetime, timezone
import importlib.util
from pathlib import Path
import re
import sys
import time
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch
from urllib.parse import urlencode

ROOT = Path(__file__).parents[1] / 'custom_components' / 'tuvio_history'
pkg = ModuleType('security_test_package')
pkg.__path__ = [str(ROOT)]
sys.modules[pkg.__name__] = pkg
spec = importlib.util.spec_from_file_location(pkg.__name__ + '.security', ROOT / 'security.py')
security = importlib.util.module_from_spec(spec)
spec.loader.exec_module(security)

# Execute the unchanged class/handlers, excluding HA imports and registration.
# HTTP, Recorder and decoding are mocks; tests never contact the user's instance.
tree = ast.parse((ROOT / '__init__.py').read_text())
nodes = [node for node in tree.body if
         (isinstance(node, ast.ClassDef) and node.name == 'History') or
         (isinstance(node, ast.AsyncFunctionDef) and node.name in ('ws_list', 'ws_map'))]
for node in nodes:
    if isinstance(node, ast.AsyncFunctionDef):
        node.decorator_list = []
environment = dict(
    asyncio=asyncio, time=time, re=re, OrderedDict=OrderedDict,
    datetime=datetime, timezone=timezone, urlencode=urlencode,
    CONF_CLEAN_MODE_ENTITY_ID='clean', CONF_SUCTION_ENTITY_ID='suction',
    CONF_WATER_ENTITY_ID='water', DOMAIN='tuvio_history', POLICY_READ='read',
    Admission=security.Admission, WorkPool=security.WorkPool,
    HistoryBusy=security.HistoryBusy, visible_records=security.visible_records,
)
exec(compile(ast.Module(body=nodes, type_ignores=[]), str(ROOT / '__init__.py'), 'exec'), environment)
History = environment['History']


class AdmissionTests(unittest.TestCase):
    def test_per_user_concurrency_rejects_without_waiting(self):
        admission = security.Admission()
        with admission.request('user'), admission.request('user'):
            with self.assertRaises(security.HistoryBusy):
                with admission.request('user'):
                    self.fail('Admitted excess work')
        self.assertEqual(admission.active, 0)

    def test_global_concurrency_rejects_other_users(self):
        from contextlib import ExitStack
        admission = security.Admission()
        with ExitStack() as stack:
            for user in range(8):
                stack.enter_context(admission.request(user))
            with self.assertRaises(security.HistoryBusy):
                with admission.request('extra'):
                    self.fail('Admitted excess work')
        self.assertEqual(admission.active, 0)

    def test_user_rate_limit_and_expiry(self):
        admission = security.Admission()
        with patch.object(security.time, 'monotonic', return_value=100):
            for _ in range(20):
                with admission.request('user'):
                    pass
            with self.assertRaises(security.HistoryBusy):
                with admission.request('user'):
                    self.fail('Ignored rate limit')
        with patch.object(security.time, 'monotonic', return_value=161):
            with admission.request('user'):
                pass
        self.assertEqual(len(admission.recent), 1)

    def test_global_rate_limit(self):
        admission = security.Admission()
        for user in range(60):
            with admission.request(user):
                pass
        with self.assertRaises(security.HistoryBusy):
            with admission.request('extra'):
                self.fail('Ignored global rate limit')

    def test_failed_request_releases_slot(self):
        admission = security.Admission()
        with self.assertRaises(ValueError):
            with admission.request('user'):
                raise ValueError
        self.assertEqual(admission.active, 0)


class HistoryTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hass = SimpleNamespace(async_add_executor_job=AsyncMock(return_value='png'))
        self.history = History(self.hass, {}, {'device_id': 'synthetic-device'}, {})

    async def asyncTearDown(self):
        await self.history.work.close()

    async def test_concurrent_page_loads_share_cloud_and_recorder_work(self):
        async def api(path):
            await asyncio.sleep(.01)
            return {'datas': [{'id': 'one', 'time': 1000}]}
        self.history.api = AsyncMock(side_effect=api)
        self.history.add_recorded_settings = AsyncMock()
        results = await asyncio.gather(*(self.history.listing(1, True) for _ in range(10)))
        self.assertEqual(self.history.api.await_count, 1)
        self.assertEqual(self.history.add_recorded_settings.await_count, 1)
        self.assertTrue(all(result is results[0] for result in results))
        # Forced refresh is cooled down too, not merely ordinary cached reads.
        await self.history.listing(1, True)
        self.assertEqual(self.history.api.await_count, 1)

    async def test_refresh_reloads_after_cooldown(self):
        self.history.api = AsyncMock(return_value={'datas': []})
        await self.history.listing(1, True)
        stamp, value = self.history.pages[1]
        self.history.pages[1] = (stamp - 31, value)
        await self.history.listing(1, False)
        self.assertEqual(self.history.api.await_count, 1)
        await self.history.listing(1, True)
        self.assertEqual(self.history.api.await_count, 2)

    async def test_maps_share_metadata_download_and_decode(self):
        self.history.records['one'] = {'id': 'one'}
        async def api(path):
            await asyncio.sleep(.01)
            return {'app_map': 'https://storage.example/map'}
        self.history.api = AsyncMock(side_effect=api)
        with patch.dict(environment, download_map=AsyncMock(return_value=b'map'), decode_map=lambda *args: None):
            results = await asyncio.gather(*(self.history.image('one') for _ in range(10)))
            self.assertEqual(results, ['png'] * 10)
            self.assertEqual(self.history.api.await_count, 1)
            self.assertEqual(environment['download_map'].await_count, 1)
            self.assertEqual(self.hass.async_add_executor_job.await_count, 1)

    async def test_negative_type_results_are_shared_and_expire(self):
        self.history.api = AsyncMock(return_value={'logs': []})
        record = {'id': 'one', 'time': 1000, 'minutes': 10}
        await asyncio.gather(*(self.history.cleaning_type(record) for _ in range(10)))
        self.assertEqual(self.history.api.await_count, 1)
        stamp, _ = self.history.types['one']
        self.history.types['one'] = (stamp - 301, None)
        await self.history.cleaning_type(record)
        self.assertEqual(self.history.api.await_count, 2)

    async def test_distinct_work_is_bounded_even_after_callers_cancel(self):
        started = asyncio.Event()
        blocker = asyncio.Event()
        async def blocked():
            started.set()
            await blocker.wait()
        callers = [asyncio.create_task(self.history.work.run(i, blocked)) for i in range(4)]
        await started.wait()
        for caller in callers:
            caller.cancel()
        await asyncio.gather(*callers, return_exceptions=True)
        with self.assertRaises(security.HistoryBusy):
            await self.history.work.run('extra', blocked)
        self.assertEqual(len(self.history.work.tasks), 4)
        blocker.set()
        await asyncio.gather(*list(self.history.work.tasks.values()))

    async def test_failed_work_can_be_retried(self):
        factory = AsyncMock(side_effect=ValueError)
        with self.assertRaises(ValueError):
            await self.history.work.run('one', factory)
        result = await self.history.work.run('one', AsyncMock(return_value='ok'))
        self.assertEqual(result, 'ok')

    async def test_close_cancels_work_and_rejects_new_requests(self):
        started = asyncio.Event()
        async def blocked():
            started.set()
            await asyncio.Event().wait()
        caller = asyncio.create_task(self.history.work.run('one', blocked))
        await started.wait()
        await self.history.work.close()
        self.assertTrue(caller.cancelled())
        with self.assertRaises(security.HistoryBusy):
            await self.history.work.run('two', blocked)

    async def test_websocket_permissions_filter_copies_of_cached_records(self):
        cached = {'records': [{'id': 'one', 'suction': 'high', 'water': 'low', 'cleaning_type': 'zone'}]}
        self.history.setting_entities = {'suction': 'select.power', 'water': 'select.water'}
        self.history.listing = AsyncMock(return_value=cached)
        self.hass.data = {'tuvio_history': {'history': self.history}}
        responses = []
        for allowed in ({'select.power', 'select.water'}, {'select.water'}, set()):
            user = SimpleNamespace(id='user', permissions=SimpleNamespace(
                check_entity=lambda entity, policy: entity in allowed))
            connection = SimpleNamespace(user=user, send_result=lambda mid, value: responses.append(value),
                                         send_error=lambda *args: self.fail(str(args)))
            await environment['ws_list'](self.hass, connection, {'id': 1, 'page': 1, 'refresh': False})
        self.assertEqual(responses[0], cached)
        self.assertNotIn('suction', responses[1]['records'][0])
        self.assertEqual(responses[2]['records'][0], {'id': 'one', 'cleaning_type': 'zone'})
        self.assertEqual(cached['records'][0]['suction'], 'high')

    async def test_websocket_overload_rejected_before_cloud_work(self):
        blocker = asyncio.Event()
        async def listing(*args):
            await blocker.wait()
            return {'records': []}
        self.history.listing = AsyncMock(side_effect=listing)
        self.hass.data = {'tuvio_history': {'history': self.history}}
        errors = []
        connection = SimpleNamespace(user=SimpleNamespace(id='user'), send_result=lambda *args: None,
                                     send_error=lambda mid, code, text: errors.append(code))
        tasks = [asyncio.create_task(environment['ws_list'](self.hass, connection,
                  {'id': i, 'page': 1, 'refresh': True})) for i in range(12)]
        await asyncio.sleep(.01)
        self.assertEqual(self.history.listing.await_count, 2)
        self.assertEqual(errors, ['history_busy'] * 10)
        blocker.set()
        await asyncio.gather(*tasks)

    async def test_unauthenticated_commands_do_not_start_work(self):
        for name in ('ws_list', 'ws_map'):
            errors = []
            connection = SimpleNamespace(user=None, send_error=lambda mid, code, text: errors.append(code))
            await environment[name](self.hass, connection, {'id': 1})
            self.assertEqual(errors, ['unauthorized'])


class DestinationTests(unittest.IsolatedAsyncioTestCase):
    def test_rejects_unsafe_urls_and_preserves_signed_query(self):
        for value in ('http://storage.example/map', 'https://user:pass@storage.example/map',
                      'https://storage.example:8443/map', 'https://127.0.0.1/map',
                      'https://10.0.0.1/map', 'https://169.254.169.254/map',
                      'https://[::1]/map', 'https://[fd00::1]/map',
                      'https://[::ffff:127.0.0.1]/map', 'https://224.0.0.1/map'):
            with self.subTest(value=value), self.assertRaises(ValueError):
                security.map_url(value)
        signed = 'https://storage.example/map?sign=a%2Fb%2B%3D&x=one+two'
        self.assertEqual(str(security.map_url(signed)), signed)

    async def test_resolver_rejects_any_non_public_dns_answer(self):
        resolver = security.PublicResolver()
        for address in ('127.0.0.1', '192.168.1.1', '169.254.169.254', '::1', 'fe80::1',
                        'fc00::1', '::ffff:10.0.0.1', '224.0.0.1', '0.0.0.0', '192.0.2.1'):
            with self.subTest(address=address):
                resolver.resolver.resolve = AsyncMock(return_value=[{'host': '8.8.8.8'}, {'host': address}])
                with self.assertRaises(OSError):
                    await resolver.resolve('storage.example', 443)
        resolver.resolver.resolve = AsyncMock(return_value=[])
        with self.assertRaises(OSError):
            await resolver.resolve('storage.example', 443)
        await resolver.close()

    async def test_connector_consumes_checked_results_without_second_resolution(self):
        resolver = security.PublicResolver()
        def answer(ip):
            return {'hostname': 'storage.example', 'host': ip, 'port': 443,
                    'family': 2, 'proto': 0, 'flags': 0}
        resolver.resolver.resolve = AsyncMock(side_effect=[[answer('8.8.8.8')], [answer('127.0.0.1')]])
        async with security.aiohttp.TCPConnector(resolver=resolver, use_dns_cache=False) as connector:
            result = await connector._resolve_host('storage.example', 443)
            self.assertEqual(result[0]['host'], '8.8.8.8')
            self.assertEqual(resolver.resolver.resolve.await_count, 1)
            with self.assertRaises(OSError):
                await connector._resolve_host('storage.example', 443)
        await resolver.close()

    async def test_redirect_is_not_followed_and_no_credentials_or_proxy_are_used(self):
        response = AsyncMock()
        response.status = 302
        request = AsyncMock()
        request.__aenter__.return_value = response
        session = SimpleNamespace(get=lambda *args, **kwargs: request)
        session_context = AsyncMock()
        session_context.__aenter__.return_value = session
        with patch.object(security.aiohttp, 'ClientSession', return_value=session_context) as factory:
            with self.assertRaises(ValueError):
                await security.download_map('https://storage.example/map')
        self.assertEqual(request.__aenter__.await_count, 1)
        self.assertFalse(factory.call_args.kwargs['trust_env'])
        self.assertIsInstance(factory.call_args.kwargs['cookie_jar'], security.aiohttp.DummyCookieJar)

    async def test_public_download_and_size_limit(self):
        async def chunks(size):
            yield b'x' * size
        for size in (3, 8_000_001):
            response = SimpleNamespace(status=200, raise_for_status=lambda: None,
                content=SimpleNamespace(iter_chunked=lambda _: chunks(size)))
            request = AsyncMock()
            request.__aenter__.return_value = response
            get = unittest.mock.Mock(return_value=request)
            session_context = AsyncMock()
            session_context.__aenter__.return_value = SimpleNamespace(get=get)
            with patch.object(security.aiohttp, 'ClientSession', return_value=session_context):
                if size > 8_000_000:
                    with self.assertRaises(ValueError):
                        await security.download_map('https://storage.example/map')
                else:
                    self.assertEqual(await security.download_map('https://storage.example/map'), b'xxx')
            self.assertEqual(get.call_args.kwargs, {'allow_redirects': False})


if __name__ == '__main__':
    unittest.main()
