"""python http_client.py D:\Audio\meeting.wav"""
import json
from pathlib import Path
import sys
import time
import urllib.request

root = Path(__file__).resolve().parents[2]
token = (root / "desktop/data/api-token.txt").read_text().strip()
base = "http://127.0.0.1:8765"

def request(method, route, data=None):
    req = urllib.request.Request(base + route, method=method,
        headers={"Authorization": "Bearer " + token, "Content-Type": "application/json"},
        data=json.dumps(data).encode("utf-8") if data is not None else None)
    with urllib.request.urlopen(req, timeout=30) as response:
        return json.load(response)

if __name__ == "__main__":
    if len(sys.argv) != 2:
        raise SystemExit("Usage: python http_client.py <audio/video file>")
    job = request("POST", "/v1/jobs", {"file_path": str(Path(sys.argv[1]).resolve())})
    while job["status"] not in {"succeeded", "failed", "cancelled"}:
        time.sleep(1)
        job = request("GET", "/v1/jobs/" + job["id"])
    if job["status"] != "succeeded":
        raise SystemExit(job["error"] or job["status"])
    print(job["result"]["text"])
    print("Saved:", job["output_dir"])
