"""Conflitos e edições sem rede na página: resolver um conflito só o tira quando a gravação acaba bem, nunca apaga um
conflito mais novo (de outro separador) nem uma edição sem rede mais nova que a do ecrã, e a fila não se perde quando uma
sincronização antiga termina. Uma proposta da IA nunca se grava sozinha.

Chromium do Playwright contra o servidor simulado de `pagina_falsa.py` (nunca a memória real). Saltam-se sem Playwright."""
import threading

import pytest

from pagina_falsa import (ESTADO, NOTA, abrir_nota_e_esperar, conflito, esperar, esperar_py,
                          por_conflito, pronta, reset)

pytestmark = pytest.mark.lento


def _item_da_fila(page):
    return page.evaluate("window.__t.idbGet('fila', 'n1')")


def _abrir_editor(page):
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_selector("#editar-btn", timeout=8000)
    page.click("#editar-btn")
    page.wait_for_selector("#ed-gravar", timeout=8000)
    page.wait_for_timeout(400)


# ───────── a fila de edições sem rede ─────────

def test_edicao_b_guardada_enquanto_a_a_sobe_nao_e_apagada_da_fila(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    page.evaluate("window.__t.guardarEdicaoOffline('n1', 'base', 'A')")
    page.evaluate("() => { window.__p = window.__t.sincronizarFila(); return 0; }")   # sem esperar pela promessa
    for _ in range(50):
        if ESTADO["sinc_pedidos"]:
            break
        page.wait_for_timeout(100)
    a_meio = ESTADO["sinc_pedidos"] == 1 and not ESTADO["sinc_solta"].is_set()
    page.evaluate("window.__t.guardarEdicaoOffline('n1', 'base', 'B')")
    ESTADO["sinc_solta"].set()
    page.evaluate("window.__p")
    page.wait_for_timeout(500)
    item = _item_da_fila(page)
    assert a_meio and bool(item) and item.get("texto") == "B" and item.get("base_texto") == "A", \
        (f"B guardada com A a meio: {a_meio} · fila depois: " +
         (f"texto={item.get('texto')!r} base={item.get('base_texto')!r}" if item else "VAZIA (B perdida)"))


def test_o_editor_grava_com_rede_e_nao_apaga_da_fila_uma_edicao_sem_rede_guardada_enquanto_editava(base, contexto):
    reset(sinc_503=True)
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    _abrir_editor(page)
    # outro separador guarda uma edição sem rede DEPOIS de este editor abrir — o texto do editor não a inclui
    page.evaluate("window.__t.guardarEdicaoOffline('n1', 'base antiga', 'OUTRA edição sem rede')")
    page.click("#ed-gravar")
    page.wait_for_timeout(1500)
    item = _item_da_fila(page)
    assert len(ESTADO["put"]) == 1 and bool(item) and item.get("texto") == "OUTRA edição sem rede" and item.get("base_texto") == "base antiga", \
        (f"gravações: {len(ESTADO['put'])} · fila depois: " +
         (f"texto={item.get('texto')!r} base={item.get('base_texto')!r}" if item else "VAZIA (edição perdida)"))


def test_o_editor_aberto_sobre_uma_edicao_por_enviar_nao_grava_por_cima_do_servidor_vai_pela_juncao(base, contexto):
    reset(sinc_503=True)
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    # no aparelho: editou-se sem rede a partir de «S antigo»; entretanto os agentes mudaram a nota para «S novo» (H2)
    page.evaluate("window.__t.guardarEdicaoOffline('n1', 'S antigo', 'S antigo + a minha edição sem rede')")
    _abrir_editor(page)
    page.click("#ed-gravar")
    page.wait_for_timeout(1500)
    item = _item_da_fila(page)
    por_cima = [c for c in ESTADO["put"] if c.get("base_hash") == "H2"]
    assert not por_cima and bool(item) and item.get("base_texto") == "S antigo" and item.get("texto") == "S antigo + a minha edição sem rede", \
        ((f"PUT com o hash actual do servidor ({len(por_cima)}) — apagava «S novo» sem junção · " if por_cima else "") +
         ("fila: " + (f"texto={item.get('texto')!r} base={item.get('base_texto')!r}" if item else "VAZIA")))


# ───────── os botões do ecrã de conflito ─────────

def test_ficar_com_a_minha_grava_com_o_hash_da_versao_do_servidor_que_se_esta_a_ver(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    page.evaluate("window.__t.idbPut('conflitos', {nome: 'n1', actual: 'S velho', teu: 'T minha', quando: 1})")
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_selector("#conf-minha", timeout=8000)
    page.click("#conf-minha")
    page.wait_for_timeout(1500)
    enviados = [p.get("base_hash") for p in ESTADO["put"]]
    resto = page.evaluate("window.__t.idbGet('conflitos', 'n1')")
    assert enviados[:1] == ["H2"] and ESTADO["nota_texto"] == "T minha" and not resto, \
        f"base_hash enviado: {enviados} · conflito {'resolvido' if not resto else 'por resolver'}"


def test_juntar_a_mao_so_tira_o_conflito_quando_a_gravacao_acaba_bem(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    page.evaluate("window.__t.idbPut('conflitos', {nome: 'n1', actual: 'S velho', teu: 'T minha', quando: 1})")
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_selector("#conf-mao", timeout=8000)
    page.click("#conf-mao")
    page.wait_for_timeout(800)
    durante = page.evaluate("window.__t.idbGet('conflitos', 'n1')")
    page.wait_for_selector("#ed-gravar", timeout=8000)
    page.click("#ed-gravar")
    page.wait_for_timeout(1500)
    depois = page.evaluate("window.__t.idbGet('conflitos', 'n1')")
    enviados = [p.get("base_hash") for p in ESTADO["put"]]
    assert bool(durante) and not depois and enviados[:1] == ["H2"], \
        (f"conflito durante a edição: {'guardado' if durante else 'APAGADO'} · base_hash {enviados} · "
         f"depois de gravar: {'resolvido' if not depois else 'ainda lá'}")


def test_proposta_da_ia_so_se_grava_quando_o_utilizador_carrega_nela_com_o_hash_em_que_foi_feita(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    page.evaluate("window.__t.idbPut('conflitos', {nome: 'n1', actual: 'S novo', hash_actual: 'H2', teu: 'T minha', "
                  "proposta: 'S novo\\nT minha\\n', proposta_hash: 'H2', resumo: 'juntei', quando: 1})")
    page.evaluate("location.hash = '#/nota/n1'")
    tem = page.wait_for_selector("#conf-proposta", timeout=8000) is not None
    gravado_antes = list(ESTADO["put"])
    page.click("#conf-proposta")
    page.wait_for_timeout(1500)
    assert tem and not gravado_antes and ESTADO["nota_texto"] == "S novo\nT minha\n" and [p.get("base_hash") for p in ESTADO["put"]] == ["H2"], \
        (f"botão: {'sim' if tem else 'não'} · gravações antes do clique: {len(gravado_antes)} · "
         f"texto final ok: {ESTADO['nota_texto'] == 'S novo' + chr(10) + 'T minha' + chr(10)}")


def test_a_juncao_automatica_sem_ia_aparece_como_tal_e_so_se_grava_com_o_clique(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    page.evaluate("window.__t.idbPut('conflitos', {nome: 'n1', actual: 'S novo', hash_actual: 'H2', teu: 'T minha', "
                  "proposta: 'S novo\\nT minha\\n', proposta_hash: 'H2', proposta_tipo: 'linhas', resumo: 'juntei linha a linha', quando: 1})")
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_selector("#conf-proposta", timeout=8000)
    botao = page.inner_text("#conf-proposta")
    antes = len(ESTADO["put"])
    page.click("#conf-proposta")
    page.wait_for_timeout(1200)
    assert "junção automática" in botao and antes == 0 and ESTADO["nota_texto"] == "S novo\nT minha\n", \
        f"botão «{botao}» · gravações antes do clique: {antes}"


def test_ficar_com_a_do_servidor_nao_deita_fora_uma_edicao_sem_rede_mais_nova_que_a_do_ecra(base, contexto):
    reset(sinc_503=True)
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    page.evaluate("window.__t.idbPut('conflitos', {nome: 'n1', actual: 'S novo', hash_actual: 'H2', teu: 'T minha', quando: 1})")
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_selector("#conf-servidor", timeout=8000)
    page.evaluate("window.__t.guardarEdicaoOffline('n1', 'T minha', 'T mais nova')")   # outro separador, depois do ecrã
    page.click("#conf-servidor")
    page.wait_for_timeout(1200)
    item = _item_da_fila(page)
    assert bool(item) and item.get("texto") == "T mais nova", f"fila depois: {item.get('texto') if item else 'VAZIA (perdida)'}"


# ───────── um conflito mais novo nunca se perde ─────────

def test_escolher_no_ecra_de_conflito_nao_apaga_um_conflito_mais_novo_registado_entretanto(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    page.evaluate("window.__t.idbPut('conflitos', {nome: 'n1', actual: 'S novo', hash_actual: 'H2', teu: 'T minha', quando: 1})")
    page.evaluate("location.hash = '#/nota/n1'")
    page.wait_for_selector("#conf-minha", timeout=8000)
    page.evaluate("window.__t.idbPut('conflitos', {nome: 'n1', actual: 'S novo', hash_actual: 'H2', teu: 'T OUTRA', quando: 2})")
    page.click("#conf-minha")
    page.wait_for_timeout(1500)
    resto = page.evaluate("window.__t.idbGet('conflitos', 'n1')")
    assert bool(resto) and resto.get("teu") == "T OUTRA", f"conflito depois: {resto.get('teu') if resto else 'APAGADO (o mais novo perdeu-se)'}"


def test_juntar_a_mao_e_gravar_com_rede_um_conflito_mais_novo_de_outro_separador_fica(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    por_conflito(page, quando=1)
    achou = abrir_nota_e_esperar(page, "conf-mao")
    page.click("#conf-mao")
    editor = esperar(page, "!!document.getElementById('ed-gravar')", 6000)
    page.wait_for_timeout(1500)
    por_conflito(page, teu="B NOVO", quando=2)             # outro separador registou um conflito mais novo
    page.click("#ed-gravar")
    esperar_py(page, lambda: len(ESTADO["put"]) >= 1, 6000)
    page.wait_for_timeout(1200)
    c = conflito(page)
    assert achou and editor and len(ESTADO["put"]) == 1 and bool(c) and c.get("quando") == 2 and c.get("teu") == "B NOVO", \
        f"PUTs {len(ESTADO['put'])} · conflito depois: {('quando=' + str(c.get('quando'))) if c else 'APAGADO'}"


def test_juntar_a_mao_e_gravar_sem_rede_o_conflito_mais_novo_tambem_fica(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    por_conflito(page, quando=1)
    achou = abrir_nota_e_esperar(page, "conf-mao")
    page.click("#conf-mao")
    editor = esperar(page, "!!document.getElementById('ed-gravar')", 6000)
    page.wait_for_timeout(1500)
    por_conflito(page, teu="B NOVO", quando=2)
    ESTADO["modo"] = "sem-rede"
    page.click("#ed-gravar")
    esperar(page, "window.__t.idbGet('fila', 'n1').then(f => !!f)", 9000)
    page.wait_for_timeout(800)
    fila = page.evaluate("window.__t.idbGet('fila', 'n1').then(f => !!f)")
    c = conflito(page)
    assert achou and editor and fila and bool(c) and c.get("quando") == 2, \
        f"guardado na fila: {'sim' if fila else 'não'} · conflito depois: {('quando=' + str(c.get('quando'))) if c else 'APAGADO'}"


def test_proposta_com_o_put_suspenso_conflito_mais_novo_e_409_o_409_nao_repoe_o_conflito_antigo(base, contexto):
    reset(put_solta=threading.Event())
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    por_conflito(page, quando=1, proposta="PROPOSTA", proposta_hash="H2", proposta_tipo="linhas")
    achou = abrir_nota_e_esperar(page, "conf-proposta")
    page.click("#conf-proposta")
    em_curso = esperar_py(page, lambda: len(ESTADO["put"]) >= 1, 6000)
    por_conflito(page, teu="B NOVO", quando=2)             # chega um conflito mais novo enquanto o PUT está pendente
    ESTADO["nota_hash"] = "H9"                             # e o servidor vai responder 409
    ESTADO["put_solta"].set()
    page.wait_for_timeout(1500)
    c = conflito(page)
    assert achou and em_curso and bool(c) and c.get("quando") == 2 and c.get("teu") == "B NOVO", \
        (f"PUT em curso: {'sim' if em_curso else 'não'} · conflito depois: "
         f"{('quando=' + str(c.get('quando')) + ' teu=' + str(c.get('teu'))) if c else 'APAGADO'}")


def test_abrir_o_conflito_com_rede_nao_escreve_por_cima_de_um_conflito_mais_novo(base, contexto):
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    por_conflito(page, quando=1, actual="S velho", hash_actual="H1")   # o servidor já tem «S novo»/H2 → vai actualizar
    # entre a leitura do conflito (A, quando=1) e a escrita do texto fresco, outro separador regista B (quando=2)
    # (ronda 13: a vista lê a fila e o conflito numa transacção — filaEConflitoDaCopia(); antes eram idbGet() soltos)
    page.evaluate("""() => { window.__feito = false;
      var B = {nome:'n1', actual:'S novo', teu:'B NOVO', hash_actual:'H2', copia:null,
               proposta:null, proposta_hash:null, proposta_tipo:null, resumo:'', quando:2};
      if(window.__t.setFilaEConflito){ var origF = window.__t.filaEConflitoDaCopia;
        window.__t.setFilaEConflito(async function(nome, dono, g){ var r = await origF(nome, dono, g);
          if(nome === 'n1' && !window.__feito){ window.__feito = true; await window.__t.idbPut('conflitos', B); }
          return r; }); return; }
      var orig = window.__t.idbGet;
      window.__t.setIdbGet(async function(loja, chave){ var r = await orig(loja, chave);
        if(loja === 'conflitos' && chave === 'n1' && !window.__feito){ window.__feito = true;
          await window.__t.idbPut('conflitos', B); }
        return r; }); }""")
    achou = abrir_nota_e_esperar(page, "conf-mao")
    page.wait_for_timeout(800)
    c = conflito(page)
    assert achou and page.evaluate("window.__feito") and bool(c) and c.get("quando") == 2, \
        f"conflito depois: {('quando=' + str(c.get('quando')) + ' teu=' + str(c.get('teu'))) if c else 'APAGADO'}"
