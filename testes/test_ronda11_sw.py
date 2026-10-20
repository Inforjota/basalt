"""Ronda 11 — corridas no service worker (basalt/static/sw.js), ensaiadas em node com sw_corridas.js (rede pendurada, relógio do
ensaio, Web Locks simulados com o disjuntor do trinco encurtado).

G2  a lápide de uma nota RECUAVA: com um 200 (de 20) a guardar e o trinco preso, um 404 de 10 lia «sem lápide» e ficava suspenso
    antes de a escrever; um 404 de 30 gravava 30; o de 10 retomava e escrevia 10 por cima — o 200 de 20 ficava guardado e
    servia-se sem rede. Agora cada 404/410 escreve a sua entrada «/__lapide/<URL>/<hora>» e vale a maior.
G3  daCache() via a lápide e ainda fazia mais leituras (marcas, geração) antes de entregar: um 404 ou um 401 pelo disjuntor nesse
    intervalo tirava a nota da cache e ela era entregue na mesma. Agora a última leitura é a da própria entrada.
Poda (menor): as entradas por instante de uma nota não se acumulam — só saem as que têm outra MAIOR da mesma nota.
Ronda 13: a entrada antiga da lápide (chave = o URL) já não se escreve — `unica` passa a None (a antiga ainda se lê).
"""
import json
import os
import shutil
import subprocess

import pytest

from conftest import AQUI
from test_sw import VER, sw

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="precisa do node")


def corrida(cenario):
    r = subprocess.run([NODE, str(AQUI / "sw_corridas.js"), cenario], capture_output=True, text=True, timeout=600, env=dict(os.environ))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


@pytest.fixture(scope="module")
def g2():
    return corrida("g2")


@pytest.fixture(scope="module")
def g3():
    return corrida("g3")


@pytest.fixture(scope="module")
def poda():
    return corrida("poda")


def test_versao_da_cache_subiu_e_o_activate_tira_as_da_v12():
    assert int(VER.rsplit("v", 1)[1]) >= 13, VER                 # ronda 13: v14 (o test_ronda13_sw.py confere a versão exacta)
    r = sw([], evento="activate", caches={"basalt-v12-app": ["/"], "basalt-v12-vendor": ["/static/vendor/x.js"],
                                          "basalt-v12-api-d-a": ["/api/notas"], "basalt-v12-api": ["/api/notas"],
                                          "basaltcfg-apagadas-d-a": ["/api/nota?nome=x"]})
    assert sorted(r["caches"]) == ["basaltcfg-apagadas-d-a"], r["caches"]


def test_g2_dois_404_pelo_disjuntor_nao_fazem_a_lapide_recuar(g2):
    c = g2["caso"]
    assert c["preso"] and c["parou"], f"o ensaio não montou a corrida: {c}"
    assert c["r10"] == 404 and c["r30"] == 404 and c["depois30"] == 30, c
    assert c["lapide"] == 30, f"a lápide recuou para {c['lapide']} (entrada antiga {c['unica']})"
    assert c["guardada"] is None and c["offline"] == "recusou", f"o 200 de 20 ficou ({c['guardada']}) e serve-se sem rede: {c['offline']}"


def test_controlo_g2_a_nota_recriada_depois_dos_404_guarda_se_e_serve_se(g2):
    assert g2["controlo"] == {"guardada": "RECRIADA", "offline": "200 cache"}, g2["controlo"]


def test_g2b_404_suspenso_em_qualquer_operacao_na_loja_das_lapides_nao_a_faz_recuar():
    ls = corrida("g2b")["linhas"]
    assert len(ls) >= 2, ls
    maus = [x for x in ls if not (x["lapide"] == 30 and x["guardada"] is None and x["offline"] == "recusou")]
    assert not maus, "\n".join(f"k={x['k']} ({x['onde']}): lápide {x['lapide']}, cache {x['guardada']}, sem rede {x['offline']}" for x in maus)


def test_g3_404_durante_a_leitura_da_geracao_nao_entrega_a_nota_apagada(g3):
    ls = g3["linhas"]
    assert len(ls) >= 2, ls
    maus = [x for x in ls if not (x["estado"] == 404 and x["offline"] == "recusou")]
    assert not maus, "\n".join(f"404 antes da {x['N']}.ª leitura da geração → {x['offline']}" for x in maus)


def test_controlo_g3_sem_corrida_serve_se_e_depois_de_um_404_nao(g3):
    assert g3["controlo"] == {"guardada": "ANTIGA", "semNada": "200 cache", "depois404": "recusou"}, g3["controlo"]


def test_g3b_404_401_ou_apagar_a_copia_antes_de_qualquer_leitura_nada_se_entrega():
    ls = corrida("g3b")["linhas"]
    por = {e: len([x for x in ls if x["escritor"] == e]) for e in ("404", "401", "apagar")}
    assert all(n >= 5 for n in por.values()), por
    maus = [x for x in ls if x["offline"] != "recusou"]
    assert not maus, "\n".join(f"{x['escritor']} antes da {x['N']}.ª leitura ({x['onde']}) → {x['offline']}" for x in maus)


def test_poda_so_tira_as_lapides_por_instante_dominadas(poda):
    s = poda["seguidas"]
    assert s["por_instante"] == [50] and s["lapide"] == 50 and s["unica"] in (50, None), f"cinco 404 seguidos (10…50): {s}"
    assert len(s["outra"]) == 1, f"a lápide de outra nota saiu ou duplicou: {s['outra']}"
    p = poda["suspensa"]
    assert p["parou"], p
    assert p["por_instante"] == [70] and p["lapide"] == 70 and p["unica"] in (70, None), f"poda de 60 suspensa enquanto 70 corria: {p}"


def test_controlo_poda_nota_recriada_serve_se_e_lapides_velhas_saem(poda):
    c = poda["controlo"]
    assert c["nova"] == "NOVA" and c["offline"] == "200 cache", c
    assert c["depois_de_31_dias"] == {"n1": [], "n1_unica": None, "n10": []}, c["depois_de_31_dias"]
