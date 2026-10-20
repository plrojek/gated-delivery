"""Proof that each check catches the fault it exists for.

Given a base artifact that passes and a list of planted faults, each one a small edit plus the id of
the check it must trip, the runner copies the base, plants one fault, runs the check and asserts the
named check reports it. The base itself must pass. A check that no fault trips is only listed, since
a suite may have checks that are proved elsewhere.

A faults file:

    {"base": "base",                                   relative to the faults file
     "check": "python3 {here}/checks.py {dir}",        {here} is the faults file's folder
     "faults": [
       {"name": "port below 1024", "check": "port", "contains": "1024",
        "edits": [{"json_set": {"file": "config.json", "path": "port", "value": 80}}]},
       {"name": "one replica is allowed", "check": "replicas", "expect": "warn", "edits": [...]},
       {"name": "port 1024 is fine", "check": "port", "expect": "pass", "edits": [...]} ]}

`expect` is `fail` (the default), `warn` (a new warning from that check, and no failure from it) or
`pass` (nothing from that check: a guard against false alarms). `contains`, when given, must appear
in the finding's message.

Edits, applied in order, paths relative to the copy:

    {"replace": {"file": F, "old": TEXT, "new": TEXT}}    TEXT must occur exactly once
    {"write": {"file": F, "text": TEXT}}
    {"delete": {"file": F}}
    {"json_set": {"file": F, "path": "a.b.0.c", "value": V}}
    {"json_delete": {"file": F, "path": "a.b"}}
    {"run": "shell command"}                              in the copy; {dir} and {here} as for checks

From Python, an edit may also be any callable taking the folder.
"""
from __future__ import annotations

import json
import shutil
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import Callable

from .commands import CommandCheck, fill, run_shell, tail
from .gate import Check, GateError, Verdict

EXPECT = ('fail', 'warn', 'pass')


class FaultError(Exception):
    """a fault that cannot be planted as written"""


@dataclass
class Fault:
    name: str
    check: str
    edits: list = field(default_factory=list)
    expect: str = 'fail'
    contains: str | None = None


@dataclass
class Outcome:
    fault: Fault
    caught: bool
    seen: str                 # what the named check reported: fail, warn, pass, or an error
    also: list[str]           # other checks that failed on this fault


def inside(folder: Path, rel: str) -> Path:
    folder = folder.resolve()
    p = (folder / rel).resolve()
    if p != folder and folder not in p.parents:
        raise FaultError('%r points outside the copy' % rel)
    return p


def walk(data, path: str, create: bool = False):
    """the container holding the last segment of a dotted path, and that segment"""
    parts = path.split('.')
    for part in parts[:-1]:
        if isinstance(data, list):
            data = data[int(part)]
        elif isinstance(data, dict):
            if part not in data and create:
                data[part] = {}
            data = data[part]
        else:
            raise FaultError('%r: %r is not an object or a list' % (path, part))
    last = parts[-1]
    return data, (int(last) if isinstance(data, list) else last)


def apply_edit(folder: Path, edit, here: Path) -> None:
    if callable(edit):
        edit(folder)
        return
    if not isinstance(edit, dict) or len(edit) != 1:
        raise FaultError('an edit is one object with one key: %r' % (edit,))
    (op, arg), = edit.items()
    try:
        if op == 'run':
            code, out, err = run_shell(fill(arg, dir=folder, here=here), folder)
            if code != 0:
                raise FaultError('the edit command exited %s%s' % (code, tail(err or out)))
            return
        p = inside(folder, arg['file'])
        if op == 'replace':
            text = p.read_text(encoding='utf-8')
            n = text.count(arg['old'])
            if n != 1:
                raise FaultError('replace: the text occurs %d times in %s, not once' % (n, arg['file']))
            p.write_text(text.replace(arg['old'], arg['new']), encoding='utf-8')
        elif op == 'write':
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(arg['text'], encoding='utf-8')
        elif op == 'delete':
            p.unlink()
        elif op in ('json_set', 'json_delete'):
            data = json.loads(p.read_text(encoding='utf-8'))
            box, key = walk(data, arg['path'], create=op == 'json_set')
            if op == 'json_set':
                box[key] = arg['value']
            else:
                del box[key]
            p.write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')
        else:
            raise FaultError('unknown edit %r' % op)
    except (KeyError, IndexError, ValueError, TypeError, OSError) as e:
        raise FaultError('%s: %s: %s' % (op, type(e).__name__, e))


def seen_by(verdict: Verdict, check: str, base_warnings: set[str]) -> tuple[str, list]:
    failed = [f for f in verdict.failures if f.check == check]
    warned = [w for w in verdict.warnings if w.check == check and str(w) not in base_warnings]
    if failed:
        return 'fail', failed
    if warned:
        return 'warn', warned
    return 'pass', []


def run_faults(base: Path | str, check: Check, faults: list[Fault], *, here: Path | str | None = None,
               echo: Callable[[str], None] = print) -> tuple[bool, list[Outcome]]:
    """plant each fault in a fresh copy of the base; True when the base passes and every fault is caught"""
    base = Path(base).resolve()
    here = Path(here).resolve() if here else Path.cwd()
    if not base.is_dir():
        raise GateError('no base folder: %s' % base)
    for f in faults:
        if f.expect not in EXPECT:
            raise FaultError('%s: expect is one of %s' % (f.name, ', '.join(EXPECT)))
    outcomes = []
    with tempfile.TemporaryDirectory(prefix='faults-') as tmp:
        copy = Path(tmp) / 'base'
        shutil.copytree(base, copy)
        verdict = check(copy)
        if not verdict.ok:
            echo('base:  FAILS')
            for x in verdict.failures:
                echo('  ' + str(x))
            echo('the base must pass before a fault means anything')
            return False, []
        base_warnings = {str(w) for w in verdict.warnings}
        echo('base:  passes, %d warning(s)' % len(base_warnings))
        width = max([len(f.name) for f in faults] + [10])
        for i, f in enumerate(faults, 1):
            copy = Path(tmp) / ('fault-%d' % i)
            shutil.copytree(base, copy)
            try:
                for e in f.edits:
                    apply_edit(copy.resolve(), e, here)
                v = check(copy)
                seen, found = seen_by(v, f.check, base_warnings)
                caught = seen == f.expect and (not f.contains or f.expect == 'pass'
                                               or any(f.contains in x.message for x in found))
                also = sorted({x.check or '?' for x in v.failures if x.check != f.check})
            except (FaultError, GateError) as e:
                seen, caught, also = 'error: %s' % e, False, []
            outcomes.append(Outcome(f, caught, seen, also))
            mark = ('ok' if caught else 'WRONG') if f.expect == 'pass' else ('caught' if caught else 'MISSED')
            echo('%-7s %-*s  %s: expected %s, got %s%s' % (mark, width, f.name, f.check,
                                                            f.expect, seen, ('  (also failed: %s)' % ', '.join(also)) if also else ''))
    missed = sum(not o.caught for o in outcomes)
    echo('%d fault(s), %d missed' % (len(outcomes), missed))
    return missed == 0, outcomes


def load_faults(path: Path | str, check_command: str | None = None, timeout: float | None = None):
    """(base, check, faults, here) from a faults file"""
    path = Path(path).resolve()
    try:
        spec = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise GateError('cannot read %s: %s' % (path, e))
    here = path.parent
    command = check_command or spec.get('check')
    if not command:
        raise GateError('no check command: give "check" in %s or --check' % path.name)
    faults = []
    for x in spec.get('faults') or []:
        unknown = set(x) - {'name', 'check', 'edits', 'expect', 'contains'}
        if unknown or 'name' not in x or 'check' not in x:
            raise GateError('a fault needs "name" and "check" and takes edits, expect, contains: %r' % (x,))
        faults.append(Fault(x['name'], x['check'], list(x.get('edits') or []), x.get('expect', 'fail'), x.get('contains')))
    if not faults:
        raise GateError('%s lists no faults' % path.name)
    return here / spec.get('base', 'base'), CommandCheck(command, here=here, timeout=timeout), faults, here
