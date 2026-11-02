"""World movement tests: real temporary stores, deterministic UTC walking clock."""
import ast
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
from types import ModuleType
import unittest
from unittest.mock import Mock, patch
from datetime import datetime, timedelta, timezone

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'tests' / 'fixtures'))
from chiyo_bundle import world_move_tool as tool
from chiyo_bundle.world_read_service import initialize
from world_gateway_fixture import WorldGatewayFixture


class WorldMoveTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(dir=os.environ.get('PI_SCRATCH_DIR'))
        self.addCleanup(self.temp.cleanup)
        self.home = Path(self.temp.name) / 'world-owner'
        initialize(self.home)
        # The action lifecycle is resolved from the *process* environment (the intent
        # executor does not take the handler's ``environment`` mapping), so point it at
        # this case's own home.  Otherwise the records of the repository's historical
        # ``data/world/run/action_lifecycle.jsonl`` leak in and every segment is refused
        # as DUPLICATE_EXECUTION.
        self._saved_lifecycle_env = {key: os.environ.get(key) for key in
                                     ('WORLD_ACTION_LIFECYCLE_PATH', 'CHIYO_WORLD_HOME',
                                      'WORLD_BODY_INTENT_MUTATION_GATE')}
        os.environ['WORLD_ACTION_LIFECYCLE_PATH'] = str(
            self.home / 'action_lifecycle.jsonl')
        os.environ['CHIYO_WORLD_HOME'] = str(self.home)
        # Real movement is what this fixture tests; the production default gate is off,
        # so the isolated case opts in explicitly (and restores the previous value).
        os.environ['WORLD_BODY_INTENT_MUTATION_GATE'] = 'canonical'
        self.addCleanup(self._restore_lifecycle_env)
        self.env = {'CHIYO_WORLD_HOME': str(self.home),
                    'CHIYO_WORLD_LIVING_SKELETON_ENABLED': 'true',
                    'CHIYO_WORLD_TRAVEL_TIME_ENABLED': 'false'}
        self.now = datetime(2026, 1, 1, 3, tzinfo=timezone.utc)
        self.wls, self.wtd = tool._world_modules()
        # 计时旅程的每一段都是新的 Action：本 case 自己的真实 gateway + registry。
        # 服务侧 Resolver 用 config.environment 决定 MOVE 是否按真实步行计时提交，
        # 必须与计时测试的请求环境一致（即时模式走工具自己的直写路径，不经服务）。
        self.gateway = WorldGatewayFixture(
            self.home, world_id='shiomi_city',
            environment=dict(self.env, CHIYO_WORLD_TRAVEL_TIME_ENABLED='true'),
            now=self.now)
        self.addCleanup(self.gateway.stop)
        self.env.update(self.gateway.request_environment())

    def _restore_lifecycle_env(self):
        for key, value in self._saved_lifecycle_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def move(self, location, now=None):
        # 业务时刻同时交给 gateway 的 Resolver，travel 时间与旅程账本保持同一时间域。
        self.gateway.set_now(now or self.now)
        return json.loads(tool.move_handler({'location': location}, environment=self.env,
                                            now=now or self.now))

    def position(self):
        return self.wls.read_move_state(hermes_home=self.home,
                                       environment=self.env, now=self.now)['world_location_id']

    def test_schema_requires_bounded_location_only(self):
        self.assertEqual(tool.MOVE_TOOL_SCHEMA['name'], 'world_move')
        params = tool.MOVE_TOOL_SCHEMA['parameters']
        self.assertEqual(params['required'], ['location'])
        self.assertFalse(params['additionalProperties'])
        self.assertEqual(params['properties']['location']['type'], 'string')

    def test_direct_move_and_json_arrival(self):
        result = self.move('nearby_street')
        self.assertTrue(result['ok'])
        self.assertEqual(result['status'], 'arrived')
        self.assertEqual(self.position(), 'nearby_street')
        self.assertIn('已到达', result['message'])

    def test_automatic_street_transfer(self):
        self.move('公园')
        with patch.object(self.wls, 'apply_move_to', wraps=self.wls.apply_move_to) as apply:
            result = self.move('便利店')
        self.assertEqual([call.args[0] for call in apply.call_args_list],
                         ['nearby_street', 'convenience_store'])
        self.assertEqual(result['route'], ['nearby_street', 'convenience_store'])
        self.assertEqual(self.position(), 'convenience_store')

    def test_chinese_aliases_and_standard_ids(self):
        for name, target in [('街道', 'nearby_street'), ('便利店', 'convenience_store'),
                             ('公园', 'park'), ('学校', 'school'), ('家居店', 'home_goods_store'),
                             ('家居用品店', 'home_goods_store'), ('房间', 'home'), ('家', 'home'),
                             (' HOME ', 'home')]:
            with self.subTest(name=name):
                result = self.move(name)
                self.assertTrue(result['ok'], result)
                self.assertEqual(self.position(), target)

    def test_already_at_location_does_not_write(self):
        path = self.wls.world_state_path(self.home)
        before = path.read_bytes()
        self.assertEqual(self.move('家')['status'], 'already_there')
        self.assertEqual(path.read_bytes(), before)

    def test_real_walking_time_and_in_progress_blocks_redirect(self):
        self.move('街道')
        self.env['CHIYO_WORLD_TRAVEL_TIME_ENABLED'] = 'true'
        started = self.move('便利店')
        self.assertEqual(started['status'], 'started')
        self.assertEqual(started['estimated_minutes'], 5)
        self.assertEqual(self.position(), 'nearby_street')
        # 这一段确实走了正式授权口：同 execution 的一次性票已被 gateway 消费。
        segment = json.loads(
            (self.home / 'data/world/world_journey.json').read_text(encoding='utf-8')
        )['active']['segments'][0]
        issued = {row['execution_id']: row for row in self.gateway.authorizations()}
        self.assertEqual(str(issued[segment['execution_id']]['state']).upper(), 'CONSUMED')
        travel_before = self.wtd.state_path(self.home).read_bytes()
        with patch.object(self.wls, 'apply_move_to', wraps=self.wls.apply_move_to) as apply:
            ongoing = self.move('公园', self.now + timedelta(seconds=61))
            apply.assert_not_called()
        self.assertEqual(ongoing['status'], 'in_progress')
        self.assertEqual(ongoing['destination_location_id'], 'convenience_store')
        self.assertEqual(ongoing['remaining_minutes'], 4)
        self.assertEqual(self.wtd.state_path(self.home).read_bytes(), travel_before)
        arrived = self.move('便利店', self.now + timedelta(minutes=5))
        # W02：这一次调用在旅程引擎内结算了最后一段并确认到达，如实返回 arrived；
        # 结算完成后再请求同一目标才是 already_there（下一条断言）。
        self.assertEqual(arrived['status'], 'arrived')
        self.assertEqual(
            self.move('便利店', self.now + timedelta(minutes=6))['status'], 'already_there')
        self.env['CHIYO_WORLD_TRAVEL_TIME_ENABLED'] = 'false'
        self.assertEqual(self.position(), 'convenience_store')

    def test_timed_transfer_never_teleports_or_skips_a_leg(self):
        self.move('公园')
        self.env['CHIYO_WORLD_TRAVEL_TIME_ENABLED'] = 'true'
        first = self.move('便利店')
        self.assertEqual(first['destination_location_id'], 'nearby_street')
        self.assertEqual(first['estimated_minutes'], 9)
        self.assertEqual(self.position(), 'park')
        self.assertIn('中转', first['message'])
        second = self.move('便利店', self.now + timedelta(minutes=9))
        self.assertEqual(second['destination_location_id'], 'convenience_store')
        self.assertEqual(second['estimated_minutes'], 5)
        self.assertEqual(self.position(), 'nearby_street')
        # W02：结算最后一段的调用返回 arrived；已完成后的重复调用才是 already_there。
        self.assertEqual(
            self.move('便利店', self.now + timedelta(minutes=14))['status'], 'arrived')
        self.assertEqual(
            self.move('便利店', self.now + timedelta(minutes=15))['status'], 'already_there')

    def test_fail_closed_input_and_feature_gate(self):
        for args in [None, {}, {'location': 42}, {'location': ''}, {'location': 'x' * 97},
                     {'location': '家', 'home': str(self.home)}, {'location': '不存在'}]:
            with self.subTest(args=args):
                result = json.loads(tool.move_handler(args, environment=self.env))
                self.assertFalse(result['ok'])
        self.env['CHIYO_WORLD_LIVING_SKELETON_ENABLED'] = 'false'
        self.assertEqual(self.move('街道')['status'], 'disabled')
        self.assertEqual(self.position(), 'home')

    def test_corrupt_travel_state_prevents_new_move(self):
        self.env['CHIYO_WORLD_TRAVEL_TIME_ENABLED'] = 'true'
        self.wtd.state_path(self.home).write_text('{invalid', encoding='utf-8')
        result = self.move('街道')
        self.assertFalse(result['ok'])
        self.assertEqual(result['reason_code'], 'ARRIVAL_REALIZATION_FAILED')
        self.assertEqual(self.position(), 'home')

    def test_failed_transfer_does_not_continue(self):
        with patch.object(self.wls, 'apply_move_to', return_value={
                'status': 'rejected', 'reason_code': 'LOCATION_CLOSED'}) as apply:
            result = self.move('便利店')
        self.assertFalse(result['ok'])
        self.assertEqual(apply.call_count, 1)
        self.assertEqual(result['reason_code'], 'LOCATION_CLOSED')

    def test_exception_is_json_without_sensitive_details(self):
        with patch.object(self.wtd, 'realize_due_travel', side_effect=RuntimeError('secret/path')):
            result = self.move('街道')
        self.assertFalse(result['ok'])
        self.assertEqual(result['error_type'], 'RuntimeError')
        self.assertNotIn('secret', json.dumps(result))

    def test_home_resolution_and_reject_relative_path(self):
        self.assertEqual(tool.resolve_world_home(self.env), self.home)
        self.assertEqual(tool.resolve_world_home({'HERMES_HOME': str(self.home)}), self.home)
        socket = self.home / 'data/world/run/read.sock'
        self.assertEqual(tool.resolve_world_home({'WORLD_BODY_SOCKET': str(socket)}), self.home)
        profile = Path(self.temp.name) / 'profile'
        (profile / 'chiyo').mkdir(parents=True)
        (profile / 'chiyo/config.json').write_text(json.dumps({'world_body_socket': str(socket)}))
        self.assertEqual(tool.resolve_world_home({'HERMES_HOME': str(profile)}), self.home)
        self.env['CHIYO_WORLD_HOME'] = 'relative/world'
        self.assertFalse(self.move('街道')['ok'])

    def test_grounded_context_in_both_clients_is_read_only_and_graceful(self):
        self.move('街道')
        self.env['CHIYO_WORLD_TRAVEL_TIME_ENABLED'] = 'true'
        with patch.object(tool, 'datetime') as clock:
            clock.now.return_value = self.now
            clock.fromisoformat.side_effect = datetime.fromisoformat
            self.move('便利店')
            for index, relative in enumerate(['components/native/integration/world_body_client.py',
                                              'components/world/service/bridge/world_body_client.py']):
                spec = importlib.util.spec_from_file_location(f'move_client_{index}', ROOT / relative)
                module = importlib.util.module_from_spec(spec)
                sys.modules[spec.name] = module
                self.addCleanup(sys.modules.pop, spec.name, None)
                spec.loader.exec_module(module)
                client = module.WorldBodyClient(environment=self.env, mode='canonical')
                client.get_status = Mock(return_value=module.ClientResult(module.OK, {'ready': True}))
                client.get_snapshot = Mock(return_value=module.ClientResult(module.OK, {
                    'location': {'place_id': 'nearby_street'}, 'pose': 'standing'}))
                client.get_body_signals = Mock(return_value=module.ClientResult(module.OK, {}))
                before = self.wtd.state_path(self.home).read_bytes()
                with patch.object(self.wtd, 'realize_due_travel') as realize:
                    context = module.render_grounded_context(client)
                    realize.assert_not_called()
                self.assertIn('正在步行前往便利店的途中(剩余约5分钟到达)', context)
                self.assertEqual(self.wtd.state_path(self.home).read_bytes(), before)
                with patch.object(tool, 'travel_context', side_effect=ValueError('unavailable')):
                    self.assertIn('location=nearby_street', module.render_grounded_context(client))
                client.environment['CHIYO_WORLD_TRAVEL_TIME_ENABLED'] = 'false'
                self.assertNotIn('出行状态', module.render_grounded_context(client))
                client.get_status.return_value.data['ready'] = False
                self.assertEqual(module.render_grounded_context(client), '')

    def test_bridge_copies_remain_identical(self):
        self.assertEqual((ROOT / 'components/native/integration/world_body_client.py').read_bytes(),
                         (ROOT / 'components/world/service/bridge/world_body_client.py').read_bytes())

    def test_plugin_registers_schema_and_handler_without_optional_world_import(self):
        # Isolate heavyweight Hermes services; test the actual plugin entry point.
        owner = ModuleType('chiyo_bundle.hermes_plugin')
        names = ['ChiyoContextEngine', 'memory_command', 'status_command', 'consider_command',
                 'note_command', 'close_services', 'configure_host_llm', 'memory_tool_gate',
                 'write_status_evidence']
        for name in names:
            setattr(owner, name, Mock())
        spec = importlib.util.spec_from_file_location('move_plugin', ROOT / 'plugins/chiyo/__init__.py')
        plugin = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(plugin)
        ctx = Mock()
        with patch.dict(sys.modules, {'chiyo_bundle.hermes_plugin': owner}):
            plugin.register(ctx)
        # The plugin now registers more than one tool (world_move and her phone), so the
        # *last* call is not necessarily world_move; pick the registration by name.
        registrations = {call.kwargs['name']: call.kwargs
                         for call in ctx.register_tool.call_args_list}
        self.assertIn('world_move', registrations)
        registration = registrations['world_move']
        self.assertEqual(registration['name'], 'world_move')
        self.assertEqual(registration['toolset'], 'chiyo_world')
        self.assertIs(registration['schema'], tool.MOVE_TOOL_SCHEMA)
        self.assertIs(registration['handler'], tool.move_handler)

    def test_memory_gate_allows_only_bounded_world_tool(self):
        # Run the real function in isolation; no network/provider dependency needed.
        source = ast.parse((ROOT / 'chiyo_bundle/hermes_plugin.py').read_text(encoding='utf-8'))
        node = next(node for node in source.body if isinstance(node, ast.FunctionDef)
                    and node.name == 'memory_tool_gate')
        namespace = {'configuration': lambda: (self.home, {'memory': True})}
        exec(compile(ast.Module(body=[node], type_ignores=[]), 'memory_tool_gate', 'exec'), namespace)
        gate = namespace['memory_tool_gate']
        self.assertIsNone(gate('world_move', {'location': '家'}))
        self.assertEqual(gate('read_file', {})['action'], 'block')
        self.assertEqual(gate('session_search', {})['action'], 'block')
        namespace['configuration'] = Mock(side_effect=ValueError('corrupt'))
        self.assertEqual(gate('world_move', {})['action'], 'block')


if __name__ == '__main__':
    unittest.main()
