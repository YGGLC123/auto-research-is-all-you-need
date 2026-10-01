# migration-batch — discover → isolated per-item change → verify

**目的** Apply the same mechanical change across many files: discover the worklist, then process each
target in its own git worktree so concurrent edits never collide, verifying each item independently. A
target that will not migrate cleanly is reported as a finding, not silently patched around.

**何时用** A repo-wide same-shape refactor — an API rename, an import rewrite, a config-key migration —
where the change is mechanical but the scale wants parallelism and per-item isolation. Not for changes
that need cross-file reasoning; those stay on the mainline.

**args**
- `CAP` — the silent-cap: the most files one run will touch without an explicit human OK (`// EDIT ME`).
- Prompt slot `<migration rule>` — the exact, mechanical transformation, stated so any agent applies it
  identically.

## 脚本

```js
export const meta = {
  name: "migration-batch",
  description: "Discover targets, apply one mechanical change per file in an isolated worktree, verify each.",
  phases: ["Discover", "Migrate", "Verify"],
};

const CAP = 50;                                        // EDIT ME — silent-cap; more than this must be logged
const OVERFLOW = "research/migrations/<run>/overflow.txt";
const WORKLIST = {/* schema: {paths: string[]} */};
const RESULT   = {/* schema: {path, changed, verify: "PASS|FAIL", diff_path, note} */};

export default pipeline(
  null,
  // DISCOVER — one agent lists the worklist; it must not edit anything
  () => agent(
    `List every file matching <migration rule>. Return paths only, one per line; do NOT edit. <=40 lines.`,
    { schema: WORKLIST },
  ),
  // MIGRATE + VERIFY — one worktree-isolated agent per file
  (found) => {
    const targets = found.paths;
    if (targets.length > CAP)                          // silent-cap MUST be logged, never quietly truncated
      log(`silent-cap: ${targets.length} targets > ${CAP}; processing ${CAP}, overflow -> ${OVERFLOW}`);
    return parallel(targets.slice(0, CAP).map((path) => () => agent(
      `In an ISOLATED git worktree, apply <migration rule> to ${path} ONLY. Do not fix unrelated breakage ` +
      `— a change that will not apply cleanly is itself the finding. Verify the file after; ` +
      `return RESULT with diff_path. <=40 lines.`,
      { schema: RESULT, isolation: "worktree" },
    )));
  },
);
```

## 产物落盘

Each agent's diff and verify output land under `research/migrations/<run>/<path>.diff`; the overflow list
(targets beyond `CAP`) is written to `OVERFLOW`. The workflow returns the `RESULT` rows — split them into
applied / failed, and route every `verify: "FAIL"` to a repair item in state rather than merging blindly.

## 裁剪指引

- **The silent-cap is a red line, not a suggestion.** If `targets > CAP`, log the overflow and stop at
  `CAP`; a silent truncation hides scope from the user. Raise `CAP` deliberately, in the diff the user sees.
- **Worktree isolation prevents collision.** Keep `isolation: "worktree"` so parallel agents never edit a
  shared tree; merge worktrees back on the mainline after the results are reviewed, not inside the fan-out.
- **"Do not fix unrelated breakage" is deliberate.** A clean-apply failure is signal that the rule is wrong
  or the file is special — capture it, do not let an agent improvise a repair that the batch cannot audit.
- For a dry run, drop the edit verb from `<migration rule>` and have each agent only report whether the
  change *would* apply — same fan-out, zero writes.
