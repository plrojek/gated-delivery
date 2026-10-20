# gated-delivery

A model session makes something: a config, a plan, a document, a patch. Code checks it. This is the gate between the two. After every session it decides from the files what happens next, and it can say no.

The model proposes, code disposes: a failure gets one repair by a fresh session; a pass gets a second look by a different session and one revision round on its comments; if nothing passes, nothing is delivered.

It is extracted from the delivery gate of [garden-planner](https://github.com/plrojek/garden-planner), where a model writes a garden plan and code decides whether it goes out. This is the same gate without the garden. Python standard library only, 3.11 or later. The pattern ran with real model sessions there; this extraction is tested with fake sessions only.

## How it fits together

```
produce ──▶ check ──fails──▶ repair: a fresh session gets the failures ──▶ check ──fails──▶ NOT DELIVERED
              │                                                              │              (folder kept as a
            passes ◀───────────────────────────────────────────────────── passes            defect report)
              │
              ▼
        second look: a different session reads it, changes nothing, writes review.json
              │
              ├── no comments ──▶ DELIVER
              └── comments ──▶ revise: one round ──▶ check ──passes──▶ DELIVER
                                                       │
                                                     fails ──▶ restore the version from before
                                                               the second look ──▶ check ──▶ DELIVER
                                                                                     │
                                                                                   fails ──▶ NOT DELIVERED
```

## The state table

| State of the folder | Next step |
|---|---|
| no session judged yet | `produce` |
| the first session ended and the check fails | `repair`: a fresh session gets `repair.json` |
| the repair ended and the check still fails | `not-delivered`: nothing ships; the folder is kept |
| an attempt passes | `second-look`: a different session writes `review.json` |
| the second look has no comments | `deliver` |
| the second look left no readable `review.json` | `deliver`, marked `review: missing` |
| the second look has comments | `revise`: one round on them |
| after that round the check passes | `deliver` |
| after that round the check fails | the version from before the second look is restored and checked again: `deliver` if it passes, `not-delivered` if it doesn't |

The rules:

- A failure stops delivery. Warnings never do; they are recorded in the state and passed on.
- Two attempts (the first session and one repair), one second look, one revision: at most four model sessions. `--attempts N` changes the first number. `--no-second-look` delivers a passing artifact at once.
- The second look changes nothing. If it edits the artifact anyway, its edits are undone before anything else happens.
- Nothing is delivered without a passing check on the bytes that are delivered. The delivered copy goes to `delivered/` with a manifest of sha256 hashes, and that folder exists only while the state is `delivered`.

State lives in `delivery.json`, so each step can be resumed, and an outer orchestrator can ask what comes next.

## "Not delivered" is a feature

A gate that always ships something is a formatter. The outcome that makes this useful is the plain no: two sessions tried, the result still fails a rule, so nothing goes out. The folder then holds the failures of both attempts, the repair session's `repair-notes.md`, the prompts each session got and their logs. That is a defect report, and it usually tells you which was wrong: the task, the inputs, or a check.

There is no third attempt by default. If two fresh sessions can't pass, I'd rather read why than pay for a third.

## Try it

```sh
python3 -m unittest -v                                                   # 68 tests, offline, no model
python3 -m gated_delivery faults examples/service-config/faults.json     # 17 planted faults, all must be caught
python3 examples/service-config/demo.py                                  # every path through the gate, with fake sessions
```

`pip install .` gives you the `gated-delivery` command; without it, `python3 -m gated_delivery` does the same.

The example in `examples/service-config/` is a JSON service config with a small check suite. `fake_session.py` stands in for the model: a bad first attempt and a good repair, a repair that is still bad, a second look with a comment, a revision that breaks what passed, a reviewer that edits the artifact, a reviewer that writes nothing. `demo.py` runs each from an empty folder through the real command line.

## Plugging in your own

### The check

Any command. It runs with `/bin/sh` inside the artifact folder and answers with its exit code: 0 passes, 1 fails. On stdout it prints nothing or one JSON object:

```json
{"failures": [{"check": "port", "message": "port 80 is outside 1024-65535"}],
 "warnings": [{"check": "replicas", "message": "one replica: a restart takes the service down"}]}
```

A finding may be a plain string; `"port: port 80 is outside 1024-65535"` is read as check `port`. Any other exit code, output that isn't such an object, or exit 0 with failures listed means the check itself is broken. The gate then stops with exit code 2 and changes nothing, so a crashing check never costs an attempt.

### The session

Any command too. The gate writes the prompt to a file and runs the command in the artifact folder:

```sh
gated-delivery run work/ \
  --task task.md \
  --artifact config.json \
  --check 'python3 {here}/checks.py {dir}' \
  --session 'claude -p "$(cat {prompt})" --allowedTools Read Write Edit "Bash(python3:*)"'
```

Placeholders in commands: `{dir}` (the artifact folder), `{here}` (the folder of the config file the command came from, or the current folder for a flag), and for sessions `{role}`, `{prompt}`, `{attempt}`. They are shell-quoted for you, so don't quote them again. Sessions also get `GATE_DIR`, `GATE_ROLE`, `GATE_PROMPT` and `GATE_ATTEMPT`. A session's exit code is recorded and decides nothing: the gate judges the files.

`--artifact` takes glob patterns, relative to the folder, and is repeatable. Only matching files are snapshotted, restored and delivered; notes the sessions write (`repair-notes.md`, `review-changes.md`) stay in the folder as records.

Settings can live in a JSON file instead of flags (`--config gate.json`, see `examples/service-config/gate.json`); relative paths in it are relative to the file, and flags override it.

### Driving it from outside

`run` does the whole loop. To drive the sessions yourself:

```sh
gated-delivery status work/                    # the step the gate waits for; changes nothing
# ...run that session...
gated-delivery next work/ --config gate.json   # judges it; the last line printed is the next step
```

`next` is not idempotent. Each call means "the session the state is waiting for has ended", the same as the original. Ask with `status`.

### From Python

```python
from gated_delivery import Finding, Gate, Runner, Verdict

def check(folder):                      # or CommandCheck('python3 checks.py {dir}')
    return Verdict(failures=[Finding('port', 'port 80 is outside 1024-65535')], warnings=[])

def session(req):                       # req.role, req.prompt, req.prompt_file, req.folder, req.attempt
    run_my_model(req.prompt, cwd=req.folder)
    return 0

gate = Gate('work', check, ['config.json'])
state = Runner(gate, session, task=open('task.md').read()).run()
print(state['status'])                  # delivered or not_delivered
```

`gate.step()` and `gate.status()` are `next` and `status`.

### Prompts

The first session gets your task as written. The other three get a template with your task inside it, because a fresh session knows nothing else: `repair.md` (the failures, one attempt, what kind of failure each is), `second-look.md` (read it as someone with no stake in it, write what you wouldn't let out, an empty list is a good answer) and `revise.md` (weigh each comment as a colleague's: done, done differently, not done). `gated-delivery prompts my-prompts/` writes them out to edit; `--prompts my-prompts/` uses them. Placeholders are written `{{name}}`: `task`, `failures`, `warnings`, `comments`, `artifact`, `files`, `check`, `repair_file`, `review_file`, `dir`, `attempt`, `max_attempts`. Unknown ones are left as they are.

### Planted faults

A check that never fails proves nothing. `gated-delivery faults FILE` takes a base artifact that must pass and a list of faults, each a small edit plus the id of the check it must trip. It plants each one in a fresh copy and reports `caught` or `MISSED`. A fault can also expect a warning, or expect silence from its check, as a guard against false alarms. Edits are `replace`, `write`, `delete`, `json_set`, `json_delete` and `run` (any command, such as `patch`); from Python, any function of the folder. See `examples/service-config/faults.json`. Run it after every change to a check.

## Files in the folder

| Path | Written by | What it is |
|---|---|---|
| `delivery.json` | the gate | the state; `--state` puts it elsewhere |
| `repair.json` | the gate | the failures handed to the repair session |
| `review.json` | the second look | its verdict and comments |
| `.gate/before-review/` | the gate | the artifact as it was when it first passed |
| `.gate/prompts/`, `.gate/sessions/` | `run` | the prompt each session got, and its output |
| `delivered/` | the gate | the delivered copy and `.gate-manifest.json`; only while the state is `delivered` |

## Limits

- **The second look is a model too.** It is there for what a rule can't see, and it can miss it, or be wrong. The revision round weighs its comments rather than obeying them, and a revision that breaks the checks is thrown away. If the second look writes no review at all, the artifact still goes out, marked `review: missing`, because it did pass the checks. If you'd rather hold it, look for that state.
- **The check is the whole guarantee.** The gate enforces exactly what the check expresses. A weak check passes weak work.
- **Sessions can write anywhere in the folder**, including the state file and the snapshot. If you don't trust them, keep the state outside (`--state`) and sandbox the session command. A tampered snapshot still has to pass the check, or it ends in `not-delivered`.
- A session cut off by a crash is judged as it stands and counts toward the limit. Delete the state file to start over.
- An output folder belongs to one artifact folder. The gate clears its own output on every step that doesn't deliver, so two folders pointed at the same `--out` wipe each other. The default, `delivered/` inside the folder, can't collide.
- Commands run through `/bin/sh`, so POSIX only.
- No cost or token accounting. Put budgets and timeouts in the session command, or use `--session-timeout`.

## Differences from the original

The original gate also builds the plan, checks the designer's text against it, holds it to a layout the customer chose, and writes the customer's package. Those are domain parts and stay there. In the gate itself, three things are stricter here:

- After a restore, the restored version is checked again before it is delivered.
- The artifact is a set of glob patterns instead of two fixed files, and a restore also removes files added since the snapshot.
- Edits by the second look are always undone. The original kept them when the plan still passed.

## License

MIT, see `LICENSE`.
