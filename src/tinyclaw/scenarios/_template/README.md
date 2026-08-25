# Scenario pack template

Copy this directory to add a governed scenario to tinyclaw — the core needs
zero changes. The fastest path: copy `../support` (it's the leanest pack),
then work through this checklist:

1. **`scenario.yaml`** — name, description, orchestrator/agent URLs on a
   fresh port block (procurement uses 9101-9105, support 9201-9205 — take
   93xx). Register `policies/<name>.yaml` (the editable, DB-backed set),
   `policies/risk.yaml`, `policies/identity.yaml`, `policies/hooks.yaml`.
2. **`urls.py`** — env-overridable URLs, one per agent (see support's).
3. **`policies/<name>.yaml`** — the policy rules. Follow the `.tierN` rule-id
   convention if you want the autonomy dial (postures) to apply. Conditions
   may use `==`, `!=`, `> >= < <=`, `in`, `contains`, `exists`, `matches`.
4. **`policies/risk.yaml`** — action registry with risk classes
   (`auto` / `threshold` / `always_human` / `blocked`); unknown actions
   fail safe to always-human.
5. **`policies/hooks.yaml`** — boundary hooks for your domain (block/redact
   on outbound messages).
6. **`tools/`** — mock systems of record (vendor registries, orders…).
7. **`agents/`** — five executors: intake (LLM extraction + guardrail
   flags), research (tool enrichment), policy (evaluate + route — compile
   the policy set per evaluation via `/api/policy-sets/<name>` for
   hot-reload), executor (permit-verified action), orchestrator (plan
   artifact → hops → permit → park-or-execute). Support's agents are the
   canonical reference; they are deliberately near-identical to
   procurement's.
8. **`__main__.py`** — launcher (spawn the five agents, SIGTERM-safe).
9. **`seed.py`** — one request per governance path (auto, tier-2/3 human,
   deny, guardrail catches).
10. **`deploy.py`** — add your module list to `SCENARIOS` so single-container
    deploys can run it; add services to `docker-compose.yml` if you use it.

The gateway discovers the pack automatically via the `scenario.yaml` glob.
Restart the gateway after dropping the pack in — and on cloud deploys, packs
live in the image (runtime scenario creation is on the Phase 3 roadmap).
