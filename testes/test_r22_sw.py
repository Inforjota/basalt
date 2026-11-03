"""r22 — a página mudou (VERIFICA_SEMPRE_v1, achado R3 da revisão externa da r13b: a nota volta a verificar a cópia sempre que se
desenha, e a pré-visualização também), por isso a cache do service worker sobe para basalt-v17: o activate tira a casca guardada da
v16 e a cópia sem rede deixa de servir a página antiga. O protocolo fica em 5 (a conversa página ↔ service worker não mudou).
Falha no sw.js do 15beb01 (basalt-v16) pela razão certa.

Depois, na mesma ronda (PREVIEW_EPOCA_v1, achado m1 da auditoria de 24/10: a pré-visualização também lê a época persistente),
a página mudou outra vez → basalt-v18, o activate tira a v17. Falha no sw.js do 27a68f2 (basalt-v17) pela razão certa."""
import shutil

import pytest

from test_sw import VER, sw

pytestmark = pytest.mark.skipif(shutil.which("node") is None, reason="precisa do node")


def test_versao_da_cache_e_v17_e_o_activate_tira_as_da_v16_mas_guarda_as_marcas_e_a_epoca():
    assert int(VER.rsplit("v", 1)[1]) >= 17, VER                 # PREVIEW_EPOCA_v1: v18 (o teste de baixo confere a exacta)
    r = sw([], evento="activate", caches={"basalt-v16-app": ["/"], "basalt-v16-vendor": ["/static/vendor/x.js"],
                                          "basalt-v16-api-d-a": ["/api/notas"], "basaltcfg-epoca": ["/__epoca"],
                                          "basaltcfg-apagadas-d-a": ["/api/nota?nome=x"]})
    assert sorted(r["caches"]) == ["basaltcfg-apagadas-d-a", "basaltcfg-epoca"], r["caches"]


def test_versao_da_cache_e_v18_e_o_activate_tira_as_da_v17_mas_guarda_as_marcas_e_a_epoca():
    assert VER == "basalt-v18", VER
    r = sw([], evento="activate", caches={"basalt-v17-app": ["/"], "basalt-v17-vendor": ["/static/vendor/x.js"],
                                          "basalt-v17-api-d-a": ["/api/notas"], "basaltcfg-epoca": ["/__epoca"],
                                          "basaltcfg-apagadas-d-a": ["/api/nota?nome=x"]})
    assert sorted(r["caches"]) == ["basaltcfg-apagadas-d-a", "basaltcfg-epoca"], r["caches"]
