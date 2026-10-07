"""t41r6 (07/10/2026) — a ronda seguinte da auditoria Astra «basalt-t41r4» (7 GRAVES + 2 MENORES), portada do Basalt de produção:
  servidor (basalt.unir, basalt.web)
    A1 (G1) o recuo da fusão apagava o inode que SAIU de B sem esperar qualquer graça: quem tinha B aberta perdia a escrita, sem
            ela ir para SALVOS;
    A2 (G2) um OSError (disco cheio) a meio deixava a fusão aplicada a meio — só o RuntimeError recuava;
    A3 (G3) a fusão anunciava sucesso com A já gravada por outro logo DEPOIS da troca (o confirmar só vê o inode que saiu);
    A4 (G4) «recuei tudo» era falso quando a troca original de A falhou no confirmar e a escrita ficou em SALVOS (o recuo ignorava
            as falhas);
    A7 (G7) PASSWORD_RX: «senha: 2026-10-06» e «password: 10:30:59» passavam (a exclusão de datas/horas era cega ao contexto);
  página (index.html) e service worker (sw.js)
    G5 (SW5a, SW5b, SW5c, P5a, P5b) o relógio a andar para trás destrancava a cópia com uma resposta antiga (SW5a) e deixava uma
        cópia VELHA por cima de uma NOVA (SW5c). Além do relatório: uma tranca posta por OUTRA parte depois de o pedido partir,
        mas com hora mais baixa, era apagada na mesma (SW5b no service worker; P5a/P5b na página) — agora «antes/depois» decide-se
        pela fotografia das CHAVES das trancas, não pela hora;
    G6 (P6a, P6b) a geração da cópia não era persistente nem comparada dentro das transacções: outro separador sem localStorage
        apagava e religava a cópia e a confirmação de um PUT antigo escrevia na cópia nova (P6a); sem localStorage a geração
        voltava a 0 ao recarregar (P6b);
    M8 (P8A, P8B) um 410 não invalidava duravelmente a cópia da página: um pacote que já tinha partido repunha a nota (P8A) e um
        404/410 num PUT não a tirava da cópia (P8B);
    M9 (SW9) no service worker, um 410 atrasado apagava da cache uma nota entretanto recriada e punha-lhe uma lápide.
Cada teste falha na versão anterior (`git archive 7fb48e0`; página com BASALT_STATIC=…/basalt/static) e passa nesta; os controlos
(C1, C2 e a parte «controlo» de cada teste de página/SW) passam nas duas.
"""
import errno
import os
import threading
from pathlib import Path

import pytest

from basalt import cas, unir, web
from pagina_falsa import (ESTADO, NOTA, abrir_nota_e_esperar, com_nota, esperar, esperar_py, fonte_sw, por_conflito, pronta,
                          reset, versao_cache)
from test_unir import A, B, BOA, real, git  # noqa: F401  — a memória de rascunho com git e as notas A/B/outra/MEMORY


# ───────────────────────── servidor: a fusão e o recuo ─────────────────────────
@pytest.fixture
def rapido(monkeypatch):
    monkeypatch.setattr(cas, "GRACA", 0.05)


def _salvos():
    """o que ficou guardado à parte (os temporários escondidos do CAS não contam)."""
    try:
        return sorted(p for p in cas.SALVOS.iterdir() if p.is_file() and not p.name.startswith("."))
    except OSError:
        return []


def _ler(real, nome):
    p = real / nome
    return p.read_text(encoding="utf-8") if p.exists() else None


def _correr(real):
    """"" = a fusão aplicou-se; senão o texto do erro (um erro que não seja RuntimeError vem com o tipo à frente — é o que o
    código antigo deixava sair)."""
    try:
        unir.aplicar(real, "nota_a", "nota_b", BOA, A, B)
    except RuntimeError as e:
        return str(e)
    except Exception as e:
        return f"{type(e).__name__}: {e}"
    return ""


def _estado(real, outra):
    ta, tb, to = _ler(real, "nota_a.md"), _ler(real, "nota_b.md"), _ler(real, "outra.md")
    return (f"A: {'de antes' if ta == A else 'fundida' if ta == BOA else 'APAGADA' if ta is None else 'outra coisa'}"
            f" · B: {'de antes' if tb == B else 'APAGADA' if tb is None else 'outra coisa'}"
            f" · outra: {'de antes' if to == outra else 'APAGADA' if to is None else 'ligações mudadas' if 'nota_b' not in to else 'outra coisa'}")


# A1 (G1). Critério do que conta como «escrita tardia»: o gancho `durante_graca` — o código antigo nunca o chama neste caminho,
# logo a escrita nunca acontece e perde-se (é exactamente o que a auditoria mediu: zero segundos de graça).
def test_recuo_da_fusao_espera_a_graca_e_a_escrita_tardia_de_quem_tinha_b_aberta_fica_em_salvos(real, rapido, monkeypatch):
    outra = _ler(real, "outra.md")
    chamadas, fd, escreveu = {}, [None], []

    def intruso(alvo):
        alvo = Path(alvo)
        chamadas[alvo.name] = chamadas.get(alvo.name, 0) + 1
        if alvo.name == "nota_b.md" and chamadas[alvo.name] == 1:        # um escritor abre B ANTES de ela ser retirada
            fd[0] = os.open(alvo, os.O_WRONLY)
        if alvo.name == "outra.md":                                      # uma troca seguinte falha → recuo
            alvo.unlink()
        if alvo.name == "nota_a.md" and chamadas[alvo.name] == 2:        # durante o recuo A desaparece → só se recria B (saiu=None)
            alvo.unlink()

    def tardio(alvo):                                                    # a escrita de quem tinha B aberta cai no inode que SAIU
        if Path(alvo).name == "nota_b.md" and fd[0] is not None and not escreveu:
            dados = (B + "EDIÇÃO tardia de quem tinha B aberta\n").encode("utf-8")
            os.ftruncate(fd[0], 0)
            os.pwrite(fd[0], dados, 0)
            escreveu.append(1)

    monkeypatch.setattr(cas, "antes_de_trocar", intruso)
    monkeypatch.setattr(cas, "durante_graca", tardio)
    erro = _correr(real)
    if fd[0] is not None:
        os.close(fd[0])
    guardados = [p for p in _salvos() if "EDIÇÃO tardia" in p.read_text(encoding="utf-8")]
    estado = _estado(real, outra)
    assert erro and fd[0] is not None, f"a fusão devia ter falhado e o escritor ter aberto B: {erro!r}"
    assert _ler(real, "nota_b.md") == B, estado                                   # controlo: B volta ao texto de antes
    assert "«nota_a»" in erro and "recuei tudo" not in erro, erro                 # controlo: A desapareceu no recuo e o erro diz-o
    assert escreveu, f"o recuo não deu graça a quem tinha B aberta (antes: zero graça, inode apagado) · {estado}"
    assert len(guardados) == 1, f"a edição tardia de quem tinha B aberta perdeu-se (nada em SALVOS) · {estado} · erro: {erro[:120]!r}"


# A2 (G2)
def test_oserror_de_disco_a_meio_da_fusao_tambem_recua_e_a_fusao_nao_fica_a_meio(real, rapido, monkeypatch):
    outra = _ler(real, "outra.md")
    original, vezes = cas.trocar, [0]

    def trocar_sem_disco(alvo, novo, esperado, rotulo="cas"):
        if Path(alvo).name == "outra.md" and rotulo == "unir" and not vezes[0]:
            vezes[0] = 1
            raise OSError(errno.ENOSPC, "No space left on device", str(alvo))
        return original(alvo, novo, esperado, rotulo)

    monkeypatch.setattr(cas, "trocar", trocar_sem_disco)
    erro = _correr(real)
    estado = _estado(real, outra)
    assert vezes[0] == 1 and erro, f"o disco cheio devia ter feito a fusão falhar: {erro!r}"
    assert (_ler(real, "nota_a.md"), _ler(real, "nota_b.md"), _ler(real, "outra.md")) == (A, B, outra), \
        f"a fusão ficou a meio (antes: só o RuntimeError recuava) · {estado} · erro: {erro[:120]!r}"
    assert "recuei tudo" in erro and "No space left" in erro, erro


# A3 (G3)
def test_gravar_em_a_logo_depois_da_troca_nao_anuncia_sucesso_e_a_fica_como_esse_alguem_a_deixou(real, rapido, monkeypatch):
    outra = _ler(real, "outra.md")
    agente = A + "AGENTE gravou por cima, já sem B.\n"

    def intruso(alvo):
        if Path(alvo).name == "nota_b.md":                               # entre a troca de A e a de B, um agente grava em A
            (real / "nota_a.md").write_text(agente, encoding="utf-8")

    monkeypatch.setattr(cas, "antes_de_trocar", intruso)
    erro = _correr(real)
    estado = _estado(real, outra)
    assert erro, f"a fusão anunciou sucesso com A gravada por outro logo depois da troca · {estado}"
    assert _ler(real, "nota_b.md") == B and _ler(real, "outra.md") == outra, estado      # B fica, as ligações voltam
    assert _ler(real, "nota_a.md") == agente, estado                                     # A fica como esse alguém a deixou
    assert "«nota_a»" in erro and "recuei tudo" not in erro, erro


# A4 (G4)
def test_escrita_em_a_antes_da_troca_e_outra_depois_o_erro_diz_que_a_nao_voltou_e_onde_ficou_a_primeira(real, rapido, monkeypatch):
    outra = _ler(real, "outra.md")
    ag1, ag2 = A + "AGENTE1 antes da troca.\n", BOA + "AGENTE2 depois da troca, sobre a fundida.\n"
    chamadas, feito = {}, []

    def intruso(alvo):
        alvo = Path(alvo)
        chamadas[alvo.name] = chamadas.get(alvo.name, 0) + 1
        if alvo.name == "nota_a.md" and chamadas[alvo.name] == 1:
            alvo.write_text(ag1, encoding="utf-8")

    def graca(alvo):
        if Path(alvo).name == "nota_a.md" and not feito:
            feito.append(1)
            Path(alvo).write_text(ag2, encoding="utf-8")

    monkeypatch.setattr(cas, "antes_de_trocar", intruso)
    monkeypatch.setattr(cas, "durante_graca", graca)
    erro = _correr(real)
    guardados = [p for p in _salvos() if p.read_text(encoding="utf-8") == ag1]
    estado = _estado(real, outra)
    assert erro and feito, f"a fusão devia ter falhado: {erro!r}"
    assert len(guardados) == 1, f"a 1.ª escrita (AGENTE1) devia ficar em SALVOS · {estado}"                      # controlo
    assert _ler(real, "nota_a.md") == ag2 and _ler(real, "nota_b.md") == B and _ler(real, "outra.md") == outra, estado   # controlo
    assert "recuei tudo" not in erro, f"o erro diz «recuei tudo» com A por voltar (antes: ignorava as falhas) · erro: {erro!r}"
    assert "«nota_a»" in erro and "não voltou" in erro, erro
    assert str(guardados[0]) in erro, f"o erro não diz onde ficou a 1.ª escrita: {erro!r}"


# A7 (G7)
def test_password_com_data_ou_hora_so_deixa_de_contar_quando_o_contexto_fala_de_validade():
    casos = [("senha: 2026-10-06", True), ("password: 10:30:59", True), ("senha: 06/10/2026", True),
             ("Validade da senha: 2026-10-06", False), ("a senha expira: 06/10/2026", False), ("password: 09h30 de amanhã", False),
             ("senha válida até: 2026-10-06", False), ("senha: Tr0ub4dor&3", True), ("password = hunter2!", True),
             ("senha: 2026-10-06x7", True), ("senha: está no cofre", False), ("password: ver /root/cofre", False),
             ("nota\nsenha: 06/10/2026\n", True), ("a senha do router: Tr0ub4dor&3 (validade 2027)", True)]
    maus = [(t, e) for t, e in casos if bool(web.tem_segredo_texto(t)) != e]
    assert not maus, "errados: " + "; ".join(f"«{t!r}» devia ser {'segredo' if e else 'texto'}" for t, e in maus)


# C1 — controlo
def test_controlo_fusao_sem_interferencia_aplica_apaga_b_muda_as_ligacoes_e_faz_commit(real, rapido):
    erro = _correr(real)
    assert not erro, erro
    assert _ler(real, "nota_a.md") == BOA and _ler(real, "nota_b.md") is None
    outra = _ler(real, "outra.md")
    assert "nota_b" not in outra and outra.count("nota_a") == 4, outra
    assert "unir nota_b em nota_a" in git(real, "log", "--oneline")


# C2 — controlo
def test_controlo_troca_que_falha_sem_mais_interferencia_recua_limpo_e_diz_recuei_tudo(real, rapido, monkeypatch):
    def intruso(alvo):
        alvo = Path(alvo)
        if alvo.name == "outra.md" and alvo.exists():
            alvo.unlink()

    monkeypatch.setattr(cas, "antes_de_trocar", intruso)
    erro = _correr(real)
    assert erro and "recuei tudo" in erro, erro
    assert _ler(real, "nota_a.md") == A and _ler(real, "nota_b.md") == B
    assert not _salvos(), _salvos()


# ───────────────────────── service worker: tranca por chaves, hora monótona, 410 atrasado ─────────────────────────
# O service worker carregado como função (self/caches/fetch/Date falsos): a rede fica pendurada até o teste soltar cada
# resposta, e o RELÓGIO é do teste (relogio.t) — é assim que se reproduz «o relógio andou para trás a meio de um pedido».
# A `caches.match` com cacheName NÃO cai para as outras lojas quando essa não existe (uma lápide guardada pelo URL da nota
# passaria por «cópia em cache»).
SW_PRELUDIO = r"""
async ([fonte, api]) => {
  function urlDe(req){ return typeof req === "string" ? new URL(req, location.origin).href : req.url; }
  function Cache(nome){ this.nome = nome; this.m = new Map(); }
  Cache.prototype.put = async function(req, resp){ this.m.set(urlDe(req), resp); };
  Cache.prototype.add = async function(){};
  Cache.prototype.match = async function(req){ var r = this.m.get(urlDe(req)); return r ? r.clone() : undefined; };
  Cache.prototype.keys = async function(){ return Array.from(this.m.keys()).map(function(u){ return new Request(u); }); };
  Cache.prototype.delete = async function(req){ return this.m.delete(urlDe(req)); };
  var lojas = new Map();
  var caches = {
    open: async function(n){ if(!lojas.has(n)) lojas.set(n, new Cache(n)); return lojas.get(n); },
    has: async function(n){ return lojas.has(n); },
    delete: async function(n){ return lojas.delete(n); },
    keys: async function(){ return Array.from(lojas.keys()); },
    match: async function(req, o){ var c = o && o.cacheName ? lojas.get(o.cacheName) : null; return c ? c.match(req) : undefined; }
  };
  var pendentes = [], semRede = false;
  var fetchFalso = function(){ if(semRede) return Promise.reject(new TypeError("Failed to fetch"));
    return new Promise(function(res){ pendentes.push(res); }); };
  var relogio = {t: 5000};
  class DateFalso extends Date { static now(){ return relogio.t; } }
  var self = {addEventListener: function(){}, skipWaiting: function(){}, clients: {claim: function(){}}, location: {origin: location.origin}};
  var sw = new Function("self", "caches", "fetch", "Date", fonte + "\nreturn {tratarApi: tratarApi, trancas: trancas};")(self, caches, fetchFalso, DateFalso);
  var dormir = function(ms){ return new Promise(function(r){ setTimeout(r, ms); }); };
  var url = location.origin + "/api/nota?nome=n1";
  function resp200(t){ return new Response(JSON.stringify({texto: t}), {status: 200, headers: {"Content-Type": "application/json"}}); }
  function resp401(){ return new Response(JSON.stringify({erro: "sessão expirada"}), {status: 401, headers: {"Content-Type": "application/json"}}); }
  function resp410(){ return new Response(JSON.stringify({erro: "apagada"}), {status: 410, headers: {"Content-Type": "application/json"}}); }
  // devolve {p: promessa} e não a promessa: um `return p` numa função async fazia o `await pedir()` esperar pela resposta
  // da rede que o teste ainda não soltou (bloqueio mútuo).
  async function pedir(){ var n = pendentes.length; var p = sw.tratarApi(new Request(url));
    for(var i = 0; i < 200 && pendentes.length === n; i++) await dormir(5); return {p: p}; }
  async function guardada(){ var c = lojas.get(api); var g = c ? await c.match(url) : undefined; if(!g) return null;
    return {inicio: g.headers.get("X-Basalt-Inicio"), texto: (await g.json()).texto}; }
  function trancasN(){ var c = lojas.get("basaltcfg-copia-trancada"); return c ? c.m.size : 0; }
  async function lapide(){ var c = lojas.get("basaltcfg-apagadas"); var r = c ? await c.match(url) : undefined; return r ? await r.text() : null; }
  async function porTranca(id, hora){ var c = await caches.open("basaltcfg-copia-trancada"); await c.put("/__tranca/" + id, new Response(String(hora))); }
  async function offline(){ semRede = true;
    try { var r = await sw.tratarApi(new Request(url)); return r ? (r.status + (r.headers.get("X-Basalt-From-Cache") ? " cache" : "")) : "nada"; }
    catch(e){ return "recusou"; } finally { semRede = false; } }
"""


def _sw_cenario(corpo):
    return SW_PRELUDIO + corpo + "\n}\n"


SW5A = _sw_cenario(r"""
  relogio.t = 2000;
  var p1 = (await pedir()).p;                       // GET antigo: parte a 2000 e fica pendurado
  relogio.t = 1000;                             // o relógio anda para trás
  var p2 = (await pedir()).p;                       // GET novo
  pendentes[1](resp401());                      // …recebe 401: o SW tranca a cópia (hora 1000 no antigo)
  await p2; await dormir(50);
  var trancadaAntes = trancasN();
  pendentes[0](resp200("PRIVADO"));             // só agora chega o 200 do GET antigo
  await p1; await dormir(150);
  var caso = {trancas: trancasN(), guardada: await guardada(), offline: await offline()};
  relogio.t = 3000;                             // controlo: um GET novo, DEPOIS da tranca, com 200 JSON destranca e guarda
  var p3 = (await pedir()).p; pendentes[2](resp200("NOVO")); await p3; await dormir(150);
  var controlo = {trancas: trancasN(), guardada: await guardada(), offline: await offline()};
  return {trancadaAntes: trancadaAntes, caso: caso, controlo: controlo};
""")

SW5B = _sw_cenario(r"""
  relogio.t = 2000;
  var p1 = (await pedir()).p;                       // GET parte a 2000 e fica pendurado
  await porTranca("outro", 1000);               // OUTRA parte (página, outro separador, SW reiniciado) tranca DEPOIS, com hora mais baixa
  pendentes[0](resp200("PRIVADO")); await p1; await dormir(150);
  var caso = {trancas: trancasN(), guardada: await guardada(), offline: await offline()};
  relogio.t = 3000;                             // controlo: um GET novo, depois da tranca, destranca-a e guarda
  var p2 = (await pedir()).p; pendentes[1](resp200("NOVO")); await p2; await dormir(150);
  var controlo = {trancas: trancasN(), guardada: await guardada(), offline: await offline()};
  return {caso: caso, controlo: controlo};
""")

SW5C = _sw_cenario(r"""
  relogio.t = 2000;
  var p1 = (await pedir()).p; pendentes[0](resp200("VELHO")); await p1; await dormir(150);
  var antes = await guardada();                 // VELHO guardado com hora 2000
  relogio.t = 1000;                             // o relógio anda para trás (o mesmo SW)
  var p2 = (await pedir()).p; pendentes[1](resp200("NOVO")); await p2; await dormir(150);
  var caso = await guardada();
  relogio.t = 4000;                             // controlo: dois pedidos, o 1.º atrasado — a resposta mais antiga não passa por cima
  var p3 = (await pedir()).p; relogio.t = 4050; var p4 = (await pedir()).p;
  pendentes[3](resp200("MAIS NOVO")); await p4; await dormir(100);
  pendentes[2](resp200("ATRASADO")); await p3; await dormir(150);
  var controlo = await guardada();
  return {antes: antes, caso: caso, controlo: controlo};
""")

SW9 = _sw_cenario(r"""
  relogio.t = 2000;
  var pC = (await pedir()).p;                       // GET enquanto a nota está apagada: o 410 fica pendurado
  relogio.t = 2050;
  var pD = (await pedir()).p; pendentes[1](resp200("RECRIADA")); await pD; await dormir(150);   // a nota foi recriada e um GET novo guardou-a
  var antes = await guardada();
  pendentes[0](resp410()); await pC; await dormir(150);                                      // …e só agora chega o 410 antigo
  var caso = {guardada: await guardada(), lapide: await lapide()};
  relogio.t = 3000;                             // controlo: um 410 de um pedido NOVO apaga a cópia e põe a lápide
  var pE = (await pedir()).p; pendentes[2](resp410()); await pE; await dormir(150);
  var controlo = {guardada: await guardada(), lapide: await lapide()};
  return {antes: antes, caso: caso, controlo: controlo};
""")


def _avaliar_sw(base, contexto, cenario):
    reset()
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    return page.evaluate(cenario, [fonte_sw(), versao_cache() + "-api"])


# SW5a (G5)
@pytest.mark.lento
def test_service_worker_relogio_para_tras_um_401_tranca_e_o_200_de_um_get_mais_antigo_nao_destranca_nem_fica_em_cache(base, contexto):
    r = _avaliar_sw(base, contexto, SW5A)
    c, k = r["caso"], r["controlo"]
    assert r["trancadaAntes"] == 1, f"o 401 não trancou a cópia: {r}"                                                   # controlo
    assert k["trancas"] == 0 and (k["guardada"] or {}).get("texto") == "NOVO" and k["offline"] == "200 cache", \
        f"controlo (200 novo depois da tranca) devia destrancar e guardar: {k}"
    assert c["trancas"] == 1 and c["guardada"] is None and c["offline"] == "recusou", \
        f"o 200 de um GET mais antigo destrancou/guardou: depois do 200 antigo: trancas {c['trancas']}, cache {c['guardada']}, sem rede {c['offline']}"


# SW5b (G5)
@pytest.mark.lento
def test_service_worker_tranca_de_outra_parte_posta_depois_de_o_get_partir_com_hora_mais_baixa_fica(base, contexto):
    r = _avaliar_sw(base, contexto, SW5B)
    c, k = r["caso"], r["controlo"]
    assert k["trancas"] == 0 and (k["guardada"] or {}).get("texto") == "NOVO" and k["offline"] == "200 cache", \
        f"controlo (200 novo depois da tranca) devia destrancar e guardar: {k}"
    assert c["trancas"] == 1 and c["guardada"] is None and c["offline"] == "recusou", \
        f"o 200 antigo apagou a tranca de outra parte (hora mais baixa): trancas {c['trancas']}, cache {c['guardada']}, sem rede {c['offline']}"


# SW5c (G5)
@pytest.mark.lento
def test_service_worker_relogio_para_tras_um_get_novo_continua_a_actualizar_a_copia_em_cache(base, contexto):
    r = _avaliar_sw(base, contexto, SW5C)
    assert (r["antes"] or {}).get("texto") == "VELHO", r
    assert (r["controlo"] or {}).get("texto") == "MAIS NOVO", \
        f"controlo (resposta atrasada não passa por cima) devia ficar MAIS NOVO: {r['controlo']}"
    assert (r["caso"] or {}).get("texto") == "NOVO", \
        f"com o relógio atrasado a cópia velha ficou por cima: antes {r['antes']} · depois {r['caso']}"


# SW9 (M9)
@pytest.mark.lento
def test_service_worker_um_410_atrasado_nao_apaga_da_cache_a_nota_recriada_nem_lhe_poe_lapide(base, contexto):
    r = _avaliar_sw(base, contexto, SW9)
    c, k = r["caso"], r["controlo"]
    assert (r["antes"] or {}).get("texto") == "RECRIADA", r
    assert k["guardada"] is None and k["lapide"] is not None, f"controlo (410 de um pedido novo) devia apagar e pôr lápide: {k}"
    assert (c["guardada"] or {}).get("texto") == "RECRIADA" and c["lapide"] is None, \
        f"o 410 ATRASADO apagou a nota recriada ou pôs-lhe lápide: cache {c['guardada']}, lápide {c['lapide']}"


# ───────────────────────── página: trancas, geração da cópia, 410 ─────────────────────────
TRANCA_A = """async () => { await window.__t.idbPut('meta', {chave:'bloqueada', valor:true, id:'A'});
  var c = await caches.open('basaltcfg-copia-trancada'); await c.put('/__tranca/A', new Response(String(Date.now() - 100000))); }"""
TRANCA_B_1000 = """async () => { var c = await caches.open('basaltcfg-copia-trancada'); await c.put('/__tranca/B', new Response('1000')); }"""
LER_OFFLINE = "window.__t.tentarOffline('/api/nota?nome=n1').then(n => n ? 'DEU' : 'nada', e => 'recusou')"
N_NOTAS = "window.__t.idbGetAll('notas').then(l => l.length)"


# P5a (G5)
@pytest.mark.lento
def test_pagina_pacote_em_curso_e_tranca_de_outra_parte_com_o_relogio_atrasado_a_tranca_fica(base, contexto):
    reset(pacote=[dict(NOTA, texto="PRIVADO", hash="H2")])
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    page.evaluate(TRANCA_A)                                           # trancada com o id A (um 401 de há bocado)
    antes = page.evaluate(LER_OFFLINE)
    ESTADO["pacote_solta"] = threading.Event()
    page.evaluate("void (window.__pac = window.__t.sincronizarPacote())")      # o pacote parte e fica suspenso no servidor
    page.wait_for_timeout(300)
    page.evaluate(TRANCA_B_1000)                                      # outra parte tranca B, com hora MAIS BAIXA que a partida do pacote
    page.wait_for_timeout(50)
    ESTADO["pacote_solta"].set()
    page.evaluate("window.__pac")
    page.wait_for_timeout(300)
    ids = page.evaluate("window.__t.trancasNaMarca().then(l => l.map(t => t.id))")
    ESTADO["modo"] = "sem-rede"
    depois = page.evaluate(LER_OFFLINE)
    assert antes == "recusou", antes                                  # controlo: trancada, a cópia não se lê sem rede
    assert "B" in ids, f"o pacote antigo apagou a tranca de outra parte (B): trancas depois do pacote {ids}"
    assert "A" not in ids, f"a tranca A (anterior ao pacote) devia ter saído: {ids}"
    assert depois == "recusou", f"a cópia ficou legível sem rede com a tranca B em vigor: {depois}"


# P5b (G5)
@pytest.mark.lento
def test_pagina_get_a_nota_em_curso_e_tranca_de_outra_parte_com_o_relogio_atrasado_o_200_antigo_nao_a_apaga(base, contexto):
    reset(pacote=[dict(NOTA, texto="PRIVADO", hash="H2")])
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    page.evaluate(TRANCA_A)
    antes = page.evaluate(LER_OFFLINE)
    ESTADO["nota_solta"] = threading.Event()
    page.evaluate("void (window.__get = window.__t.apiGet('/api/nota?nome=n1').then(function(){ return 'ok'; }, function(e){ return 'erro ' + e.status; }))")
    page.wait_for_timeout(300)
    page.evaluate(TRANCA_B_1000)
    page.wait_for_timeout(50)
    ESTADO["nota_solta"].set()
    r = page.evaluate("window.__get")
    page.wait_for_timeout(300)
    ids = page.evaluate("window.__t.trancasNaMarca().then(l => l.map(t => t.id))")
    ESTADO["modo"] = "sem-rede"
    depois = page.evaluate(LER_OFFLINE)
    assert antes == "recusou" and r == "ok", f"antes: {antes} · GET: {r}"                    # controlo
    assert "B" in ids, f"o 200 antigo apagou a tranca de outra parte (B): trancas depois {ids}"
    assert "A" not in ids, f"a tranca A (anterior ao pedido) devia ter saído: {ids}"
    assert depois == "recusou", f"a cópia ficou legível sem rede com a tranca B em vigor: {depois}"


# P6a (G6)
@pytest.mark.lento
def test_pagina_dois_separadores_o_segundo_sem_localstorage_apaga_e_religa_a_copia_a_confirmacao_antiga_nao_escreve_na_nova(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")], put_solta=threading.Event())
    ctx = contexto()
    a = ctx.new_page()
    a.on("dialog", lambda d: d.accept())
    a.goto(base + "/")
    pronta(a)
    com_nota(a)
    b = ctx.new_page()                                                # separador B sem localStorage (o código admite-o)
    b.add_init_script("Storage.prototype.setItem = function(){}; Storage.prototype.removeItem = function(){}; Storage.prototype.getItem = function(){ return null; };")
    b.goto(base + "/")
    pronta(b)
    por_conflito(a)
    assert abrir_nota_e_esperar(a, "conf-minha")
    a.click("#conf-minha")                                            # o PUT («MEU», base H2) parte do separador A e fica suspenso
    assert esperar_py(a, lambda: len(ESTADO["put"]) >= 1)
    b.evaluate("window.__t.desligarCopiaLocal()")                     # no separador B: «apagar a cópia deste aparelho»
    b.evaluate("window.__t.religarCopiaLocal()")                      # …e «voltar a ligar»: cópia limpa (S novo), geração nova
    assert esperar(b, "window.__t.idbGetAll('notas').then(l => l.length === 1 && l[0].texto === 'S novo')")
    ESTADO["modo"] = "sem-rede"
    ESTADO["put_solta"].set()                                         # o PUT responde 200 (gravou «MEU», hash H3)
    avisou = esperar(a, "document.body.textContent.indexOf('apagada entretanto') >= 0", 4000)
    a.wait_for_timeout(300)
    n = a.evaluate("window.__t.idbGet('notas', 'n1').then(function(x){ return x ? {texto: x.texto, hash: x.hash} : null; })")
    assert [p.get("texto") for p in ESTADO["put"]] == ["MEU"]
    assert n and n["texto"] == "S novo", f"a confirmação velha escreveu na cópia nova: {n}  (antes: 'MEU'/H3)"
    assert avisou, "a página não disse que a cópia foi apagada entretanto"


# P6b (G6)
@pytest.mark.lento
def test_pagina_sem_localstorage_a_geracao_da_copia_sobrevive_a_recarregar_a_pagina(base, contexto):
    reset(pacote=[dict(NOTA, texto="S novo", hash="H2")])
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    page.evaluate("window.__t.desligarCopiaLocal()")
    page.evaluate("window.__t.religarCopiaLocal()")
    esperar(page, "window.__t.idbGetAll('notas').then(l => l.length === 1)")
    g1 = page.evaluate("window.__t.geracao()")
    page.evaluate("localStorage.clear()")                             # o browser limpou o localStorage (ou nunca o teve)
    page.reload()
    pronta(page)
    subiu = esperar(page, "window.__t.geracao() === 1", 3000)
    g2 = page.evaluate("window.__t.geracao()")
    assert g1 == 1, f"geração depois de apagar+religar: {g1}"                                # controlo
    assert subiu and g2 == 1, f"depois de recarregar sem localStorage a geração é {g2} (antes: volta a 0)"


# P8A (M8)
@pytest.mark.lento
def test_pagina_pacote_em_curso_e_get_410_o_pacote_atrasado_nao_repoe_a_nota_e_um_pacote_novo_repoe_e_limpa_a_lapide(base, contexto):
    reset(pacote=[dict(NOTA, texto="PRIVADO", hash="H2")])
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    ESTADO["pacote_solta"] = threading.Event()
    page.evaluate("void (window.__pac = window.__t.sincronizarPacote())")      # o pacote (com a nota) parte e fica suspenso
    page.wait_for_timeout(300)
    ESTADO["nota_status"] = 410
    r = page.evaluate("window.__t.apiGet('/api/nota?nome=n1').then(() => 'ok', e => 'erro ' + e.status)")
    page.wait_for_timeout(200)
    n1 = page.evaluate(N_NOTAS)
    ESTADO["pacote_solta"].set()
    page.evaluate("window.__pac")
    page.wait_for_timeout(300)                                        # o pacote antigo chega
    n2 = page.evaluate(N_NOTAS)
    ESTADO["nota_status"] = 200                                       # controlo: a nota existe outra vez e um pacote NOVO traz-a
    ESTADO["pacote_solta"] = None
    page.evaluate("window.__t.sincronizarPacote()")
    voltou = esperar(page, N_NOTAS + ".then(n => n === 1)", 4000)
    lap = page.evaluate("window.__t.idbGet('meta', 'apagada:n1').then(x => x ? 'lápide' : 'sem lápide')")
    assert r == "erro 410" and n1 == 0, f"GET: {r} · notas depois do 410: {n1}"                # controlo
    assert n2 == 0, f"o pacote atrasado repôs a nota apagada: {n2} nota(s) (antes: 1)"
    assert voltou, "um pacote novo não repôs a nota"
    assert lap == "sem lápide", lap


# P8B (M8)
@pytest.mark.lento
def test_pagina_um_410_ou_404_num_put_tira_a_nota_da_copia_deste_aparelho_com_lapide(base, contexto):
    reset(pacote=[dict(NOTA, texto="PRIVADO", hash="H2")], nota_status=410)
    page = contexto().new_page()
    page.goto(base + "/")
    pronta(page)
    com_nota(page)
    r = page.evaluate("window.__t.apiSend('PUT', '/api/nota?nome=n1', {texto: 'X', base_hash: 'H2'}).then(() => 'ok', e => 'erro ' + e.status)")
    page.wait_for_timeout(200)
    n = page.evaluate(N_NOTAS)
    lap = page.evaluate("window.__t.idbGet('meta', 'apagada:n1').then(x => x ? 'lápide' : 'sem lápide')")
    ESTADO["modo"] = "sem-rede"
    depois = page.evaluate(LER_OFFLINE)
    ESTADO["modo"] = "ok"
    ESTADO["nota_status"] = 404                                       # e o 404
    page.evaluate("n => window.__t.idbPut('notas', n)", dict(NOTA, texto="PRIVADO", hash="H2"))
    r4 = page.evaluate("window.__t.apiSend('PUT', '/api/nota?nome=n1', {texto: 'X', base_hash: 'H2'}).then(() => 'ok', e => 'erro ' + e.status)")
    page.wait_for_timeout(200)
    n4 = page.evaluate(N_NOTAS)
    assert r == "erro 410", f"PUT: {r}"                                                       # controlo: o servidor falso devolve o 410
    assert n == 0, f"o 410 num PUT não tirou a nota da cópia: {n} nota(s) (antes: 1)"
    assert lap == "lápide", lap
    assert depois == "recusou", f"a nota ainda se lê sem rede: {depois}"
    assert r4 == "erro 404" and n4 == 0, f"404: {r4}, notas {n4}"
