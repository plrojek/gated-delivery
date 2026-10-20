"""Every path through the gate, offline, with fake sessions.

    python3 examples/service-config/demo.py [SCENARIO ...] [--keep]

For each scenario of fake_session.py it makes an empty folder, runs
`gated-delivery run DIR --config gate.json --session "python3 fake_session.py SCENARIO"`, and prints
what the gate printed, the history from delivery.json, and what was delivered. --keep leaves the
folders behind and prints where they are.
"""
import json
import os
import shlex
import subprocess
import sys
import tempfile
from pathlib import Path

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent.parent
sys.path.insert(0, str(HERE))
from fake_session import SCENARIOS  # noqa: E402


def show(scenario: str, folder: Path) -> int:
    session = 'python3 %s %s' % (shlex.quote(str(HERE / 'fake_session.py')), scenario)
    cmd = [sys.executable, '-m', 'gated_delivery', 'run', str(folder), '--config', str(HERE / 'gate.json'), '--session', session]
    env = dict(os.environ, PYTHONPATH=str(ROOT) + os.pathsep + os.environ.get('PYTHONPATH', ''))
    p = subprocess.run(cmd, capture_output=True, text=True, env=env)
    print('== %s' % scenario)
    for line in p.stdout.splitlines():
        print('   ' + line)
    if p.stderr.strip():
        print('   stderr: ' + p.stderr.strip())
    st = json.loads((folder / 'delivery.json').read_text(encoding='utf-8'))
    out = folder / 'delivered'
    if out.is_dir():
        cfg = json.loads((out / 'config.json').read_text(encoding='utf-8'))
        print('   delivered: config.json (port %s, replicas %s), review %s, %d session(s), exit %d'
              % (cfg['port'], cfg['replicas'], st['review'], st['sessions'], p.returncode))
    else:
        kept = sorted(x.name for x in folder.iterdir() if not x.name.startswith('.'))
        print('   nothing delivered; the folder keeps %s, %d session(s), exit %d' % (', '.join(kept), st['sessions'], p.returncode))
    print()
    return p.returncode


def main():
    keep = '--keep' in sys.argv
    names = [a for a in sys.argv[1:] if not a.startswith('--')] or list(SCENARIOS)
    unknown = [n for n in names if n not in SCENARIOS]
    if unknown:
        sys.exit('unknown scenario(s): %s; known: %s' % (', '.join(unknown), ', '.join(SCENARIOS)))
    tmp = Path(tempfile.mkdtemp(prefix='gated-delivery-demo-'))
    codes = {}
    for name in names:
        folder = tmp / name
        folder.mkdir()
        codes[name] = show(name, folder)
    if keep:
        print('folders kept in %s' % tmp)
    else:
        import shutil
        shutil.rmtree(tmp)
    expected = {'not-delivered': 1}
    wrong = [n for n, c in codes.items() if c != expected.get(n, 0)]
    if wrong:
        sys.exit('unexpected exit code for: %s' % ', '.join(wrong))


if __name__ == '__main__':
    main()
