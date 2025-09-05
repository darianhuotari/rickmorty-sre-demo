# RICKMORTY-SRE-DEMO

A demo application that integrates with the [Rick and Morty API](https://rickandmortyapi.com/documentation/#rest).

---

## 🚀 Overview

- **FastAPI** service providing REST endpoints.
- **Database caching** with Postgres (default in Kind) or SQLite (fallback in local dev).
- **Refresh pipeline** to keep data up to date.
- **Health checks** for liveness and readiness.
- **Metrics** endpoint for Prometheus scraping.
- **Tests**: unit / integration, end-to-end (Kind cluster).
- **CI/CD**: GitHub Actions with linting, tests, security scanning, e2e validation, and Docker Hub publishing.
- ✅ [Take-home checklist here!](checklist.md)
- 🏭 [Production discussion items here!](production_discussion.md)

---

## ✨ Features

- REST API with cached responses from the Rick & Morty API.
- Database backend (Postgres or SQLite).
- Health endpoints:
  - `/healthz` – liveness
  - `/healthcheck` – deep health (DB + upstream probe)
- `/metrics` – Prometheus exposition (request counts/latency, cache metrics, health gauges).
- Unit, integration, and e2e test coverage.
- Security checks:
  - [`bandit`](https://bandit.readthedocs.io/)
  - [`pip-audit`](https://pypi.org/project/pip-audit/)
  - [`trivy`](https://aquasecurity.github.io/trivy/)

---

## 📚 API

### `GET /characters`

**Query params**
- `sort`: `id` | `name` (default `id`)
- `order`: `asc` | `desc` (default `asc`)
- `page`: integer ≥ 1 (default `1`)
- `page_size`: 1–100 (default `20`)

**Responses**
- **200** – `CharactersPage`
- **422** – invalid query shape (FastAPI validation)
- **400** – semantic client error from CRUD (e.g., invalid sort that slipped past validation)
- **503** – transient DB unavailable (includes `Retry-After`)
- **500** – server/DB error

**Example**
```bash
curl "http://localhost:8000/characters?sort=name&order=asc&page=1&page_size=10"
```

### Health

- `GET /healthz` – in-process liveness
- `GET /healthcheck` – deep health (upstream + DB) with `status: ok|degraded`

### Metrics

- `GET /metrics` – Prometheus exposition
  - `http_requests_total{path,method,status}`
  - `http_request_latency_seconds_bucket|sum|count{path,method}`
  - `page_cache_hits_total`, `page_cache_puts_total`, `cache_errors_total{cache,op}`
  - `db_ok`, `upstream_ok`, `last_refresh_age_seconds`

> Tip: tune histogram buckets around your SLOs (e.g., 0.01, 0.05, 0.1, 0.2, 0.5, 1, 2.5).

---

## ⚙️ Configuration

Environment variables:

| Var | Default | Purpose |
|---|---:|---|
| `DATABASE_URL` | Postgres in Kind / SQLite for tests | SQLAlchemy async URL |
| `DB_POOL_SIZE`, `DB_MAX_OVERFLOW` | unset | Postgres pool tuning |
| `REFRESH_WORKER_ENABLED` | `1` | Enable periodic refresher task |
| `REFRESH_INTERVAL` | `300` | Seconds between refresh checks |
| `REFRESH_TTL` | `600` | Consider data stale after N seconds |
| `CACHE_TTL` | `300` | Per-pod page cache TTL (seconds) |
| `MAX_RETRIES` | `5` | Upstream API retries |
| `REQUEST_TIMEOUT` | `10.0` | Upstream HTTP timeout (seconds) |
| `LOG_LEVEL` | `INFO` | App log level |
| `PROMETHEUS_MULTIPROC_DIR` | unset | Enable Prom client multiprocess mode (see below) |

**Multiprocess note:** if you run multiple workers per pod (e.g., Gunicorn + Uvicorn workers), set `PROMETHEUS_MULTIPROC_DIR` and use the Prometheus client’s multiprocess mode.

---

## 🧠 Behavior & Error Semantics

- **422** from FastAPI validation (before handler).
- **400** only for known, deterministic client errors raised by CRUD (e.g., `ValueError`).
- **503** for transient DB issues (`OperationalError`, `InterfaceError`, `TimeoutError`) with `Retry-After`.
- **500** for unexpected server/DB errors (`ProgrammingError`, `DatabaseError`, other exceptions).

**Caching**
- Per-pod LRU+TTL page cache around `/characters` with singleflight locking.
- Simple per-pod upstream API cache to reduce upstream load
- Cache failures are non-fatal (requests fall back to DB; metrics increment `cache_errors_total`).

---

## 🛠 Getting Started

### Prerequisites
- Python 3.12+
- [Docker](https://docs.docker.com/get-docker/)
- [Kind](https://kind.sigs.k8s.io/) (for Kubernetes testing)
- `make`

### Local Development (venv)
```bash
make dev       # install runtime + dev deps
make run       # start FastAPI app on http://localhost:8000
uvicorn app.main:app --reload --port 8000 --workers 1 # sometimes it's hard to kill the parent process
```

### Kubernetes (Kind)
```bash
make kind-up      # create cluster + deploy app
make test-e2e     # run e2e tests
make kind-down    # cleanup cluster
```

---

## ✅ Tests & Quality

```bash
make lint         # black / flake8 lint
make format       # black format

make test         # unit / integration tests; outputs coverage report
make test-e2e     # e2e tests; requires kind cluster to be online

make security     # Bandit / pip-audit scans
```

**Testing notes**
- Unit/integration use SQLite in-memory(unit tests) or on-disk(integration tests) and override dependencies.

---

## 🔎 Observability Quickstart (PromQL)

```promql
# p95 latency per path (5m window)
histogram_quantile(
  0.95,
  sum by (le, path) (rate(http_request_latency_seconds_bucket[5m]))
)

# Request rate per path
sum by (path) (rate(http_requests_total[5m]))

# Cache error rate
sum(rate(cache_errors_total[5m]))
```

---

## 🔄 CI/CD

GitHub Actions pipeline includes:
- Lint + unit tests on PR and main
- Security scan (Bandit + pip-audit) on PRs
- End-to-end tests in Kind when relevant paths change
- Trivy image scan before publishing to Docker Hub
- Docker images tagged by commit SHA and `latest`

---

## 📦 Deployment

- Helm chart: `deploy/helm/rickmorty`
- Example:
  ```bash
  make kind-up    # Stand up a fresh cluster

  make kind-down  # Tear down the cluster
  ```
- **Secrets**
  - Database credentials (dev defaults provided for Kind)
  - Override with Vault or Kubernetes Secrets in production

---

## 🐳 Docker

```bash
docker build -t rickmorty-sre-demo:dev .
docker run --rm -p 8000:8000 rickmorty-sre-demo:dev
# browse http://localhost:8000/docs
```

---

## ☸️ Probes & Scaling (Kubernetes)

- Probes (defaults align with app behavior):
  - `readinessProbe`: `/healthcheck`
  - `livenessProbe`: `/healthz`
  - `startupProbe`: `/healthcheck`
- HPA: consider cache characteristics—fewer, warmer replicas often mean higher hit ratios (tune per load).

---

## 🤝 Contributing

- Code style: Black, Flake8
- Run `make lint` before pushing
- PRs must pass lint, tests, and security checks

---

## 🙏 Credits

[Rick and Morty API](https://rickandmortyapi.com/documentation/#rest) for the data source.


### Load testing:

Note: set a host file entry for rickmorty.local to 127.0.0.1 for SNI to work

### Clean up any previous pod(s)
kubectl -n rm delete pod fortio --ignore-not-found

### Start load test: entrypoint is `fortio`, so first arg is `load`
kubectl -n rm run fortio --restart=Never --image=fortio/fortio:latest_release -- \
  load -qps 100 -c 10 -t 5m \
  "http://rickmorty.local:8000/characters?page=1&page_size=1&sort=id&order=asc"

### Stream results
kubectl -n rm logs -f pod/fortio

### Cleanup
kubectl -n rm delete pod fortio

### Same but in docker (replace names /ports as required):
docker run fortio/fortio load -qps 100 -c 10 -t 5m "http://rickmorty.local:8080/characters?page=1&page_size=50&sort=id&order=asc"