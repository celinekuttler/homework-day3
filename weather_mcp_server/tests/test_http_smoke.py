import json
import os
import subprocess
import time
import urllib.request

PORT = 8099
TESTS_DIR = os.path.dirname(os.path.abspath(__file__))
SERVER_DIR = os.path.dirname(TESTS_DIR)
PY = os.path.join(SERVER_DIR, os.pardir, ".venv", "Scripts", "python.exe")

proc = subprocess.Popen(
    [PY, "weather_mcp_server.py"],
    cwd=SERVER_DIR,
    stdout=subprocess.PIPE,
    stderr=subprocess.STDOUT,
    text=True,
    env={"PORT": str(PORT), "PYTHONUNBUFFERED": "1", **os.environ},
)


SESSION_ID = [None]


def post(payload, path="/mcp"):
    headers = {"Content-Type": "application/json", "Accept": "application/json, text/event-stream"}
    if SESSION_ID[0]:
        headers["Mcp-Session-Id"] = SESSION_ID[0]
    body = json.dumps(payload).encode()
    req = urllib.request.Request(
        f"http://127.0.0.1:{PORT}{path}",
        data=body,
        headers=headers,
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            sid = resp.headers.get("Mcp-Session-Id")
            if sid:
                SESSION_ID[0] = sid
            return resp.status, resp.read().decode()
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode()


def parse_mcp(raw):
    """Extract JSON from streamable-HTTP responses (SSE 'data:' lines or bare JSON)."""
    raw = raw.strip()
    if not raw.startswith("{"):
        datas = [ln[len("data:"):].strip() for ln in raw.splitlines() if ln.startswith("data:")]
        raw = datas[-1] if datas else ""
    return json.loads(raw)


try:
    # wait for boot
    booted = False
    for _ in range(30):
        try:
            status, raw = post({"jsonrpc": "2.0", "id": 1, "method": "initialize",
                                "params": {"protocolVersion": "2025-03-26",
                                           "capabilities": {},
                                           "clientInfo": {"name": "smoke", "version": "1.0"}}})
            booted = True
            break
        except Exception:
            time.sleep(1)
    assert booted, "server did not boot"
    init = parse_mcp(raw)
    print("initialize -> status", status, "| protocolVersion:",
          init.get("result", {}).get("protocolVersion"), "| serverInfo:", init.get("result", {}).get("serverInfo"))

    post({"jsonrpc": "2.0", "method": "notifications/initialized"})

    status, raw = post({"jsonrpc": "2.0", "id": 2, "method": "tools/list"})
    tools = parse_mcp(raw)["result"]["tools"]
    print("tools/list ->", [t["name"] for t in tools])

    status, raw = post({"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                        "params": {"name": "get_current_weather", "arguments": {"location": "Chicago, IL"}}})
    call = parse_mcp(raw)
    text = call["result"]["content"][0]["text"]
    data = json.loads(text)
    print("tools/call get_current_weather(Chicago, IL) -> status:", data.get("status"),
          "| temp:", data.get("data", {}).get("temperature"))

    status, raw = post({"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                        "params": {"name": "get_weather_alerts", "arguments": {"location": "London"}}})
    call = parse_mcp(raw)
    text = call["result"]["content"][0]["text"]
    data = json.loads(text)
    print("tools/call get_weather_alerts(London) -> status:", data.get("status"),
          "| note:", data.get("note", "")[:60])

    print("HTTP SMOKE TEST DONE")
finally:
    proc.terminate()
    try:
        out, _ = proc.communicate(timeout=10)
        print("--- server stdout tail ---")
        print("\n".join(out.strip().splitlines()[-5:]))
    except subprocess.TimeoutExpired:
        proc.kill()
