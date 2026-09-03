"""研报原件在线查看路由(/api/research/<id>/file)单元测试。

覆盖:未知 id 404、路径穿越 403、可渲染类型(PDF)inline 200、
不可预览类型 415。文件与目录全部隔离到 tmp_path,不碰真库真文件。
"""

import pytest

import web_app


class _DB:
    def __init__(self, row):
        self._row = row

    def get_research_report(self, rid):
        return self._row


@pytest.fixture
def upload_dir(tmp_path, monkeypatch):
    d = tmp_path / "uploads"
    d.mkdir()
    monkeypatch.setattr(web_app, "RESEARCH_UPLOAD_DIR", d)
    return d


def _client():
    return web_app.app.test_client()


def test_file_route_unknown_id_404(upload_dir, monkeypatch):
    monkeypatch.setattr(web_app, "get_db", lambda: _DB(None))
    resp = _client().get("/api/research/999/file")
    assert resp.status_code == 404


def test_file_route_no_original_404(upload_dir, monkeypatch):
    monkeypatch.setattr(web_app, "get_db", lambda: _DB({"file_path": None}))
    resp = _client().get("/api/research/1/file")
    assert resp.status_code == 404


def test_file_route_path_escape_403(upload_dir, monkeypatch):
    outside = upload_dir.parent / "evil.pdf"
    outside.write_bytes(b"%PDF-1.4")
    monkeypatch.setattr(web_app, "get_db", lambda: _DB({"file_path": str(outside)}))
    resp = _client().get("/api/research/1/file")
    assert resp.status_code == 403


def test_file_route_pdf_inline(upload_dir, monkeypatch):
    pdf = upload_dir / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    monkeypatch.setattr(web_app, "get_db", lambda: _DB({"file_path": str(pdf)}))
    resp = _client().get("/api/research/1/file")
    assert resp.status_code == 200
    assert resp.content_type == "application/pdf"
    assert "attachment" not in (resp.headers.get("Content-Disposition") or "")


def test_file_route_non_previewable_415(upload_dir, monkeypatch):
    doc = upload_dir / "a.docx"
    doc.write_bytes(b"PK")
    monkeypatch.setattr(web_app, "get_db", lambda: _DB({"file_path": str(doc)}))
    resp = _client().get("/api/research/1/file")
    assert resp.status_code == 415


def test_file_route_missing_file_404(upload_dir, monkeypatch):
    monkeypatch.setattr(
        web_app, "get_db",
        lambda: _DB({"file_path": str(upload_dir / "gone.pdf")}),
    )
    resp = _client().get("/api/research/1/file")
    assert resp.status_code == 404
