# grandlivre

> **Unofficial Python client for the Pennylane accounting API (v2).** Async, typed,
> read *and* idempotent write, validated against the real API. Not affiliated with
> Pennylane.

*This is a short English summary. The French [README.md](README.md) is the
reference and is kept ahead of this file; docstrings and the CHANGELOG are in French.*

**Who it is for**: developers automating the bookkeeping of companies kept on
Pennylane (scripts, back-office jobs, agents). **What it is**: a typed, async HTTP
client, independent of any language model; use it in a script, a service, or behind
an MCP server called by Claude, Mistral or any other client. **What it is not**:
neither accounting software, nor an official SDK, nor an AI tool. Pennylane ships an
official, read-only MCP server since August 2026; this library remains the tool for
scripts, services, and idempotent writes.

> **Trademark and scope.** "Pennylane" is a trademark of Pennylane SAS; this project
> is not developed, endorsed or approved by Pennylane. The name is used only to
> designate the service the library connects to. No relation either with
> [PennyLane](https://pennylane.ai/), Xanadu's quantum computing library (a homonym).

## What you get

- **Read, 18 resources**: trial balance (general, and auxiliary with one account per
  third party), ledger entry lines (with their lettering state) and grouped entries,
  journals, chart of accounts, fiscal years, customers, suppliers, supplier and
  customer invoices, bank accounts, transactions (filtered by account and period,
  with matched invoices), lettering, FEC and ledger exports, `me`, changelogs.
  Cursor pagination with a hard cap, async iterators, `Decimal` amounts, list filters
  sent in the API's `filter` JSON parameter (the only one the API applies).
- **Write, 7 operations** behind a write-ahead log (WAL) that prevents duplicates:
  journal entry creation, supplier invoice payment status, customer invoice marked
  paid, transaction matching, customer invoice import with its PDF, Factur-X
  e-invoice import, credit note linking. Every operation is recorded on disk before
  the request; the same operation sent twice short-circuits instead of duplicating;
  doubtful outcomes (timeout, 5xx) are never retried automatically and are left to a
  human to resolve.
- **Validated against a real Pennylane company** for every read and write operation
  listed above (campaigns from May to October 2026), with optional integration tests
  (`pytest -m smoke`).

## Install

```bash
pip install grandlivre
```

Python ≥ 3.12. Dependencies: `httpx`, `pydantic` (v2), `pydantic-settings`,
`aiolimiter`, `tenacity`, `anyio`.

## Quick start

```python
import asyncio
from grandlivre import PennylaneClient, Settings, TrialBalanceResource


async def main() -> None:
    async with PennylaneClient(settings=Settings()) as client:  # reads PENNYLANE_API_TOKEN
        tb = await TrialBalanceResource(client).get(exercice="2025", is_auxiliary=True)
        for account in tb.accounts:
            print(account.number, account.label, account.balance)


asyncio.run(main())
```

Write examples, configuration, architecture and design choices: see the French
[README.md](README.md). MCP integration: [`docs/cookbook/mcp-integration.md`](docs/cookbook/mcp-integration.md).

## Contributing, security, license

[CONTRIBUTING.md](./CONTRIBUTING.md), [SECURITY.md](./SECURITY.md) (private
vulnerability reports only), [Contributor Covenant 2.1](./CODE_OF_CONDUCT.md).
[Apache License 2.0](./LICENSE).
