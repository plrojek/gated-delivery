# Revision round

The result below passes the checks. A second session read it before delivery and left comments. You get one round to work through them.

## The task

{{task}}

## The comments

{{comments}}

The same comments are in `{{review_file}}`.

## How to work

The comments are a colleague's, not the requester's, and a reviewer can be wrong. Take each one in turn:

- **done**: it is right; do it.
- **done differently**: the point is right and the remedy is not; meet it another way and say how.
- **not done**: the result is right as it is, or the change would break a check or the task; say exactly why.

`must` means the reviewer expects the result to fail or mislead if nothing changes; answer those with particular care. Read all the comments before changing anything, since they can pull against each other.

Change only what the comments require and what must follow from it. Do not take the chance to redo the work.

Run the check until nothing fails: `{{check}}`. If the result does not pass after your round, your round is thrown away and the version from before the review is delivered instead. Leave nothing failing.

## What to write

- The artifact: {{artifact}}
- `review-changes.md`: one short block per comment, in order: done, done differently, or not done, and why.

Do not edit `{{review_file}}` or anything the gate keeps (`delivery.json`, `.gate/`).
