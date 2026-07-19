"""Tests for the single-tender bid brief (veta.brief)."""

from __future__ import annotations

from veta import brief as brief_mod
from veta.brief import TenderBrief


def test_money():
    assert brief_mod._money(1000) == "$1,000 MXN"
    assert brief_mod._money(None) == "not published"


def test_yn():
    assert brief_mod._yn(1) == "yes"
    assert brief_mod._yn(0) == "no"
    assert brief_mod._yn(None) == "no"


def test_dt():
    assert brief_mod._dt("2026-07-03T15:23:31") == "2026-07-03 15:23"
    assert brief_mod._dt(None) == "not listed"


def test_pct():
    assert brief_mod._pct(10) == "10%"
    assert brief_mod._pct(None) is None


def test_safe_filename():
    assert brief_mod._safe_filename("CONVOCATORIA / anexo #1") == "CONVOCATORIA_anexo_1"
    assert brief_mod._safe_filename("") == "documento"


class _FakeClient:
    def __init__(self, detail=None, partidas=None, by_numero=None, doc=None):
        self._detail = detail
        self._partidas = partidas or []
        self._by_numero = by_numero
        self._doc = doc or (b"%PDF-1.7 data", "application/pdf")
        self.downloaded = []

    def fetch_by_numero(self, numero):
        return self._by_numero

    def fetch_detail(self, uuid):
        return self._detail

    def fetch_partidas(self, uuid):
        return self._partidas

    def download_document(self, uuid_documento):
        self.downloaded.append(uuid_documento)
        return self._doc


DETAIL = {
    "numero_procedimiento": "LA-1",
    "uuid_procedimiento": "a" * 32,
    "nombre_procedimiento": "Compra",
    "anexos": [
        {"numero": 1, "descripcion": "CONVOCATORIA", "uuid_documento": "doc-1"},
    ],
}


def test_build_brief_by_uuid():
    client = _FakeClient(detail=DETAIL, partidas=[{"clave_p_especifica": "25401"}])
    brief = brief_mod.build_brief(client, "a" * 32)
    assert brief is not None
    assert brief.numero == "LA-1"
    assert len(brief.partidas) == 1


def test_build_brief_by_numero_resolves_uuid():
    client = _FakeClient(detail=DETAIL, by_numero={"uuid_procedimiento": "a" * 32})
    brief = brief_mod.build_brief(client, "LA-1")
    assert brief is not None
    assert brief.numero == "LA-1"


def test_build_brief_missing_returns_none():
    client = _FakeClient(detail=None, by_numero=None)
    assert brief_mod.build_brief(client, "LA-DOES-NOT-EXIST") is None


def test_render_brief_sections():
    brief = TenderBrief(detail=DETAIL, partidas=[], anexos=DETAIL["anexos"])
    text = brief_mod.render_brief(brief)
    for section in ("BUYER", "TIMELINE", "PROCEDURE", "BID ECONOMICS", "ATTACHMENTS"):
        assert section in text
    assert "LA-1" in text


def test_download_anexos_writes_files(tmp_path):
    client = _FakeClient(detail=DETAIL)
    brief = TenderBrief(detail=DETAIL, partidas=[], anexos=DETAIL["anexos"])
    paths = brief_mod.download_anexos(client, brief, str(tmp_path))
    assert len(paths) == 1
    assert paths[0].endswith(".pdf")
    assert client.downloaded == ["doc-1"]
    with open(paths[0], "rb") as fh:
        assert fh.read().startswith(b"%PDF")
