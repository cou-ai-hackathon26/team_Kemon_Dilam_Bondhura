# Read-only access from an MCP client

In Settings, a workspace owner can create a read-only token. The token is shown once, expires after 90 days and can be revoked in Settings. Keep it in the MCP client’s secret store. Each token belongs to the issuing workspace.

For Streamable HTTP, use the endpoint displayed in Settings with an `Authorization: Bearer <token>` header. The server provides `cash_balance`, `cash_forecast` and `open_supplier_bills`. Each amount uses the currency’s integer minor units. Source references and supplier text carry an explicit untrusted-data marker.

For an MCP client supporting stdio, launch `python scripts/mcp_stdio.py` from the repository checkout with the pinned API dependencies installed. Set `CAPZORYX_MCP_URL` to the displayed endpoint and `CAPZORYX_MCP_TOKEN` through the client’s environment or secret store. The bridge uses HTTPS for remote endpoints.
