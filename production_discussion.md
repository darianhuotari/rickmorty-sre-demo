### Prod discussion points (in no particular order):

Ensure connection draining is implemented to allow client requests time to complete during scale-down or deployment operations

We should capture / log request IDs for tracing / correlation

Docker-compose exists, but we mostly test via Kind or running the app on the CLI; an in-memory DB is used in unit-tests and a file-based DB is used everywhere else, except in Kind

Grafana dashboard JSON: basic / sample json available [here](requirements.txt)

Prometheus alert rules: basic / sample alert definitions available [here](requirements.txt)

Distributed tracing (OpenTelemetry/Jaeger): is awesome in production systems! Skipped due to time but would love to chat about tracing options.  

GitOps (ArgoCD) / multi-repo setup: normally we'd separate the infrastructure and application repo(s); would love to discuss different strategies.

App promotion strategies (tag-based approach vs image version bumper in monorepo / per repo, GitOps-style)

We serve stale data assuming DB is up and the upstream API is down; we also serve from cache temporarily (in production we would externalize these in-app caches to Redis or something similar)

We should require tests to pass + PR approval before allowing merges

We should build PDBs into the helm chart

Deep readiness probe trade-offs (shed load early, etc)

Proper secret management (Vault, external secret operator like AKV2K8S)

TLS (LetsEncrypt on cluster)

Could simplify tests / rely less on mocking - add better contract testing?

Implement chaos testing (either mocked or real).

Implement load testing (how many RPS can 1 pod handle with various layers of cache, should we split into multiple services, etc); want to do this on prod-like infra to get a feel for numbers

Leverage suites like hypothesis, mutation tests (cosmic-ray, mutmut) to help catch edge cases.

Force TLS connection(s) to DB; consider DB HA strategies (prefer availability over consistency, most likely)

Single worker uvicorn should be appropriate for IO heavy endpoints; would maybe use more if we had a CPU-heavy workload

Security policies on repo (branch protection, contributors.md, security scanning)