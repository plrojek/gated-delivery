"""The command line, and the example end to end with fake sessions."""
import contextlib
import io
import json
import os
import shutil
import subprocess
import sys
import unittest

from gated_delivery.cli import main

from tests.support import EXAMPLE, ROOT, FolderTest, put


def cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        code = main([str(a) for a in argv])
    return code, out.getvalue().splitlines(), err.getvalue()


class Cli(FolderTest):

    def setUp(self):
        super().setUp()
        shutil.copy(EXAMPLE / 'base' / 'config.json', self.dir / 'config.json')
        self.check = 'python3 %s {dir}' % (EXAMPLE / 'checks.py')

    def test_status_then_next_by_hand(self):
        self.assertEqual(cli('status', self.dir)[1], ['produce'])
        code, out, _ = cli('next', self.dir, '--check', self.check, '--artifact', 'config.json')
        self.assertEqual((code, out[-1]), (0, 'second-look'))
        self.assertEqual(cli('status', self.dir)[1][-1], 'second-look')
        put(self.dir, 'review.json', {'verdict': 'ship as is', 'comments': []})
        code, out, _ = cli('next', self.dir, '--check', self.check, '--artifact', 'config.json')
        self.assertEqual(out[-1], 'deliver')
        self.assertTrue((self.dir / 'delivered/config.json').exists())

    def test_next_prints_failures_then_the_step(self):
        cfg = json.loads((self.dir / 'config.json').read_text())
        cfg.update(port=80, replicas=1)
        put(self.dir, 'config.json', cfg)
        code, out, _ = cli('next', self.dir, '--check', self.check, '--artifact', 'config.json')
        self.assertEqual(out, ['  port: port 80 is outside 1024-65535',
                               '  warn replicas: one replica: a restart takes the service down', 'repair'])

    def test_a_broken_check_exits_2_and_changes_nothing(self):
        code, out, err = cli('next', self.dir, '--check', 'exit 5', '--artifact', 'config.json')
        self.assertEqual(code, 2)
        self.assertIn('the check exited 5', err)
        self.assertFalse((self.dir / 'delivery.json').exists())

    def test_usage_errors_exit_2(self):
        self.assertEqual(cli('next', self.dir, '--artifact', 'x')[0], 2)
        self.assertEqual(cli('next', self.dir, '--check', 'true')[0], 2)
        self.assertEqual(cli('next', self.dir, '--check', 'true', '--artifact', '../x')[0], 2)
        put(self.tmp, 'bad.json', {'chek': 'typo'})
        code, _, err = cli('next', self.dir, '--config', self.tmp / 'bad.json')
        self.assertEqual(code, 2)
        self.assertIn('unknown key', err)
        self.assertEqual(cli('run', self.dir, '--check', 'true', '--artifact', 'x')[0], 2)   # no --session

    def test_config_paths_are_relative_to_the_config_and_flags_win(self):
        put(self.tmp, 'cfg/gate.json', {'check': 'exit 7', 'artifact': 'config.json', 'state': 'state.json', 'second_look': False})
        code, out, _ = cli('next', self.dir, '--config', self.tmp / 'cfg/gate.json', '--check', self.check)
        self.assertEqual(out[-1], 'deliver')
        self.assertTrue((self.tmp / 'cfg/state.json').exists())

    def test_prompts_writes_the_defaults_once(self):
        code, out, _ = cli('prompts', self.tmp / 'p')
        self.assertEqual(code, 0)
        self.assertEqual(sorted(os.listdir(self.tmp / 'p')), ['repair.md', 'revise.md', 'second-look.md'])
        self.assertEqual(cli('prompts', self.tmp / 'p')[0], 2)

    def test_faults_command(self):
        code, out, _ = cli('faults', EXAMPLE / 'faults.json')
        self.assertEqual((code, out[-1]), (0, '17 fault(s), 0 missed'))
        code, out, _ = cli('faults', EXAMPLE / 'faults.json', '--check', 'echo "{}"')     # a check that finds nothing
        self.assertEqual(code, 1)
        self.assertEqual(out[-1], '17 fault(s), 16 missed')                              # all but the pass guard


class Demo(unittest.TestCase):
    """the example from an empty folder to the end, through the real command line and shell"""

    def test_every_scenario_ends_where_it_should(self):
        p = subprocess.run([sys.executable, str(EXAMPLE / 'demo.py')], capture_output=True, text=True, cwd=ROOT, timeout=300)
        self.assertEqual(p.returncode, 0, p.stdout + p.stderr)
        blocks = {b.split('\n')[0]: b for b in p.stdout.split('== ')[1:]}
        expect = {'clean': 'review clean, 2 session(s)', 'repaired': 'review applied, 4 session(s)',
                  'not-delivered': 'nothing delivered', 'revise-breaks': 'replicas 1), review dropped, 3 session(s)',
                  'reviewer-edits': 'port 8080, replicas 1), review clean', 'no-review': 'review missing'}
        self.assertEqual(set(blocks), set(expect))
        for name, text in expect.items():
            self.assertIn(text, blocks[name], name)


if __name__ == '__main__':
    unittest.main()
