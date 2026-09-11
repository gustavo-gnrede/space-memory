from fastapi.testclient import TestClient

from space_memory.app import create_app


def make_client(tmp_path):
    app = create_app(
        database_url=f"sqlite:///{tmp_path / 'panel.db'}",
        token_pepper="test-pepper",
        bootstrap_tokens={"spm_panel": {"space_id": "space-a", "agent_id": "panel-agent"}},
    )
    return TestClient(app)


def panel_html(tmp_path):
    return make_client(tmp_path).get("/").text


def panel_script(tmp_path):
    html = panel_html(tmp_path)
    return html.split("<script>")[1].split("</script>")[0]


# --- serving and structure --------------------------------------------------


def test_root_serves_panel(tmp_path):
    response = make_client(tmp_path).get("/")
    assert response.status_code == 200
    assert 'id="brain-canvas"' in response.text
    assert 'id="activity-list"' in response.text
    assert "prefers-reduced-motion" in response.text
    assert "Space Memory" in response.text


def test_panel_exposes_login_view_and_hides_panel_by_default(tmp_path):
    html = panel_html(tmp_path)
    # login is the default experience
    assert 'id="login-view"' in html
    assert 'id="login-form"' in html
    assert 'type="password"' in html
    # the connected panel must not render as if already authenticated
    assert 'id="panel-view" hidden' in html


# --- token handling ---------------------------------------------------------


def test_panel_uses_sessionstorage_not_localstorage(tmp_path):
    script = panel_script(tmp_path)
    assert "sessionStorage" in script
    assert "localStorage" not in script


def test_panel_sends_token_via_header_not_url(tmp_path):
    html = panel_html(tmp_path)
    # token travels in the Authorization header, never a query string
    assert "Authorization" in html
    assert '<form id="login-form" novalidate>' in html  # no action/method


def test_panel_logout_removes_session(tmp_path):
    script = panel_script(tmp_path)
    assert 'id="logout"' in panel_html(tmp_path)
    assert "sessionStorage.removeItem" in script


# --- the 401 vs empty distinction ------------------------------------------


def test_panel_distinguishes_invalid_key_from_empty_space(tmp_path):
    script = panel_script(tmp_path)
    # 401/403 must route to an explicit invalid-access state …
    assert "401" in script and "403" in script
    assert "Acesso inválido ou expirado" in script
    # … while a valid-but-empty Space has its own message
    assert "sem eventos" in script


# --- backend contract the panel relies on ----------------------------------


def test_events_reject_invalid_token(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/api/v1/events?after=0", headers={"Authorization": "Bearer spm_wrong"})
    assert response.status_code == 401


def test_events_reject_missing_token(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/api/v1/events?after=0")
    assert response.status_code == 401


def test_events_accept_valid_token_on_empty_space(tmp_path):
    client = make_client(tmp_path)
    response = client.get("/api/v1/events?after=0", headers={"Authorization": "Bearer spm_panel"})
    assert response.status_code == 200
    assert response.json()["items"] == []
