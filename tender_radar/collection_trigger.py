"""Wait for a bounded Render job using short requests, not one long POST."""
import json
import os
import sys
import time
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen

BASE_URL = "https://qs-concost.onrender.com"


def trigger(scopes: str) -> dict:
    token = os.environ["DIGEST_TRIGGER_TOKEN"]
    headers = {"Authorization": f"Bearer {token}", "X-Collect-Scopes": scopes,
               "X-Collect-Scheduled": os.getenv("SCHEDULED_RUN", "false")}
    request = Request(BASE_URL + "/api/automation/collect", data=b"{}", headers=headers, method="POST")
    # Never retry an ambiguous POST: it may already have started a collection.
    with urlopen(request, timeout=30) as response:
        accepted = json.load(response)
    if not accepted.get("accepted") or not accepted.get("job_id"):
        raise RuntimeError("Collection was not accepted: " + str(accepted.get("reason", accepted.get("error", "unknown"))))
    url = BASE_URL + "/api/automation/collect/status/" + quote(accepted["job_id"], safe="")
    print("Accepted collection job:", accepted["job_id"])
    deadline = time.monotonic() + 180
    last_error = ""
    while time.monotonic() < deadline:
        try:
            with urlopen(Request(url, headers={"Authorization": f"Bearer {token}"}), timeout=15) as response:
                job = json.load(response)
        except HTTPError as exc:
            print("Status request HTTP", exc.code)
            if exc.code in (401, 403, 404):
                raise RuntimeError(f"Collection status unavailable: HTTP {exc.code}") from exc
            last_error = f"HTTP {exc.code}"
            time.sleep(3)
            continue
        except (URLError, TimeoutError, ValueError) as exc:
            last_error = type(exc).__name__
            time.sleep(3)
            continue
        if job.get("status") == "complete":
            print(json.dumps(job, ensure_ascii=False))
            if not job.get("ok"):
                raise RuntimeError("No source completed successfully")
            return job
        time.sleep(2)
    raise RuntimeError("Collection completion could not be verified within 3 minutes: " + last_error)


if __name__ == "__main__":
    trigger(sys.argv[1])
