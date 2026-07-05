# auto_consumer_law MCP Server

Standalone MCP server exposing US auto-purchase consumer-law reference data
(documentation fees, tax rates, legal citations) via four deterministic tools.

**Transport**: stdio — the server is launched as a subprocess by any MCP client.  
**Framework**: [FastMCP](https://github.com/jlowin/fastmcp) (Python MCP SDK)

---

## Tools

| Tool | Description |
|------|-------------|
| `get_doc_fee_rule(state)` | Statutory doc-fee cap, typical range, and legal citation |
| `get_tax_rates(state)` | State + average local sales-tax rates and combined rate |
| `get_legal_citation(state, fee_type)` | Citation text + source note for doc_fee / registration / title / sales_tax |
| `check_fees(state, fees)` | Per-fee audit: legal / excessive / illegal with threshold and citation |

---

## Quickstart

### Prerequisites

- Python ≥ 3.14  
- [`uv`](https://docs.astral.sh/uv/) installed

Install dependencies from the repo root:

```bash
uv sync
```

### Run the server manually (smoke test)

```bash
# From the repo root — verify the server starts without error
uv run python mcp_servers/auto_consumer_law/server.py
# (press Ctrl-C to exit — it blocks waiting for stdio from an MCP client)
```

### Run via `mcp dev` (interactive inspector)

```bash
uv run mcp dev mcp_servers/auto_consumer_law/server.py
```

This opens the MCP Inspector UI in your browser where you can call each tool
interactively.

---

## Connecting to an MCP client

### Claude Desktop

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "auto_consumer_law": {
      "command": "uv",
      "args": [
        "run",
        "--project", "/absolute/path/to/CatchFees-Agents",
        "python",
        "mcp_servers/auto_consumer_law/server.py"
      ]
    }
  }
}
```

### Gemini / Antigravity CLI

```json
{
  "mcpServers": {
    "auto_consumer_law": {
      "command": "uv",
      "args": ["run", "python", "mcp_servers/auto_consumer_law/server.py"],
      "cwd": "/absolute/path/to/CatchFees-Agents"
    }
  }
}
```

### Any other MCP client (stdio)

Start the server subprocess with:

```
uv run python mcp_servers/auto_consumer_law/server.py
```

then communicate over stdin/stdout using the MCP JSON-RPC protocol.

---

## Tool usage examples

### `get_doc_fee_rule`

```python
# California — capped at $85
get_doc_fee_rule("CA")
# → {"is_capped": true, "cap_amount": 85.0, "cap_type": "fixed",
#    "percent_cap": null, "typical_range": "$85",
#    "legal_citation": "CA Civil Code §4456.5", "law_summary": null}

# Ohio — lesser-of rule ($398 OR 10% of vehicle price)
get_doc_fee_rule("OH")
# → {"is_capped": true, "cap_amount": 398.0, "cap_type": "lesser-of",
#    "percent_cap": 0.10, "typical_range": "$250",
#    "legal_citation": "Ohio Admin Code 109:4-3-16",
#    "law_summary": "Ohio law caps the doc fee at the lesser of $398 or 10% …"}

# Florida — no statutory cap
get_doc_fee_rule("FL")
# → {"is_capped": false, "cap_amount": null, "cap_type": null, …}
```

### `get_tax_rates`

```python
get_tax_rates("TX")
# → {"state_rate": 0.0625, "avg_local_rate": 0.02,
#    "combined_rate": 0.0825, "note": "Max combined 8.25%",
#    "citation": "TX Tax Code §152.021"}

get_tax_rates("OR")   # no sales tax
# → {"state_rate": 0.0, "avg_local_rate": 0.0, "combined_rate": 0.0, …}
```

### `get_legal_citation`

```python
get_legal_citation("OH", "doc_fee")
# → {"fee_type": "doc_fee",
#    "citation": "Ohio Admin Code 109:4-3-16",
#    "source_note": "Ohio law caps the doc fee at the lesser of $398 …",
#    "state": "OH"}

get_legal_citation("CA", "sales_tax")
# → {"fee_type": "sales_tax",
#    "citation": "CA Rev & Tax Code §6051",
#    "source_note": "7.25% state base rate + district taxes",
#    "state": "CA"}
```

### `check_fees`

```python
# California — doc fee above statutory cap → ILLEGAL
check_fees("CA", {"doc_fee": 500, "registration": 55, "title": 23})
# → {
#     "state": "CA",
#     "verdicts": {
#       "doc_fee": {
#         "verdict": "illegal",
#         "amount": 500.0,
#         "threshold_used": 85.0,
#         "threshold_label": "CA statutory cap",
#         "citation": "CA Civil Code §4456.5",
#         "explanation": "CA caps doc fees at $85.00 … $415.00 over the legal limit."
#       },
#       "registration": {"verdict": "legal", …},
#       "title":        {"verdict": "legal", …}
#     },
#     "summary": "1 illegal; 2 legal fee(s) found in CA."
#   }

# Florida — uncapped, but $1200 doc fee is >1.5× typical → EXCESSIVE
check_fees("FL", {"doc_fee": 1200})
# → {"verdicts": {"doc_fee": {"verdict": "excessive", …}}, …}
```

---

## Running the tests

```bash
# From the repo root
uv run pytest mcp_servers/auto_consumer_law/tests/ -v
```

Expected output: all tests pass with no warnings.

---

## Data sources

| File | Contents |
|------|----------|
| `src/catchfees/data/state-fees.json` | Doc-fee caps, registration ranges, title fees for all 50 states + DC |
| `src/catchfees/data/tax-rates.json` | State and average local sales-tax rates |
| `src/catchfees/data/tax-laws.json` | Primary statutory citations for each state's vehicle tax |

All data is compiled from primary statutory sources and is current as of the
dataset version in this repository. Always verify against current state law
before relying on this data for legal advice.

---

## Verdict thresholds

| Fee type | Illegal condition | Excessive condition |
|----------|-------------------|---------------------|
| `doc_fee` | Amount > statutory cap (capped states only) | Amount > 1.5 × typical |
| `registration` | — (no hard cap in data) | Amount > 2 × upper typical range |
| `title` | — (no hard cap in data) | Amount > 2 × statutory fee |

> **Note**: "Illegal" means the fee explicitly violates a state statutory cap.
> "Excessive" means the fee is unusually high but may not be illegal in states
> without a cap. Always consult a licensed attorney for legal advice.
