"""Fase D, princípio 3 do DESENHO: NADA do autor nem da sua infra dentro do pacote — nem IPs privados, nem nomes de máquinas,
nem caminhos de casa, nem ferramentas internas. Falha com ficheiro:linha para se corrigir à mão.
README/LICENCA/CHANGELOG podem dizer quem fez (nome e empresa); mais nada pode.
Este ficheiro também é varrido: os nomes a procurar estão em hexadecimal, para não serem eles próprios um rasto."""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTAS = ("basalt", "static", "testes", "docker", "sistema", "docs", "site", "editor-src")
SOLTOS = ("README.md", "README.en.md", "CHANGELOG.md", "LICENCA", "DESENHO.md", "pyproject.toml", "pytest.ini")
EXTENSOES = {".py", ".html", ".js", ".css", ".md", ".toml", ".exemplo", ".yml", ".yaml", ".service", ".timer", ".json", ".txt",
             ".webmanifest", ".sh", ".ini", ".cfg", ".env", ""}
# ronda 8 (rasto): exclui-se pelas partes do caminho DENTRO do pacote — pelo caminho absoluto, uma cópia de trabalho guardada numa
# pasta com um destes nomes ficava com o pacote inteiro de fora e o teste passava sem ler nada
EXCLUIR = {".worktrees", "node_modules", "__pycache__", ".git", ".pytest_cache"}


def _w(h: str) -> str:
    return bytes.fromhex(h).decode()


def _e(h: str) -> str:
    return re.escape(_w(h))


# nomes da instalação de origem: máquinas, projectos, ferramentas, pessoas (em qualquer sítio da palavra)
_SOLTAS = ("6c616365726461", "63616c68657461", "70726f786d6f78", "6c6567696f6e", "73796e6f6c6f6779", "6865726d6573", "7768617473617070",
           "626173616c742d776562", "626173616c745f7765622e7079", "626173616c742d3276696173", "626173616c742d736967",
           "626173616c745f6361732e7079", "6a6f736571", "71756164726f73")
# os que só contam como palavra inteira (são pedaços de palavras comuns)
_INTEIRAS = ("68697665", "6661726f6c", "6173747261", "7461736361")
_RONDA_DE_ORIGEM = "74343172"          # o prefixo das rondas de auditoria da origem, seguido de um algarismo
_EMPRESA = "696e666f726a6f7461"

# o que denuncia a origem: rede privada, máquinas, caminhos, projectos e ferramentas de casa
RASTOS = re.compile(r"(?<![\d.])(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(?![\d.])|"   # toda a rede privada (RFC 1918)
                    r"/home/(?!utilizador/|exemplo/)[a-z]+/|~/\.local/bin|"
                    + "|".join(_e(h) for h in _SOLTAS) + "|"
                    + "|".join(r"\b" + _e(h) + r"\b" for h in _INTEIRAS) + "|"
                    + r"\b" + _e(_RONDA_DE_ORIGEM) + r"\d|\bjos[ée]\b",
                    re.I)
# só nos créditos: quem fez
CREDITOS = {"README.md", "README.en.md", "CHANGELOG.md", "LICENCA", "pyproject.toml", "site/index.html"}
PERMITIDO_NOS_CREDITOS = re.compile(_e(_EMPRESA) + "|" + _e("71756164726f73") + r"|\bjos[ée]\b", re.I)
PRIVADO = re.compile(_e(_EMPRESA), re.I)


def _ficheiros():
    for p in PASTAS:
        for f in (RAIZ / p).rglob("*"):
            if f.is_file() and f.suffix in EXTENSOES and not EXCLUIR.intersection(f.relative_to(RAIZ).parts):
                yield f
    for s in SOLTOS:
        if (RAIZ / s).exists():
            yield RAIZ / s


def _credito(f) -> bool:
    return f.name in CREDITOS or str(f.relative_to(RAIZ)) in CREDITOS


def _hits(f):
    try:
        linhas = f.read_text(encoding="utf-8").splitlines()
    except (UnicodeDecodeError, OSError):
        return []
    out = []
    for i, l in enumerate(linhas, 1):
        for m in RASTOS.finditer(l):
            if _credito(f) and PERMITIDO_NOS_CREDITOS.fullmatch(m.group(0)):
                continue
            out.append(f"{f.relative_to(RAIZ)}:{i}: {m.group(0)!r}  ← {l.strip()[:90]}")
        for m in PRIVADO.finditer(l):
            if not _credito(f):
                out.append(f"{f.relative_to(RAIZ)}:{i}: {m.group(0)!r}  ← {l.strip()[:90]}")
    return out


def test_ve_o_pacote_todo():
    """ronda 8 (rasto): a varredura chegou a ver só os 7 ficheiros soltos da raiz e passava. Agora tem de ver o código, a página,
    o service worker, os testes (incluindo este), a documentação e os ficheiros de instalação."""
    vistos = {str(f.relative_to(RAIZ)) for f in _ficheiros()}
    for obrigatorio in ("basalt/web.py", "basalt/static/index.html", "basalt/static/sw.js", "basalt/basalt.toml.exemplo",
                        "testes/test_auth.py", "testes/test_sem_rasto.py", "docs/INSTALAR.md", "docker/Dockerfile",
                        "sistema/basalt.service", "README.md"):
        assert obrigatorio in vistos, f"a varredura não vê {obrigatorio} (viu {len(vistos)} ficheiros)"
    assert len(vistos) > 50, f"a varredura só viu {len(vistos)} ficheiros"


def test_sem_rasto_do_autor():
    hits = [h for f in _ficheiros() for h in _hits(f)]
    assert not hits, "rastos do autor no pacote:\n  " + "\n  ".join(hits)


def test_sem_segredos_nem_chaves():
    rx = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|\d{9,10}:[A-Za-z0-9_-]{35}|bslt_[A-Za-z0-9_-]{30,}")
    hits = [f"{f.relative_to(RAIZ)}:{i}" for f in _ficheiros() for i, l in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
            if rx.search(l) and "exemplo" not in l.lower() and "example" not in l.lower() and "ABCDEFGHIJ" not in l]
    assert not hits, "parece uma chave a sério:\n  " + "\n  ".join(hits)


def test_detecta_rasto():
    """o teste tem de apanhar um caso mau (senão só confirma o que já espero). As sondas montam-se aos bocados, para este
    ficheiro não ser ele próprio um rasto."""
    assert RASTOS.search("ssh root@192.168" + ".1.104") and RASTOS.search("/home/" + "alguem/x")
    assert RASTOS.search("o " + _w("6173747261").capitalize() + " disse") and RASTOS.search("auditoria " + _w(_RONDA_DE_ORIGEM) + "3")
    assert not RASTOS.search("/home/utilizador/basalt")
    assert not RASTOS.search("exemplo.pt") and not RASTOS.search("127.0.0.1") and not RASTOS.search("telegram") and not RASTOS.search("BASALT_CAS_GRACA") and not RASTOS.search("Codex, Cursor")
    # toda a rede privada (10/8, 172.16/12), não só 192.168 e 10.0 — e um projecto de casa pelo nome
    assert RASTOS.search("http://10.8" + ".0.3:8121") and RASTOS.search("172.16" + ".4.2") and RASTOS.search("172.31" + ".255.1")
    assert RASTOS.search("a " + _w("7461736361") + " pediu") and RASTOS.search(_w("6c616365726461").upper())
    assert not RASTOS.search("172.32.0.1") and not RASTOS.search("172.15.0.1") and not RASTOS.search("versão 3.10.2.1") and not RASTOS.search("10.2.1") \
        and not RASTOS.search("desastrado") and not RASTOS.search("tascas") and not RASTOS.search("archive")
    # IPs de documentação (RFC 5737) não são rasto: são os que o pacote usa nos exemplos
    assert not RASTOS.search("203.0.113.5") and not RASTOS.search("198.51.100.0/24")
    # os créditos só valem em quem pode dizer quem fez: o site de apresentação sim, a página da aplicação não
    assert _credito(RAIZ / "site" / "index.html") and _credito(RAIZ / "README.md") and not _credito(RAIZ / "basalt" / "static" / "index.html")
    assert PRIVADO.search("geral@" + _w(_EMPRESA) + ".com")
