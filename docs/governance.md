# AI Governance Mapping

How tinyclaw's concrete controls map to the frameworks an enterprise AI
program actually gets measured against.

## Control inventory

| # | Control | Implementation | Verify by |
|---|---|---|---|
| 1 | Policy-as-code | YAML rules; all rules evaluated; most-restrictive effect wins; every hit audited | `policies/procurement.yaml`, audit `policy.evaluate` |
| 2 | Risk-tiered actions | Registry per action: `auto` / `threshold` / `always_human` / `blocked`; unknown actions fail safe → always_human | `policies/risk.yaml`, `RiskRouter` |
| 3 | Human oversight | A2A `input-required` pause + approval queue; decisions signed with actor + comment + timestamp | Approvals view, audit `approval.decide` |
| 4 | Execution authorization | HMAC permits bound to task + action + TTL; executor refuses everything else (test-proven) | `test_executor_refuses_without_permit` |
| 5 | Audit trail | Hash-chained, append-only, tamper-evident (SHA-256 over prev-hash + canonical entry) | Governance view → *chain verified ✓* |
| 6 | Data protection | PII redaction (email/phone/card) **before** any LLM call and before audit persistence | `guardrail.hit` events |
| 7 | Input integrity | Prompt-injection heuristics over all untrusted fields (text + payload); flags feed policy → deny | seed case *“Special” vendor* |
| 8 | Agent identity & least privilege | Identity registry with scopes; executor is the only `po:write` holder | `policies/identity.yaml` |
| 9 | Governed expansion | Deploying a high-risk agent definition itself requires human approval | Studio deploy → Approvals |
| 10 | Observability | Distributed trace across all agents + LLM spans; KPIs: autonomy rate, escalation rate, approval latency, denials | Overview view, Langfuse |

## NIST AI Risk Management Framework (AI RMF 1.0)

| Function | tinyclaw coverage |
|---|---|
| **GOVERN** | Policy-as-code with explicit rule ownership (#1); agent identities with scopes (#8); governed agent deployment (#9) |
| **MAP** | Risk registry classifies every consequential action (#2); scenario manifests declare agents, policies, tools |
| **MEASURE** | Governance KPIs (autonomy, escalation, latency, denials, guardrail hits); full distributed traces; LLM token/cost spans |
| **MANAGE** | Risk-tiered routing (#2); human-in-the-loop escalation with SLA (#3); deny as a first-class outcome (#4); audit for post-incident review (#5) |

## EU AI Act (Regulation (EU) 2024/1689)

| Article | Requirement | tinyclaw coverage |
|---|---|---|
| Art. 12 — Record-keeping / logging | Automatically document activity over the system lifetime | Hash-chained audit log + event history + OTel traces (#5, #10) |
| Art. 14 — Human oversight | Effective oversight; ability to disregard/interrupt output | `input-required` pause, approve/reject with comment, permits (#3, #4) |
| Art. 15 — Accuracy/robustness | Technical measures for reliable performance | Deterministic control plane; mock-mode reproducibility; fail-safe defaults for unknown actions (#2); injection guardrails (#7) |
| Art. 10 — Data governance | Relevant data-quality / protection handling | PII redaction pre-LLM and pre-persistence (#6) |
| Art. 26 — Deployer obligations | Monitor operation, keep logs, inform affected persons | KPI dashboard + audit trail as the monitoring substrate |

*This is a demo system: the mapping shows where each obligation would be
satisfied architecturally, not a certification claim.*

## ISO/IEC 42001 (AI management systems)

The control inventory above doubles as an AI management system's operational
layer: policy documents (the YAML sets), defined responsibilities (identity
registry), operational monitoring (KPIs), and continual-improvement evidence
(audit chain + trace history).
