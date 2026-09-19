"""Line-by-line execution tracer for learning/debugging (like an IDE debugger).

Only active when ASSISTANT_DEBUG=true (local .env). It records every line of
this project's own code (app/*.py) that runs while one request is handled,
so the chat window can replay the flow file -> file, line -> line.
"""
import linecache
import os
import sys
from functools import wraps
from typing import Any, Dict, List

from flask import jsonify

APP_DIR = os.path.dirname(os.path.abspath(__file__))
MAX_EVENTS = 400


def enabled() -> bool:
    return os.getenv("ASSISTANT_DEBUG", "").lower() == "true"


class CodeTracer:
    def __init__(self) -> None:
        self.events: List[Dict[str, Any]] = []
        self.depth = 0
        self.truncated = False

    def _mine(self, frame) -> bool:
        path = os.path.abspath(frame.f_code.co_filename)
        return path.startswith(APP_DIR) and os.path.basename(path) != "debug_trace.py"

    def _add(self, kind: str, frame, code: str = "") -> None:
        if len(self.events) >= MAX_EVENTS:
            self.truncated = True
            return
        self.events.append({
            "type": kind,
            "file": os.path.basename(frame.f_code.co_filename),
            "line": frame.f_lineno,
            "func": frame.f_code.co_name,
            "depth": self.depth,
            "code": code,
        })

    def global_trace(self, frame, event, arg):
        if event != "call" or not self._mine(frame):
            return None
        self._add("call", frame)
        self.depth += 1
        return self.local_trace

    def local_trace(self, frame, event, arg):
        if event == "line":
            src = linecache.getline(frame.f_code.co_filename, frame.f_lineno).strip()
            self._add("line", frame, src[:180])
        elif event == "return":
            self.depth = max(self.depth - 1, 0)
            self._add("return", frame)
        return self.local_trace


def trace_request(fn):
    """Route decorator: in debug mode, attach `codeTrace` to the JSON reply."""
    @wraps(fn)
    def wrapper(*args, **kwargs):
        if not enabled():
            return fn(*args, **kwargs)
        tracer = CodeTracer()
        sys.settrace(tracer.global_trace)
        try:
            result = fn(*args, **kwargs)
        finally:
            sys.settrace(None)
        response, status = result if isinstance(result, tuple) else (result, 200)
        data = response.get_json(silent=True)
        if isinstance(data, dict):
            data["codeTrace"] = tracer.events
            data["codeTraceTruncated"] = tracer.truncated
            return jsonify(data), status
        return result
    return wrapper
