## Rough production launch roadmap (open to discussion!):

## Day-0 Gates (before prod)
**Goal:** Secure, reproducible, operable baseline.

- [ ] **Pre-production infrastructure deployed**  
  Deploy (as code via CI/CD) a pre-prod environment that mirrors prod (config parity, smaller SKUs as policy allows). Seed anonymized data where needed.  
  **Done when:** pre-prod stands up automatically and closely matches prod.

- [ ] **Green CI required before merge**  
  Lint, unit tests, type checks, security scans (Bandit/pip-audit), container scan (Trivy).  
  **Repo policy:** branch protection (PR review required), status checks required, contributors.md, Dependabot.  
  **Done when:** PRs cannot merge without passing checks + review.

- [ ] **Image versioning & provenance**  
  Push `:vX.Y.Z` and `:sha`; never deploy `:latest`. Attach SBOM if available.  
  **Done when:** deployments reference immutable tags and K8s shows exact image SHAs.

- [ ] **Ingress TLS**  
  cert-manager + Let’s Encrypt; redirect HTTP→HTTPS.  
  **Done when:** TLS certs auto-provision/renew; security headers set by ingress (where applicable).

- [ ] **Secrets strategy selected and wired**  
  Choose one: External Secrets Operator (ESO), SOPS, or Vault. App remains agnostic of secret origins.  
  **Done when:** app consumes K8s Secrets populated by chosen mechanism; no plaintext secrets in repo.

- [ ] **Structured logging + Request IDs**  
  Keep existing basic logs but make them **JSON** and include `request_id`/`trace_id`, user agent, latency, route, status.  
  **Done when:** a single request can be followed end-to-end across components.

- [ ] **Repository security policies**  
  Finalize branch protection, CODEOWNERS, SECURITY.md.  
  **Done when:** policies enforced automatically on main.

- [ ] **Pre-prod promotion gate**  
  Automatic smoke tests, a short load test, and a **rollback rehearsal** must pass before prod.  
  **Done when:** promotion to prod is blocked unless all gates pass.

---

## Week-1 Hardening (SLO/SLA safety)
**Goal:** Graceful rollouts, resilience to node/app churn, and early shedding under dependency trouble.

- [ ] **Connection draining / graceful shutdown**  
  App honors SIGTERM with readiness flip + in-flight drain; rolling updates use surge-only.  
  **Done when:** no 5xx spike on rollout; graceful shutdown verified.

- [ ] **Pod Disruption Budget (PDB)**  
  Prevent thundering-herd evictions during upgrades/maintenance. Ensure this doesn't block rollouts (i.e. 2 replicas without HPA and minAvailable: 2)
  **Done when:** voluntary disruptions never drop below healthy quorum.

- [ ] **GitOps split with Argo CD**  
  Separate **app repo** (code, optionally charts) and **infra repo** (clusters, Argo apps, policies, optionally charts). Leverage app-of-apps pattern if deploying many microservices.
  **Promotion:** tag-based or Argo Image Updater; avoid manual image bumps.  
  **Done when:** staging→prod promotion is a tag/PR, not kubectl.

- [ ] **(If feasible) TLS to DB now**  
  If operator/managed DB supports it readily, enable in Week-1; otherwise keep in Month-1.  
  **Done when:** DB connections require TLS.

---

## Weeks 2–3: Observability & Ops
**Goal:** Fast detection, consistent promotion, and production-like testing fidelity.

- [ ] **/metrics + first 3 alerts**  
  Expose Prometheus metrics; alerts for **error rate**, **p99 latency**, **crashloops** (see sample rules).  
  **Done when:** alerts fire in pre-prod; thresholds tuned to SLOs.

- [ ] **SLOs & burn-rate alerts**  
  Define availability and latency SLOs; add **multi-window burn-rate** alerts (e.g., 2h & 24h).  
  **Done when:** pages align with SLO impact, not noise.

- [ ] **Dashboards (Grafana)**  
  Publish dashboards (see sample JSON) for traffic, latency, errors, resource use, cache hit-rate.  
  **Done when:** on-call can answer “Is it the app, upstream, or infra?” within minutes.

- [ ] **Secrets key rotation policy**  
  Define rotation cadence/policy in ESO/Vault/SOPS and verify reload.  
  **Done when:** rotation event observed without app downtime.

- [ ] **Readiness strategy**  
  Deep readiness probe that degrades when critical deps are impaired; shed load early. Note that the app already has a deep readiness probe but we'd want to further test / verify we shed load under the right scenarios.
  **Done when:** upstream/DB failures reflect in readiness, protecting tail latency.

- [ ] **Progressive delivery**  
  Canary or blue/green (Argo Rollouts), with metric gates (AnalysisTemplates: no increasing error rates, p99s) before 100% traffic.  
  **Done when:** bad releases auto-halt/rollback.

- [ ] **Integration fidelity**  
  Add **contract tests** to reduce mocking; **DB integration tests** on Kind/minikube; run **migrations (e.g., Alembic)** in CI or another singleton pattern. Keep docker-compose for dev; ensure parity with Kind.  
  **Done when:** critical paths covered by contract/integration tests; mocks minimized.

- [ ] **Runbooks & on-call**  
  Rollout rollback, cache flush policy, DB failover, trace/log triage checklists; on-call schedule defined.  
  **Done when:** runbooks exist and have been dry-run.

- [ ] **WAF (monitor-only) in pre-prod/staging**
  Enable WAF (cloud WAF or NGINX Ingress + ModSecurity/OWASP CRS) in **log-only** mode on pre-prod/staging.
  Exclude non-user paths (`/healthz`, `/metrics`), set request/URI/body size limits, start with CRS Paranoia Level 1.
  Stream logs to observability (dashboards + alerts) and manage config in Git.
  **Done when:** false-positive rate is low on representative load; necessary exceptions documented; dashboards and alerts exist.

---

## Months 1 & 2 Enhancements
**Goal:** Deep visibility, failure practice, and data-layer resilience.

- [ ] **Distributed tracing**  
  OTel SDK + OTLP exporter; Collector → Jaeger/Tempo/Cloud. Propagate `traceparent`; correlate logs↔traces via IDs.  
  **Done when:** a slow request shows per-span timings across services.

- [ ] **Chaos & load**  
  Chaos drills: DB down, pod kill, network jitter; practice runbooks.  
  Load tests with k6: RPS and p99 under cache layers; decide if service split is warranted.  
  **Done when:** SLOs hold at target load; bottlenecks have owners and plans.

- [ ] **Cache strategy**  
  Stale-while-revalidate backed by **Redis** (externalized cache). Serve stale if upstream is down; clear fallback rules.  
  **Done when:** cache survives pod restarts; hit rate and TTLs observable.

- [ ] **Persistence & durability** 
  Managed Postgres with TLS, **migrations**, **backups & PITR**; document **RPO/RTO**; test connection pooling.  
  **Done when:** restore drill completes within RTO; schema changes are migration-gated.

- [ ] **Data layer hardening / HA**  
  If TLS wasn’t enabled earlier, finish it now; add HA/failover plan and drill.  
  **Done when:** failover drill works and docs are current.

- [ ] **WAF in prod (canary → blocking)**
  Roll out WAF to prod behind a canary (e.g., 10% → 50% → 100%). Start in monitor-only, then switch to blocking once FP < agreed threshold and critical paths are tuned.
  Add basic bot/rate-limits as needed; alert on WAF block spikes; tie into progressive delivery gates for auto-halt/auto-rollback.
  **Done when:** blocking enabled at 100% with acceptable FP rate; rollback/runbook verified.

- [ ] **Baseline NetworkPolicies (default-deny + allowlist)**
  Apply namespace-scoped default-deny for **ingress & egress**, then allow only:
  • ingress from ingress controller (and monitoring) to app port(s)  
  • egress to DNS (kube-dns), Postgres/Redis (if used), OTLP/Prometheus endpoints, and required external APIs over 443  
  Include node-origin health probes if needed (node/cluster CIDR).
  **Done when:** expected traffic flows (app, probes, scraping) pass; blocked egress is observable; policies live in Git.


---

## Cross-Cutting Defaults & Ops Notes
- **Infrastructure resiliency:** multi-AZ plus `topologySpreadConstraints` and anti-affinity to keep replicas apart.  
- **Uvicorn workers:** single worker is fine for I/O-heavy endpoints; scale via replicas; add workers only for CPU-bound paths.  
- **Security scanning:** repo and image scanning as part of CI; periodic re-scans.  
- **Promotion strategy:** prefer immutable tags + GitOps; avoid manual edits in live clusters.  
- **Logging & IDs:** gateways inject `request_id` if absent; propagate across services.

---

## Backlog / Discussion Topics
- Simplify tests further by increasing contract/integration coverage; reduce brittle mocks.  
- Evaluate finer-grained services only if load tests justify it.  
- Expand alerting to include saturation (CPU/mem), work queue depth (in-flight requests), dependency-specific SLOs.  
- Consider policy-as-code (OPA/Gatekeeper) for cluster guardrails.
- eBPF observability vs mesh: evaluate **Cilium/Hubble** (eBPF observability, network policy) separately from a service mesh (mTLS, traffic policy, canaries). The latter may be unnecessary; the former could be useful.

---