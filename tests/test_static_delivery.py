"""Source and container console delivery must retain the standalone widget."""

import pytest
from httpx import ASGITransport, AsyncClient
from starlette.applications import Starlette
from starlette.responses import JSONResponse
from starlette.routing import Mount, Route

from server.config import settings
from server.main import _console_directory, _ConsoleMount, _SPAStaticFiles, app


def test_source_build_takes_precedence_over_old_static_assets(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "widget.js").write_text("// maintained widget", encoding="utf-8")
    assert _console_directory(tmp_path) is None
    dist = tmp_path / "console" / "dist"
    dist.mkdir(parents=True)
    (dist / "index.html").write_text("new build", encoding="utf-8")
    assert _console_directory(tmp_path) == dist
    (static / "index.html").write_text("old build", encoding="utf-8")
    assert _console_directory(tmp_path) == dist


def test_container_uses_packaged_console(tmp_path):
    static = tmp_path / "static"
    static.mkdir()
    (static / "index.html").write_text("container build", encoding="utf-8")
    assert _console_directory(tmp_path) == static


@pytest.mark.parametrize("path,status", [
    ("/", 200), ("/playground", 200), ("/business-data", 200),
    ("/assets/missing.js", 404), ("/assets/missing", 404),
    ("/missing.css", 404), ("/api/v1/no-such-endpoint", 404), ("/api/v1", 404),
])
async def test_only_console_navigation_gets_html_fallback(tmp_path, path, status):
    (tmp_path / "index.html").write_text("<html>Aezab console</html>", encoding="utf-8")
    site = Starlette(routes=[Mount("/", app=_SPAStaticFiles(directory=str(tmp_path)))])
    async with AsyncClient(transport=ASGITransport(app=site), base_url="http://test") as client:
        response = await client.get(path)
    assert response.status_code == status
    if status == 200:
        assert "Aezab console" in response.text
    else:
        assert "Aezab console" not in response.text


async def test_static_method_errors_are_not_masked_as_console(tmp_path):
    (tmp_path / "index.html").write_text("Aezab console", encoding="utf-8")
    site = Starlette(routes=[Mount("/", app=_SPAStaticFiles(directory=str(tmp_path)))])
    async with AsyncClient(transport=ASGITransport(app=site), base_url="http://test") as client:
        response = await client.post("/playground")
    assert response.status_code == 405


async def test_standalone_widget_is_available_with_source_console():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        response = await client.get("/widget.js")
    assert response.status_code == 200
    assert "text/javascript" in response.headers["content-type"]
    assert "data-agent-id" in response.text


@pytest.mark.parametrize("root_path", ["", "/proxy"])
async def test_console_mount_preserves_api_redirects_and_missing_route_404(tmp_path, root_path):
    (tmp_path / "index.html").write_text("Aezab console", encoding="utf-8")

    async def list_agents(request):
        return JSONResponse({"agents": []})

    site = Starlette(routes=[
        Route(settings.api_prefix + "/agents/", list_agents),
        _ConsoleMount("/", app=_SPAStaticFiles(directory=str(tmp_path))),
    ])
    async with AsyncClient(transport=ASGITransport(app=site, root_path=root_path),
                           base_url="http://test") as client:
        endpoint = root_path + settings.api_prefix + "/agents"
        response = await client.get(endpoint)
        assert response.status_code == 307
        assert response.headers["location"] == "http://test" + endpoint + "/"
        followed = await client.get(endpoint, follow_redirects=True)
        assert followed.json() == {"agents": []}
        unknown = await client.get(root_path + settings.api_prefix + "/missing")
        assert unknown.status_code == 404
        assert "Aezab console" not in unknown.text
