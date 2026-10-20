"""basalt/static/sw.js (service worker) em node: a cópia guardada da API só se serve quando a REDE falha — nunca num 401/403,
404/410 ou redireccionamento para o login. O ambiente simulado (caches, fetch, self) está em sw_ambiente.js."""
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from urllib.parse import quote

import pytest

from conftest import AQUI, RAIZ

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="precisa do node")

# a versão da cache lê-se do próprio sw.js (v3 → v4 → v5…): com o nome à mão, metade dos testes passava à toa
_SW = Path(os.environ.get("BASALT_SW") or (RAIZ / "basalt" / "static" / "sw.js"))   # BASALT_SW: testar outra cópia (o sw_ambiente.js lê a mesma)
VER = re.search(r'CACHE_VERSAO\s*=\s*"(basalt-v\d+)"', _SW.read_text(encoding="utf-8")).group(1)
DONO = "d-teste"                  # o dono que o sw_ambiente.js põe nos pedidos por omissão
API = VER + "-api-" + DONO        # ronda 9: uma cache da API por dono (antes: VER + "-api", comum)
NOTA = "/api/nota?nome=barcos"
ORIGEM = "https://basalt.teste"   # a origem do sw_ambiente.js


def tem_lapide(caches, url=NOTA, dono=DONO):
    """Há lápide da nota na loja do dono: a entrada antiga (chave = o URL; até à ronda 12) ou uma por instante
    («/__lapide/<URL codificado>/<hora>»; ronda 13: só estas se escrevem)."""
    prefixo = "/__lapide/" + quote(ORIGEM + url, safe="") + "/"
    return any(k == url or k.startswith(prefixo) for k in caches.get("basaltcfg-apagadas-" + dono, []))


def sw(passos, **cfg):
    cfg["passos"] = passos
    r = subprocess.run([NODE, str(AQUI / "sw_ambiente.js"), json.dumps(cfg)], capture_output=True, text=True, timeout=30)
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def guardar_e_depois(rede, url=NOTA):
    """1.º pedido com rede (a resposta fica guardada), 2.º com `rede`."""
    return sw([{"url": url, "rede": "ok"}, {"url": url, "rede": rede}])


class TestCopiaSoQuandoARedeFalha:
    @pytest.mark.parametrize("url", ["/api/notas", NOTA, "/api/indice", "/api/categorias"])
    def test_sem_rede_serve_a_copia_guardada_marcada_como_cache(self, url):
        r = guardar_e_depois("rede", url)
        assert r["passos"][0] == {"intercetado": True, "tipo": "default", "status": 200, "deCache": False, "recusa": False, "corpo": {"versao": 0}}
        assert r["passos"][1]["status"] == 200 and r["passos"][1]["deCache"] is True
        assert r["passos"][1]["corpo"] == {"versao": 0}

    def test_com_rede_responde_sempre_a_rede_e_actualiza_a_copia(self):
        r = sw([{"url": NOTA}, {"url": NOTA}, {"url": NOTA, "rede": "rede"}])
        assert [p["corpo"]["versao"] for p in r["passos"]] == [0, 1, 1]
        assert [p["deCache"] for p in r["passos"]] == [False, False, True]

    def test_sem_rede_e_sem_copia_o_erro_chega_a_pagina(self):
        r = sw([{"url": NOTA, "rede": "rede"}])
        assert "Failed to fetch" in r["passos"][0]["erro"]

    def test_copia_de_uma_nota_nao_serve_para_outra(self):
        r = sw([{"url": NOTA}, {"url": "/api/nota?nome=outra", "rede": "rede"}])
        assert "erro" in r["passos"][1]

    def test_pedido_a_api_vai_com_redirect_manual(self):
        assert {c["redirect"] for c in guardar_e_depois("ok")["chamadas"]} == {"manual"}


class TestNuncaEscondeUmaRecusa:
    @pytest.mark.parametrize("codigo", ["401", "403"])
    def test_acesso_recusado_passa_para_a_pagina_e_apaga_as_copias(self, codigo):
        r = sw([{"url": "/api/notas"}, {"url": NOTA}, {"url": NOTA, "rede": codigo}, {"url": "/api/notas", "rede": "rede"}])
        assert r["passos"][2]["status"] == int(codigo) and r["passos"][2]["deCache"] is False
        assert API not in r["caches"]
        assert "erro" in r["passos"][3]                      # depois disso, sem rede já não há cópia nenhuma

    @pytest.mark.parametrize("codigo", ["404", "410"])
    def test_nota_que_ja_nao_existe_da_404_e_sai_da_copia(self, codigo):
        r = sw([{"url": "/api/notas"}, {"url": NOTA}, {"url": NOTA, "rede": codigo}, {"url": NOTA, "rede": "rede"}])
        assert r["passos"][2]["status"] == int(codigo) and r["passos"][2]["deCache"] is False
        assert r["caches"][API] == ["/api/notas"]
        assert "erro" in r["passos"][3]

    def test_redireccionamento_para_o_login_vira_401_e_nao_serve_a_copia(self):
        # o 302 para o login (opaqueredirect) vira um 401 sintético com X-Basalt-Recusa e tranca a cópia
        # (antes era Response.error(), que a página lia como «sem rede» e podia mostrar a cópia do aparelho)
        r = sw([{"url": NOTA}, {"url": NOTA, "rede": "login"}, {"url": NOTA, "rede": "rede"}])
        p = r["passos"][1]
        assert p["status"] == 401 and p["deCache"] is False and p["recusa"] is True and p["corpo"].get("login") is True
        assert API not in r["caches"]
        assert any(k.startswith("/__tranca/") for k in r["caches"].get("basaltcfg-copia-trancada", []))
        assert "erro" in r["passos"][2]

    def test_pagina_de_login_em_html_com_200_nao_fica_guardada(self):
        r = sw([{"url": NOTA, "rede": "html"}])
        assert r["passos"][0]["corpo"].startswith("<html>")
        assert r["caches"].get(API, []) == []

    @pytest.mark.parametrize("codigo", ["502", "503", "504"])
    def test_servidor_em_baixo_conta_como_sem_rede(self, codigo):
        # comportamento escolhido no sw.js: o Basalt a reiniciar é como estar sem rede
        r = guardar_e_depois(codigo)
        assert r["passos"][1]["status"] == 200 and r["passos"][1]["deCache"] is True

    def test_erro_500_nao_serve_a_copia(self):
        r = guardar_e_depois("500")
        assert r["passos"][1]["status"] == 500 and r["passos"][1]["deCache"] is False


class TestOQueNaoSeIntercepta:
    @pytest.mark.parametrize("metodo", ["PUT", "POST"])
    def test_escritas_vao_sempre_direitas_a_rede(self, metodo):
        assert sw([{"url": NOTA, "metodo": metodo}])["passos"][0] == {"intercetado": False}

    @pytest.mark.parametrize("url", ["/api/estado", "/api/procura?q=x", "/api/pacote", "/api/entrada", "/api/unir"])
    def test_outras_rotas_da_api_nao_ficam_em_cache(self, url):
        r = sw([{"url": url}, {"url": url, "rede": "rede"}])
        assert r["passos"] == [{"intercetado": False}, {"intercetado": False}] and r["caches"] == {}

    def test_abrir_a_api_na_barra_de_enderecos_nao_se_intercepta(self):
        assert sw([{"url": NOTA, "modo": "navigate"}])["passos"][0] == {"intercetado": False}

    def test_outros_dominios_nao_se_interceptam(self):
        assert sw([{"url": "https://outro.exemplo.org/api/notas"}])["passos"][0] == {"intercetado": False}


class TestCopiaDesligadaEVersoes:
    def test_com_a_copia_desligada_nada_da_api_se_guarda(self):
        r = sw([{"url": NOTA}], caches={"basaltcfg-copia-desligada": []})
        assert r["passos"][0]["corpo"] == {"versao": 0}
        assert r["caches"].get(API, []) == []

    def test_activate_apaga_caches_antigas_mas_mantem_a_marca_de_desligada(self):
        r = sw([], evento="activate", caches={"basalt-v2-api": ["/api/notas"], "basalt-v2-app": ["/"],
                                               API: ["/api/notas"], "basaltcfg-copia-desligada": [],
                                               "basaltcfg-copia-trancada": ["/__tranca/x"]})
        assert sorted(r["caches"]) == sorted([API, "basaltcfg-copia-desligada", "basaltcfg-copia-trancada"])

    def test_casca_da_app_serve_se_da_cache_sem_rede(self):
        r = sw([{"url": "/"}, {"url": "/", "rede": "rede"}])
        assert r["passos"][1]["status"] == 200 and r["passos"][1]["deCache"] is True


class TestUmaCachePorDono:
    """ronda 9 (achado G1 da ronda 2): a cópia da API guardada pelo service worker era UMA para o browser — a conta A, sem rede,
    recebia as notas que a conta B tinha deixado em cache. Agora há uma cache por dono (o X-Basalt-Dono do pedido), guarda-se só
    com o eco do mesmo dono na resposta e serve-se só ao mesmo dono; sem dono não se guarda nem se serve."""

    def test_sem_rede_a_conta_a_nao_recebe_a_copia_da_conta_b(self):
        r = sw([{"url": NOTA, "dono": "d-b"}, {"url": NOTA, "dono": "d-a", "rede": "rede"}, {"url": NOTA, "dono": "d-b", "rede": "rede"}])
        assert "erro" in r["passos"][1], f"sem rede, a conta A recebeu a cópia da conta B: {r['passos'][1]}"
        assert r["passos"][2]["deCache"] and r["passos"][2]["corpo"] == {"versao": 0}, f"controlo: a de B serve-se a B ({r['passos'][2]})"

    def test_cada_dono_tem_a_sua_copia(self):
        r = sw([{"url": NOTA, "dono": "d-a"}, {"url": NOTA, "dono": "d-b"},
                {"url": NOTA, "dono": "d-a", "rede": "rede"}, {"url": NOTA, "dono": "d-b", "rede": "rede"}])
        assert [r["passos"][i].get("corpo") for i in (2, 3)] == [{"versao": 0}, {"versao": 1}], r["passos"]
        assert r["caches"].get(VER + "-api-d-a") == [NOTA] and r["caches"].get(VER + "-api-d-b") == [NOTA], r["caches"]

    def test_resposta_sem_eco_do_dono_nao_se_guarda(self):
        r = sw([{"url": NOTA, "dono": "d-a", "eco": ""}, {"url": NOTA, "dono": "d-a", "rede": "rede"}])
        assert r["passos"][0]["status"] == 200 and "erro" in r["passos"][1], r["passos"]
        assert not any(NOTA in v for n, v in r["caches"].items() if "-api" in n), r["caches"]

    def test_resposta_com_o_eco_de_outro_dono_nao_se_guarda(self):
        r = sw([{"url": NOTA, "dono": "d-a", "eco": "d-b"}, {"url": NOTA, "dono": "d-a", "rede": "rede"},
                {"url": NOTA, "dono": "d-b", "rede": "rede"}])
        assert "erro" in r["passos"][1] and "erro" in r["passos"][2], r["passos"]
        assert not any(NOTA in v for n, v in r["caches"].items() if "-api" in n), r["caches"]

    def test_pedido_sem_dono_nao_guarda_nem_recebe_a_copia(self):
        r = sw([{"url": NOTA, "dono": "d-a"}, {"url": NOTA, "dono": "", "rede": "rede"}, {"url": "/api/notas", "dono": ""},
                {"url": "/api/notas", "dono": "d-a", "rede": "rede"}])
        assert "erro" in r["passos"][1], f"um pedido sem dono recebeu a cópia de alguém: {r['passos'][1]}"
        assert r["passos"][2]["status"] == 200 and "erro" in r["passos"][3], r["passos"]
        assert sorted(n for n in r["caches"] if "-api" in n) == [VER + "-api-d-a"], r["caches"]

    @pytest.mark.parametrize("codigo", ["401", "403"])
    def test_acesso_recusado_apaga_as_copias_de_todos_os_donos(self, codigo):
        r = sw([{"url": NOTA, "dono": "d-a"}, {"url": NOTA, "dono": "d-b"}, {"url": NOTA, "dono": "d-a", "rede": codigo},
                {"url": NOTA, "dono": "d-b", "rede": "rede"}])
        assert "erro" in r["passos"][3], f"depois de um {codigo}, a cópia de outra conta continuou a servir-se: {r['passos'][3]}"
        assert not [n for n in r["caches"] if "-api" in n], r["caches"]

    def test_activate_fica_so_com_as_caches_por_dono_desta_versao(self):
        r = sw([], evento="activate", caches={VER + "-api-d-x": ["/api/notas"], VER + "-api": ["/api/notas"],
                                               "basalt-v1-api-d-a": ["/api/notas"], "basalt-v1-api": ["/api/notas"]})
        assert sorted(n for n in r["caches"] if "-api" in n) == [VER + "-api-d-x"], r["caches"]


class TestTrancaELapides:
    """Um 200 em HTML não destranca a cópia sem provar a sessão; e lápides: um 404/410 fica registado com a hora, uma
    resposta boa mais NOVA volta a guardar, uma mais antiga não."""

    def test_um_200_em_html_nao_apaga_a_tranca_que_so_o_service_worker_tem(self):
        r = sw([{"url": NOTA, "rede": "html"}], caches={"basaltcfg-copia-trancada": ["/__tranca/sw1"]})
        assert "/__tranca/sw1" in r["caches"].get("basaltcfg-copia-trancada", []), r["caches"]

    def test_um_410_deixa_lapide_e_uma_resposta_boa_mais_nova_volta_a_guardar(self):
        r = sw([{"url": NOTA, "rede": "410"}, {"url": NOTA, "rede": "ok"}, {"url": NOTA, "rede": "rede"}])
        assert tem_lapide(r["caches"]), r["caches"]
        assert r["passos"][2]["status"] == 200 and r["passos"][2]["deCache"], r["passos"]

