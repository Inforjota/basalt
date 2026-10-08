"""Ronda 8 (contas no mesmo browser) — a cópia local e a fila sem internet não atravessam contas.

A página guarda notas, fila e conflitos no browser; se a conta A sai e entra a conta B no mesmo browser, a página tem de saber
que a cópia é de outro dono. O servidor diz quem é o dono (`/api/eu` → `dono`) e recusa (403 + `X-Basalt-Outra-Conta`, SEM
mexer em nada) um pedido que traga o dono de outra conta — a edição sem internet de A nunca é gravada em nome de B. Os anexos
deixam de ficar 1 h na cache do browser: revalidam sempre e a etiqueta (ETag) depende do dono.
Na página (Chromium do Playwright contra `pagina_falsa.py`): a fila de A não sobe em nome de B, a cópia de A sai antes de chegar a
de B, «Terminar sessão» tira a cópia do browser, um separador antigo de A não escreve na cópia de B e avisa, e o service worker
esvazia a cópia da API ao entrar/sair e não tranca nada num 403 «outra conta».
Cada teste falha no código anterior a esta ronda pela razão certa (a página antiga: `BASALT_STATIC=<cópia antiga>/basalt/static`)."""
import threading

import pytest

from conftest import FM, sha
from pagina_falsa import ESTADO, NOTA, buscar, com_nota, com_sw, esperar, esperar_py, pronta, reset

ANA, BEA = "ana@exemplo.pt", "bea@exemplo.pt"
DOIS = [{"email": ANA, "espaco": "a"}, {"email": BEA, "espaco": "b"}]
OUTRO = "0" * 24                 # o dono de uma conta que não é a da sessão


@pytest.fixture
def s(servidor_local):
    return servidor_local(modo="cabecalho", utilizadores=DOIS)


def _dono(s, email):
    r = s.get("/api/eu", email=email)
    assert r.codigo == 200
    assert "dono" in r.json, "o /api/eu não diz quem é o dono da cópia local"
    return r.json["dono"]


def _nota(s, espaco, nome, texto="olá\n"):
    t = FM.format(nome=nome) + "\n" + texto
    (s.espaco(espaco).criar().copia / f"{nome}.md").write_text(t, encoding="utf-8")
    return t


def test_eu_diz_o_dono_estavel_e_diferente_por_conta(s):
    a, b = _dono(s, ANA), _dono(s, BEA)
    assert a and b and a != b
    assert _dono(s, ANA) == a                                  # estável entre pedidos
    assert _dono(s, ANA.upper()) == a                          # o email do proxy pode vir com maiúsculas
    assert ANA not in a and "@" not in a                       # opaco: não é o email


def test_get_com_dono_de_outra_conta_recusa_sem_trancar(s):
    # B deixou a página aberta (a cópia local é de B); entrou A neste browser — o cookie já é de A
    r = s.get("/api/notas", email=ANA, cabecalhos={"X-Basalt-Dono": OUTRO})
    assert r.codigo == 403, f"um pedido com o dono de outra conta foi servido ({r.codigo})"
    assert r.cabecalhos.get("X-Basalt-Outra-Conta") == "1" and r.json.get("outraConta") is True
    r = s.get("/api/pacote", email=ANA, cabecalhos={"X-Basalt-Dono": OUTRO})              # o pacote de A não vai para a cópia de B
    assert r.codigo == 403 and r.cabecalhos.get("X-Basalt-Outra-Conta") == "1"
    # controlos: sem o cabeçalho (ronda 9: 428, ver test_ronda9_donos.py) e com o dono certo
    assert s.get("/api/notas", email=ANA, dono=False).codigo == 428
    assert s.get("/api/notas", email=ANA, cabecalhos={"X-Basalt-Dono": _dono(s, ANA)}).codigo == 200
    assert s.get("/api/notas", email=ANA, cabecalhos={"X-Basalt-Dono": _dono(s, BEA)}).codigo == 403


def test_escrita_com_dono_de_outra_conta_nao_grava(s):
    t = _nota(s, "b", "comum")
    p = s.espaco("b").criar().copia / "comum.md"
    # a edição sem internet de A, na fila do browser, enviada quando já é B quem tem a sessão
    r = s.pedido("POST", "/api/sincronizar", {"nome": "comum", "base_texto": t, "texto": t + "escrito pela ana\n"},
                 email=BEA, cabecalhos={"X-Basalt-Dono": OUTRO})
    assert r.codigo == 403 and r.cabecalhos.get("X-Basalt-Outra-Conta") == "1", f"sincronizou em nome de outra conta ({r.codigo})"
    r = s.pedido("PUT", s.nota_url("comum"), {"texto": t + "escrito pela ana\n", "base_hash": sha(t)},
                 email=BEA, cabecalhos={"X-Basalt-Dono": OUTRO})
    assert r.codigo == 403
    assert p.read_text(encoding="utf-8") == t, "a edição de outra conta foi gravada"
    # o controlo: com o dono certo grava
    r = s.pedido("PUT", s.nota_url("comum"), {"texto": t + "escrito pela bea\n", "base_hash": sha(t)},
                 email=BEA, cabecalhos={"X-Basalt-Dono": _dono(s, BEA)})
    assert r.codigo == 200 and p.read_text(encoding="utf-8").endswith("escrito pela bea\n")


def test_anexo_revalida_e_etiqueta_depende_do_dono(s):
    png = b"\x89PNG\r\n\x1a\n" + b"mesmos bytes nas duas contas"
    for email in (ANA, BEA):
        r = s.pedido("POST", "/api/anexos?nome=foto.png", bruto=png, email=email, cabecalhos={"Content-Type": "image/png"})
        assert r.codigo == 201, r.corpo
    ra = s.get("/anexos/foto.png", email=ANA)
    assert ra.codigo == 200 and ra.corpo == png
    cc = ra.cabecalhos.get("Cache-Control", "")
    assert "no-cache" in cc and "max-age" not in cc, f"o anexo fica na cache do browser sem revalidar: {cc!r}"
    ea = ra.cabecalhos.get("ETag")
    assert ea, "sem ETag o browser não pode revalidar (descarrega tudo de novo)"
    assert s.get("/anexos/foto.png", email=ANA, cabecalhos={"If-None-Match": ea}).codigo == 304
    # B com a etiqueta de A (o browser guardou-a): tem de receber o SEU anexo, não um 304 para a cópia de A
    rb = s.get("/anexos/foto.png", email=BEA, cabecalhos={"If-None-Match": ea})
    assert rb.codigo == 200 and rb.cabecalhos.get("ETag") not in (None, ea)



# ───────────────────────── a página e o service worker ─────────────────────────

FILA = "window.__t.idbGetAll('fila').then(l => l.map(x => x.texto))"
TEXTOS = "window.__t.idbGetAll('notas').then(l => l.map(x => x.texto))"
LER_OFFLINE = "window.__t.tentarOffline('/api/nota?nome=n1').then(n => n ? 'DEU: ' + (n.texto || '') : 'nada', e => 'recusou')"
GUARDADO = "d => window.__t.idbGet('meta', 'pendentes-de:' + d).then(x => x ? (x.fila || []).map(i => i.texto) : null)"
GUARDAR = "([t, g]) => window.__t.guardarEdicaoOffline('n1', 'S novo', t, g).then(() => 'guardou', e => 'recusou: ' + e.message)"
# a cópia «crua» (sem as funções da página): depois de «Terminar sessão» a página já é outra
IDB_CRU = """() => new Promise(res => { const r = indexedDB.open('basalt');
  r.onerror = () => res({erro: String(r.error)});
  r.onsuccess = () => { const db = r.result, out = {};
    if(!db.objectStoreNames.contains('notas')){ db.close(); res({notas: 0, fila: 0, guardado: null, dono: null}); return; }
    const tx = db.transaction(['notas', 'fila', 'meta'], 'readonly');
    tx.objectStore('notas').count().onsuccess = e => out.notas = e.target.result;
    tx.objectStore('fila').getAll().onsuccess = e => out.fila = e.target.result.map(i => i.texto);
    tx.objectStore('meta').get('pendentes-de:d-ana').onsuccess = e => out.guardado = e.target.result ? (e.target.result.fila || []).map(i => i.texto) : null;
    tx.objectStore('meta').get('dono').onsuccess = e => out.dono = e.target.result ? e.target.result.valor : null;
    tx.oncomplete = () => { db.close(); res(out); }; }; })"""


def _confirmado(page, dono, ms=8000):
    """espera que o servidor tenha confirmado o dono (a página antiga não tem `donoOk` → null → segue logo)."""
    return esperar(page, f"window.__t.donoOk() === null || (window.__t.donoOk() === true && window.__t.dono() === '{dono}')", ms)


def _abrir(ctx, base, dono="d-ana"):
    page = ctx.new_page()
    page.on("dialog", lambda d: d.accept())
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    _confirmado(page, dono)
    return page


def _enviados(texto):
    return [h for t, h in ESTADO["sinc"] if t == texto]


# Antes: a página não sabia de quem era a cópia — a fila de A subia com a sessão de B (sem cabeçalho de dono, o servidor aceitava).
@pytest.mark.lento
def test_pagina_a_fila_sem_rede_de_ana_nao_sobe_quando_entra_bea_fica_guardada_a_parte_e_volta_quando_ana_entra(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], sinc_503=True)
    ESTADO["sinc_solta"].set()
    a = _abrir(contexto(), base)
    r = a.evaluate(GUARDAR, ["EDICAO DA ANA", a.evaluate("window.__t.geracao()")])
    a.evaluate("window.__t.sincronizarFila()")                       # o servidor está ocupado (503): fica na fila
    a.wait_for_timeout(500)
    fila_a = a.evaluate(FILA)
    ESTADO["sinc"].clear()
    ESTADO.update(dono="d-bea", sinc_503=False)                        # sai a ana, entra a bea neste browser
    a.reload()
    pronta(a)
    _confirmado(a, "d-bea")
    a.evaluate("window.__t.sincronizarFila()")
    a.wait_for_timeout(1200)
    subiu_como_bea = _enviados("EDICAO DA ANA")
    fila_b, guardado = a.evaluate(FILA), a.evaluate(GUARDADO, "d-ana")
    ESTADO["dono"] = "d-ana"                                           # a ana volta a entrar
    a.reload()
    pronta(a)
    _confirmado(a, "d-ana")
    a.evaluate("window.__t.sincronizarFila()")
    voltou = esperar_py(a, lambda: "d-ana" in _enviados("EDICAO DA ANA"), 6000)
    guardado_depois = a.evaluate(GUARDADO, "d-ana")
    visto = f"fila com a bea: {fila_b} · guardado à parte: {guardado} · enviado como bea: {subiu_como_bea}"
    assert r == "guardou" and fila_a == ["EDICAO DA ANA"], f"controlo: a edição da ana devia estar na fila ({r}, {fila_a})"
    assert not subiu_como_bea, f"a edição da ana subiu com a sessão da bea · {visto} (antes: subia sem dono)"
    assert fila_b == [], f"a fila da ana ficou na cópia da bea · {visto}"
    assert guardado == ["EDICAO DA ANA"], f"a edição da ana devia ficar guardada à parte, em nome dela · {visto}"
    assert voltou and guardado_depois is None, \
        f"quando a ana entra outra vez a edição volta à fila e sobe em nome dela: enviados {ESTADO['sinc']}, ainda guardado {guardado_depois}"


# Antes: a cópia de A (notas) ficava no browser até chegar o pacote de B — sem rede no meio, a nota de A abria com B.
@pytest.mark.lento
def test_pagina_a_copia_de_ana_sai_antes_de_chegar_o_pacote_de_bea(base, contexto):
    reset(pacote=[dict(NOTA, texto="DA ANA", hash="H1")])
    a = _abrir(contexto(), base)
    textos_a = a.evaluate(TEXTOS)
    antes = ESTADO["pacote_pedidos"]
    ESTADO.update(dono="d-bea", pacote=[dict(NOTA, texto="DA BEA", hash="H9")], pacote_solta=threading.Event())
    a.reload()
    pronta(a)
    partiu = esperar_py(a, lambda: ESTADO["pacote_pedidos"] > antes)  # o pacote da bea está «a meio»
    a.wait_for_timeout(300)
    durante, offline = a.evaluate(TEXTOS), a.evaluate(LER_OFFLINE)
    ESTADO["pacote_solta"].set()
    chegou = esperar(a, "window.__t.idbGetAll('notas').then(l => l.length === 1 && l[0].texto === 'DA BEA')")
    com_dono = [h for m, c, h in ESTADO["donos"] if c == "/api/pacote"][-1:]
    assert textos_a == ["DA ANA"] and partiu, f"controlo: cópia da ana {textos_a}, pacote da bea pedido: {partiu}"
    assert durante == [] and not offline.startswith("DEU"), \
        f"com o pacote da bea a meio, a cópia ainda tinha as notas da ana: {durante} · sem rede: {offline} (antes: ['DA ANA'])"
    assert chegou and com_dono == ["d-bea"], f"o pacote da bea devia chegar e ser pedido em nome dela: chegou {chegou}, dono {com_dono}"


# Antes: «Terminar sessão» deixava as notas e a fila da conta no browser, para a conta seguinte.
@pytest.mark.lento
def test_pagina_terminar_sessao_tira_a_copia_do_browser_e_guarda_a_fila_em_nome_de_quem_saiu(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], modo_auth="senha", sinc_503=True)
    ESTADO["sinc_solta"].set()
    a = _abrir(contexto(), base)
    r = a.evaluate(GUARDAR, ["EDICAO DA ANA", a.evaluate("window.__t.geracao()")])
    botao = esperar(a, "!!document.getElementById('sair-btn') && !document.getElementById('sair-btn').hidden")
    a.click("#sair-btn")
    saiu = esperar_py(a, lambda: ESTADO["sair"] == 1 and a.url.endswith("/entrar"), 10000)
    a.wait_for_timeout(300)
    cru = a.evaluate(IDB_CRU)
    assert r == "guardou" and botao and saiu, f"controlo: guardou {r}, botão {botao}, saiu {saiu} ({a.url}, POST /api/sair: {ESTADO['sair']})"
    assert cru.get("notas") == 0 and cru.get("fila") == [], f"a cópia ficou no browser depois de terminar a sessão: {cru} (antes: 1 nota e a fila)"
    assert cru.get("guardado") == ["EDICAO DA ANA"] and cru.get("dono") == "", f"a fila devia ficar guardada em nome de quem saiu: {cru}"


# Antes: um separador que ficou aberto com A continuava a escrever na cópia (que já era de B) e a pedir dados como se nada fosse.
@pytest.mark.lento
def test_pagina_separador_antigo_de_ana_nao_escreve_na_copia_de_bea_e_avisa_para_recarregar_sem_trancar(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")])
    ctx = contexto()
    a = _abrir(ctx, base)
    ESTADO["dono"] = "d-bea"                                           # a bea entrou noutro separador
    b = _abrir(ctx, base, "d-bea")
    ga, gb = a.evaluate("window.__t.geracao()"), b.evaluate("window.__t.geracao()")
    r_velha = a.evaluate(GUARDAR, ["DA ANA (geração dela)", ga])
    r_nova = a.evaluate(GUARDAR, ["DA ANA (geração da bea)", gb])  # mesmo com a geração em vigor: o dono não é o dela
    fila = b.evaluate(FILA)
    get_a = a.evaluate("window.__t.apiGet('/api/notas').then(() => 'ok', e => 'erro ' + (e && e.status))")
    aviso = esperar(a, "!!document.getElementById('outra-conta-backdrop')", 3000)
    trancada = a.evaluate("window.__t.bloqueada()"), b.evaluate("window.__t.bloqueada()")
    get_b = b.evaluate("window.__t.apiGet('/api/notas').then(() => 'ok', e => 'erro ' + (e && e.status))")
    visto = f"geração da ana: {r_velha} · geração da bea: {r_nova} · fila: {fila}"
    assert fila == [], f"o separador antigo da ana escreveu na cópia da bea · {visto} (antes: a edição entrava na fila)"
    assert r_velha.startswith("recusou") and r_nova.startswith("recusou"), f"as duas gravações deviam ser recusadas · {visto}"
    assert aviso, f"o separador antigo não avisou que entrou outra conta (GET: {get_a})"
    assert trancada == (False, False), f"«outra conta» não é sessão expirada: nada se tranca (ana, bea) = {trancada}"
    assert get_b == "ok", f"controlo: o separador da bea continua a funcionar ({get_b})"


# Ronda 9 (achado G3 da ronda 2): uma cópia de antes desta versão (sem dono gravado) não se sabe de quem é. Com alguma coisa
# (fila, conflitos ou notas) trata-se como de outra conta: a fila fica à parte em «pendentes-de:?» e NÃO sobe em nome de quem
# entrou (antes ficava dela, e a edição de quem quer que a tivesse escrito subia com a sessão nova).
@pytest.mark.lento
def test_copia_sem_dono_gravado_com_fila_nao_sobe_em_nome_de_quem_entra_fica_a_parte(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], sinc_503=True)
    ESTADO["sinc_solta"].set()
    a = _abrir(contexto(), base)
    a.evaluate(GUARDAR, ["EDICAO DE ALGUEM", a.evaluate("window.__t.geracao()")])
    a.evaluate("window.__t.idbDelete('meta', 'dono')")
    a.reload()
    pronta(a)
    _confirmado(a, "d-ana")
    a.wait_for_timeout(800)
    fila, guardado = a.evaluate(FILA), a.evaluate(GUARDADO, "?")
    dono = a.evaluate("window.__t.idbGet('meta', 'dono').then(x => x ? x.valor : null)")
    visto = f"fila {fila} · à parte {guardado} · enviada em nome de {_enviados('EDICAO DE ALGUEM')}"
    assert not _enviados("EDICAO DE ALGUEM"), f"a fila de uma cópia sem dono subiu em nome de quem entrou · {visto}"
    assert fila == [] and guardado == ["EDICAO DE ALGUEM"], f"a fila da cópia sem dono devia ficar à parte, sem se perder · {visto}"
    assert dono == "d-ana", f"a cópia passa a ser da conta que entrou: {dono}"


# Controlo: uma cópia sem dono gravado mas VAZIA fica simplesmente da conta que entra (nada à parte).
@pytest.mark.lento
def test_controlo_copia_sem_dono_gravado_vazia_fica_da_conta_que_entra(base, contexto):
    reset(pacote=[])
    a = contexto().new_page()
    a.goto(base + "/")
    pronta(a)
    assert _confirmado(a, "d-ana")
    a.evaluate("window.__t.idbDelete('meta', 'dono')")
    a.reload()
    pronta(a)
    assert _confirmado(a, "d-ana"), "a cópia vazia sem dono não ficou da conta que entrou"
    a.wait_for_timeout(300)
    assert a.evaluate(GUARDADO, "?") is None and a.evaluate(FILA) == []


def _cache_e_sem_rede(page):
    ESTADO["modo"] = "ok"
    buscar(page, "/api/nota?nome=n1")
    page.wait_for_timeout(600)                                         # o service worker guardou-a
    ESTADO["modo"] = "sem-rede"
    r = buscar(page, "/api/nota?nome=n1")
    ESTADO["modo"] = "ok"
    return r


# Antes: depois de sair, a resposta da API guardada pelo service worker continuava a servir-se sem rede à conta seguinte.
@pytest.mark.lento
def test_service_worker_sair_esvazia_a_copia_da_api_e_sem_rede_a_nota_de_quem_saiu_nao_se_serve(base, contexto):
    reset(nota_texto="DA ANA (privada)")
    page = contexto(sw=True).new_page()
    com_sw(page, base)
    antes = _cache_e_sem_rede(page)
    buscar(page, "/api/nota?nome=n1")
    page.wait_for_timeout(600)
    sair = page.evaluate("fetch('/api/sair', {method: 'POST', credentials: 'same-origin', headers: {'X-Basalt': '1'}}).then(r => r.status, e => 0)")
    ESTADO["modo"] = "sem-rede"
    depois = buscar(page, "/api/nota?nome=n1")
    ESTADO["modo"] = "ok"
    fuga = "DA ANA" in (depois.get("texto") or "")
    assert "DA ANA" in (antes.get("texto") or "") and antes.get("cache") == "1", f"controlo: sem rede a cópia da API devia servir ({antes})"
    assert sair == 200 and ESTADO["sair"] == 1, f"controlo: o POST /api/sair devia chegar ao servidor ({sair}, {ESTADO['sair']})"
    assert not fuga, f"depois de sair, sem rede o service worker serviu a nota da conta que saiu: {depois.get('status')} (antes: 200 da cache)"


# Antes: o service worker tratava o 403 «outra conta» como sessão expirada e trancava a cópia.
@pytest.mark.lento
def test_service_worker_403_outra_conta_nao_tranca_e_o_403_normal_tranca(base, contexto):
    page = contexto(sw=True).new_page()
    com_sw(page, base)
    ESTADO["modo"] = "ok"
    outra = page.evaluate("fetch('/api/notas', {credentials: 'same-origin', headers: {'X-Basalt-Dono': 'd-outra'}}).then(r => r.status, e => 0)")
    page.wait_for_timeout(300)
    n_outra = page.evaluate("window.__t.trancasNaMarca().then(l => l.length)")
    ESTADO["modo"] = "403"
    normal = buscar(page, "/api/notas").get("status")
    ESTADO["modo"] = "ok"
    page.wait_for_timeout(300)
    n_normal = page.evaluate("window.__t.trancasNaMarca().then(l => l.length)")
    assert outra == 403, f"controlo: o servidor devia recusar o dono de outra conta ({outra})"
    assert n_outra == 0, f"o 403 «outra conta» trancou a cópia ({n_outra} trancas; antes: 1)"
    assert normal == 403 and n_normal >= 1, f"controlo: um 403 normal tranca ({normal}, {n_normal} trancas)"
