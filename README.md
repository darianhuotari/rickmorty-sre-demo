# RICKMORTY-SRE-DEMO


Todo:
Move rate-limit logic to nginx
Remove redis 
Remove slowAPI / custom rate-limit tests



Run locally: uvicorn app.main:app --reload --port 8000




# Load test:
# Clean up any previous pod
kubectl -n rm delete pod fortio --ignore-not-found

# Start load: entrypoint is `fortio`, so first arg is `load`
kubectl -n rm run fortio --restart=Never --image=fortio/fortio:latest_release -- \
  load -qps 800 -c 200 -t 5m \
  "http://rickmorty-rm:8000/characters?page=1&page_size=1&sort=id&order=asc"

# Stream results
kubectl -n rm logs -f pod/fortio

# Cleanup
kubectl -n rm delete pod fortio