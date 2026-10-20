"""The gate: after every model session, decide from the files what happens next.

A session leaves an artifact in a folder. The gate checks it and returns the next step:

    produce        the first session has not been judged yet
    repair         the attempt failed: a fresh session gets the list of failures (repair.json)
    second-look    the artifact passes: a different session reads it and writes review.json
    revise         the second look left comments: one revision round on them
    deliver        the artifact passed on exactly the bytes copied to the output folder
    not-delivered  no attempt passed; nothing is delivered and the folder stays as a defect report

The rules:
  - a failure stops delivery; warnings never do
  - at most `max_attempts` attempts (the first session plus repairs), one second look, one revision
  - the second look changes nothing; if it did, its changes are undone
  - if the revision fails, the version from before the second look is restored, checked again and
    delivered if it passes
  - nothing is delivered without a passing check on the bytes that are delivered

State is a JSON file (delivery.json in the folder unless told otherwise). `step()` is not idempotent:
each call means "the session the state is waiting for has ended". `status()` only reads.
"""
from __future__ import annotations

import datetime
import hashlib
import json
import os
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

STATE_FILE = 'delivery.json'
REPAIR_FILE = 'repair.json'
REVIEW_FILE = 'review.json'
WORK_DIR = '.gate'
OUT_DIR = 'delivered'
MANIFEST = '.gate-manifest.json'

PRODUCE, REPAIR, SECOND_LOOK, REVISE = 'produce', 'repair', 'second-look', 'revise'
DELIVER, NOT_DELIVERED = 'deliver', 'not-delivered'
SESSION_ROLES = (PRODUCE, REPAIR, SECOND_LOOK, REVISE)
STATE_VERSION = 1
HISTORY_REASONS = 40


class GateError(Exception):
    """The gate could not decide: the check broke, a file it owns is unreadable. The state is not advanced."""


@dataclass(frozen=True)
class Finding:
    """One failure or warning. `check` is the id of the check that reported it, when known."""
    check: str | None
    message: str

    def __str__(self) -> str:
        return '%s: %s' % (self.check, self.message) if self.check else self.message

    def to_json(self) -> dict:
        return {'check': self.check, 'message': self.message}

    @classmethod
    def from_json(cls, x) -> 'Finding':
        if isinstance(x, dict):
            return cls(x.get('check'), str(x.get('message', '')))
        return cls(None, str(x))


@dataclass
class Verdict:
    """What a check run found. The artifact passes when there are no failures."""
    failures: list[Finding] = field(default_factory=list)
    warnings: list[Finding] = field(default_factory=list)

    @property
    def ok(self) -> bool:
        return not self.failures


Check = Callable[[Path], Verdict]


def now() -> str:
    return datetime.datetime.now().isoformat(timespec='seconds')


def sha256(path: Path) -> str:
    h = hashlib.sha256()
    with open(path, 'rb') as f:
        for block in iter(lambda: f.read(1 << 16), b''):
            h.update(block)
    return h.hexdigest()


def write_json(path: Path, data) -> None:
    """write through a temporary file, so a crash never leaves half a file"""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name('.%s.%d.tmp' % (path.name, os.getpid()))
    try:
        with open(tmp, 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=1)
            f.write('\n')
        os.replace(tmp, path)
    except BaseException:
        tmp.unlink(missing_ok=True)
        raise


class Gate:
    """The delivery gate for one artifact folder.

    folder        where the sessions work and the artifact lives
    check         a callable taking the folder and returning a Verdict (see CommandCheck); it raises
                  GateError when it cannot judge
    artifact      glob patterns, relative to the folder, naming the files that make up the artifact.
                  Only these are snapshotted, restored and delivered
    max_attempts  the first session plus repairs; 2 means one repair
    second_look   False delivers a passing artifact at once
    state_path    the state file; keep it outside the folder if you do not trust the sessions
    out_dir       where a delivered artifact is copied; it exists only while the state is `delivered`
    """

    def __init__(self, folder, check: Check, artifact, *, max_attempts: int = 2, second_look: bool = True,
                 state_path=None, out_dir=None, clock: Callable[[], str] = now):
        self.dir = Path(folder).resolve()
        if not self.dir.is_dir():
            raise GateError('no such folder: %s' % self.dir)
        self.check = check
        self.patterns = [artifact] if isinstance(artifact, str) else list(artifact)
        if not self.patterns:
            raise ValueError('name the artifact: at least one glob pattern')
        for p in self.patterns:
            if os.path.isabs(p) or '..' in Path(p).parts:
                raise ValueError('artifact patterns are relative to the folder and stay inside it: %r' % p)
        if max_attempts < 1:
            raise ValueError('max_attempts must be at least 1')
        self.max_attempts = max_attempts
        self.second_look = second_look
        self.state_path = Path(state_path).resolve() if state_path else self.dir / STATE_FILE
        self.out = Path(out_dir).resolve() if out_dir else self.dir / OUT_DIR
        if self.out == self.dir or self.out in self.dir.parents:
            raise ValueError('the output folder cannot be the artifact folder or one of its parents')
        self.work = self.dir / WORK_DIR
        self.snapshot_dir = self.work / 'before-review'
        self.repair_file = self.dir / REPAIR_FILE
        self.review_file = self.dir / REVIEW_FILE
        self.clock = clock

    @property
    def max_sessions(self) -> int:
        return self.max_attempts + 2

    # ------------------------------------------------------------------ the artifact on disk

    def owned(self, path: Path) -> bool:
        """files the gate writes itself; never part of the artifact"""
        if path in (self.state_path, self.repair_file, self.review_file):
            return True
        return any(path == d or d in path.parents for d in (self.work, self.out))

    def files(self) -> dict[str, str]:
        """the artifact as it is now: relative path -> sha256, or 'symlink' for a symbolic link"""
        found = {}
        for pattern in self.patterns:
            for p in self.dir.glob(pattern):
                if self.owned(p) or not (p.is_file() or p.is_symlink()):
                    continue
                rel = p.relative_to(self.dir).as_posix()
                found[rel] = 'symlink' if p.is_symlink() else sha256(p)
        return dict(sorted(found.items()))

    def judge(self) -> tuple[Verdict, dict[str, str]]:
        """run the check; return the verdict and the artifact it was given"""
        before = self.files()
        verdict = self.check(self.dir)
        if not isinstance(verdict, Verdict):
            raise GateError('the check returned %r, not a Verdict' % type(verdict).__name__)
        if self.files() != before:
            raise GateError('the check changed the artifact; a check must only read')
        links = [rel for rel, h in before.items() if h == 'symlink']
        if links:
            verdict = Verdict(verdict.failures + [Finding('gate', '%s is a symbolic link; the artifact must be plain files' % rel)
                                                  for rel in links], verdict.warnings)
        return verdict, before

    def snapshot(self) -> dict[str, str]:
        shutil.rmtree(self.snapshot_dir, ignore_errors=True)
        manifest = self.files()
        for rel in manifest:
            dst = self.snapshot_dir / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.dir / rel, dst)
        return manifest

    def restore(self, manifest: dict[str, str]) -> None:
        """make the artifact exactly what the snapshot holds: files added since are removed"""
        for rel in self.files():
            if rel not in manifest:
                (self.dir / rel).unlink()
        for rel in manifest:
            src, dst = self.snapshot_dir / rel, self.dir / rel
            if not src.is_file():
                raise GateError('the copy from before the second look is missing %s' % rel)
            if dst.is_symlink() or dst.is_dir():
                raise GateError('cannot restore %s: something else stands there now' % rel)
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    def check_output(self) -> None:
        """refuse, before doing anything, an output folder the gate did not make"""
        if self.out.exists() and not (self.dir in self.out.parents or (self.out / MANIFEST).is_file()):
            raise GateError('refusing to replace %s: it was not made by the gate' % self.out)

    def clear_output(self) -> None:
        """remove the output folder, but only one the gate could have made"""
        if self.out.exists():
            self.check_output()
            shutil.rmtree(self.out)

    def write_output(self, manifest: dict[str, str]) -> None:
        self.clear_output()
        self.out.mkdir(parents=True)
        for rel in manifest:
            dst = self.out / rel
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(self.dir / rel, dst)
        copied = {rel: sha256(self.out / rel) for rel in manifest}
        if copied != manifest:
            raise GateError('the artifact changed while it was being delivered')
        write_json(self.out / MANIFEST, {'files': manifest})

    # ------------------------------------------------------------------ state

    def new_state(self) -> dict:
        return {'version': STATE_VERSION, 'status': 'in_progress', 'next': PRODUCE, 'attempts': 0, 'sessions': 0,
                'review': 'none', 'failures': [], 'warnings': [], 'history': []}

    def load(self) -> dict | None:
        try:
            text = self.state_path.read_text(encoding='utf-8')
        except FileNotFoundError:
            return None
        try:
            st = json.loads(text)
        except ValueError as e:
            raise GateError('the state file %s is not JSON: %s' % (self.state_path, e))
        missing = [k for k in self.new_state() if k not in st] if isinstance(st, dict) else ['all']
        if missing:
            raise GateError('the state file %s lacks %s' % (self.state_path, ', '.join(missing)))
        if st['version'] != STATE_VERSION:
            raise GateError('the state file is version %r; this gate reads version %d' % (st['version'], STATE_VERSION))
        return st

    def save(self, st: dict) -> None:
        write_json(self.state_path, st)

    def status(self) -> dict:
        """the state as saved, or a fresh one; never changes anything"""
        return self.load() or self.new_state()

    def log(self, st: dict, event: str, verdict: Verdict | None = None) -> None:
        entry = {'at': self.clock(), 'event': event, 'failures': [], 'warnings': []}
        if verdict:
            entry['failures'] = [str(f) for f in verdict.failures][:HISTORY_REASONS]
            entry['warnings'] = [str(w) for w in verdict.warnings][:HISTORY_REASONS]
        st['history'].append(entry)

    # ------------------------------------------------------------------ the decision

    def step(self) -> dict:
        """The session the state waits for has ended: judge the folder, decide, save, return the state.

        A terminal state is returned unchanged. On GateError the state on disk stays as it was, except
        that a restore already made is recorded, so a retry neither repeats nor loses it."""
        st = self.status()
        if st['status'] != 'in_progress':
            return st
        self.check_output()
        if st.get('judging') != st['next']:                  # a retry after a GateError does not count twice
            if st['sessions'] >= self.max_sessions:
                raise GateError('%d sessions judged already; the state file was changed by hand' % st['sessions'])
            st['sessions'] += 1
            st['judging'] = st['next']
        st.pop('session', None)
        handler = {'none': self._after_attempt, 'asked': self._after_second_look,
                   'revising': self._after_revision}.get(st['review'])
        if handler is None:
            raise GateError('the state says review %r while in progress' % st['review'])
        handler(st)
        st.pop('judging', None)
        if st['status'] != 'delivered':
            self.clear_output()
        self.save(st)
        return st

    def _after_attempt(self, st: dict) -> None:
        """after the first session or a repair"""
        verdict, manifest = self.judge()
        st['attempts'] += 1
        n = st['attempts']
        if not verdict.ok and n < self.max_attempts:
            write_json(self.repair_file, {'attempt': n + 1, 'of': self.max_attempts,
                                          'failures': [f.to_json() for f in verdict.failures],
                                          'warnings': [w.to_json() for w in verdict.warnings]})
            self._set(st, REPAIR, verdict)
            self.log(st, 'attempt %d failed: a fresh session repairs it' % n, verdict)
        elif not verdict.ok:
            self._not_delivered(st, verdict, 'attempt %d failed: not delivered' % n)
        elif self.second_look:
            st['snapshot'] = self.snapshot()
            if self.review_file.exists():                      # an old review must not pass for a new one
                os.replace(self.review_file, self.work / 'review.stale.json')
            st['review'] = 'asked'
            self._set(st, SECOND_LOOK, verdict)
            self.log(st, 'attempt %d passes: a second look is asked' % n, verdict)
        else:
            st['review'] = 'skipped'
            self._deliver(st, verdict, manifest, 'attempt %d passes: delivered without a second look' % n)

    def _after_second_look(self, st: dict) -> None:
        """after the second-look session, which is not meant to change anything"""
        if self.files() != st['snapshot']:
            self.restore(st['snapshot'])
            self.log(st, 'the second look changed the artifact: its changes were undone')
            self.save(st)
        comments, problem = self.read_review()
        if comments:
            st['review'] = 'revising'
            st['comments'] = len(comments)
            st.update(next=REVISE, failures=[])
            self.log(st, 'second look: %d comment(s), one revision round' % len(comments))
            return
        verdict, manifest = self.judge()
        st['review'] = 'missing' if problem else 'clean'
        if problem:
            self.log(st, 'second look: %s; the artifact goes out unreviewed' % problem)
        if verdict.ok:
            self._deliver(st, verdict, manifest, 'second look: nothing to change' if not problem else 'delivered unreviewed')
        else:
            self._not_delivered(st, verdict, 'the version the second look was given no longer passes: not delivered')

    def _after_revision(self, st: dict) -> None:
        """after the revision round on the second look's comments"""
        if not st.get('restored'):
            verdict, manifest = self.judge()
            if verdict.ok:
                st['review'] = 'applied'
                self._deliver(st, verdict, manifest, 'the revision passes')
                return
            self.log(st, 'the revision fails: the version from before the second look is restored', verdict)
            st['restored'] = True
            self.save(st)
        self.restore(st['snapshot'])
        verdict, manifest = self.judge()
        st.pop('restored', None)
        st['review'] = 'dropped'
        if verdict.ok:
            self._deliver(st, verdict, manifest, 'the version from before the second look is delivered')
        else:
            self._not_delivered(st, verdict, 'the restored version no longer passes: not delivered')

    def _set(self, st: dict, next_step: str, verdict: Verdict) -> None:
        st.update(next=next_step, failures=[f.to_json() for f in verdict.failures],
                  warnings=[w.to_json() for w in verdict.warnings])

    def _deliver(self, st: dict, verdict: Verdict, manifest: dict[str, str], event: str) -> None:
        self.write_output(manifest)
        self._set(st, DELIVER, verdict)
        st['status'] = 'delivered'
        st['delivered'] = {'at': self.clock(), 'out': str(self.out), 'files': manifest}
        self.log(st, event, verdict)

    def _not_delivered(self, st: dict, verdict: Verdict, event: str) -> None:
        self._set(st, NOT_DELIVERED, verdict)
        st['status'] = 'not_delivered'
        self.log(st, event, verdict)

    def read_review(self) -> tuple[list[dict], str | None]:
        """the second look's comments, and what was wrong with the file if it was not a review"""
        try:
            data = json.loads(self.review_file.read_text(encoding='utf-8'))
        except FileNotFoundError:
            return [], 'it left no %s' % REVIEW_FILE
        except (ValueError, UnicodeDecodeError):
            return [], '%s is not JSON' % REVIEW_FILE
        if not isinstance(data, dict) or not isinstance(data.get('comments'), list):
            return [], '%s has no "comments" list' % REVIEW_FILE
        out = []
        for c in data['comments']:
            c = {'text': c} if isinstance(c, str) else c
            if isinstance(c, dict) and str(c.get('text', '')).strip():
                out.append(c)
        return out, None
