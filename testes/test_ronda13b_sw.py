"""Correcções da ronda 13 — a página mudou (verificação final nas respostas da rede e antes de desenhar), por isso a cache do
service worker sobe para basalt-v15: o activate tira a casca guardada da v14 e a cópia sem rede deixa de servir a página antiga.
O protocolo fica em 5 (a conversa página ↔ service worker não mudou).
Falha no sw.js da ronda 13 (basalt-v14) pela razão certa.
"""
import shutil

import pytest

from test_sw import VER, sw

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="precisa do node")


def test_versao_da_cache_e_v15_e_o_activate_tira_as_da_v14_mas_guarda_as_marcas_e_a_epoca():
    assert VER == "basalt-v15", VER
    r = sw([], evento="activate", caches={"basalt-v14-app": ["/"], "basalt-v14-vendor": ["/static/vendor/x.js"],
                                          "basalt-v14-api-d-a": ["/api/notas"], "basaltcfg-epoca": ["/__epoca"],
                                          "basaltcfg-apagadas-d-a": ["/api/nota?nome=x"]})
    assert sorted(r["caches"]) == ["basaltcfg-apagadas-d-a", "basaltcfg-epoca"], r["caches"]
