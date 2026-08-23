# LinkedIn post — draft

---

I spent the last weeks building 🦞 **tinyclaw** — a tiny but *governed* multi-agent platform — to answer one question:

**What does it actually take to let AI agents act, not just chat, in an enterprise?**

My answer: four non-negotiables.

**1️⃣ Agents must speak a standard protocol.**
Every handoff runs on the Linux Foundation's A2A protocol — Agent Card discovery, JSON-RPC, task lifecycle. No bespoke message buses that die with the demo.

**2️⃣ Observability is the product.**
One distributed trace follows a request across 5 agents and every LLM call (OpenTelemetry → self-hosted Langfuse). If you can't see the decision, you can't trust the decision.

**3️⃣ Autonomy is earned per action, not granted globally.**
Every consequential action is risk-tiered. Small purchases auto-execute (still audited). Large ones park the task in the protocol's own `input-required` state until a human decides. Sanctioned vendors and prompt-injection attempts are hard-denied — before any model call.

**4️⃣ The platform governs itself.**
Deploying a new agent with high-risk tools? That deployment itself needs human approval. Even a fully compromised orchestrator can't force execution — the executor only accepts signed, task-bound permits.

The demo ships a procurement scenario: intake → research → policy → human approval → PO execution, with a hash-chained tamper-evident audit log mapped to NIST AI RMF and EU AI Act articles.

It runs end-to-end with zero API keys (deterministic mock mode — CI proves it), or switch to OpenAI/Anthropic with one env var.

🔗 github.com/your-handle/tinyclaw

Lesson for product leaders: the moat isn't the agents — it's the governance, observability, and human-oversight scaffolding around them. That's what enterprises buy.

#AIAgents #A2A #AIGovernance #LLMOps #ProductManagement

---

## Notes for posting

- Attach 2–3 dashboard screenshots: **Overview** (KPIs + live pipeline), **Approvals** (a parked decision with its context packet), **Governance** (hash chain verifying green).
- Even better: a 30s screen recording of the approve-and-execute round trip.
- If you ran the Langfuse profile, add the end-to-end trace waterfall as the final slide.
