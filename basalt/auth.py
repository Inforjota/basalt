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
import re
import secrets
import sys
import threading
import time
import tomllib
from dataclasses import dataclass
from http.cookies import SimpleCookie
from pathlib import Path

from . import config

from .config import Config, Utilizador

COOKIE = "basalt_sessao"
PODA_SESSOES_SEGUNDOS = 3600          # ronda 9 (achado M4 da ronda 2): as sessões mortas saem do ficheiro ao carregar e, durante o uso, no máximo de hora a hora
SCRYPT = dict(n=2 ** 14, r=8, p=1)   # ~40 ms, 16 MiB; dklen 32
MAXMEM = 64 * 1024 * 1024             # o OpenSSL recusa acima de 32 MiB por omissão


# ───────────────────────────── editar o basalt.toml no sítio (a linha de comandos e a página; só a chave pedida) ─────────────────────────────
# ronda 13: vivem aqui (e não em __main__) porque o servidor também os usa — importar `basalt.__main__` de dentro do processo lançado
# com `python -m basalt` carregava o mesmo ficheiro uma segunda vez, como outro módulo ao lado de `__main__`.
_BLOCO_RX = re.compile(r"(?ms)^\[\[utilizadores\]\][ \t]*\n(.*?)(?=^\[|\Z)")


def _bloco_de(texto: str, email: str):
    """(início, fim) do bloco [[utilizadores]] com esse e-mail, ou None."""
    for m in _BLOCO_RX.finditer(texto):
        e = re.search(r'(?m)^\s*email\s*=\s*"([^"]*)"', m.group(1))
        if e and e.group(1).strip().lower() == email.lower():
            return m.start(), m.end()
    return None


def _toml_str(v: str) -> str:
    return '"' + v.replace("\\", "\\\\").replace('"', '\\"') + '"'


def definir_chave_utilizador(texto: str, email: str, chave: str, valor) -> str:
    """põe `chave = valor` no bloco desse utilizador: substitui a linha (mesmo comentada) ou acrescenta-a a seguir ao e-mail."""
    pos = _bloco_de(texto, email)
    if not pos:
        raise KeyError(email)
    a, b = pos
    bloco = texto[a:b]
    linha = f"{chave} = {_toml_str(valor) if isinstance(valor, str) else str(valor).lower()}"
    rx = re.compile(rf"(?m)^[ \t]*#?[ \t]*{re.escape(chave)}[ \t]*=[^\n]*$")
    if rx.search(bloco):
        bloco = rx.sub(linha, bloco, count=1)
    else:
        bloco = re.sub(r'(?m)^(\s*email\s*=\s*"[^"]*"[^\n]*)$', lambda m: m.group(1) + "\n" + linha, bloco, count=1)
    return texto[:a] + bloco + texto[b:]


def chave_utilizador(texto: str, email: str, chave: str):
    """o valor de `chave` no bloco desse utilizador, tal como está no TEXTO (None = não há esse utilizador ou essa chave)."""
    for u in tomllib.loads(texto).get("utilizadores") or []:
        if isinstance(u, dict) and str(u.get("email", "")).strip().lower() == email.lower():
            return u.get(chave)
    return None


def bloco_utilizador(email: str, espaco: str, nome: str, admin: bool, senha_hash: str = "") -> str:
    out = ["[[utilizadores]]", f"email = {_toml_str(email)}", f"espaco = {_toml_str(espaco)}", f"nome = {_toml_str(nome)}"]
    if senha_hash:
        out.append(f"senha_hash = {_toml_str(senha_hash)}")
    out.append(f"admin = {'true' if admin else 'false'}")
    return "\n".join(out) + "\n"


def acrescentar_utilizador(texto: str, bloco: str) -> str:
    """o novo bloco fica a seguir ao último [[utilizadores]] (ou antes de [ia], ou no fim)."""
    blocos = list(_BLOCO_RX.finditer(texto))
    if blocos:
        fim = blocos[-1].end()
        return texto[:fim].rstrip("\n") + "\n\n" + bloco + "\n" + texto[fim:].lstrip("\n")
    m = re.search(r"(?m)^\[ia\]", texto)
    if m:
        return texto[:m.start()] + bloco + "\n" + texto[m.start():]
    return texto.rstrip("\n") + "\n\n" + bloco


def remover_utilizador(texto: str, email: str) -> str:
    """tira o bloco [[utilizadores]] desse e-mail; o resto do toml (e os outros blocos) ficam exactamente iguais — TOML não
    precisa de linhas em branco entre secções (KeyError = esse e-mail não está no texto)."""
    pos = _bloco_de(texto, email)
    if not pos:
        raise KeyError(email)
    a, b = pos
    return texto[:a] + texto[b:]


def revogar_sessoes(ficheiro_sessoes: Path, email: str) -> int:
    """r14 (`basalt utilizador apagar`): tira do sessoes.json todas as sessões dessa conta — para quem já tinha a sessão aberta
    no browser deixar de poder continuar a usá-la. O servidor pode estar a correr ao mesmo tempo: a mesma escrita com cuidado
    do `Autenticador._guardar` (tmp + os.replace, 0600). Devolve quantas sessões saíram (0 = nenhuma, ou o ficheiro não existe)."""
    try:
        d = json.loads(ficheiro_sessoes.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return 0
    if not isinstance(d, dict):
        return 0
    e = (email or "").strip().lower()
    restantes = {k: v for k, v in d.items() if not (isinstance(v, dict) and str(v.get("email", "")).strip().lower() == e)}
    n = len(d) - len(restantes)
    if n:
        tmp = ficheiro_sessoes.with_suffix(".tmp")
        tmp.write_text(json.dumps(restantes), encoding="utf-8")
        os.chmod(tmp, 0o600)
        os.replace(tmp, ficheiro_sessoes)
    return n


class trinco_toml:
    """um só escritor do basalt.toml de cada vez (a interface e a linha de comandos): flock num ficheiro ao lado."""
    def __init__(self, caminho: Path):
        self.caminho = Path(caminho).with_name(Path(caminho).name + ".trinco")

    def __enter__(self):
        import fcntl
        from . import caminhos
        # ronda 13b: sem seguir ligações nem truncar (era open(…, "a"): uma ligação no lugar do trinco era seguida e o alvo criado
        # ou tomado). Uma ligação = Recusado, um OSError: quem chama diz que não gravou
        try:
            self.fd = caminhos.abrir_trinco(self.caminho)
        except PermissionError:
            # ronda 13: o trinco foi criado por outro utilizador (a CLI corrida como root deixa-o 0644 de root): o flock não precisa de
            # escrita — abre-se só para ler. Se nem isso der, a excepção (OSError) chega a quem chamou, que a explica.
            self.fd = caminhos.abrir(self.caminho.parent, [self.caminho.name])
        try:
            fcntl.flock(self.fd, fcntl.LOCK_EX)
        except BaseException:
            os.close(self.fd)
            raise
        return self

    def __exit__(self, *a):
        os.close(self.fd)


def gravar_toml(caminho: Path, texto: str):
    """troca o basalt.toml de uma vez (temporário único 0600 → só se carregar → os.replace); senão não mexe no verdadeiro.
    ronda 13: o ficheiro novo fica com o dono e o grupo do anterior (como o `sed -i`) — a CLI corrida como root sobre um basalt.toml
    do serviço deixava-o root:root 0600 e o serviço, que o relê em execução, fechava a porta a toda a gente («não carrega»)."""
    import tempfile
    caminho = Path(caminho)
    try:
        dono = os.stat(caminho)
    except OSError:
        dono = None
    fd, tmp = tempfile.mkstemp(prefix=".basalt.toml.", dir=caminho.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(texto)
        os.chmod(tmp, 0o600)
        if dono is not None:
            try:
                os.chown(tmp, dono.st_uid, dono.st_gid)
            except OSError:                       # sem privilégio para mudar o dono (não somos root): fica o nosso, como antes
                pass
        config.carregar(Path(tmp))
        os.replace(tmp, caminho)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


def editar_toml(caminho: Path, editar) -> str:
    """lê → `editar(texto)` → grava, TUDO com o trinco tomado: quem escreve parte sempre do ficheiro actual. Devolve o texto gravado.
    ronda 13: a linha de comandos lia o basalt.toml, pedia a senha no terminal (podia demorar) e gravava o texto velho — uma senha
    mudada na página entretanto era desfeita sem ninguém dar por isso (e a sessão nova dessa pessoa morria na releitura)."""
    caminho = Path(caminho)
    with trinco_toml(caminho):
        novo = editar(caminho.read_text(encoding="utf-8"))
        gravar_toml(caminho, novo)
        return novo


class SenhaMudouEntretanto(Exception):
    """ronda 13: entre conferir a senha actual e gravar a nova, a senha desta conta mudou no basalt.toml (CLI `utilizador senha`)."""


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
        self._criar_lock = threading.Lock()   # r14: uma conta nova de cada vez (o tecto e o «já existe» conferem-se lá dentro)
        self.ao_criar_conta = None            # r14: fn(utilizador) que o servidor põe para preparar o espaço da conta nova (pastas + git)
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
            # auditoria 21/10: o que em [auth] governa as contas novas e os tectos também é relido — descer o
            # tecto, apertar os domínios ou desligar criar_contas ao vivo vale no pedido seguinte (antes só a lista de utilizadores
            # mudava e _criar_conta ficava com o max_contas do arranque). Modo, cabeçalhos e segredo ficam como arrancaram: isso é
            # reiniciar (o segredo nem vem do toml); ligar criar_contas ao vivo sem segredo continua a não criar nada (_criar_conta).
            for campo in ("criar_contas", "dominios", "max_contas", "espaco_max_mb"):
                setattr(self.cfg.auth, campo, getattr(novo.auth, campo))
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

    def mudar_senha(self, email: str, atual: str, nova: str, ip: str) -> tuple[bool, str]:
        """19/10/2026 («Mudar a senha» na interface): (ok, token-da-sessão-nova ou motivo). Só no modo senha. Confere a senha
        actual (com o mesmo limite por IP do entrar), grava o hash novo no basalt.toml (trinco + ficheiro temporário + validação,
        como o `utilizador senha`) e relê-o: as outras sessões e as ligações das IAs caem sozinhas (estão presas à impressão da
        senha antiga). Este browser recebe uma sessão nova para não ser posto fora."""
        if self.cfg.auth.modo != "senha":
            return False, "Neste servidor o início de sessão não é por senha do Basalt."
        if self._origem is None:
            return False, "Este servidor não tem basalt.toml onde gravar a senha."
        if not isinstance(nova, str) or len(nova) < 8:
            return False, "A senha nova tem de ter pelo menos 8 caracteres."
        if len(nova) > 1024:
            return False, "A senha nova é demasiado longa."
        if nova == atual:
            return False, "A senha nova é igual à actual."
        self.reler_se_mudou()
        with self._lock:
            if self._fechado:
                return False, "O basalt.toml do servidor tem um erro: corrige-o primeiro."
            if self._bloqueado(ip):
                return False, "Demasiadas tentativas; espera %d minutos." % self.cfg.auth.janela_minutos
            u = self.cfg.utilizador(email)
            if not (u and u.senha_hash and verificar_senha(u.senha_hash, atual or "")):
                self._falhou(ip)
                return False, "A senha actual está errada."
        h = hash_senha(nova)                                  # scrypt fora do trinco (não atrasa os outros pedidos)
        conta, conferida = u.email, u.senha_hash

        def trocar(texto: str) -> str:
            """ronda 13 (compara-e-troca): só se a senha no FICHEIRO ainda for a que se conferiu — entre a conferência e o trinco
            a CLI (`utilizador senha`) pode ter mudado esta conta, e a prova dada era da senha antiga."""
            if _bloco_de(texto, conta) is None:
                raise KeyError(conta)
            if (chave_utilizador(texto, conta, "senha_hash") or "") != conferida:
                raise SenhaMudouEntretanto()
            return definir_chave_utilizador(texto, conta, "senha_hash", h)   # o mesmo editor do `utilizador senha` (só a chave pedida)
        try:
            editar_toml(self._origem, trocar)
        except KeyError:
            return False, "A tua conta não está no basalt.toml (foi apagada?)."
        except SenhaMudouEntretanto:
            return False, "A senha desta conta foi mudada entretanto no servidor: entra outra vez com a senha nova."
        except (OSError, config.ConfigInvalida, ValueError) as e:
            return False, f"Não consegui gravar a senha nova ({getattr(e, 'strerror', None) or e}); a antiga continua a valer."
        self.reler_se_mudou()
        with self._lock:
            u = self.cfg.utilizador(email)
            if not (u and hmac.compare_digest(u.senha_hash or "", h)):
                return False, "A senha foi gravada, mas o servidor ainda não a leu: entra outra vez com a senha nova."
            # ronda 13b: só se diz que correu bem com o corte das IAs GRAVADO na base (em memória perdia-se num reinício)
            if getattr(self, "oauth", None) is not None and self.oauth.revogacoes_pendentes():
                return False, ("A senha mudou, mas o corte das IAs ligadas ainda não ficou gravado (base ocupada; até lá ficam "
                               "recusadas): entra outra vez com a senha nova.")
            token = secrets.token_urlsafe(32)
            agora = time.time()
            self._sessoes[hash_token(token)] = Sessao(email=u.email, expira=agora + self.cfg.auth.sessao_horas * 3600, criada=agora,
                                                      cred=impressao_credencial(u.senha_hash))
            try:
                self._guardar()
            except OSError:
                self._sessoes.pop(hash_token(token), None)
                return False, "A senha mudou, mas não consegui guardar a sessão: entra outra vez com a senha nova."
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
            return self._do_proxy(get)
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

    # ── r14: modo cabecalho com o login Google (oauth2-proxy) e contas criadas no 1.º login ──
    def proxy_confirmado(self, get) -> bool:
        """com segredo do proxy configurado, só um pedido que o traga veio mesmo do proxy (o resto é o visitante a escrever o
        cabeçalho do e-mail numa rota que o proxy deixou passar sem login). Sem segredo configurado: como antes."""
        seg = self.cfg.auth.segredo_proxy
        if not seg:
            return True
        dado = (get(self.cfg.auth.cabecalho_segredo) or "").strip()
        return hmac.compare_digest(dado.encode("utf-8"), seg.encode("utf-8"))

    def _do_proxy(self, get) -> Utilizador | None:
        if not self.proxy_confirmado(get):
            return None
        email = (get(self.cfg.auth.cabecalho) or "").strip().lower()
        if not email:
            return None
        sub = (get(self.cfg.auth.cabecalho_sub) or "").strip() if self.cfg.auth.cabecalho_sub else ""
        u = self.cfg.utilizador(email)
        if u is None and self.cfg.auth.criar_contas:
            u = self._criar_conta(email, sub)
        if u is None:
            return None
        # a conta Google desse e-mail foi apagada e recriada (Workspace) → é outra pessoa: não entra nas notas da anterior
        if self.cfg.auth.cabecalho_sub and u.sub and not hmac.compare_digest(u.sub, sub):
            return None
        return u

    def _criar_conta(self, email: str, sub: str) -> Utilizador | None:
        """conta nova (espaço próprio com nome aleatório, sem senha, não admin) no basalt.toml, dentro do trinco do toml.
        None = não se cria (e-mail estranho, domínio fora da lista, tecto de contas, sem `sub` quando se exige, erro a gravar)."""
        if self._origem is None:
            return None
        self.reler_se_mudou()                                   # auditoria 21/10: o tecto e os domínios são os do toml ACTUAL
        a = self.cfg.auth
        if self._fechado or len(a.segredo_proxy) < 32:          # sem segredo, nunca (defesa a mais)
            return None
        if len(email) > 254 or not re.fullmatch(r"[a-z0-9._%+-]+@[a-z0-9-]+(\.[a-z0-9-]+)+", email):
            return None
        if a.dominios and email.rsplit("@", 1)[1] not in a.dominios:
            return None
        if a.cabecalho_sub and not re.fullmatch(r"[A-Za-z0-9._:@|-]{1,255}", sub or ""):
            return None
        with self._criar_lock:
            self.reler_se_mudou()
            a = self.cfg.auth                                   # relido dentro do trinco (antes ficava o [auth] de fora dele)
            u = self.cfg.utilizador(email)
            if u is not None:                                   # outro pedido criou-a entretanto
                return u
            if not a.criar_contas or (a.dominios and email.rsplit("@", 1)[1] not in a.dominios):
                return None                                     # o toml mudou entretanto: desligou-se ou apertaram-se os domínios
            if a.max_contas and len(self.cfg.utilizadores) >= a.max_contas:
                print(f"contas: tecto de {a.max_contas} atingido — {email} não foi criada", file=sys.stderr, flush=True)
                return None
            espaco = "c" + secrets.token_hex(6)                  # nunca derivado do e-mail (não se adivinha nem se enumera)

            def editar(texto):
                if _bloco_de(texto, email):
                    return texto
                novo = acrescentar_utilizador(texto, bloco_utilizador(email, espaco, email.split("@")[0], False))
                return definir_chave_utilizador(novo, email, "sub", sub) if sub else novo
            try:
                editar_toml(self._origem, editar)
            except (OSError, config.ConfigInvalida, ValueError) as e:
                print(f"contas: não consegui criar {email} ({getattr(e, 'strerror', None) or e})", file=sys.stderr, flush=True)
                return None
            self.reler_se_mudou()
            u = self.cfg.utilizador(email)
            if u is not None:
                print(f"contas: {email} criada no 1.º login (espaço {u.espaco})", file=sys.stderr, flush=True)
                if self.ao_criar_conta is not None:     # o servidor prepara o espaço (pastas + git) como faz aos configurados no arranque
                    try:
                        self.ao_criar_conta(u)
                    except Exception as e:              # a conta existe; sem git fica só sem histórico (como um espaço sem git)
                        print(f"contas: aviso — não consegui preparar o espaço de {email} ({e})", file=sys.stderr, flush=True)
            return u

    def token_do_pedido(self, cabecalhos) -> str:
        c = SimpleCookie()
        try:
            c.load(cabecalhos.get("Cookie") or "")
        except Exception:
            return ""
        m = c.get(COOKIE)
        return m.value if m else ""
