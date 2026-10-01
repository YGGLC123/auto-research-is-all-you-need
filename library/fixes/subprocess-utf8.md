---
id: subprocess-utf8
kind: fix
status: candidate
phases: [experiments, reproducibility]
---

# subprocess.run(text=True) on Windows decodes GBK and crashes on UTF-8 child output

Fix: every subprocess.run(..., text=True) gets encoding='utf-8', errors='replace'; reconfigure sys.stdout/stderr to UTF-8 at CLI entry. Verify by rerunning the failing capture.
