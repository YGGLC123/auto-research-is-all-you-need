# role-fanout — dispatch 2–3 long-lived roles in one round, star-shaped

**目的** Advance a turn with several **standing roles** at once — chronicler, figure engineer, evidence
steward (or experiment/theory operator) — without letting them talk to each other. The CLI mints one
`ROLE_TASK/v1` envelope per role, each role answers in its own fresh instance, every reply lands as a file,
and every reply is booked with `role receipt`. The mainline is the only place their outputs ever meet.

**何时用** A turn that changed the story *and* touched figures or evidence: instead of three sequential
dispatches you send them in one round and merge once. Also the standard shape for a session's first round
after `role status` lists several `REHYDRATE_REQUIRED` roles. Contract: [../docs/role-fleet.md](../docs/role-fleet.md)
(§3 envelope, §4 receipt, §6 controller discipline).

**args**
- `ROLES` — the 2–3 roles and the `kind` each is dispatched with (`// EDIT ME`). One entry per role;
  **never two entries for the same role** — one un-receipted dispatch per role at a time (§6.3).
- `PROJECT` / `PLUGIN` — project root and plugin root, so the CLI paths resolve from anywhere.
- `OUT` — where the replies are written before they are booked.

## 脚本

```js
export const meta = {
  name: "role-fanout",
  description: "Dispatch 2-3 standing roles in one round (star), then book every reply with role receipt.",
  phases: ["Dispatch", "Answer", "Receipt"],
};

// EDIT ME — the roles for this round, and the kind each is dispatched with.
const ROLES = [
  { role: "chronicler",       kind: "CHRONICLE_TURN", agent: "research-chronicler" },
  { role: "figure-engineer",  kind: "FIGURE_REQUEST", agent: "research-figure-engineer", kTask: "K07" },
  { role: "evidence-steward", kind: "EVIDENCE_AUDIT", agent: "research-evidence-steward" },
];

const PROJECT = ".";                                         // EDIT ME — project root
const PLUGIN  = "<plugin>";                                  // EDIT ME — plugin root (holds scripts/)
const ROLE    = `py ${PLUGIN}/scripts/role_runtime.py role`; // POSIX: python3
const OUT     = ".research-os/scratch/role-fanout";          // disposable; receipts are the durable trace

const REPLY = {/* schema: role-specific fixed fields + flags[] + proposed_edges[] + memory_delta{} */};

export default async function () {
  // DISPATCH — the CLI mints each envelope (memory digest + graph context + idempotency key) and registers
  // it as that role's live dispatch. Never hand-write an envelope. Output is JSON: {message_id, envelope, …}.
  const dispatched = ROLES.map((r) => JSON.parse(sh(
    `${ROLE} dispatch ${r.role} --root ${PROJECT} --kind ${r.kind} --turn current` +
    (r.kTask ? ` --k-task ${r.kTask}` : ""),
  )));

  // ANSWER — one fresh instance per role, in parallel. Each prompt carries ONLY that role's envelope:
  // no role ever sees another role's envelope or reply. That is the star, enforced here by construction.
  const replies = await parallel(dispatched.map((d, i) => () => agent(
    `You are dispatched under the role-fleet contract. Envelope (verbatim; read nothing beyond its refs): ` +
    JSON.stringify(d.envelope) + ` ` +
    `Propose only. Return your profile's fixed fields plus flags[], proposed_edges[], memory_delta{}. ` +
    `<=30 lines. Write no files. Contact no other role — a cross-role need is a K## item in flags.`,
    { schema: REPLY, subagent_type: ROLES[i].agent },
  )));

  // RECEIPT — every reply becomes a file, then a booked receipt. A dispatch without one is an incident.
  const receipts = replies.map((reply, i) => {
    const path = `${OUT}/${dispatched[i].message_id}.json`;
    write(path, JSON.stringify(reply));            // reply -> disk; the CLI reads the file, never the model
    return JSON.parse(sh(
      `${ROLE} receipt ${ROLES[i].role} --root ${PROJECT} ` +
      `--message-id ${dispatched[i].message_id} --file ${path}`,   // a replay returns ALREADY_ANSWERED
    ));
  });

  // MERGE — the mainline is the only meeting point. Conflicting proposals are adjudicated by you, not negotiated.
  return {
    booked:    receipts.map((r, i) => `${ROLES[i].role}: ${r.result}`),  // ANSWERED | ANSWERED_NO_CHANGE | REJECTED
    flags:     replies.flatMap((r, i) => (r.flags ?? []).map((f) => `${ROLES[i].role}:${f}`)),
    edges:     replies.flatMap((r) => r.proposed_edges ?? []),           // -> graph link, one at a time, by you
    conflicts: [],                                                      // fill when two roles touch one node
  };                                                                    // <=30 lines rendered
}
```

## 产物落盘

Replies are written under `.research-os/scratch/role-fanout/<message_id>.json` and are **disposable** —
the durable trace is `.research-os/roles/receipts/<message_id>.json`, written by `role receipt`, which also
merges each `memory_delta` (≤12 KB per role), advances the cursor, and returns any `K##` task. Re-booking the
same `message_id` returns `ALREADY_ANSWERED` and merges nothing twice, so a retried round is safe.
`proposed_edges` are **not** written by the receipt: you apply them yourself, one at a time, with
`py <plugin>/scripts/research_graph.py link --from … --to … --kind … [--polarity +|-] --basis …`, so every row in
`.research-os/graph/edges.jsonl` has a controller behind it. A `CHRONICLE_TURN` receipt additionally routes
through the narrative commit path — do not also call `turn record-disposition` by hand for that manifest.

## 裁剪指引

- **`sh` / `write` are your host's escapes** — on Claude Code they are a Bash call and a Write call; the
  script is a plan you execute step by step, not a sandboxed runtime. Everything else is the standard
  mini-API in [README.md](README.md).
- **Star topology is not optional.** Never pass one role's reply into another role's prompt, and never
  build a "reconciliation agent" that receives two replies. If figure and evidence disagree about a node,
  *you* decide and record a decision ([../docs/role-fleet.md](../docs/role-fleet.md) §6.4). A workflow that
  chains roles has rebuilt the group chat this contract exists to prevent.
- **One un-receipted dispatch per role.** Two entries for the same role in `ROLES`, or a second round
  before the first was booked, is refused by the CLI. Scale the round by adding *different* roles
  (experiment-runner with `RUN_RECONCILE`, theory-operator with `CLAIM_UPDATE`), never by adding lanes to one.
- **`CHRONICLE_TURN` belongs to the chronicler alone** — the CLI refuses that kind for any other role, since
  it drives the one narrative commit transaction.
- **Book even the empty answer.** `ANSWERED_NO_CHANGE` is a real receipt; skipping it because "nothing
  happened" is what produces `DISPATCH_UNANSWERED` at the next session start.
- **Judges are not roles.** `research-verifier` / `research-refuter` / `research-reproducer` stay one-shot
  and gate-triggered ([../docs/verification-policy.md](../docs/verification-policy.md)); they are never
  registered here and never given a memory.
- Trim to 2 roles for an ordinary turn; 3 is the practical ceiling before the merge stops being cheap.
