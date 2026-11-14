# Incident Response

An MCP server at `http://mcp-server:8000/mcp` exposes incident tools.
Investigate the services (api, cache, db) with `get_metrics`, then call
`resolve_incident` with the root cause you identify.
