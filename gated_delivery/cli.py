"""gated-delivery: a model proposes, code disposes.

    gated-delivery status DIR                      the pending step; changes nothing
    gated-delivery next DIR --check CMD --artifact PATTERN ...
                                                   a session has ended: judge DIR and print the next step
    gated-delivery run DIR --session CMD --check CMD --artifact PATTERN ... --task FILE
                                                   run the sessions the gate asks for, to the end
    gated-delivery faults FAULTS.json              prove each check catches its planted fault
    gated-delivery prompts DIR                     write the default prompt templates to DIR for editing

The last line `next` prints is the step: produce, repair, second-look, revise, deliver or not-delivered.

Exit codes: status and next 0; run 0 when delivered and 1 when not; faults 0 when every fault is
caught and 1 otherwise; 2 for a usage error or when the gate could not decide (a broken check).
"""
from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

from . import __version__, prompts
from .commands import CommandCheck, CommandSession
from .faults import FaultError, load_faults, run_faults
from .gate import Gate, GateError
from .runner import Runner, describe

CONFIG_KEYS = {'check', 'session', 'artifact', 'task', 'prompts', 'attempts', 'second_look', 'state', 'out',
               'check_timeout', 'session_timeout'}


class Usage(Exception):
    pass


def load_config(path: str | None) -> tuple[dict, Path | None]:
    """settings from a JSON file; relative paths in it are relative to the file, and {here} is its folder"""
    if not path:
        return {}, None
    p = Path(path).resolve()
    try:
        cfg = json.loads(p.read_text(encoding='utf-8'))
    except (OSError, ValueError) as e:
        raise Usage('cannot read the config %s: %s' % (path, e))
    if not isinstance(cfg, dict):
        raise Usage('the config must be a JSON object')
    unknown = set(cfg) - CONFIG_KEYS
    if unknown:
        raise Usage('unknown key(s) in %s: %s (known: %s)' % (p.name, ', '.join(sorted(unknown)), ', '.join(sorted(CONFIG_KEYS))))
    for key in ('task', 'prompts', 'state', 'out'):
        if cfg.get(key):
            cfg[key] = str((p.parent / cfg[key]).resolve())
    if isinstance(cfg.get('artifact'), str):
        cfg['artifact'] = [cfg['artifact']]
    return cfg, p.parent


def settings(args) -> dict:
    """the config file, overridden by flags; each command keeps the folder its {here} refers to"""
    cfg, cfg_dir = load_config(args.config)
    s = {'here': {}}
    for key in CONFIG_KEYS:
        flag = getattr(args, key, None)
        if flag is not None and flag != []:
            s[key] = flag
            s['here'][key] = Path.cwd()
        elif key in cfg:
            s[key] = cfg[key]
            s['here'][key] = cfg_dir
    return s


def make_gate(args, s: dict) -> Gate:
    if not s.get('check'):
        raise Usage('name the check command: --check CMD (or "check" in --config)')
    if not s.get('artifact'):
        raise Usage('name the artifact: --artifact PATTERN, repeatable (or "artifact" in --config)')
    check = CommandCheck(s['check'], here=s['here']['check'], timeout=s.get('check_timeout'))
    second_look = s.get('second_look', True)
    try:
        return Gate(args.dir, check, s['artifact'], max_attempts=int(s.get('attempts', 2)), second_look=bool(second_look),
                    state_path=s.get('state'), out_dir=s.get('out'))
    except ValueError as e:
        raise Usage(str(e))


def status_gate(args) -> Gate:
    """a gate that only reads its state"""
    s = settings(args)
    def no_check(_):
        raise GateError('status does not run the check')
    return Gate(args.dir, no_check, s.get('artifact') or ['*'], state_path=s.get('state'), out_dir=s.get('out'))


def print_state(st: dict, as_json: bool) -> None:
    if as_json:
        print(json.dumps(st, ensure_ascii=False, indent=1))
    else:
        for line in describe(st):
            print(line)


def cmd_status(args) -> int:
    print_state(status_gate(args).status(), args.json)
    return 0


def cmd_next(args) -> int:
    s = settings(args)
    print_state(make_gate(args, s).step(), args.json)
    return 0


def cmd_run(args) -> int:
    s = settings(args)
    gate = make_gate(args, s)
    if not s.get('session'):
        raise Usage('name the session command: --session CMD (or "session" in --config)')
    task = None
    if s.get('task'):
        try:
            task = Path(s['task']).read_text(encoding='utf-8')
        except OSError as e:
            raise Usage('cannot read the task: %s' % e)
    session = CommandSession(s['session'], here=s['here']['session'], timeout=s.get('session_timeout'))
    st = Runner(gate, session, task=task, task_file=s.get('task'), prompts_dir=s.get('prompts')).run()
    if args.json:
        print(json.dumps(st, ensure_ascii=False, indent=1))
    return 0 if st['status'] == 'delivered' else 1


def cmd_faults(args) -> int:
    base, check, faults, here = load_faults(args.file, args.check, args.check_timeout)
    try:
        ok, _ = run_faults(base, check, faults, here=here)
    except FaultError as e:
        raise Usage(str(e))
    return 0 if ok else 1


def cmd_prompts(args) -> int:
    try:
        for p in prompts.write_defaults(args.dir):
            print(p)
    except FileExistsError as e:
        raise Usage('%s exists; not overwritten' % e)
    return 0


def parser() -> argparse.ArgumentParser:
    ap = argparse.ArgumentParser(prog='gated-delivery', description=__doc__.split('\n')[0],
                                 epilog='See README.md for the state table and the check contract.')
    ap.add_argument('--version', action='version', version='%(prog)s ' + __version__)
    sub = ap.add_subparsers(dest='cmd', required=True)

    def common(p, gate=True):
        p.add_argument('dir', help='the artifact folder')
        p.add_argument('--config', help='JSON file with any of: ' + ', '.join(sorted(CONFIG_KEYS)))
        p.add_argument('--state', help='the state file (default DIR/delivery.json)')
        p.add_argument('--out', help='where a delivered artifact is copied (default DIR/delivered)')
        p.add_argument('--artifact', action='append', default=[], metavar='PATTERN',
                       help='glob, relative to DIR, of the files that make up the artifact; repeatable')
        p.add_argument('--json', action='store_true', help='print the whole state as JSON')
        if gate:
            p.add_argument('--check', metavar='CMD', help='the check command: exit 0 or 1, JSON findings on stdout')
            p.add_argument('--check-timeout', type=float, metavar='S')
            p.add_argument('--attempts', type=int, help='the first session plus repairs (default 2)')
            p.add_argument('--no-second-look', dest='second_look', action='store_const', const=False,
                           help='deliver a passing artifact at once')

    common(sub.add_parser('status', help='print the pending step; change nothing'), gate=False)
    common(sub.add_parser('next', help='a session has ended: judge the folder and print the next step'))
    p = sub.add_parser('run', help='run the sessions the gate asks for, to deliver or not-delivered')
    common(p)
    p.add_argument('--session', metavar='CMD', help='the session command; {prompt} is the prompt file')
    p.add_argument('--session-timeout', type=float, metavar='S')
    p.add_argument('--task', metavar='FILE', help='the prompt of the first session')
    p.add_argument('--prompts', metavar='DIR', help='templates overriding repair.md, second-look.md, revise.md')
    p = sub.add_parser('faults', help='prove each check catches its planted fault')
    p.add_argument('file', help='the faults file')
    p.add_argument('--check', metavar='CMD', help='overrides "check" in the faults file')
    p.add_argument('--check-timeout', type=float, metavar='S')
    p = sub.add_parser('prompts', help='write the default prompt templates to DIR')
    p.add_argument('dir')
    return ap


def main(argv=None) -> int:
    args = parser().parse_args(argv)
    fn = {'status': cmd_status, 'next': cmd_next, 'run': cmd_run, 'faults': cmd_faults, 'prompts': cmd_prompts}[args.cmd]
    try:
        return fn(args)
    except Usage as e:
        print('gated-delivery: %s' % e, file=sys.stderr)
        return 2
    except GateError as e:
        print('gated-delivery: the gate could not decide: %s' % e, file=sys.stderr)
        return 2


if __name__ == '__main__':
    sys.exit(main())
