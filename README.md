# 🦞 tinyclaw

**A tiny, governable multi-agent platform — A2A-native, observable, human-supervised.**

tinyclaw is a working answer to the question every enterprise asks about AI
agents: *"what does it take to let agents **act**, not just chat — and stay in
control?"* It is small enough to read in an afternoon and structured like a
real platform: standard protocol, real observability, policy-as-code
governance, and humans in the loop where risk demands it.

## What it demonstrates

| Pillar | Implementation |
|---|---|
| **Agent-to-agent communication** | Official [A2A protocol SDK](https://github.com/a2aproject/a2a-python) (Linux Foundation): JSON-RPC over HTTP, Agent Card discovery at `/.well-known/agent-card.json`, task lifecycle — the protocol's own `input-required` state is the human-in-the-loop pause primitive |
| **Observability** | OpenTelemetry everywhere; W3C tracecontext propagated **inside A2A message metadata** so one distributed trace spans every agent hop + every LLM call (GenAI semantic conventions); OTLP export to self-hosted **Langfuse**; live SSE event feed + governance KPIs (autonomy rate, escalation rate, approval latency, denials) |
| **Human approval (HITL)** | Risk-tiered routing: tier-1 actions auto-execute (still audited); tier-2+ park in `input-required` and wait for a signed human decision (who / when / comment) that resumes the task |
| **AI governance** | Policy-as-code (YAML, most-restrictive-wins, all hits audited) · risk registry per action (`auto`/`threshold`/`always_human`/`blocked`, unknown = fail-safe) · PII redaction + prompt-injection guardrails **before any LLM call** · hash-chained **tamper-evident audit log** · agent identities & scopes · mapped to [NIST AI RMF + EU AI Act](docs/governance.md) |
| **Execution authorization** | HMAC-signed permits bound to task + action + TTL. The executor refuses everything it can't verify — even a compromised orchestrator can't force execution (test-proven) |
| **Agent Studio** | Create agents declaratively (prompt/model/tools/risk/policies), test them in a console with policy dry-run, deploy to a live A2A server — and deploying a **high-risk agent itself requires human approval**. The platform governs its own expansion. |
| **Scenario packs** | The core is scenario-agnostic; procurement approval ships as the first pluggable pack |

## Quickstart (zero API keys)

```bash
git clone https://github.com/<you>/tinyclaw && cd tinyclaw
uv sync --extra dev          # or: pip install -e ".[dev]"
./dev.sh                     # gateway + 5 agents + runtime, seeded demo
```

Open **http://127.0.0.1:9100** — the Approvals tab has human decisions waiting
(a $12.4k tier-2 and a $68k tier-3 request, parked mid-flight by the A2A
protocol itself). Approve one, reject the other, then check **Governance**:
the hash-chained audit log verifies green, and Overview shows the KPIs move.

The seeded demo covers every governance path:

| Seed request | Path |
|---|---|
| USB-C cables — $420 | tier 1 → auto-executed (audited) |
| Chairs — $12,400 | tier 2 → `input-required` → your approval → PO issued |
| Laptops — $68,000 | tier 3 → human approval |
| Northwind Trading | sanctions hit → hard-denied |
| Desk lamps (PII in description) | card/email redacted before the LLM saw it |
| "Ignore all previous instructions Ltd" | injection flagged → denied |

## With a real LLM

```bash
TINYCLAW_LLM_PROVIDER=openai  OPENAI_API_KEY=sk-…  ./dev.sh
TINYCLAW_LLM_PROVIDER=anthropic ANTHROPIC_API_KEY=… ./dev.sh
```

## Docker (one command, observability optional)

```bash
docker compose up --build                        # platform, mock mode
docker compose --profile observability up        # + self-hosted Langfuse :3000
```

Set `OTEL_EXPORTER_OTLP_ENDPOINT=http://langfuse-web:3000/api/public/otel`
and the same UI links light up with full traces. (~4 GB RAM for the
observability profile.)

## The dashboard

1. **Overview** — governance KPIs, live 5-stage pipeline, live A2A traffic, fleet health
2. **Tasks** — agent-to-agent conversation timeline, published plan, distributed-trace waterfall, Langfuse deep link
3. **Approvals** — human inbox with full context packets (request / research / policy evaluation)
4. **Agent Studio** — create → test (console + policy dry-run) → governed deploy
5. **Governance** — policy rules, risk registry, agent scopes, hash-chained audit log with live verification
6. **Playground** — submit any request JSON; sample edge cases included

## Repository map

```
src/tinyclaw/
├── core/            # scenario-agnostic framework
│   ├── agent.py     #   TinyclawExecutor: A2A server + tracing + lifecycle
│   ├── a2a_client.py#   client with tracecontext propagation
│   ├── llm/         #   Mock | OpenAI | Anthropic behind one interface
│   ├── governance/  #   policy engine, risk router, guardrails, audit, identity
│   ├── hitl/        #   signed execution permits
│   └── observability/#  OTel setup, GenAI spans
├── gateway/         # control plane: approvals, audit chain, KPIs, SSE, Studio API
├── runtime/         # Agent Studio: declarative agents hosted as real A2A servers
└── scenarios/
    └── procurement/ # the reference scenario pack (agents, policies, seed)
ui/                  # React + TS dashboard (six views)
docs/                # architecture, governance mapping, LinkedIn writeup
```

## Tests

```bash
uv run pytest tests/ -v
```

- **Unit**: policy engine (tiering, deny precedence, all-hits reporting), risk
  router (fail-safe defaults), guardrails, audit tamper detection, permit
  binding/expiry.
- **E2E**: boots the real gateway + five agent processes in mock mode and
  drives the full governed flow — including a human-approval round trip and
  a direct attack on the executor with no permit (it refuses).

## Design notes worth reading

- [Architecture & ADRs](docs/architecture.md) — including *patterns borrowed
  from production coding-agent harnesses* (plan-first execution, denials are
  final, no ambient authority, hooks as policy enforcement points)
- [Governance mapping](docs/governance.md) — controls → NIST AI RMF → EU AI Act
- [LinkedIn writeup draft](docs/writeup-linkedin.md)

## Honest scope (demo-grade by design)

- Inter-agent auth is a static bearer token; production path (Agent Card
  security schemes, OAuth2/mTLS) is documented in the architecture doc.
- The executor "issues POs" against an in-memory ledger; tools in Studio are
  declared for governance, execution wiring is the roadmap.
- SQLite + in-memory task stores; swapping to Postgres/SQL task store is a
  single-module change.

MIT license. Built to be read — every governance claim above has a test or a
UI view that proves it.
