#!/usr/bin/env python3
"""Local trace viewer server for claude_proxy reports."""

from __future__ import annotations

import argparse
import json
import mimetypes
from datetime import datetime, timezone
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, unquote, urlparse


ROOT = Path(__file__).resolve().parent
DEFAULT_REPORTS = ROOT.parent / "reports"
STATIC_DIR = ROOT / "static"
REPORTS_DIR = DEFAULT_REPORTS


def json_response(handler: BaseHTTPRequestHandler, payload: object, status: int = 200) -> None:
    raw = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    handler.send_response(status)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("Content-Length", str(len(raw)))
    handler.end_headers()
    handler.wfile.write(raw)


def read_report(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        return {"_read_error": str(exc)}
    return value if isinstance(value, dict) else {"_read_error": "report root is not an object"}


def response_body(report: dict) -> dict:
    value = report.get("response", {}).get("body")
    return value if isinstance(value, dict) else {}


def usage_summary(report: dict) -> dict:
    usage = response_body(report).get("usage", {})
    if not isinstance(usage, dict):
        return {}
    names = {
        "input": ("input_tokens", "prompt_tokens"),
        "output": ("output_tokens", "completion_tokens"),
        "cacheRead": ("cache_read_input_tokens", "cached_tokens"),
        "cacheWrite": ("cache_creation_input_tokens",),
    }
    result = {}
    for key, candidates in names.items():
        for candidate in candidates:
            value = usage.get(candidate)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                result[key] = int(value)
                break
    return result


def block_count(value: object) -> int:
    if isinstance(value, list):
        return len(value)
    return 1 if value else 0


def make_item(path: Path) -> dict:
    report = read_report(path)
    relative = path.relative_to(REPORTS_DIR).as_posix()
    parts = relative.split("/")
    session = parts[0] if parts else "unknown-session"
    request = report.get("forwarded_request", {})
    request = request if isinstance(request, dict) else {}
    request_headers = request.get("headers", {})
    request_headers = request_headers if isinstance(request_headers, dict) else {}
    agent_id = request_headers.get("x-claude-code-agent-id") or (parts[-2] if len(parts) >= 3 else None)
    request_body = request.get("body")
    request_body = request_body if isinstance(request_body, dict) else {}
    response = report.get("response", {})
    response = response if isinstance(response, dict) else {}
    content = response_body(report).get("content", [])
    stat = path.stat()
    return {
        "id": relative,
        "session": session,
        "sequence": parts[-1].removesuffix(".json") if parts else "?",
        "mtime": stat.st_mtime,
        "timestamp": datetime.fromtimestamp(stat.st_mtime, timezone.utc).isoformat(),
        "agentId": agent_id,
        "isSubagent": bool(agent_id),
        "parentSession": session if agent_id else None,
        "method": request.get("method", "?"),
        "url": request.get("url", ""),
        "path": urlparse(str(request.get("url", ""))).path or "/",
        "status": response.get("status_code"),
        "model": request_body.get("model", ""),
        "stream": bool(request_body.get("stream")),
        "messageCount": len(request_body.get("messages", [])) if isinstance(request_body.get("messages"), list) else 0,
        "toolCount": len(request_body.get("tools", [])) if isinstance(request_body.get("tools"), list) else 0,
        "responseBlocks": block_count(content),
        "usage": usage_summary(report),
        "size": stat.st_size,
        "error": report.get("_read_error"),
    }


def report_index() -> list[dict]:
    REPORTS_DIR.mkdir(parents=True, exist_ok=True)
    items = []
    for path in REPORTS_DIR.rglob("*.json"):
        try:
            items.append(make_item(path))
        except OSError:
            continue
    return sorted(items, key=lambda item: (item["mtime"], item["id"]), reverse=True)


def safe_report_path(report_id: str) -> Path | None:
    candidate = (REPORTS_DIR / unquote(report_id)).resolve()
    try:
        candidate.relative_to(REPORTS_DIR.resolve())
    except ValueError:
        return None
    return candidate if candidate.is_file() and candidate.suffix == ".json" else None


class Handler(BaseHTTPRequestHandler):
    server_version = "TraceViewer/1.0"

    def do_GET(self) -> None:
        parsed = urlparse(self.path)
        if parsed.path == "/api/healthz":
            json_response(self, {"status": "ok", "reports": str(REPORTS_DIR)})
            return
        if parsed.path == "/api/traces":
            json_response(self, {"items": report_index(), "reports": str(REPORTS_DIR)})
            return
        if parsed.path == "/api/trace":
            report_id = parse_qs(parsed.query).get("id", [""])[0]
            path = safe_report_path(report_id)
            if path is None:
                json_response(self, {"error": "trace not found"}, 404)
                return
            payload = read_report(path)
            payload["_meta"] = next((item for item in report_index() if item["id"] == report_id), {})
            json_response(self, payload)
            return
        self.serve_static(parsed.path)

    def serve_static(self, path: str) -> None:
        relative = "index.html" if path in {"/", ""} else unquote(path.lstrip("/"))
        candidate = (STATIC_DIR / relative).resolve()
        try:
            candidate.relative_to(STATIC_DIR.resolve())
        except ValueError:
            self.send_error(404)
            return
        if not candidate.is_file():
            self.send_error(404)
            return
        body = candidate.read_bytes()
        content_type = mimetypes.guess_type(candidate.name)[0] or "application/octet-stream"
        if candidate.suffix == ".js":
            content_type = "text/javascript"
        self.send_response(200)
        self.send_header("Content-Type", f"{content_type}; charset=utf-8")
        self.send_header("Cache-Control", "no-store")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, format: str, *args: object) -> None:
        print(f"[trace-viewer] {self.address_string()} - {format % args}")


def main() -> None:
    global REPORTS_DIR
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1")
    parser.add_argument("--port", type=int, default=30013)
    parser.add_argument("--reports", type=Path, default=DEFAULT_REPORTS)
    args = parser.parse_args()
    REPORTS_DIR = args.reports.expanduser().resolve()
    server = ThreadingHTTPServer((args.host, args.port), Handler)
    print(f"Trace Viewer: http://{args.host}:{args.port}")
    print(f"Reports: {REPORTS_DIR}")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        server.server_close()


if __name__ == "__main__":
    main()

