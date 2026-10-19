"""basalt.oauth — «Traz a tua IA»: servidor de autorização OAuth 2.1 para o MCP remoto (/mcp).

O claude.ai (site, Desktop, telemóvel) e o ChatGPT não aceitam um cabeçalho `Authorization` fixo: ligam-se a um servidor MCP
remoto só por OAuth. Este módulo é esse servidor, dentro do próprio Basalt e só com a biblioteca padrão do Python.

Só existe com `[basalt] url_publico` definido e fora do modo aberto (`Config.oauth_activo`); sem isso, `/.well-known/*` e
`/oauth/*` respondem 404 e o /mcp continua só com tokens `bslt_`.

O que se segue (e porquê):
  - metadados RFC 9728 (recurso) e RFC 8414 (servidor); `iss` em todas as respostas que voltam à aplicação (RFC 9207);
  - clientes só por registo dinâmico (RFC 7591), sempre públicos (PKCE S256 obrigatório, sem segredo de cliente);
  - endereços de regresso (redirect_uri) numa LISTA FECHADA: os do claude.ai/claude.com, do ChatGPT, loopback em qualquer porta
    (aplicações no próprio computador, RFC 8252) e os que o administrador acrescentar em `[oauth] redirects_extra`;
  - consentimento na página do próprio Basalt, com a sessão do Basalt (modo senha) ou do proxy (modo cabecalho). A sessão é
    SameSite=Strict: a primeira resposta é uma página mínima que se recarrega no mesmo sítio, e só então a sessão se vê;
  - o formulário de consentimento leva um CSRF = HMAC(chave, pedido, conta, sessão), confere Origin/Sec-Fetch-Site e o dono da
    página; o pedido fica preso à primeira conta que o abriu;
  - tokens opacos guardados só como sha256: acesso `bsla_` (1 h), renovação `bslr_` (30 dias, rotativo — um token de renovação
    usado duas vezes deita abaixo a família inteira), código (10 min, gasta-se na 1.ª tentativa; usado duas vezes revoga a família);
  - cada ligação (família) fica presa à conta, ao espaço e à impressão da senha: mudar a senha, mudar a conta de espaço ou
    apagá-la desliga tudo o que essa conta tinha ligado;
  - os tokens daqui (OAuth e os tokens pessoais criados na página «Ligar a tua IA») SÓ valem no /mcp — nunca na API da página;
  - nada de tokens, códigos ou chaves nos registos (o registo de pedidos corta a query; aqui só se escrevem prefixos de ids).

Porte do servidor OAuth de outro projecto do autor (auditado em várias rondas), adaptado: SQLite com `BEGIN IMMEDIATE` em vez de
bloqueios por família, consentimento no mesmo processo, isolamento por dono (conta + espaço), CSP própria na página.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import html
import json
import os
import re
import secrets
import sqlite3
import sys
import threading
import time
import unicodedata
import urllib.parse
from pathlib import Path

from . import config

ESCOPO = "notas"
T_ACESSO, T_RENOVAR, T_CODIGO, T_PEDIDO = 3600, 30 * 86400, 600, 900
REGISTOS_POR_IP_HORA, REGISTOS_POR_DIA, CLIENTES_MAX = 10, 100, 2000
CLIENTE_SEM_USO_S = 86400
PENDENTES_POR_IP, PENDENTES_MAX = 20, 200
TOKEN_FALHAS_MAX, TOKEN_JANELA_S = 30, 15 * 60
PESSOAIS_MAX = 20
FORM_MAX = 16384
FORM_CONSENTIMENTO_MAX = 4096
ESPERA_S = 10
PAGINAS_MAX = 12800                 # × 4 KiB = 50 MB: a base nunca enche o disco (passado isso: 503, nada mudou)
RELOGIO = time.time                 # os testes trocam isto para fazer expirar sem esperar

P_ACESSO, P_RENOVAR, P_CLIENTE, P_PESSOAL = "bsla_", "bslr_", "bslc_", "bslt_"

LOOPBACK = {"localhost", "127.0.0.1", "::1"}
REDIRECTS_ORIGEM = (
    "https://claude.ai/api/mcp/auth_callback",
    "https://claude.com/api/mcp/auth_callback",
    "https://chatgpt.com/connector_platform_oauth_redirect",
)
_RE_CHATGPT = re.compile(r"^https://chatgpt\.com/connector/oauth/[A-Za-z0-9._~-]{1,200}$")
ANFITRIOES_OFICIAIS = {"claude.ai", "claude.com", "chatgpt.com"}
_RE_IMITA = re.compile(r"claude|anthropic|chatgpt|openai")
_RE_VERIFIER = re.compile(r"^[A-Za-z0-9\-._~]{43,128}$")
_RE_CHALLENGE = re.compile(r"^[A-Za-z0-9_-]{43}$")
RE_PEDIDO = re.compile(r"^[A-Za-z0-9_-]{20,64}$")
RE_FAMILIA = re.compile(r"^[A-Za-z0-9_-]{16,64}$")
_RE_CONTROLO = re.compile(r"[\x00-\x1f\x7f]")

SEM_CACHE = (("Cache-Control", "no-store"), ("Pragma", "no-cache"))


def agora() -> float:
    return RELOGIO()


def _h(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _avisar(evento: str, **campos) -> None:
    """uma linha no registo do servidor. Só ids curtos e nomes de anfitrião — nunca tokens, códigos, chaves nem e-mails."""
    print(time.strftime("%d/%m %H:%M:%S"), "oauth:", evento, " ".join(f"{k}={v}" for k, v in campos.items()), file=sys.stderr, flush=True)


# ───────────────────────────── base de dados (SQLite, 0600) ─────────────────────────────
class Ocupado(Exception):
    """a base ficou bloqueada mais de ESPERA_S s (ou chegou ao tecto): nada mudou; responder 503."""


_ESQUEMA = """
CREATE TABLE IF NOT EXISTS meta (chave TEXT PRIMARY KEY, valor TEXT NOT NULL);
CREATE TABLE IF NOT EXISTS clientes (
    client_id TEXT PRIMARY KEY, nome TEXT NOT NULL DEFAULT '', redirect_uris TEXT NOT NULL, ip TEXT NOT NULL,
    criado REAL NOT NULL, usado REAL);
CREATE INDEX IF NOT EXISTS clientes_ip ON clientes (ip, criado);
CREATE TABLE IF NOT EXISTS pedidos (
    id TEXT PRIMARY KEY, client_id TEXT NOT NULL, nome TEXT NOT NULL, redirect_uri TEXT NOT NULL, explicito INTEGER NOT NULL,
    state TEXT, challenge TEXT NOT NULL, ip TEXT NOT NULL, email TEXT, criado REAL NOT NULL, expira REAL NOT NULL, usado REAL);
CREATE INDEX IF NOT EXISTS pedidos_ip ON pedidos (ip);
CREATE TABLE IF NOT EXISTS familias (
    id TEXT PRIMARY KEY, tipo TEXT NOT NULL CHECK (tipo IN ('oauth', 'pessoal')), client_id TEXT NOT NULL, nome TEXT NOT NULL,
    onde TEXT NOT NULL, email TEXT NOT NULL, espaco TEXT NOT NULL, cred TEXT NOT NULL, criado REAL NOT NULL, ultimo_uso REAL,
    revogada REAL);
CREATE INDEX IF NOT EXISTS familias_conta ON familias (email, espaco);
CREATE TABLE IF NOT EXISTS codigos (
    hash TEXT PRIMARY KEY, familia TEXT NOT NULL, client_id TEXT NOT NULL, redirect_uri TEXT NOT NULL, explicito INTEGER NOT NULL,
    challenge TEXT NOT NULL, recurso TEXT NOT NULL, criado REAL NOT NULL, expira REAL NOT NULL, usado REAL);
CREATE TABLE IF NOT EXISTS tokens (
    hash TEXT PRIMARY KEY, tipo TEXT NOT NULL CHECK (tipo IN ('acesso', 'renovar', 'pessoal')), familia TEXT NOT NULL,
    recurso TEXT NOT NULL, criado REAL NOT NULL, expira REAL, usado REAL, revogado REAL);
CREATE INDEX IF NOT EXISTS tokens_familia ON tokens (familia);
"""


def _erro_sqlite(e: sqlite3.OperationalError):
    s = str(e)
    if "locked" in s or "busy" in s or "full" in s:
        return Ocupado(s)
    return None


class Transacao:
    """`with Transacao(conn) as t:` — BEGIN IMMEDIATE (um só escritor: emitir, renovar, revogar e decidir nunca se cruzam); no fim
    COMMIT, também quando se devolve um erro de negócio (um código gasto fica gasto). Uma excepção, ou `t.desfazer()`, faz ROLLBACK."""

    def __init__(self, conn):
        self.conn = conn
        self._desfazer = False

    def desfazer(self):
        self._desfazer = True

    def __enter__(self):
        try:
            self.conn.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError as e:
            raise (_erro_sqlite(e) or e)
        return self

    def __exit__(self, tipo, valor, tb):
        if tipo is None and not self._desfazer:
            try:
                self.conn.execute("COMMIT")
            except sqlite3.OperationalError as e:
                self.conn.execute("ROLLBACK")
                raise (_erro_sqlite(e) or e)
        else:
            try:
                self.conn.execute("ROLLBACK")
            except sqlite3.Error:
                pass
        return False


def _uma(conn, sql, args=()):
    linhas = conn.execute(sql, args).fetchall()
    return linhas[0] if linhas else None


# ───────────────────────────── endereços ─────────────────────────────
def redirect_valido(u) -> bool:
    """forma: https com anfitrião, ou http de loopback; sem fragmento nem credenciais, sem caracteres de controlo."""
    if not isinstance(u, str) or not u or len(u) > 500 or _RE_CONTROLO.search(u) or " " in u:
        return False
    try:
        p = urllib.parse.urlsplit(u)
        p.port
    except ValueError:
        return False
    if p.fragment or p.username or p.password or not p.hostname:
        return False
    if p.scheme == "https":
        return True
    return p.scheme == "http" and p.hostname.lower() in LOOPBACK


def _loopback(u: str) -> bool:
    try:
        p = urllib.parse.urlsplit(u)
        return p.scheme == "http" and (p.hostname or "").lower() in LOOPBACK
    except ValueError:
        return False


def redirect_permitido(cfg, u) -> bool:
    """a lista fechada: os do claude.ai/claude.com e do ChatGPT, loopback (qualquer porta), e [oauth] redirects_extra (à letra)."""
    if not redirect_valido(u):
        return False
    return u in REDIRECTS_ORIGEM or bool(_RE_CHATGPT.match(u)) or _loopback(u) or u in cfg.oauth.redirects_extra


def redirect_coincide(pedido: str, registados) -> bool:
    """à letra; no loopback a porta pode ser outra (RFC 8252 §7.3: a aplicação escolhe uma porta livre de cada vez)."""
    if pedido in registados:
        return True
    try:
        p = urllib.parse.urlsplit(pedido)
        if p.scheme != "http" or (p.hostname or "").lower() not in LOOPBACK:
            return False
        for r in registados:
            q = urllib.parse.urlsplit(r)
            if (q.scheme, (q.hostname or "").lower(), q.path, q.query) == ("http", p.hostname.lower(), p.path, p.query):
                return True
    except ValueError:
        return False
    return False


def origem_de(u: str) -> str:
    p = urllib.parse.urlsplit(u)
    return f"{p.scheme}://{p.netloc}"


def pkce_ok(verifier, challenge) -> bool:
    if not isinstance(verifier, str) or not _RE_VERIFIER.match(verifier) or not isinstance(challenge, str):
        return False
    calc = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode()
    return hmac.compare_digest(calc, challenge)


def recurso_ok(cfg, r) -> bool:
    return r in (cfg.recurso, cfg.recurso + "/")


def escopo_ok(s) -> bool:
    """sem scope, ou um scope que pede «notas» (e nada que não conheçamos)."""
    if s is None or s == "":
        return True
    return isinstance(s, str) and set(s.split()) <= {ESCOPO}


# ───────────────────────────── texto à vista ─────────────────────────────
_INVISIVEIS = {0x115F, 0x1160, 0x17B4, 0x17B5, 0x180E, 0x2800, 0x3164, 0xFFA0, 0x034F}
_CATEGORIAS_ESCONDIDAS = {"Cc", "Cf", "Cs", "Co", "Cn", "Zl", "Zp", "Mn", "Me"}


def visivel(texto, so_ascii: bool = False) -> str:
    """escapa para HTML e torna visível o que um cliente malicioso poderia esconder no nome ou no endereço (controlo, zero-width,
    marcas de direcção, espaços estranhos) como «[U+XXXX]». `so_ascii` (endereços): tudo o que não é ASCII imprimível."""
    texto = texto if isinstance(texto, str) else str(texto)
    if not so_ascii:
        texto = unicodedata.normalize("NFC", texto)
    partes = []
    for c in texto:
        cp = ord(c)
        if so_ascii:
            escondido = not (0x21 <= cp <= 0x7E)
        else:
            cat = unicodedata.category(c)
            escondido = cat in _CATEGORIAS_ESCONDIDAS or (cat == "Zs" and c != " ") or cp in _INVISIVEIS
        partes.append(f"[U+{cp:04X}]" if escondido else c)
    return html.escape("".join(partes), quote=True)


def _span(texto, so_ascii=False) -> str:
    return f"<span class=v dir=ltr>{visivel(texto, so_ascii)}</span>"


def imita_oficial(nome: str, redirect_uri: str) -> bool:
    """um nome que se faz passar pelo Claude/ChatGPT mas volta para outro sítio que não o deles (nem o próprio computador)."""
    n = re.sub(r"[^a-z]", "", unicodedata.normalize("NFKD", nome or "").encode("ascii", "ignore").decode().lower())
    if not _RE_IMITA.search(n):
        return False
    host = (urllib.parse.urlsplit(redirect_uri).hostname or "").lower()
    return host not in ANFITRIOES_OFICIAIS and host not in LOOPBACK


# ───────────────────────────── páginas (consentimento e erros) ─────────────────────────────
TEXTOS = {
    "pt": {
        "erro_titulo": "Não foi possível ligar", "erro_h1": "Não foi possível ligar a aplicação",
        "erro_volta": "Volta à aplicação (Claude, ChatGPT…) e tenta ligar outra vez.",
        "continuar": "A continuar…", "continuar_link": "Continuar",
        "titulo": "Ligar uma aplicação ao Basalt", "h1": "Ligar uma aplicação às tuas notas?",
        "app": "Aplicação", "declarado": "— nome declarado pela própria aplicação, não confirmado",
        "volta": "Volta para", "conta": "Conta",
        "local": "O endereço de regresso é LOCAL (uma aplicação neste computador). Só permitas se acabaste de a ligar tu.",
        "pode": "Vai poder", "pode_lista": ["procurar e ler as tuas notas;", "escrever, acrescentar e criar notas;",
                                            "guardar ideias na Entrada."],
        "nao_pode": "Não vai poder apagar notas, nem ver as notas de outras contas.",
        "aviso": "Se não foste tu a pedir esta ligação agora mesmo, carrega em Recusar.",
        "recusar": "Recusar", "permitir": "Permitir", "outra_conta": "Não és tu? Entrar com outra conta",
        "desligar": "Para desligar depois: no Basalt, menu ⋯ → «Ligar a tua IA» → Revogar.",
        "e_pedido": "pedido de ligação inválido", "e_expirou": "este pedido de ligação expirou ou já foi usado",
        "e_outra": "este pedido de ligação foi aberto noutra conta; volta à aplicação e liga outra vez",
        "e_sem_sessao": "o proxy não identificou nenhuma conta com acesso a este Basalt",
        "e_imita": "a aplicação diz chamar-se como o Claude ou o ChatGPT, mas volta para outro sítio — recusado",
        "e_origem": "pedido vindo de outra página", "e_csrf": "o formulário não é válido; volta à aplicação e liga outra vez",
        "e_token": "esta página não aceita tokens; abre-a no browser", "e_ocupado": "o servidor está ocupado; tenta outra vez daqui a pouco",
        "e_demasiados": "demasiados pedidos de ligação por aprovar; tenta mais tarde",
        "e_fechado": "a configuração do servidor tem um erro: ninguém entra até ser corrigida",
    },
    "en": {
        "erro_titulo": "Could not connect", "erro_h1": "Could not connect the application",
        "erro_volta": "Go back to the application (Claude, ChatGPT…) and try to connect again.",
        "continuar": "Continuing…", "continuar_link": "Continue",
        "titulo": "Connect an application to Basalt", "h1": "Connect an application to your notes?",
        "app": "Application", "declarado": "— name declared by the application itself, not verified",
        "volta": "Returns to", "conta": "Account",
        "local": "The return address is LOCAL (an application on this computer). Only allow it if you just connected it yourself.",
        "pode": "It will be able to", "pode_lista": ["search and read your notes;", "write, append to and create notes;",
                                                     "save ideas to the Inbox."],
        "nao_pode": "It will not be able to delete notes, nor see other accounts' notes.",
        "aviso": "If you did not ask for this connection just now, press Deny.",
        "recusar": "Deny", "permitir": "Allow", "outra_conta": "Not you? Sign in with another account",
        "desligar": "To disconnect later: in Basalt, menu ⋯ → «Connect your AI» → Revoke.",
        "e_pedido": "invalid connection request", "e_expirou": "this connection request has expired or was already used",
        "e_outra": "this connection request was opened by another account; go back to the application and connect again",
        "e_sem_sessao": "the proxy did not identify any account with access to this Basalt",
        "e_imita": "the application calls itself Claude or ChatGPT, but returns somewhere else — refused",
        "e_origem": "request from another page", "e_csrf": "the form is not valid; go back to the application and connect again",
        "e_token": "this page does not accept tokens; open it in the browser", "e_ocupado": "the server is busy; try again shortly",
        "e_demasiados": "too many connection requests waiting; try later",
        "e_fechado": "the server configuration has an error: nobody gets in until it is fixed",
    },
}

_CSS = ("body{font-family:system-ui,-apple-system,Segoe UI,sans-serif;background:#0b1419;color:#dfe8ec;margin:0;padding:16px}"
        ".c{max-width:520px;margin:40px auto;background:#13222b;border-radius:12px;padding:24px;box-shadow:0 1px 3px #0006}"
        "h1{font-size:20px;margin:0 0 12px}ul{padding-left:20px}li{margin:4px 0}.aviso{background:#4a3b0c;padding:10px;"
        "border-radius:8px;font-size:14px}.n{color:#9fb3bd;font-size:13px}.b{display:flex;gap:10px;margin-top:20px}"
        ".v{unicode-bidi:isolate;font-family:ui-monospace,monospace;background:#0b1419;padding:1px 4px;border-radius:4px;"
        "word-break:break-all}a{color:#7cc4e8}"
        "button{flex:1;padding:12px;border-radius:8px;border:1px solid #33505f;background:#13222b;font-size:15px;cursor:pointer;"
        "color:#dfe8ec}.p{background:#2f7fa8;color:#fff;border-color:#2f7fa8}.l{flex:none;border:0;background:none;color:#7cc4e8;"
        "padding:0;font-size:13px;text-decoration:underline;margin-top:14px}")


def _csp(form_action: str = "") -> str:
    return ("default-src 'none'; style-src 'unsafe-inline'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'"
            + (" " + form_action if form_action else ""))


def _pagina(h, status: int, titulo: str, corpo: str, idioma: str, form_action: str = "", extra=(), cabeca: str = ""):
    b = (f"<!doctype html><html lang={idioma}><head><meta charset=utf-8><meta name=viewport content='width=device-width,initial-scale=1'>"
         f"<meta name=robots content=noindex>{cabeca}<title>{html.escape(titulo)}</title><style>{_CSS}</style></head>"
         f"<body><div class=c>{corpo}</div></body></html>").encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "text/html; charset=utf-8")
    h.send_header("Content-Security-Policy", _csp(form_action))
    h.send_header("X-Frame-Options", "DENY")
    for k, v in SEM_CACHE:
        h.send_header(k, v)
    # same-origin, não no-referrer: com no-referrer o browser manda «Origin: null» no POST do formulário e a verificação do Origin falha
    h.send_header("Referrer-Policy", "same-origin")
    h.send_header("X-Content-Type-Options", "nosniff")
    for k, v in extra:
        h.send_header(k, v)
    h.send_header("Content-Length", str(len(b)))
    h.end_headers()
    h.wfile.write(b)


def _json(h, status: int, obj, extra=()):
    b = json.dumps(obj, ensure_ascii=False).encode("utf-8")
    h.send_response(status)
    h.send_header("Content-Type", "application/json; charset=utf-8")
    for k, v in SEM_CACHE:
        h.send_header(k, v)
    h.send_header("X-Content-Type-Options", "nosniff")
    for k, v in extra:
        h.send_header(k, v)
    h.send_header("Content-Length", str(len(b)))
    h.end_headers()
    h.wfile.write(b)


def _json_erro(h, erro: str, desc: str, status: int = 400):
    _json(h, status, {"error": erro, "error_description": desc})


def _redirigir(h, destino: str, status: int = 303, extra=()):
    h.send_response(status)
    h.send_header("Location", destino)
    for k, v in SEM_CACHE:
        h.send_header(k, v)
    h.send_header("Referrer-Policy", "same-origin")
    for k, v in extra:
        h.send_header(k, v)
    h.send_header("Content-Length", "0")
    h.end_headers()


def _ler_corpo(h, limite: int):
    """o corpo, ou None se não houver Content-Length válido ou passar do limite (não se lê mais do que isso)."""
    try:
        n = int(h.headers.get("Content-Length") or 0)
    except ValueError:
        return None
    if n < 0 or n > limite:
        return None
    return h.rfile.read(n) if n else b""


def _form(raw) -> dict:
    if not raw:
        return {}
    try:
        return dict(urllib.parse.parse_qsl(raw.decode("utf-8"), keep_blank_values=True))
    except (UnicodeDecodeError, ValueError):
        return {}


# ───────────────────────────── o servidor de autorização ─────────────────────────────
class OAuth:
    """um por servidor web (criar_servidor). `aut` é o Autenticador: dá a conta do pedido e diz se o basalt.toml está fechado."""

    def __init__(self, cfg, aut):
        self.cfg = cfg
        self.aut = aut
        self.caminho = Path(cfg.dados).expanduser() / "oauth.sqlite"
        self._pronta = False
        self._lock = threading.Lock()
        self._falhas: dict[str, list[float]] = {}

    # ── base ──
    def _ligar(self) -> sqlite3.Connection:
        if not self._pronta:
            with self._lock:
                if not self._pronta:
                    self.caminho.parent.mkdir(parents=True, exist_ok=True)
                    # O_NOFOLLOW: uma ligação simbólica no lugar da base não é seguida (a raiz de dados é a âncora de confiança)
                    fd = os.open(self.caminho, os.O_RDWR | os.O_CREAT | os.O_NOFOLLOW, 0o600)
                    try:
                        os.fchmod(fd, 0o600)
                    finally:
                        os.close(fd)
                    c = self._abrir()
                    try:
                        c.executescript(_ESQUEMA)
                    finally:
                        c.close()
                    self._pronta = True
        return self._abrir()

    def _abrir(self) -> sqlite3.Connection:
        if os.path.islink(self.caminho):
            raise Ocupado("a base OAuth é uma ligação simbólica — recusado")
        conn = sqlite3.connect(str(self.caminho), timeout=ESPERA_S, isolation_level=None, check_same_thread=False)
        conn.execute(f"PRAGMA busy_timeout = {ESPERA_S * 1000}")
        conn.execute("PRAGMA journal_mode = WAL")
        conn.execute("PRAGMA synchronous = FULL")
        conn.execute(f"PRAGMA max_page_count = {int(PAGINAS_MAX)}")
        return conn

    def _bd(self):
        oauth = self

        class _Ctx:
            def __enter__(self_):
                try:
                    self_.c = oauth._ligar()
                except sqlite3.OperationalError as e:
                    raise (_erro_sqlite(e) or e)
                return self_.c

            def __exit__(self_, tipo, valor, tb):
                self_.c.close()
                if isinstance(valor, sqlite3.OperationalError):
                    e = _erro_sqlite(valor)
                    if e:
                        raise e from valor
                return False
        return _Ctx()

    def _chave(self, conn) -> bytes:
        r = _uma(conn, "SELECT valor FROM meta WHERE chave='csrf'")
        if r:
            return bytes.fromhex(r[0])
        with Transacao(conn):
            r = _uma(conn, "SELECT valor FROM meta WHERE chave='csrf'")
            if r:
                return bytes.fromhex(r[0])
            k = secrets.token_bytes(32)
            conn.execute("INSERT INTO meta (chave, valor) VALUES ('csrf', ?)", (k.hex(),))
            return k

    def _limpar(self, conn, t):
        """dentro de uma Transacao: pedidos expirados ou decididos, códigos com mais de um dia, tokens expirados há mais de 7 dias,
        clientes registados que nunca chegaram a ser usados (24 h) e famílias revogadas há mais de 90 dias."""
        conn.execute("DELETE FROM pedidos WHERE expira <= ? OR usado IS NOT NULL", (t,))
        conn.execute("DELETE FROM codigos WHERE expira < ?", (t - 86400,))
        conn.execute("DELETE FROM tokens WHERE expira IS NOT NULL AND expira < ?", (t - 7 * 86400,))
        conn.execute("DELETE FROM clientes WHERE usado IS NULL AND criado < ?", (t - CLIENTE_SEM_USO_S,))
        conn.execute("DELETE FROM tokens WHERE familia IN (SELECT id FROM familias WHERE revogada IS NOT NULL AND revogada < ?)",
                     (t - 90 * 86400,))
        conn.execute("DELETE FROM familias WHERE revogada IS NOT NULL AND revogada < ?", (t - 90 * 86400,))

    # ── contas ──
    def _conta(self, email: str):
        u = self.cfg.utilizador(email)
        if u is None and self.cfg.auth.modo == "aberto" and not self.cfg.utilizadores and email == "local@basalt":
            return config.Utilizador(email="local@basalt", nome="local", admin=True)
        return u

    def _cred(self, u) -> str:
        from .auth import impressao_credencial
        return impressao_credencial(u.senha_hash)

    def _familia_viva(self, email, espaco, cred):
        """a conta da família continua a existir, no mesmo espaço e com a mesma senha? Devolve o Utilizador ou None."""
        u = self._conta(email)
        if u is None or u.espaco != espaco or not hmac.compare_digest(self._cred(u), cred):
            return None
        return u

    @staticmethod
    def _revogar_familia(conn, familia, t=None):
        """dentro de uma Transacao: a família fica marcada (nenhum par novo nasce dela) e os tokens dela caem."""
        t = agora() if t is None else t
        conn.execute("UPDATE familias SET revogada=? WHERE id=? AND revogada IS NULL", (t, familia))
        conn.execute("UPDATE tokens SET revogado=? WHERE familia=? AND revogado IS NULL", (t, familia))

    # ── o /mcp pergunta: de quem é este token? ──
    def utilizador_do_token(self, token: str):
        """o Utilizador de um token de ACESSO (`bsla_`, só com o OAuth activo, para este recurso) ou de um token PESSOAL (`bslt_`
        criado na página) — ou None. A conta tem de existir, no mesmo espaço e com a mesma senha de quando a ligação nasceu."""
        if not isinstance(token, str) or len(token) > 200:
            return None
        if token.startswith(P_ACESSO):
            if not self.cfg.oauth_activo:
                return None
            tipo = "acesso"
        elif token.startswith(P_PESSOAL):
            tipo = "pessoal"
        else:
            return None
        t = agora()
        try:
            with self._bd() as conn:
                row = _uma(conn, "SELECT f.id, f.email, f.espaco, f.cred, f.ultimo_uso, k.recurso FROM tokens k JOIN familias f ON f.id = k.familia "
                                 "WHERE k.hash=? AND k.tipo=? AND k.revogado IS NULL AND (k.expira IS NULL OR k.expira > ?) "
                                 "AND f.revogada IS NULL", (_h(token), tipo, t))
                if not row:
                    return None
                fam, email, espaco, cred, ultimo, recurso = row
                if tipo == "acesso" and recurso != self.cfg.recurso:
                    return None
                u = self._familia_viva(email, espaco, cred)
                if u is None:
                    return None
                if ultimo is None or t - ultimo > 60:      # «último uso» na página, sem escrever em todos os pedidos
                    try:
                        conn.execute("UPDATE familias SET ultimo_uso=? WHERE id=?", (t, fam))
                    except sqlite3.OperationalError:
                        pass
                return u
        except (Ocupado, sqlite3.Error, OSError):
            return None

    # ── metadados ──
    def metadados_recurso(self) -> dict:
        return {"resource": self.cfg.recurso, "authorization_servers": [self.cfg.issuer], "scopes_supported": [ESCOPO],
                "bearer_methods_supported": ["header"], "resource_name": "Basalt"}

    def metadados_servidor(self) -> dict:
        i = self.cfg.issuer
        return {"issuer": i, "authorization_endpoint": i + "/oauth/authorize", "token_endpoint": i + "/oauth/token",
                "registration_endpoint": i + "/oauth/register", "scopes_supported": [ESCOPO], "response_types_supported": ["code"],
                "response_modes_supported": ["query"], "grant_types_supported": ["authorization_code", "refresh_token"],
                "token_endpoint_auth_methods_supported": ["none"], "code_challenge_methods_supported": ["S256"],
                "authorization_response_iss_parameter_supported": True}

    # ── registo dinâmico (RFC 7591) ──
    def registar_cliente(self, raw, ip: str):
        """(estado, objecto JSON)."""
        if raw is None:
            return 400, {"error": "invalid_client_metadata", "error_description": "pedido grande demais"}
        try:
            m = json.loads(raw)
        except ValueError:
            return 400, {"error": "invalid_client_metadata", "error_description": "o corpo não é JSON"}
        if not isinstance(m, dict):
            return 400, {"error": "invalid_client_metadata", "error_description": "o corpo tem de ser um objecto JSON"}
        uris = m.get("redirect_uris")
        if not isinstance(uris, list) or not 1 <= len(uris) <= 5:
            return 400, {"error": "invalid_redirect_uri", "error_description": "redirect_uris: entre 1 e 5"}
        if not all(redirect_permitido(self.cfg, u) for u in uris):
            return 400, {"error": "invalid_redirect_uri", "error_description": "endereço de regresso fora da lista aceite por este servidor "
                         "(claude.ai, claude.com, chatgpt.com, loopback, ou [oauth] redirects_extra)"}
        gts = m.get("grant_types", ["authorization_code"])
        if not isinstance(gts, list) or not set(gts) <= {"authorization_code", "refresh_token"}:
            return 400, {"error": "invalid_client_metadata", "error_description": "grant_types: só authorization_code e refresh_token"}
        rts = m.get("response_types", ["code"])
        if not isinstance(rts, list) or not set(rts) <= {"code"}:
            return 400, {"error": "invalid_client_metadata", "error_description": "response_types: só code"}
        if m.get("token_endpoint_auth_method", "none") != "none":
            return 400, {"error": "invalid_client_metadata", "error_description": "token_endpoint_auth_method: só none (cliente público com PKCE)"}
        nome = m.get("client_name") if isinstance(m.get("client_name"), str) else ""
        nome = _RE_CONTROLO.sub("", nome).strip()[:80]
        try:
            with self._bd() as conn:
                with Transacao(conn):
                    t = agora()
                    self._limpar(conn, t)
                    n_ip = _uma(conn, "SELECT COUNT(*) FROM clientes WHERE ip=? AND criado > ?", (ip, t - 3600))[0]
                    n_dia = _uma(conn, "SELECT COUNT(*) FROM clientes WHERE criado > ?", (t - 86400,))[0]
                    n_total = _uma(conn, "SELECT COUNT(*) FROM clientes")[0]
                    if n_ip >= REGISTOS_POR_IP_HORA or n_dia >= REGISTOS_POR_DIA or n_total >= CLIENTES_MAX:
                        return 429, {"error": "invalid_client_metadata", "error_description": "demasiados registos; tenta mais tarde"}
                    cid = P_CLIENTE + secrets.token_urlsafe(16)
                    conn.execute("INSERT INTO clientes (client_id, nome, redirect_uris, ip, criado) VALUES (?, ?, ?, ?, ?)",
                                 (cid, nome, json.dumps(uris), ip, t))
        except Ocupado:
            return 503, {"error": "temporarily_unavailable", "error_description": "ocupado; tenta outra vez daqui a pouco"}
        _avisar("cliente_registado", client=cid[:12], anfitrioes=",".join(sorted({urllib.parse.urlsplit(u).hostname or "" for u in uris})))
        return 201, {"client_id": cid, "client_id_issued_at": int(t), "client_name": nome, "redirect_uris": uris, "grant_types": gts,
                     "response_types": ["code"], "token_endpoint_auth_method": "none", "scope": ESCOPO}

    def _cliente(self, conn, client_id):
        if not isinstance(client_id, str) or not client_id.startswith(P_CLIENTE) or len(client_id) > 100:
            return None
        r = _uma(conn, "SELECT client_id, nome, redirect_uris FROM clientes WHERE client_id=?", (client_id,))
        return {"client_id": r[0], "nome": r[1], "redirect_uris": json.loads(r[2])} if r else None

    def _redir_app(self, h, uri: str, **q):
        q = {k: v for k, v in q.items() if v is not None}
        q["iss"] = self.cfg.issuer
        sep = "&" if urllib.parse.urlsplit(uri).query else ("" if uri.endswith("?") else "?")
        _redirigir(h, uri + sep + urllib.parse.urlencode(q))

    # ── idioma das páginas ──
    def _idioma(self, h) -> str:
        from .web import idioma_do_pedido
        return idioma_do_pedido(self.cfg.idioma, h.headers.get("Accept-Language"))

    def _erro_pagina(self, h, chave: str, status: int = 400):
        i = self._idioma(h)
        T = TEXTOS[i]
        _pagina(h, status, T["erro_titulo"], f"<h1>{T['erro_h1']}</h1><p>{html.escape(T[chave])}</p><p class=n>{T['erro_volta']}</p>", i)

    # ── 1) GET /oauth/authorize ──
    def autorizar(self, h):
        q = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(h.path).query, keep_blank_values=True))
        if "pedido" in q:
            return self._consentimento(h, q.get("pedido") or "")
        ip = h._ip()
        try:
            with self._bd() as conn:
                cli = self._cliente(conn, q.get("client_id"))
        except Ocupado:
            return self._erro_pagina(h, "e_ocupado", 503)
        if cli is None:
            i = self._idioma(h)
            return _pagina(h, 400, TEXTOS[i]["erro_titulo"], f"<h1>{TEXTOS[i]['erro_h1']}</h1><p>client_id ?</p>", i)
        ru = q.get("redirect_uri")
        explicito = bool(ru)
        if not ru:
            if len(cli["redirect_uris"]) != 1:
                return self._erro_pagina(h, "e_pedido")
            ru = cli["redirect_uris"][0]
        elif not redirect_valido(ru) or not redirect_coincide(ru, cli["redirect_uris"]):
            return self._erro_pagina(h, "e_pedido")
        if not redirect_permitido(self.cfg, ru):        # a lista pode ter encolhido depois do registo ([oauth] redirects_extra)
            return self._erro_pagina(h, "e_pedido")
        state = q.get("state")
        if state is not None and len(state) > 1000:
            return self._redir_app(h, ru, error="invalid_request", error_description="state demasiado longo")
        if q.get("response_type") != "code":
            return self._redir_app(h, ru, error="unsupported_response_type", state=state)
        if q.get("code_challenge_method") != "S256" or not _RE_CHALLENGE.match(q.get("code_challenge") or ""):
            return self._redir_app(h, ru, error="invalid_request", error_description="PKCE S256 obrigatório", state=state)
        if not recurso_ok(self.cfg, q.get("resource") or self.cfg.recurso):
            return self._redir_app(h, ru, error="invalid_target", error_description="este servidor só emite tokens para " + self.cfg.recurso,
                                   state=state)
        if not escopo_ok(q.get("scope")):
            return self._redir_app(h, ru, error="invalid_scope", error_description="o único scope é «notas»", state=state)
        try:
            with self._bd() as conn:
                with Transacao(conn):
                    t = agora()
                    self._limpar(conn, t)
                    total = _uma(conn, "SELECT COUNT(*) FROM pedidos")[0]
                    n_ip = _uma(conn, "SELECT COUNT(*) FROM pedidos WHERE ip=?", (ip,))[0]
                    if total >= PENDENTES_MAX or n_ip >= PENDENTES_POR_IP:
                        pid = None
                    else:
                        pid = secrets.token_urlsafe(24)
                        conn.execute("INSERT INTO pedidos (id, client_id, nome, redirect_uri, explicito, state, challenge, ip, criado, expira) "
                                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                     (pid, cli["client_id"], cli["nome"], ru, int(explicito), state, q["code_challenge"], ip, t, t + T_PEDIDO))
        except Ocupado:
            return self._erro_pagina(h, "e_ocupado", 503)
        if pid is None:
            return self._erro_pagina(h, "e_demasiados", 429)
        # A sessão do Basalt é SameSite=Strict: nesta navegação, que vem de outro sítio (claude.ai…), o browser não a manda. Uma
        # página mínima recarrega-se no mesmo sítio — essa navegação já é «do Basalt» e a sessão vê-se.
        destino = "/oauth/authorize?" + urllib.parse.urlencode({"pedido": pid})
        i = self._idioma(h)
        T = TEXTOS[i]
        _pagina(h, 200, T["continuar"], f"<p>{T['continuar']}</p><p><a href='{html.escape(destino, quote=True)}'>{T['continuar_link']}</a></p>",
                i, cabeca=f"<meta http-equiv=refresh content='0;url={html.escape(destino, quote=True)}'>")

    def _utilizador_browser(self, h):
        """a conta da SESSÃO do browser (cookie do modo senha ou cabeçalho do proxy). Um Authorization no pedido é recusado."""
        return h.aut.utilizador_do_pedido(h.headers, h._ip())

    def _sessao(self, h) -> str:
        """o que identifica a sessão (o hash do cookie, no modo senha); "" no modo cabecalho (a sessão é do proxy)."""
        if self.cfg.auth.modo != "senha":
            return ""
        from .auth import hash_token
        tok = h.aut.token_do_pedido(h.headers)
        return hash_token(tok) if tok else ""

    def _csrf(self, chave: bytes, pid: str, email: str, sessao: str) -> str:
        return hmac.new(chave, f"basalt-oauth\0{pid}\0{email.lower()}\0{sessao}".encode("utf-8"), hashlib.sha256).hexdigest()

    def _consentimento(self, h, pid: str):
        if not RE_PEDIDO.match(pid):
            return self._erro_pagina(h, "e_pedido")
        if (h.headers.get("Authorization") or "").strip():
            return self._erro_pagina(h, "e_token", 403)
        h.aut.reler_se_mudou()
        if getattr(h.aut, "_fechado", ""):
            return self._erro_pagina(h, "e_fechado", 503)
        try:
            with self._bd() as conn:
                p = _uma(conn, "SELECT client_id, nome, redirect_uri, email FROM pedidos WHERE id=? AND usado IS NULL AND expira > ?",
                         (pid, agora()))
        except Ocupado:
            return self._erro_pagina(h, "e_ocupado", 503)
        if not p:
            return self._erro_pagina(h, "e_expirou")
        client_id, nome, ru, preso = p
        if imita_oficial(nome, ru):
            return self._erro_pagina(h, "e_imita", 403)
        user = self._utilizador_browser(h)
        if user is None:
            if self.cfg.auth.modo == "senha":
                volta = "/oauth/authorize?" + urllib.parse.urlencode({"pedido": pid})
                return _redirigir(h, "/entrar?" + urllib.parse.urlencode({"volta": volta}), 302)
            return self._erro_pagina(h, "e_sem_sessao", 403)
        email = user.email.lower()
        try:
            with self._bd() as conn:
                with Transacao(conn):
                    conn.execute("UPDATE pedidos SET email=? WHERE id=? AND email IS NULL", (email, pid))
                    preso = _uma(conn, "SELECT email FROM pedidos WHERE id=?", (pid,))[0]
                chave = self._chave(conn)
        except Ocupado:
            return self._erro_pagina(h, "e_ocupado", 503)
        if preso != email:
            return self._erro_pagina(h, "e_outra", 403)
        from .web import dono_de
        i = self._idioma(h)
        T = TEXTOS[i]
        local = (urllib.parse.urlsplit(ru).hostname or "").lower() in LOOPBACK
        csrf = self._csrf(chave, pid, email, self._sessao(h))
        lista = "".join(f"<li>{x}</li>" for x in T["pode_lista"])
        corpo = (f"<h1>{T['h1']}</h1>"
                 f"<p>{T['app']}: {_span(nome or '(?)')} <span class=n>{T['declarado']}</span></p>"
                 f"<p>{T['volta']}: {_span(ru, so_ascii=True)}</p>"
                 + (f"<p class=aviso>{T['local']}</p>" if local else "")
                 + f"<p>{T['conta']}: {_span(user.email, so_ascii=True)}</p>"
                 f"<p><b>{T['pode']}</b>:</p><ul>{lista}</ul><p>{T['nao_pode']}</p>"
                 f"<p class=aviso>{T['aviso']}</p>"
                 "<form method=post action='/oauth/autorizar'>"
                 f"<input type=hidden name=pedido value='{html.escape(pid, quote=True)}'>"
                 f"<input type=hidden name=csrf value='{csrf}'>"
                 f"<input type=hidden name=dono value='{dono_de(user)}'>"
                 f"<div class=b><button name=decisao value=recusar>{T['recusar']}</button>"
                 f"<button class=p name=decisao value=permitir>{T['permitir']}</button></div>"
                 + (f"<button class=l name=decisao value=sair>{T['outra_conta']}</button>" if self.cfg.auth.modo == "senha" else "")
                 + f"</form><p class=n>{T['desligar']}</p>")
        _pagina(h, 200, T["titulo"], corpo, i, form_action=origem_de(ru))

    # ── 2) POST /oauth/autorizar (Permitir / Recusar / Outra conta) ──
    def decidir(self, h):
        if (h.headers.get("Authorization") or "").strip():
            return self._erro_pagina(h, "e_token", 403)
        origem = h.headers.get("Origin")
        sfs = (h.headers.get("Sec-Fetch-Site") or "").lower()
        if not origem or config.anfitriao_de(origem) not in config.anfitrioes(self.cfg) or (sfs and sfs != "same-origin"):
            return self._erro_pagina(h, "e_origem", 403)
        raw = _ler_corpo(h, FORM_CONSENTIMENTO_MAX)
        if raw is None:
            return self._erro_pagina(h, "e_pedido", 413)
        f = _form(raw)
        pid, decisao = f.get("pedido") or "", f.get("decisao") or ""
        if not RE_PEDIDO.match(pid) or decisao not in ("permitir", "recusar", "sair"):
            return self._erro_pagina(h, "e_pedido")
        h.aut.reler_se_mudou()
        if getattr(h.aut, "_fechado", ""):
            return self._erro_pagina(h, "e_fechado", 503)
        user = self._utilizador_browser(h)
        if user is None:
            return self._erro_pagina(h, "e_csrf", 403)
        email = user.email.lower()
        from .web import dono_de
        ip = h._ip()
        try:
            with self._bd() as conn:
                chave = self._chave(conn)
                if not hmac.compare_digest((f.get("csrf") or "").encode("utf-8", "replace"),
                                           self._csrf(chave, pid, email, self._sessao(h)).encode()) \
                        or not hmac.compare_digest((f.get("dono") or "").encode("utf-8", "replace"), dono_de(user).encode()):
                    _avisar("consentimento_recusado", motivo="csrf", ip=ip)
                    return self._erro_pagina(h, "e_csrf", 403)       # o pedido NÃO se gasta: pode voltar à página certa
                if decisao == "sair":
                    if self.cfg.auth.modo != "senha":
                        return self._erro_pagina(h, "e_pedido")
                    with Transacao(conn):
                        conn.execute("UPDATE pedidos SET email=NULL WHERE id=? AND email=? AND usado IS NULL", (pid, email))
                    h.aut.sair(h.aut.token_do_pedido(h.headers))
                    volta = "/oauth/authorize?" + urllib.parse.urlencode({"pedido": pid})
                    return _redirigir(h, "/entrar?" + urllib.parse.urlencode({"volta": volta}),
                                      extra=(("Set-Cookie", h.aut.cabecalho_apagar_cookie()),))
                with Transacao(conn):
                    t = agora()
                    p = _uma(conn, "SELECT client_id, nome, redirect_uri, explicito, state, challenge, email FROM pedidos "
                                   "WHERE id=? AND usado IS NULL AND expira > ?", (pid, t))
                    if not p:
                        return self._erro_pagina(h, "e_expirou")
                    client_id, nome, ru, expl, state, chal, preso = p
                    if preso != email:
                        return self._erro_pagina(h, "e_outra", 403)
                    conn.execute("UPDATE pedidos SET usado=? WHERE id=?", (t, pid))
                    if decisao != "permitir":
                        codigo = None
                    else:
                        codigo, familia = secrets.token_urlsafe(32), secrets.token_urlsafe(16)
                        onde = (urllib.parse.urlsplit(ru).hostname or "").lower()
                        conn.execute("INSERT INTO familias (id, tipo, client_id, nome, onde, email, espaco, cred, criado) "
                                     "VALUES (?, 'oauth', ?, ?, ?, ?, ?, ?, ?)",
                                     (familia, client_id, nome, onde, email, user.espaco, self._cred(user), t))
                        conn.execute("INSERT INTO codigos (hash, familia, client_id, redirect_uri, explicito, challenge, recurso, criado, expira) "
                                     "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                                     (_h(codigo), familia, client_id, ru, expl, chal, self.cfg.recurso, t, t + T_CODIGO))
                        conn.execute("UPDATE clientes SET usado=? WHERE client_id=?", (t, client_id))
        except Ocupado:
            return self._erro_pagina(h, "e_ocupado", 503)
        if codigo is None:
            _avisar("ligacao_recusada", client=client_id[:12], ip=ip)
            return self._redir_app(h, ru, error="access_denied", state=state)
        _avisar("ligacao_aprovada", client=client_id[:12], familia=familia[:8], para=(urllib.parse.urlsplit(ru).hostname or ""), ip=ip)
        return self._redir_app(h, ru, code=codigo, state=state)

    # ── 3) POST /oauth/token ──
    def _bloqueado(self, ip, t) -> bool:
        with self._lock:
            for k in list(self._falhas):
                fs = [x for x in self._falhas[k] if t - x < TOKEN_JANELA_S]
                if fs:
                    self._falhas[k] = fs
                else:
                    del self._falhas[k]
            return len(self._falhas.get(ip, [])) >= TOKEN_FALHAS_MAX

    def _falha(self, h, ip, motivo, erro, desc, status=400):
        with self._lock:
            self._falhas.setdefault(ip, []).append(agora())
        _avisar("token_recusado", motivo=motivo, ip=ip)
        return _json_erro(h, erro, desc, status)

    def token(self, h):
        ip = h._ip()
        if self._bloqueado(ip, agora()):
            return _json_erro(h, "temporarily_unavailable", "demasiadas tentativas falhadas; espera uns minutos", 429)
        raw = _ler_corpo(h, FORM_MAX)
        if raw is None:
            return _json_erro(h, "invalid_request", "pedido grande demais", 413)
        f = _form(raw)
        client_id = f.get("client_id")
        ah = h.headers.get("Authorization") or ""
        basic_mau = False
        if not client_id and ah.lower().startswith("basic "):
            try:
                client_id = urllib.parse.unquote(base64.b64decode(ah[6:], validate=True).decode().split(":", 1)[0])
            except Exception:
                client_id, basic_mau = None, True
        gt = f.get("grant_type")
        if gt not in ("authorization_code", "refresh_token"):
            return _json_erro(h, "unsupported_grant_type", "só authorization_code e refresh_token")
        h.aut.reler_se_mudou()
        if getattr(h.aut, "_fechado", ""):          # basalt.toml estragado: nada se gasta nem se revoga — volta a valer quando o corrigirem
            return _json_erro(h, "invalid_grant", "a configuração do servidor tem um erro; tenta depois")
        if gt == "refresh_token" and basic_mau:
            return self._falha(h, ip, "basic_invalido", "invalid_client", "Authorization Basic inválido", 401)
        if gt == "refresh_token" and not client_id:
            return _json_erro(h, "invalid_request", "falta o client_id")
        try:
            with self._bd() as conn:
                if gt == "authorization_code":
                    return self._troca_codigo(h, conn, f, client_id or None, basic_mau, ip)
                return self._renova(h, conn, f, client_id, ip)
        except Ocupado:                              # ROLLBACK feito: o código/token ficou como estava
            return _json_erro(h, "temporarily_unavailable", "ocupado; tenta outra vez daqui a pouco", 503)

    def _emitir(self, conn, familia, recurso):
        """o par novo (acesso + renovação) dentro da Transacao de quem chama, ou None se a família já não vale."""
        if not _uma(conn, "SELECT 1 FROM familias WHERE id=? AND revogada IS NULL", (familia,)):
            return None
        acesso, renovar = P_ACESSO + secrets.token_urlsafe(32), P_RENOVAR + secrets.token_urlsafe(32)
        t = agora()
        for tok, tipo, dur in ((acesso, "acesso", T_ACESSO), (renovar, "renovar", T_RENOVAR)):
            conn.execute("INSERT INTO tokens (hash, tipo, familia, recurso, criado, expira) VALUES (?, ?, ?, ?, ?, ?)",
                         (_h(tok), tipo, familia, recurso, t, t + dur))
        conn.execute("UPDATE familias SET ultimo_uso=? WHERE id=?", (t, familia))
        return {"access_token": acesso, "token_type": "Bearer", "expires_in": T_ACESSO, "refresh_token": renovar, "scope": ESCOPO}

    def _conta_da_familia(self, conn, familia):
        r = _uma(conn, "SELECT email, espaco, cred FROM familias WHERE id=?", (familia,))
        return self._familia_viva(*r) if r else None

    def _troca_codigo(self, h, conn, f, client_id, basic_mau, ip):
        codigo = f.get("code") or ""
        if not isinstance(codigo, str) or len(codigo) > 200:
            codigo = ""
        revogada = None
        with Transacao(conn):
            t = agora()
            row = _uma(conn, "SELECT familia, client_id, redirect_uri, explicito, challenge, recurso, expira, usado FROM codigos WHERE hash=?",
                       (_h(codigo),)) if codigo else None
            if not row:
                return self._falha(h, ip, "codigo_desconhecido", "invalid_grant", "código desconhecido")
            familia, cid, ru, expl, chal, recurso, expira, usado = row
            if usado is not None:               # 2.ª vez: alguém copiou o código → cai tudo o que ele deu
                self._revogar_familia(conn, familia, t)
                revogada = familia
            elif expira <= t:
                return self._falha(h, ip, "codigo_expirado", "invalid_grant", "código expirado")
            else:
                # um código serve UMA tentativa: gasta-se já, mesmo que a troca falhe a seguir (quem o interceptou não vai experimentando)
                conn.execute("UPDATE codigos SET usado=? WHERE hash=?", (t, _h(codigo)))
        if revogada:
            _avisar("familia_revogada", motivo="codigo_reutilizado", familia=revogada[:8], ip=ip)
            return self._falha(h, ip, "codigo_reutilizado", "invalid_grant", "código já usado")
        if basic_mau:
            return self._falha(h, ip, "basic_invalido", "invalid_client", "Authorization Basic inválido", 401)
        if not client_id:
            return self._falha(h, ip, "sem_client_id", "invalid_request", "falta o client_id")
        if client_id != cid:
            return self._falha(h, ip, "outro_cliente", "invalid_grant", "o código foi emitido para outra aplicação")
        if f.get("redirect_uri") != ru and (expl or f.get("redirect_uri") is not None):
            return self._falha(h, ip, "outro_redirect", "invalid_grant", "redirect_uri diferente do pedido")
        if not pkce_ok(f.get("code_verifier"), chal):
            return self._falha(h, ip, "pkce", "invalid_grant", "PKCE: code_verifier errado")
        if (f.get("resource") and not recurso_ok(self.cfg, f["resource"])) or recurso != self.cfg.recurso:
            return self._falha(h, ip, "outro_recurso", "invalid_target", "este servidor só emite tokens para " + self.cfg.recurso)
        with Transacao(conn) as tx:
            if self._conta_da_familia(conn, familia) is None:     # conta apagada, mudou de espaço ou de senha desde o «Permitir»
                self._revogar_familia(conn, familia)
                par = None
            else:
                par = self._emitir(conn, familia, recurso)
                if par is None:
                    tx.desfazer()
        if par is None:
            return self._falha(h, ip, "familia_revogada", "invalid_grant", "ligação desligada")
        _avisar("token_emitido", via="codigo", familia=familia[:8], ip=ip)
        return _json(h, 200, par)

    def _renova(self, h, conn, f, client_id, ip):
        tok = f.get("refresh_token") or ""
        if not isinstance(tok, str) or not tok.startswith(P_RENOVAR) or len(tok) > 200:
            return self._falha(h, ip, "renovacao_desconhecida", "invalid_grant", "token de renovação desconhecido")
        revogada, motivo = None, ""
        with Transacao(conn) as tx:
            t = agora()
            row = _uma(conn, "SELECT k.familia, f.client_id, k.recurso, k.usado, k.revogado, k.expira FROM tokens k JOIN familias f ON f.id = k.familia "
                             "WHERE k.hash=? AND k.tipo='renovar'", (_h(tok),))
            if not row:
                return self._falha(h, ip, "renovacao_desconhecida", "invalid_grant", "token de renovação desconhecido")
            familia, cid, recurso, usado, revogado, expira = row
            if revogado is not None:
                return self._falha(h, ip, "renovacao_revogada", "invalid_grant", "ligação desligada")
            if usado is not None:                # rotação: um token de renovação só serve uma vez
                self._revogar_familia(conn, familia, t)
                revogada, motivo = familia, "renovacao_reutilizada"
            elif expira <= t:
                return self._falha(h, ip, "renovacao_expirada", "invalid_grant", "token de renovação expirado")
            elif client_id != cid:
                return self._falha(h, ip, "outro_cliente", "invalid_grant", "o token foi emitido para outra aplicação")
            elif (f.get("resource") and not recurso_ok(self.cfg, f["resource"])) or recurso != self.cfg.recurso:
                return self._falha(h, ip, "outro_recurso", "invalid_target", "este servidor só emite tokens para " + self.cfg.recurso)
            elif not escopo_ok(f.get("scope")):
                return self._falha(h, ip, "escopo", "invalid_scope", "o único scope é «notas»")
            elif self._conta_da_familia(conn, familia) is None:
                self._revogar_familia(conn, familia, t)
                revogada, motivo = familia, "conta_mudou"
            else:
                conn.execute("UPDATE tokens SET usado=? WHERE hash=?", (t, _h(tok)))
                par = self._emitir(conn, familia, recurso)
                if par is None:
                    tx.desfazer()
                    return self._falha(h, ip, "familia_revogada", "invalid_grant", "ligação desligada")
        if revogada:
            _avisar("familia_revogada", motivo=motivo, familia=revogada[:8], ip=ip)
            return self._falha(h, ip, motivo, "invalid_grant",
                               "token de renovação já usado" if motivo == "renovacao_reutilizada" else "conta indisponível")
        _avisar("token_emitido", via="renovacao", familia=familia[:8], ip=ip)
        return _json(h, 200, par)

    # ── página «Ligar a tua IA»: as ligações de UMA conta ──
    def ligacoes(self, u) -> list:
        with self._bd() as conn:
            linhas = conn.execute("SELECT f.id, f.tipo, f.nome, f.onde, f.criado, f.ultimo_uso, f.cred FROM familias f WHERE f.email=? AND f.espaco=? "
                                  "AND f.revogada IS NULL AND EXISTS (SELECT 1 FROM tokens k WHERE k.familia=f.id AND k.revogado IS NULL "
                                  "AND (k.expira IS NULL OR k.expira > ?)) ORDER BY f.criado DESC",
                                  (u.email.lower(), u.espaco, agora())).fetchall()
        cred = self._cred(u)
        return [{"id": r[0], "tipo": r[1], "nome": r[2], "onde": r[3], "criado": int(r[4]), "ultimo_uso": int(r[5]) if r[5] else None}
                for r in linhas if hmac.compare_digest(r[6], cred)]

    def revogar(self, u, familia: str | None = None) -> int:
        """revoga uma ligação (ou todas, com familia=None) DESTA conta. Devolve quantas."""
        with self._bd() as conn:
            with Transacao(conn):
                if familia is None:
                    ids = [r[0] for r in conn.execute("SELECT id FROM familias WHERE email=? AND espaco=? AND revogada IS NULL",
                                                      (u.email.lower(), u.espaco)).fetchall()]
                else:
                    r = _uma(conn, "SELECT id FROM familias WHERE id=? AND email=? AND espaco=? AND revogada IS NULL",
                             (familia, u.email.lower(), u.espaco))
                    ids = [r[0]] if r else []
                t = agora()
                for f in ids:
                    self._revogar_familia(conn, f, t)
        for f in ids:
            _avisar("familia_revogada", motivo="pagina", familia=f[:8])
        return len(ids)

    def token_pessoal(self, u, nome: str):
        """(token, id) ou (None, motivo). O token mostra-se UMA vez; só o sha256 fica guardado. Só vale no /mcp."""
        nome = _RE_CONTROLO.sub("", nome or "").strip()[:60] or "Codex"
        with self._bd() as conn:
            with Transacao(conn):
                t = agora()
                n = _uma(conn, "SELECT COUNT(*) FROM familias WHERE email=? AND espaco=? AND tipo='pessoal' AND revogada IS NULL",
                         (u.email.lower(), u.espaco))[0]
                if n >= PESSOAIS_MAX:
                    return None, f"já tens {PESSOAIS_MAX} tokens pessoais; revoga algum primeiro"
                familia = secrets.token_urlsafe(16)
                token = P_PESSOAL + secrets.token_urlsafe(32)
                conn.execute("INSERT INTO familias (id, tipo, client_id, nome, onde, email, espaco, cred, criado) "
                             "VALUES (?, 'pessoal', '', ?, '', ?, ?, ?, ?)", (familia, nome, u.email.lower(), u.espaco, self._cred(u), t))
                conn.execute("INSERT INTO tokens (hash, tipo, familia, recurso, criado, expira) VALUES (?, 'pessoal', ?, '', ?, NULL)",
                             (_h(token), familia, t))
        _avisar("token_pessoal_criado", familia=familia[:8])
        return token, familia


# ───────────────────────────── rotas (web.ROTAS_EXTRA) ─────────────────────────────
def _o(h):
    return getattr(h, "oauth", None)


def _desligado(h):
    return _json(h, 404, {"erro": "não existe"})


def _rota_prm(h):
    o = _o(h)
    if o is None or not o.cfg.oauth_activo:
        return _desligado(h)
    _json(h, 200, o.metadados_recurso())


def _rota_asm(h):
    o = _o(h)
    if o is None or not o.cfg.oauth_activo:
        return _desligado(h)
    _json(h, 200, o.metadados_servidor())


def _rota_register(h):
    o = _o(h)
    if o is None or not o.cfg.oauth_activo:
        return _desligado(h)
    estado, obj = o.registar_cliente(_ler_corpo(h, FORM_MAX), h._ip())
    _json(h, estado, obj)


def _rota_authorize(h):
    o = _o(h)
    if o is None or not o.cfg.oauth_activo:
        return _desligado(h)
    o.autorizar(h)


def _rota_autorizar(h):
    o = _o(h)
    if o is None or not o.cfg.oauth_activo:
        return _desligado(h)
    o.decidir(h)


def _rota_token(h):
    o = _o(h)
    if o is None or not o.cfg.oauth_activo:
        return _desligado(h)
    o.token(h)


def _api_conta(h, escrita: bool):
    """a conta da página para /api/ligacoes*: SÓ a sessão do browser (nunca um Bearer — um token não cria nem revoga tokens), com o dono
    certo (X-Basalt-Dono) e, nas escritas, X-Basalt + mesma origem. None se já se respondeu."""
    if (h.headers.get("Authorization") or "").strip():
        h._json(403, {"erro": "Esta parte só se usa na página do Basalt, não com tokens."})
        return None
    u = h._quem()
    if u is None:
        h._sem_acesso(urllib.parse.urlsplit(h.path).path)
        return None
    if escrita:
        if not h._escrita(u):
            return None
    elif h._dono_errado(u):
        return None
    return u


def _motivo_desligado(cfg) -> str:
    if cfg.auth.modo == "aberto":
        return "aberto"
    if not cfg.url_publico:
        return "sem_url_publico"
    return ""


def _rota_ligacoes(h):
    o = _o(h)
    u = _api_conta(h, escrita=False)
    if u is None:
        return
    try:
        lig = o.ligacoes(u)
    except Ocupado:
        return h._json(503, {"erro": "Ocupado; tenta outra vez daqui a pouco."})
    cfg = o.cfg
    base = cfg.url_publico or ""
    h._json(200, {"oauth": cfg.oauth_activo, "motivo": _motivo_desligado(cfg), "url_publico": base,
                  "mcp": (base + "/mcp") if base else "", "modo": cfg.auth.modo, "token_admin": bool(u.mcp_token_hash),
                  "ligacoes": lig})


def _rota_revogar(h):
    o = _o(h)
    u = _api_conta(h, escrita=True)
    if u is None:
        return
    try:
        c = h._corpo()
    except Exception:
        return h._json(400, {"erro": "Pedido inválido."})
    if not isinstance(c, dict):
        return h._json(400, {"erro": "Pedido inválido."})
    if c.get("todas") is True:
        alvo = None
    else:
        alvo = c.get("id")
        if not isinstance(alvo, str) or not RE_FAMILIA.match(alvo):
            return h._json(400, {"erro": "Ligação inválida."})
    try:
        n = o.revogar(u, alvo)
    except Ocupado:
        return h._json(503, {"erro": "Ocupado; tenta outra vez daqui a pouco."})
    if alvo is not None and n == 0:
        return h._json(404, {"erro": "Essa ligação já não existe."})
    h._json(200, {"ok": True, "revogadas": n})


def _rota_token_pessoal(h):
    o = _o(h)
    u = _api_conta(h, escrita=True)
    if u is None:
        return
    try:
        c = h._corpo()
    except Exception:
        return h._json(400, {"erro": "Pedido inválido."})
    if not isinstance(c, dict):
        return h._json(400, {"erro": "Pedido inválido."})
    try:
        tok, ref = o.token_pessoal(u, str(c.get("nome") or ""))
    except Ocupado:
        return h._json(503, {"erro": "Ocupado; tenta outra vez daqui a pouco."})
    if tok is None:
        return h._json(429, {"erro": ref})
    h._json(201, {"token": tok, "id": ref})


ROTAS = {
    ("GET", "/.well-known/oauth-protected-resource"): _rota_prm,
    ("GET", "/.well-known/oauth-protected-resource/mcp"): _rota_prm,
    ("GET", "/.well-known/oauth-authorization-server"): _rota_asm,
    ("POST", "/oauth/register"): _rota_register,
    ("GET", "/oauth/authorize"): _rota_authorize,
    ("POST", "/oauth/autorizar"): _rota_autorizar,
    ("POST", "/oauth/token"): _rota_token,
    ("GET", "/api/ligacoes"): _rota_ligacoes,
    ("POST", "/api/ligacoes/revogar"): _rota_revogar,
    ("POST", "/api/ligacoes/token"): _rota_token_pessoal,
}


def registar(rotas: dict) -> None:
    """as rotas ficam SEMPRE registadas (cada uma vê `h.oauth` e `h.cfg` do seu servidor); com o OAuth desligado as de OAuth dão 404."""
    rotas.update(ROTAS)
