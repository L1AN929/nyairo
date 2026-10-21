#!/usr/bin/env python3
"""Bounded continuation tests; retained fixtures live only in session scratch."""
import contextlib
import copy
import hashlib
import importlib.util
import io
import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('task_resume', ROOT/'scripts/task_resume.py')
resume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(resume)


class TaskResumeTest(unittest.TestCase):
    def setUp(self):
        scratch = os.environ.get('PI_SCRATCH_DIR')
        if not scratch:
            raise unittest.SkipTest('PI_SCRATCH_DIR required; fixtures are retained')
        self.root = Path(tempfile.mkdtemp(prefix='continuity-test-', dir=scratch))
        self.anchor = self.root/'anchor.txt'
        self.anchor.write_text('unchanged business source', encoding='utf-8')
        self.checkpoint = self.root/'current.json'
        self.state = dict(schema='agent.task-continuity.v1', revision=1,
                          updated_at='2026-10-19T23:00:00+08:00', goal='continue day life',
                          phase='stage delivered', stop_point='no action open',
                          next_actions=['read current report, not old suites'],
                          do_not_repeat=['do not reopen spent runtime'], boundaries=['no reset'],
                          repository=dict(head='abc', branch='work'), writers=[],
                          runtime=dict(remaining_authorized_runtime_seconds=0),
                          evidence=[dict(path='anchor.txt')],
                          anchors=[dict(path='anchor.txt', sha256=hashlib.sha256(self.anchor.read_bytes()).hexdigest())])
        self.patch = mock.patch.object(resume, 'git', side_effect=lambda repo, *args: 'abc' if args[-1] == 'HEAD' else 'work')
        self.patch.start()
        self.addCleanup(self.patch.stop)

    def read(self):
        self.checkpoint.write_text(json.dumps(self.state), encoding='utf-8')
        return resume.inspect(self.checkpoint, self.root)

    def test_fresh_reader_restores_goal_stop_next_and_spent_budget(self):
        report = self.read()
        self.assertEqual(report['status'], 'READY')
        for key in ('goal', 'stop_point', 'next_actions', 'do_not_repeat', 'runtime'):
            self.assertEqual(report['checkpoint'][key], self.state[key])
        self.assertIn('historical', report['warnings'][-1])

    def test_changed_anchor_stops_without_repairing_it(self):
        self.anchor.write_text('another writer changed it', encoding='utf-8')
        report = self.read()
        self.assertEqual(report['status'], 'STOP')
        self.assertIn('anchor changed', report['errors'][0])
        self.assertEqual(self.anchor.read_text(), 'another writer changed it')

    def test_changed_branch_stops(self):
        self.state['repository']['branch'] = 'wrong'
        self.assertEqual(self.read()['status'], 'STOP')

    def test_missing_evidence_warns_without_claiming_health(self):
        self.state['evidence'] = [dict(path='absent.json')]
        report = self.read()
        self.assertEqual(report['status'], 'READY')
        self.assertIn('evidence unavailable', report['warnings'][0])

    def test_invalid_or_missing_checkpoint_stops(self):
        self.assertEqual(resume.inspect(self.checkpoint, self.root)['status'], 'STOP')
        self.checkpoint.write_text('{partial', encoding='utf-8')
        self.assertEqual(resume.inspect(self.checkpoint, self.root)['status'], 'STOP')
        self.checkpoint.write_text('{"goal":"a","goal":"b"}', encoding='utf-8')
        self.assertIn('duplicate', resume.inspect(self.checkpoint, self.root)['errors'][0])

    def test_oversized_checkpoint_stops(self):
        self.state['goal'] = 'x' * 8001
        self.assertEqual(self.read()['status'], 'STOP')

    def test_escape_anchor_and_empty_state_stop(self):
        original = copy.deepcopy(self.state)
        self.state['anchors'][0]['path'] = '../outside'
        self.assertEqual(self.read()['status'], 'STOP')
        self.state = original
        self.state['anchors'] = []
        self.assertEqual(self.read()['status'], 'STOP')

    def test_cli_is_read_only_and_exit_codes_are_explicit(self):
        self.read()
        before = {p.name: p.read_bytes() for p in self.root.iterdir()}
        with contextlib.redirect_stdout(io.StringIO()) as output:
            code = resume.main(['--checkpoint', str(self.checkpoint), '--repo', str(self.root), '--json'])
        self.assertEqual(code, 0)
        self.assertEqual(json.loads(output.getvalue())['status'], 'READY')
        self.assertEqual(before, {p.name: p.read_bytes() for p in self.root.iterdir()})
        self.state['repository']['head'] = 'wrong'
        self.read()
        with contextlib.redirect_stdout(io.StringIO()):
            self.assertEqual(resume.main(['--checkpoint', str(self.checkpoint), '--repo', str(self.root)]), 2)


if __name__ == '__main__':
    unittest.main()
