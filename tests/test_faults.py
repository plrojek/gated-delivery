"""The planted-fault runner: it catches what it should, and says so when a fault is missed."""
import json
import shutil
import unittest

from gated_delivery import Fault, run_faults
from gated_delivery.faults import FaultError, apply_edit, load_faults

from tests.support import EXAMPLE, FolderTest, ScriptedCheck, put


class Example(unittest.TestCase):

    def test_every_planted_fault_of_the_example_is_caught(self):
        base, check, faults, here = load_faults(EXAMPLE / 'faults.json')
        lines = []
        ok, outcomes = run_faults(base, check, faults, here=here, echo=lines.append)
        self.assertTrue(ok, '\n'.join(lines))
        self.assertEqual(len(outcomes), 17)
        self.assertEqual(lines[0], 'base:  passes, 0 warning(s)')
        self.assertEqual(lines[-1], '17 fault(s), 0 missed')
        self.assertEqual({o.fault.expect for o in outcomes}, {'fail', 'warn', 'pass'})


class Runner(FolderTest):

    def setUp(self):
        super().setUp()
        self.base = self.tmp / 'base'
        put(self.base, 'art.json', {'fail': [], 'warn': ['old']})

    def run_faults(self, faults):
        lines = []
        ok, outcomes = run_faults(self.base, ScriptedCheck(), faults, echo=lines.append)
        return ok, outcomes, lines

    def test_a_fault_aimed_at_the_wrong_check_is_missed(self):
        ok, outcomes, lines = self.run_faults([
            Fault('right', 'x', [{'json_set': {'file': 'art.json', 'path': 'fail', 'value': ['boom']}}]),
            Fault('wrong id', 'y', [{'json_set': {'file': 'art.json', 'path': 'fail', 'value': ['boom']}}]),
        ])
        self.assertFalse(ok)
        self.assertEqual([o.caught for o in outcomes], [True, False])
        self.assertEqual(outcomes[1].also, ['x'])
        self.assertTrue(lines[2].startswith('MISSED  wrong id'))
        self.assertEqual(lines[-1], '2 fault(s), 1 missed')

    def test_a_base_that_fails_proves_nothing(self):
        put(self.base, 'art.json', {'fail': ['already broken']})
        ok, outcomes, lines = self.run_faults([Fault('f', 'x', [])])
        self.assertFalse(ok)
        self.assertEqual(outcomes, [])
        self.assertEqual(lines[0], 'base:  FAILS')

    def test_a_warning_the_base_already_had_is_not_a_catch(self):
        ok, outcomes, _ = self.run_faults([
            Fault('same warning', 'w', [], expect='warn'),
            Fault('new warning', 'w', [{'json_set': {'file': 'art.json', 'path': 'warn', 'value': ['old', 'new']}}], expect='warn'),
        ])
        self.assertEqual([o.caught for o in outcomes], [False, True])

    def test_a_pass_guard_that_fires_is_wrong(self):
        ok, outcomes, lines = self.run_faults([
            Fault('quiet', 'x', [], expect='pass'),
            Fault('noisy', 'x', [{'json_set': {'file': 'art.json', 'path': 'fail', 'value': ['oops']}}], expect='pass'),
        ])
        self.assertEqual([o.caught for o in outcomes], [True, False])
        self.assertTrue(lines[1].startswith('ok '))
        self.assertTrue(lines[2].startswith('WRONG '))

    def test_contains_must_match_the_message(self):
        edit = [{'json_set': {'file': 'art.json', 'path': 'fail', 'value': ['port 80 is low']}}]
        ok, outcomes, _ = self.run_faults([Fault('a', 'x', edit, contains='port 80'), Fault('b', 'x', edit, contains='port 81')])
        self.assertEqual([o.caught for o in outcomes], [True, False])

    def test_an_edit_that_cannot_be_applied_is_a_miss_with_the_reason(self):
        ok, outcomes, _ = self.run_faults([Fault('bad edit', 'x', [{'replace': {'file': 'art.json', 'old': 'nowhere', 'new': 'x'}}])])
        self.assertFalse(ok)
        self.assertIn('occurs 0 times', outcomes[0].seen)


class Edits(FolderTest):

    def test_each_kind_of_edit(self):
        put(self.dir, 'c.json', {'a': {'b': [1, 2, 3]}, 'keep': 1})
        put(self.dir, 't.txt', 'one two')
        for e in ({'json_set': {'file': 'c.json', 'path': 'a.b.1', 'value': 20}},
                  {'json_set': {'file': 'c.json', 'path': 'new.deep', 'value': True}},
                  {'json_delete': {'file': 'c.json', 'path': 'keep'}},
                  {'replace': {'file': 't.txt', 'old': 'two', 'new': '2'}},
                  {'write': {'file': 'sub/n.txt', 'text': 'n'}},
                  {'delete': {'file': 'sub/n.txt'}},
                  {'run': 'echo ran > {dir}/ran.txt'},
                  lambda folder: put(folder, 'callable.txt', 'yes')):
            apply_edit(self.dir, e, self.tmp)
        self.assertEqual(self.read('c.json'), {'a': {'b': [1, 20, 3]}, 'new': {'deep': True}})
        self.assertEqual((self.dir / 't.txt').read_text(), 'one 2')
        self.assertFalse((self.dir / 'sub/n.txt').exists())
        self.assertEqual((self.dir / 'ran.txt').read_text(), 'ran\n')
        self.assertTrue((self.dir / 'callable.txt').exists())

    def test_edits_stay_inside_the_copy(self):
        for e in ({'write': {'file': '../escape.txt', 'text': 'x'}}, {'delete': {'file': '/etc/hosts'}},
                  {'json_set': {'file': 'missing.json', 'path': 'a', 'value': 1}}, {'frobnicate': {}}, {'run': 'exit 4'},
                  {'replace': {'file': 'x'}}, 'not an edit'):
            with self.subTest(edit=e), self.assertRaises(FaultError):
                apply_edit(self.dir, e, self.tmp)
        self.assertFalse((self.tmp / 'escape.txt').exists())

    def test_a_faults_file_is_checked_when_read(self):
        bad = self.tmp / 'faults.json'
        for spec in ({'check': 'true', 'faults': []}, {'faults': [{'name': 'n', 'check': 'c'}]},
                     {'check': 'true', 'faults': [{'name': 'n'}]}, {'check': 'true', 'faults': [{'name': 'n', 'check': 'c', 'typo': 1}]}):
            bad.write_text(json.dumps(spec))
            with self.subTest(spec=spec), self.assertRaises(Exception):
                load_faults(bad)
        shutil.rmtree(self.dir)


if __name__ == '__main__':
    unittest.main()
