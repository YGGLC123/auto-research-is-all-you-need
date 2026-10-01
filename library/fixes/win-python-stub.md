---
id: win-python-stub
kind: fix
status: candidate
phases: [bootstrap, experiments]
---

# Bare 'python' on Windows can be the Store stub (exit 49, silent) — invoke 'py' first

Symptom: command using 'python' exits 49, zero output. Diagnosis: PATH hits the WindowsApps store stub. Fix: prefer 'py', then python3, then python; encode in provider chains and docs. Verify: 'py --version' works while 'python --version' is silent.
