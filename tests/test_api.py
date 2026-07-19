"""Tests for the ComprasMX client pure helpers (veta.api)."""

from __future__ import annotations

import pytest

from veta import api


def test_build_filter_applies_overrides():
    payload = api.build_filter(id_ley=1, estatus_alterno=["VIGENTE"])
    assert payload["id_ley"] == 1
    assert payload["estatus_alterno"] == ["VIGENTE"]
    # Untouched keys keep their defaults.
    assert payload["id_proceso"] == 0
    assert payload["compra_consolidada"] is False


def test_build_filter_does_not_mutate_default():
    api.build_filter(id_ley=99)
    assert api.DEFAULT_FILTER["id_ley"] is None


def test_build_filter_rejects_unknown_key():
    with pytest.raises(KeyError):
        api.build_filter(not_a_real_key=1)


def test_is_licitacion_publica_by_label():
    assert api.is_licitacion_publica({"tipo_procedimiento": "LICITACIÓN PÚBLICA"})
    assert api.is_licitacion_publica({"tipo_procedimiento": "  licitación pública "})


def test_is_licitacion_publica_by_numero_prefix():
    assert api.is_licitacion_publica({"numero_procedimiento": "LA-07-123"})
    assert api.is_licitacion_publica({"numero_procedimiento": "LO-91-1"})


def test_is_licitacion_publica_rejects_invitacion():
    record = {"tipo_procedimiento": "INVITACIÓN", "numero_procedimiento": "IA-88-1"}
    assert not api.is_licitacion_publica(record)


def test_filter_licitaciones():
    records = [
        {"numero_procedimiento": "LA-1", "tipo_procedimiento": "LICITACIÓN PÚBLICA"},
        {"numero_procedimiento": "IA-2", "tipo_procedimiento": "INVITACIÓN"},
    ]
    kept = api.filter_licitaciones(records)
    assert [r["numero_procedimiento"] for r in kept] == ["LA-1"]
