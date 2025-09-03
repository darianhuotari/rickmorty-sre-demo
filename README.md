# RICKMORTY-SRE-DEMO


Todo:

Cleanup helm chart(s)?

Add lightweight health endpoint for k8s probes (except maybe startup)

Add DB TTL as env var / configurable via Helm

Code security / quality checks in CI

Dependency manager in CI

lightweight image scan (Trivy)

Note on using docker-compose & that an in-memory DB is used

Architecture

Documentation / readme updates


Prod discussion points:
Require tests to pass before allowing merges
Secret management
Track request IDs via headers
TLS?
Simplify helm chart layout?



Run locally: uvicorn app.main:app --reload --port 8000



# Bring local kind cluster online:

kind create cluster --name rm

kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

kubectl apply -f .\deploy\shim\ingress-nginx-configmap.yaml
kubectl -n ingress-nginx rollout restart deploy/ingress-nginx-controller

helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm repo update

helm upgrade --install metrics-server metrics-server/metrics-server `
   -n kube-system --create-namespace `
   -f .\deploy\shim\metrics-server-args-patch.yaml

helm repo add bitnami https://charts.bitnami.com/bitnami

cd deploy\helm\rickmorty

helm repo update

cd ..\..\..\

docker build -t rickmorty-sre-demo:latest .

kind load docker-image rickmorty-sre-demo:latest --name rm

helm upgrade --install rm ./deploy/helm/rickmorty -n rm --create-namespace --set postgresql.enabled=true --set image.repository=rickmorty-sre-demo --set image.tag=latest

kubectl -n rm logs deploy/rickmorty-rm -c app -f

kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8080:80




# Bring local kind cluster online:

kind create cluster --name rm

kubectl apply -f https://raw.githubusercontent.com/kubernetes/ingress-nginx/main/deploy/static/provider/kind/deploy.yaml

kubectl apply -f .\deploy\shim\ingress-nginx-configmap.yaml
kubectl -n ingress-nginx rollout restart deploy/ingress-nginx-controller

helm repo add metrics-server https://kubernetes-sigs.github.io/metrics-server/
helm repo update

helm upgrade --install metrics-server metrics-server/metrics-server `
   -n kube-system --create-namespace `
   -f .\deploy\shim\metrics-server-args-patch.yaml

helm repo add bitnami https://charts.bitnami.com/bitnami

cd deploy\helm\rickmorty

helm repo update

cd ..\..\..\

docker build -t rickmorty-sre-demo:latest .

kind load docker-image rickmorty-sre-demo:latest --name rm

helm upgrade --install rm ./deploy/helm/rickmorty -n rm --create-namespace --set postgresql.enabled=true --set image.repository=rickmorty-sre-demo --set image.tag=latest

kubectl -n rm logs deploy/rickmorty-rm -c app -f

kubectl -n ingress-nginx port-forward svc/ingress-nginx-controller 8080:80


kind delete cluster --name -rm


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