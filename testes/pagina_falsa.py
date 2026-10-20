"""Servidor simulado e utilitários para testar a PÁGINA (basalt/static/index.html + sw.js) no Chromium do Playwright.

O servidor é falso e local (127.0.0.1, porta livre): nunca toca em notas reais. Serve a página com um «gancho» (expõe as
funções internas em `window.__t`) e a API com modos que se mudam durante o teste (`ESTADO["modo"]`): ok · 403 · 404 · login
(302 para outro domínio) · html (200 text/html: a página de login do proxy) · sem-rede (fecha a ligação sem responder). Há também «pedidos suspensos» (`sinc_solta`,
`put_solta`, `pacote_solta`, `nota_solta` = o GET à nota, `pacote_uma` = só o próximo pedido de pacote): o servidor segura a
resposta até o teste a soltar, para reproduzir corridas.
Contas (ronda 8): `/api/eu` diz o `dono` da conta que está «no browser» (`ESTADO["dono"]`; mudá-lo = outra conta entrou); um pedido
com `X-Basalt-Dono` diferente leva 403 «outra conta» (`X-Basalt-Outra-Conta: 1`), como o servidor a sério; ronda 9: sem ele leva 428
(menos /api/eu), e com o certo a resposta traz o eco `X-Basalt-Dono` (o service worker só guarda com ele). Ficam registados os donos
de cada pedido (`donos`), os textos enviados a /api/sincronizar com o dono (`sinc`) e a base deles (`sinc_base`), e os POST
a /api/sair (`sair`).

`BASALT_STATIC=DIR` testa outra versão da página (ex.: uma cópia antiga, para provar que cada teste apanha o defeito).
Esperas SEMPRE com `page.wait_for_timeout` (nunca `time.sleep`: os eventos do Playwright não chegam).
Sem Playwright (ou sem o Chromium dele) os testes de página saltam-se. As fixtures (`base`, `navegador`, `contexto`) estão no
`conftest.py`: o navegador é um só para toda a sessão (dois `sync_playwright()` ao mesmo tempo não se dão).
"""
import json
import os
import re
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import pytest

from conftest import RAIZ

STATIC = Path(os.environ.get("BASALT_STATIC") or (RAIZ / "basalt" / "static"))
VENDOR = RAIZ / "basalt" / "static" / "vendor"          # bibliotecas (iguais em todas as versões)
MARCA = "basaltcfg-copia-trancada"

GANCHO = ('window.__t = {guardarEdicaoOffline: guardarEdicaoOffline, sincronizarFila: sincronizarFila, '
          'sincronizarPacote: sincronizarPacote, idbGet: idbGet, idbGetAll: idbGetAll, idbPut: idbPut, idbDelete: idbDelete, '
          'tentarOffline: tentarOffline, desligarCopiaLocal: desligarCopiaLocal, religarCopiaLocal: religarCopiaLocal, apiGet: apiGet, '
          'apiSend: apiSend, enviarAnexo: typeof enviarAnexo === "function" ? enviarAnexo : null, trancasNaMarca: trancasNaMarca, desbloquearCopiaLocal: desbloquearCopiaLocal, '
          # ronda 11: trancar (o que um 401 faz) a meio de uma leitura
          'bloquearCopiaLocal: typeof bloquearCopiaLocal === "function" ? bloquearCopiaLocal : null, '
          'geracao: function(){ return GERACAO_COPIA; }, desligada: function(){ return COPIA_DESLIGADA; }, '
          'bloqueada: function(){ return COPIA_BLOQUEADA; }, setIdbGet: function(f){ idbGet = f; }, '
          # ronda 8 (contas no mesmo browser): com `typeof`, para o gancho servir também uma página anterior (BASALT_STATIC)
          'trocarDono: typeof trocarDono === "function" ? trocarDono : null, '
          'verificarDono: typeof verificarDono === "function" ? verificarDono : null, '
          'dono: function(){ return typeof DONO === "undefined" ? null : DONO; }, '
          'donoOk: function(){ return typeof DONO_OK === "undefined" ? null : DONO_OK; }, '
          'outraConta: function(){ return typeof OUTRA_CONTA === "undefined" ? null : OUTRA_CONTA; }, '
          # ronda 10 (G1 da r3): «a página abriu sem rede com a cópia de A» = DONO=A ainda por confirmar
          'donoPorConfirmar: function(){ if(typeof DONO_OK !== "undefined") DONO_OK = false; }, '
          # ronda 9 (achados 2-5 da ronda 8): a cópia apagada a meio, o relógio da meta e o protocolo do SW
          'confirmarGravacaoComRede: typeof confirmarGravacaoComRede === "function" ? confirmarGravacaoComRede : null, '
          'confirmarEnvioFila: typeof confirmarEnvioFila === "function" ? confirmarEnvioFila : null, '
          'swDaCopiaPronto: typeof swDaCopiaPronto === "function" ? swDaCopiaPronto : null, '
          'reservarInstante: typeof reservarInstante === "function" ? reservarInstante : null, '
          'guardarPacoteLocal: typeof guardarPacoteLocal === "function" ? guardarPacoteLocal : null, '
          # ronda 12 (A): o aviso das edições guardadas à parte (lido de novo depois de o teste mexer na cópia)
          'atualizarAvisoPendentes: typeof atualizarAvisoPendentes === "function" ? atualizarAvisoPendentes : null, '
          'protocolo: function(){ return typeof PROTOCOLO_COPIA === "undefined" ? null : PROTOCOLO_COPIA; }, '
          # ronda 13: a fila e o conflito da vista da nota (numa transacção), a época da cópia e as edições guardadas à parte
          'filaEConflitoDaCopia: typeof filaEConflitoDaCopia === "function" ? filaEConflitoDaCopia : null, '
          'setFilaEConflito: typeof filaEConflitoDaCopia === "function" ? function(f){ filaEConflitoDaCopia = f; } : null, '
          'lerEpocaCopia: typeof lerEpocaCopia === "function" ? lerEpocaCopia : null, '
          'geracaoActual: typeof geracaoActual === "function" ? geracaoActual : null, '
          'setGeracaoActual: typeof geracaoActual === "function" ? function(f){ geracaoActual = f; } : null, '
          'guardarEdicaoDeOutraConta: typeof guardarEdicaoDeOutraConta === "function" ? guardarEdicaoDeOutraConta : null, '
          'enviarPendentesEmMeuNome: typeof enviarPendentesEmMeuNome === "function" ? enviarPendentesEmMeuNome : null, '
          'accaoPendentes: typeof accaoPendentes === "function" ? accaoPendentes : null, '
          'setApiGet: function(f){ apiGet = f; }, '
          'openNovaNotaModal: typeof openNovaNotaModal === "function" ? openNovaNotaModal : null};\n  iniciarOffline();')

NOTA = {"nome": "n1", "titulo": "n1", "desc": "nota de teste", "tipo": "project", "mtime": 1758900000, "projeto": "demo",
        "editavel": True, "categorias": []}
ESTADO = {}


def reset(**k):
    ESTADO.update(modo="ok", pacote=[], put=[], sinc_pedidos=0, nota_texto="S novo", nota_hash="H2", put_solta=None,
                  sinc_409=None, sinc_503=False, pacote_solta=None, pacote_pedidos=0, nota_solta=None, put_falha_rede=False,
                  nota_status=200, idioma="pt", pacote_uma=None, dono="d-ana", modo_auth="cabecalho", donos=[], sinc=[], sinc_base=[], sair=0, anexos=[],
                  fonte_texto="TEXTO DA FONTE", fonte_status=200)
    ESTADO["sinc_solta"] = threading.Event()
    ESTADO.update(k)


reset()


class Servidor(BaseHTTPRequestHandler):
    def log_message(self, *a):
        pass

    def _envia(self, code, corpo, tipo="application/json; charset=utf-8", extra=None):
        b = corpo if isinstance(corpo, bytes) else json.dumps(corpo, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", tipo)
        self.send_header("Cache-Control", "no-cache")
        for k, v in (extra or {}).items():
            self.send_header(k, v)
        if getattr(self, "_eco", ""):                         # ronda 9: o eco do dono, como o servidor a sério
            self.send_header("X-Basalt-Dono", self._eco)
        self.send_header("Content-Length", str(len(b)))
        self.end_headers()
        self.wfile.write(b)

    def _estatico(self, caminho):
        if caminho in ("/", "/index.html"):
            html = (STATIC / "index.html").read_text(encoding="utf-8")
            assert html.count("  iniciarOffline();") == 1
            from basalt.web import indice_no_idioma          # o idioma da página, como o servidor a serve (`ESTADO["idioma"]`: pt | en)
            corpo = indice_no_idioma(html.replace("  iniciarOffline();", "  " + GANCHO).encode(), ESTADO["idioma"])
            return self._envia(200, corpo, "text/html; charset=utf-8")
        if caminho == "/sw.js":
            return self._envia(200, (STATIC / "sw.js").read_bytes(), "application/javascript")
        if caminho in ("/manifest.webmanifest", "/favicon.ico", "/icon-192.png", "/icon-512.png"):
            p = RAIZ / "basalt" / "static" / caminho[1:]
            tipo = {".webmanifest": "application/manifest+json", ".ico": "image/x-icon", ".png": "image/png"}[p.suffix]
            return self._envia(200, p.read_bytes(), tipo)
        if caminho.startswith("/static/vendor/"):
            p = VENDOR / caminho[len("/static/vendor/"):]
            if p.is_file():
                tipo = "application/javascript" if p.suffix == ".js" else "text/css" if p.suffix == ".css" else "application/octet-stream"
                return self._envia(200, p.read_bytes(), tipo)
        return self._envia(404, {"erro": "não existe"})

    def _api_ok(self, u, q):
        c = u.path
        if c == "/api/eu":
            eu = {"modo": ESTADO["modo_auth"], "email": "ana@exemplo.pt", "nome": "ana"}
            if ESTADO["dono"] is not None:                    # None = um servidor que não diz o dono
                eu["dono"] = ESTADO["dono"]
            return eu
        if c == "/api/estado":
            return {"notas": 1, "ligacoes": 0, "entrada": 0, "pedacos": 0, "ultima_foto": "2026-10-06 10:00 · teste", "modelo": "modelo-teste"}
        if c == "/api/notas":
            return [NOTA]
        if c == "/api/pacote":
            return {"gerado": 1, "notas": ESTADO["pacote"]}
        if c == "/api/nota":
            return dict(NOTA, texto=ESTADO["nota_texto"], hash=ESTADO["nota_hash"],
                        meta={"name": q.get("nome", ["n1"])[0], "description": "", "type": "project"},
                        backlinks=[], parecidas=[], historico=[], categorias=[])
        if c in ("/api/recentes", "/api/entrada", "/api/entrada/auto", "/api/unir", "/api/procura"):
            return []
        if c == "/api/indice":
            return {"markdown": "# Índice\n", "atualizado": 0, "notas": 1}
        if c == "/api/diario":
            return {"dia": "2026-10-06", "commits": [], "notas_novas": [], "notas_mudadas": [], "tarefas": [], "entrada": []}
        if c == "/api/categorias":
            return {"categorias": [], "notas": {}}
        if c == "/api/grafo":
            return {"nodes": [], "links": []}
        if c == "/api/fonte":                                 # ronda 13 (pendente b): um texto de uma fonte externa, só leitura
            ref = q.get("ref", ["a.md"])[0]
            return {"fonte": q.get("fonte", ["docs"])[0], "ref": ref, "titulo": ref, "omitidos": 0,
                    "pedacos": [{"ref": ref, "texto": ESTADO["fonte_texto"]}]}
        if c == "/api/arrumar":
            return {"duplicados": [], "tarefas_paradas": [], "pendentes": [], "commits_hoje": [], "tarefas_hoje": []}
        return {}

    def _api(self, metodo):
        self._eco = ""
        u = urllib.parse.urlparse(self.path)
        q = urllib.parse.parse_qs(u.query)
        modo = ESTADO["modo"]
        if modo == "sem-rede":
            self.close_connection = True
            return                                            # fecha sem responder → o fetch falha como sem rede
        if modo == "login":
            return self._envia(302, b"", "text/html", {"Location": "http://localhost:9/oauth2/sign_in"})
        if modo == "403":
            return self._envia(403, {"erro": "Acesso só para geral@exemplo.pt."})
        if modo == "html":                                    # ronda 5: a sessão caiu e o proxy devolve a página de login com 200
            return self._envia(200, b"<!doctype html><title>Entrar</title><p>Entra com a tua conta.</p>", "text/html; charset=utf-8")
        hd = (self.headers.get("X-Basalt-Dono") or "").strip()
        ESTADO["donos"].append((metodo, u.path, hd))
        if hd and ESTADO["dono"] and hd != ESTADO["dono"]:    # ronda 8: o cookie já é de outra conta
            if metodo in ("PUT", "POST"):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
            return self._envia(403, {"erro": "Entrou outra conta neste browser: recarrega a página antes de continuar.", "outraConta": True},
                               extra={"X-Basalt-Outra-Conta": "1"})
        if not hd and ESTADO["dono"] and u.path not in ("/api/eu", "/api/sair"):   # ronda 9: sem dono não se responde
            if metodo in ("PUT", "POST"):
                self.rfile.read(int(self.headers.get("Content-Length") or 0))
            return self._envia(428, {"erro": "Pedido sem o dono da cópia (X-Basalt-Dono): recarrega a página.", "semDono": True},
                               extra={"X-Basalt-Sem-Dono": "1"})
        if hd and hd == ESTADO["dono"]:
            self._eco = hd
        if metodo == "POST" and u.path == "/api/sair":
            self.rfile.read(int(self.headers.get("Content-Length") or 0))
            ESTADO["sair"] += 1
            return self._envia(200, {})
        if modo == "404" and u.path == "/api/nota":
            return self._envia(404, {"erro": "Nota não encontrada (ou reservada por ter dados sensíveis)."})
        if metodo == "PUT" and u.path == "/api/nota":
            corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            ESTADO["put"].append(corpo)
            if ESTADO["put_solta"] is not None:
                ESTADO["put_solta"].wait(20)                  # PUT «suspenso» até o teste o soltar
            if ESTADO["put_falha_rede"]:                      # …e acaba como falha de rede (ligação cortada sem resposta)
                self.close_connection = True
                return
            if ESTADO["nota_status"] != 200:                  # ronda 6 M8: a nota já não existe quando o PUT chega
                return self._envia(ESTADO["nota_status"], {"erro": "a nota já não existe"})
            if corpo.get("base_hash") == ESTADO["nota_hash"]:
                ESTADO["nota_texto"], ESTADO["nota_hash"] = corpo.get("texto", ""), "H3"
                return self._envia(200, {"ok": True, "hash": "H3"})
            return self._envia(409, {"erro": "A nota mudou entretanto.", "estado": "conflito", "actual": ESTADO["nota_texto"],
                                     "hash_actual": ESTADO["nota_hash"]})
        if metodo == "POST" and u.path == "/api/anexos":       # ronda 9: o envio de anexos (o corpo é o ficheiro)
            ESTADO["anexos"].append((q.get("nome", [""])[0], self.rfile.read(int(self.headers.get("Content-Length") or 0)), hd))
            return self._envia(201, {"nome": q.get("nome", [""])[0]})
        if metodo == "POST" and u.path == "/api/sincronizar":
            corpo = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            ESTADO["sinc_pedidos"] += 1
            ESTADO["sinc"].append((corpo.get("texto", ""), hd))
            ESTADO["sinc_base"].append(corpo.get("base_texto", ""))      # ronda 12: a base com que a edição subiu
            if ESTADO["sinc_503"]:
                return self._envia(503, {"erro": "ocupado", "estado": "ocupado"})
            ESTADO["sinc_solta"].wait(20)
            if ESTADO["sinc_409"]:
                return self._envia(409, dict(ESTADO["sinc_409"], teu=corpo.get("texto", "")))
            return self._envia(200, {"estado": "gravado", "hash": "h-" + corpo.get("texto", "")})
        if u.path == "/api/pacote":
            ESTADO["pacote_pedidos"] += 1
            # ronda 7 (ordem dos pacotes): o pacote leva as notas do momento em que o pedido CHEGOU ao servidor (um pacote suspenso é
            # velho); `pacote_uma` = um Event que suspende só ESTE pedido (o seguinte já não), `pacote_solta` continua a segurar todos
            notas = list(ESTADO["pacote"])
            ev, ESTADO["pacote_uma"] = ESTADO["pacote_uma"], None
            if ev is not None:
                ev.wait(20)
            if ESTADO["pacote_solta"] is not None:
                ESTADO["pacote_solta"].wait(20)               # pacote «a meio» até o teste o soltar
            return self._envia(200, {"gerado": 1, "notas": notas})
        if u.path == "/api/nota" and ESTADO["nota_solta"] is not None:
            ESTADO["nota_solta"].wait(20)                     # GET à nota «suspenso» até o teste o soltar (antes do nota_status)
        if u.path == "/api/fonte" and ESTADO["fonte_status"] != 200:   # ronda 13 (pendente b): o texto saiu da fonte (404) ou a sessão caiu (401)
            return self._envia(ESTADO["fonte_status"], {"erro": "texto não encontrado"})
        if u.path == "/api/nota" and ESTADO["nota_status"] != 200:
            return self._envia(ESTADO["nota_status"], {"erro": "a nota já não existe"})
        return self._envia(200, self._api_ok(u, q))

    def do_GET(self):
        if self.path.startswith("/api/"):
            return self._api("GET")
        return self._estatico(urllib.parse.urlparse(self.path).path)

    def do_PUT(self):
        return self._api("PUT")

    def do_POST(self):
        return self._api("POST")


# ───────────────────────── ajudantes ─────────────────────────

def esperar(page, js, ms=8000):
    for _ in range(ms // 100):
        if page.evaluate(js):
            return True
        page.wait_for_timeout(100)
    return False


def esperar_py(page, cond, ms=8000):
    for _ in range(ms // 100):
        if cond():
            return True
        page.wait_for_timeout(100)
    return False


def pronta(page):
    return esperar(page, "!!window.__t")


def com_nota(page):
    return esperar(page, "window.__t.idbGetAll('notas').then(l => l.length === 1)")


def conta_notas(page):
    return page.evaluate("window.__t ? window.__t.idbGetAll('notas').then(function(l){return l.length;}) : -1")


def buscar(page, caminho, dono="d-ana"):
    """um fetch da página (passa pelo service worker); ronda 9: com o dono da cópia, como a página ("" = sem); ronda 13: e o
    protocolo (o service worker só serve a cópia a quem o diz)."""
    return page.evaluate("""async ([c, d]) => {
        try { const r = await fetch(c, {credentials: 'same-origin', headers: Object.assign({'X-Basalt-Protocolo': '5'}, d ? {'X-Basalt-Dono': d} : {})});
              return {status: r.status, cache: r.headers.get('X-Basalt-From-Cache'), texto: await r.text()}; }
        catch (e) { return {status: 0, erro: String(e)}; } }""", [caminho, dono])


def com_sw(page, base):
    """abre a página e espera que o service worker a controle (recarrega uma vez se for preciso)."""
    page.goto(base + "/")
    pronta(page)
    if not esperar(page, "!!navigator.serviceWorker.controller", 5000):
        page.reload()
        pronta(page)
        esperar(page, "!!navigator.serviceWorker.controller", 5000)


def nome_cache_api(page):
    return page.evaluate("caches.keys().then(l => l.filter(n => /^basalt-.*-api-d-ana$/.test(n))[0] || '')")


def por_na_cache(page, nome, texto):
    """o cache.put atrasado do service worker (corre depois do «apagar»)."""
    page.evaluate("""([n, t]) => caches.open(n).then(c => c.put(location.origin + '/api/nota?nome=n1',
        new Response(JSON.stringify({nome: 'n1', texto: t}), {headers: {'Content-Type': 'application/json', 'X-Basalt-Dono': 'd-ana'}})))""", [nome, texto])


def conflito(page):
    return page.evaluate("window.__t.idbGet('conflitos', 'n1').then(function(c){ return c || null; })")


def por_conflito(page, **extra):
    c = dict(nome="n1", actual="S novo", teu="MEU", hash_actual="H2", copia=None, proposta=None, proposta_hash=None,
             proposta_tipo=None, resumo="", quando=1)
    c.update(extra)
    page.evaluate("c => window.__t.idbPut('conflitos', c)", c)


def abrir_nota_e_esperar(page, botao):
    page.evaluate("location.hash = '#/nota/n1'")
    return esperar(page, f"!!document.getElementById('{botao}')", 6000)


# O service worker carregado como função (self/caches/fetch falsos) para reproduzir corridas à letra.
def fonte_sw():
    return (STATIC / "sw.js").read_text(encoding="utf-8")


def versao_cache():
    return re.search(r'CACHE_VERSAO\s*=\s*"([^"]+)"', fonte_sw()).group(1)


SW_TRANCA_NOVA = r"""
async ([fonte, marca]) => {
  function urlDe(req){ return typeof req === "string" ? new URL(req, location.origin).href : req.url; }
  var ganchos = {};
  function Cache(nome){ this.nome = nome; this.m = new Map(); }
  Cache.prototype.put = async function(req, resp){ this.m.set(urlDe(req), resp); };
  Cache.prototype.add = async function(){};
  Cache.prototype.match = async function(req){ var r = this.m.get(urlDe(req)); return r ? r.clone() : undefined; };
  Cache.prototype.keys = async function(){ return Array.from(this.m.keys()).map(function(u){ return new Request(u); }); };
  Cache.prototype.delete = async function(req){
    if(ganchos.antesDeApagar) await ganchos.antesDeApagar(this.nome);
    return this.m.delete(urlDe(req));
  };
  var lojas = new Map();
  var caches = {
    open: async function(n){ if(!lojas.has(n)) lojas.set(n, new Cache(n)); return lojas.get(n); },
    has: async function(n){ return lojas.has(n); },
    delete: async function(n){ if(ganchos.antesDeApagar) await ganchos.antesDeApagar(n); return lojas.delete(n); },
    keys: async function(){ return Array.from(lojas.keys()); },
    match: async function(req, o){ var c = o && o.cacheName ? lojas.get(o.cacheName) : null; if(c) return c.match(req);
      for (const cc of lojas.values()){ var r = await cc.match(req); if(r) return r; } return undefined; }
  };
  var fetchFalso = async function(){ return new Response(JSON.stringify({texto: "PRIVADO"}), {status: 200, headers: {"Content-Type": "application/json", "X-Basalt-Dono": "d-ana"}}); };
  var self = {addEventListener: function(){}, skipWaiting: function(){}, clients: {claim: function(){}}, location: {origin: location.origin}};
  var sw = new Function("self", "caches", "fetch", fonte + "\nreturn {tratarApi: tratarApi, trancar: trancar, copiaTrancada: copiaTrancada};")(self, caches, fetchFalso);
  await sw.trancar();                                            // tranca antiga (um 401 de há bocado)
  await new Promise(function(r){ setTimeout(r, 30); });
  var injectada = false;
  ganchos.antesDeApagar = async function(nome){                  // no instante em que o destrancar vai apagar…
    if(nome !== marca || injectada) return;
    injectada = true; await sw.trancar();                        // …outro pedido recebe 401 e tranca de novo
  };
  // ronda 9: o SW devolve a resposta logo e guarda/destranca a seguir (o que no fetch vai para o waitUntil) — o ensaio
  // recolhe esse trabalho e espera por ele antes de olhar.
  var trabalhos = [];
  var r = await sw.tratarApi(new Request(location.origin + "/api/nota?nome=n1", {headers: {"X-Basalt-Dono": "d-ana", "X-Basalt-Protocolo": "5"}}), function(p){ trabalhos.push(p); });   // 200 → destranca a antiga
  await Promise.all(trabalhos);
  return {estado: r.status, injectada: injectada, trancada: await sw.copiaTrancada()};
}
"""

# …com a rede ATRASÁVEL: cada fetch fica pendurado até o teste o soltar — é assim que se reproduz
# «GET antigo suspenso, 410 novo, 200 antigo a chegar depois».
SW_REDE_ATRASADA = r"""
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
    match: async function(req, o){ var c = o && o.cacheName ? lojas.get(o.cacheName) : null; if(c) return c.match(req);
      for (const cc of lojas.values()){ var r = await cc.match(req); if(r) return r; } return undefined; }
  };
  var pendentes = [];
  var fetchFalso = function(){ return new Promise(function(res){ pendentes.push(res); }); };
  var self = {addEventListener: function(){}, skipWaiting: function(){}, clients: {claim: function(){}}, location: {origin: location.origin}};
  var sw = new Function("self", "caches", "fetch", fonte + "\nreturn {tratarApi: tratarApi};")(self, caches, fetchFalso);
  var dormir = function(ms){ return new Promise(function(r){ setTimeout(r, ms); }); };
  var url = location.origin + "/api/nota?nome=n1";
  var p1 = sw.tratarApi(new Request(url, {headers: {"X-Basalt-Dono": "d-ana", "X-Basalt-Protocolo": "5"}}));           // GET antigo: fica pendurado na rede
  await dormir(30);
  var p2 = sw.tratarApi(new Request(url, {headers: {"X-Basalt-Dono": "d-ana", "X-Basalt-Protocolo": "5"}}));           // GET novo
  await dormir(30);
  pendentes[1](new Response(JSON.stringify({erro: "apagada"}), {status: 410, headers: {"Content-Type": "application/json", "X-Basalt-Dono": "d-ana"}}));
  var r2 = await p2;                                 // a nota foi apagada de vez
  await dormir(30);
  pendentes[0](new Response(JSON.stringify({texto: "PRIVADO"}), {status: 200, headers: {"Content-Type": "application/json", "X-Basalt-Dono": "d-ana"}}));
  var r1 = await p1;                                 // …e só agora chega a resposta do GET antigo
  await dormir(150);
  var c = lojas.get(api); var guardada = c ? await c.match(url) : undefined;
  return {r1: r1.status, r2: r2.status, guardada: !!guardada};
}
"""
