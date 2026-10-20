# Second attempt

A session worked on the task below and ended with a result that does not pass the checks. You are a different session and you get one attempt to put it right. If your result does not pass either, nothing is delivered, and this folder is kept as a record of what went wrong.

## The task

{{task}}

## What failed

{{failures}}

Warnings, which do not block delivery:

{{warnings}}

The same list is in `{{repair_file}}`.

## How to work

1. Run the check yourself, so that you see the failures and not only the list: `{{check}}`
2. Decide what kind of failure each one is:
   - **A mistake**: a wrong value, a missing field, a text that no longer matches the result. Correct it and touch nothing else.
   - **A knot**: the first session went in circles because its approach cannot pass. Change the approach, as much as that takes.
   - **A conflict in the task itself**: two requirements that no result can meet together. Make the best result that passes, decide which requirement gives way, and say which one, why, and what would have to change for the other answer. Do not pass by dropping the thing the task asks for most.
3. Check again until nothing fails.
4. If a failure is still there when you are done, leave the result in its best state and say in `repair-notes.md` what cannot be satisfied and why: the task, the inputs, or a check that is wrong for this case. That note is how the cause gets fixed.

## What to write

- The artifact: {{artifact}}
- `repair-notes.md`, always: a few lines on what kind of failure it was and what you changed.

Do not edit `{{repair_file}}`, `{{review_file}}` or anything the gate keeps (`delivery.json`, `.gate/`).
