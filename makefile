
help:
	@echo "Common commands:"
	@echo "  make venv      - create virtualenv"
	@echo "  make install   - install runtime deps"
	@echo "  make dev       - install runtime + dev deps"
	@echo "  make run       - start FastAPI app (uvicorn)"
	@echo "  make lint      - run black (check only) & flake8"
	@echo "  make format    - auto-format with black"
	@echo "Testing commands:"
	@echo "  make test      - run all tests except e2e"
	@echo "  make test-e2e  - run e2e tests (requires kind cluster)"
	@echo "Security commands:"
	@echo "  make security  - run code & dependency security checks"

VENV ?= .venv

# Fallbacks for POSIX systems
ifeq ($(OS),Windows_NT)
    PYTHON = "$(VENV)\Scripts\python.exe"
    PIP = "$(VENV)\Scripts\pip.exe"
    ACTIVATE = "$(VENV)\Scripts\activate"
else
    PYTHON = $(VENV)/bin/python
    PIP = $(VENV)/bin/pip
    ACTIVATE = source $(VENV)/bin/activate
endif

.PHONY: help venv install dev run lint format test coverage clean security security-tools

venv:
ifeq ($(OS),Windows_NT)
	if not exist "$(VENV)\Scripts\python.exe" python -m venv "$(VENV)"
else
	@[ -x "$(VENV)/bin/python" ] || python -m venv "$(VENV)"
endif

install: venv
	$(PIP) install -r requirements.txt

dev: install
	$(PIP) install -r requirements-dev.txt

run:
	$(PYTHON) -m uvicorn app.main:app --reload --port 8000

lint:
	$(PYTHON) -m pip install black flake8 --quiet
	$(PYTHON) -m black --check app tests
	$(PYTHON) -m flake8 app tests

format:
	$(PYTHON) -m black app tests

test:
	$(PYTHON) -m pytest tests --ignore=tests/test_e2e.py --cov=app --cov-report=term-missing --cov-fail-under=80 -v

test-logs:
	$(PYTHON) -m pytest tests --ignore=tests/test_e2e.py --cov=app --cov-report=term-missing --cov-fail-under=80 -v --log-cli-level=INFO

coverage:
	$(PYTHON) -m pytest tests --ignore=tests/test_e2e.py --cov=app --cov-report=html

clean:
	rm -rf __pycache__ .pytest_cache .mypy_cache .coverage htmlcov
	rm -rf $(VENV)

# Installs local security tooling into the venv
security-tools: venv
	"$(PIP)" install --quiet bandit pip-audit

# Runs code and dependency security checks; bandit only fails on high confidence / high severity issues
security: security-tools
	@echo Running Bandit...
	"$(PYTHON)" -m bandit -q -r app -x tests -lll -iii
	@echo Running pip-audit on requirements.txt...
	"$(PYTHON)" -m pip_audit -r requirements.txt --strict
	@echo Running pip-audit on requirements-dev.txt...
	"$(PYTHON)" -m pip_audit -r requirements-dev.txt --strict
	@echo Running pip-audit on requirements-e2e.txt...
	"$(PYTHON)" -m pip_audit -r requirements-e2e.txt --strict


# -------- Docker Compose helpers (local dev) --------
COMPOSE ?= docker compose
PROJECT ?= rm
COMPOSE_FILES := -f docker-compose.yml

.PHONY: compose-up compose-up-d compose-down compose-logs app-logs db-logs app-exec db-psql compose-reset

# Bring up Postgres + app in foreground (Ctrl+C to stop)
compose-up:
	$(COMPOSE) $(COMPOSE_FILES) -p $(PROJECT) up --build

# Same, but detached
compose-up-d:
	$(COMPOSE) $(COMPOSE_FILES) -p $(PROJECT) up -d --build

# Stop & remove containers (keeps DB volume)
compose-down:
	$(COMPOSE) $(COMPOSE_FILES) -p $(PROJECT) down

# Tail combined logs
compose-logs:
	$(COMPOSE) $(COMPOSE_FILES) -p $(PROJECT) logs -f --tail=200

# Tail only app or db logs
app-logs:
	$(COMPOSE) $(COMPOSE_FILES) -p $(PROJECT) logs -f --tail=200 app

db-logs:
	$(COMPOSE) $(COMPOSE_FILES) -p $(PROJECT) logs -f --tail=200 db

# Run a command inside the app container (e.g., make app-exec CMD="pytest -q")
CMD ?= echo "No CMD specified. Try: make app-exec CMD=\"pytest -q\""
app-exec:
	$(COMPOSE) $(COMPOSE_FILES) -p $(PROJECT) exec app /bin/sh -lc '$(CMD)'

# Quick psql shell to the db (requires psql client inside the container)
db-psql:
	$(COMPOSE) $(COMPOSE_FILES) -p $(PROJECT) exec -e PGPASSWORD=secret db psql -U rmuser -d rm

# Nuke containers + volumes (DESTROYS DB DATA)
compose-reset:
	$(COMPOSE) $(COMPOSE_FILES) -p $(PROJECT) down -v


#--- kind helpers ---

# -------- OS switch (Windows vs POSIX) --------
ifeq ($(OS),Windows_NT)
  IS_WINDOWS := 1
  PYTHON_E2E = $(VENV)/Scripts/python.exe
else
  IS_WINDOWS := 0
  PYTHON_E2E = $(VENV)/bin/python
endif

# E2E test targets
.PHONY: e2e-test e2e-start-portforward e2e-stop-portforward

# Store Python path properly for Windows
E2E_PYTHON := $(subst /,\,$(PYTHON))

ifeq ($(OS),Windows_NT)
# Windows: Use PowerShell jobs to manage port-forward
e2e-start-portforward:
	@powershell -Command "Write-Host 'Starting port-forward...'; $$job = Start-Process kubectl -ArgumentList '-n', 'ingress-nginx', 'port-forward', 'svc/ingress-nginx-controller', '8080:80' -NoNewWindow -PassThru; Set-Content -Path '.port-forward-job' -Value $$job.Id; Write-Host 'Waiting for port to be ready...'; $$maxAttempts = 30; $$attempt = 0; while ($$attempt -lt $$maxAttempts) { if ((Test-NetConnection -ComputerName 127.0.0.1 -Port 8080 -WarningAction SilentlyContinue).TcpTestSucceeded) { Write-Host 'Port-forward is ready!'; exit 0 }; Start-Sleep -Seconds 1; $$attempt++; Write-Host 'Still waiting... (attempt $$attempt)' }; Write-Host 'Port-forward failed to start' -ForegroundColor Red; exit 1"

e2e-stop-portforward:
	@powershell -Command "Write-Host 'Cleaning up port-forward...'; if (Test-Path .port-forward-job) { $$id = Get-Content .port-forward-job; Write-Host ('Found process id: ' + $$id); $$process = Get-Process -Id $$id -ErrorAction SilentlyContinue; if ($$process) { Write-Host 'Stopping process...'; Stop-Process -Id $$id -Force; Write-Host 'Process stopped.' }; Remove-Item .port-forward-job; Write-Host 'Temporary file removed.' } else { Write-Host 'No port-forward file found.' }"
else
# Unix: Use background process with pid file
e2e-start-portforward:
	@kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8080:80 & echo $$! > .port-forward.pid
	@sleep 3

e2e-stop-portforward:
	@if [ -f .port-forward.pid ]; then kill $$(cat .port-forward.pid); rm .port-forward.pid; fi
endif

test-e2e: venv e2e-start-portforward
	$(PIP) install -r requirements-e2e.txt
	$(PYTHON) -m pytest "tests/test_e2e.py" -v -m e2e || ($(MAKE) e2e-stop-portforward && exit 1)
	$(MAKE) e2e-stop-portforward

# ---- timeouts (define once, no trailing spaces) ----
WAIT_SECS ?= 300
WAIT_SECS := $(strip $(WAIT_SECS))
WAIT_DUR  := $(WAIT_SECS)s


# ---- Configurable knobs ----
KIND            ?= kind
KIND_CLUSTER    ?= rm

KUBECTL         ?= kubectl
HELM            ?= helm
DOCKER          ?= docker

APP_NS          ?= rm
INGRESS_NS      ?= ingress-nginx

IMG_NAME        ?= rickmorty-sre-demo
IMG_TAG         ?= latest
IMG_REF         ?= $(IMG_NAME):$(IMG_TAG)

RICKMORTY_CHART ?= ./deploy/helm/rickmorty

.PHONY: kind-up kind-down logs port-forward _wait-core _wait-ingress _wait-admission-endpoints

kind-up:
	@$(info Creating kind cluster '$(KIND_CLUSTER)'...)
	$(KIND) create cluster --name $(KIND_CLUSTER)

	@$(info Installing ingress-nginx (controller + admission webhook)...)
	$(KUBECTL) apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

	@$(info Updating Helm dependencies...)
	cd $(RICKMORTY_CHART) && $(HELM) dependency update

	@$(info Waiting for core components...)
	$(MAKE) _wait-core
	@$(info Waiting for ingress-nginx to be fully ready...)
	$(MAKE) _wait-ingress

	@$(info Applying ingress-nginx config and restarting controller...)
	$(KUBECTL) apply -f ./deploy/shim/ingress-nginx-configmap.yaml
	$(KUBECTL) -n $(INGRESS_NS) rollout restart deploy/ingress-nginx-controller
	$(KUBECTL) -n $(INGRESS_NS) rollout status deploy/ingress-nginx-controller --timeout=180s

	@$(info Installing metrics-server...)
	$(HELM) repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
	$(HELM) repo update
	$(HELM) upgrade --install metrics-server metrics-server/metrics-server \
		-n kube-system --create-namespace \
		-f ./deploy/shim/metrics-server-args-patch.yaml --wait --timeout 5m

	@$(info Building app image $(IMG_REF)...)
	$(DOCKER) build -t $(IMG_REF) .
	$(KIND) load docker-image $(IMG_REF) --name $(KIND_CLUSTER)

	@$(info Installing app chart and waiting for resources...)
	$(HELM) repo add bitnami https://charts.bitnami.com/bitnami
	$(HELM) repo update
	$(HELM) upgrade --install rm $(RICKMORTY_CHART) \
		-n $(APP_NS) --create-namespace \
		--set postgresql.enabled=true \
		--set image.repository=$(IMG_NAME) \
		--set image.tag=$(IMG_TAG) \
		--wait --timeout 10m

	@echo "✅ kind cluster '$(KIND_CLUSTER)' and app are up."
	@echo "   - Tail app logs:      make logs"
	@echo "   - Port-forward NGINX: make port-forward"
	@echo "   - Run e2e tests:		make e2e-test (automatically handles port-forward)"
	@echo "   - Tear-down cluster: 	make kind-down"

# Be forgiving during early boot; ignore failures with leading '-'
_wait-core:
	-$(KUBECTL) -n kube-system rollout status deploy/coredns --timeout=$(WAIT_DUR)
	-$(KUBECTL) wait --for=condition=Ready pods -n kube-system -l k8s-app=kube-dns --timeout=$(WAIT_DUR)
	$(KUBECTL) get nodes
	-$(KUBECTL) wait --for=condition=Ready node --all --timeout=$(WAIT_DUR)

_wait-ingress:
	@$(info Waiting for ingress-nginx controller rollout...)
	$(KUBECTL) -n $(INGRESS_NS) rollout status deploy/ingress-nginx-controller --timeout=$(WAIT_DUR)
	@$(info Waiting for admission jobs (create/patch)...)
	-$(KUBECTL) -n $(INGRESS_NS) wait --for=condition=complete job/ingress-nginx-admission-create --timeout=$(WAIT_DUR)
	-$(KUBECTL) -n $(INGRESS_NS) wait --for=condition=complete job/ingress-nginx-admission-patch --timeout=$(WAIT_DUR)
	@$(info Waiting for admission service endpoints...)
	$(MAKE) _wait-admission-endpoints

# Cross-platform wait for Service endpoints to be populated
_wait-admission-endpoints:
ifeq ($(IS_WINDOWS),1)
	@powershell -NoProfile -Command "$$deadline=(Get-Date).AddSeconds($(WAIT_SECS)); while((Get-Date) -lt $$deadline){ $$ep=kubectl -n $(INGRESS_NS) get endpoints ingress-nginx-controller-admission -o json 2>$$null | ConvertFrom-Json; if($$ep -and $$ep.subsets -and $$ep.subsets.Count -gt 0 -and $$ep.subsets[0].addresses -and $$ep.subsets[0].addresses.Count -gt 0){ Write-Host 'admission endpoints ready'; exit 0 }; Start-Sleep -Seconds 3 }; Write-Error 'Timed out waiting for admission endpoints'; exit 1"
else
	@sh -lc 'i=0; while [ $$i -lt $(WAIT_SECS) ]; do ip=$$($(KUBECTL) -n $(INGRESS_NS) get endpoints ingress-nginx-controller-admission -o jsonpath="{.subsets[0].addresses[0].ip}" 2>/dev/null); if [ -n "$$ip" ]; then echo "admission endpoints ready ($$ip)"; exit 0; fi; sleep 3; i=$$((i+3)); done; echo "Timed out waiting for admission endpoints"; exit 1'
endif

logs:
	$(KUBECTL) -n $(APP_NS) logs deploy/rickmorty-rm -c app -f

port-forward:
	$(KUBECTL) -n $(INGRESS_NS) port-forward svc/ingress-nginx-controller 8080:80

kind-down:
	$(KIND) delete cluster --name $(KIND_CLUSTER)

