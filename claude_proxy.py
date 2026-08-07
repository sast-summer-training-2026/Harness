#!/usr/bin/env python3
"""Small Anthropic passthrough proxy for Claude Code -> vLLM.

vLLM's /v1/messages endpoint validates that request.messages contains only
user/assistant roles. Claude Code can occasionally include system-role entries
inside messages during long tool/background-task loops. This proxy follows the
same broad idea as LiteLLM's Anthropic adapter: keep system text as system
prompt material, but make sure messages[] only contains user/assistant before
forwarding to vLLM.
"""

from __future__ import annotations

import argparse
import asyncio
import base64
import hashlib
import json
import logging
import re
from pathlib import Path
from typing import Any, AsyncIterator

import httpx
from fastapi import FastAPI, Request, Response
from fastapi.responses import JSONResponse, StreamingResponse

HOP_BY_HOP_HEADERS = {
    "connection",
    "keep-alive",
    "proxy-authenticate",
    "proxy-authorization",
    "te",
    "trailer",
    "transfer-encoding",
    "upgrade",
    "content-length",
    "content-encoding",
}
SENSITIVE_REPORT_HEADERS = {
    "authorization",
    "proxy-authorization",
    "x-api-key",
    "api-key",
}

logger = logging.getLogger("anthropic_vllm_proxy")
app = FastAPI(title="Anthropic vLLM cleanup proxy")

UPSTREAM = "http://127.0.0.1:30010"
TIMEOUT = httpx.Timeout(connect=10.0, read=None, write=60.0, pool=10.0)
DIRECT = False
REPORT_DIR: Path | None = None
REPORT_SEQUENCES: dict[Path, int] = {}
REPORT_SEQUENCE_LOCK = asyncio.Lock()


def _create_report_dir() -> Path:
    report_dir = Path(__file__).resolve().parent / "reports"
    report_dir.mkdir(parents=True, exist_ok=True)
    return report_dir


def _safe_report_component(value: str, fallback: str) -> str:
    value = value.strip()
    if not value:
        return fallback
    safe = re.sub(r"[^A-Za-z0-9._-]", "_", value)
    if safe in {".", ".."}:
        safe = fallback
    if safe != value or len(safe) > 180:
        digest = hashlib.sha256(value.encode("utf-8")).hexdigest()[:12]
        safe = f"{safe[:160]}-{digest}"
    return safe


async def _allocate_report_path(request: Request) -> Path | None:
    if REPORT_DIR is None:
        return None
    session_id = _safe_report_component(
        request.headers.get("x-claude-code-session-id", ""),
        "unknown-session",
    )
    report_dir = REPORT_DIR / session_id
    agent_id = request.headers.get("x-claude-code-agent-id")
    if agent_id:
        report_dir /= _safe_report_component(agent_id, "unknown-agent")
    async with REPORT_SEQUENCE_LOCK:
        report_dir.mkdir(parents=True, exist_ok=True)
        sequence = REPORT_SEQUENCES.get(report_dir)
        if sequence is None:
            sequence = max(
                (
                    int(path.stem)
                    for path in report_dir.glob("*.json")
                    if path.stem.isdigit()
                ),
                default=0,
            )
        sequence += 1
        REPORT_SEQUENCES[report_dir] = sequence
        return report_dir / f"{sequence}.json"


def _parse_sse_events(text: str) -> list[dict[str, Any]]:
    events: list[dict[str, Any]] = []
    event_name = "message"
    data_lines: list[str] = []

    def append_event() -> None:
        nonlocal event_name, data_lines
        if not data_lines:
            event_name = "message"
            return
        data_text = "\n".join(data_lines)
        try:
            data: Any = json.loads(data_text)
        except json.JSONDecodeError:
            data = data_text
        events.append({"event": event_name, "data": data})
        event_name = "message"
        data_lines = []

    for line in text.splitlines():
        if not line:
            append_event()
        elif line.startswith(":"):
            continue
        else:
            field, separator, value = line.partition(":")
            if separator and value.startswith(" "):
                value = value[1:]
            if field == "event":
                event_name = value
            elif field == "data":
                data_lines.append(value)

    append_event()
    return events


def _merge_content_block_delta(block: dict[str, Any], delta: dict[str, Any]) -> None:
    delta_type = delta.get("type")
    if delta_type == "input_json_delta":
        block["_partial_json"] = block.get("_partial_json", "") + str(
            delta.get("partial_json", "")
        )
        return

    field_by_type = {
        "text_delta": "text",
        "thinking_delta": "thinking",
        "signature_delta": "signature",
    }
    field = field_by_type.get(str(delta_type))
    if field is not None:
        block[field] = str(block.get(field, "")) + str(delta.get(field, ""))
        return

    for key, value in delta.items():
        if key == "type":
            continue
        if isinstance(value, str) and isinstance(block.get(key), str):
            block[key] += value
        elif key == "citation":
            block.setdefault("citations", []).append(value)
        else:
            block[key] = value


def _joined_sse_response(text: str) -> Any:
    events = _parse_sse_events(text)
    message: dict[str, Any] | None = None
    content_blocks: dict[int, dict[str, Any]] = {}
    error: Any = None

    for event in events:
        data = event.get("data")
        if not isinstance(data, dict):
            continue
        event_type = data.get("type") or event.get("event")
        if event_type == "message_start" and isinstance(data.get("message"), dict):
            message = dict(data["message"])
            content_blocks = {
                index: dict(block)
                for index, block in enumerate(message.get("content", []))
                if isinstance(block, dict)
            }
        elif event_type == "content_block_start":
            index = data.get("index")
            block = data.get("content_block")
            if isinstance(index, int) and isinstance(block, dict):
                content_blocks[index] = dict(block)
        elif event_type == "content_block_delta":
            index = data.get("index")
            delta = data.get("delta")
            if isinstance(index, int) and isinstance(delta, dict):
                _merge_content_block_delta(content_blocks.setdefault(index, {}), delta)
        elif event_type == "message_delta":
            if message is None:
                message = {"type": "message", "content": []}
            delta = data.get("delta")
            if isinstance(delta, dict):
                message.update(delta)
            usage = data.get("usage")
            if isinstance(usage, dict):
                existing_usage = message.get("usage")
                merged_usage = (
                    dict(existing_usage) if isinstance(existing_usage, dict) else {}
                )
                merged_usage.update(usage)
                message["usage"] = merged_usage
        elif event_type == "error":
            error = data

    if message is None:
        return error if error is not None else {"content": []}

    message.setdefault("type", "message")
    message.setdefault("role", "assistant")
    assembled_content: list[dict[str, Any]] = []
    for index in sorted(content_blocks):
        block = content_blocks[index]
        partial_json = block.pop("_partial_json", None)
        if partial_json is not None:
            try:
                block["input"] = json.loads(partial_json)
            except json.JSONDecodeError:
                block["input"] = partial_json
        assembled_content.append(block)
    message["content"] = assembled_content
    return message


def _body_for_report(body: bytes, content_type: str = "") -> Any:
    if not body:
        return None
    text: str
    try:
        text = body.decode("utf-8")
    except UnicodeDecodeError:
        return {"encoding": "base64", "data": base64.b64encode(body).decode("ascii")}
    if "text/event-stream" in content_type.lower():
        return _joined_sse_response(text)
    try:
        return json.loads(text)
    except json.JSONDecodeError:
        return text


async def _write_report(report_path: Path | None, report: dict[str, Any]) -> None:
    if report_path is None:
        return
    data = json.dumps(report, ensure_ascii=False, indent=2)
    try:
        await asyncio.to_thread(report_path.write_text, data, encoding="utf-8")
    except OSError:
        logger.exception("failed to write report %s", report_path)


def _normalize_cache_token_usage(body: Any) -> None:
    usage = body.get("usage") if isinstance(body, dict) else None
    if not isinstance(usage, dict):
        return

    def token_count(*names: str) -> int:
        for name in names:
            value = usage.get(name)
            if isinstance(value, (int, float)) and not isinstance(value, bool):
                return int(value)
        return 0

    usage["cached_tokens"] = token_count(
        "cache_read_input_tokens", "cached_tokens"
    )
    usage["cache_creation_input_tokens"] = token_count(
        "cache_creation_input_tokens"
    )


def _record_response(
    report: dict[str, Any],
    *,
    status_code: int,
    headers: dict[str, str],
    body: Any,
) -> None:
    _normalize_cache_token_usage(body)
    report["response"] = {
        "status_code": status_code,
        "headers": _redact_headers(headers),
        "body": body,
    }


def _content_to_system_blocks(content: Any) -> list[dict[str, Any]]:
    """Convert an in-messages system content value to Anthropic system blocks."""
    if content is None:
        return []
    if isinstance(content, str):
        text = content.strip()
        return [{"type": "text", "text": text}] if text else []
    if isinstance(content, list):
        blocks: list[dict[str, Any]] = []
        for item in content:
            if isinstance(item, str):
                text = item.strip()
                if text:
                    blocks.append({"type": "text", "text": text})
            elif isinstance(item, dict):
                if item.get("type") == "text":
                    text = str(item.get("text", "")).strip()
                    if text:
                        block = {"type": "text", "text": text}
                        if isinstance(item.get("cache_control"), dict):
                            block["cache_control"] = item["cache_control"]
                        blocks.append(block)
                else:
                    # vLLM/Qwen system prompts cannot use images/tool blocks. Keep a
                    # textual marker instead of forwarding invalid system content.
                    blocks.append(
                        {"type": "text", "text": json.dumps(item, ensure_ascii=False)}
                    )
            else:
                blocks.append({"type": "text", "text": str(item)})
        return blocks
    return [{"type": "text", "text": str(content)}]


def _merge_system(existing: Any, extra_blocks: list[dict[str, Any]]) -> Any:
    if not extra_blocks:
        return existing
    if existing is None:
        return extra_blocks
    if isinstance(existing, str):
        existing = existing.strip()
        blocks = [{"type": "text", "text": existing}] if existing else []
        return blocks + extra_blocks
    if isinstance(existing, list):
        return existing + extra_blocks
    return [{"type": "text", "text": str(existing)}] + extra_blocks


def sanitize_anthropic_body(body: Any) -> tuple[Any, int]:
    """Move system-role entries out of messages[] and into top-level system."""
    if not isinstance(body, dict):
        return body, 0
    messages = body.get("messages")
    if not isinstance(messages, list):
        return body, 0

    cleaned: list[Any] = []
    lifted_system_blocks: list[dict[str, Any]] = []
    removed = 0

    for message in messages:
        if isinstance(message, dict) and message.get("role") == "system":
            lifted_system_blocks.extend(_content_to_system_blocks(message.get("content")))
            removed += 1
            continue
        cleaned.append(message)

    if removed == 0:
        return body, 0

    new_body = dict(body)
    new_body["messages"] = cleaned
    new_body["system"] = _merge_system(new_body.get("system"), lifted_system_blocks)
    return new_body, removed


def _forward_headers(request: Request) -> dict[str, str]:
    headers: dict[str, str] = {}
    for key, value in request.headers.items():
        lk = key.lower()
        if lk in HOP_BY_HOP_HEADERS or lk == "host":
            continue
        headers[key] = value
    return headers


def _redact_headers(headers: dict[str, str]) -> dict[str, str]:
    """Keep reports useful without persisting API credentials."""
    return {
        key: ("<redacted>" if key.lower() in SENSITIVE_REPORT_HEADERS else value)
        for key, value in headers.items()
    }


def _response_headers(headers: httpx.Headers) -> dict[str, str]:
    return {
        key: value
        for key, value in headers.items()
        if key.lower() not in HOP_BY_HOP_HEADERS
    }


def _build_upstream_url(path: str) -> str:
    """Join an Anthropic base URL with either /messages or /v1/messages."""
    base = UPSTREAM.rstrip("/")
    request_path = "/" + path.lstrip("/")
    if base.endswith("/v1") and (
        request_path == "/v1" or request_path.startswith("/v1/")
    ):
        request_path = request_path[3:] or "/"
    return f"{base}{request_path}"


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {
        "status": "ok",
        "upstream": UPSTREAM,
        "mode": "direct" if DIRECT else "sanitize",
        "report_dir": str(REPORT_DIR) if REPORT_DIR is not None else "",
    }


@app.api_route(
    "/{path:path}",
    methods=["GET", "POST", "PUT", "PATCH", "DELETE", "HEAD", "OPTIONS"],
)
async def proxy(path: str, request: Request) -> Response:
    report_path = await _allocate_report_path(request)
    upstream_url = _build_upstream_url(path)
    if request.url.query:
        upstream_url += f"?{request.url.query}"

    headers = _forward_headers(request)
    raw_body = await request.body()
    content = raw_body
    request_content_type = request.headers.get("content-type", "")
    report: dict[str, Any] = {}
    if report_path is not None:
        report = {
            "forwarded_request": {
                "method": request.method,
                "url": upstream_url,
                "headers": _redact_headers(headers),
                "body": None,
            },
        }

    if not DIRECT and request.method.upper() in {"POST", "PUT", "PATCH"} and raw_body:
        if "application/json" in request_content_type:
            try:
                body = json.loads(raw_body)
            except json.JSONDecodeError:
                body = None
            if body is not None:
                body, removed = sanitize_anthropic_body(body)
                if removed:
                    logger.info(
                        "lifted %s system message(s) for %s",
                        removed,
                        request.url.path,
                    )
                content = json.dumps(
                    body, ensure_ascii=False, separators=(",", ":")
                ).encode("utf-8")
                headers["content-type"] = "application/json"

    if report_path is not None:
        report["forwarded_request"]["body"] = _body_for_report(content)

    stream_requested = False
    if content:
        try:
            parsed = json.loads(content)
            stream_requested = bool(isinstance(parsed, dict) and parsed.get("stream"))
        except Exception:
            stream_requested = False

    client = httpx.AsyncClient(timeout=TIMEOUT)
    if stream_requested:
        upstream_request = client.build_request(
            request.method,
            upstream_url,
            headers=headers,
            content=content if content else None,
        )
        try:
            upstream_response = await client.send(upstream_request, stream=True)
        except httpx.HTTPError as exc:
            await client.aclose()
            logger.exception("upstream request failed")
            error_body = {"error": {"type": "upstream_error", "message": str(exc)}}
            _record_response(
                report,
                status_code=502,
                headers={},
                body=error_body,
            )
            await _write_report(report_path, report)
            return JSONResponse(error_body, status_code=502)

        async def body_iter() -> AsyncIterator[bytes]:
            response_chunks: list[bytes] = []
            try:
                async for chunk in upstream_response.aiter_raw():
                    if report_path is not None:
                        response_chunks.append(chunk)
                    yield chunk
            finally:
                await upstream_response.aclose()
                await client.aclose()
                if report_path is not None:
                    response_body = b"".join(response_chunks)
                    assembled_body = _body_for_report(
                        response_body,
                        upstream_response.headers.get("content-type", ""),
                    )
                    _record_response(
                        report,
                        status_code=upstream_response.status_code,
                        headers=dict(upstream_response.headers),
                        body=assembled_body,
                    )
                    await _write_report(report_path, report)

        return StreamingResponse(
            body_iter(),
            status_code=upstream_response.status_code,
            headers=_response_headers(upstream_response.headers),
            media_type=upstream_response.headers.get("content-type"),
        )

    try:
        upstream_response = await client.request(
            request.method,
            upstream_url,
            headers=headers,
            content=content if content else None,
        )
    except httpx.HTTPError as exc:
        logger.exception("upstream request failed")
        error_body = {"error": {"type": "upstream_error", "message": str(exc)}}
        _record_response(
            report,
            status_code=502,
            headers={},
            body=error_body,
        )
        await _write_report(report_path, report)
        return JSONResponse(error_body, status_code=502)
    finally:
        await client.aclose()

    if report_path is not None:
        parsed_body = _body_for_report(
            upstream_response.content,
            upstream_response.headers.get("content-type", ""),
        )
        _record_response(
            report,
            status_code=upstream_response.status_code,
            headers=dict(upstream_response.headers),
            body=parsed_body,
        )
        await _write_report(report_path, report)

    return Response(
        content=upstream_response.content,
        status_code=upstream_response.status_code,
        headers=_response_headers(upstream_response.headers),
        media_type=upstream_response.headers.get("content-type"),
    )


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--host", default="10.200.0.1")
    parser.add_argument("--port", type=int, default=30012)
    parser.add_argument("--upstream", default="http://127.0.0.1:30010")
    parser.add_argument("--log-level", default="info")
    parser.add_argument(
        "--report",
        action="store_true",
        help=(
            "store forwarded requests and assembled responses under "
            "server/reports/<session-id>/[<agent-id>/]"
        ),
    )
    parser.add_argument(
        "--direct",
        action="store_true",
        help="forward request bodies unchanged instead of sanitizing system messages",
    )
    args = parser.parse_args()

    global DIRECT, REPORT_DIR, UPSTREAM
    UPSTREAM = args.upstream.rstrip("/")
    DIRECT = args.direct
    if args.report:
        REPORT_DIR = _create_report_dir()
    logging.basicConfig(level=getattr(logging, args.log_level.upper(), logging.INFO))
    if REPORT_DIR is not None:
        logger.info("writing request/response reports to %s", REPORT_DIR)

    import uvicorn

    uvicorn.run(app, host=args.host, port=args.port, log_level=args.log_level)


if __name__ == "__main__":
    main()
