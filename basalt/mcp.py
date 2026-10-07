"""basalt.mcp — os agentes de IA (Claude Code, Codex, Cursor, claude.ai…) lêem e escrevem nas notas por MCP
(Model Context Protocol, JSON-RPC 2.0).

Transportes: stdio (`python3 -m basalt mcp --utilizador EMAIL`: o cliente lança o processo e fala por stdin/stdout; o
registo vai para stderr) e HTTP (`POST /mcp` no servidor web; SÓ `Authorization: Bearer <token>` — nunca a cookie nem o
cabeçalho do proxy, para um site alheio nunca conseguir escrever com a sessão do browser).
Uma só via de escrita: as mesmas funções da web (`gravar`/`criar`: trinco, CAS, recusa de segredos, 409 com o texto actual).
O agente lê (`ler_nota` dá o `hash`), escreve com `base_hash` e, em vez de sobrepor, recebe o conflito com a versão actual.
Não há ferramenta para apagar: por desenho um agente nunca apaga notas (apaga-se na interface, com histórico git).
"""
from __future__ import annotations

import argparse
import datetime
import importlib
import json
import re
import sys
import urllib.parse

from . import __version__, config
from .config import Config, Utilizador
from .lexical import IndiceLexical, tokens

VERSOES = ("2025-06-18", "2025-03-26", "2024-11-05")   # a primeira é a nossa; aceitam-se as outras se o cliente as pedir
CORPO_MAX = 1024 * 1024
TIPOS = ("user", "feedback", "project", "reference", "outros")
URI = "basalt://nota/"
TEXTO_MAX = 400_000

INSTRUCOES = (
    "Basalt: as notas (Markdown) do utilizador. Antes de escrever, lê a nota com ler_nota — dá o `hash` — e passa-o em "
    "escrever_nota como base_hash. Se vier `estado: conflito`, a nota mudou entretanto: lê outra vez, junta as duas versões "
    "e volta a gravar com o hash novo; nunca sobreponhas. Para juntar texto no fim usa acrescentar_nota. Para uma ideia "
    "solta, sem saberes a nota, usa guardar_ideia (fica na Entrada para o utilizador arrumar). Não há como apagar notas."
)

FERRAMENTAS = [
    {"name": "procurar", "description": "Procura notas por significado (ou por palavras, se o servidor não tiver embeddings). "
                                        "Devolve nome, título, semelhança e um trecho.",
     "inputSchema": {"type": "object", "properties": {
         "consulta": {"type": "string", "description": "o que procuras, em linguagem natural"},
         "n": {"type": "integer", "minimum": 1, "maximum": 30, "default": 8}}, "required": ["consulta"]}},
    {"name": "ler_nota", "description": "Lê uma nota inteira: texto, hash (para escrever_nota), tipo, ligações e backlinks.",
     "inputSchema": {"type": "object", "properties": {"nome": {"type": "string", "description": "nome da nota (sem .md)"}},
                     "required": ["nome"]}},
    {"name": "escrever_nota", "description": "Substitui o texto de uma nota que já existe. Exige base_hash = o hash dado por "
                                             "ler_nota; se a nota mudou entretanto devolve conflito com o texto actual (nada é sobreposto).",
     "inputSchema": {"type": "object", "properties": {
         "nome": {"type": "string"}, "texto": {"type": "string", "description": "o texto completo da nota, com o frontmatter"},
         "base_hash": {"type": "string", "description": "o hash devolvido por ler_nota"}}, "required": ["nome", "texto", "base_hash"]}},
    {"name": "acrescentar_nota", "description": "Junta um parágrafo no FIM de uma nota (lê, acrescenta e grava com CAS; repete se "
                                                "alguém gravou no mesmo instante).",
     "inputSchema": {"type": "object", "properties": {"nome": {"type": "string"}, "texto": {"type": "string"}},
                     "required": ["nome", "texto"]}},
    {"name": "criar_nota", "description": "Cria uma nota nova. Se o texto não trouxer frontmatter (---), é posto um com name, "
                                          "description e metadata.type. O nome só pode ter minúsculas, dígitos e _.",
     "inputSchema": {"type": "object", "properties": {
         "nome": {"type": "string", "pattern": "^[a-z0-9_]{2,100}$"}, "texto": {"type": "string"},
         "descricao": {"type": "string", "description": "uma linha: para que serve a nota"},
         "tipo": {"type": "string", "enum": list(TIPOS), "default": "project"}}, "required": ["nome", "texto"]}},
    {"name": "listar_notas", "description": "Lista as notas (mais recentes primeiro): nome, título, descrição, tipo, categorias, data.",
     "inputSchema": {"type": "object", "properties": {
         "tipo": {"type": "string", "enum": list(TIPOS)}, "categoria": {"type": "string"},
         "contem": {"type": "string", "description": "só notas cujo nome, título ou descrição contenham este texto"},
         "limite": {"type": "integer", "minimum": 1, "maximum": 500, "default": 100}}}},
    {"name": "guardar_ideia", "description": "Guarda uma ideia solta na Entrada do Basalt (o utilizador decide depois a que nota vai).",
     "inputSchema": {"type": "object", "properties": {"texto": {"type": "string"}}, "required": ["texto"]}},
]


class ParametrosInvalidos(ValueError):
    pass


def _erro(id_, codigo, mensagem):
    return {"jsonrpc": "2.0", "id": id_, "error": {"code": codigo, "message": mensagem}}


def _str(a, chave, obrig=False, maximo=TEXTO_MAX):
    v = a.get(chave)
    if v is None or v == "":
        if obrig:
            raise ParametrosInvalidos(f"falta «{chave}»")
        return ""
    if not isinstance(v, str):
        raise ParametrosInvalidos(f"«{chave}» tem de ser texto")
    if len(v) > maximo:
        raise ParametrosInvalidos(f"«{chave}» demasiado longo (máx. {maximo} caracteres)")
    return v


def _int(a, chave, omissao, minimo, maximo):
    v = a.get(chave, omissao)
    if isinstance(v, bool) or not isinstance(v, int):
        raise ParametrosInvalidos(f"«{chave}» tem de ser um inteiro")
    return max(minimo, min(maximo, v))


def _quando(mtime):
    return datetime.datetime.fromtimestamp(int(mtime)).isoformat(timespec="minutes")


def _trecho(texto, consulta, largura=240):
    """a primeira linha (fora do frontmatter) com uma palavra da consulta; senão a primeira linha com texto."""
    linhas = texto.split("\n")
    if linhas and linhas[0].strip() == "---":
        try:
            linhas = linhas[linhas.index("---", 1) + 1:]
        except ValueError:
            pass
    ts = set(tokens(consulta))
    for l in linhas:
        if ts & set(tokens(l)):
            return re.sub(r"\s+", " ", l.strip())[:largura]
    return next((re.sub(r"\s+", " ", l.strip())[:largura] for l in linhas if l.strip()), "")


def com_frontmatter(nome, texto, descricao="", tipo="project"):
    if texto.lstrip().startswith("---"):
        return texto
    d = re.sub(r"\s+", " ", descricao or "").strip()
    return f"---\nname: {nome}\ndescription: {d}\nmetadata:\n  type: {tipo if tipo in TIPOS else 'project'}\n---\n\n{texto}"


class ServidorMCP:
    """trata mensagens JSON-RPC em nome de um utilizador; as ferramentas correm dentro do Espaço dele."""

    def __init__(self, cfg: Config, web=None):
        self.cfg = cfg
        self._web = web

    @property
    def web(self):
        if self._web is None:
            self._web = importlib.import_module("basalt.web")
        return self._web

    # ───────────────────────────── JSON-RPC ─────────────────────────────
    def tratar(self, msg, utilizador: Utilizador):
        """uma mensagem (ou lista) → resposta (ou lista, ou None para notificações)."""
        if isinstance(msg, list):
            if not msg:
                return _erro(None, -32600, "pedido vazio")
            out = [r for r in (self.tratar(m, utilizador) for m in msg) if r is not None]
            return out or None
        if not isinstance(msg, dict) or msg.get("jsonrpc") != "2.0" or not isinstance(msg.get("method"), str):
            return _erro(msg.get("id") if isinstance(msg, dict) else None, -32600, "pedido JSON-RPC inválido")
        metodo, params, id_ = msg["method"], msg.get("params") or {}, msg.get("id")
        if id_ is None:                        # notificação (initialized, cancelled, …): nunca se responde
            return None
        if not isinstance(params, dict):
            return _erro(id_, -32602, "params tem de ser um objecto")
        try:
            if metodo == "initialize":
                r = self._initialize(params)
            elif metodo == "ping":
                r = {}
            elif metodo == "tools/list":
                r = {"tools": FERRAMENTAS}
            elif metodo == "tools/call":
                r = self._chamar(params, utilizador)
            elif metodo == "resources/list":
                r = self._recursos(utilizador)
            elif metodo == "resources/templates/list":
                r = {"resourceTemplates": [{"uriTemplate": URI + "{nome}", "name": "nota", "description": "uma nota do Basalt",
                                            "mimeType": "text/markdown"}]}
            elif metodo == "resources/read":
                r = self._ler_recurso(params, utilizador)
            else:
                return _erro(id_, -32601, f"método desconhecido: {metodo}")
        except ParametrosInvalidos as e:
            return _erro(id_, -32602, str(e))
        except Exception as e:                 # nunca se deixa cair o transporte por causa de uma ferramenta
            return _erro(id_, -32603, f"erro interno: {type(e).__name__}: {e}"[:300])
        return {"jsonrpc": "2.0", "id": id_, "result": r}

    def _initialize(self, params):
        pedida = params.get("protocolVersion")
        return {"protocolVersion": pedida if pedida in VERSOES else VERSOES[0],
                "capabilities": {"tools": {"listChanged": False}, "resources": {"subscribe": False, "listChanged": False}},
                "serverInfo": {"name": "basalt", "version": __version__}, "instructions": INSTRUCOES}

    def _espaco(self, u):
        return self.cfg.espaco_de(u).criar()

    def _chamar(self, params, u):
        nome = params.get("name")
        fn = getattr(self, "_t_" + str(nome), None) if isinstance(nome, str) and nome in {f["name"] for f in FERRAMENTAS} else None
        if fn is None:
            raise ParametrosInvalidos(f"ferramenta desconhecida: {nome}")
        args = params.get("arguments") or {}
        if not isinstance(args, dict):
            raise ParametrosInvalidos("arguments tem de ser um objecto")
        with config.dentro(self._espaco(u)):
            d, erro = fn(args, u)
        return {"content": [{"type": "text", "text": json.dumps(d, ensure_ascii=False)}], "structuredContent": d, "isError": bool(erro)}

    # ───────────────────────────── ferramentas ─────────────────────────────
    def _nota(self, nome):
        """(caminho, bytes, texto) ou None"""
        loc = self.web.localizar(nome)
        if not loc:
            return None
        b = loc[0].read_bytes()
        return loc, b, b.decode("utf-8", "replace")

    def _t_ler_nota(self, a, u):
        nome = _str(a, "nome", obrig=True, maximo=200)
        n = self._nota(nome)
        if not n:
            return {"erro": f"nota não encontrada: {nome}"}, True
        loc, b, texto = n
        p, editavel = loc[0], loc[-1]                   # localizar → (caminho, editável); no pacote todas as notas são editáveis
        campo = self.web.campo
        return {"nome": nome, "titulo": campo(texto, "name") or nome, "descricao": campo(texto, "description"),
                "tipo": campo(texto, "type") or "outros", "hash": self.web.sha(b), "editavel": bool(editavel),
                "ligacoes": sorted(self.web.ligacoes_de(texto)), "backlinks": self.web.backlinks(nome)[:30],
                "modificada": _quando(p.stat().st_mtime), "texto": texto}, False

    def _t_escrever_nota(self, a, u):
        nome, texto, base = _str(a, "nome", True, 200), _str(a, "texto", True), _str(a, "base_hash", True, 64)
        estado, d = self.web.gravar(nome, texto, base)
        if estado == 200:
            return {"ok": True, "nome": nome, "hash": d.get("hash")}, False
        d = dict(d)
        d.setdefault("estado", "conflito" if estado == 409 else "erro")
        return d, True

    def _t_acrescentar_nota(self, a, u):
        nome, texto = _str(a, "nome", True, 200), _str(a, "texto", True)
        ultimo = {}
        for _ in range(3):
            n = self._nota(nome)
            if not n:
                return {"erro": f"nota não encontrada: {nome}"}, True
            _loc, b, actual = n
            novo = actual.rstrip("\n") + "\n\n" + texto.strip() + "\n"
            estado, d = self.web.gravar(nome, novo, self.web.sha(b))
            if estado == 200:
                return {"ok": True, "nome": nome, "hash": d.get("hash")}, False
            ultimo = dict(d)
            if estado != 409:
                break
        ultimo.setdefault("estado", "erro")
        return ultimo, True

    def _t_criar_nota(self, a, u):
        nome, texto = _str(a, "nome", True, 200), _str(a, "texto", True)
        tipo = _str(a, "tipo", maximo=20) or "project"
        if tipo not in TIPOS:
            raise ParametrosInvalidos(f"tipo tem de ser um de {TIPOS}")
        estado, d = self.web.criar(nome, com_frontmatter(nome, texto, _str(a, "descricao", maximo=300), tipo))
        if estado == 201:
            return {"ok": True, "nome": nome}, False
        return dict(d, estado="erro"), True

    def _t_listar_notas(self, a, u):
        tipo, cat = _str(a, "tipo", maximo=20), _str(a, "categoria", maximo=80)
        contem, limite = _str(a, "contem", maximo=200).lower(), _int(a, "limite", 100, 1, 500)
        out = []
        for o in self.web.todas_notas():
            if tipo and o["tipo"] != tipo:
                continue
            if cat and cat not in o.get("categorias", []):
                continue
            if contem and contem not in (o["nome"] + " " + o["titulo"] + " " + o["desc"]).lower():
                continue
            out.append({"nome": o["nome"], "titulo": o["titulo"], "descricao": o["desc"], "tipo": o["tipo"],
                        "categorias": o.get("categorias", []), "modificada": _quando(o["mtime"]), "editavel": bool(o.get("editavel", True))})
            if len(out) >= limite:
                break
        return {"notas": out, "total": len(out)}, False

    def _t_procurar(self, a, u):
        consulta, n = _str(a, "consulta", True, 2000), _int(a, "n", 8, 1, 30)
        try:
            res = self.web.procura(consulta, n) or []
        except Exception:
            res = []
        out = []
        for x in res:
            nome = x.get("nome")
            if not nome:
                continue
            out.append({"nome": nome, "titulo": x.get("titulo") or nome, "semelhanca": x.get("semelhanca", x.get("score")),
                        "trecho": re.sub(r"\s+", " ", str(x.get("trecho") or x.get("texto") or ""))[:240]})
        motor = "embeddings"
        if not out:                                  # sem índice/embeddings: BM25 sobre as notas, mesmo formato
            motor, out = "lexical", self._procurar_lexical(consulta, n)
        return {"consulta": consulta, "motor": motor, "resultados": out}, False

    def _procurar_lexical(self, consulta, n):
        meta, docs = {}, []
        for o in self.web.todas_notas():
            nt = self._nota(o["nome"])
            if nt:
                meta[o["nome"]] = (o["titulo"], nt[2])
                docs.append((o["nome"], o["titulo"] + "\n" + o["desc"] + "\n" + nt[2]))
        idx = IndiceLexical().construir(docs)
        return [{"nome": id_, "titulo": meta[id_][0], "semelhanca": round(sem, 3), "trecho": _trecho(meta[id_][1], consulta)}
                for id_, _p, sem in idx.procurar(consulta, n)]

    def _t_guardar_ideia(self, a, u):
        texto = _str(a, "texto", True, 20_000).strip()
        inbox = config.actual().inbox
        inbox.parent.mkdir(parents=True, exist_ok=True)
        reg = {"t": datetime.datetime.now().isoformat(timespec="seconds"), "de": "mcp", "autor": u.email, "texto": texto, "arquivado": False}
        with open(inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps(reg, ensure_ascii=False) + "\n")
        return {"ok": True, "guardada_em": "entrada", "quando": reg["t"]}, False

    # ───────────────────────────── recursos ─────────────────────────────
    def _recursos(self, u):
        with config.dentro(self._espaco(u)):
            notas = self.web.todas_notas()
        return {"resources": [{"uri": URI + urllib.parse.quote(o["nome"], safe=":_-"), "name": o["titulo"], "description": o["desc"],
                               "mimeType": "text/markdown"} for o in notas]}

    def _ler_recurso(self, params, u):
        uri = params.get("uri")
        if not isinstance(uri, str) or not uri.startswith(URI):
            raise ParametrosInvalidos(f"uri tem de começar por {URI}")
        nome = urllib.parse.unquote(uri[len(URI):])
        with config.dentro(self._espaco(u)):
            n = self._nota(nome)
        if not n:
            raise ParametrosInvalidos(f"nota não encontrada: {nome}")
        return {"contents": [{"uri": uri, "mimeType": "text/markdown", "text": n[2]}]}


# ───────────────────────────── transporte stdio ─────────────────────────────
def stdio(servidor: ServidorMCP, utilizador: Utilizador, entrada=None, saida=None):
    """uma mensagem JSON por linha em stdin → uma resposta por linha em stdout. Nunca escrever mais nada em stdout."""
    entrada, saida = entrada or sys.stdin, saida or sys.stdout
    for linha in entrada:
        linha = linha.strip()
        if not linha:
            continue
        try:
            msg = json.loads(linha)
        except ValueError:
            resp = _erro(None, -32700, "JSON inválido")
        else:
            resp = servidor.tratar(msg, utilizador)
        if resp is not None:
            saida.write(json.dumps(resp, ensure_ascii=False) + "\n")
            saida.flush()


# ───────────────────────────── transporte HTTP (chamado pelo web.py em /mcp) ─────────────────────────────
def _responder(handler, estado, obj, extra=None):
    corpo = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    handler.send_response(estado)
    handler.send_header("Content-Type", "application/json; charset=utf-8")
    handler.send_header("Content-Length", str(len(corpo)))
    handler.send_header("Cache-Control", "no-store")
    handler.send_header("X-Content-Type-Options", "nosniff")
    for k, v in (extra or {}).items():
        handler.send_header(k, v)
    handler.end_headers()
    handler.wfile.write(corpo)


def origem_ok(origem: str, host: str) -> bool:
    """Origin (se vier) tem de ser o próprio sítio ou loopback — defesa contra DNS rebinding em servidores locais."""
    try:
        o = urllib.parse.urlsplit(origem).hostname or ""
    except ValueError:
        return False
    h = urllib.parse.urlsplit("//" + host).hostname or ""
    return bool(o) and (o == h or o in ("localhost", "127.0.0.1", "::1"))


def http(handler, servidor: ServidorMCP, autenticador):
    """um pedido a /mcp. Só POST, só Bearer (nunca cookie/cabeçalho do proxy), corpo ≤ 1 MiB; notificações → 202."""
    h = handler.headers
    if handler.command != "POST":
        return _responder(handler, 405, {"erro": "o MCP por HTTP só aceita POST"}, {"Allow": "POST"})
    origem = h.get("Origin")
    if origem and not origem_ok(origem, h.get("Host") or ""):
        return _responder(handler, 403, {"erro": "Origin não permitido"})
    desafio = {"WWW-Authenticate": 'Bearer realm="basalt"'}
    auth = (h.get("Authorization") or "").strip()
    if not auth.lower().startswith("bearer "):
        return _responder(handler, 401, {"erro": "é preciso Authorization: Bearer <token> (basalt utilizador mcp-token EMAIL)"}, desafio)
    ip = handler.client_address[0] if getattr(handler, "client_address", None) else ""
    u = autenticador.utilizador_do_pedido(h, ip)
    if u is None:
        return _responder(handler, 401, {"erro": "token inválido"}, desafio)
    try:
        n = int(h.get("Content-Length") or 0)
    except ValueError:
        n = -1
    if n < 0 or n > CORPO_MAX:
        return _responder(handler, 413, {"erro": f"corpo demasiado grande (máx. {CORPO_MAX} bytes)"})
    try:
        msg = json.loads((handler.rfile.read(n) if n else b"").decode("utf-8"))
    except ValueError:
        return _responder(handler, 400, _erro(None, -32700, "JSON inválido"))
    resp = servidor.tratar(msg, u)
    if resp is None:
        handler.send_response(202)
        handler.send_header("Content-Length", "0")
        handler.end_headers()
        return
    _responder(handler, 200, resp)


def registar(rotas: dict, cfg: Config, autenticador, web=None) -> ServidorMCP:
    """liga o MCP ao servidor web: rotas[("POST", "/mcp")] e rotas[("GET", "/mcp")] (→ 405). Chamado por web.criar_servidor."""
    servidor = ServidorMCP(cfg, web=web)
    for metodo in ("POST", "GET"):
        rotas[(metodo, "/mcp")] = lambda h, _s=servidor: http(h, _s, autenticador)
    return servidor


# ───────────────────────────── linha de comandos ─────────────────────────────
def utilizador_local(cfg: Config, email: str = "") -> Utilizador:
    if email:
        u = cfg.utilizador(email)
        if not u:
            raise SystemExit(f"basalt mcp: utilizador desconhecido: {email}")
        return u
    if len(cfg.utilizadores) == 1:
        return cfg.utilizadores[0]
    if not cfg.utilizadores:
        return Utilizador(email="local@basalt", nome="local", admin=True)
    raise SystemExit("basalt mcp: há vários utilizadores — indica --utilizador EMAIL")


def main(argv=None):
    ap = argparse.ArgumentParser(prog="basalt mcp", description="servidor MCP por stdio (para Claude Code, Codex, Cursor…)")
    ap.add_argument("--config", help="basalt.toml (omissão: BASALT_CONFIG, ./basalt.toml, ~/.config/basalt/basalt.toml)")
    ap.add_argument("--utilizador", default="", help="e-mail do utilizador em nome de quem o agente escreve")
    a = ap.parse_args(argv)
    cfg = config.carregar(a.config)
    u = utilizador_local(cfg, a.utilizador)
    esp = cfg.espaco_de(u).criar()
    print(f"basalt mcp {__version__}: {u.email} · espaço «{esp.nome}» · {len(FERRAMENTAS)} ferramentas", file=sys.stderr, flush=True)
    stdio(ServidorMCP(cfg), u)


if __name__ == "__main__":
    main()
