#!/usr/bin/env python3
"""Build the demo project: a small, entirely made-up paper and one night of AI edits.

    python examples/build_demo.py                 # -> ./demo-project
    python examples/build_demo.py --out /tmp/demo

The paper, the numbers and the sources are fiction.  What is real is the
machinery: every write goes through the plugin's own CLIs, exactly as it would
in a live project, so the resulting map shows what the system records.

Monday, the researcher writes the story with the AI and states one rule of
taste.  They look at the map, then go to bed.  Overnight the AI rewrites a
claim, adds a caveat that trips the taste rule, drops a claim that collides
with prior work, and quietly demotes the flagship result to a robustness
appendix.  In the morning the map shows all of it -- and flags the demotion as
lever debt, because nothing ever refuted that result.
"""

from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

PLUGIN = Path(__file__).resolve().parent.parent
SCRIPTS = PLUGIN / "scripts"
ENV = dict(os.environ, PYTHONUTF8="1", PYTHONIOENCODING="utf-8")

CLAIM_KEYS = ("subject", "target", "predicate", "quantifier", "domain", "conditions", "polarity")


def run(script: str, *argv) -> dict:
    proc = subprocess.run([sys.executable, str(SCRIPTS / script)] + [str(a) for a in argv],
                          capture_output=True, text=True, encoding="utf-8", errors="replace", env=ENV)
    if proc.returncode != 0:
        raise SystemExit(f"{script} {' '.join(map(str, argv[:3]))} failed:\n{proc.stdout}\n{proc.stderr}")
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError:
        return {"raw": proc.stdout}


class Story:
    def __init__(self, root: Path):
        self.root = root
        self.ops = root / ".demo-ops.json"
        self.meta = root / ".demo-meta.json"

    def node(self, *argv):
        run("narrative.py", "node", argv[0], self.root, *argv[1:], "--out", self.ops)
        return json.loads(self.ops.read_text(encoding="utf-8"))["ops"]

    def commit(self, ops: list, message: str, at: str, by: str):
        self.ops.write_text(json.dumps({"ops": ops}, ensure_ascii=False), encoding="utf-8")
        self.meta.write_text(json.dumps({"message": message, "at": at,
                                         "author": {"role": by, "session": "demo"}},
                                        ensure_ascii=False), encoding="utf-8")
        run("narrative.py", "commit", self.root, "--ops-file", self.ops, "--meta-file", self.meta,
            "--now", at)

    def claim(self, nid, parent, title, statement, epistemic="PENDING", basis=(), bet=None):
        argv = ["add", "--id", nid, "--node-type", "claim", "--parent", parent, "--title", title,
                "--statement", statement, "--epistemic", epistemic]
        for key in CLAIM_KEYS:
            argv += ["--identity", "%s=%s" % (key, nid.lower() if key == "subject" else "-")]
        for ref in basis:
            argv += ["--basis", ref]
        if bet:
            argv += ["--live-bet", "--resolution-condition", bet]
        return self.node(*argv)

    def section(self, nid, parent, title, summary):
        return self.node("add", "--id", nid, "--node-type", "narrative", "--parent", parent,
                         "--title", title, "--summary", summary,
                         "--identity", "role_hint=section", "--identity", "thesis=%s" % nid.lower())


def build(out: Path) -> Path:
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    run("research_os.py", "bootstrap", out, "--title", "Do Reviewers Read Appendices?",
        "--stage", "manuscript_draft", "--paper-type", "empirical_ml",
        "--objective", "Measure whether reviewers engage with appendices, and what it costs authors")

    # -- evidence on record (fiction), and one decision the researcher signed
    (out / "evidence-ledger.md").write_text(
        "# Evidence ledger (synthetic demo data)\n\n| id | source | finding |\n|---|---|---|\n"
        "| E-01 | crawl of 12,000 OpenReview threads, 2018-2025 | median appendix length tripled |\n"
        "| E-02 | reference parser over all reviews | 11.4% of reviews cite an appendix item |\n"
        "| E-03 | matched pairs, proofs in body vs appendix | soundness -0.31 (se 0.07) |\n"
        "| E-04 | review length model | +38% words when the appendix is cited |\n"
        "| E-05 | placebo: random 'appendix map' on control papers | no change in mentions |\n",
        encoding="utf-8")
    (out / ".research-os" / "decisions.md").write_text(
        "# Research Decisions\n\n## 2026-09-28 headline is the 11% number\n"
        "- The 11% mention rate leads the paper (user-approved: headline-11pct).\n",
        encoding="utf-8")

    story = Story(out)
    monday = "2026-09-28T09:%02d:00Z"
    run("narrative.py", "init", out, "--title", "Reviewers rarely read appendices, and it shows in the scores",
        "--thesis", "appendix engagement is low and consequential", "--summary",
        "A measurement paper: how often reviews engage with appendices, and what that costs authors.",
        "--at", monday % 0, "--now", monday % 0)
    ops = []
    for nid, title, summary in (
            ("N-1", "Motivation: the appendix arms race", "Appendices keep growing; reviewing time does not."),
            ("N-2", "Measurement: do reviews mention the appendix?", "Parse every review for appendix references."),
            ("N-3", "Consequences: what an unread appendix costs", "Scores, rebuttals and what moves them."),
            ("N-4", "What venues could do", "Cheap interventions and how to test them.")):
        ops = story.section(nid, "R-0", title, summary)
        story.commit(ops, f"outline: {title.split(':')[0].lower()}", monday % (1 + int(nid[-1])), "user")
    claims = [
        ("C-1", "N-1", "Median appendix length tripled between 2018 and 2025",
         "Across 12,000 OpenReview threads the median appendix grew from 6 to 19 pages.", "HELD", ["E-01"], None),
        ("C-2", "N-1", "Authors move key results to the appendix to fit page limits",
         "A growing share of main theorems are stated in the body but proved only in the appendix.",
         "PENDING", [], None),
        ("C-3", "N-2", "Only 11% of reviews cite anything in the appendix",
         "11.4% of reviews reference a numbered appendix item; most of those are proofs.",
         "HELD", ["E-02", "decision:headline-11pct"], None),
        ("C-4", "N-2", "Appendix mentions cluster in the last two days of the review window",
         "If reviewers read appendices at all, they do it under deadline pressure.", "PENDING", [],
         "timestamps for the 2025 cycle arrive"),
        ("C-5", "N-3", "Papers whose proofs live only in the appendix get lower soundness scores",
         "Matched on topic and length, moving proofs to the appendix costs 0.31 points of soundness.",
         "HELD", ["E-03"], None),
        ("C-6", "N-3", "Rebuttals that point to the appendix recover half a point",
         "When authors quote the appendix in the rebuttal, soundness scores recover about half the gap.",
         "PENDING", [], None),
        ("C-7", "N-4", "A one-page 'appendix map' raises appendix mentions",
         "A structured map of the appendix at the end of the body makes reviewers use it.", "PENDING", [],
         "the randomized pilot with two workshops reports"),
    ]
    for i, (nid, parent, title, statement, ep, basis, bet) in enumerate(claims):
        ops = story.claim(nid, parent, title, statement, ep, basis, bet)
        story.commit(ops, f"add {nid}", monday % (10 + i), "user")
    story.commit(story.node("set-role", "--role", "headline", "--id", "C-3"), "headline: the 11% number",
                 monday % 20, "user")
    story.commit(story.node("set-role", "--role", "flagship", "--id", "C-5"),
                 "flagship: the soundness penalty", monday % 21, "user")
    run("taste_ledger.py", "--project", out, "add", "--by", "user", "--source-kind", "user",
        "--scope", "story", "--scope", "writing", "--stance", "avoid",
        "--text", "Never write our own work as a weakness",
        "--quote", "Don't hand reviewers a stick to beat us with.",
        "--pattern", r"\blimitations?\b|\bfail(s|ed|ure)?\b|\bwe do not claim\b")
    run("taste_ledger.py", "--project", out, "add", "--by", "user", "--source-kind", "user",
        "--scope", "figures", "--stance", "prefer", "--text", "Every main claim gets a figure",
        "--quote", "If it matters, draw it.")
    # the researcher looks at the map before bed: this is "your last view"
    run("research_map.py", "--project", out, "render", "--out", out / ".research-os" / "map" / "monday.html")

    # -- overnight: the AI keeps working
    night = "2026-10-01T02:%02d:00Z"
    story.commit(story.node("patch", "--id", "C-3", "--set",
                            'title="Only 9% of reviews cite the appendix once duplicates are removed"',
                            "--set", 'statement="After removing reviews that quote the same item twice, '
                                     '9.1% of reviews reference a numbered appendix item."'),
                 "tighten the measurement claim after de-duplication", night % 3, "main")
    story.commit(story.section("N-5", "R-0", "Appendix: robustness checks",
                               "Results that hold, but are not the story."),
                 "open a robustness appendix", night % 9, "main")
    story.commit(story.claim("C-9", "N-3", "Reviewers who cite the appendix write 38% longer reviews",
                             "Engagement shows up in effort: appendix-citing reviews are 38% longer.",
                             "HELD", ["E-04"]),
                 "add a cleaner engagement result", night % 14, "main")
    # one commit: hand the flagship role to C-9 and demote C-5 (the contract wants both at once,
    # and `commit` validates the combined tree -- a single `node patch` would be refused alone)
    ops = story.node("set-role", "--role", "flagship", "--id", "C-9")
    ops.append({"op": "patch_fields", "id": "C-5",
                "fields": {"state": {"narrative": "DEMOTED"},
                           "disposition_meta": {"target": "N-5",
                                                "reason": "reads better as a robustness check"}}})
    story.commit(ops, "reframe: the engagement result leads; soundness penalty to robustness",
                 night % 21, "main")
    story.commit(story.node("move", "--id", "C-5", "--new-parent", "N-5"), "move the soundness penalty to the appendix",
                 night % 22, "main")
    story.commit(story.node("patch", "--id", "C-6", "--set",
                            'statement="Rebuttals that quote the appendix recover about half the gap. '
                            'One limitation is that the effect fails to replicate on the 2023 cycle."'),
                 "add a caveat to the rebuttal result", night % 30, "main")
    story.commit(story.node("detach", "--id", "C-2", "--disposition", "DROPPED", "--reason",
                            "collides with prior work: Smith & Lee (2025) already measured this"),
                 "drop the motivation claim that prior work already covers", night % 41, "main")
    run("research_graph.py", "link", "--root", out, "--from", "ro::ledger:E-05", "--to", "ro::narr:C-7",
        "--kind", "supported_by", "--polarity", "-", "--basis", "placebo: the appendix map changed nothing")

    # the manuscript the AI also touched overnight
    (out / "paper").mkdir(exist_ok=True)
    (out / "paper" / "sec3_consequences.tex").write_text(
        "\\section{What an unread appendix costs}\n"
        "Reviewers who cite the appendix write 38\\% longer reviews.\n"
        "% note to self: check the 2023 cycle\n"
        "One limitation of our design is that we cannot observe reading time.\n",
        encoding="utf-8")
    for leftover in (story.ops, story.meta):
        leftover.unlink(missing_ok=True)
    return out


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="build the made-up demo project")
    parser.add_argument("--out", default="demo-project")
    parser.add_argument("--no-render", dest="no_render", action="store_true")
    args = parser.parse_args(argv)
    out = build(Path(args.out).resolve())
    if not args.no_render:
        page = out / ".research-os" / "map" / "research-map.html"
        result = run("research_map.py", "--project", out, "render", "--out", page)
        print("\n".join(result.get("summary") or []))
        print(f"\nopen {page}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
