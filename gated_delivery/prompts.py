"""Prompt templates for the sessions the gate asks for.

The first session (`produce`) gets your task as written. The other three get a template that
carries your task inside it:

    repair.md        a fresh session, the list of failures, one attempt
    second-look.md   a different session reads the passing artifact and writes review.json
    revise.md        one round on the second look's comments

Placeholders are written {{name}}; an unknown one is left as it is, so a task may contain template
syntax of its own. Values: task, task_file, artifact, files, check, failures, warnings, comments,
repair_file, review_file, dir, attempt, max_attempts.

To change a template, write the defaults out with `gated-delivery prompts DIR`, edit them, and pass
`--prompts DIR`. A file missing from DIR falls back to the default.
"""
from __future__ import annotations

import re
from importlib import resources
from pathlib import Path

from .gate import PRODUCE, REPAIR, REVISE, SECOND_LOOK

TEMPLATED = (REPAIR, SECOND_LOOK, REVISE)
NAMES = {REPAIR: 'repair.md', SECOND_LOOK: 'second-look.md', REVISE: 'revise.md'}
SLOT = re.compile(r'\{\{(\w+)\}\}')


def default_template(role: str) -> str:
    return resources.files('gated_delivery').joinpath('prompts', NAMES[role]).read_text(encoding='utf-8')


def template(role: str, override_dir: Path | str | None = None) -> str:
    if override_dir:
        p = Path(override_dir) / NAMES[role]
        if p.is_file():
            return p.read_text(encoding='utf-8')
    return default_template(role)


def render(text: str, values: dict) -> str:
    """one pass: values are not scanned again, so a task holding {{x}} stays as written"""
    return SLOT.sub(lambda m: str(values[m.group(1)]) if m.group(1) in values else m.group(0), text)


def bullets(items, empty: str = '(none)') -> str:
    lines = ['- %s' % x for x in items]
    return '\n'.join(lines) if lines else empty


def comment_lines(comments: list[dict]) -> str:
    out = []
    for c in comments:
        head = ' '.join(str(c[k]) for k in ('id', 'weight') if c.get(k))
        where = ' (%s)' % ', '.join('%s: %s' % (k, c[k]) for k in ('where', 'file', 'line') if c.get(k)) if any(
            c.get(k) for k in ('where', 'file', 'line')) else ''
        out.append('- %s%s%s' % ('[%s] ' % head if head else '', str(c['text']).strip(), where))
    return '\n'.join(out) if out else '(none)'


def write_defaults(folder: Path | str) -> list[Path]:
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    written = []
    for role in TEMPLATED:
        p = folder / NAMES[role]
        if p.exists():
            raise FileExistsError(p)
        p.write_text(default_template(role), encoding='utf-8')
        written.append(p)
    return written


__all__ = ['PRODUCE', 'TEMPLATED', 'template', 'default_template', 'render', 'bullets', 'comment_lines', 'write_defaults']
