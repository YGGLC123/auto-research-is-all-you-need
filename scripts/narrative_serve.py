#!/usr/bin/env python3
"""auto-research narrative tree -- live page with in-place Q&A.

`narrative serve` starts a loopback-only HTTP server that

  * serves the narrative-tree page freshly rendered on every load
    (`narrative_render_html.build_payload` → the head snapshot, annotations,
    blame), so an answer landed a second ago is already on the page;
  * answers a question asked *at* a node: `POST /api/ask {node_id, question}`
    streams the reply as NDJSON deltas, then records the exchange as an
    append-only `qa` annotation (contract §10) under that node — the same
    record the chronicler would write, by the same rules (never overwritten,
    superseded only).

The answerer is the Claude CLI run in print mode with the node's context
(north star, path, statement, children, siblings, history, prior Q&A and the
≤40-line trajectory) and a fixed brief: answer in the reader's language, from
the material only, naming sections by title and never by id.  No tools, one
turn, cwd = a scratch directory so no project hook fires inside the nested
session.  The page never writes research state itself; the server writes one
annotation per answered question and nothing else.

    py scripts/narrative_serve.py --project <root> [--ref main] [--port 0]
                                  [--model opus] [--no-open]
    py scripts/narrative_serve.py --self-test

stdlib-only, Python >= 3.9.  Exit codes: 0 ok, 1 refusal, 2 environment/usage.
"""

from __future__ import annotations

import argparse
import glob
import io
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import uuid
import webbrowser
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
import research_os as ros  # noqa: E402
import narrative as nar  # noqa: E402
import narrative_render_html as page  # noqa: E402

DEFAULT_MODEL = "opus"
ANSWER_BY = "chronicler-live"
MAX_QUESTION = 2000
MAX_CONTEXT_CHARS = 60000
CLI_GLOBS = [
    "%USERPROFILE%/.vscode/extensions/anthropic.claude-code-*/resources/native-binary/claude.exe",
    "%LOCALAPPDATA%/Programs/claude/claude.exe",
]
# Tools the nested session must not have: the answer is read-only by contract.
DISALLOWED_TOOLS = "Bash,PowerShell,Edit,Write,MultiEdit,NotebookEdit,Agent,Task,WebFetch,WebSearch"


# ============================================================ CLI discovery

def _version_key(path: str) -> list:
    nums = re.findall(r"(\d+)", path)
    return [int(x) for x in nums]


def find_claude_cli() -> str | None:
    found = shutil.which("claude")
    if found:
        return found
    for pattern in CLI_GLOBS:
        hits = glob.glob(os.path.expandvars(os.path.expanduser(pattern)))
        if hits:
            return sorted(hits, key=_version_key)[-1]
    return None


# ============================================================ context + prompt

def _title(node_id: str, node: dict) -> str:
    t = str(node.get("title") or "")
    if node_id and t.startswith(node_id):
        rest = t[len(node_id):].lstrip(" :：·-—")
        if rest:
            t = rest
    return t or "（无标题）"


def _clean(text: str, limit: int = 4000) -> str:
    s = str(text or "").strip()
    return s if len(s) <= limit else s[:limit - 1] + "…"


_ID_TOKEN = re.compile(r"\b([A-Z]{1,2}-[0-9]+(?:\.[0-9]+)*)\b")


def _humanize(text: str, nodes: dict) -> str:
    """Machine readouts name nodes by id; the answerer must see titles."""
    def swap(match):
        nid = match.group(1)
        return "「%s」" % _title(nid, nodes[nid]) if nid in nodes else match.group(0)
    return _ID_TOKEN.sub(swap, text)


def node_context(control: Path, ref: str, node_id: str) -> dict:
    """Everything the answerer may rely on, and nothing outside the store."""
    snapshot = nar.load_head_snapshot(control, ref)
    nodes = snapshot.get("nodes") or {}
    node = nodes.get(node_id)
    if not node:
        raise nar.NarrativeError("NODE_NOT_FOUND", f"{node_id} is not in {ref}")
    parent_of = {}
    for pid, body in nodes.items():
        for kid in body.get("children") or []:
            parent_of[kid] = pid
    path = []
    cur = parent_of.get(node_id)
    while cur and cur != snapshot.get("root"):
        path.append(_title(cur, nodes[cur]))
        cur = parent_of.get(cur)
    path.reverse()
    root = nodes.get(snapshot.get("root")) or {}
    parent = parent_of.get(node_id)
    siblings = [k for k in (nodes.get(parent, {}).get("children") or []) if k != node_id and k in nodes]
    children = [k for k in (node.get("children") or []) if k in nodes]
    annotations = page.collect_annotations(control).get(node_id, [])
    blame = page.collect_blame(control, ref).get(node_id, [])
    try:
        trajectory = [_humanize(str(x), nodes) for x in nar.trajectory_lines(control)]
    except Exception:
        trajectory = []
    roles = {v: k for k, v in (snapshot.get("role_assignments") or {}).items() if v}
    return {
        "north_star": _clean(root.get("statement") or root.get("summary") or root.get("title"), 1200),
        "path": path,
        "title": _title(node_id, node),
        "summary": _clean(node.get("summary"), 800),
        "statement": _clean(node.get("statement"), 6000),
        "state": node.get("state") or {},
        "role": roles.get(node_id),
        "children": [{"title": _title(k, nodes[k]), "summary": _clean(nodes[k].get("summary"), 200)}
                     for k in children[:12]],
        "siblings": [_title(k, nodes[k]) for k in siblings[:12]],
        "history": [{"at": b.get("at"), "kind": b.get("kind"), "cause": b.get("cause"),
                     "message": _clean((b.get("why") or {}).get("note") or "", 200),
                     "changes": _clean("；".join(
                         (c["field"] + "：" + (c.get("before") or "无") + " → " + (c.get("after") or "无"))
                         if c.get("kind") == "field" else
                         ("正文 +%d −%d 句" % (c.get("ins", 0), c.get("del", 0))) if c.get("kind") == "diff"
                         else c.get("text", "") for c in (b.get("changes") or [])), 400)}
                    for b in blame[:6]],
        "qa": [{"question": _clean(a.get("question"), 600), "answer": _clean(a.get("answer"), 1200)}
               for a in annotations[-6:] if a.get("question")],
        "trajectory": trajectory[:40],
    }


EP_CN = {"HELD": "已成立", "KILLED": "已证否", "PENDING": "待定"}
NA_CN = {"ACTIVE": "在用", "DROPPED": "已弃", "DEMOTED": "已降级", "MERGED": "已并入"}
ROLE_CN = {"headline": "头条", "flagship": "旗舰", "backbone": "承重",
           "empirical_core": "实证核", "foil": "对照"}
KIND_CN = {"refine": "修订", "restructure": "重构", "overthrow": "推翻", "merge": "合并"}


def build_prompt(ctx: dict, question: str) -> str:
    lines = [
        "你是这个研究项目的史官，负责回答读者在叙事树某一段旁边提出的问题。",
        "规则：只依据下面给出的材料作答；材料里没有的就直说“叙事里没有记载”，不要猜；",
        "用中文，先给结论再给依据；提到别的段落时用它的标题，不要出现任何内部编号或代码；",
        "默认 250 字以内，读者明确要详细时才展开；如果这个问题更适合问另一段，指出那一段的标题。",
        "",
        "【项目主线】" + (ctx.get("north_star") or "（未记载）"),
        "【这一段的位置】" + (" › ".join(ctx.get("path") or []) or "（一级章节）"),
        "【这一段的标题】" + ctx["title"],
    ]
    st = ctx.get("state") or {}
    words = [EP_CN.get(st.get("epistemic"), st.get("epistemic") or "待定")]
    if st.get("narrative") and st.get("narrative") != "ACTIVE":
        words.append(NA_CN.get(st["narrative"], st["narrative"]))
    if ctx.get("role"):
        words.append("角色：" + ROLE_CN.get(ctx["role"], ctx["role"]))
    lines.append("【状态】" + " · ".join(words))
    if ctx.get("summary"):
        lines.append("【简介】" + ctx["summary"])
    lines.append("【正文】" + (ctx.get("statement") or "（这一段还没有正文）"))
    if ctx.get("children"):
        lines.append("【下级段落】")
        for c in ctx["children"]:
            lines.append("- " + c["title"] + ((": " + c["summary"]) if c.get("summary") else ""))
    if ctx.get("siblings"):
        lines.append("【同级段落】" + "；".join(ctx["siblings"]))
    if ctx.get("history"):
        lines.append("【这一段的修改历史（新在前）】")
        for h in ctx["history"]:
            lines.append("- %s · %s · %s%s" % (h.get("at") or "", KIND_CN.get(h.get("kind"), h.get("kind") or ""),
                                               h.get("changes") or "（未改文字）",
                                               ("；说明：" + h["message"]) if h.get("message") else ""))
    if ctx.get("qa"):
        lines.append("【这一段已有的问答】")
        for q in ctx["qa"]:
            lines.append("问：" + q["question"])
            lines.append("答：" + (q.get("answer") or "（尚未回答）"))
    if ctx.get("trajectory"):
        lines.append("【整个故事的轨迹（机器摘要，供参考）】")
        lines.extend(str(x) for x in ctx["trajectory"])
    lines.append("")
    lines.append("【读者的问题】" + question.strip())
    text = "\n".join(lines)
    if len(text) > MAX_CONTEXT_CHARS:
        text = text[:MAX_CONTEXT_CHARS - 1] + "…"
    return text


# ============================================================ answerers

class ClaudeCliAnswerer:
    """Runs `claude -p` once per question; streams text deltas to `on_delta`."""

    def __init__(self, model: str = DEFAULT_MODEL, cli: str | None = None, workdir: Path | None = None):
        self.model = model
        self.cli = cli or find_claude_cli()
        self.workdir = workdir or Path(tempfile.mkdtemp(prefix="ar-narrative-serve-"))

    def available(self) -> bool:
        return bool(self.cli)

    def __call__(self, prompt: str, on_delta) -> str:
        if not self.cli:
            raise RuntimeError("claude CLI not found (PATH or VS Code extension bundle)")
        env = dict(os.environ)
        env.pop("CLAUDECODE", None)          # nested print-mode session is allowed
        env.pop("CLAUDE_CODE_ENTRYPOINT", None)
        cmd = [self.cli, "-p", "--model", self.model, "--output-format", "stream-json",
               "--verbose", "--include-partial-messages", "--max-turns", "1",
               "--disallowedTools", DISALLOWED_TOOLS]
        proc = subprocess.Popen(cmd, cwd=str(self.workdir), env=env, stdin=subprocess.PIPE,
                                stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        assert proc.stdin and proc.stdout
        proc.stdin.write(prompt.encode("utf-8"))
        proc.stdin.close()
        streamed, final = [], None
        for raw in proc.stdout:
            line = raw.decode("utf-8", "replace").strip()
            if not line.startswith("{"):
                continue
            try:
                ev = json.loads(line)
            except ValueError:
                continue
            if ev.get("type") == "stream_event":
                inner = ev.get("event") or {}
                if inner.get("type") == "content_block_delta":
                    delta = inner.get("delta") or {}
                    if delta.get("type") == "text_delta" and delta.get("text"):
                        streamed.append(delta["text"])
                        on_delta(delta["text"])
            elif ev.get("type") == "result":
                if isinstance(ev.get("result"), str):
                    final = ev["result"]
        proc.wait(timeout=30)
        err = proc.stderr.read().decode("utf-8", "replace") if proc.stderr else ""
        text = final if final is not None else "".join(streamed)
        if not text.strip():
            raise RuntimeError("empty answer from the CLI" + ((": " + err.strip()[:300]) if err.strip() else ""))
        if not streamed:
            on_delta(text)
        return text


class FakeAnswerer:
    """Deterministic answerer for the self-test: echoes the question title-first."""

    def __init__(self, reply: str = "（测试回答）"):
        self.reply = reply
        self.prompts: list = []

    def available(self) -> bool:
        return True

    def __call__(self, prompt: str, on_delta) -> str:
        self.prompts.append(prompt)
        for piece in (self.reply[:4], self.reply[4:]):
            if piece:
                on_delta(piece)
        return self.reply


# ============================================================ annotation write

def add_qa_annotation(control: Path, ref: str, node_id: str, question: str, answer: str,
                      by: str = ANSWER_BY) -> dict:
    """Same record `narrative annotate add --kind qa` writes, without the stdout."""
    commit_id = nar.resolve_commitish(control, ref)
    snapshot = nar.commit_snapshot(control, commit_id)
    if node_id not in snapshot["nodes"]:
        raise nar.NarrativeError("NODE_NOT_FOUND", f"{node_id} not in {commit_id[:12]}")
    annotation_id = "a" + uuid.uuid4().hex[:10]
    path = nar.ndir(control) / "annotations" / node_id / f"{annotation_id}.json"
    body = {"schema": "auto-research/narrative-annotation-v1", "annotation_id": annotation_id,
            "node_id": node_id, "commit_id": commit_id, "kind": "qa",
            "question": question, "answer": answer, "adjudicated": True,
            "by": by, "at": nar.now_iso(), "supersedes": None}
    with nar.narrative_lock(control):
        nar.run_transaction(control, f"annotate-{annotation_id}",
                            [{"path": path, "text": nar.dump_json(body)}], note="live qa")
    return body


# ============================================================ HTTP

class NarrativeServer(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = True

    def __init__(self, address, control: Path, ref: str, answerer, model_label: str):
        super().__init__(address, Handler)
        self.control = control
        self.ref = ref
        self.answerer = answerer
        self.model_label = model_label
        self.answered = 0
        # In-flight questions, so a page that reloads mid-answer still sees the
        # question and the partial text: {node_id: [{id, question, started_at, partial, error?}]}
        self.inflight: dict = {}
        self.inflight_lock = threading.Lock()

    def track(self, node_id: str, question: str) -> dict:
        item = {"id": "q" + uuid.uuid4().hex[:8], "question": question,
                "started_at": nar.now_iso(), "partial": "", "error": None, "expires": None}
        with self.inflight_lock:
            self.inflight.setdefault(node_id, []).append(item)
        return item

    def untrack(self, node_id: str, item: dict, error: str | None = None) -> None:
        import time
        with self.inflight_lock:
            if error:                      # keep a failed ask visible for two minutes
                item["error"] = error
                item["expires"] = time.time() + 120
            else:
                rows = self.inflight.get(node_id) or []
                if item in rows:
                    rows.remove(item)
                if not rows:
                    self.inflight.pop(node_id, None)

    def pending_for(self, node_id: str) -> list:
        import time
        now = time.time()
        with self.inflight_lock:
            rows = [r for r in (self.inflight.get(node_id) or []) if not r["expires"] or r["expires"] > now]
            if rows:
                self.inflight[node_id] = rows
            else:
                self.inflight.pop(node_id, None)
            return [{"id": r["id"], "question": r["question"], "started_at": r["started_at"],
                     "partial": r["partial"], "error": r["error"]} for r in rows]


class Handler(BaseHTTPRequestHandler):
    server: NarrativeServer

    def log_message(self, fmt, *args):  # quiet by default; the CLI prints the URL once
        if os.environ.get("AR_SERVE_LOG"):
            super().log_message(fmt, *args)

    def _json(self, status: int, body: dict) -> None:
        data = json.dumps(body, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self):
        route = self.path.split("?", 1)[0]
        if route in ("/", "/index.html"):
            try:
                payload = page.build_payload(self.server.control, ref=self.server.ref)
                payload["live"] = {"model": self.server.model_label}
                html = page.render_html(payload).encode("utf-8")
            except Exception as exc:  # the page must say why, not 500 silently
                return self._json(500, {"ok": False, "error": f"{type(exc).__name__}: {exc}"})
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.send_header("Content-Length", str(len(html)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(html)
            return
        if route == "/api/ping":
            return self._json(200, {"ok": True, "live": True, "model": self.server.model_label,
                                    "ref": self.server.ref, "answered": self.server.answered})
        if route.startswith("/api/qa/"):
            node_id = urllib.parse.unquote(route[len("/api/qa/"):])
            anns = page.collect_annotations(self.server.control).get(node_id, [])
            return self._json(200, {"ok": True, "node_id": node_id, "annotations": anns,
                                    "pending": self.server.pending_for(node_id)})
        return self._json(404, {"ok": False, "error": "not found"})

    def do_POST(self):
        route = self.path.split("?", 1)[0]
        if route != "/api/ask":
            return self._json(404, {"ok": False, "error": "not found"})
        try:
            length = int(self.headers.get("Content-Length") or 0)
            body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        except (ValueError, UnicodeDecodeError):
            return self._json(400, {"ok": False, "error": "bad json"})
        node_id = str(body.get("node_id") or "")
        question = str(body.get("question") or "").strip()
        if not node_id or not question:
            return self._json(400, {"ok": False, "error": "node_id and question are required"})
        if len(question) > MAX_QUESTION:
            return self._json(400, {"ok": False, "error": "question too long"})
        try:
            ctx = node_context(self.server.control, self.server.ref, node_id)
        except nar.NarrativeError as exc:
            return self._json(404, {"ok": False, "error": exc.code, "detail": exc.detail})
        prompt = build_prompt(ctx, question)

        self.send_response(200)
        self.send_header("Content-Type", "application/x-ndjson; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("X-Accel-Buffering", "no")
        self.end_headers()
        lock = threading.Lock()

        def emit(obj: dict) -> None:
            data = (json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8")
            with lock:
                try:
                    self.wfile.write(data)
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError, OSError):
                    pass

        item = self.server.track(node_id, question)
        emit({"start": True, "title": ctx["title"], "model": self.server.model_label, "pending_id": item["id"]})

        def on_delta(t: str) -> None:
            item["partial"] += t
            emit({"delta": t})
        try:
            answer = self.server.answerer(prompt, on_delta)
        except Exception as exc:
            self.server.untrack(node_id, item, error=f"{type(exc).__name__}: {exc}")
            emit({"error": f"{type(exc).__name__}: {exc}"})
            return
        try:
            record = add_qa_annotation(self.server.control, self.server.ref, node_id, question, answer)
        except Exception as exc:
            self.server.untrack(node_id, item, error=f"answered, but the record could not be written: {exc}")
            emit({"error": f"answered, but the annotation could not be written: {exc}", "answer": answer})
            return
        self.server.untrack(node_id, item)
        self.server.answered += 1
        emit({"done": True, "annotation_id": record["annotation_id"], "at": record["at"],
              "by": record["by"], "answer": answer})


def serve(control: Path, ref: str = "main", port: int = 0, model: str = DEFAULT_MODEL,
          open_browser: bool = True, answerer=None, block: bool = True):
    control = Path(control)
    nar.require_store(control)
    nar.load_head_snapshot(control, ref)   # fail early on a bad ref
    if answerer is None:
        answerer = ClaudeCliAnswerer(model=model)
        if not answerer.available():
            ros.die("claude CLI not found: put `claude` on PATH or install the VS Code extension", 2)
    label = model if not isinstance(answerer, FakeAnswerer) else "fake"
    httpd = NarrativeServer(("127.0.0.1", port), control, ref, answerer, label)
    url = "http://127.0.0.1:%d/" % httpd.server_address[1]
    receipt = {"ok": True, "url": url, "ref": ref, "model": label,
               "project": control.parent.name, "pid": os.getpid()}
    if not block:
        thread = threading.Thread(target=httpd.serve_forever, daemon=True)
        thread.start()
        return httpd, url
    print(json.dumps(receipt, ensure_ascii=False), flush=True)
    if open_browser:
        try:
            webbrowser.open(url)
        except Exception:
            pass
    try:
        httpd.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        httpd.server_close()
    return None


# ============================================================ self-test

_OPENER = urllib.request.build_opener(urllib.request.ProxyHandler({}))  # never via HTTP_PROXY


def _http(url: str, data: bytes | None = None) -> tuple:
    req = urllib.request.Request(url, data=data,
                                 headers={"Content-Type": "application/json"} if data else {})
    with _OPENER.open(req, timeout=20) as resp:
        return resp.status, resp.read().decode("utf-8")


def self_test() -> int:
    checks = []

    def check(name, ok, detail=""):
        checks.append({"check": name, "ok": bool(ok), "detail": detail})

    tmp = Path(tempfile.mkdtemp(prefix="ar-serve-"))
    httpd = None
    try:
        control = tmp / "proj" / ".research-os"
        control.mkdir(parents=True)
        buffer, saved = io.StringIO(), sys.stdout
        sys.stdout = buffer
        try:
            nar.cmd_init(argparse.Namespace(
                root=control.parent, root_id="N-0", title="临时项目", thesis="临时论点",
                summary="", venue="RFS", message=None, session="W-s", at="2026-08-26T00:00:00Z",
                turn_uuid=None, manifest_id=None))
        finally:
            sys.stdout = saved

        def claim(nid, title, statement):
            return {"id": nid, "node_type": "claim",
                    "identity_key": {"subject": nid, "target": "returns", "predicate": "p-" + nid,
                                     "quantifier": "all", "domain": "US equities", "conditions": [],
                                     "polarity": "positive"},
                    "title": title, "summary": title + " 的总结", "statement": statement,
                    "state": {"epistemic": "PENDING", "narrative": "ACTIVE"}}
        nar.commit_from_ops(control, "main", [
            {"op": "add_node", "node": claim("C-001", "动机", "现有研究假定附录无人读。"), "parent": "N-0"},
            {"op": "add_node", "node": claim("C-002", "机制", "阅读量取决于审稿流程。"), "parent": "C-001"},
        ], {"message": "seed", "at": "2026-08-26T01:00:00Z",
            "author": {"session": "W-s", "role": "main"}, "origin": "live",
            "approval_refs": {"overthrow": None, "forced_cause": None}})

        ctx = node_context(control, "main", "C-002")
        check("context carries path / title / statement / siblings",
              ctx["path"] == ["动机"] and ctx["title"] == "机制" and "流程" in ctx["statement"])
        prompt = build_prompt(ctx, "为什么取决于流程？")
        check("prompt is Chinese, title-based, id-free",
              "史官" in prompt and "【这一段的标题】机制" in prompt and "C-002" not in prompt
              and "【读者的问题】为什么取决于流程？" in prompt)
        check("prompt forbids invention and ids",
              "叙事里没有记载" in prompt and "不要出现任何内部编号" in prompt)

        fake = FakeAnswerer("因为阅读量随流程变化。")
        httpd, url = serve(control, "main", port=0, answerer=fake, open_browser=False, block=False)
        status, text = _http(url)
        check("GET / serves the page fresh", status == 200 and text.startswith("<!doctype html>")
              and '"live":{"model":"fake"}' in text.replace(" ", ""))
        status, text = _http(url + "api/ping")
        check("ping says live", status == 200 and json.loads(text)["live"] is True)
        status, text = _http(url + "api/ask",
                             json.dumps({"node_id": "C-002", "question": "为什么取决于流程？"}).encode("utf-8"))
        rows = [json.loads(x) for x in text.strip().split("\n")]
        deltas = "".join(r.get("delta", "") for r in rows)
        done = rows[-1]
        check("ask streams deltas then done", status == 200 and rows[0].get("start")
              and deltas == "因为阅读量随流程变化。" and done.get("done") and done.get("annotation_id"))
        anns = page.collect_annotations(control).get("C-002", [])
        check("answer persisted as a qa annotation by chronicler-live",
              len(anns) == 1 and anns[0]["kind"] == "qa" and anns[0]["by"] == ANSWER_BY
              and anns[0]["answer"] == "因为阅读量随流程变化。")
        status, text = _http(url)
        check("next page load already carries the answer", "因为阅读量随流程变化。" in text)
        status, text = _http(url + "api/qa/C-002")
        qa = json.loads(text)
        check("GET /api/qa/<node> lists the node's Q&A and no pending", status == 200
              and len(qa["annotations"]) == 1 and qa["pending"] == [])
        slow = FakeAnswerer("慢答案。")
        gate = threading.Event()
        orig = slow.__call__

        def slow_call(prompt, on_delta):
            on_delta("慢")
            gate.wait(5)
            on_delta("答案。")
            return "慢答案。"
        httpd.answerer = slow_call
        results = {}

        def ask_bg():
            try:
                results["r"] = _http(url + "api/ask",
                                     json.dumps({"node_id": "C-002", "question": "第二问"}).encode("utf-8"))
            except Exception as exc:  # pragma: no cover
                results["r"] = ("err", str(exc))
        t = threading.Thread(target=ask_bg, daemon=True)
        t.start()
        import time as _time
        for _ in range(40):
            _time.sleep(0.05)
            pend = json.loads(_http(url + "api/qa/C-002")[1])["pending"]
            if pend and pend[0]["partial"]:
                break
        check("a reload mid-answer sees the question and its partial text",
              pend and pend[0]["question"] == "第二问" and pend[0]["partial"] == "慢")
        gate.set()
        t.join(10)
        qa2 = json.loads(_http(url + "api/qa/C-002")[1])
        check("after the answer lands, pending is empty and the record exists",
              qa2["pending"] == [] and len(qa2["annotations"]) == 2
              and any(a["answer"] == "慢答案。" for a in qa2["annotations"]))
        httpd.answerer = fake
        ctx2 = node_context(control, "main", "C-002")
        check("prior Q&A feeds the next prompt", ctx2["qa"] and "为什么取决于流程" in build_prompt(ctx2, "再问"))
        try:
            _http(url + "api/ask", json.dumps({"node_id": "C-404", "question": "x"}).encode("utf-8"))
            check("unknown node refused", False)
        except urllib.error.HTTPError as exc:
            check("unknown node refused", exc.code == 404)
        try:
            _http(url + "api/ask", json.dumps({"node_id": "C-002", "question": ""}).encode("utf-8"))
            check("empty question refused", False)
        except urllib.error.HTTPError as exc:
            check("empty question refused", exc.code == 400)
        check("loopback only", httpd.server_address[0] == "127.0.0.1")
        check("fake answerer saw the full brief", len(fake.prompts) == 1 and "【正文】" in fake.prompts[0])
        cli = find_claude_cli()
        check("claude CLI discoverable (informational)", True, cli or "not found on this machine")
    except Exception as exc:  # pragma: no cover - environment failure
        check("self-test ran to the end", False, "%s: %s" % (type(exc).__name__, exc))
    finally:
        if httpd is not None:
            httpd.shutdown()
            httpd.server_close()
        shutil.rmtree(tmp, ignore_errors=True)

    failed = [c for c in checks if not c["ok"]]
    print(json.dumps({"ok": not failed, "checks": len(checks), "failed": len(failed),
                      "rows": checks if failed else [c["check"] for c in checks]},
                     ensure_ascii=False, indent=2))
    return 1 if failed else 0


# ============================================================ CLI

def main(argv=None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    parser = argparse.ArgumentParser(prog="narrative_serve.py",
                                     description="live narrative-tree page with in-place Q&A")
    parser.add_argument("--project", help="project root, .research-os dir, or state.json")
    parser.add_argument("--ref", default="main")
    parser.add_argument("--port", type=int, default=0, help="0 = pick a free port")
    parser.add_argument("--model", default=None, help="CLI model id (default: narrative config "
                                                      "`live_qa_model`, else %s)" % DEFAULT_MODEL)
    parser.add_argument("--no-open", dest="no_open", action="store_true")
    parser.add_argument("--self-test", dest="self_test", action="store_true")
    args = parser.parse_args(argv)
    if args.self_test:
        return self_test()
    if not args.project:
        ros.die("--project is required (or --self-test)", 2)
    control = ros.locate_control(Path(args.project))
    model = args.model
    if not model:
        try:
            model = nar.load_config(control).get("live_qa_model") or DEFAULT_MODEL
        except Exception:
            model = DEFAULT_MODEL
    try:
        serve(control, args.ref, port=args.port, model=model, open_browser=not args.no_open)
    except nar.NarrativeError as exc:
        print(json.dumps({"ok": False, "error": exc.code, "detail": exc.detail}, ensure_ascii=False),
              file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
