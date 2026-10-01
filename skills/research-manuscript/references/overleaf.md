# Conditional Overleaf Workflow (capability: `overleaf-sync`)

Load this reference only when the user identifies an exact Overleaf project as a manuscript source or synchronization destination. Overleaf access is not a default manuscript dependency; it is the `overleaf-sync` capability, probed at phase entry (`on_missing: blocker` — queue the sync, keep drafting locally).

## Route by host (registry order — first reachable wins)

| host | first choice | notes |
|---|---|---|
| claude-code | `overleaf-skill` (Overleaf official Git integration, git-bridge) | clone project → overlay paper files → commit/push; pull-back supported; NOT browser automation |
| codex | chrome UI via `chrome:control-chrome` with the user's existing authenticated session | only when Git or a source export cannot perform the required project-specific UI operation |

On either host, an existing authorized local checkout or a current source export/ZIP supplied by the user outranks any live remote operation. Google Drive can carry an explicitly authorized source export, but it is not an Overleaf synchronization substitute. Overleaf Git access depends on the user's account plan and a project-scoped Git token — never assume it exists; if the probe fails, raise the blocker and continue locally.

## Ground the project and authority

Record the project name or URL, main TeX entry point, compiler, bibliography route, intended sync direction, and project-scoped authorization (`state.authorization`). Select one canonical writer for the current operation. Do not edit local and web copies concurrently.

## Establish a recoverable baseline

Fetch or export the current source, identify the base commit or compute a timestamped source snapshot SHA-256, and compile the untouched baseline. Preserve the project structure, main paper, supplement, bibliography, figures, and build configuration. Stop if the baseline cannot be associated with the intended project or if concurrent edits make the base ambiguous.

## Edit and verify locally

Make scoped source changes locally. Compile via the `latex-toolchain` capability (latexmk → pdflatex); inspect the log and render the resulting PDF for visual QA. Resolve errors, broken references, missing citations, figure failures, and anonymity leaks before synchronization. A successful command without PDF inspection is not sufficient.

## Synchronize safely

Immediately before any remote write, re-fetch or re-check the remote for concurrent changes. Show or record the exact changed files, diff, destination project, and sync direction. Never force-push, blindly overwrite a whole project archive, or upload uninspected files. Stop on a conflict, project mismatch, or unexpected remote revision.

After the authorized write, run or observe the Overleaf compile once and verify the log and rendered PDF correspond to the changed source. Record only non-secret evidence: local base commit or snapshot hash, remote revision or timestamp when observable, changed paths, outcome, and an immutable `run_id` when the codex chrome UI route was used. Never record credentials, cookies, tokens, or session dumps.

## Sharing

**Sharing discipline.** Collaborators receive cloud links only, never local paths. Formal anonymous submissions receive only the anonymous PDF / supplement / code ZIP — never an Overleaf or GitHub link that could deanonymize. Test any new mechanics on a sandbox project first, never on a real one. When one paper goes out as several copies (one per coauthor or reviewer), keep a single registry of which copy went where, and dry-run any batch push before it writes.

If the preferred route is unavailable or unstable, do not switch to an unapproved browser-control stack. Continue through an authorized local checkout or export when possible; otherwise raise a blocker with the queued sync as its `queued_action` and report the exact boundary.
