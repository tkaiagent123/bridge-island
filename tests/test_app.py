"""The local web app: routing, forms, validation, security and a real socket."""

import http.client
import json
import re
import subprocess
import sys
import threading
import urllib.parse
from pathlib import Path
from typing import Any

import pytest
import yaml

from bridge_island import cli
from bridge_island.scenario import parse_scenario, run_scenario
from bridge_island.summary import findings, summarize
from bridge_island.ui import KEEP_RESULTS, NAV, App, make_server

PORT = 8765
HOST = {"Host": f"127.0.0.1:{PORT}"}
ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture
def app() -> App:
    return App(PORT)


def get(app: App, path: str, headers: dict[str, str] | None = None) -> Any:
    return app.handle("GET", path, HOST if headers is None else headers)


def post(app: App, path: str, token: bool = True, **fields: str) -> Any:
    if token:
        fields.setdefault("t", app.token)
    body = urllib.parse.urlencode(fields).encode()
    return app.handle("POST", path, HOST, body)


def text(resp: Any) -> str:
    return resp.body.decode("utf-8")


def asset_id(page: str, name: str) -> str:
    return re.search(rf"/download/([\w-]+)/{re.escape(name)}", page).group(1)  # type: ignore[union-attr]


# --- pages ----------------------------------------------------------------------------------


@pytest.mark.parametrize(
    "path, heading",
    [
        ("/", "<h1>bridge-island</h1>"),
        ("/scenarios", "<h1>Scenario files</h1>"),
        ("/report", "<h1>Full report</h1>"),
        ("/rules", "<h1>Check the guidelines</h1>"),
        ("/experiments", "<h1>Experiments</h1>"),
        ("/help", "<h1>Help</h1>"),
    ],
)
def test_every_page_loads_with_navigation_and_no_outside_links(
    app: App, path: str, heading: str
) -> None:
    resp = get(app, path)
    page = text(resp)
    assert resp.status == 200 and heading in page and '<html lang="en">' in page
    assert len(re.findall(r'<a href="[^"]+" aria-current=page>', page)) == 1 and all(
        label in page for _, label in NAV
    )
    assert "http://" not in page and "https://" not in page
    for form in re.findall(r"<form[^>]*>", page):
        assert 'method="post"' in form and 'action="/' in form  # forms post to this app only
    if "<form" in page:
        assert f'name="t" value="{app.token}"' in page


def test_unknown_page_is_a_friendly_404(app: App) -> None:
    resp = get(app, "/nope")
    assert resp.status == 404 and "Page not found" in text(resp)


def test_other_methods_and_oversized_bodies_are_refused(app: App) -> None:
    assert app.handle("PUT", "/", HOST).status == 405
    assert app.handle("POST", "/run", HOST, b"x" * (300 * 1024)).status == 413


# --- security -------------------------------------------------------------------------------


def test_host_header_must_be_this_machine_and_port(app: App) -> None:
    assert get(app, "/", {"Host": "evil.example"}).status == 403
    assert get(app, "/", {"Host": "127.0.0.1:1"}).status == 403
    assert get(app, "/", {}).status == 403
    assert get(app, "/", {"Host": f"localhost:{PORT}"}).status == 200


def test_posts_need_this_sessions_token(app: App) -> None:
    assert post(app, "/run", token=False, example="aviation").status == 403
    assert post(app, "/run", t="wrong", example="aviation").status == 403
    assert "expired" in text(post(app, "/rules", token=False))
    assert post(app, "/run", example="aviation").status == 200
    assert App(PORT).token != app.token  # a new session gets a new token


def test_nothing_typed_is_used_as_a_file_path(app: App) -> None:
    for name in ("../../pyproject.toml", "C:\\Windows\\win.ini", "stall_and_resume.yaml/../x", ""):
        page = text(post(app, "/run-scenario", name=name))
        assert "Choose a scenario from the list" in page
    assert get(app, "/download/../../pyproject.toml").status == 404
    assert get(app, "/download/abc/..%2F..%2Fpyproject.toml").status == 404
    assert get(app, "/download/unknown/summary.json").status == 404


def test_typed_text_is_escaped(app: App) -> None:
    yaml_text = "example: aviation\nduration_s: 2\ndescription: '<script>alert(1)</script>'\n"
    page = text(post(app, "/run-scenario", yaml=yaml_text))
    assert "&lt;script&gt;alert(1)&lt;/script&gt;" in page and "<script>alert(1)" not in page


# --- running --------------------------------------------------------------------------------


def test_a_default_run_explains_itself_and_offers_downloads(app: App) -> None:
    page = text(post(app, "/run", example="aviation", version="1", duration="4", seed="1"))
    assert "What happened" in page and "How each system fared" in page
    assert page.count("<svg") == 4 and "Change something and run again" in page
    for name in ("summary.json", "trace.csv", "scenario.yaml", "manifest.json"):
        assert "/download/" in page and name in page
    run = asset_id(page, "summary.json")
    summary = json.loads(get(app, f"/download/{run}/summary.json").body)
    assert summary["time_s"] == 4.0 and set(summary["subsystems"]) >= {"CueingProcessor"}
    csv_resp = get(app, f"/download/{run}/trace.csv")
    assert csv_resp.body.startswith(b"# synthetic, illustrative\nsubsystem,input")
    assert "attachment" in csv_resp.headers["Content-Disposition"]


def test_the_downloaded_scenario_reproduces_the_run(app: App) -> None:
    page = text(
        post(
            app,
            "/run",
            example="medical",
            version="1",
            duration="5",
            seed="3",
            stall="1",
            stall_at="1",
            stall_for="1",
            fault="out_of_range",
            fault_at="2",
        )
    )
    run = asset_id(page, "scenario.yaml")
    data = yaml.safe_load(get(app, f"/download/{run}/scenario.yaml").body)
    assert data["example"] == "medical" and [list(e)[1] for e in data["events"]] == [
        "stall",
        "send",
    ]
    from bridge_island.examples import load_world

    system, _ = run_scenario(parse_scenario(data), load_world("medical"))
    again = summarize(system)
    original = json.loads(get(app, f"/download/{asset_id(page, 'summary.json')}/summary.json").body)
    assert again == original  # the file captures the run exactly


def test_stall_fault_and_version_choices_change_the_story(app: App) -> None:
    stalled = text(
        post(
            app,
            "/run",
            example="aviation",
            duration="8",
            stall="1",
            stall_at="2.2",
            stall_for="1.5",
        )
    )
    assert "The Bridge stalled and then resumed" in stalled and "bridge_stall" in stalled
    faulty = text(
        post(app, "/run", example="medical", duration="4", fault="unauthorized", fault_at="1")
    )
    assert "cyber monitor blocked 1 message" in faulty and "rejected 0" in faulty
    v2 = text(post(app, "/run", example="aviation", version="2", duration="3"))
    assert "no longer available" in v2
    naive = text(
        post(
            app,
            "/run",
            example="aviation",
            duration="8",
            time_tag="delivery",
            stall="1",
            stall_at="2.2",
            stall_for="1.5",
        )
    )
    assert (
        "arrived later than its latency budget"
        not in naive.split("VoiceRelay received")[1].split("</li>")[0]
    )
    estimates = text(post(app, "/run", example="aviation", duration="4", rate_policy="extrapolate"))
    assert "were estimates, not real samples" in estimates


@pytest.mark.parametrize(
    "fields, message",
    [
        ({"duration": "0"}, "How long must be between 1 and 120"),
        ({"duration": "abc"}, "How long must be a number"),
        ({"duration": "999"}, "between 1 and 120"),
        ({"seed": "1.5x"}, "Random seed must be a number"),
        ({"example": "submarine"}, "Choose one of the examples"),
        ({"stall": "1", "stall_at": "50", "duration": "5"}, "Stall start must be between"),
        ({"stall": "1", "stall_for": "0"}, "Stall length must be between"),
        ({"fault": "made_up"}, "Choose one of the listed faulty messages"),
        ({"version": "7"}, "baseline versions [1, 2], not 7"),
        ({"time_tag": "never"}, "time_tag"),
    ],
)
def test_bad_input_is_explained_not_crashed(app: App, fields: dict[str, str], message: str) -> None:
    resp = post(app, "/run", **{"example": "aviation", **fields})
    page = text(resp)
    assert resp.status == 200 and "Please check this" in page and message in page
    assert "Traceback" not in page and 'role="alert"' in page


# --- scenario files -------------------------------------------------------------------------


def test_shipped_scenarios_are_listed_and_runnable(app: App) -> None:
    page = text(get(app, "/scenarios"))
    for name in ("stall_and_resume.yaml", "fault_injection.yaml", "extrapolate_rate_policy.yaml"):
        assert name in page
    result = text(post(app, "/run-scenario", name="fault_injection.yaml"))
    assert "The Bridge blocks the bad ones" in result and "cyber monitor blocked 2" in result


def test_pasted_scenarios_are_validated_with_friendly_errors(app: App) -> None:
    good = text(post(app, "/run-scenario", yaml="example: medical\nduration_s: 2\n"))
    assert "Result: medical" in good
    for pasted, message in [
        ("a: [unclosed", "not valid YAML"),
        ("example: aviation\n", "missing required key"),
        ("example: aviation\nduration_s: 1\ncolour: red\n", "unknown key"),
        ("- a list\n", "mapping at the top level"),
    ]:
        page = text(post(app, "/run-scenario", yaml=pasted))
        assert message in page and "Please check this" in page and "Traceback" not in page


# --- report, rules, experiments -------------------------------------------------------------


def test_report_opens_in_the_browser_or_downloads_as_a_file(app: App) -> None:
    opened = post(
        app, "/report", example="medical", duration="3", seed="1", rate_policy="cap", mode="open"
    )
    assert (
        opened.status == 200
        and "Back to bridge-island" in text(opened)
        and "Content-Disposition" not in opened.headers
    )
    saved = post(
        app,
        "/report",
        example="medical",
        duration="3",
        seed="1",
        rate_policy="cap",
        mode="download",
    )
    assert saved.body.startswith(b"<!doctype html>") and b"Back to bridge-island" not in saved.body
    assert 'filename="bridge-island-medical.html"' in saved.headers["Content-Disposition"]
    stalled = text(
        post(
            app,
            "/report",
            example="aviation",
            duration="4",
            stall="1",
            stall_at="1",
            stall_for="1",
            mode="open",
        )
    )
    assert "Bridge stalled at 1 s for 1 s" in stalled
    assert "Choose a rate policy" in text(post(app, "/report", example="aviation", rate_policy="x"))
    assert "between 1 and 30" in text(post(app, "/report", example="aviation", duration="500"))


def test_guideline_check_reports_pass_or_fail_in_words(app: App) -> None:
    page = text(post(app, "/rules", example="aviation", version="2"))
    assert "6 of 6 pass" in page and page.count("PASS") == 6 and "&#10003;" in page
    assert "baseline versions" in text(post(app, "/rules", example="aviation", version="9"))


def test_experiments_run_and_offer_their_files(app: App) -> None:
    for name, expect in [
        ("topology", "Topology sweep"),
        ("containment", "Fault containment"),
        ("timing", "Timing and continuity"),
    ]:
        page = text(post(app, "/experiments", name=name, example="aviation", seed="1"))
        assert expect in page and "synthetic, illustrative" in page and "/download/" in page
        assert "wrote " not in page  # file paths from the experiment's console output are hidden
    page = text(
        post(app, "/experiments", name="change-impact", example="medical", seed="2", trials="20")
    )
    assert "Monte Carlo over 20" in page and "change_impact_sensitivity.csv" in page
    csv_run = asset_id(page, "change_impact.csv")
    assert get(app, f"/download/{csv_run}/change_impact.csv").body.startswith(b"# synthetic")
    assert "between 10 and 2000" in text(
        post(app, "/experiments", name="change-impact", trials="999999")
    )
    assert "Choose one of the experiments" in text(post(app, "/experiments", name="rm -rf"))


def test_old_results_are_dropped_to_bound_memory(app: App) -> None:
    ids = [app._store({"a.txt": ("text/plain", b"x")}) for _ in range(KEEP_RESULTS + 3)]
    assert get(app, f"/download/{ids[0]}/a.txt").status == 404
    assert get(app, f"/download/{ids[-1]}/a.txt").status == 200
    assert len(app._results) == KEEP_RESULTS


def test_plain_english_findings(app: App) -> None:
    from bridge_island.examples import load_world

    sc = parse_scenario(
        {"example": "aviation", "duration_s": 6, "version": 2, "events": [{"at": 2, "stall": 1}]}
    )
    system, _ = run_scenario(sc, load_world("aviation"))
    sentences = findings(summarize(system))
    joined = " ".join(sentences)
    assert any(s.startswith("CueingProcessor received") for s in sentences)
    assert "no longer available: baro_alt_m" in joined and "stalled and then resumed" in joined
    assert sentences[-1] == "No unique system has a link that skips the Bridge."


# --- a real socket --------------------------------------------------------------------------


def test_the_real_server_binds_locally_sends_safe_headers_and_serves_forms() -> None:
    server, running = make_server(0)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        assert server.server_address[0] == "127.0.0.1"  # never reachable from other machines
        conn = http.client.HTTPConnection("127.0.0.1", running.port, timeout=30)
        conn.request("GET", "/")
        resp = conn.getresponse()
        page = resp.read().decode()
        assert resp.status == 200 and "<h1>bridge-island</h1>" in page
        assert (
            resp.getheader("Cache-Control") == "no-store"
            and resp.getheader("X-Content-Type-Options") == "nosniff"
        )
        csp = resp.getheader("Content-Security-Policy") or ""
        assert (
            "default-src 'none'" in csp
            and "frame-ancestors 'none'" in csp
            and "form-action 'self'" in csp
        )
        token = re.search(r'name="t" value="([^"]+)"', page).group(1)  # type: ignore[union-attr]
        body = urllib.parse.urlencode({"t": token, "example": "aviation", "duration": "3"})
        conn.request("POST", "/run", body, {"Content-Type": "application/x-www-form-urlencoded"})
        resp = conn.getresponse()
        assert resp.status == 200 and "What happened" in resp.read().decode()
        conn.request("GET", "/", headers={"Host": "evil.example"})
        assert conn.getresponse().status == 403  # DNS-rebinding style request
        assert "script-src 'none'" in csp  # the home page has no script, so none may run
        nonces = []
        for _ in range(2):  # the one page with a script: allowed by a nonce that differs every time
            conn.request("GET", "/scenarios")
            resp = conn.getresponse()
            page = resp.read().decode()
            nonce = re.search(
                r"script-src 'nonce-([^']+)'", resp.getheader("Content-Security-Policy") or ""
            )
            assert nonce and f'<script nonce="{nonce.group(1)}">' in page and "<script>" not in page
            assert "unsafe-inline'; img" not in (resp.getheader("Content-Security-Policy") or "")
            nonces.append(nonce.group(1))
        assert nonces[0] != nonces[1]
        conn.close()
    finally:
        server.shutdown()
        server.server_close()


def test_the_next_port_is_used_when_the_first_is_busy() -> None:
    first, a = make_server(0)
    busy = a.port
    try:
        second, b = make_server(busy)
        try:
            assert b.port != busy and busy < b.port < busy + 20
        finally:
            second.server_close()
    finally:
        first.server_close()


# --- launching ------------------------------------------------------------------------------


def test_the_launcher_and_module_entry_point(tmp_path: Path) -> None:
    bat = (ROOT / "Start-bridge-island.bat").read_text(encoding="utf-8")
    assert "bridge-island ui" in bat and "python -m bridge_island ui" in bat and "pause" in bat
    attrs = (ROOT / ".gitattributes").read_text()
    assert "*.bat text eol=crlf" in attrs  # cmd.exe needs Windows line endings
    done = subprocess.run(
        [sys.executable, "-m", "bridge_island", "--version"],
        capture_output=True,
        text=True,
        cwd=tmp_path,
    )
    assert done.returncode == 0 and "bridge-island" in done.stdout


def test_ui_command_reports_a_busy_machine_as_a_friendly_error(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    def refuse(*_: Any, **__: Any) -> None:
        raise OSError("no free port from 8765 to 8784")

    monkeypatch.setattr("bridge_island.ui.serve", refuse)
    assert cli.main(["ui", "--no-browser"]) == 2
    err = capsys.readouterr().err
    assert err.startswith("error: the app could not start: no free port")
    with pytest.raises(SystemExit) as exc:
        cli.main(["ui", "--help"])
    assert exc.value.code == 0
