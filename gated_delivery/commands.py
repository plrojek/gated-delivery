"""Shell commands as checks and sessions.

A command is a template run by /bin/sh inside the artifact folder. Placeholders are replaced with
shell-quoted values, so do not quote them yourself:

    {dir}      the artifact folder (absolute)
    {here}     the folder of the config or faults file the command came from; the current folder
               for a command given on the command line
    {role}     the session role: produce, repair, second-look or revise      (sessions only)
    {prompt}   the file holding the rendered prompt for this session          (sessions only)
    {attempt}  the number of the attempt this session belongs to             (sessions only)

Sessions also get them as environment variables: GATE_DIR, GATE_ROLE, GATE_PROMPT, GATE_ATTEMPT.

The check contract: exit code 0 when the artifact passes, 1 when it fails, and on stdout either
nothing or one JSON object:

    {"failures": [{"check": "port", "message": "port 80 is below 1024"}],
     "warnings": [{"check": "replicas", "message": "one replica: no redundancy"}]}

A finding may also be a plain string; "port: port 80 is below 1024" is read as check `port`.
Any other exit code, output that is not such an object, or exit 0 with failures listed means the
check itself is broken: the gate raises GateError and does not advance.
"""
from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
from dataclasses import dataclass
from pathlib import Path

from .gate import Finding, GateError, Verdict

PLACEHOLDER = re.compile(r'\{(dir|here|role|prompt|attempt)\}')
STRING_FINDING = re.compile(r'^([A-Za-z0-9_.-]+): (.+)$', re.S)


def fill(template: str, **values) -> str:
    """replace the known placeholders with shell-quoted values; leave every other brace alone"""
    def sub(m):
        v = values.get(m.group(1))
        return m.group(0) if v is None else shlex.quote(str(v))
    return PLACEHOLDER.sub(sub, template)


def run_shell(command: str, cwd: Path, *, env: dict | None = None, timeout: float | None = None,
              stdout=subprocess.PIPE, stderr=subprocess.PIPE) -> tuple[int | None, str, str]:
    """run a command in its own process group; on timeout kill the whole group. Returns (code, out, err),
    code None on timeout."""
    p = subprocess.Popen(['/bin/sh', '-c', command], cwd=cwd, env=env, stdin=subprocess.DEVNULL,
                         stdout=stdout, stderr=stderr, text=True, start_new_session=True)
    try:
        out, err = p.communicate(timeout=timeout)
        return p.returncode, out or '', err or ''
    except subprocess.TimeoutExpired:
        try:
            os.killpg(p.pid, signal.SIGKILL)
        except ProcessLookupError:
            pass
        out, err = p.communicate()
        return None, out or '', err or ''


def tail(text: str, lines: int = 5) -> str:
    t = text.strip().splitlines()[-lines:]
    return (': ' + ' | '.join(t)) if t else ''


def to_finding(x) -> Finding:
    if isinstance(x, dict):
        msg = x.get('message', x.get('text'))
        if msg is None:
            raise GateError('a finding has no "message": %s' % json.dumps(x)[:200])
        return Finding(x.get('check') or x.get('id'), str(msg))
    if isinstance(x, str):
        m = STRING_FINDING.match(x)
        return Finding(m.group(1), m.group(2)) if m else Finding(None, x)
    raise GateError('a finding is neither an object nor a string: %r' % (x,))


def parse_check_output(code: int | None, out: str, err: str = '') -> Verdict:
    """read the check contract; raise GateError when the check itself is broken"""
    if code is None:
        raise GateError('the check timed out' + tail(err))
    if code not in (0, 1):
        raise GateError('the check exited %d (0 = pass, 1 = fail; anything else is an error of the check)%s' % (code, tail(err)))
    data = {}
    if out.strip():
        try:
            data = json.loads(out)
        except ValueError:
            raise GateError('the check printed something that is not JSON%s' % tail(out))
    if not isinstance(data, dict):
        raise GateError('the check printed JSON that is not an object')
    for key in ('failures', 'warnings'):
        if not isinstance(data.get(key, []), list):
            raise GateError('"%s" in the check output is not a list' % key)
    failures = [to_finding(x) for x in data.get('failures', [])]
    warnings = [to_finding(x) for x in data.get('warnings', [])]
    if code == 0 and failures:
        raise GateError('the check exited 0 but listed %d failure(s)' % len(failures))
    if code == 1 and not failures:
        failures = [Finding(None, 'the check exited 1 and listed no failure' + tail(err))]
    return Verdict(failures, warnings)


@dataclass
class CommandCheck:
    """A check that runs a shell command. See the module docstring for the contract."""
    command: str
    here: Path | str | None = None
    timeout: float | None = None

    def display(self, folder: Path) -> str:
        return fill(self.command, dir=folder, here=self.here or Path.cwd())

    def __call__(self, folder: Path) -> Verdict:
        code, out, err = run_shell(self.display(folder), folder, timeout=self.timeout)
        return parse_check_output(code, out, err)


@dataclass
class SessionRequest:
    """What a session is asked to do."""
    role: str
    number: int                # the session's place in the order, 1 to 4 with the defaults
    attempt: int               # the attempt it belongs to (for second-look and revise: the passing one)
    folder: Path
    prompt_file: Path
    prompt: str
    log_file: Path


@dataclass
class CommandSession:
    """A session that runs a shell command, e.g. `claude -p "$(cat {prompt})"`. Its output goes to the log."""
    command: str
    here: Path | str | None = None
    timeout: float | None = None

    def __call__(self, req: SessionRequest) -> int | None:
        values = dict(dir=req.folder, here=self.here or Path.cwd(), role=req.role, prompt=req.prompt_file, attempt=req.attempt)
        env = dict(os.environ, GATE_DIR=str(req.folder), GATE_ROLE=req.role, GATE_PROMPT=str(req.prompt_file),
                   GATE_ATTEMPT=str(req.attempt))
        req.log_file.parent.mkdir(parents=True, exist_ok=True)
        with open(req.log_file, 'w', encoding='utf-8') as log:
            code, _, _ = run_shell(fill(self.command, **values), req.folder, env=env, timeout=self.timeout,
                                   stdout=log, stderr=subprocess.STDOUT)
        return code
