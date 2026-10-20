"""Drive one artifact from the first session to `deliver` or `not-delivered`.

Before a session starts, the state file records {role, number, started}; when it ends, `ended` and
its exit code. A run that was cut off picks up from there: a session that ended is judged; a session
that started and never ended is judged as it stands, because it counts against the limit either way.
Delete the state file to start over.

The exit code of a session is recorded and does not decide anything: the gate judges the files.
"""
from __future__ import annotations

from pathlib import Path
from typing import Callable

from . import prompts
from .gate import PRODUCE, REVISE, SECOND_LOOK, Finding, Gate, GateError
from .commands import SessionRequest

Session = Callable[[SessionRequest], 'int | None']


def attempt_of(st: dict) -> int:
    """the attempt a pending session belongs to: a new one for produce and repair, the passing one after"""
    return st['attempts'] if st['next'] in (SECOND_LOOK, REVISE) else st['attempts'] + 1


def describe(st: dict) -> list[str]:
    """the lines `next` prints: failures, warnings, then the step"""
    lines = ['  ' + str(Finding.from_json(f)) for f in st.get('failures') or []]
    lines += ['  warn ' + str(Finding.from_json(w)) for w in st.get('warnings') or []]
    return lines + [st['next']]


class Runner:
    """task         the prompt of the first session (text); needed only while the first session is pending
    task_file    where it came from, if anywhere (shown to the other sessions)
    prompts_dir  overrides for the templates
    check_hint   the check command as a session should run it; CommandCheck provides it itself
    """

    def __init__(self, gate: Gate, session: Session, *, task: str | None = None, task_file: Path | str | None = None,
                 prompts_dir: Path | str | None = None, check_hint: str | None = None,
                 echo: Callable[[str], None] = print):
        self.gate, self.session = gate, session
        self.task, self.task_file = task, task_file
        self.prompts_dir = prompts_dir
        self.check_hint = check_hint
        self.echo = echo

    def values(self, st: dict) -> dict:
        g = self.gate
        hint = self.check_hint
        if hint is None and hasattr(g.check, 'display'):
            hint = g.check.display(g.dir)
        failures = [Finding.from_json(f) for f in st.get('failures') or []]
        warnings = [Finding.from_json(w) for w in st.get('warnings') or []]
        comments, _ = g.read_review() if st['next'] == REVISE else ([], None)
        v = {
            'task_file': str(self.task_file or '(none)'),
            'artifact': ', '.join('`%s`' % p for p in g.patterns),
            'files': prompts.bullets(['`%s`' % f for f in g.files()]),
            'check': hint or '(the gate runs the check; it is not available to you)',
            'failures': prompts.bullets(failures),
            'warnings': prompts.bullets(warnings),
            'comments': prompts.comment_lines(comments),
            'repair_file': g.repair_file.name,
            'review_file': g.review_file.name,
            'dir': str(g.dir),
            'attempt': str(attempt_of(st)),
            'max_attempts': str(g.max_attempts),
        }
        # the task goes into the other templates as the first session saw it, its own slots filled
        v['task'] = prompts.render(self.task, v).strip() if self.task else '(the task was not given to the gate)'
        return v

    def prompt(self, st: dict) -> str:
        role = st['next']
        if role == PRODUCE:
            if self.task is None:
                raise GateError('the first session needs a task (--task)')
            text = self.task
        else:
            text = prompts.template(role, self.prompts_dir)
        return prompts.render(text, self.values(st))

    def run(self) -> dict:
        g = self.gate
        st = g.status()
        while st['status'] == 'in_progress':
            role = st['next']
            s = st.get('session') or {}
            if s.get('role') == role and not s.get('ended'):
                self.echo('session %d (%s) started and never ended: judging what it left' % (s['number'], role))
            elif s.get('role') != role:
                self.start(st, role)
            seen = len(st['history'])
            st = g.step()
            for h in st['history'][seen:]:
                self.echo('  ' + h['event'])
                for r in h.get('failures') or []:
                    self.echo('    - ' + r)
                for r in h.get('warnings') or []:
                    self.echo('    - warn ' + r)
            self.echo('-> ' + st['next'])
        return st

    def start(self, st: dict, role: str) -> None:
        g = self.gate
        number = st['sessions'] + 1
        attempt = attempt_of(st)
        text = self.prompt(st)
        prompt_file = g.work / 'prompts' / ('%d-%s.md' % (number, role))
        prompt_file.parent.mkdir(parents=True, exist_ok=True)
        prompt_file.write_text(text, encoding='utf-8')
        log_file = g.work / 'sessions' / ('%d-%s.log' % (number, role))
        st['session'] = {'role': role, 'number': number, 'started': g.clock()}
        g.save(st)
        self.echo('session %d: %s' % (number, role))
        code = self.session(SessionRequest(role=role, number=number, attempt=attempt, folder=g.dir,
                                           prompt_file=prompt_file, prompt=text, log_file=log_file))
        st['session'].update(ended=g.clock(), exit=code)
        g.save(st)
        if code != 0:
            self.echo('session %d exited %s; the gate judges the files anyway' % (number, 'on timeout' if code is None else code))
