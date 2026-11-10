"""Read-only API smoke checks plus rejected invalid requests on local test data."""
import argparse
import json
from urllib.error import HTTPError
from urllib.request import ProxyHandler, Request, build_opener


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8766)
    args = parser.parse_args()
    base = f"http://127.0.0.1:{args.port}"
    opener = build_opener(ProxyHandler({}))
    def get(path):
        with opener.open(base + path, timeout=30) as response:
            return response.read()
    def post(path, payload, token):
        request = Request(base + path, data=json.dumps(payload).encode(), headers={"Content-Type": "application/json", "X-Workbench-Token": token})
        with opener.open(request, timeout=30) as response:
            return json.load(response)
    status = json.loads(get("/api/status"))
    for path in ("/", "/app.js", "/style.css"):
        assert get(path), path
    demo = json.loads(get("/api/analysis?mode=demo"))
    assert len(demo["stocks"]) == 90 and demo["meta"]["mode"] == "demo"
    assert demo["market"]["height"] == 5
    code = demo["candidates"][0]
    detail = json.loads(get("/api/stock?mode=demo&code=" + code))
    assert len(detail["bars"]) == 60
    for style in ("balanced", "relay", "trend"):
        result = post("/api/analyze", {"mode": "demo", "config": {"style": style}}, status["csrf"])
        assert result["config"]["style"] == style
        assert result["market"]["active_count"] == 90
    try:
        post("/api/analyze", {}, "invalid")
        raise AssertionError("Invalid CSRF accepted")
    except HTTPError as error:
        assert error.code == 403
    try:
        post("/api/import", {"dataset": {"schema_version": 99}}, status["csrf"])
        raise AssertionError("Invalid snapshot accepted")
    except HTTPError as error:
        assert error.code == 400
    print("PASS: static assets, demo isolation, market height, chart history, three modes, CSRF, invalid import")


if __name__ == "__main__":
    main()
