# Deploying tinyclaw

Target stack: **Cloudflare Pages** (frontend) · **Render** (backend) ·
**Aiven for PostgreSQL** (state). Total cost at demo scale: one Render
container + one small Aiven PG + free Pages.

```
┌────────────────────┐        ┌──────────────── Render container ───────────────┐
│ Cloudflare Pages   │  HTTPS │ gateway (uvicorn :$PORT)  ← only public face     │
│ ui/dist (static)   │──────▶ │ intake :9101 · research :9102 · policy :9103    │
│ VITE_GATEWAY → API │  CORS  │ executor :9104 · orchestrator :9105 · runtime   │
└────────────────────┘        │            (all on container loopback)          │
                              └───────────────┬─────────────────────────────────┘
                                              │ TINYCLAW_DATABASE_URL
                                        ┌─────▼─────┐
                                        │ Aiven PG  │  approvals · audit chain ·
                                        └───────────┘  tasks · KPI state · posture
```

The dashboard is static and talks to the gateway API cross-origin; the CORS
allow-list comes from `TINYCLAW_CORS_ORIGINS` on the Render service.

---

## 1. Aiven for PostgreSQL

1. Create an Aiven account → **Create service → PostgreSQL** (e.g.
   `tinyclaw`, hobbyist plan is plenty for a demo; pick a region near your
   Render region).
2. When the service is up, open **Overview → Connection information** and
   copy the **Service URI** (`postgres://avnadmin:…@pg-….aivencloud.com:PORT/db?sslmode=require`).
3. Keep it secret — this goes into Render next. Aiven enforces TLS; the
   psycopg driver handles `sslmode=require` from the URI automatically.

## 2. Render (backend, the whole agent mesh)

The repo ships a Render Blueprint (`render.yaml`) that runs the Docker image
with `python -m tinyclaw.deploy` — one web service that supervises the five
A2A agents + the Agent Studio runtime on loopback and exposes only the
gateway, with `/api/health` as the health check.

1. Render dashboard → **New + → Blueprint** → select the `tinyclaw` repo.
2. It reads `render.yaml` and prompts for the unset values:
   * `TINYCLAW_DATABASE_URL` → paste the Aiven Service URI
   * `TINYCLAW_CORS_ORIGINS` → your Pages domain (from step 3; you can add
     it after the first Pages deploy and redeploys are automatic)
   * `TINYCLAW_APPROVAL_SECRET` / `TINYCLAW_INTERNAL_TOKEN` → auto-generated
3. Deploy. First boot creates the schema in Postgres automatically
   (no migrations to run).
4. Verify: `https://tinyclaw-api.onrender.com/api/health` →
   `{"ok": true, …}`.

Notes:
* **Real LLMs**: set `TINYCLAW_LLM_PROVIDER=openai` + `OPENAI_API_KEY`
  (or anthropic + `ANTHROPIC_API_KEY`). Default is the keyless mock.
* **Memory / both scenarios**: the deploy entrypoint runs the whole mesh in
  a single process — every agent app on a thread sharing one interpreter
  (~50MB total for both scenario meshes). That's what lets the free 512MB
  instance run `procurement,support` together. `TINYCLAW_SINGLE_PROCESS=0`
  switches to isolated one-process-per-agent (better crash isolation,
  ~10× memory) for bigger plans.
* **Durability**: approvals/audit live in Postgres and survive redeploys.
  The agents' A2A task stores are SQLite on the container's ephemeral disk —
  fine for a demo; add a Render disk and point `TINYCLAW_TASK_STORE_DIR`-
  style storage there if you want task parks to survive redeploys too.
* **Free/starter tier cold starts**: the first request after sleep takes a
  few seconds while the mesh wakes.

## 3. Cloudflare Pages (frontend)

1. Cloudflare dashboard → **Workers & Pages → Create → Pages → Connect to
   Git** → select the `tinyclaw` repo.
2. Build settings:
   * **Root directory**: `ui`
   * **Build command**: `npm ci && npm run build`
   * **Build output directory**: `dist`
3. Environment variables (Settings → Variables):
   * `VITE_GATEWAY` = `https://tinyclaw-api.onrender.com` (your Render URL)
4. Deploy. Your dashboard is live at `https://<project>.pages.dev`
   (custom domain optional).
5. Put that Pages URL into Render's `TINYCLAW_CORS_ORIGINS` so the browser
   is allowed to call the API.

No-rebuild override: open the deployed dashboard's console and run
`localStorage.setItem("tinyclaw.gateway", "https://tinyclaw-api.onrender.com")`
to repoint the bundle at a different backend without redeploying.

## 4. First run

The deployed environment starts empty by design. Either use **Playground**
in the dashboard (samples are one click), or seed the full demo from
anywhere:

```bash
curl -X POST https://tinyclaw-api.onrender.com/api/playground/submit \
  -H 'content-type: application/json' \
  -d '{"scenario":"procurement","requests":[
    {"title":"chairs","requester":"ops@acme.test","vendor":"Acme Office Supply",
     "description":"24 chairs","amount":12400,"cost_center":"CC-1180"}]}'
```

Then approve it in the dashboard's **Approvals** tab and watch the KPIs,
audit chain, and (with the OTLP endpoint set) traces light up.

## 5. LLM observability (Langfuse Cloud — recommended, free)

The deployed mesh can't run self-hosted Langfuse (512MB), but it doesn't need
to: Langfuse Cloud hosts the backend and accepts OTLP over the internet.

1. Sign up at [cloud.langfuse.com](https://cloud.langfuse.com) → create a
   project → **Settings → API Keys → Create new API keys** (public + secret).
2. On the Render service, add two environment variables:
   * `OTEL_EXPORTER_OTLP_ENDPOINT` = `https://cloud.langfuse.com/api/public/otel/v1/traces`
   * `OTEL_EXPORTER_OTLP_HEADERS` = `authorization=Basic <base64>` where
     base64 is `printf 'pk-lf-…:sk-lf-…' | base64` (public key : secret key)
3. Redeploy. Submit any playground request — the full trace tree (gateway →
   orchestrator → specialists, with every LLM prompt/completion and token
   count) appears in Langfuse within seconds.
4. Optional: rebuild Pages with `VITE_LANGFUSE_URL=https://cloud.langfuse.com`
   to turn the dashboard's trace link into a direct deep-link.

The same two variables work for local runs (`export` them before `./dev.sh`).
Free tier allows 50k observations/month — a demo uses a tiny fraction.

## Security notes (read before sharing the URL)

This is a portfolio demo, hardened where it matters and honest where it
isn't:

* The **internal** endpoints (`/internal/*`) are authenticated with a static
  bearer token — generated per-deploy by the blueprint. In a real deployment
  they would not be internet-routable at all (network policy / private
  service).
* The public dashboard APIs (`/api/*`) have **no user auth** — anyone with
  the URL can approve a (simulated) purchase. Add an identity layer in front
  (Cloudflare Access is the natural fit here) before showing it to
  strangers.
* Rotate `TINYCLAW_APPROVAL_SECRET` and `TINYCLAW_INTERNAL_TOKEN` if they
  ever leak; permits issued under an old secret stop verifying.

## Local dry-run of the deployed shape

```bash
PORT=8090 uv run python -m tinyclaw.deploy
# gateway on :8090, agents on loopback — same topology as Render, zero Docker
```
