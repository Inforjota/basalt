"""basalt.auth — quem é o utilizador deste pedido.

Três modos (basalt.toml → [auth] modo):
  cabecalho  o proxy à frente já fez o login (SSO) e manda o e-mail num cabeçalho; o Basalt só confia nesse cabeçalho
             porque o proxy o SUBSTITUI sempre (nunca o deixa passar do visitante). Sem proxy assim, não usar.
  senha      formulário próprio: POST /api/entrar {email, senha} → cookie HttpOnly/SameSite=Strict; scrypt; limite de
             tentativas por IP; sessões guardadas em <dados>/sessoes.json (sobrevivem a reinícios).
  aberto     sem login — só quando o servidor escuta em 127.0.0.1 (config.validar recusa o resto). O utilizador é o primeiro
             configurado ou, sem nenhum, «local».
Para os agentes (MCP e CLI remoto): `Authorization: Bearer <token>`; só se guarda o sha256 do token.
"""
from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path

from .config import Config, Utilizador

COOKIE = "basalt_sessao"
SCRYPT = dict(n=2 ** 14, r=8, p=1)   # ~40 ms, 16 MiB; dklen 32
MAXMEM = 64 * 1024 * 1024             # o OpenSSL recusa acima de 32 MiB por omissão


# ───────────────────────────── senhas e tokens ─────────────────────────────
def hash_senha(senha: str) -> str:
    if len(senha) < 8:
        raise ValueError("a senha tem de ter pelo menos 8 caracteres")
    sal = secrets.token_bytes(16)
    h = hashlib.scrypt(senha.encode("utf-8"), salt=sal, dklen=32, maxmem=MAXMEM, **SCRYPT)
    return "scrypt$%d$%d$%d$%s$%s" % (SCRYPT["n"], SCRYPT["r"], SCRYPT["p"], base64.b64encode(sal).decode(), base64.b64encode(h).decode())


def verificar_senha(guardado: str, senha: str) -> bool:
    try:
        alg, n, r, p, sal, h = guardado.split("$")
        if alg != "scrypt":
            return False
        calc = hashlib.scrypt(senha.encode("utf-8"), salt=base64.b64decode(sal), dklen=32, maxmem=MAXMEM, n=int(n), r=int(r), p=int(p))
        return hmac.compare_digest(calc, base64.b64decode(h))
    except Exception:
        return False


def gerar_token() -> tuple[str, str]:
    """(token para dar UMA vez ao utilizador, hash para o toml)."""
    t = "bslt_" + secrets.token_urlsafe(32)
    return t, hash_token(t)


def hash_token(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


# ───────────────────────────── sessões e limite de tentativas ─────────────────────────────
@dataclass
class Sessao:
    email: str
    expira: float
    criada: float


class Autenticador:
    def __init__(self, cfg: Config, ficheiro_sessoes: Path | None = None):
        self.cfg = cfg
        self.ficheiro = ficheiro_sessoes if ficheiro_sessoes is not None else cfg.dados / "sessoes.json"
        self._lock = threading.Lock()
        self._sessoes: dict[str, Sessao] = {}
        self._falhas: dict[str, list[float]] = {}   # ip → instantes das falhas
        self._carregar()

    # ── persistência ──
    def _carregar(self):
        try:
            d = json.loads(self.ficheiro.read_text(encoding="utf-8"))
            agora = time.time()
            self._sessoes = {k: Sessao(**v) for k, v in d.items() if v.get("expira", 0) > agora}
        except (OSError, ValueError, TypeError):
            self._sessoes = {}

    def _guardar(self):
        try:
            self.ficheiro.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.ficheiro.with_suffix(".tmp")
            tmp.write_text(json.dumps({k: vars(v) for k, v in self._sessoes.items()}), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.ficheiro)
        except OSError:
            pass

    # ── limite por IP ──
    def _bloqueado(self, ip: str) -> bool:
        janela = self.cfg.auth.janela_minutos * 60
        agora = time.time()
        fs = [t for t in self._falhas.get(ip, []) if agora - t < janela]
        self._falhas[ip] = fs
        return len(fs) >= self.cfg.auth.tentativas

    def _falhou(self, ip: str):
        self._falhas.setdefault(ip, []).append(time.time())

    # ── entrar / sair (modo senha) ──
    def entrar(self, email: str, senha: str, ip: str) -> tuple[bool, str]:
        """(ok, token-ou-motivo). Sempre o mesmo tempo e a mesma mensagem para e-mail inexistente e senha errada."""
        with self._lock:
            if self._bloqueado(ip):
                return False, "Demasiadas tentativas; espera %d minutos." % self.cfg.auth.janela_minutos
            u = self.cfg.utilizador(email)
            guardado = u.senha_hash if (u and u.senha_hash) else hash_senha("senha-inexistente-%d" % secrets.randbits(32))
            if not (u and u.senha_hash and verificar_senha(guardado, senha)):
                self._falhou(ip)
                return False, "E-mail ou senha errados."
            token = secrets.token_urlsafe(32)
            agora = time.time()
            self._sessoes[hash_token(token)] = Sessao(email=u.email, expira=agora + self.cfg.auth.sessao_horas * 3600, criada=agora)
            self._falhas.pop(ip, None)
            self._guardar()
            return True, token

    def sair(self, token: str):
        with self._lock:
            if self._sessoes.pop(hash_token(token or ""), None) is not None:
                self._guardar()

    def cabecalho_cookie(self, token: str, seguro: bool) -> str:
        return (f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={self.cfg.auth.sessao_horas * 3600}"
                + ("; Secure" if seguro else ""))

    def cabecalho_apagar_cookie(self) -> str:
        return f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"

    # ── quem é ──
    def utilizador_do_pedido(self, cabecalhos, ip: str = "") -> Utilizador | None:
        """`cabecalhos` é o `self.headers` do handler (ou um dict). None = 401."""
        get = cabecalhos.get
        # agentes: Bearer (vale em todos os modos; é o único caminho para o MCP por HTTP)
        auth = (get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            h = hash_token(auth[7:].strip())
            u = next((u for u in self.cfg.utilizadores if u.mcp_token_hash and hmac.compare_digest(u.mcp_token_hash, h)), None)
            return u
        modo = self.cfg.auth.modo
        if modo == "aberto":
            return self.cfg.utilizadores[0] if self.cfg.utilizadores else Utilizador(email="local@basalt", nome="local", admin=True)
        if modo == "cabecalho":
            email = (get(self.cfg.auth.cabecalho) or "").strip().lower()
            return self.cfg.utilizador(email) if email else None
        # senha: cookie
        c = SimpleCookie()
        try:
            c.load(get("Cookie") or "")
        except Exception:
            return None
        m = c.get(COOKIE)
        if not m:
            return None
        with self._lock:
            s = self._sessoes.get(hash_token(m.value))
            if not s:
                return None
            if s.expira < time.time():
                self._sessoes.pop(hash_token(m.value), None)
                self._guardar()
                return None
        return self.cfg.utilizador(s.email)

    def token_do_pedido(self, cabecalhos) -> str:
        c = SimpleCookie()
        try:
            c.load(cabecalhos.get("Cookie") or "")
        except Exception:
            return ""
        m = c.get(COOKIE)
        return m.value if m else ""
