---
name: view-traces
description: Start the proxy trace viewer web UI and inspect captured Claude↔vLLM request/response traces recorded under reports/. Use whenever the user wants to view, browse, debug, or summarize recent proxy traces — e.g. "open the trace viewer", "show me the latest request the proxy saw", "why did the proxy record a failure", "inspect a captured message".
---

# View Proxy Traces

This project is a Claude Code → vLLM passthrough proxy (`claude_proxy.py`). When run
with `--report`, it writes one JSON file per forwarded request to:

```
reports/<x-claude-code-session-id>/<seq>.json
```

(`unknown-session/` is used when the session id header is absent.) Each file has the
shape:

```jsonc
{
  "forwarded_request": { "method", "url", "headers", ...body... },
  // ...response fields...
}
```

Sensitive headers (authorization, api keys) are redacted by the proxy before writing.

## Start the trace viewer

A small web UI lives in `trace_viewer/`. Start it with:

```bash
bash trace_viewer/run_trace_viewer.sh
```

It serves on `http://127.0.0.1:30013` by default and reads `reports/`. Override with
the env vars `TRACE_VIEWER_HOST`, `TRACE_VIEWER_PORT`, `TRACE_VIEWER_REPORTS`.

After starting it, open `http://127.0.0.1:30013` in a browser, or tell the user the URL.
To check it came up, curl the health/root endpoint; if it fails, tail
`trace_viewer/trace_viewer.log`.

To run the proxy + viewer together against Claude Code, use `run_claude.sh`.

## Inspect traces programmatically

Prefer reading JSON directly over loading huge files into context — individual traces
can exceed 100KB. Use `jq` / `python` to pull only the fields you need:

- List the most recent session dirs: `ls -t reports/ | head`
- Count requests in a session: `ls reports/<session-id>/ | wc -l`
- Show a request's role/sequence compactly: `jq '{role: .forwarded_request.body.role, model: .forwarded_request.body.model}' reports/<id>/<n>.json`

## Notes

- Don't echo redacted auth values; the proxy already strips them — keep it that way.
- Traces are large and repetitive. Summarize; don't paste whole files back to the user.
