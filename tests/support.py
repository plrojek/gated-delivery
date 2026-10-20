"""Shared pieces for the tests: a folder per test and a check whose verdict is written in the artifact."""
import json
import shutil
import tempfile
import unittest
from pathlib import Path

from gated_delivery import Finding, Gate, GateError, Verdict

ROOT = Path(__file__).resolve().parent.parent
EXAMPLE = ROOT / 'examples' / 'service-config'


def put(folder: Path, name: str, data) -> None:
    p = folder / name
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(data if isinstance(data, str) else json.dumps(data), encoding='utf-8')


class ScriptedCheck:
    """Reads art.json, which says what the check finds: {"fail": [...], "warn": [...]}. A file named
    inputs-broken in the folder (not part of the artifact) makes everything fail, as a changed input or
    a nondeterministic check would. `error` makes the next calls raise GateError."""

    def __init__(self):
        self.calls = 0
        self.error = None

    def __call__(self, folder: Path) -> Verdict:
        self.calls += 1
        if self.error:
            raise GateError(self.error)
        if (folder / 'inputs-broken').exists():
            return Verdict([Finding('inputs', 'the inputs changed')])
        try:
            spec = json.loads((folder / 'art.json').read_text(encoding='utf-8'))
        except (OSError, ValueError):
            return Verdict([Finding('parse', 'no readable art.json')])
        return Verdict([Finding('x', m) for m in spec.get('fail', [])], [Finding('w', m) for m in spec.get('warn', [])])


class FolderTest(unittest.TestCase):
    """a fresh folder and a gate over art.json and parts/*.txt"""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix='gate-test-'))
        self.dir = self.tmp / 'work'
        self.dir.mkdir()
        self.check = ScriptedCheck()

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def gate(self, **kw) -> Gate:
        kw.setdefault('clock', lambda: '2026-01-01T00:00:00')
        return Gate(self.dir, self.check, ['art.json', 'parts/*.txt'], **kw)

    def art(self, fail=(), warn=(), **extra):
        put(self.dir, 'art.json', dict({'fail': list(fail), 'warn': list(warn)}, **extra))

    def review(self, *comments, raw=None):
        put(self.dir, 'review.json', raw if raw is not None else {'verdict': 'ship', 'comments': list(comments)})

    def read(self, rel: str):
        return json.loads((self.dir / rel).read_text(encoding='utf-8'))
