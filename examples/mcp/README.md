# MCP client examples

- [`stdio.json`](stdio.json) — the agent starts a local server process. Use this
  for a single machine or when you do not want to expose an HTTP port.
- [`http.json`](http.json) — connect to a shared streamable-http deployment.
  Replace the URL and the Bearer token.

## Notes

- The stdio command is `python -m magicalgirl_web_oracle.mcp_server.server`.
  If the package is installed in a virtual environment, point the client at that
  environment's `python` (for example `.venv/bin/python` or
  `.venv\Scripts\python.exe`).
- For HTTP, `MCP_AUTH_TOKEN` must match the token configured on the server. If
  the server token is empty, omit the `headers` object.
- Some clients use a different shape (for example nested `mcp.servers`) or a
  different `type` value (`http` instead of `streamable-http`). Keep the URL,
  transport, and headers, and adapt the surrounding structure to your client.
- Never commit a real token. Use a placeholder in shared configs and store the
  token in the client's secret store or a private file.
