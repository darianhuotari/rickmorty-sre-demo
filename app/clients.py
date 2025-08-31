import time
import httpx
from typing import List, Dict
from tenacity import retry, wait_exponential, stop_after_attempt, retry_if_exception_type

BASE_URL = "https://rickandmortyapi.com/api/character/?species=Human&status=Alive"

class TransientHTTPError(Exception): ...

def _respect_retry_after(resp: httpx.Response):
    if resp.status_code == 429:
        ra = resp.headers.get("Retry-After")
        if ra:
            try:
                time.sleep(int(ra))
            except ValueError:
                pass  # ignore if not integer; our tenacity backoff still applies

@retry(wait=wait_exponential(multiplier=1, min=1, max=10),
       stop=stop_after_attempt(5),
       retry=retry_if_exception_type(TransientHTTPError))
async def fetch_filtered_characters() -> List[Dict]:
    characters: List[Dict] = []
    async with httpx.AsyncClient(timeout=10.0) as client:
        url = BASE_URL
        while url:
            resp = await client.get(url)
            if resp.status_code in (429, 500, 502, 503, 504):
                _respect_retry_after(resp)
                raise TransientHTTPError(f"Upstream error {resp.status_code}")
            resp.raise_for_status()
            data = resp.json()

            for c in data.get("results", []):
                # Only Earth (any variant)
                if c.get("origin", {}).get("name", "").startswith("Earth"):
                    characters.append({
                        "id": c["id"],
                        "name": c["name"],
                        "status": c["status"],
                        "species": c["species"],
                        "origin": c["origin"]["name"],
                    })

            url = data.get("info", {}).get("next")
    return characters