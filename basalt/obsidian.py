"""basalt.obsidian — trazer um cofre Obsidian (ou qualquer pasta de Markdown) para um espaço, e levá-lo de volta.

A memória do Basalt JÁ é um cofre Obsidian (Markdown numa pasta, [[ligações]]). A importação só faz o que falta:
  · dá a cada nota um nome de ficheiro que o Basalt aceita (minúsculas, sem acentos, `_`) e reescreve as [[ligações]]
    para esses nomes — o título original fica como texto da ligação ([[nome|Título original]]);
  · põe o cabeçalho (name/description/type) nas notas que não o têm;
  · guarda os anexos referenciados (![[foto.png]]) na pasta de anexos do espaço, pelo mesmo caminho da página (`anexos.guardar`:
    detector de segredos, nunca sobrepõe), ANTES das notas que os referem;
  · NUNCA sobrepõe uma nota que já exista no espaço (fica em «saltadas»).
Nada se segue por ligações simbólicas — nem ficheiros nem pastas do cofre, nem na exportação.
A exportação copia a memória (sem .git) para uma pasta — abre-se no Obsidian tal como está.
"""
from __future__ import annotations

import errno
import os
import re
import shutil
import stat
import subprocess
import unicodedata
from pathlib import Path, PurePosixPath

from . import anexos as anexos_mod
from . import caminhos, cas, config
from .mcp import com_frontmatter
from .segredos import SegredoEmClaro, VerificacaoFalhou, tem_segredo_texto, verificar

LIGACAO_RX = re.compile(r"(!?)\[\[([^\]|#]+)(#[^\]|]*)?(\|[^\]]*)?\]\]")
IGNORAR = {".obsidian", ".trash", ".git", ".smart-env"}
ANEXOS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".mp3", ".m4a", ".wav", ".mp4", ".webm", ".csv", ".json", ".txt"}
NAO_VERIFICADO = "não consegui verificar com o [segredos] verificador configurado — não importei (tenta outra vez)"


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


def _ignorar(nome: str, tipo: str) -> bool:
    return nome in IGNORAR or nome.startswith(".")


def _inventario(origem: Path):
    """(notas, anexos disponíveis, ligações simbólicas) do cofre, pelo caminho relativo com `/`. ronda 9 (anexos sem ligações
    simbólicas): percorre-se a pé, sem seguir NENHUMA ligação — nem de ficheiro nem de pasta (o `rglob` listava a ligação a uma pasta
    e deixava o resto ao `is_file`)."""
    mds, disp, ligadas = [], {}, []
    for rel, tipo, st in caminhos.percorrer(origem, _ignorar):
        suf = PurePosixPath(rel).suffix.lower()
        if tipo == "l":
            if suf == ".md" or suf in ANEXOS or (origem / rel).is_dir():
                ligadas.append(rel)
        elif tipo == "f":
            if suf == ".md":
                mds.append(rel)
            elif suf in ANEXOS:
                disp[rel] = st
    return mds, disp, sorted(set(ligadas))


def _resolver(alvo: str, pasta_nota: str, disp: dict, por_base: dict):
    """(caminho relativo do anexo, None) ou (None, motivo). ronda 9 (anexos de pastas diferentes): os anexos são identificados pelo
    caminho, não pelo nome — `a/foto.png` e `b/foto.png` são ficheiros diferentes. Um caminho explícito é esse ficheiro; um nome
    sozinho é o único ficheiro com esse nome, ou (havendo vários) o que está na pasta da própria nota; senão é ambíguo."""
    t = alvo.strip()
    if "/" in t:
        if t in disp:
            return t, None
        local = f"{pasta_nota}/{t}" if pasta_nota else ""
        if local in disp:
            return local, None
        fim = [r for r in disp if r.endswith("/" + t)]
        if len(fim) == 1:
            return fim[0], None
        if fim:
            return None, f"anexo ambíguo: há {len(fim)} ficheiros que acabam em «{t}» ({', '.join(sorted(fim)[:4])}…)"
        return None, "anexo referenciado mas não encontrado"
    cs = por_base.get(t, [])
    if len(cs) == 1:
        return cs[0], None
    if not cs:
        return None, "anexo referenciado mas não encontrado"
    local = f"{pasta_nota}/{t}" if pasta_nota else t
    if local in cs:
        return local, None
    return None, f"anexo ambíguo: há {len(cs)} ficheiros «{t}» ({', '.join(sorted(cs)[:4])}) e a nota não está em nenhuma dessas pastas"


def _nome_seguro(nome: str):
    """o nome do ficheiro tal como está, se a página o aceita; senão um derivado (slug do nome + sufixo em minúsculas); None se nem assim."""
    if anexos_mod.nome_ok(nome) or anexos_mod.nome_com_segredo(nome):   # ronda 10 (G7 da r3): um nome com chave chega ao guarda tal e qual (e é recusado)
        return nome
    p = PurePosixPath(nome)
    cand = slug(p.stem) + p.suffix.lower()
    return cand if anexos_mod.nome_ok(cand) else None


def importar(origem, esp=None, simular: bool = False) -> dict:
    """devolve {importadas: [nome…], saltadas: [(ficheiro, porquê)…], anexos: [nome…], nomes: {título: nome}}.
    Ordem (ronda 9): 1) lê e prepara as notas, sem escrever; 2) guarda os anexos que elas referem; 3) escreve as notas, já com os
    nomes finais dos anexos, validando o texto FINAL (depois de reescrever ligações e pôr o cabeçalho). `simular` faz tudo menos escrever."""
    origem = Path(origem).expanduser().resolve()
    esp = esp or config.actual()
    if not origem.is_dir():
        raise FileNotFoundError(f"não há a pasta {origem}")
    mds, disp, ligadas = _inventario(origem)
    por_base = {}
    for rel in sorted(disp):
        por_base.setdefault(PurePosixPath(rel).name, []).append(rel)
    # 1) título → nome do Basalt (sem colisões entre as notas importadas; o Obsidian liga pelo nome do ficheiro, não pela pasta)
    nome_de, por_titulo, usados = {}, {}, set()
    for rel in mds:
        stem = PurePosixPath(rel).stem
        base = slug(stem)
        n, i = base, 1
        while n in usados:
            i += 1
            n = f"{base[:96]}_{i}"
        usados.add(n)
        nome_de[rel] = n
        por_titulo.setdefault(stem.strip().lower(), n)

    def final(n, texto, pasta_nota, mapa=None):
        """o texto como fica gravado: ligações entre notas reescritas, anexos com o nome final (`mapa`: origem → nome) e o cabeçalho."""
        def ligacao(m):
            emb, alvo, anc, alias = m.groups()
            t = alvo.strip()
            if emb:                                 # ![[imagens/foto.png]] → ![[foto.png]]: o anexo vai para a pasta do espaço, sem subpastas
                if mapa is not None and PurePosixPath(t).suffix.lower() in ANEXOS:
                    rel, _ = _resolver(t, pasta_nota, disp, por_base)
                    if rel in mapa and mapa[rel] != t:
                        return f"![[{mapa[rel]}{anc or ''}{alias or ''}]]"
                return m.group(0)
            nn = por_titulo.get(t.lower()) or por_titulo.get(Path(t).stem.lower())
            if not nn:
                return m.group(0)                   # liga a uma nota que não vem: fica como está (aparece «por criar»)
            return f"[[{nn}{alias or ('' if nn == t else '|' + t)}]]"
        return _com_nome(n, LIGACAO_RX.sub(ligacao, texto))

    r = {"importadas": [], "saltadas": [(l, "é uma ligação simbólica — não a sigo") for l in ligadas], "anexos": [],
         "nomes": {PurePosixPath(rel).stem: n for rel, n in nome_de.items()}}
    # 2) as notas que entram (nada se escreve ainda) e os anexos que elas pedem
    preparadas, pedidos, problemas = [], set(), set()
    for rel in mds:
        n = nome_de[rel]
        if (esp.real / f"{n}.md").exists():
            r["saltadas"].append((rel, f"já existe «{n}» no espaço"))
            continue
        try:
            bruto = caminhos.ler_bytes(origem, rel)     # lido pelo descritor verificado, não por um caminho que se abre outra vez
        except OSError:
            r["saltadas"].append((rel, "é uma ligação simbólica (ou foi trocada por uma) — não a sigo"))
            continue
        try:
            texto = bruto.decode("utf-8")
        except UnicodeDecodeError:
            r["saltadas"].append((rel, "não é UTF-8"))
            continue
        pasta_nota = str(PurePosixPath(rel).parent)
        pasta_nota = "" if pasta_nota == "." else pasta_nota
        if tem_segredo_texto(texto) or tem_segredo_texto(final(n, texto, pasta_nota)):    # o nome da nota também vai para o cabeçalho
            r["saltadas"].append((rel, "tem o que parece uma password ou chave em claro"))
            continue
        for m in LIGACAO_RX.finditer(texto):
            alvo = m.group(2).strip()
            if m.group(1) and PurePosixPath(alvo).suffix.lower() in ANEXOS:
                origem_rel, porque = _resolver(alvo, pasta_nota, disp, por_base)
                if origem_rel:
                    pedidos.add(origem_rel)
                else:
                    problemas.add((alvo, porque))
        preparadas.append((rel, n, texto, pasta_nota))
    # 3) os anexos, pelo caminho da página (segredos, tamanho, nunca sobrepor); cada ficheiro de origem uma só vez
    guardados, virtual, avisos = {}, {}, list(problemas)
    for rel in sorted(pedidos):
        nome_fonte = PurePosixPath(rel).name
        nome = _nome_seguro(nome_fonte)
        if nome is None:
            avisos.append((rel, f"o nome «{nome_fonte}» não é aceite e não consegui derivar outro"))
            continue
        if disp[rel].st_size > anexos_mod.TAMANHO_MAX:     # o mesmo limite que a página
            avisos.append((rel, "anexo demasiado grande (máximo %d MB)" % (anexos_mod.TAMANHO_MAX // 1024 // 1024)))
            continue
        try:
            dados = caminhos.ler_bytes(origem, rel, anexos_mod.TAMANHO_MAX)
        except OSError as e:
            avisos.append((rel, "anexo demasiado grande (máximo %d MB)" % (anexos_mod.TAMANHO_MAX // 1024 // 1024)
                           if e.errno == errno.EFBIG else "é uma ligação simbólica (ou foi trocada por uma) — não a sigo"
                           if isinstance(e, caminhos.Recusado) else f"não consegui ler o anexo ({e.strerror or e})"))
            continue
        try:
            guardado = anexos_mod.prever(esp, nome, dados, virtual) if simular else anexos_mod.guardar(esp, nome, dados)
        except VerificacaoFalhou:
            avisos.append((rel, NAO_VERIFICADO))
            continue
        except SegredoEmClaro:
            avisos.append((rel, "tem o que parece uma password ou chave em claro"))
            continue
        except (ValueError, OSError) as e:
            avisos.append((rel, str(e)))
            continue
        guardados[rel] = guardado
        if guardado not in r["anexos"]:
            r["anexos"].append(guardado)
    r["saltadas"].extend(sorted(avisos))
    # 4) as notas, com o nome final dos anexos; o que se valida é o que se vai gravar
    finais = [final(n, texto, pasta_nota, guardados) for _, n, texto, pasta_nota in preparadas]
    try:
        com_segredo = verificar(finais)                    # ronda 13: o guarda + o [segredos] verificador, todas em lote
    except VerificacaoFalhou:
        com_segredo = None                                 # não se sabe → nenhuma entra (falha fechada)
    for i, (rel, n, texto, pasta_nota) in enumerate(preparadas):
        destino = esp.real / f"{n}.md"
        texto_final = finais[i]
        if com_segredo is None:
            r["saltadas"].append((rel, NAO_VERIFICADO))
            continue
        if com_segredo[i]:                                 # ronda 9: validar DEPOIS da última transformação, não antes
            r["saltadas"].append((rel, "tem o que parece uma password ou chave em claro (no texto já reescrito)"))
            continue
        if not simular:
            motivo = cas.escrever(destino, texto_final.encode("utf-8"), None, "importar")    # só cria; nunca sobrepõe o que apareceu entretanto
            if motivo:
                r["saltadas"].append((rel, f"já existe «{n}» no espaço ({motivo})"))
                continue
        r["importadas"].append(n)
    if not simular and r["importadas"] and (esp.real / ".git").is_dir():
        try:
            subprocess.run(["git", "-C", str(esp.real), *config.git_c(), "add", "-A"], capture_output=True, timeout=60)
            subprocess.run(["git", "-C", str(esp.real), *config.git_c(), "commit", "-q", "-m",
                            f"basalt: importação de {len(r['importadas'])} notas ({origem.name})"], capture_output=True, timeout=60)
        except (OSError, subprocess.SubprocessError):
            pass
    return r


def _copiar_pasta(dfd: int, destino: Path) -> None:
    """copia o conteúdo da pasta aberta `dfd` para `destino`, descendo por descritores e sem seguir ligações simbólicas: o que se
    copia é o que se abriu (O_NOFOLLOW + ficheiro normal), por isso trocar um ficheiro por uma ligação a meio não faz copiar o alvo."""
    for nome, tipo, st in caminhos.entradas(dfd):
        if nome == ".git" or (nome.startswith(".") and nome.endswith(".tmp")) or tipo not in ("f", "d"):
            continue                                    # sem .git, sem temporários, sem ligações nem ficheiros especiais
        alvo = destino / nome
        if tipo == "d":
            try:
                sub = caminhos.abrir_subpasta(dfd, nome)
            except OSError:                             # trocada por uma ligação (ou desapareceu) depois de listada
                continue
            try:
                alvo.mkdir(exist_ok=True)
                _copiar_pasta(sub, alvo)
            finally:
                os.close(sub)
            continue
        try:
            fd = caminhos.abrir_ficheiro(dfd, nome)
        except OSError:
            continue
        with os.fdopen(fd, "rb") as origem, open(alvo, "xb") as saida:
            shutil.copyfileobj(origem, saida)
            fst = os.fstat(origem.fileno())
        os.utime(alvo, ns=(fst.st_atime_ns, fst.st_mtime_ns))
        os.chmod(alvo, stat.S_IMODE(fst.st_mode))


def exportar(destino, esp=None) -> int:
    """copia a memória (notas + anexos, sem .git) para `destino`; devolve quantas notas foram."""
    esp = esp or config.actual()
    destino = Path(destino).expanduser().resolve()
    if destino.exists() and any(destino.iterdir()):
        raise FileExistsError(f"{destino} não está vazia")
    destino.mkdir(parents=True, exist_ok=True)
    fd = caminhos.abrir_pasta(esp.real)                 # ronda 9 (anexos sem ligações simbólicas): a memória percorre-se por descritores
    try:
        _copiar_pasta(fd, destino)
    finally:
        os.close(fd)
    return len(list(destino.glob("*.md")))
