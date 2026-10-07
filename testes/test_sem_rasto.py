"""Fase D, princípio 3 do DESENHO: NADA do autor nem da sua infra dentro do pacote — nem IPs privados, nem nomes de máquinas,
nem caminhos de casa, nem ferramentas internas. Falha com ficheiro:linha para se corrigir à mão.
README/LICENCA/CHANGELOG podem dizer quem fez (nome e empresa); mais nada pode."""
import re
from pathlib import Path

RAIZ = Path(__file__).resolve().parent.parent
PASTAS = ("basalt", "static", "testes", "docker", "sistema", "docs", "site", "editor-src")
SOLTOS = ("basalt.toml.exemplo", "README.md", "README.en.md", "CHANGELOG.md", "LICENCA", "DESENHO.md", "pyproject.toml", "pytest.ini")
# o que denuncia a origem: rede privada, máquinas, caminhos, projectos e ferramentas de casa
RASTOS = re.compile(r"(?<![\d.])(?:192\.168|10\.\d{1,3}|172\.(?:1[6-9]|2\d|3[01]))\.\d{1,3}\.\d{1,3}(?![\d.])|"   # toda a rede privada (RFC 1918)
                    r"/home/(?!utilizador/|exemplo/)[a-z]+/|~/\.local/bin|lacerda|calheta|proxmox|legion|synology|"
                    r"hermes|\bhive\b|\bfarol\b|\bastra\b|\bt41r\d|\btasca\b|whatsapp|"
                    r"basalt-web|basalt_web\.py|basalt-2vias|basalt-sig|basalt_cas\.py|joseq|quadros|\bjos[ée]\b|josequadros",
                    re.I)
# só nos créditos: quem fez
CREDITOS = {"README.md", "README.en.md", "CHANGELOG.md", "LICENCA", "pyproject.toml", "site/index.html"}
PERMITIDO_NOS_CREDITOS = re.compile(r"inforjota|quadros|\bjos[ée]\b", re.I)
PRIVADO = re.compile(r"inforjota", re.I)


def _ficheiros():
    for p in PASTAS:
        for f in (RAIZ / p).rglob("*"):
            if f.is_file() and f.suffix in {".py", ".html", ".js", ".css", ".md", ".toml", ".yml", ".yaml", ".service", ".timer", ".json", ".txt",
                                             ".webmanifest", ".sh", ".ini", ".cfg", ".env", ""} \
                    and "/.worktrees/" not in str(f) and "/node_modules/" not in str(f) and f.name != "test_sem_rasto.py":
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


def test_sem_rasto_do_autor():
    hits = [h for f in _ficheiros() for h in _hits(f)]
    assert not hits, "rastos do autor no pacote:\n  " + "\n  ".join(hits)


def test_sem_segredos_nem_chaves():
    rx = re.compile(r"sk-ant-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{32,}|ghp_[A-Za-z0-9]{30,}|AKIA[0-9A-Z]{16}|\d{9,10}:[A-Za-z0-9_-]{35}|bslt_[A-Za-z0-9_-]{30,}")
    hits = [f"{f.relative_to(RAIZ)}:{i}" for f in _ficheiros() for i, l in enumerate(f.read_text(encoding="utf-8", errors="replace").splitlines(), 1)
            if rx.search(l) and "exemplo" not in l.lower() and "example" not in l.lower() and "ABCDEFGHIJ" not in l]
    assert not hits, "parece uma chave a sério:\n  " + "\n  ".join(hits)


def test_detecta_rasto():
    """o teste tem de apanhar um caso mau (senão só confirma o que já espero)."""
    assert RASTOS.search("ssh root@192.168.1.104") and RASTOS.search("/home/alguem/x") and RASTOS.search("o Astra disse") and RASTOS.search("auditoria t41r3")
    assert not RASTOS.search("/home/utilizador/basalt") and RASTOS.search("/home/alguem/x")
    assert not RASTOS.search("exemplo.pt") and not RASTOS.search("127.0.0.1") and not RASTOS.search("telegram") and not RASTOS.search("BASALT_CAS_GRACA") and not RASTOS.search("Codex, Cursor")
    # toda a rede privada (10/8, 172.16/12), não só 192.168 e 10.0 — e um projecto de casa pelo nome
    assert RASTOS.search("http://10.8.0.3:8121") and RASTOS.search("172.16.4.2") and RASTOS.search("172.31.255.1") and RASTOS.search("a tasca pediu")
    assert not RASTOS.search("172.32.0.1") and not RASTOS.search("172.15.0.1") and not RASTOS.search("versão 3.10.2.1") and not RASTOS.search("10.2.1") \
        and not RASTOS.search("desastrado") and not RASTOS.search("tascas")
    # os créditos só valem em quem pode dizer quem fez: o site de apresentação sim, a página da aplicação não
    assert _credito(RAIZ / "site" / "index.html") and _credito(RAIZ / "README.md") and not _credito(RAIZ / "basalt" / "static" / "index.html")
