"""Checks for a small service config, written to the gate's contract.

    python3 checks.py [DIR] [--text]

Reads DIR/config.json (DIR defaults to the current folder). Prints the findings as JSON, or as lines
with --text, and exits 1 when any check fails. Warnings never change the exit code.

Each check has one id, and every finding carries it, so a planted fault can name the check it must
trip (faults.json).
"""
import json
import re
import sys
from pathlib import Path

LEVELS = ('debug', 'info', 'warning', 'error')
SCHEMA = {'name': str, 'port': int, 'replicas': int, 'log_level': str, 'health_path': str, 'timeouts': dict}
TIMEOUTS = ('connect_ms', 'request_ms')
NAME = re.compile(r'^[a-z][a-z0-9-]{0,39}$')


def is_type(v, t):
    return isinstance(v, t) and not (t is int and isinstance(v, bool))


def run(folder):
    fail, warn = [], []
    try:
        cfg = json.loads((Path(folder) / 'config.json').read_text(encoding='utf-8'))
    except FileNotFoundError:
        return [('parse', 'there is no config.json')], []
    except ValueError as e:
        return [('parse', 'config.json is not JSON: %s' % e)], []
    if not isinstance(cfg, dict):
        return [('parse', 'config.json is not an object')], []

    ok = {}
    for key, t in SCHEMA.items():
        if key not in cfg:
            fail.append(('fields', '%s is missing' % key))
        elif not is_type(cfg[key], t):
            fail.append(('fields', '%s should be %s, is %s' % (key, t.__name__, type(cfg[key]).__name__)))
        else:
            ok[key] = cfg[key]
    for key in sorted(set(cfg) - set(SCHEMA)):
        warn.append(('unknown_keys', '%s is not a known setting and will be ignored' % key))

    if 'name' in ok and not NAME.match(ok['name']):
        fail.append(('name', '%r: lowercase letters, digits and hyphens, starting with a letter, 40 at most' % ok['name']))
    if 'port' in ok and not 1024 <= ok['port'] <= 65535:
        fail.append(('port', 'port %d is outside 1024-65535' % ok['port']))
    if 'replicas' in ok:
        if not 1 <= ok['replicas'] <= 10:
            fail.append(('replicas', '%d replicas; between 1 and 10' % ok['replicas']))
        elif ok['replicas'] == 1:
            warn.append(('replicas', 'one replica: a restart takes the service down'))
    if 'timeouts' in ok:
        t = ok['timeouts']
        bad = [k for k in TIMEOUTS if not is_type(t.get(k), int) or t[k] <= 0]
        if bad:
            fail.append(('timeouts', '%s should be positive whole milliseconds' % ', '.join(bad)))
        elif t['connect_ms'] >= t['request_ms']:
            fail.append(('timeouts', 'connect_ms %d is not below request_ms %d' % (t['connect_ms'], t['request_ms'])))
        elif t['request_ms'] > 60000:
            fail.append(('timeouts', 'request_ms %d is over a minute' % t['request_ms']))
    if 'log_level' in ok:
        if ok['log_level'] not in LEVELS:
            fail.append(('log_level', '%r is not one of %s' % (ok['log_level'], ', '.join(LEVELS))))
        elif ok['log_level'] == 'debug':
            warn.append(('log_level', 'debug logging in a deployed config'))
    if 'health_path' in ok:
        p = ok['health_path']
        if not p.startswith('/') or any(c.isspace() for c in p):
            fail.append(('health_path', '%r should start with / and hold no spaces' % p))
    return fail, warn


def main():
    args = [a for a in sys.argv[1:] if not a.startswith('--')]
    fail, warn = run(args[0] if args else '.')
    if '--text' in sys.argv:
        for status, items in (('FAIL', fail), ('WARN', warn)):
            for check, msg in items:
                print('%s %-12s %s' % (status, check, msg))
        print('%d failure(s), %d warning(s)' % (len(fail), len(warn)))
    else:
        as_json = lambda items: [{'check': c, 'message': m} for c, m in items]
        print(json.dumps({'failures': as_json(fail), 'warnings': as_json(warn)}, indent=1))
    sys.exit(1 if fail else 0)


if __name__ == '__main__':
    main()
