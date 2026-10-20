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
import sys
import threading
import time
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path

from . import config

from .config import Config, Utilizador

COOKIE = "basalt_sessao"
PODA_SESSOES_SEGUNDOS = 3600          # ronda 9 (achado M4 da ronda 2): as sessões mortas saem do ficheiro ao carregar e, durante o uso, no máximo de hora a hora
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
    cred: str = ""          # ronda 9 (achado G4 da ronda 2): a impressão da senha com que a sessão abriu ("" = de antes: não vale)


def impressao_credencial(senha_hash: str) -> str:
    """a sessão só vale enquanto a senha for a mesma com que abriu — mesmo que a senha mude com o servidor parado."""
    return hashlib.sha256(("basalt-sessao\0" + (senha_hash or "")).encode("utf-8")).hexdigest()[:32] if senha_hash else ""


class Autenticador:
    def __init__(self, cfg: Config, ficheiro_sessoes: Path | None = None):
        self.cfg = cfg
        self.ficheiro = ficheiro_sessoes if ficheiro_sessoes is not None else cfg.dados / "sessoes.json"
        self._lock = threading.Lock()
        self._sessoes: dict[str, Sessao] = {}
        self._falhas: dict[str, list[float]] = {}   # ip → instantes das falhas
        self._poda_em = 0.0                         # quando se podaram as sessões pela última vez
        self._carregar()
        self._origem = Path(cfg.origem) if cfg.origem else None   # ronda 8 (configuração relida em execução): o basalt.toml relê-se quando muda (revogar sem reiniciar)
        self._marca = self._marca_do_toml()
        self._fechado = ""          # ronda 9 (achado G4 da ronda 2): o basalt.toml mudou e não carrega → ninguém entra (o motivo)
        with self._lock:
            self._podar_sessoes(forcar=True)

    # ── persistência ──
    def _carregar(self):
        try:
            d = json.loads(self.ficheiro.read_text(encoding="utf-8"))
            campos = set(Sessao.__dataclass_fields__)
            # ronda 10 (achado M1 da r3): carregam-se TODAS (as válidas na forma) — a poda forçada do arranque tira as expiradas da
            # memória E do ficheiro; antes saíam só da memória aqui, a poda já não as via e o ficheiro guardava-as para sempre
            self._sessoes = {k: Sessao(**{c: x for c, x in v.items() if c in campos}) for k, v in d.items()
                             if isinstance(v, dict) and isinstance(v.get("expira"), (int, float))}
        except (OSError, ValueError, TypeError, AttributeError):   # PORTE: JSON válido mas de outra forma ([] ou {"x": 5}) também arranca limpo
            self._sessoes = {}

    def _guardar(self):
        try:
            self.ficheiro.parent.mkdir(parents=True, exist_ok=True)
            tmp = self.ficheiro.with_suffix(".tmp")
            tmp.write_text(json.dumps({k: vars(v) for k, v in self._sessoes.items()}), encoding="utf-8")
            os.chmod(tmp, 0o600)
            os.replace(tmp, self.ficheiro)
        except OSError:
            raise                                     # ronda 8 (configuração relida em execução): quem chama decide o que dizer; engolir fazia «saí» sem ter saído

    def _viva(self, s: Sessao, agora: float) -> bool:
        """uma sessão ainda vale: não expirou, o utilizador existe e a impressão da senha é a de hoje (as de antes da impressão não valem)."""
        u = self.cfg.utilizador(s.email)
        return s.expira > agora and u is not None and bool(s.cred) and hmac.compare_digest(s.cred, impressao_credencial(u.senha_hash))

    def _podar_sessoes(self, forcar: bool = False) -> int:
        """ronda 9 (achado M4 da ronda 2): tira de memória E do ficheiro as sessões expiradas, as de utilizadores que já não existem e as de
        quem mudou de senha — antes ficavam em sessoes.json (hash do cookie + e-mail) até alguém voltar com o cookie. Corre ao carregar e, durante o
        uso, no máximo de hora a hora. Chamar com o `_lock` tomado. Se o ficheiro não se deixar gravar, diz-se no stderr e o pedido segue."""
        agora = time.time()
        if not forcar and agora - self._poda_em < PODA_SESSOES_SEGUNDOS:
            return 0
        self._poda_em = agora
        mortas = [k for k, s in self._sessoes.items() if not self._viva(s, agora)]
        for k in mortas:
            self._sessoes.pop(k, None)
        if mortas:
            self._guardar_sem_falhar()
        return len(mortas)

    def _guardar_sem_falhar(self):
        try:
            self._guardar()
        except OSError as e:
            print(f"sessões: não consegui gravar ({e})", file=sys.stderr, flush=True)

    # ── ronda 8 (configuração relida em execução): os utilizadores (e-mails, senhas, tokens MCP) seguem o basalt.toml em execução ──
    def _marca_do_toml(self):
        if self._origem is None:
            return None
        try:
            st = self._origem.stat()
            return (st.st_mtime_ns, st.st_size, st.st_ino)
        except OSError:
            return ("sem-ficheiro",)       # ronda 9 (achado G4 da ronda 2): apagar o basalt.toml também conta como mudança

    def reler_se_mudou(self) -> bool:
        """se o basalt.toml mudou desde a última leitura, os utilizadores passam a ser os novos (apagar um token ou um utilizador
        revoga-o no pedido seguinte) e as sessões de quem deixou de existir, ou mudou de senha, terminam.
        ronda 9 (achado G4 da ronda 2): um toml que mudou e não carrega (erro, sem utilizadores, apagado) FECHA a porta — ninguém
        entra, nem por token, até voltar a carregar (diz-se porquê no stderr); antes ficavam os utilizadores anteriores, e apagar o
        último utilizador (ou o ficheiro) não revogava nada. A marca do ficheiro só se actualiza DEPOIS de lido, dentro do trinco:
        um pedido que chegue a meio da releitura espera por ela em vez de entrar com os utilizadores antigos."""
        marca = self._marca_do_toml()
        if marca is None or marca == self._marca:
            return False
        with self._lock:
            if marca == self._marca:
                return False
            try:
                novo = config.carregar(self._origem)
            except (config.ConfigInvalida, OSError, ValueError) as e:
                self._fechado = str(e) or e.__class__.__name__   # ronda 10 (G3 da r3): fechar ANTES de a marca nova se ver
                self._marca = marca
                print(f"basalt.toml mudou mas não carrega ({e}): ninguém entra até o corrigires", file=sys.stderr, flush=True)
                return False
            antes = {u.email.lower(): u.senha_hash for u in self.cfg.utilizadores}
            novos = list(novo.utilizadores)
            vivos = {u.email.lower(): u.senha_hash for u in novos}
            # ronda 8 (configuração relida em execução): quem mudou de senha também sai — uma sessão roubada não sobrevive à troca
            outra_senha = {e for e, h in vivos.items() if e in antes and antes[e] != h}
            mortas = [k for k, s in self._sessoes.items() if s.email.lower() not in vivos or s.email.lower() in outra_senha]
            for k in mortas:
                self._sessoes.pop(k, None)
            self.cfg.utilizadores = novos                      # o mesmo objecto Config que o servidor web usa
            if getattr(self, "oauth", None) is not None:
                self.oauth.contas_mudaram()                    # ronda 13: quem saiu ou mudou fica com as ligações revogadas na base
            self._fechado = ""
            # ronda 10 (achado G3 da r3): a marca nova só se publica DEPOIS de os utilizadores novos estarem no lugar (e as sessões
            # mortas fora). Antes mudava primeiro: um pedido que visse a marca nova saltava o trinco e autenticava-se (Bearer, proxy)
            # contra a lista antiga — a conta acabada de apagar ainda entrava durante a troca.
            self._marca = marca
            if mortas:
                try:
                    self._guardar()
                except OSError as e:
                    print(f"sessões: não consegui gravar ({e})", file=sys.stderr, flush=True)
            print(f"basalt.toml relido: {len(novos)} utilizador(es)", file=sys.stderr, flush=True)
            return True

    # ── limite por IP ──
    def _esquecer_falhas(self, agora: float):
        """ronda 9 (achado M4 da ronda 2): as tentativas falhadas mais velhas que a janela esquecem-se — de TODOS os IPs, não só do que pergunta
        (antes um IP que nunca voltava ficava na memória até reiniciar). Um IP sem tentativas dentro da janela deixa de ter entrada."""
        janela = self.cfg.auth.janela_minutos * 60
        for ip in list(self._falhas):
            fs = [t for t in self._falhas[ip] if agora - t < janela]
            if fs:
                self._falhas[ip] = fs
            else:
                del self._falhas[ip]

    def _bloqueado(self, ip: str) -> bool:
        self._esquecer_falhas(time.time())
        return len(self._falhas.get(ip, [])) >= self.cfg.auth.tentativas

    def _falhou(self, ip: str):
        agora = time.time()
        self._esquecer_falhas(agora)
        self._falhas.setdefault(ip, []).append(agora)

    # ── entrar / sair (modo senha) ──
    def entrar(self, email: str, senha: str, ip: str) -> tuple[bool, str]:
        """(ok, token-ou-motivo). Sempre o mesmo tempo e a mesma mensagem para e-mail inexistente e senha errada."""
        self.reler_se_mudou()
        with self._lock:
            if self._fechado:
                return False, "O basalt.toml do servidor tem um erro: ninguém entra até ser corrigido."
            self._podar_sessoes()
            if self._bloqueado(ip):
                return False, "Demasiadas tentativas; espera %d minutos." % self.cfg.auth.janela_minutos
            u = self.cfg.utilizador(email)
            guardado = u.senha_hash if (u and u.senha_hash) else hash_senha("senha-inexistente-%d" % secrets.randbits(32))
            if not (u and u.senha_hash and verificar_senha(guardado, senha)):
                self._falhou(ip)
                return False, "E-mail ou senha errados."
            token = secrets.token_urlsafe(32)
            agora = time.time()
            self._sessoes[hash_token(token)] = Sessao(email=u.email, expira=agora + self.cfg.auth.sessao_horas * 3600, criada=agora,
                                                      cred=impressao_credencial(u.senha_hash))
            self._falhas.pop(ip, None)
            try:
                self._guardar()
            except OSError as e:                                  # ronda 8 (configuração relida em execução): sem sessão gravada não há sessão — e diz-se
                self._sessoes.pop(hash_token(token), None)
                return False, f"Não consegui guardar a sessão ({e.strerror or e}); o início de sessão não ficou feito."
            return True, token

    def sair(self, token: str) -> tuple[bool, str]:
        """(ok, motivo). A sessão sai sempre da memória; se o ficheiro não se deixar gravar diz-se — ao reiniciar podia voltar a valer."""
        with self._lock:
            if self._sessoes.pop(hash_token(token or ""), None) is None:
                return True, ""
            try:
                self._guardar()
            except OSError as e:
                return False, (f"A sessão terminou, mas não consegui gravar o ficheiro de sessões ({e.strerror or e}): "
                               "ao reiniciar o servidor podia voltar a valer.")
            return True, ""

    def cabecalho_cookie(self, token: str, seguro: bool) -> str:
        return (f"{COOKIE}={token}; Path=/; HttpOnly; SameSite=Strict; Max-Age={self.cfg.auth.sessao_horas * 3600}"
                + ("; Secure" if seguro else ""))

    def cabecalho_apagar_cookie(self) -> str:
        return f"{COOKIE}=; Path=/; HttpOnly; SameSite=Strict; Max-Age=0"

    # ── quem é ──
    def utilizador_do_pedido(self, cabecalhos, ip: str = "", mcp: bool = False) -> Utilizador | None:
        """`cabecalhos` é o `self.headers` do handler (ou um dict). None = 401. `mcp=True` só no /mcp: aí valem também os tokens
        do «Traz a tua IA» (OAuth `bsla_` e os pessoais criados na página) — que NUNCA valem na API da página."""
        self.reler_se_mudou()
        if self._fechado:
            return None
        get = cabecalhos.get
        # agentes: Bearer (vale em todos os modos; é o único caminho para o MCP por HTTP)
        auth = (get("Authorization") or "").strip()
        if auth.lower().startswith("bearer "):
            h = hash_token(auth[7:].strip())
            u = next((u for u in self.cfg.utilizadores if u.mcp_token_hash and hmac.compare_digest(u.mcp_token_hash, h)), None)
            if u is None and mcp and getattr(self, "oauth", None) is not None:
                return self.oauth.utilizador_do_token(auth[7:].strip())
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
            self._podar_sessoes()                    # no máximo de hora a hora: as mortas saem do ficheiro mesmo que ninguém volte com o cookie delas
            s = self._sessoes.get(hash_token(m.value))
            if not s:
                return None
            if s.expira < time.time():
                self._sessoes.pop(hash_token(m.value), None)
                self._guardar_sem_falhar()           # ronda 9: um disco que não deixa gravar não derruba o pedido
                return None
        u = self.cfg.utilizador(s.email)
        # ronda 9 (achado G4 da ronda 2): a senha mudou desde que a sessão abriu (também com o servidor parado) → já não vale
        if u is None or not s.cred or not hmac.compare_digest(s.cred, impressao_credencial(u.senha_hash)):
            return None
        return u

    def token_do_pedido(self, cabecalhos) -> str:
        c = SimpleCookie()
        try:
            c.load(cabecalhos.get("Cookie") or "")
        except Exception:
            return ""
        m = c.get(COOKIE)
        return m.value if m else ""
