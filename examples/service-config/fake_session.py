"""A stand-in for a model session, so the whole gate runs offline.

    python3 fake_session.py SCENARIO

Run by the gate inside the artifact folder, with GATE_ROLE and GATE_PROMPT set. It reads the role
and writes what a session in that scenario would write. It does not read the prompt beyond its
first line, which it prints so the session log shows which prompt it was given.

Scenarios:
    clean           a good first attempt; the second look has nothing to say
    repaired        a bad first attempt, a good repair, a second look with a comment, a good revision
    not-delivered   a bad first attempt and a repair that is still bad
    revise-breaks   a good first attempt; the revision on the second look's comment breaks it
    reviewer-edits  the second look edits the artifact, which it must not, and leaves no comment
    no-review       the second look ends without writing review.json
"""
import json
import os
import sys
from pathlib import Path

GOOD = {'name': 'payments-api', 'port': 8080, 'replicas': 1, 'log_level': 'info', 'health_path': '/healthz',
        'timeouts': {'connect_ms': 500, 'request_ms': 3000}}


def config(**changes):
    c = json.loads(json.dumps(GOOD))
    for k, v in changes.items():
        c[k] = v
    return c


BAD = config(port=80, log_level='debug', timeouts={'connect_ms': 3000, 'request_ms': 500})
STILL_BAD = config(timeouts={'connect_ms': 3000, 'request_ms': 500})
REVISED = config(replicas=3)
BROKEN = config(replicas=0)
COMMENT = {'verdict': 'ship after changes', 'comments': [
    {'id': 'r1', 'weight': 'must', 'where': 'config.json: replicas',
     'text': 'One replica means a restart takes the payments API down, and the task asks it to survive one '
             'instance restarting. Use at least 2; 3 leaves room for a rolling deploy.'}]}
NO_COMMENTS = {'verdict': 'ship as is', 'comments': []}

SCENARIOS = {
    'clean':          {'produce': GOOD, 'second-look': NO_COMMENTS},
    'repaired':       {'produce': BAD, 'repair': GOOD, 'second-look': COMMENT, 'revise': REVISED},
    'not-delivered':  {'produce': BAD, 'repair': STILL_BAD},
    'revise-breaks':  {'produce': GOOD, 'second-look': COMMENT, 'revise': BROKEN},
    'reviewer-edits': {'produce': GOOD, 'second-look': ('edit', config(port=81), NO_COMMENTS)},
    'no-review':      {'produce': GOOD, 'second-look': None},
}

NOTES = {
    'repair': 'A mistake, not a knot: port 80 and the two timeouts swapped. Set port 8080 and connect 500 ms, request 3000 ms.\n',
    'revise': 'r1 (must): done. replicas 1 -> 3.\n',
}


def write(name, data):
    Path(name).write_text(json.dumps(data, indent=2) + '\n', encoding='utf-8')


def main():
    scenario, role = sys.argv[1], os.environ['GATE_ROLE']
    first = Path(os.environ['GATE_PROMPT']).read_text(encoding='utf-8').splitlines()[0]
    print('fake %s session (%s), prompt: %s' % (role, scenario, first))
    action = SCENARIOS[scenario].get(role)
    if role == 'second-look':
        if isinstance(action, tuple):
            _, edited, review = action
            write('config.json', edited)
            write('review.json', review)
            print('edited config.json and wrote review.json')
        elif action is None:
            print('wrote nothing')
        else:
            write('review.json', action)
            print('wrote review.json with %d comment(s)' % len(action['comments']))
        return
    if action is None:
        sys.exit('the %s scenario has no %s session' % (scenario, role))
    write('config.json', action)
    if role == 'repair':
        Path('repair-notes.md').write_text(NOTES['repair'] if action is GOOD else
                                           'Could not reconcile the timeouts with the task as I read it.\n', encoding='utf-8')
    if role == 'revise':
        Path('review-changes.md').write_text(NOTES['revise'], encoding='utf-8')
    print('wrote config.json')


if __name__ == '__main__':
    main()
