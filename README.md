# RICKMORTY-SRE-DEMO


Todo:

Cleanup helm chart(s)?

connection draining to allow client requests time to complete?

Add DB TTL as env var / configurable via Helm

Dependency manager in CI

Note on using docker-compose & that an in-memory DB is used in unit-tests

Manually test behavior for invalid gets on /character; add unit / int tests if necessary

/metrics endpoint (with 1–2 custom metrics).

Grafana dashboard JSON.

Prometheus alert rules.

Distributed tracing (OpenTelemetry/Jaeger).

3 production-ready alerts (based on metrics endpoint, and optionally others)

Architecture

Documentation / readme updates


Prod discussion points:
Require tests to pass before allowing merges
PDB
Readiness probe trade-offs (shed load early, etc)
Secret management
Track request IDs via headers
TLS?
Simplify helm chart layout?



Run locally: uvicorn app.main:app --reload --port 8000

A demo application that integrates with the [Rick and Morty API](https://rickandmortyapi.com/documentation/#rest).

---

## 🚀 Overview

- **FastAPI** service providing REST endpoints.
- **Database caching** with Postgres (default in Kind) or SQLite (fallback in local dev).
- **Refresh pipeline** to keep data up to date.
- **Health checks** for liveness and readiness.
- **Tests**: unit + end-to-end (Kind cluster).
- **CI/CD**: GitHub Actions with linting, tests, security scanning, e2e validation, and Docker Hub publishing.

---

## ✨ Features

- REST API with cached responses from Rick & Morty API.
- Database backend (Postgres or SQLite).
- Health endpoints:
  - `/healthz` – liveness
  - `/healthcheck` – readiness
- Unit, integration, and e2e test coverage.
- Security checks:
  - [`bandit`](https://bandit.readthedocs.io/)
  - [`pip-audit`](https://pypi.org/project/pip-audit/)
  - [`trivy`](https://aquasecurity.github.io/trivy/)

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
```

### Kubernetes (Kind)
```bash
make kind-up      # create cluster + deploy app
make test-e2e     # run e2e tests
make kind-down    # cleanup cluster
```

### ✅ Tests & Quality
```bash
make lint         # black / flake8 lint
make format       # black format

make test         # unit / integration tests; outputs coverage report
make test-e2e     # e2e tests; requres kind cluster to be online (see Kubernetes (Kind) section)

make security     # Bandit / pip-audit scans
```

### 🔄 CI/CD

GitHub Actions pipeline includes:

- Lint + unit tests on PR and main.

- Security scan (Bandit + pip-audit) on PRs.

- End-to-end tests in Kind when relevant paths change.

- Trivy image scan before publishing to Docker Hub.

- Docker images tagged by commit SHA and main.

### 📦 Deployment

- Helm chart: deploy/helm/rickmorty

- Example:

<example here>

- Secrets:

    - Database credentials (default dev values provided for running in Kind).

    - Override with Vault or Kubernetes secrets in production.
    
### 🤝 Contributing

Code style: Black, Flake8

Run make lint before pushing.

PRs must pass lint, tests, and security checks.

### 🙏 Credits

[Rick and Morty API](https://rickandmortyapi.com/documentation/#rest) for the data source.


# Load test:
# Clean up any previous pod(s)
kubectl -n rm delete pod fortio --ignore-not-found

# Start load: entrypoint is `fortio`, so first arg is `load`
kubectl -n rm run fortio --restart=Never --image=fortio/fortio:latest_release -- \
  load -qps 800 -c 200 -t 5m \
  "http://rickmorty-rm:8000/characters?page=1&page_size=1&sort=id&order=asc"

# Stream results
kubectl -n rm logs -f pod/fortio

# Cleanup
kubectl -n rm delete pod fortio

Same but docker:
docker run fortio/fortio load -qps 100 -c 20 -t 5m "http://rickmorty.local:8080/characters?page=1&page_size=50&sort=id&order=asc"