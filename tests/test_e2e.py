"""End-to-end tests for the Rick and Morty API service running in Kubernetes.

These tests verify the service behavior when running in a real Kubernetes cluster.
They require a running Kind cluster with the service deployed (via 'make kind-up').

Usage:
1. Start cluster and deploy app: make kind-up
2. Run tests: make e2e-test
3. Clean up: make kind-down
"""

import pytest
import httpx
import time
from kubernetes import client, config
from kubernetes.client.rest import ApiException

# Constants
NAMESPACE = "rm"
SERVICE_NAME = "rickmorty-rm"
DEPLOYMENT_NAME = "rickmorty-rm"
INGRESS_HOST = "rickmorty.local"  # Must match ingress host in Helm values
INGRESS_PORT = 8080  # Port forwarded by 'make kind-up'


def get_service_url():
    """Get the service URL from Kubernetes ingress."""
    # Direct IP access - users only need hosts file for browser access
    return f"http://127.0.0.1:{INGRESS_PORT}"


def get_request_headers():
    """Get headers required for ingress routing."""
    # Set Host header for ingress routing
    return {"Host": INGRESS_HOST}


@pytest.fixture(scope="session")
def k8s_client():
    """Initialize the Kubernetes client."""
    try:
        config.load_kube_config()
    except Exception:
        config.load_incluster_config()
    return client.CoreV1Api()


@pytest.fixture(scope="session")
def apps_client():
    """Initialize the Kubernetes apps client for deployment operations."""
    try:
        config.load_kube_config()
    except Exception:
        config.load_incluster_config()
    return client.AppsV1Api()


def wait_for_deployment_ready(apps_client, namespace, name, timeout=300):
    """Wait for a deployment to be ready."""
    start = time.time()
    while time.time() - start < timeout:
        try:
            deployment = apps_client.read_namespaced_deployment(name, namespace)
            if (deployment.status.ready_replicas or 0) == deployment.spec.replicas:
                return True
        except ApiException:
            pass
        time.sleep(2)
    raise TimeoutError(f"Deployment {name} not ready after {timeout} seconds")


def wait_for_ingress_ready(timeout=300):
    """Wait for the ingress to be ready by polling the health endpoint."""
    url = f"{get_service_url()}/healthcheck"
    start = time.time()
    while time.time() - start < timeout:
        try:
            response = httpx.get(url, headers=get_request_headers())
            if response.status_code == 200:
                return True
        except Exception:
            pass
        time.sleep(2)
    raise TimeoutError(f"Ingress not ready after {timeout} seconds")


@pytest.fixture(scope="session", autouse=True)
def ensure_deployment_ready(apps_client):
    """Ensure the deployment is ready before running tests."""
    wait_for_deployment_ready(apps_client, NAMESPACE, DEPLOYMENT_NAME)
    wait_for_ingress_ready()


@pytest.mark.e2e
def test_service_health():
    """Test the service health endpoint through ingress."""
    url = f"{get_service_url()}/healthcheck"
    response = httpx.get(url, headers=get_request_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["status"] == "ok"
    assert data["db_ok"] is True
    assert data["upstream_ok"] is True
    assert data["character_count"] > 0


@pytest.mark.e2e
def test_characters_endpoint():
    """Test the characters endpoint through ingress."""
    url = f"{get_service_url()}/characters"
    response = httpx.get(url, headers=get_request_headers())
    assert response.status_code == 200
    data = response.json()
    assert data["total_count"] > 0
    assert len(data["results"]) > 0


@pytest.mark.e2e
def test_pagination_through_ingress():
    """Test pagination works correctly through ingress."""
    # Get first page
    url = f"{get_service_url()}/characters?page=1&page_size=5"
    response = httpx.get(url, headers=get_request_headers())
    assert response.status_code == 200
    page1 = response.json()
    assert len(page1["results"]) == 5

    # Get second page
    url = f"{get_service_url()}/characters?page=2&page_size=5"
    response = httpx.get(url, headers=get_request_headers())
    assert response.status_code == 200
    page2 = response.json()
    assert len(page2["results"]) == 5

    # Verify pages are different
    page1_ids = {char["id"] for char in page1["results"]}
    page2_ids = {char["id"] for char in page2["results"]}
    assert not page1_ids.intersection(page2_ids)


@pytest.mark.e2e
def test_kubernetes_resources(k8s_client, apps_client):
    """Verify Kubernetes resource configuration."""
    # Check deployment
    deployment = apps_client.read_namespaced_deployment(DEPLOYMENT_NAME, NAMESPACE)
    assert deployment.status.ready_replicas == deployment.spec.replicas

    # Check service
    service = k8s_client.read_namespaced_service(SERVICE_NAME, NAMESPACE)
    assert service.spec.type == "ClusterIP"

    # Check pods
    pods = k8s_client.list_namespaced_pod(
        NAMESPACE,
        label_selector="app.kubernetes.io/name=rickmorty,app.kubernetes.io/instance=rm",
    )
    assert len(pods.items) > 0

    # Verify all pods are running
    for pod in pods.items:
        assert pod.status.phase == "Running"

        # Check container resource limits
        container = pod.spec.containers[0]
        assert container.resources.limits is not None
        assert container.resources.requests is not None
