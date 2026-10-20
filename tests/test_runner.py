"""The loop: which sessions run, what their prompts say, and picking up after a crash."""
import unittest

from gated_delivery import GateError, Runner

from tests.support import FolderTest, put

TASK = 'Make art.json. Check with {{check}}. Keep {{unknown}} as it is.'


class Scripted:
    """a session that does what a script says for its role and keeps the requests it got"""

    def __init__(self, test, script):
        self.test, self.script, self.requests = test, dict(script), []

    def __call__(self, req):
        self.requests.append(req)
        action = self.script[req.role]
        if isinstance(action, list):
            action = action.pop(0)
        action()
        return 0


class Loop(FolderTest):

    def run_with(self, script, **kw):
        session = Scripted(self, script)
        lines = []
        st = Runner(self.gate(**kw), session, task=TASK, check_hint='CHECK-CMD', echo=lines.append).run()
        return st, [r.role for r in session.requests], session.requests, lines

    def test_the_longest_path_is_four_sessions(self):
        st, roles, reqs, lines = self.run_with({
            'produce': lambda: self.art(fail=['bad port']),
            'repair': lambda: self.art(warn=['one replica']),
            'second-look': lambda: self.review({'id': 'r1', 'weight': 'must', 'text': 'add a replica', 'where': 'art.json'}),
            'revise': lambda: self.art(revised=True),
        })
        self.assertEqual(roles, ['produce', 'repair', 'second-look', 'revise'])
        self.assertEqual((st['status'], st['review'], st['sessions']), ('delivered', 'applied', 4))
        self.assertEqual([r.number for r in reqs], [1, 2, 3, 4])
        self.assertEqual([r.attempt for r in reqs], [1, 2, 2, 2])
        self.assertEqual(lines[-1], '-> deliver')
        for r in reqs:
            self.assertEqual(r.prompt_file.read_text(), r.prompt)
            self.assertEqual(r.prompt_file.name, '%d-%s.md' % (r.number, r.role))

    def test_prompts_carry_the_task_and_what_the_session_needs(self):
        _, _, reqs, _ = self.run_with({
            'produce': lambda: self.art(fail=['bad port'], warn=['chatty']),
            'repair': lambda: self.art(),
            'second-look': lambda: self.review({'id': 'r1', 'weight': 'must', 'text': 'add a replica', 'where': 'art.json'}),
            'revise': lambda: self.art(),
        })
        produce, repair, look, revise = (r.prompt for r in reqs)
        self.assertEqual(produce, 'Make art.json. Check with CHECK-CMD. Keep {{unknown}} as it is.')
        for p in (repair, look, revise):
            self.assertIn('Make art.json. Check with CHECK-CMD.', p)
            self.assertIn('{{unknown}}', p)
            self.assertNotIn('{{task}}', p)
        self.assertIn('- x: bad port', repair)
        self.assertIn('- w: chatty', repair)
        self.assertIn('repair.json', repair)
        self.assertIn('- `art.json`', look)
        self.assertIn('review.json', look)
        self.assertIn('[r1 must] add a replica (where: art.json)', revise)

    def test_a_template_can_be_overridden(self):
        put(self.tmp, 'mine/second-look.md', 'Look at {{artifact}} only.')
        session = Scripted(self, {'produce': lambda: self.art(), 'second-look': lambda: self.review()})
        Runner(self.gate(), session, task=TASK, prompts_dir=self.tmp / 'mine', echo=lambda _: None).run()
        self.assertEqual(session.requests[1].prompt, 'Look at `art.json`, `parts/*.txt` only.')

    def test_not_delivered_after_two_sessions(self):
        st, roles, _, lines = self.run_with({'produce': lambda: self.art(fail=['a']), 'repair': lambda: self.art(fail=['b'])})
        self.assertEqual(roles, ['produce', 'repair'])
        self.assertEqual(st['status'], 'not_delivered')
        self.assertIn('    - x: b', lines)
        self.assertEqual(lines[-1], '-> not-delivered')

    def test_a_failed_session_exit_does_not_decide(self):
        def session(req):
            self.art()
            return 9
        lines = []
        st = Runner(self.gate(second_look=False), session, task=TASK, echo=lines.append).run()
        self.assertEqual(st['status'], 'delivered')
        self.assertIn('session 1 exited 9; the gate judges the files anyway', lines)

    def test_the_first_session_needs_a_task(self):
        with self.assertRaisesRegex(GateError, 'needs a task'):
            Runner(self.gate(), lambda r: 0, echo=lambda _: None).run()


class Resume(FolderTest):

    def test_a_session_that_ended_is_judged_not_run_again(self):
        g = self.gate()
        st = g.status()
        st['session'] = {'role': 'produce', 'number': 1, 'started': 't', 'ended': 't', 'exit': 0}
        g.save(st)
        self.art(fail=['bad'])
        ran = []
        Runner(g, lambda r: ran.append(r.role) or self.art(), task=TASK, echo=lambda _: None).run()
        self.assertEqual(ran[0], 'repair')               # produce was not run again
        self.assertEqual(g.status()['history'][0]['event'], 'attempt 1 failed: a fresh session repairs it')

    def test_a_session_cut_off_is_judged_as_it_stands(self):
        g = self.gate(second_look=False)
        st = g.status()
        st['session'] = {'role': 'produce', 'number': 1, 'started': 't'}
        g.save(st)
        self.art()
        lines, ran = [], []
        st = Runner(g, lambda r: ran.append(r.role), task=TASK, echo=lines.append).run()
        self.assertEqual(ran, [])
        self.assertEqual((st['status'], st['sessions']), ('delivered', 1))
        self.assertIn('session 1 (produce) started and never ended: judging what it left', lines)

    def test_a_terminal_state_runs_nothing(self):
        self.art()
        g = self.gate(second_look=False)
        g.step()
        st = Runner(g, lambda r: self.fail('no session should run'), echo=lambda _: None).run()
        self.assertEqual(st['status'], 'delivered')


if __name__ == '__main__':
    unittest.main()
