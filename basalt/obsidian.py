"""basalt.obsidian — trazer um cofre Obsidian (ou qualquer pasta de Markdown) para um espaço, e levá-lo de volta.

A memória do Basalt JÁ é um cofre Obsidian (Markdown numa pasta, [[ligações]]). A importação só faz o que falta:
  · dá a cada nota um nome de ficheiro que o Basalt aceita (minúsculas, sem acentos, `_`) e reescreve as [[ligações]]
    para esses nomes — o título original fica como texto da ligação ([[nome|Título original]]);
  · põe o cabeçalho (name/description/type) nas notas que não o têm;
  · copia os anexos referenciados (![[foto.png]]) para a pasta de anexos do espaço;
  · NUNCA sobrepõe uma nota que já exista no espaço (fica em «saltadas»).
A exportação copia a memória (sem .git) para uma pasta — abre-se no Obsidian tal como está.
"""
from __future__ import annotations

import re
import shutil
import subprocess
import unicodedata
from pathlib import Path

from . import anexos as anexos_mod
from . import cas, config
from .mcp import com_frontmatter
from .segredos import tem_segredo_texto

LIGACAO_RX = re.compile(r"(!?)\[\[([^\]|#]+)(#[^\]|]*)?(\|[^\]]*)?\]\]")
IGNORAR = {".obsidian", ".trash", ".git", ".smart-env"}
ANEXOS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".mp3", ".m4a", ".wav", ".mp4", ".webm", ".csv", ".json", ".txt"}


def slug(titulo: str) -> str:
    s = "".join(c for c in unicodedata.normalize("NFD", (titulo or "").lower()) if unicodedata.category(c) != "Mn")
    s = re.sub(r"[^a-z0-9]+", "_", s).strip("_")[:100]
    return s if len(s) >= 2 else ("nota_" + s if s else "nota")


def _descricao(texto: str) -> str:
    for l in texto.splitlines():
        l = l.strip().lstrip("#").strip()
        if l and not l.startswith("---") and not l.startswith("!["):
            return l[:120]
    return ""


def _com_nome(nome: str, texto: str) -> str:
    """cabeçalho completo nas notas sem ele; nas que já têm `---`, garante o `name:`."""
    if not texto.startswith("---"):
        return com_frontmatter(nome, texto, _descricao(texto))
    partes = texto.split("\n---", 1)
    cab = partes[0]
    if re.search(r"(?m)^name:", cab):
        return texto
    return cab.rstrip("\n") + f"\nname: {nome}" + "\n---" + (partes[1] if len(partes) > 1 else "\n")


def _ficheiros(origem: Path):
    for f in sorted(origem.rglob("*")):
        rel = f.relative_to(origem).parts
        if any(p in IGNORAR or p.startswith(".") for p in rel):
            continue
        if f.is_file() and not f.is_symlink():       # ronda 8 (ligações simbólicas): uma ligação simbólica no cofre não se segue (podia apontar para fora)
            yield f


def importar(origem, esp=None, simular: bool = False) -> dict:
    """devolve {importadas: [nome…], saltadas: [(ficheiro, porquê)…], anexos: [nome…], nomes: {título: nome}}."""
    origem = Path(origem).expanduser().resolve()
    esp = esp or config.actual()
    if not origem.is_dir():
        raise FileNotFoundError(f"não há a pasta {origem}")
    todos = list(_ficheiros(origem))
    ligadas = [str(f.relative_to(origem)) for f in sorted(origem.rglob("*.md")) if f.is_symlink()]
    mds = [f for f in todos if f.suffix.lower() == ".md"]
    disponiveis = {f.name: f for f in todos if f.suffix.lower() in ANEXOS}
    pasta_anexos = Path(getattr(esp, "anexos", esp.real / "anexos"))
    # 1) título → nome do Basalt (sem colisões entre as notas importadas; o Obsidian liga pelo nome do ficheiro, não pela pasta)
    nome_de, por_titulo, usados = {}, {}, set()
    for f in mds:
        base = slug(f.stem)
        n, i = base, 1
        while n in usados:
            i += 1
            n = f"{base[:96]}_{i}"
        usados.add(n)
        nome_de[f] = n
        por_titulo.setdefault(f.stem.strip().lower(), n)

    def ligacao(m):
        emb, alvo, _anc, alias = m.groups()
        if emb:                                     # ronda 8 (importar anexos): ![[imagens/foto.png]] → ![[foto.png]] (o anexo vai para a pasta do espaço, sem subpastas)
            t = alvo.strip()
            if "/" in t and Path(t).name in disponiveis:
                return f"![[{Path(t).name}{_anc or ''}{alias or ''}]]"
            return m.group(0)
        t = alvo.strip()
        n = por_titulo.get(t.lower()) or por_titulo.get(Path(t).stem.lower())
        if not n:
            return m.group(0)                       # liga a uma nota que não vem: fica como está (aparece «por criar»)
        return f"[[{n}{alias or ('' if n == t else '|' + t)}]]"

    r = {"importadas": [], "saltadas": [(l, "é uma ligação simbólica — não a sigo") for l in ligadas], "anexos": [],
         "nomes": {f.stem: n for f, n in nome_de.items()}}
    anexos_pedidos = set()
    for f in mds:
        n = nome_de[f]
        destino = esp.real / f"{n}.md"
        if destino.exists():
            r["saltadas"].append((str(f.relative_to(origem)), f"já existe «{n}» no espaço"))
            continue
        try:
            texto = f.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            r["saltadas"].append((str(f.relative_to(origem)), "não é UTF-8"))
            continue
        if tem_segredo_texto(texto):
            r["saltadas"].append((str(f.relative_to(origem)), "tem o que parece uma password ou chave em claro"))
            continue
        texto = LIGACAO_RX.sub(ligacao, texto)
        anexos_pedidos.update(a.strip() for _, a in re.findall(r"(!)\[\[([^\]|#]+)", texto))
        texto = _com_nome(n, texto)
        if not simular:
            motivo = cas.escrever(destino, texto.encode("utf-8"), None, "importar")    # ronda 8 (escrita sem sobrepor): só cria; nunca sobrepõe o que apareceu entretanto
            if motivo:
                r["saltadas"].append((str(f.relative_to(origem)), f"já existe «{n}» no espaço ({motivo})"))
                continue
        r["importadas"].append(n)
    for a in sorted(anexos_pedidos):
        f = disponiveis.get(Path(a).name)
        if not f:
            r["saltadas"].append((a, "anexo referenciado mas não encontrado"))
            continue
        try:
            tam = f.stat().st_size
        except OSError:
            tam = 0
        if tam > anexos_mod.TAMANHO_MAX:                  # ronda 8 (importar anexos): o mesmo limite que a página (antes a importação passava por cima)
            r["saltadas"].append((a, "anexo demasiado grande (máximo %d MB)" % (anexos_mod.TAMANHO_MAX // 1024 // 1024)))
            continue
        ja = pasta_anexos / f.name
        if ja.exists():                                   # ronda 8 (importar anexos): já há um com este nome — só «importado» se for o MESMO conteúdo
            try:
                igual = ja.read_bytes() == f.read_bytes()
            except OSError:
                igual = False
            if not igual:
                r["saltadas"].append((a, f"já existe um anexo «{f.name}» diferente no espaço — não o substituí"))
                continue
        elif not simular:
            pasta_anexos.mkdir(parents=True, exist_ok=True)
            shutil.copy2(f, ja)
        r["anexos"].append(f.name)
    if not simular and r["importadas"] and (esp.real / ".git").is_dir():
        try:
            subprocess.run(["git", "-C", str(esp.real), *config.git_c(), "add", "-A"], capture_output=True, timeout=60)
            subprocess.run(["git", "-C", str(esp.real), *config.git_c(), "commit", "-q", "-m",
                            f"basalt: importação de {len(r['importadas'])} notas ({origem.name})"], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    return r


def exportar(destino, esp=None) -> int:
    """copia a memória (notas + anexos, sem .git) para `destino`; devolve quantas notas foram."""
    esp = esp or config.actual()
    destino = Path(destino).expanduser().resolve()
    if destino.exists() and any(destino.iterdir()):
        raise FileExistsError(f"{destino} não está vazia")
    def _ignorar(pasta, nomes):     # ronda 8 (ligações simbólicas): sem .git, sem temporários e sem seguir ligações simbólicas (iam copiar ficheiros de fora)
        return [n for n in nomes if n == ".git" or (n.startswith(".") and n.endswith(".tmp")) or (Path(pasta) / n).is_symlink()]
    shutil.copytree(esp.real, destino, ignore=_ignorar, dirs_exist_ok=True)
    return len(list(destino.glob("*.md")))
