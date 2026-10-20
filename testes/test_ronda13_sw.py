"""Ronda 13 (página e service worker) — o lado do service worker (basalt/static/sw.js), em node: corridas com sw_corridas.js
(rede pendurada, relógio do ensaio, Web Locks simulados com o disjuntor do trinco encurtado) e passos com sw_ambiente.js.

Achado 1 da revisão externa r10: a resposta da ÚLTIMA releitura (`caches.match`) já em mão, com a promessa por resolver, era
    entregue depois de um 404 pelo disjuntor ter acabado (lápide + apagar). Variante: a lápide posta depois das verificações e o
    apagar suspenso — a entrada ainda lá está quando se relê. Agora cada volta do daCache() guarda a época (a de memória, que um
    404/401/troca de conta muda logo ao chegar, e a persistente, «basaltcfg-epoca») e só entrega se nenhuma mudou.
Achado 4 do porte / 3 da r10: os separadores abertos com a página antiga ficavam com o apiGet() antigo. O protocolo sobe para 5
    (a página antiga recarrega no controllerchange) e o service worker só serve a cópia a pedidos com «X-Basalt-Protocolo: 5» — a
    página antiga que não recarregou (editor com alterações) não a recebe.
Achados 1A/1B do porte e 2 da r10 (o lado do SW): um 404/401 e «revogar» (pedido pela página antes de trancar/apagar/trocar
    de conta) mudam a época e avisam as páginas abertas ({tipo:"revogada"}), e só acabam depois de elas responderem (ou de
    AVISO_MS) — a página recusa o que tinha a meio.
Achado 5 do porte (menor): a entrada antiga da lápide (chave = o URL) recuava sob concorrência; deixa de se escrever (ainda se lê).
Pendente b: /api/fonte entra na cópia (lápide 404/410, tranca 401, época).
Cada teste falha no sw.js anterior a esta ronda pela razão certa (`BASALT_SW=<cópia antiga>/basalt/static/sw.js`), menos os controlos.
"""
import json
import os
import shutil
import subprocess

import pytest

from conftest import AQUI
from test_sw import VER, sw, tem_lapide

NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="precisa do node")


def corrida(cenario):
    r = subprocess.run([NODE, str(AQUI / "sw_corridas.js"), cenario], capture_output=True, text=True, timeout=600, env=dict(os.environ))
    assert r.returncode == 0, r.stderr
    return json.loads(r.stdout)


def test_versao_da_cache_subiu_e_o_activate_tira_as_da_v13_mas_guarda_as_marcas_e_a_epoca():
    assert int(VER.rsplit("v", 1)[1]) >= 14, VER                 # correcções da ronda 13: v15 (o test_ronda13b_sw.py confere a versão exacta)
    r = sw([], evento="activate", caches={"basalt-v13-app": ["/"], "basalt-v13-vendor": ["/static/vendor/x.js"],
                                          "basalt-v13-api-d-a": ["/api/notas"], "basaltcfg-epoca": ["/__epoca"],
                                          "basaltcfg-apagadas-d-a": ["/api/nota?nome=x"]})
    assert sorted(r["caches"]) == ["basaltcfg-apagadas-d-a", "basaltcfg-epoca"], r["caches"]


# ── achado 1 da r10 ──
def test_resposta_da_ultima_releitura_ja_em_mao_nao_se_entrega_depois_de_um_404_que_acabou():
    c = corrida("ultimo")
    assert c["antes"] == "ANTIGA" and c["parou"] and c["meio"]["achou"], f"o ensaio não montou a corrida: {c}"
    assert c["estado"] == 404 and c["meio"]["lapide"] and c["meio"]["guardada"] is None, f"o 404 não acabou a meio: {c}"
    assert c["offline"] == "recusou", f"a Response da última releitura entregou-se depois do 404 (lápide + apagar): {c['offline']}"
    assert c["depois"] == "recusou", c


def test_lapide_posta_depois_das_verificacoes_com_o_apagar_suspenso_nao_se_entrega():
    c = corrida("lapide_depois")
    assert c["antes"] == "ANTIGA" and c["disparou"] and c["parado"], f"o ensaio não montou a corrida: {c}"
    assert c["meio"]["lapide"] and c["meio"]["guardada"] == "ANTIGA", f"a lápide devia estar posta e a entrada ainda lá: {c}"
    assert c["offline"] == "recusou", f"a entrada já invalidada pela lápide entregou-se: {c['offline']}"
    assert c["r404"] == 404 and c["depois"] == "recusou", c


# ── achado 4 do porte / 3 da r10 ──
def test_a_copia_so_se_serve_a_pedidos_do_protocolo_5_e_leva_a_epoca():
    c = corrida("protocolo")
    assert c["antes"] == "ANTIGA", c
    assert c["sem"] == "recusou", f"um pedido sem «X-Basalt-Protocolo: 5» (página antiga) recebeu a cópia: {c['sem']}"
    assert c["com"] == "200 cache", f"controlo: o pedido da página de agora devia receber a cópia: {c}"
    assert c["cab"]["protocolo"] == "5" and c["cab"]["epoca"], f"a resposta da cópia não traz o protocolo e a época: {c['cab']}"


def test_passo_sem_protocolo_nao_recebe_a_copia_e_controlo_com_protocolo_recebe():
    sem = sw([{"url": "/api/notas", "rede": "ok"}, {"url": "/api/notas", "rede": "rede", "protocolo": ""}])
    assert "erro" in sem["passos"][1] and not sem["passos"][1].get("deCache"), sem["passos"][1]
    com = sw([{"url": "/api/notas", "rede": "ok"}, {"url": "/api/notas", "rede": "rede"}])
    assert com["passos"][1]["status"] == 200 and com["passos"][1]["deCache"], com["passos"][1]


# ── achados 1A/1B do porte e 2 da r10: o aviso às páginas ──
@pytest.mark.parametrize("caso", ["404", "401", "revogar"])
def test_404_401_e_revogar_avisam_as_paginas_e_so_acabam_depois_da_resposta(caso):
    a = corrida("aviso")[caso]
    assert a["recebeu"] == ["revogada"], f"{caso}: a página aberta não foi avisada: {a}"
    assert a["calada"] == ["revogada"], f"{caso}: a página que não responde também tem de ser avisada: {a}"
    assert a["acabou_depois_da_resposta"], f"{caso}: acabou antes de a página responder ao aviso: {a}"
    assert a["demorou"] < 2500, f"{caso}: a página calada prendeu o service worker ({a['demorou']} ms)"
    assert a["epoca"], f"{caso}: a época persistente não mudou: {a}"
    if caso == "revogar":
        assert a["estado"] == {"ok": True}, a
    else:
        assert a["estado"] == int(caso), a


# ── achado 5 do porte ──
def test_g2_put_2_a_entrada_antiga_da_lapide_ja_nao_recua():
    c = corrida("g2put2")
    assert c["preso"] and c["r10"] == 404 and c["r30"] == 404, f"o ensaio não montou a corrida: {c}"
    assert c["lapide"] == 30 and c["guardada"] is None and c["offline"] == "recusou", c
    assert c["unica"] is None, f"a entrada antiga da lápide ainda se escreve (e recuou para {c['unica']}, com a efectiva em 30)"


# ── pendente b: /api/fonte na cópia ──
FONTE = "/api/fonte?fonte=docs&ref=a.md"


def test_api_fonte_guarda_se_e_serve_se_sem_rede():
    r = sw([{"url": FONTE, "rede": "ok"}, {"url": FONTE, "rede": "rede"}])
    assert r["passos"][1].get("status") == 200 and r["passos"][1].get("deCache"), f"/api/fonte sem rede não veio da cópia: {r['passos'][1]}"


def test_api_fonte_404_deixa_lapide_e_401_tranca():
    r = sw([{"url": FONTE, "rede": "ok"}, {"url": FONTE, "rede": "404"}, {"url": FONTE, "rede": "rede"}])
    assert tem_lapide(r["caches"], FONTE) and "erro" in r["passos"][2], (r["caches"], r["passos"][2])
    r = sw([{"url": FONTE, "rede": "ok"}, {"url": FONTE, "rede": "401"}, {"url": FONTE, "rede": "rede"}])
    assert r["caches"].get("basaltcfg-copia-trancada") and "erro" in r["passos"][2], (r["caches"], r["passos"][2])
