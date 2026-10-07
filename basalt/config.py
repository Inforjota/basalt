"""basalt.config — a configuração (basalt.toml + variáveis de ambiente) e o Espaço: as pastas de um utilizador.

Ordem: valores por omissão → `basalt.toml` (BASALT_CONFIG, ./basalt.toml, ~/.config/basalt/basalt.toml,
/etc/basalt/basalt.toml) → variáveis de ambiente (vencem sempre; são também o que os testes usam).

Um Espaço tem três pastas: `memoria/` (as notas, a verdade; é um cofre Obsidian), `copia/` (a cópia editável onde a web
e o telemóvel escrevem; o 2vias leva à memória) e `dados/` (estado: registo, trinco, índice, entrada, categorias…).
O servidor serve vários utilizadores: cada pedido corre «dentro» do espaço do utilizador autenticado (`usar`/`actual`).
"""
from __future__ import annotations

import contextlib
import os
import threading
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

ENV_CONFIG = "BASALT_CONFIG"
LOCAIS = ("./basalt.toml", "~/.config/basalt/basalt.toml", "/etc/basalt/basalt.toml")
ESPACO_OMISSAO = "principal"
FORNECEDORES = ("nenhum", "openai", "anthropic", "claude-cli")
MODOS_AUTH = ("cabecalho", "senha", "aberto")


class ConfigInvalida(ValueError):
    pass


@dataclass
class Utilizador:
    email: str
    espaco: str = ESPACO_OMISSAO
    nome: str = ""
    senha_hash: str = ""        # `basalt utilizador senha EMAIL` (scrypt); vazio = não entra por senha
    mcp_token_hash: str = ""    # `basalt utilizador mcp-token EMAIL` (sha256 do token); vazio = sem MCP
    admin: bool = False


@dataclass
class AuthConfig:
    modo: str = "senha"                 # cabecalho (proxy com SSO) | senha (formulário próprio) | aberto (só 127.0.0.1)
    cabecalho: str = "X-Basalt-Email"   # só no modo cabecalho: posto pelo proxy, nunca de confiança vindo do visitante
    sessao_horas: int = 24 * 14
    tentativas: int = 5                 # por IP, por janela
    janela_minutos: int = 15


@dataclass
class IAConfig:
    fornecedor: str = "nenhum"          # nenhum | openai (Ollama/OpenRouter/Mistral/LM Studio) | anthropic | claude-cli
    url: str = ""                       # openai: base, ex. http://127.0.0.1:11434/v1 ou https://openrouter.ai/api/v1
    chave_env: str = "BASALT_IA_CHAVE"  # NOME da variável com a chave (a chave nunca vai no toml)
    chave_ficheiro: str = ""            # ou um ficheiro 0600 com a chave
    modelo_chat: str = ""
    tempo_limite: int = 120             # segundos por chamada
    propostas_por_hora: int = 12        # propostas de junção pela IA (sincronizar)
    pedidos_por_hora: int = 40          # IA no editor (resumir/reescrever/…)
    texto_maximo: int = 6000            # caracteres por pedido ao editor
    embeddings_fornecedor: str = ""     # "" = o mesmo que o chat quando é openai; openai | ollama | nenhum
    embeddings_url: str = ""            # openai: …/v1 (usa /embeddings) · ollama: http://host:11434 (usa /api/embeddings)
    embeddings_modelo: str = "nomic-embed-text"
    embeddings_chave_env: str = ""      # "" = a mesma do chat

    def embeddings_activos(self) -> bool:
        f = self.embeddings_fornecedor or ("openai" if self.fornecedor == "openai" else "nenhum")
        return f != "nenhum"


@dataclass
class EntradaConfig:
    automatica: bool = True             # o vigia arruma sozinho as ideias de fontes autorizadas
    fontes_de_confianca: tuple = ("web", "terminal", "cli", "mcp")
    telegram_token_env: str = ""        # opcional: bot Telegram para «guardar ideias» (BASALT_TG_TOKEN)
    telegram_chats: tuple = ()          # ids de chat autorizados


@dataclass
class GitConfig:
    nome: str = "Basalt"                # autor das fotografias git da memória (não precisa de existir como conta)
    email: str = "basalt@localhost"


@dataclass
class CategoriasConfig:
    lista: dict = field(default_factory=dict)    # {"Nome": "definição para a IA"}; vazio = a lista de origem (basalt.categorias.DEF)
    regras: list = field(default_factory=list)   # [["regex pelo nome da nota", "Nome"]]; vazio = as regras de origem


@dataclass
class Config:
    dados: Path                         # raiz: <dados>/<espaco>/{memoria,copia,dados}
    porta: int = 8121
    escutar: str = "127.0.0.1"
    lingua: str = "pt"
    git: bool = True                    # fotografias git da memória (histórico, diário, desfazer fusões)
    auth: AuthConfig = field(default_factory=AuthConfig)
    ia: IAConfig = field(default_factory=IAConfig)
    entrada: EntradaConfig = field(default_factory=EntradaConfig)
    git_autor: GitConfig = field(default_factory=GitConfig)
    categorias: CategoriasConfig = field(default_factory=CategoriasConfig)
    utilizadores: list = field(default_factory=list)
    fontes_extra: list = field(default_factory=list)   # comandos que imprimem JSON [{fonte, ref, titulo, texto}] para o índice
    origem: Path | None = None          # o toml lido (None = só omissão + ambiente)

    # ── consultas ──
    def utilizador(self, email: str) -> Utilizador | None:
        e = (email or "").strip().lower()
        return next((u for u in self.utilizadores if u.email.lower() == e), None)

    def espaco(self, nome: str = ESPACO_OMISSAO) -> "Espaco":
        return Espaco.de(nome, self.dados)

    def espaco_de(self, u: Utilizador) -> "Espaco":
        return self.espaco(u.espaco)

    def validar(self) -> None:
        if self.auth.modo not in MODOS_AUTH:
            raise ConfigInvalida(f"[auth] modo tem de ser um de {MODOS_AUTH}, não {self.auth.modo!r}")
        if self.ia.fornecedor not in FORNECEDORES:
            raise ConfigInvalida(f"[ia] fornecedor tem de ser um de {FORNECEDORES}, não {self.ia.fornecedor!r}")
        if self.ia.fornecedor == "openai" and not self.ia.url:
            raise ConfigInvalida("[ia] fornecedor openai precisa de url (ex.: http://127.0.0.1:11434/v1)")
        if self.auth.modo == "aberto" and self.escutar not in ("127.0.0.1", "::1", "localhost"):
            raise ConfigInvalida("[auth] modo aberto só com escutar = 127.0.0.1 — qualquer pessoa na rede entraria")
        vistos = set()
        for u in self.utilizadores:
            e = u.email.lower()
            if "@" not in e or e in vistos:
                raise ConfigInvalida(f"utilizador inválido ou repetido: {u.email!r}")
            vistos.add(e)
            if not ESPACO_RX_OK(u.espaco):
                raise ConfigInvalida(f"nome de espaço inválido: {u.espaco!r} (letras, dígitos, _ e -)")
        if self.auth.modo != "aberto" and not self.utilizadores:
            raise ConfigInvalida("sem utilizadores: acrescenta [[utilizadores]] ou usa `basalt utilizador novo EMAIL`")


def ESPACO_RX_OK(nome: str) -> bool:
    return bool(nome) and len(nome) <= 64 and all(c.isalnum() or c in "_-" for c in nome) and not nome.startswith("-")


# ───────────────────────────── o Espaço ─────────────────────────────
# Variáveis antigas (produção do José e a suite de testes) que continuam a mandar, campo a campo:
_AMBIENTE = {
    "real": ("BASALT_REAL", "BASALT_DIR"), "copia": ("BASALT_COPIA", "BASALT_WEB_COPIA"), "dados": ("BASALT_DADOS_ESPACO",),
    "ledger": ("BASALT_LEDGER",), "trinco": ("BASALT_TRINCO",), "excluir": ("BASALT_EXCLUIR",),
    "semantica": ("BASALT_SIG_DB",), "inbox": ("BASALT_INBOX",), "cas_salvos": ("BASALT_CAS_SALVOS",),
    "versoes_offline": ("BASALT_VERSOES_OFFLINE",), "categorias": ("BASALT_CATEGORIAS",), "jobs_unir": ("BASALT_UNIR_JOBS",),
}


@dataclass(frozen=True)
class Espaco:
    nome: str
    raiz: Path
    real: Path              # memoria/ — as notas (verdade; cofre Obsidian)
    copia: Path             # copia/   — a cópia editável (web, telemóvel, 2vias)
    dados: Path             # dados/   — estado
    ledger: Path            # hashes do último ciclo do 2vias
    trinco: Path            # flock partilhado por quem escreve na cópia
    excluir: Path           # notas com segredo (nunca servidas)
    semantica: Path         # índice (sqlite)
    inbox: Path             # entrada.jsonl (ideias por arrumar)
    toques: Path
    apagadas: Path
    permissoes_entrada: Path
    auto_arrumar: Path      # pasta
    categorias: Path
    jobs_unir: Path         # pasta
    versoes_offline: Path   # pasta
    cas_salvos: Path        # pasta
    anexos: Path            # memoria/anexos (imagens, PDF)

    @classmethod
    def de(cls, nome: str, raiz_dados: Path | str, ambiente: dict | None = None) -> "Espaco":
        amb = os.environ if ambiente is None else ambiente
        raiz = Path(raiz_dados).expanduser() / nome
        d = Path(amb.get("BASALT_DADOS_ESPACO", raiz / "dados")).expanduser()

        def env(campo, omissao):
            for k in _AMBIENTE.get(campo, ()):
                if amb.get(k):
                    return Path(amb[k]).expanduser()
            return omissao
        real = env("real", raiz / "memoria")
        return cls(
            nome=nome, raiz=raiz, real=real, copia=env("copia", raiz / "copia"), dados=d,
            ledger=env("ledger", d / "ledger.json"), trinco=env("trinco", d / "copia.lock"),
            excluir=env("excluir", d / "excluidas.txt"), semantica=env("semantica", d / "semantica.sqlite"),
            inbox=env("inbox", d / "entrada.jsonl"), toques=d / "toques.jsonl", apagadas=d / "entrada-apagadas.jsonl",
            permissoes_entrada=d / "entrada-permissoes.json", auto_arrumar=d / "auto-arrumar",
            categorias=env("categorias", d / "categorias.json"), jobs_unir=env("jobs_unir", d / "unir"),
            versoes_offline=env("versoes_offline", d / "offline-versoes"), cas_salvos=env("cas_salvos", d / "cas-salvos"),
            anexos=real / "anexos",
        )

    def criar(self) -> "Espaco":
        for p in (self.real, self.copia, self.dados, self.auto_arrumar, self.jobs_unir, self.versoes_offline, self.cas_salvos):
            p.mkdir(parents=True, exist_ok=True)
        return self

    def ambiente(self) -> dict:
        """as variáveis para os processos filhos (sig, 2vias, categorias…) correrem NESTE espaço."""
        return {"BASALT_REAL": str(self.real), "BASALT_DIR": str(self.real), "BASALT_COPIA": str(self.copia),
                "BASALT_WEB_COPIA": str(self.copia), "BASALT_DADOS_ESPACO": str(self.dados), "BASALT_LEDGER": str(self.ledger),
                "BASALT_TRINCO": str(self.trinco), "BASALT_EXCLUIR": str(self.excluir), "BASALT_SIG_DB": str(self.semantica),
                "BASALT_INBOX": str(self.inbox), "BASALT_CAS_SALVOS": str(self.cas_salvos),
                "BASALT_VERSOES_OFFLINE": str(self.versoes_offline), "BASALT_CATEGORIAS": str(self.categorias),
                "BASALT_UNIR_JOBS": str(self.jobs_unir)}


_actual = threading.local()


def usar(espaco: Espaco | None) -> None:
    _actual.espaco = espaco


def actual() -> Espaco:
    e = getattr(_actual, "espaco", None) or _processo
    if e is None:
        raise RuntimeError("nenhum espaço activo neste fio (usar(espaco) primeiro)")
    return e


@contextlib.contextmanager
def dentro(espaco: Espaco):
    anterior = getattr(_actual, "espaco", None)
    usar(espaco)
    try:
        yield espaco
    finally:
        usar(anterior)


_processo: Espaco | None = None


def definir_processo(espaco: Espaco | None) -> None:
    """para os programas de linha de comandos (`python -m basalt.duasvias`, …): um só espaço para o processo inteiro."""
    global _processo
    _processo = espaco


def espaco_de_processo(ambiente: dict | None = None) -> Espaco:
    """o espaço de um subprocesso: BASALT_ESPACO (nome; omissão «principal») dentro da raiz de dados do basalt.toml ou de
    BASALT_DADOS. As variáveis antigas por pasta (BASALT_REAL, BASALT_COPIA, …) mandam, campo a campo (ver Espaco.de);
    `Espaco.ambiente()` é o que o servidor passa aos filhos."""
    amb = os.environ if ambiente is None else ambiente
    nome = amb.get("BASALT_ESPACO") or ESPACO_OMISSAO
    try:
        raiz = carregar(ambiente=amb).dados
    except ConfigInvalida:
        raiz = Path(amb.get("BASALT_DADOS") or "~/.local/share/basalt/dados").expanduser()
    return Espaco.de(nome, raiz, amb)


class Via:
    """Um caminho que segue o Espaço activo: `REAL = Via("real")` é sempre a pasta `real` do espaço do pedido (fio) ou, num
    subprocesso, do espaço do processo. Comporta-se como um `Path` (o que não está aqui vai para o caminho actual)."""

    def __init__(self, campo: str, *resto: str):
        self._campo, self._resto = campo, resto

    def _p(self) -> Path:
        e = getattr(_actual, "espaco", None) or _processo
        if e is None:
            raise RuntimeError(f"nenhum espaço activo (config.usar/dentro) ao aceder a {self._campo!r}")
        return Path(getattr(e, self._campo), *self._resto)

    def __truediv__(self, outro):
        return Via(self._campo, *self._resto, str(outro))

    def __fspath__(self):
        return str(self._p())

    __str__ = __fspath__

    def __repr__(self):
        return f"Via({self._campo!r}{''.join(', ' + repr(r) for r in self._resto)})"

    def __eq__(self, outro):
        try:
            return self._p() == Path(os.fspath(outro))
        except (TypeError, RuntimeError):
            return NotImplemented

    def __hash__(self):
        return hash(self._p())

    def __getattr__(self, nome):
        if nome.startswith("__"):
            raise AttributeError(nome)
        return getattr(self._p(), nome)


# ───────────────────────────── carregar ─────────────────────────────
def _ficheiro(caminho: str | Path | None) -> Path | None:
    if caminho:
        p = Path(caminho).expanduser()
        if not p.is_file():
            raise ConfigInvalida(f"configuração não encontrada: {p}")
        return p
    if os.environ.get(ENV_CONFIG):
        return _ficheiro(os.environ[ENV_CONFIG])
    for c in LOCAIS:
        p = Path(c).expanduser()
        if p.is_file():
            return p
    return None


def _int(amb, k, omissao):
    v = amb.get(k)
    try:
        return int(v) if v not in (None, "") else omissao
    except ValueError:
        raise ConfigInvalida(f"{k} tem de ser um inteiro, não {v!r}")


def carregar(caminho: str | Path | None = None, ambiente: dict | None = None) -> Config:
    amb = os.environ if ambiente is None else ambiente
    f = _ficheiro(caminho)
    t = {}
    if f:
        try:
            t = tomllib.loads(f.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raise ConfigInvalida(f"{f}: {e}")
    b = t.get("basalt", {})
    dados = Path(amb.get("BASALT_DADOS") or b.get("dados") or "~/.local/share/basalt/dados").expanduser()
    cfg = Config(dados=dados, origem=f)
    cfg.porta = _int(amb, "BASALT_PORTA", _int(amb, "BASALT_WEB_PORT", int(b.get("porta", 8121))))
    cfg.escutar = amb.get("BASALT_ESCUTAR") or amb.get("BASALT_WEB_HOST") or b.get("escutar", "127.0.0.1")
    cfg.lingua = amb.get("BASALT_LINGUA") or b.get("lingua", "pt")
    cfg.git = (amb.get("BASALT_GIT") or ("1" if b.get("git", True) else "0")) == "1"

    a = t.get("auth", {})
    cfg.auth = AuthConfig(modo=amb.get("BASALT_AUTH_MODO") or a.get("modo", "senha"),
                          cabecalho=a.get("cabecalho", "X-Basalt-Email"), sessao_horas=int(a.get("sessao_horas", 24 * 14)),
                          tentativas=int(a.get("tentativas", 5)), janela_minutos=int(a.get("janela_minutos", 15)))
    if amb.get("BASALT_WEB_DEV") == "1":          # compatibilidade: o antigo «modo dev» = aberto (só em loopback)
        cfg.auth.modo = "aberto"

    i = t.get("ia", {})
    e = i.get("embeddings", {}) if isinstance(i.get("embeddings"), dict) else {}
    cfg.ia = IAConfig(
        fornecedor=amb.get("BASALT_IA_FORNECEDOR") or i.get("fornecedor", "nenhum"),
        url=amb.get("BASALT_IA_URL") or i.get("url", ""), chave_env=i.get("chave_env", "BASALT_IA_CHAVE"),
        chave_ficheiro=i.get("chave_ficheiro", ""), modelo_chat=amb.get("BASALT_IA_MODELO") or i.get("modelo_chat", ""),
        tempo_limite=int(i.get("tempo_limite", 120)), propostas_por_hora=int(i.get("propostas_por_hora", 12)),
        pedidos_por_hora=int(i.get("pedidos_por_hora", 40)), texto_maximo=int(i.get("texto_maximo", 6000)),
        embeddings_fornecedor=amb.get("BASALT_EMB_FORNECEDOR") or e.get("fornecedor", ""),
        embeddings_url=amb.get("BASALT_EMB_URL") or amb.get("EMB_URL") or e.get("url", ""),
        embeddings_modelo=amb.get("BASALT_EMB_MODELO") or e.get("modelo", "nomic-embed-text"),
        embeddings_chave_env=e.get("chave_env", ""),
    )
    if cfg.ia.embeddings_url and not cfg.ia.embeddings_fornecedor:    # compatibilidade: EMB_URL do Ollama (…/api/embeddings)
        cfg.ia.embeddings_fornecedor = "ollama" if "/api/" in cfg.ia.embeddings_url or ":11434" in cfg.ia.embeddings_url else "openai"

    en = t.get("entrada", {})
    tg = en.get("telegram", {}) if isinstance(en.get("telegram"), dict) else {}
    cfg.entrada = EntradaConfig(automatica=bool(en.get("automatica", True)),
                                fontes_de_confianca=tuple(en.get("fontes_de_confianca", ("web", "terminal", "cli", "mcp"))),
                                telegram_token_env=tg.get("token_env", ""), telegram_chats=tuple(str(c) for c in tg.get("chats", ())))

    g = t.get("git", {})
    cfg.git_autor = GitConfig(nome=str(g.get("nome", "Basalt")), email=str(g.get("email", "basalt@localhost")))
    ca = t.get("categorias", {})
    cfg.categorias = CategoriasConfig(lista={str(k): str(v) for k, v in ca.get("lista", {}).items()},
                                      regras=[[str(r[0]), str(r[1])] for r in ca.get("regras", []) if len(r) == 2])

    for u in t.get("utilizadores", []):
        if not isinstance(u, dict) or not u.get("email"):
            raise ConfigInvalida("[[utilizadores]] precisa de email")
        cfg.utilizadores.append(Utilizador(email=str(u["email"]).strip(), espaco=str(u.get("espaco", ESPACO_OMISSAO)),
                                           nome=str(u.get("nome", "")), senha_hash=str(u.get("senha_hash", "")),
                                           mcp_token_hash=str(u.get("mcp_token_hash", "")), admin=bool(u.get("admin", False))))
    # compatibilidade: BASALT_AUTH_EMAIL="a@x.pt,b@y.pt" → utilizadores no espaço principal (modo cabecalho sem toml)
    for em in (amb.get("BASALT_AUTH_EMAIL") or "").split(","):
        em = em.strip()
        if em and not cfg.utilizador(em):
            cfg.utilizadores.append(Utilizador(email=em))
    fe = t.get("fontes_extra", {})
    cfg.fontes_extra = [str(c) for c in fe.get("comandos", [])] if isinstance(fe, dict) else []
    cfg.validar()
    return cfg


def exemplo() -> str:
    return (Path(__file__).resolve().parent.parent / "basalt.toml.exemplo").read_text(encoding="utf-8")
