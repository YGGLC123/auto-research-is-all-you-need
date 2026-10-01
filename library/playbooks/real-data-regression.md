---
id: real-data-regression
kind: playbook
status: candidate
phases: [method, experiments]
---

# Freeze no schema before regressing it against every real-world instance

Before freezing any contract: enumerate ALL real instances, read-only validate sweep, copy-migrate the hardest cases, fix contract to reality (read wide write strict), re-sweep, then freeze.
