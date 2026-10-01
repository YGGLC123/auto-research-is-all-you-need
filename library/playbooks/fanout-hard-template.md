---
id: fanout-hard-template
kind: playbook
status: candidate
phases: [method, loop]
---

# Fan out parallel writer agents only with a hard conventions template plus a mechanical linter

(1) freeze conventions template incl. return format+line cap; (2) each agent runs a mechanical self-check to green; (3) disjoint file partition; (4) central verify after merge. Anti-pattern: prose-only instructions.
