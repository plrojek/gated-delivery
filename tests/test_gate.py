"""Every transition of the state table, and the rules around it."""
import json
import os
import shutil
import unittest

from gated_delivery import Gate, GateError, Verdict
from gated_delivery.gate import MANIFEST

from tests.support import FolderTest, put


class Transitions(FolderTest):

    def test_first_attempt_fails_asks_for_a_repair(self):
        self.art(fail=['broken'], warn=['meh'])
        st = self.gate().step()
        self.assertEqual((st['status'], st['next'], st['attempts'], st['sessions']), ('in_progress', 'repair', 1, 1))
        rep = self.read('repair.json')
        self.assertEqual(rep['attempt'], 2)
        self.assertEqual(rep['failures'], [{'check': 'x', 'message': 'broken'}])
        self.assertEqual(rep['warnings'], [{'check': 'w', 'message': 'meh'}])
        self.assertFalse((self.dir / 'delivered').exists())

    def test_repair_fails_too_not_delivered(self):
        self.art(fail=['broken'])
        g = self.gate()
        g.step()
        self.art(fail=['still broken'])
        (self.dir / 'delivered').mkdir()                        # a session cannot plant an output
        put(self.dir, 'delivered/art.json', '{}')
        st = g.step()
        self.assertEqual((st['status'], st['next'], st['attempts'], st['sessions']), ('not_delivered', 'not-delivered', 2, 2))
        self.assertEqual(st['failures'], [{'check': 'x', 'message': 'still broken'}])
        self.assertFalse((self.dir / 'delivered').exists())
        self.assertTrue((self.dir / 'art.json').exists())       # the folder stays as the defect report
        self.assertEqual(st['history'][-1]['event'], 'attempt 2 failed: not delivered')

    def test_repair_passes_asks_for_a_second_look(self):
        self.art(fail=['broken'])
        g = self.gate()
        g.step()
        self.art()
        st = g.step()
        self.assertEqual((st['next'], st['review'], st['attempts']), ('second-look', 'asked', 2))

    def test_pass_takes_a_snapshot_and_moves_an_old_review_aside(self):
        self.art()
        put(self.dir, 'parts/a.txt', 'A')
        self.review({'text': 'left over from before'})
        st = self.gate().step()
        self.assertEqual((st['next'], st['review']), ('second-look', 'asked'))
        self.assertEqual(sorted(st['snapshot']), ['art.json', 'parts/a.txt'])
        self.assertEqual((self.dir / '.gate/before-review/parts/a.txt').read_text(), 'A')
        self.assertFalse((self.dir / 'review.json').exists())
        self.assertTrue((self.dir / '.gate/review.stale.json').exists())

    def test_an_empty_artifact_that_passes_still_gets_its_second_look(self):
        def passes(folder):
            return Verdict()
        g = Gate(self.dir, passes, ['nothing-matches-*'])
        self.review({'text': 'stale'})
        st = g.step()
        self.assertEqual((st['next'], st['snapshot']), ('second-look', {}))
        self.assertTrue((self.dir / '.gate/review.stale.json').exists())

    def test_second_look_without_comments_delivers(self):
        self.art()
        g = self.gate()
        g.step()
        self.review()
        st = g.step()
        self.assertEqual((st['status'], st['next'], st['review'], st['sessions']), ('delivered', 'deliver', 'clean', 2))
        self.assertEqual(self.read('delivered/art.json'), self.read('art.json'))
        self.assertEqual(json.loads((self.dir / 'delivered' / MANIFEST).read_text())['files'], st['delivered']['files'])

    def test_blank_comments_are_no_comments(self):
        self.art()
        g = self.gate()
        g.step()
        self.review({'id': 'r1', 'text': '  '}, {'id': 'r2'}, 7)
        self.assertEqual(g.step()['review'], 'clean')

    def test_no_review_file_delivers_unreviewed(self):
        for raw in (None, 'not json', {'verdict': 'fine'}, [1, 2]):
            with self.subTest(raw=raw):
                shutil.rmtree(self.dir)
                self.dir.mkdir()
                self.art()
                g = self.gate()
                g.step()
                if raw is not None:
                    self.review(raw=raw if isinstance(raw, str) else json.dumps(raw))
                st = g.step()
                self.assertEqual((st['status'], st['review']), ('delivered', 'missing'))
                self.assertIn('unreviewed', st['history'][-2]['event'])

    def test_comments_ask_for_one_revision(self):
        self.art()
        g = self.gate()
        g.step()
        self.review({'id': 'r1', 'weight': 'must', 'text': 'fix it'}, 'and a plain string')
        st = g.step()
        self.assertEqual((st['status'], st['next'], st['review'], st['comments']), ('in_progress', 'revise', 'revising', 2))
        self.assertFalse((self.dir / 'delivered').exists())

    def _to_revision(self, g):
        self.art(warn=['before'])
        put(self.dir, 'parts/a.txt', 'A')
        put(self.dir, 'parts/b.txt', 'B')
        g.step()
        self.review({'text': 'change it'})
        g.step()

    def test_revision_passes_delivers_the_revision(self):
        g = self.gate()
        self._to_revision(g)
        self.art(revised=True)
        st = g.step()
        self.assertEqual((st['status'], st['review'], st['sessions']), ('delivered', 'applied', 3))
        self.assertTrue(self.read('delivered/art.json')['revised'])
        self.assertEqual(st['warnings'], [])

    def test_revision_fails_restores_and_delivers_the_version_before(self):
        g = self.gate()
        self._to_revision(g)
        self.art(fail=['worse'], revised=True)
        put(self.dir, 'parts/c.txt', 'new file')                # added by the revision
        (self.dir / 'parts/b.txt').unlink()                     # removed by the revision
        put(self.dir, 'review-changes.md', 'notes')             # not part of the artifact
        st = g.step()
        self.assertEqual((st['status'], st['review']), ('delivered', 'dropped'))
        self.assertNotIn('revised', self.read('art.json'))
        self.assertEqual(sorted(p.name for p in (self.dir / 'parts').iterdir()), ['a.txt', 'b.txt'])
        self.assertEqual(sorted(p.name for p in (self.dir / 'delivered/parts').iterdir()), ['a.txt', 'b.txt'])
        self.assertTrue((self.dir / 'review-changes.md').exists())
        self.assertEqual(st['warnings'], [{'check': 'w', 'message': 'before'}])
        events = [h['event'] for h in st['history'][-2:]]
        self.assertIn('the revision fails', events[0])
        self.assertEqual(st['history'][-2]['failures'], ['x: worse'])

    def test_restored_version_that_fails_is_not_delivered(self):
        g = self.gate()
        self._to_revision(g)
        self.art(fail=['worse'])
        put(self.dir, 'inputs-broken', '')                      # the check now fails the old version too
        st = g.step()
        self.assertEqual((st['status'], st['review']), ('not_delivered', 'dropped'))
        self.assertFalse((self.dir / 'delivered').exists())

    def test_edits_by_the_second_look_are_undone(self):
        self.art()
        g = self.gate()
        g.step()
        before = (self.dir / 'art.json').read_text()
        self.art(fail=['the reviewer broke it'])
        put(self.dir, 'parts/extra.txt', 'x')
        self.review()
        st = g.step()
        self.assertEqual((st['status'], st['review']), ('delivered', 'clean'))
        self.assertEqual((self.dir / 'art.json').read_text(), before)
        self.assertFalse((self.dir / 'parts/extra.txt').exists())
        self.assertIn('its changes were undone', st['history'][-2]['event'])

    def test_edits_by_the_second_look_are_undone_before_the_revision(self):
        self.art()
        g = self.gate()
        g.step()
        before = (self.dir / 'art.json').read_text()
        self.art(fail=['the reviewer broke it'])
        self.review({'text': 'something'})
        st = g.step()
        self.assertEqual(st['next'], 'revise')
        self.assertEqual((self.dir / 'art.json').read_text(), before)

    def test_version_given_to_the_second_look_that_no_longer_passes_is_not_delivered(self):
        self.art()
        g = self.gate()
        g.step()
        put(self.dir, 'inputs-broken', '')
        self.review()
        st = g.step()
        self.assertEqual(st['status'], 'not_delivered')

    def test_no_second_look_delivers_at_once(self):
        self.art(warn=['w'])
        st = self.gate(second_look=False).step()
        self.assertEqual((st['status'], st['review'], st['sessions']), ('delivered', 'skipped', 1))
        self.assertTrue((self.dir / 'delivered/art.json').exists())
        self.assertFalse((self.dir / '.gate/before-review').exists())


class Rules(FolderTest):

    def test_warnings_never_block(self):
        self.art(warn=['one', 'two'])
        g = self.gate()
        self.assertEqual(g.step()['next'], 'second-look')
        self.review()
        st = g.step()
        self.assertEqual(st['status'], 'delivered')
        self.assertEqual([w['message'] for w in st['warnings']], ['one', 'two'])

    def test_a_terminal_state_stays_and_is_not_judged_again(self):
        self.art()
        g = self.gate(second_look=False)
        st = g.step()
        calls = self.check.calls
        self.art(fail=['changed afterwards'])
        self.assertEqual(g.step(), st)
        self.assertEqual(self.check.calls, calls)

    def test_one_attempt_means_no_repair(self):
        self.art(fail=['broken'])
        st = self.gate(max_attempts=1).step()
        self.assertEqual(st['status'], 'not_delivered')
        self.assertFalse((self.dir / 'repair.json').exists())

    def test_three_attempts_mean_two_repairs(self):
        self.art(fail=['broken'])
        g = self.gate(max_attempts=3)
        self.assertEqual(g.step()['next'], 'repair')
        self.assertEqual(g.step()['next'], 'repair')
        self.assertEqual(self.read('repair.json')['attempt'], 3)
        st = g.step()
        self.assertEqual((st['status'], st['attempts']), ('not_delivered', 3))

    def test_status_does_not_advance(self):
        g = self.gate()
        self.assertEqual(g.status()['next'], 'produce')
        self.assertFalse(g.state_path.exists())
        self.art(fail=['broken'])
        g.step()
        calls = self.check.calls
        for _ in range(3):
            self.assertEqual(g.status()['next'], 'repair')
        self.assertEqual(self.check.calls, calls)

    def test_a_broken_check_leaves_the_state_alone(self):
        self.art(fail=['broken'])
        g = self.gate()
        self.check.error = 'the check crashed'
        with self.assertRaises(GateError):
            g.step()
        self.assertFalse(g.state_path.exists())
        self.check.error = None
        st = g.step()
        self.assertEqual((st['next'], st['attempts'], st['sessions']), ('repair', 1, 1))

    def test_a_retry_after_an_error_in_the_restore_counts_one_session(self):
        g = self.gate()
        self.art()
        g.step()
        self.review({'text': 'change it'})
        g.step()
        self.art(fail=['worse'])

        real = self.check.__call__
        state = {'n': 0}

        def flaky(folder):                     # the revision is judged, then the check breaks on the restored version
            state['n'] += 1
            if state['n'] == 2:
                raise GateError('the check crashed')
            return real(folder)
        g.check = flaky
        with self.assertRaises(GateError):
            g.step()
        saved = g.status()
        self.assertTrue(saved['restored'])
        self.assertEqual(saved['sessions'], 3)
        self.assertNotIn('worse', (self.dir / 'art.json').read_text())
        st = g.step()
        self.assertEqual((st['status'], st['review'], st['sessions']), ('delivered', 'dropped', 3))
        self.assertNotIn('restored', st)

    def test_the_check_must_not_change_the_artifact(self):
        self.art()

        def writes(folder):
            put(folder, 'art.json', '{"fail": []}')
            return self.check(folder)
        g = self.gate()
        g.check = writes
        with self.assertRaisesRegex(GateError, 'changed the artifact'):
            g.step()

    def test_a_symbolic_link_in_the_artifact_fails(self):
        self.art()
        (self.dir / 'parts').mkdir()
        os.symlink('/etc/hosts', self.dir / 'parts/link.txt')
        st = self.gate().step()
        self.assertEqual(st['next'], 'repair')
        self.assertIn('symbolic link', st['failures'][0]['message'])

    def test_an_output_folder_the_gate_did_not_make_is_refused(self):
        other = self.tmp / 'mine'
        other.mkdir()
        put(other, 'important.txt', 'keep me')
        self.art()
        with self.assertRaisesRegex(GateError, 'not made by the gate'):
            self.gate(out_dir=other, second_look=False).step()
        self.assertTrue((other / 'important.txt').exists())

    def test_an_output_folder_outside_is_replaced_once_it_is_the_gates(self):
        out = self.tmp / 'out'
        self.art()
        st = self.gate(out_dir=out, second_look=False, state_path=self.tmp / 'state.json').step()
        self.assertEqual(st['status'], 'delivered')
        self.assertTrue((out / 'art.json').exists())
        self.assertTrue((self.tmp / 'state.json').exists())
        self.assertFalse((self.dir / 'delivery.json').exists())

    def test_patterns_stay_inside_the_folder(self):
        for bad in ('/etc/*', '../*', 'a/../../b'):
            with self.subTest(pattern=bad), self.assertRaises(ValueError):
                Gate(self.dir, self.check, [bad])
        with self.assertRaises(ValueError):
            Gate(self.dir, self.check, [])
        with self.assertRaises(ValueError):
            self.gate(out_dir=self.dir)

    def test_gate_files_are_never_part_of_the_artifact(self):
        g = Gate(self.dir, self.check, ['**/*'])
        self.art()
        g.step()
        self.assertEqual(sorted(g.files()), ['art.json'])

    def test_an_unreadable_state_file_is_an_error(self):
        put(self.dir, 'delivery.json', '{not json')
        with self.assertRaises(GateError):
            self.gate().step()
        put(self.dir, 'delivery.json', '{"status": "delivered"}')
        with self.assertRaisesRegex(GateError, 'lacks'):
            self.gate().status()


class Bound(FolderTest):
    """Walk the whole decision tree: an attempt or a revision passes or fails; a second look comments,
    says nothing, or leaves no file. Every path ends, within attempts + 2 sessions."""

    def replay(self, attempts, path):
        shutil.rmtree(self.dir)
        self.dir.mkdir()
        g = self.gate(max_attempts=attempts)
        st = g.status()
        for outcome in path:
            if st['next'] == 'second-look':
                if outcome != 'missing':
                    self.review(*([{'text': 'c'}] if outcome == 'comments' else []))
            else:
                self.art(fail=['f'] if outcome == 'fail' else [])
            st = g.step()
        return st

    def test_every_path_ends_within_the_limit(self):
        for attempts in (1, 2, 3):
            limit, ends, paths = attempts + 2, set(), [()]
            while paths:
                path = paths.pop()
                st = self.replay(attempts, path)
                if st['status'] != 'in_progress':
                    self.assertEqual(st['sessions'], len(path))
                    self.assertLessEqual(len(path), limit, path)
                    ends.add((st['status'], st['review']))
                    continue
                self.assertLess(len(path), limit, path)
                choices = ('comments', 'silent', 'missing') if st['next'] == 'second-look' else ('pass', 'fail')
                paths += [path + (c,) for c in choices]
            expected = {('not_delivered', 'none'), ('delivered', 'clean'), ('delivered', 'missing'),
                        ('delivered', 'applied'), ('delivered', 'dropped')}
            self.assertEqual(ends, expected, attempts)


if __name__ == '__main__':
    unittest.main()
