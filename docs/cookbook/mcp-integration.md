# MCP integration cookbook — `grandlivre`

> Recipes for exposing `grandlivre` through a Model Context Protocol
> (MCP) server. Each section documents one pattern, inline and self-contained,
> so you can copy any block directly into your own server module.
>
> Patterns drawn from the upstream Python SDK community workarounds and
> from production use, adapted to the accounting domain. This document does
> not reference any private repository paths.

The Model Context Protocol lets you hand a typed accounting client to an
LLM-driven host (Claude Desktop, Claude Code, Cursor, custom agents, …).
`grandlivre` is transport-agnostic and knows nothing about MCP. This cookbook
shows the four patterns that turn the library into a reliable MCP surface:

1. **Wrapping** — minimal FastMCP server, dual transport (`stdio` /
   `streamable-http`), resources for semi-static reference data, tools
   for model-invocable queries.
2. **Protocol injection** — short numbered instructions co-located with
   the tool's return payload, so the LLM client receives discipline
   at the point of use rather than in a README it never re-reads.
3. **stdio safety** — FD-level stdout redirect at import, so heavy
   dependencies cannot corrupt the JSON-RPC frame stream.
4. **Writes** — wiring the library's built-in write-ahead log (duplicate
   protection, human reconciliation) into your tools instead of building
   your own.

The four sections are independent: pick the ones you need.

---

## 1. Wrapping `grandlivre` in a FastMCP server

The `grandlivre` library gives you a typed async client for the Pennylane V2 API: cursor pagination, rate limiting keyed on cabinet, retry on 429/5xx, `Decimal`-strict parsing, and Pydantic v2 models for the core accounting resources (`TrialBalanceResource`, `LedgerEntriesResource`, `SupplierInvoicesResource`, `FiscalYearResource`, `JournalsResource`, `LedgerAccountsResource`). On its own that is enough to script a batch report or back a custom FastAPI endpoint. What it does not give you is a way to hand those capabilities to a Large Language Model running inside a host like Claude Desktop, Claude Code, Cursor, or any other MCP-capable client.

The Model Context Protocol (MCP) closes that gap. By wrapping the library in a small FastMCP server, you expose the accounting domain through three primitives the protocol understands:

- **Tools** (model-invocable functions) — e.g. `get_trial_balance(exercice)`, which the agent decides to call to answer a question.
- **Resources** (application-controlled documents addressable by URI) — e.g. `pennylane://fiscal_years/{cabinet_id}/{dossier_id}`, loaded by the host at session start and refreshed on `notifications/resources/updated`. The chart of accounts and the list of fiscal years are semi-static reference data, and exposing them as resources lets the client cache them aggressively (a direct lever against the Pennylane rate limit).
- **Custom HTTP routes** — for liveness probes and version reporting, separate from the MCP transport itself.

The example below is a minimal but production-shaped server. It uses two transports (`stdio` for local pairing with a desktop client, `streamable-http` for a horizontally scalable deployment), binds to loopback by default, refuses to start on a non-local interface unless explicitly authorized, and emits a security warning at boot when HTTP authentication is not yet configured. The implementation deliberately stays under 100 lines; everything heavy lives in `grandlivre`.

```python
# server.py — minimal MCP server wrapping grandlivre
from __future__ import annotations

import json
import logging
import os
import sys

from mcp.server.fastmcp import FastMCP
from grandlivre import (
    FiscalYearResource,
    PennylaneClient,
    Settings,
    TrialBalanceResource,
)
from starlette.requests import Request
from starlette.responses import JSONResponse

# Logging to stderr — stdout is reserved for the MCP stdio transport.
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    stream=sys.stderr,
)
logger = logging.getLogger("pennylane-mcp")

VERSION = "0.1.0"

# Settings: grandlivre reads PENNYLANE_API_TOKEN, PENNYLANE_BASE_URL,
# PENNYLANE_RATE_LIMIT and PENNYLANE_CABINET_ID from the environment (or a .env file).
SETTINGS = Settings()

MCP_TRANSPORT = os.environ.get("MCP_TRANSPORT", "streamable-http").lower()
MCP_HTTP_HOST = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
MCP_HTTP_PORT = int(os.environ.get("MCP_HTTP_PORT", "8765"))
MCP_ALLOW_NON_LOCAL_BIND = os.environ.get("MCP_ALLOW_NON_LOCAL_BIND", "").lower() == "true"

mcp = FastMCP(
    name="pennylane-read",
    instructions=(
        "Read-only access to Pennylane V2: trial balance, fiscal years. "
        "Use the fiscal_years resource at session start to discover available exercises, "
        "then call get_trial_balance for a specific year."
    ),
    stateless_http=False,   # required for sampling and server-initiated notifications
    json_response=False,    # SSE-style streaming, not single JSON responses
    host=MCP_HTTP_HOST,
    port=MCP_HTTP_PORT,
)


@mcp.resource(
    "pennylane://fiscal_years/{cabinet_id}/{dossier_id}",
    name="fiscal_years",
    description=(
        "List of fiscal years for the scoped Pennylane dossier. Application-controlled: "
        "the host loads this at session start and refreshes on notifications/resources/updated."
    ),
    mime_type="application/json",
)
async def fiscal_years(cabinet_id: str, dossier_id: str) -> str:
    async with PennylaneClient(settings=SETTINGS) as client:
        years = [fy.model_dump(mode="json") async for fy in FiscalYearResource(client).iter()]
    return json.dumps(
        {"cabinet_id": cabinet_id, "dossier_id": dossier_id, "items": years},
        ensure_ascii=False,
    )


@mcp.tool(
    name="get_trial_balance",
    description=(
        "Return the trial balance for a given fiscal year. "
        "For each account: debits, credits, and balance (debits - credits). "
        "Amounts are Decimal serialized as strings. "
        "Parameter `exercice`: year as a string (e.g. '2024'). The tool resolves "
        "period_start/period_end internally via the fiscal_years endpoint."
    ),
)
async def get_trial_balance(exercice: str) -> str:
    async with PennylaneClient(settings=SETTINGS) as client:
        response = await TrialBalanceResource(client).get(exercice)
    return json.dumps(response.model_dump(mode="json"), ensure_ascii=False)


@mcp.custom_route("/health", methods=["GET"])
async def health(_: Request) -> JSONResponse:
    return JSONResponse({"status": "ok"})


@mcp.custom_route("/version", methods=["GET"])
async def version(_: Request) -> JSONResponse:
    return JSONResponse({"name": "pennylane-read", "version": VERSION, "transport": MCP_TRANSPORT})


def main() -> None:
    if MCP_TRANSPORT == "stdio":
        logger.info("Starting pennylane-read on stdio transport.")
        mcp.run(transport="stdio")
        return

    if MCP_TRANSPORT == "streamable-http":
        is_local = MCP_HTTP_HOST in {"127.0.0.1", "localhost", "::1"}
        if not is_local and not MCP_ALLOW_NON_LOCAL_BIND:
            logger.error(
                "Refusing to start: MCP_HTTP_HOST=%s is not a local interface and "
                "MCP_ALLOW_NON_LOCAL_BIND is not 'true'. No HTTP authentication is "
                "configured in this minimal server.",
                MCP_HTTP_HOST,
            )
            raise SystemExit(2)
        logger.warning(
            "HTTP server starting WITHOUT authentication on %s:%s. "
            "Add mTLS / JWT / shared header before any non-local deployment.",
            MCP_HTTP_HOST,
            MCP_HTTP_PORT,
        )
        mcp.run(transport="streamable-http")
        return

    logger.error("Unknown transport: %s (expected 'stdio' or 'streamable-http').", MCP_TRANSPORT)
    raise SystemExit(2)


if __name__ == "__main__":
    main()
```

> **Architectural decisions baked into this skeleton**
>
> - **`stateless_http=False`, `json_response=False`** — non-negotiable as long as you want server-initiated notifications (`notifications/resources/updated` to invalidate cached resources) or sampling (server-driven LLM completions). Stateless HTTP collapses MCP to a request/response RPC and severs both capabilities.
> - **Sticky sessions on header `mcp-session-id`** — when you scale horizontally behind a load balancer, route on this header. Stateful MCP sessions cannot be load-balanced round-robin.
> - **MCP for interactive plane, plain Python for batch plane** — async workers (Celery, RQ, Arq) should import `grandlivre` directly. Do not open an MCP session from a batch worker; you would pay the protocol overhead with no LLM consumer on the other end.
> - **Resources are scoped by `cabinet_id` + `dossier_id` in the URI** — any cache key built downstream must reuse the full composition. A semi-static resource served from a wrongly-scoped cache is how one tenant ends up reading another tenant's chart of accounts.
> - **Tool descriptions ≥ 3 lines** — describe what the tool does, when to call it, when not to call it, and the parameter contract. The agent uses this text to decide; terse descriptions produce wrong tool selection.

> **Running it**
>
> ```bash
> # Install
> pip install grandlivre mcp
>
> # Configure (or use a .env file at the project root)
> export PENNYLANE_API_TOKEN="..."          # required
> export PENNYLANE_BASE_URL="https://app.pennylane.com/api/external/v2"
> export PENNYLANE_RATE_LIMIT=5
> export PENNYLANE_CABINET_ID="default"
>
> # stdio transport — pair with Claude Desktop, Claude Code, Cursor, etc.
> MCP_TRANSPORT=stdio python server.py
>
> # streamable-http transport — for a deployed server
> MCP_TRANSPORT=streamable-http \
>   MCP_HTTP_HOST=127.0.0.1 MCP_HTTP_PORT=8765 \
>   python server.py
>
> # Liveness probes (HTTP transport only)
> curl -s http://127.0.0.1:8765/health
> curl -s http://127.0.0.1:8765/version
> ```
>
> To register with a `stdio`-capable host, point its MCP config at `python /absolute/path/to/server.py` with `MCP_TRANSPORT=stdio` and the `PENNYLANE_*` environment variables in the launch env.

---

## 2. Protocol injection — `_protocol` field at point of use

When exposing accounting data through MCP tools, a recurring failure mode is LLM clients answering from prior context or training memory rather than from the tool's actual return value. The client may guess the fiscal year, paginate incorrectly, or summarize a truncated payload as if it were complete. README-level documentation does not reliably prevent this, because the client does not re-read the README at each tool call — it sees the tool's response.

The **protocol injection** pattern addresses this by embedding a short, numbered, prescriptive instruction *inside the tool's return payload*, under a conventional key (`_protocol`). The LLM client receives the discipline at the point of use, co-located with the data. This is **soft enforcement**: nothing on the server blocks misuse, but the instruction is hard to ignore because it arrives in the same message as the data the model is about to reason on. The technique is applied here to accounting tools where the cost of an unverified answer (wrong fiscal year, hallucinated total) is high.

### Example: three inline protocols for `grandlivre`

```python
# grandlivre/mcp/protocols.py

TRIAL_BALANCE_PROTOCOL = """IMPORTANT — Trial Balance Protocol:
1. BEFORE ANSWERING accounting questions: confirm the CORRECT fiscal year
   in the returned payload (`fiscal_year_id`, `start_date`, `end_date`).
   Never assume "current year" — fiscal years may not align with calendar years.
2. IF UNSURE which fiscal year applies: call `list_fiscal_years` first and ask
   the user to confirm before querying.
3. CITE the fiscal year explicitly in your answer (e.g. "for FY 2024-2025
   ending 2025-06-30, the balance is ...").
"""

SUPPLIER_INVOICES_PROTOCOL = """IMPORTANT — Supplier Invoices Protocol:
1. BEFORE LISTING invoices: confirm the fiscal year AND the date range
   (`date_from`, `date_to`) with the user. Defaults may not match intent.
2. PAGINATION: results are capped at 500 per page. If `next_cursor` is
   present in the response, more results exist — continue with the cursor
   before reporting totals.
3. NEVER report "no invoices found" when `truncated=true` — it means the
   cap was hit, not that the dataset is empty.
"""

LEDGER_ENTRIES_PROTOCOL = """IMPORTANT — Ledger Entries Protocol:
1. BEFORE SUMMARIZING: read the returned `summary` field if present
   (`total_debit`, `total_credit`, `balance`). Use it verbatim.
2. IF `truncated=true`: do NOT sum the visible entries as if they were
   complete. Request continuation via `cursor` until exhausted, or report
   the truncation explicitly to the user.
3. SUM MANUALLY only when `summary` is null AND `truncated=false`.
"""
```

### Insertion at the tool boundary

The protocol travels with the data. The pattern is a one-liner addition to every tool return:

```python
# grandlivre/mcp/server.py
from mcp.server.fastmcp import FastMCP
from grandlivre import PennylaneClient, TrialBalanceResource
from grandlivre.mcp.protocols import (
    TRIAL_BALANCE_PROTOCOL,
    SUPPLIER_INVOICES_PROTOCOL,
    LEDGER_ENTRIES_PROTOCOL,
)

mcp = FastMCP("grandlivre")

@mcp.tool()
async def get_trial_balance(fiscal_year_id: str) -> dict:
    """Return the trial balance for a given fiscal year."""
    client = PennylaneClient.from_env()
    balance = await TrialBalanceResource(client).get(fiscal_year_id)
    return {
        "data": balance.model_dump(mode="json"),
        "fiscal_year_id": fiscal_year_id,
        "_protocol": TRIAL_BALANCE_PROTOCOL,
    }

@mcp.tool()
async def get_supplier_invoices(
    fiscal_year_id: str,
    date_from: str | None = None,
    date_to: str | None = None,
    cursor: str | None = None,
) -> dict:
    """List supplier invoices for a fiscal year, paginated (cap 500)."""
    client = PennylaneClient.from_env()
    page = await client.supplier_invoices.paginate(
        fiscal_year_id=fiscal_year_id,
        date_from=date_from,
        date_to=date_to,
        cursor=cursor,
    )
    return {
        "data": [inv.model_dump(mode="json") for inv in page.items],
        "next_cursor": page.next_cursor,
        "truncated": page.truncated,
        "_protocol": SUPPLIER_INVOICES_PROTOCOL,
    }
```

The constant lives module-level so it's allocated once and easy to audit. The key `_protocol` is conventional: any leading-underscore key signals "metadata for the client, not domain data."

> **Trade-offs — soft vs hard enforcement**
>
> Protocol injection is **soft enforcement**: a well-behaved LLM client will read and follow the protocol; a misbehaving or fine-tuned-around-it client can ignore it. The MCP transport does not let the server validate that the client respected the instruction. For genuinely safety-critical invariants (e.g. "never return invoices outside the requested fiscal year"), enforce **server-side**: validate inputs, filter outputs, raise on contract violations. Use protocol injection for the layer above — disciplining the LLM's *reasoning* over correct data, not the correctness of the data itself.

> **When NOT to use this pattern**
>
> - **Trivial tools without hallucination risk.** A `/health` endpoint returning `{"status": "ok"}` does not benefit from a protocol — there is nothing to misinterpret.
> - **Very large responses.** If a tool already returns tens of kilobytes of structured data, a 300-character protocol adds proportionally little signal and contributes noise to the context window. Prefer documenting the discipline in the tool's `description` (FastMCP `@mcp.tool(description=...)`) which the client sees during tool discovery.
> - **Tools with strong self-describing schemas.** When the Pydantic model and field names make misuse implausible (e.g. a tool that returns a single `Decimal`), the protocol is redundant.
> - **High-frequency polling tools.** Repeating the same protocol on every poll wastes tokens; emit it on the first call of a session or rely on `instructions` at server initialization.

Reserve `_protocol` for tools where the *interpretation* of the response is where mistakes happen — fiscal-year scoping, pagination boundaries, truncation handling. Those are the moments where reminding the client at the point of use earns its keep.

---

## 3. stdio safety — FD-level stdout protection at import

When a Pennylane MCP server runs over the `stdio` transport, the JSON-RPC protocol uses file descriptors `0` (stdin) and `1` (stdout) as the wire. Anything else that writes to FD `1` — even a single line — corrupts the frame stream: the MCP client either fails to parse the next message, drops the connection, or crashes. The danger is not theoretical: several common dependencies print banners, version notices, telemetry opt-out messages, or CUDA/ONNX initialization output directly at the **C level**, bypassing `sys.stdout`. Reassigning `sys.stdout` in Python is therefore insufficient — the only safe protection is to redirect FD `1` itself before those libraries are imported.

For the `streamable-http` (or SSE) transport, this concern does not apply: the JSON-RPC frames travel over an HTTP socket, and stdout remains a normal log channel. The pattern below is gated on `MCP_TRANSPORT == "stdio"` so it becomes a no-op for HTTP deployments. Place the guard at the very top of `server.py`, **before any other import**, so that even libraries imported transitively by `httpx`, `aiolimiter`, or accounting SDKs cannot leak onto the protocol channel.

```python
# server.py — must be the FIRST executable code in the module
import os
import sys

_REAL_STDOUT = sys.stdout
_REAL_STDOUT_FD: int | None = None

if os.environ.get("MCP_TRANSPORT", "streamable-http") == "stdio":
    try:
        # Duplicate the real stdout FD so we can restore it later for JSON-RPC.
        _REAL_STDOUT_FD = os.dup(1)
        # FD-level redirect: anything written to FD 1 (including C-level printf,
        # write(2), banners from native extensions) now goes to stderr.
        os.dup2(2, 1)
    except (OSError, AttributeError):
        # Non-fatal: on exotic platforms without dup2, fall through to the
        # Python-level guard below. C-level pollution may still occur there.
        pass
    # Python-level redirect: redundant with dup2 but explicit, and covers
    # the (rare) case where a library caches sys.stdout at import time.
    sys.stdout = sys.stderr

# Now it is safe to import heavy dependencies that may print at import.
import asyncio  # noqa: E402
import httpx  # noqa: E402
import aiolimiter  # noqa: E402
from mcp.server.fastmcp import FastMCP  # noqa: E402

from grandlivre import PennylaneClient  # noqa: E402


def _restore_stdout() -> None:
    """Restore the real stdout FD so MCP can use it as the JSON-RPC channel.

    Call this exactly once, immediately before ``mcp.run(transport='stdio')``.
    After this call, ``print()`` and any C-level write to FD 1 will once again
    land on the protocol stream — so don't print anything from your own code.
    """
    global _REAL_STDOUT_FD
    if _REAL_STDOUT_FD is not None:
        try:
            os.dup2(_REAL_STDOUT_FD, 1)
            os.close(_REAL_STDOUT_FD)
        except OSError:
            pass
        _REAL_STDOUT_FD = None
    sys.stdout = _REAL_STDOUT


mcp = FastMCP("grandlivre-mcp")


@mcp.tool()
async def get_trial_balance(exercice: str) -> dict:
    """Return the trial balance for the given fiscal year."""
    async with PennylaneClient() as client:
        return await client.trial_balance.get(exercice)


def main() -> None:
    transport = os.environ.get("MCP_TRANSPORT", "streamable-http")
    if transport == "stdio":
        # Hand FD 1 back to MCP just before it starts writing JSON-RPC frames.
        _restore_stdout()
        mcp.run(transport="stdio")
    else:
        host = os.environ.get("MCP_HTTP_HOST", "127.0.0.1")
        port = int(os.environ.get("MCP_HTTP_PORT", "8765"))
        mcp.run(transport="streamable-http", host=host, port=port)


if __name__ == "__main__":
    main()
```

> **Regression test — guard against future stdout pollution**
>
> Heavy dependencies are added over time, and a new transitive import may quietly start printing a banner. Pin the invariant with a subprocess test that boots the server over stdio and asserts that the first byte on stdout is `{` (a JSON-RPC frame), not the leading character of a banner.
>
> ```python
> import json
> import os
> import subprocess
>
>
> def test_no_stdout_pollution_stdio() -> None:
>     proc = subprocess.Popen(
>         ["python", "-m", "your_package.server"],
>         env={**os.environ, "MCP_TRANSPORT": "stdio"},
>         stdin=subprocess.PIPE,
>         stdout=subprocess.PIPE,
>         stderr=subprocess.PIPE,
>     )
>     init = json.dumps(
>         {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
>     )
>     out, _err = proc.communicate(init.encode() + b"\n", timeout=5)
>     first_line = out.split(b"\n")[0]
>     assert first_line.startswith(b"{"), f"stdout polluted: {first_line!r}"
>     parsed = json.loads(first_line)
>     assert parsed["jsonrpc"] == "2.0"
> ```
>
> Run this test in CI for every PR. A library upgrade that reintroduces a banner will fail it before reaching users.

> **When NOT to use this pattern**
>
> If your deployment only ever exposes `streamable-http` or SSE — for example, a containerized server behind an ingress — the guard is unnecessary because stdout never carries protocol frames. The `MCP_TRANSPORT` check above already makes the pattern a no-op in that case, so it is safe to keep the code in place: it costs nothing at HTTP startup and protects the day someone runs the same module locally over stdio for debugging.

*Works around upstream `modelcontextprotocol/python-sdk` issue #225.*

---

## 4. Writes — use the built-in WAL, do not build your own

`grandlivre` ships its own write-ahead log for the write operations it exposes
(`LedgerEntriesResource.create`, `InvoicePaymentsResource.*`). Inject a durable
store once and pass it to the resources; the MCP server never talks to a WAL file
directly:

```python
from pathlib import Path
from grandlivre import InvoicePaymentsResource, LedgerEntriesResource, SqliteIdempotencyStore

STORE = SqliteIdempotencyStore(Path(os.environ["GRANDLIVRE_WAL_PATH"]))

@mcp.tool()
async def create_entry(...) -> dict:
    async with PennylaneClient(settings=get_settings()) as client:
        created = await LedgerEntriesResource(client, wal_store=STORE).create(
            ..., namespace=dossier_id
        )
    return {"id": created.id}
```

What you get for free: `IN_FLIGHT` persisted before the request, short-circuit on
an identical resubmission (zero request), `IN_DOUBT` + `WALInDoubtError` instead of
blind retries, `recover()` after a crash, and — for payment statuses — a fresh read
of the invoice before answering "already done". Use `ensure_deployment_safety` at
startup if several processes may write, and implement `IdempotencyStore` (validated
by `grandlivre.store_testing`) when you need a cross-host store such as Postgres.

## References

The stdio pattern addresses upstream `modelcontextprotocol/python-sdk` issue #225; the other patterns come from production use of `grandlivre` behind MCP servers.

If you are building a new MCP server on top of `grandlivre`, pick the sections relevant to your transport and risk profile:

| You are exposing... | Sections to read |
|---|---|
| Local stdio server for a desktop LLM client | 1, 2, 3 |
| Containerized HTTP server behind ingress | 1, 2 |
| Read-only server | 1, 2, 3 |
| Server exposing write tools | 1, 2, 3, 4 |

Contributions to this cookbook are welcome — see `CONTRIBUTING.md` for the full workflow and the simplicity criterion that guides reviews.
