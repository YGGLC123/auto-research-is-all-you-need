# novelty-sweep — multi-lens prior-art search → collision verify

**目的** Search for prior art through four independent blind lenses (by venue, by method, by term, by
author), merge and de-duplicate the hits, then verify each candidate for a real collision with your
claim. Repeat until two consecutive rounds surface nothing new (loop-until-dry).

**何时用** Novelty/collision checking before a "first to…" claim, or a defensive-citation sweep. The
blind lenses catch prior art that a single query framing would miss; the verify pass stops an
adjacent-but-different paper from being logged as a collision.

**args**
- `LENSES` — usually the four below; drop `by-author` if the field has no obvious incumbents.
- Prompt slot `<claim>` — the exact claim being checked for novelty.

## 脚本

```js
export const meta = {
  name: "novelty-sweep",
  description: "Four-lens blind prior-art sweep, dedup, per-candidate collision verify; two dry rounds stop.",
  phases: ["Sweep", "Verify"],
};

const LENSES = ["by-venue", "by-method", "by-term", "by-author"];   // EDIT ME
const CANDIDATE = {/* schema: {id, title, authors, year, venue, url, why_relevant} */};
const COLLISION = {/* schema: {verdict: "COLLIDES|ADJACENT|CLEAR", overlap, evidence_path} */};

export default async function () {
  const seen = new Set();
  const collisions = [];
  let dry = 0;
  while (dry < 2) {                                     // loop-until-dry: two empty rounds and stop
    // Sweep — four blind lenses; none sees the others' hits
    const batches = await parallel(LENSES.map((lens) => () => agent(
      `Blind prior-art search for <claim> via the "${lens}" lens ONLY. You have not seen other lenses. ` +
      `Skip ids already found: ${[...seen].join(", ") || "none"}. Return new candidates as rows. <=30 lines.`,
      { schema: CANDIDATE },
    )));
    const fresh = dedupeById(batches.flat()).filter((c) => !seen.has(c.id));   // merge across lenses
    if (fresh.length === 0) { dry++; continue; }
    dry = 0;
    fresh.forEach((c) => seen.add(c.id));
    // Verify — one collision check per fresh candidate
    const verdicts = await parallel(fresh.map((c) => () => agent(
      `Does ${JSON.stringify(c)} actually collide with <claim>? Quote the overlapping construction/result; ` +
      `decide COLLIDES / ADJACENT / CLEAR with a citation. <=40 lines.`,
      { schema: COLLISION, subagent_type: "research-verifier" },
    )));
    collisions.push(...verdicts.filter((v) => v.verdict === "COLLIDES"));
  }
  return collisions;                                   // only verified collisions reach the mainline
}
```

## 产物落盘

Each lens writes its raw candidate list to `research/novelty/<run>/<lens>.md`; verifiers write the
per-candidate collision analysis (with quoted overlap) to `research/novelty/<run>/verify/`. The
workflow returns only the `COLLIDES` rows — those become defensive citations or a novelty blocker.

## 裁剪指引

- **`dedupeById` is yours to define** — normalize on DOI/arXiv id first, then a title+year fuzzy key;
  cross-lens duplicates are the common case and must merge before the verify pass to avoid double work.
- **Blindness is load-bearing.** Keep the `you have not seen other lenses` line; if you let lenses read
  each other, they converge and stop finding the paper the framing hid.
- Two dry rounds is the default stop; raise to three for a high-stakes "first" claim, lower to one for a
  quick scan.
- **Run this at the novelty gate, once** ([../docs/verification-policy.md](../docs/verification-policy.md)) — the
  per-candidate verify pass is what makes the sweep trustworthy, but the sweep itself is a gate-point spend, not
  something to re-run after every draft revision.
- `ADJACENT` is not `CLEAR` — surface adjacent work to the mainline as related-work fodder even though it
  does not block the novelty claim.
