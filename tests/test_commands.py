"""The check contract, placeholders, timeouts, and session commands."""
import json
import sys
import time
import unittest
from pathlib import Path

from gated_delivery import CommandCheck, CommandSession, Finding, GateError, SessionRequest, parse_check_output
from gated_delivery.commands import fill

from tests.support import FolderTest, put

PY = sys.executable


class Contract(unittest.TestCase):

    def test_exit_0_passes_with_warnings(self):
        v = parse_check_output(0, json.dumps({'failures': [], 'warnings': [{'check': 'a', 'message': 'm'}]}))
        self.assertTrue(v.ok)
        self.assertEqual(v.warnings, [Finding('a', 'm')])

    def test_exit_0_with_no_output_passes(self):
        self.assertTrue(parse_check_output(0, '').ok)

    def test_exit_1_fails_with_its_findings(self):
        v = parse_check_output(1, json.dumps({'failures': [{'check': 'port', 'message': 'too low'}, 'name: bad', 'plain text']}))
        self.assertEqual(v.failures, [Finding('port', 'too low'), Finding('name', 'bad'), Finding(None, 'plain text')])

    def test_exit_1_without_findings_still_fails(self):
        v = parse_check_output(1, '', 'Traceback\nSomething broke')
        self.assertFalse(v.ok)
        self.assertIn('listed no failure', v.failures[0].message)
        self.assertIn('Something broke', v.failures[0].message)

    def test_a_broken_check_is_an_error_not_a_verdict(self):
        cases = [(2, '', 'exited 2'), (None, '', 'timed out'), (1, 'FAIL port', 'not JSON'), (1, '[1]', 'not an object'),
                 (0, json.dumps({'failures': ['x']}), 'exited 0 but listed'), (1, json.dumps({'failures': 'x'}), 'not a list'),
                 (1, json.dumps({'failures': [{'check': 'x'}]}), 'no "message"'), (1, json.dumps({'failures': [3]}), 'neither')]
        for code, out, msg in cases:
            with self.subTest(code=code, out=out), self.assertRaisesRegex(GateError, msg):
                parse_check_output(code, out)

    def test_placeholders_are_quoted_and_other_braces_left_alone(self):
        cmd = fill("awk '{print $1}' {dir}/x {unknown} {here}", dir='/a b', here="it's")
        self.assertEqual(cmd, "awk '{print $1}' '/a b'/x {unknown} 'it'\"'\"'s'")


class Commands(FolderTest):

    def test_a_check_runs_inside_the_folder_and_gets_its_path(self):
        spaced = self.tmp / 'with space'
        spaced.mkdir()
        put(spaced, 'cfg', 'x')
        script = self.tmp / 'chk.py'
        script.write_text('import json, os, sys\n'
                          'ok = os.path.exists("cfg") and os.path.samefile(sys.argv[1], ".")\n'
                          'print(json.dumps({"failures": [] if ok else ["wrong folder"]}))\n'
                          'sys.exit(0 if ok else 1)\n')
        check = CommandCheck('%s {here}/chk.py {dir}' % PY, here=self.tmp)
        self.assertTrue(check(spaced).ok)

    def test_a_check_that_hangs_is_killed_with_its_children(self):
        check = CommandCheck('sleep 30 & sleep 30; wait', timeout=0.5)
        t = time.monotonic()
        with self.assertRaisesRegex(GateError, 'timed out'):
            check(self.dir)
        self.assertLess(time.monotonic() - t, 10)

    def test_a_session_gets_its_role_prompt_and_a_log(self):
        prompt = self.dir / '.gate/prompts/1-produce.md'
        put(self.dir, '.gate/prompts/1-produce.md', 'do the thing\n')
        log = self.dir / '.gate/sessions/1-produce.log'
        session = CommandSession('echo "$GATE_ROLE $GATE_ATTEMPT {role}"; cat {prompt}; echo oops >&2; exit 3')
        code = session(SessionRequest('produce', 1, 1, self.dir, prompt, 'do the thing', log))
        self.assertEqual(code, 3)
        self.assertEqual(log.read_text().split('\n')[:3], ['produce 1 produce', 'do the thing', 'oops'])

    def test_a_session_that_runs_too_long_returns_none(self):
        log = self.dir / 'log'
        session = CommandSession('sleep 30', timeout=0.3)
        self.assertIsNone(session(SessionRequest('repair', 2, 2, self.dir, Path('/dev/null'), '', log)))


if __name__ == '__main__':
    unittest.main()
