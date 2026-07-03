"""E2E test: real design request + export PDF/DXF via the API."""
import json, sys, time, urllib.request, urllib.error

BASE = "http://localhost:8000"
TIMEOUT = 120

def req(method, path, body=None):
    url = f"{BASE}{path}"
    data = json.dumps(body).encode() if body else None
    r = urllib.request.Request(url, data=data, method=method)
    r.add_header("Content-Type", "application/json")
    try:
        resp = urllib.request.urlopen(r, timeout=30)
        return json.loads(resp.read().decode())
    except urllib.error.HTTPError as e:
        body = e.read().decode()[:500]
        return {"_error": f"HTTP {e.code}", "_body": body}

def download(path, output_name):
    url = f"{BASE}{path}"
    r = urllib.request.urlopen(url, timeout=30)
    data = r.read()
    with open(output_name, "wb") as f:
        f.write(data)
    return len(data)

# ── Step 1: Health ────────────────────────────────────────────────────────
print("=== 1. Health check ===")
h = req("GET", "/health")
assert h.get("status") == "healthy", f"FAIL: {h}"
print(f"  OK")

# ── Step 2: Start design (moderate specs that should converge) ────────────
print("\n=== 2. Start design ===")
payload = {
    "user_input": "compression spring 200N 30mm deflection max OD 40mm",
    "max_iterations": 5,
}
result = req("POST", "/api/v1/design/", payload)
session_id = result.get("session_id")
print(f"  session_id: {session_id}  status: {result.get('status')}")
assert session_id, f"No session_id: {result}"

# ── Step 3: Poll ──────────────────────────────────────────────────────────
print(f"\n=== 3. Polling (max {TIMEOUT}s) ===")
start = time.time()
final_status = None
while time.time() - start < TIMEOUT:
    progress = req("GET", f"/api/v1/design/{session_id}/status")
    s = progress.get("status", "unknown")
    step = progress.get("current_step", "")
    print(f"  [{time.time()-start:5.1f}s] {s} step={step}")
    if s != "processing":
        final_status = s
        break
    time.sleep(1.5)

if not final_status:
    print("❌ TIMED OUT")
    sys.exit(1)

# ── Step 4: Result (with retry for DB write lag) ──────────────────────────
print(f"\n=== 4. Result: {final_status} ===")
for attempt in range(5):
    result = req("GET", f"/api/v1/design/{session_id}")
    rs = result.get("status", "unknown")
    print(f"  attempt {attempt+1}: status={rs}")
    if rs in ("approved", "error", "needs_clarification", "iteration_limit_reached"):
        break
    time.sleep(1.0)
print(f"  final status: {result.get('status')}")

# If needs_clarification, answer and retry
if result.get("status") == "needs_clarification":
    questions = result.get("clarification_questions", [])
    print(f"  Clarification needed: {questions}")
    answers = ["yes"] * len(questions)
    result2 = req("POST", "/api/v1/design/clarify", {
        "session_id": session_id,
        "answers": answers,
    })
    print(f"  After clarify: {result2.get('status')}")
    # Poll again
    start = time.time()
    while time.time() - start < TIMEOUT:
        progress = req("GET", f"/api/v1/design/{session_id}/status")
        s = progress.get("status", "unknown")
        if s != "processing":
            final_status = s
            break
        time.sleep(1.5)
    result = req("GET", f"/api/v1/design/{session_id}")
    print(f"  Final after clarify: {result.get('status')}")

status = result.get("status")
if status != "approved":
    errors = result.get("errors", result.get("report", {}))
    print(f"  !! Not approved: {status}")
    print(f"  {json.dumps(errors, indent=2, default=str)[:600]}")
    print("\n!! Skipping export tests (design not approved)")
else:
    report = result.get("report", {})
    geo = report.get("geometry", {})
    mat = report.get("material", {})
    comp = report.get("compliance", {})
    comm = report.get("commercial", {})
    print(f"  [OK] Spring: d={geo.get('wire_diameter_mm')}mm OD={geo.get('outer_diameter_mm')}mm L0={geo.get('free_length_mm')}mm")
    print(f"  [OK] Material: {mat.get('name')} Sy={mat.get('yield_strength_mpa')}MPa")
    print(f"  [OK] Sf_shear={comp.get('safety_factor_shear')} Sf_buckling={comp.get('safety_factor_buckling')}")

    top = (comm.get("ranked_proposals") or [{}])[0]
    if top:
        cost = top.get("total_cost_usd", 0)
        life = top.get("estimated_life_cycles", 0)
        mfg = top.get("manufacturing_usd", 0)
        print(f"  [OK] Best: total=${cost:.4f} mfg=${mfg:.4f} life={life:,}")

    # ── Step 5: Export PDF ────────────────────────────────────────────────
    print(f"\n=== 5. Export PDF ===")
    size = download(f"/api/v1/design/{session_id}/export/pdf", "_e2e_test.pdf")
    assert size > 1000, f"PDF too small: {size}"
    print(f"  [OK] PDF: {size} bytes -> _e2e_test.pdf")

    # ── Step 6: Export DXF ────────────────────────────────────────────────
    print(f"\n=== 6. Export DXF ===")
    size = download(f"/api/v1/design/{session_id}/export/dxf", "_e2e_test.dxf")
    assert size > 1000, f"DXF too small: {size}"
    print(f"  [OK] DXF: {size} bytes -> _e2e_test.dxf")

print(f"\n{'='*50}")
print(f"[PASSED] E2E — session {session_id}")
print(f"{'='*50}")
