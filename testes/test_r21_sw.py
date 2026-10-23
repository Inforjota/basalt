"""r21 — a página mudou (SAIR_URL_v1: botão «Terminar sessão» em modo cabecalho com `[auth] sair_url`), por isso a cache do
service worker sobe para basalt-v16: o activate tira a casca guardada da v15 e a cópia sem rede deixa de servir a página antiga.
O protocolo fica em 5 (a conversa página ↔ service worker não mudou). Falha no sw.js da r20 (basalt-v15) pela razão certa."""
import shutil

import pytest

from test_sw import VER, sw

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="precisa do node")


def test_versao_da_cache_e_v16_e_o_activate_tira_as_da_v15_mas_guarda_as_marcas_e_a_epoca():
    assert VER == "basalt-v16", VER
    r = sw([], evento="activate", caches={"basalt-v15-app": ["/"], "basalt-v15-vendor": ["/static/vendor/x.js"],
                                          "basalt-v15-api-d-a": ["/api/notas"], "basaltcfg-epoca": ["/__epoca"],
                                          "basaltcfg-apagadas-d-a": ["/api/nota?nome=x"]})
    assert sorted(r["caches"]) == ["basaltcfg-apagadas-d-a", "basaltcfg-epoca"], r["caches"]
