"""A fake OpenAI-compatible model endpoint, for end-to-end tests without a key.

The unit tests swap the completer for a script and never touch HTTP. That
leaves the parts a real run depends on untested: provider resolution from
models.yaml, the wire format, structured-output negotiation, the retry loop
reacting to real status codes, the server's job thread and the startup check.
This endpoint speaks just enough of /chat/completions to drive all of that,
and can misbehave on purpose in the ways real providers do.

It recognises four kinds of request by their system prompt:

  preflight  "Reply with JSON only." -> {"ok": true}
  annotate   the picture editor's prompt -> one annotation per `id: ...` line
  plan       anything else, first message without "CURRENT LAYOUT" -> LAYOUT
  revise     a user message containing "CURRENT LAYOUT" -> LAYOUT + a summary

Faults are consumed one per plan/revise request, in order, then it behaves:

  503            HTTP 503 with an error body (transient)
  error_body     HTTP 200 whose body is {"error": ...} (OpenRouter does this)
  empty_schema   empty content while response_format is json_schema; once the
                 client steps down to json_object it answers (the bug that
                 started all this: Sonnet via OpenRouter, json_mode on)
  tool_calls     the layout delivered as tool-call arguments, content empty
  length         empty content, finish_reason=length, reasoning tokens spent
  refusal        message.refusal set, content empty

A model whose name contains "missing" answers 404, like a typo'd model id; one
whose name contains "down" always answers 503.

Run it by hand to try the editor with no key and no cost:

    python phase1/tests/fake_model.py --port 8911 \\
        --layout layout.json --faults empty_schema,tool_calls
    # models.yaml:  default: {base_url: "http://127.0.0.1:8911/v1", model: fake/model, json_mode: true}
"""

from __future__ import annotations

import argparse
import json
import re
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

PREFLIGHT = "Reply with JSON only."
ANNOTATE = "You are a picture editor"


def _text_of(content) -> str:
    if isinstance(content, str):
        return content
    return "\n".join(p.get("text", "") for p in content or [] if isinstance(p, dict))


class FakeModel:
    def __init__(self, layout: dict, faults: list[str] | None = None,
                 revise_summary: str = "spread 4 lightened"):
        self.layout = layout
        self.faults = list(faults or [])
        self.revise_summary = revise_summary
        self.requests: list[dict] = []   # what each request looked like, for assertions
        self.lock = threading.Lock()
        self.server: ThreadingHTTPServer | None = None

    # --- lifecycle ---------------------------------------------------------

    def start(self, port: int = 0) -> str:
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *a):  # keep test output readable
                pass

            def do_POST(self):
                n = int(self.headers.get("Content-Length") or 0)
                body = json.loads(self.rfile.read(n) or b"{}")
                code, payload = fake.answer(body, self.headers.get("Authorization"))
                out = json.dumps(payload).encode()
                self.send_response(code)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(out)))
                self.end_headers()
                self.wfile.write(out)

        self.server = ThreadingHTTPServer(("127.0.0.1", port), Handler)
        threading.Thread(target=self.server.serve_forever, daemon=True).start()
        return f"http://127.0.0.1:{self.server.server_address[1]}/v1"

    def stop(self) -> None:
        if self.server:
            self.server.shutdown()
            self.server.server_close()

    def set_faults(self, faults: list[str]) -> None:
        with self.lock:
            self.faults = list(faults)

    def of_kind(self, kind: str) -> list[dict]:
        return [r for r in self.requests if r["kind"] == kind]

    # --- behaviour ---------------------------------------------------------

    def answer(self, body: dict, auth: str | None) -> tuple[int, dict]:
        msgs = body.get("messages") or []
        system = _text_of(msgs[0]["content"]) if msgs and msgs[0]["role"] == "system" else ""
        users = [_text_of(m["content"]) for m in msgs if m["role"] == "user"]
        rf = (body.get("response_format") or {}).get("type")
        model = body.get("model", "")

        if system.startswith(PREFLIGHT):
            kind = "preflight"
        elif system.startswith(ANNOTATE):
            kind = "annotate"
        elif users and "CURRENT LAYOUT" in users[0]:
            kind = "revise"
        else:
            kind = "plan"

        fault = None
        with self.lock:
            if kind in ("plan", "revise") and self.faults:
                fault = self.faults.pop(0)
            self.requests.append({
                "kind": kind, "model": model, "fault": fault, "response_format": rf,
                "max_tokens": body.get("max_tokens"),
                "reasoning": body.get("reasoning") or body.get("reasoning_effort"),
                "auth": auth, "turns": len(msgs),
            })

        if "missing" in model:
            return 404, {"error": {"message": f"model {model} not found", "code": 404}}
        if "down" in model:
            return 503, {"error": {"message": "upstream overloaded"}}

        if kind == "preflight":
            return 200, self._reply('{"ok": true}')
        if kind == "annotate":
            ids = re.findall(r"^id: (\S+)$", "\n".join(users), re.M)
            return 200, self._reply(json.dumps([
                {"id": i, "subject": "placeholder", "people": 0, "who": "", "scene": "test",
                 "focal": [0.5, 0.5], "strength": 3} for i in ids]))

        layout = dict(self.layout)
        if kind == "revise":
            layout["change_summary"] = self.revise_summary
        text = json.dumps(layout)

        if fault == "503":
            return 503, {"error": {"message": "upstream overloaded"}}
        if fault == "error_body":
            return 200, {"error": {"message": "provider returned error", "code": 502}}
        if fault == "empty_schema":
            if rf == "json_schema":
                return 200, self._reply("")
            # The client already stepped down: nothing to provoke any more.
        if fault == "tool_calls":
            return 200, self._reply(None, finish="tool_calls", tool_calls=[{
                "id": "call_1", "type": "function",
                "function": {"name": "album_layout", "arguments": text}}])
        if fault == "length":
            return 200, self._reply(None, finish="length", reasoning="thinking " * 500,
                                    reasoning_tokens=body.get("max_tokens") or 16000)
        if fault == "refusal":
            return 200, self._reply(None, refusal="I can't help with that.")
        return 200, self._reply(text)

    @staticmethod
    def _reply(content, finish="stop", reasoning_tokens=0, **message) -> dict:
        return {
            "id": "fake", "object": "chat.completion",
            "choices": [{"index": 0, "finish_reason": finish,
                         "message": {"role": "assistant", "content": content, **message}}],
            "usage": {"prompt_tokens": 100, "completion_tokens": 50,
                      "completion_tokens_details": {"reasoning_tokens": reasoning_tokens}},
        }


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__,
                                 formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--port", type=int, default=8911)
    ap.add_argument("--layout", required=True, help="layout JSON the fake returns")
    ap.add_argument("--faults", default="", help="comma-separated, consumed in order")
    args = ap.parse_args()
    with open(args.layout) as f:
        layout = json.load(f)
    fake = FakeModel(layout, [x for x in args.faults.split(",") if x])
    url = fake.start(args.port)
    print(f"fake model at {url} — Ctrl-C to stop")
    try:
        threading.Event().wait()
    except KeyboardInterrupt:
        fake.stop()


if __name__ == "__main__":
    main()
