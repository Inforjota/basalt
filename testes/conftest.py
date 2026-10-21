"""Utilitários comuns aos testes do Basalt.

Tudo corre em pastas temporárias (memória real, cópia editável, estado, trinco, HOME): nada toca em notas reais, não se
chama nenhuma IA verdadeira (há um fornecedor simulado em `ollama_falso.py`) e não é preciso instalar o pacote — o
`conftest` põe a raiz do repositório no `sys.path`.
"""
import hashlib
import http.client
import json
import os
import re
import socket
import subprocess
import sys
import threading
import time
import urllib.parse
from pathlib import Path

import pytest

RAIZ = Path(__file__).resolve().parent.parent
AQUI = Path(__file__).resolve().parent
sys.path.insert(0, str(RAIZ))
sys.path.insert(0, str(AQUI))

# o CAS lê a graça do ambiente ao importar: curta nos testes (as pastas «salvos» seguem o espaço, ver config.Espaco)
os.environ.setdefault("BASALT_CAS_GRACA", "0.05")
# o índice nunca vai buscar fontes de fora (comandos, outros projectos) dentro dos testes
os.environ["BASALT_SIG_ISOLADO"] = "1"
# nenhuma configuração do utilizador que corre os testes entra nos testes
for _k in ("BASALT_CONFIG", "BASALT_DADOS", "BASALT_ESPACO", "BASALT_REAL", "BASALT_DIR", "BASALT_COPIA", "BASALT_WEB_COPIA",
           "BASALT_DADOS_ESPACO", "BASALT_LEDGER", "BASALT_TRINCO", "BASALT_EXCLUIR", "BASALT_SIG_DB", "BASALT_INBOX",
           "BASALT_CAS_SALVOS", "BASALT_AUTH_MODO", "BASALT_AUTH_EMAIL", "BASALT_WEB_DEV", "BASALT_IA_FORNECEDOR", "BASALT_IA_URL",
           "BASALT_IA_MODELO", "BASALT_EMB_FORNECEDOR", "BASALT_EMB_URL", "EMB_URL", "BASALT_GIT", "BASALT_GIT_NOME",
           "BASALT_GIT_EMAIL", "BASALT_PORTA", "BASALT_WEB_PORT", "BASALT_ESCUTAR", "BASALT_WEB_HOST", "BASALT_ANCORA"):
    os.environ.pop(_k, None)
# r16 (auditoria de isolamento 21/10, M4): o modo cabecalho já não arranca sem o segredo do proxy — os servidores dos testes têm-no
# (no processo e nos filhos, pelo ambiente), e o `Cliente` manda-o como o proxy (`segredo_proxy=False` = o visitante sem ele)
SEGREDO_PROXY = "segredo-do-proxy-dos-testes-0123456789"
os.environ["BASALT_SEGREDO_PROXY"] = SEGREDO_PROXY

EMAIL = "geral@exemplo.pt"
FM = "---\nname: {nome}\ndescription: nota de teste\nmetadata:\n  type: project\n---\n"


def sha(b):
    if isinstance(b, str):
        b = b.encode("utf-8")
    return hashlib.sha1(b).hexdigest()


def porta_livre():
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


def esperar_porta(porta, segundos=15, proc=None):
    fim = time.time() + segundos
    while time.time() < fim:
        if proc is not None and proc.poll() is not None:
            raise RuntimeError(f"o processo terminou antes de abrir a porta {porta}")
        try:
            with socket.create_connection(("127.0.0.1", porta), timeout=0.2):
                return
        except OSError:
            time.sleep(0.05)
    raise RuntimeError(f"a porta {porta} não abriu em {segundos} s")


@pytest.fixture(autouse=True)
def _isolar_processo():
    """cada teste começa e acaba com o ambiente, o espaço do processo, a IA e os caches do servidor como os encontrou
    (o servidor em processo põe BASALT_CONFIG & co no ambiente, e o `web` guarda o trinco e as excluídas por espaço)."""
    from basalt import config, ia, web
    antes = dict(os.environ)
    for d in (web._excls, web._trincos, web._quota_locks):      # r16: e os trincos da quota por espaço
        d.clear()
    web._ia_usos.clear()
    web._ia_usos_conta.clear()
    web._rec_usos.clear()
    yield
    config.definir_processo(None)
    config.usar(None)
    ia.definir(None)
    os.environ.clear()
    os.environ.update(antes)


class Memoria:
    """memória real + cópia editável + estado (registo, trinco, índice…) + HOME, tudo numa pasta temporária.
    `esp` é o `config.Espaco` correspondente; `ambiente()` as variáveis para os subprocessos correrem NELE."""

    def __init__(self, base: Path):
        from basalt import config
        self.base = base
        self.real, self.copia, self.home, self.dados = base / "real", base / "copia", base / "home", base / "dados"
        self.ledger, self.trinco, self.excluir = base / "ledger.json", base / "copia.lock", base / "excluidas.txt"
        self.partilhada = self.dados          # onde ficam o índice, a entrada, os «salvos»…
        self.esp = config.Espaco.de("t", base / "raiz", ambiente={
            "BASALT_REAL": str(self.real), "BASALT_COPIA": str(self.copia), "BASALT_DADOS_ESPACO": str(self.dados),
            "BASALT_LEDGER": str(self.ledger), "BASALT_TRINCO": str(self.trinco), "BASALT_EXCLUIR": str(self.excluir)}).criar()
        self.home.mkdir(parents=True, exist_ok=True)

    def ambiente(self):
        return self.esp.ambiente()

    def nota(self, nome, texto, real=True, copia=True, alinhar=True):
        if real:
            (self.real / f"{nome}.md").write_text(texto, encoding="utf-8")
        if copia:
            (self.copia / f"{nome}.md").write_text(texto, encoding="utf-8")
        if alinhar:
            led = json.loads(self.ledger.read_text()) if self.ledger.exists() else {}
            led[f"{nome}.md"] = sha(texto)
            self.ledger.write_text(json.dumps(led))

    def ler(self, onde, nome):
        p = (self.real if onde == "real" else self.copia) / f"{nome}.md"
        return p.read_text(encoding="utf-8") if p.exists() else None


@pytest.fixture
def memoria(tmp_path):
    """a memória de teste, já como espaço do processo (módulos e Vias a apontar para ela)."""
    from basalt import config
    m = Memoria(tmp_path)
    config.definir_processo(m.esp)
    return m


def ia_de_teste(ollama, embeddings=True):
    """a `ia.IA` que fala com o fornecedor simulado (chat openai + embeddings ollama)."""
    from basalt import ia
    from basalt.config import IAConfig
    return ia.IA(IAConfig(fornecedor="openai", url=ollama.url_chat, modelo_chat="modelo-de-teste",
                          embeddings_fornecedor="ollama" if embeddings else "nenhum", embeddings_url=ollama.url_emb,
                          embeddings_modelo="nomic-embed-text"))


class IAFalsa:
    """a IA de chat sem rede, para os testes que decidem o que ela responde (a junção da IA, o editor…).
    `resposta`: um texto, uma função (prompt -> texto) ou uma excepção (levantada em cada chamada). Conta `chamadas` e guarda
    os `prompts`. É uma `ia.IA` verdadeira (mesmas propriedades, `chat_activo`, `descricao()`…) com o `chat` trocado.
    `fornecedor="nenhum"` dá a IA desligada; `**cfg` são campos do `IAConfig` (propostas_por_hora, texto_maximo…)."""

    def __new__(cls, resposta="", fornecedor="openai", **cfg):
        from basalt import ia
        from basalt.config import IAConfig

        class _IA(ia.IA):
            def chat(self, prompt, sistema="", json_=False, tempo=None):
                if not self.chat_activo:
                    raise ia.IAIndisponivel("IA desligada")
                self.chamadas += 1
                self.prompts.append(prompt)
                r = self.resposta(prompt) if callable(self.resposta) else self.resposta
                if isinstance(r, BaseException):
                    raise r
                return r

        o = _IA(IAConfig(fornecedor=fornecedor, url="http://127.0.0.1:9/v1", modelo_chat="modelo-de-teste", **cfg))
        o.resposta, o.chamadas, o.prompts = resposta, 0, []
        return o


def usar_ia(resposta="", **k):
    """põe uma `IAFalsa` como a IA do processo (o fixture autouse volta a deixá-la por definir no fim do teste)."""
    from basalt import ia
    f = IAFalsa(resposta, **k)
    ia.definir(f)
    return f


class Resposta:
    def __init__(self, codigo, cabecalhos, corpo):
        self.codigo, self.cabecalhos, self.corpo = codigo, cabecalhos, corpo

    @property
    def json(self):
        return json.loads(self.corpo.decode("utf-8"))


def toml_de(porta, dados, modo="cabecalho", ollama=None, utilizadores=None, extra=""):
    """um basalt.toml mínimo para os testes. `utilizadores`: lista de dicts (email, espaco, admin, senha_hash, mcp_token_hash)."""
    utilizadores = utilizadores if utilizadores is not None else [{"email": EMAIL, "espaco": "t", "admin": True}]
    t = [f'[basalt]\ndados = "{dados}"\nporta = {porta}\nescutar = "127.0.0.1"\ngit = false\n',
         f'[auth]\nmodo = "{modo}"\n']
    if ollama is not None:
        t.append(f'[ia]\nfornecedor = "openai"\nurl = "{ollama.url_chat}"\nmodelo_chat = "modelo-de-teste"\n'
                 f'[ia.embeddings]\nfornecedor = "ollama"\nurl = "{ollama.url_emb}"\nmodelo = "nomic-embed-text"\n')
    for u in utilizadores:
        t.append("[[utilizadores]]\n" + "".join(f'{k} = {json.dumps(v)}\n' for k, v in u.items()))
    t.append(extra)
    return "\n".join(t)


class Cliente:
    """pedidos HTTP a um servidor local (porta `self.porta`)."""
    porta = 0

    def pedido(self, metodo, caminho, corpo=None, email=EMAIL, csrf=True, cabecalhos=None, bruto=None, cabecalho_email="X-Basalt-Email",
               dono=True, segredo_proxy=True):
        """`dono`: True = manda o X-Basalt-Dono da conta do pedido (como a página: ronda 9, o servidor recusa a API sem ele),
        False = não manda, texto = esse. `segredo_proxy`: True = manda o X-Basalt-Proxy-Segredo do ambiente (como o proxy, r16),
        False = não manda, texto = esse."""
        c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=30)
        h = dict(cabecalhos or {})
        if email is not None:
            h[cabecalho_email] = email
        if segredo_proxy is True:
            segredo_proxy = os.environ.get("BASALT_SEGREDO_PROXY", "")
        if segredo_proxy:
            h.setdefault("X-Basalt-Proxy-Segredo", segredo_proxy)
        if dono is True and "X-Basalt-Dono" not in h and caminho.startswith(("/api/", "/anexos/")) \
                and not caminho.startswith(("/api/eu", "/api/entrar", "/api/sair", "/api/saude")) and "Authorization" not in h:
            dono = self.dono_de_quem(h)
        if isinstance(dono, str) and dono:
            h.setdefault("X-Basalt-Dono", dono)
        if csrf:
            h["X-Basalt"] = "1"
        dados = bruto if bruto is not None else (json.dumps(corpo).encode() if corpo is not None else None)
        if dados is not None:
            h.setdefault("Content-Type", "application/json")
        c.request(metodo, caminho, body=dados, headers=h)
        r = c.getresponse()
        out = Resposta(r.status, dict(r.getheaders()), r.read())
        c.close()
        return out

    def get(self, caminho, **k):
        return self.pedido("GET", caminho, csrf=k.pop("csrf", False), **k)

    def dono_de_quem(self, h):
        """o dono que o /api/eu dá a quem traz estes cabeçalhos (guardado por servidor e por credencial); "" se não entrar."""
        cred = {k: v for k, v in h.items() if k.lower() not in ("x-basalt", "content-type", "x-basalt-dono")}
        chave = (self.porta, tuple(sorted(cred.items())))
        donos = self.__dict__.setdefault("_donos", {})
        if chave not in donos:
            c = http.client.HTTPConnection("127.0.0.1", self.porta, timeout=30)
            c.request("GET", "/api/eu", headers=cred)
            r = c.getresponse(); b = r.read(); c.close()
            d = ""
            if r.status == 200:
                try:
                    d = json.loads(b).get("dono") or ""
                except ValueError:
                    d = ""
            if not d:
                return ""                              # não se guarda: a sessão pode ainda nem existir
            donos[chave] = d
        return donos[chave]

    def nota_url(self, nome):
        return "/api/nota?nome=" + urllib.parse.quote(nome, safe="")


class Servidor(Cliente):
    """o servidor do Basalt a correr NOUTRO processo (como em produção), numa porta local, com a memória em pastas
    temporárias, modo `cabecalho` (o utilizador é o do cabeçalho X-Basalt-Email) e, se houver `ollama`, com IA e embeddings."""

    def __init__(self, mem: Memoria, ollama=None, modo="cabecalho", utilizadores=None, extra="", indexar=False):
        self.mem = mem
        # porta 0: quem escolhe é o sistema, no próprio bind — com uma porta «livre» escolhida antes, outro processo podia ficar com
        # ela entretanto e o servidor não arrancava. A porta lê-se da linha «basalt em http://…:PORTA» do registo.
        self.porta = 0
        self.toml = mem.base / "basalt.toml"
        self.toml.write_text(toml_de(0, mem.base / "raiz", modo, ollama, utilizadores, extra), encoding="utf-8")
        env = dict(os.environ, **mem.ambiente(), HOME=str(mem.home), PYTHONUNBUFFERED="1", PYTHONPATH=str(RAIZ),
                   BASALT_CAS_GRACA=os.environ["BASALT_CAS_GRACA"], BASALT_SIG_ISOLADO="1")
        env.pop("BASALT_CONFIG", None)
        self.env = env
        if indexar:      # antes de arrancar: um índice já feito (o servidor só indexa sozinho se o encontrar vazio)
            mem.excluir.write_text("".join(f"{p.stem}\n" for p in sorted(mem.real.glob("*.md")) if not (mem.copia / p.name).exists()),
                                   encoding="utf-8")        # o que a cópia não tem está retido: não entra no índice
            r = subprocess.run([sys.executable, "-m", "basalt.sig", "indexar"], env=dict(env, BASALT_CONFIG=str(self.toml)),
                               cwd=str(mem.base), capture_output=True, text=True, timeout=120)
            assert r.returncode == 0, r.stdout + r.stderr
        self.log = open(mem.base / "servidor.log", "w+")
        self.proc = subprocess.Popen([sys.executable, str(AQUI / "lanca_servidor.py"), str(self.toml)], env=env, cwd=str(mem.base),
                                     stdout=self.log, stderr=subprocess.STDOUT)
        try:
            self.porta = self._porta_do_registo()
            esperar_porta(self.porta, proc=self.proc)
        except Exception:
            self.parar()
            raise RuntimeError("o servidor não arrancou:\n" + self.registo())

    def _porta_do_registo(self, segundos=30):
        fim = time.time() + segundos
        while time.time() < fim:
            m = re.search(r"^basalt em http://\S+:(\d+) ", self.registo(), re.M)
            if m:
                return int(m.group(1))
            if self.proc.poll() is not None:
                raise RuntimeError("o processo terminou antes de abrir a porta")
            time.sleep(0.05)
        raise RuntimeError(f"o servidor não disse em que porta escuta em {segundos} s")

    def parar(self):
        if self.proc.poll() is None:
            self.proc.terminate()
            try:
                self.proc.wait(5)
            except subprocess.TimeoutExpired:
                self.proc.kill()
        self.log.close()

    def registo(self):
        return (self.mem.base / "servidor.log").read_text(errors="replace")


class ServidorLocal(Cliente):
    """o servidor DENTRO do processo de teste (criar_servidor + um fio), para o que precisa de mexer no módulo
    (ROTAS_EXTRA, IA desligada, vários utilizadores, modos de autenticação)."""

    def __init__(self, tmp_path: Path, modo="cabecalho", utilizadores=None, ollama=None, extra=""):
        from basalt import config, web
        self.dados = tmp_path / "dados-do-servidor"
        self.toml = tmp_path / "basalt.toml"
        self.toml.write_text(toml_de(0, self.dados, modo, ollama, utilizadores, extra), encoding="utf-8")
        self.cfg = config.carregar(self.toml)
        self.srv = web.criar_servidor(self.cfg, host="127.0.0.1", porta=0)
        self.porta = self.srv.server_address[1]
        self.fio = threading.Thread(target=self.srv.serve_forever, kwargs={"poll_interval": 0.05}, daemon=True)
        self.fio.start()

    def espaco(self, nome="t"):
        return self.cfg.espaco(nome)

    def parar(self):
        self.srv.shutdown()
        self.srv.server_close()


@pytest.fixture
def servidor_local(tmp_path):
    feitos = []

    def fazer(**k):
        s = ServidorLocal(tmp_path, **k)
        feitos.append(s)
        return s
    yield fazer
    for s in feitos:
        s.parar()


# ───────────────────────── testes de página (Playwright) — ver pagina_falsa.py ─────────────────────────

@pytest.fixture(scope="session")
def base():
    """URL do servidor simulado da página (porta livre, só 127.0.0.1)."""
    from http.server import ThreadingHTTPServer
    from pagina_falsa import Servidor
    srv = ThreadingHTTPServer(("127.0.0.1", 0), Servidor)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{srv.server_address[1]}"
    srv.shutdown()
    srv.server_close()


@pytest.fixture(scope="session")
def navegador():
    """um Chromium do Playwright para toda a sessão (salta-se sem Playwright ou sem o Chromium)."""
    sync_api = pytest.importorskip("playwright.sync_api", reason="precisa do Playwright para Python (pip install playwright)")
    with sync_api.sync_playwright() as pw:
        try:
            b = pw.chromium.launch()
        except Exception as e:                                # sem o Chromium do Playwright (playwright install chromium)
            pytest.skip(f"o Playwright não consegue lançar o Chromium: {str(e)[:120]}")
        yield b
        b.close()


@pytest.fixture
def contexto(navegador):
    """contexto(sw=False, **k) → novo contexto do navegador (fechado no fim). O estado do servidor simulado volta ao início."""
    from pagina_falsa import ESTADO, reset
    reset()
    abertos = []

    def novo(sw=False, **k):
        c = navegador.new_context(service_workers="allow" if sw else "block", **k)
        abertos.append(c)
        return c

    yield novo
    for ev in (ESTADO.get("sinc_solta"), ESTADO.get("put_solta"), ESTADO.get("pacote_solta")):
        if ev is not None:
            ev.set()                                          # não deixar pedidos suspensos no servidor para o teste seguinte
    for c in abertos:
        c.close()
