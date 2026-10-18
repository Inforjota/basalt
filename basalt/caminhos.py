"""basalt.caminhos — contenção no espaço: nada do que o Basalt lista ou serve segue ligações simbólicas (symlinks).

Os nomes já são filtrados (NOME_RX nas notas, nome_ok nos anexos), por isso pela web não se sai da pasta. Isto cobre o que
chega por outra via — um cofre Obsidian importado com ligações, um anexo posto à mão na pasta, uma nota simbólica na memória
real — e que as listagens por glob e as leituras seguiam sem perguntar, copiando ou servindo ficheiros de fora do espaço.
"""
import errno
import os
import stat
from pathlib import Path


# ───────────────────────── ronda 10 (achado G4 da r3): âncoras de confiança ─────────────────────────
# Antes a «raiz de confiança» era a própria pasta da nota ou dos anexos (`copia/`, `memoria/`): abria-se seguindo ligações, por isso
# `A/copia` → `B/copia` (ou `A` → `B`) passava a verificação e servia as notas de outra conta. Agora a confiança fica só na raiz
# que o administrador configurou — a pasta `dados` do basalt.toml (registada por `config.Espaco.de`) — e numa pasta dada por
# variável de ambiente FORA dela. De lá para baixo (`<espaco>/copia/<nota>`, `<espaco>/memoria/anexos/…`) cada componente abre-se
# com O_NOFOLLOW a partir do descritor do anterior: as listagens, as leituras e a verificação são a mesma operação.
_ANCORAS: set[str] = set()


def definir_ancora(raiz) -> None:
    """regista uma raiz de confiança (normalmente a pasta `dados` do basalt.toml)."""
    if raiz:
        _ANCORAS.add(os.path.abspath(os.fspath(raiz)))


def _ancoras() -> list[str]:
    extra = [os.path.abspath(a) for a in (os.environ.get("BASALT_ANCORA") or "").split(os.pathsep) if a]
    return sorted(_ANCORAS.union(extra), key=len, reverse=True)


def ancora_de(pasta) -> tuple[str, list[str]]:
    """(raiz de confiança, componentes abaixo dela) de `pasta`: a âncora mais longa que a contém; sem nenhuma, a própria pasta."""
    s = os.path.abspath(os.fspath(pasta))
    for a in _ancoras():
        if s == a:
            return a, []
        if s.startswith(a.rstrip(os.sep) + os.sep):
            return a, [x for x in s[len(a.rstrip(os.sep)) + 1:].split(os.sep) if x]
    return s, []


def regular(p) -> bool:
    """um ficheiro normal com esse nome, a que se chega sem passar por NENHUMA ligação simbólica abaixo da âncora (nem no
    próprio nome, nem nas pastas acima dele — ronda 10, achado G4 da r3)."""
    p = Path(p)
    try:
        os.close(abrir(p.parent, [p.name]))
        return True
    except (OSError, ValueError):
        return False


def notas(pasta, padrao="*.md") -> list[Path]:
    """os ficheiros normais de `pasta` que casam com o padrão, por ordem de nome — sem ligações simbólicas, nem nos ficheiros nem
    nas pastas acima deles (até à âncora). Uma pasta que não existe dá lista vazia; uma pasta a que só se chega por uma ligação
    simbólica é recusada (Recusado)."""
    import fnmatch
    return [Path(pasta) / nome for nome, tipo, _st in listar_dir(pasta) if tipo == "f" and fnmatch.fnmatchcase(nome, padrao)]


def ler(p) -> bytes:
    """os bytes do ficheiro `p`, lidos pelo descritor verificado (sem ligações até à âncora) — substitui `Path.read_bytes`."""
    p = Path(p)
    return ler_bytes(p.parent, [p.name])


def pasta_contida(pasta) -> None:
    """recusa (Recusado) uma pasta a que só se chega por uma ligação simbólica abaixo da âncora; não existir não é recusa.
    Para quem ainda escreve por caminho (o CAS troca ficheiros por renameat2): verifica-se antes de cada escrita."""
    try:
        os.close(abrir_pasta(pasta))
    except FileNotFoundError:
        pass


def ler_texto(p) -> str:
    """o texto do ficheiro `p`, lido pelo descritor verificado (sem ligações até à âncora) — substitui `Path.read_text`."""
    p = Path(p)
    return ler_bytes(p.parent, [p.name]).decode("utf-8", errors="replace")


# ───────────────────────── ronda 9 (anexos sem ligações simbólicas): aberturas a partir de uma raiz de confiança ─────────────────────────
# `regular()` só olha para o último componente e depois quem chama abre o caminho outra vez: uma pasta-ancestral que seja uma
# ligação simbólica passava, e trocar o ficheiro por uma ligação entre a verificação e a leitura também. Aqui o caminho percorre-se
# componente a componente a partir de um descritor da raiz (que é de confiança), sem nunca seguir uma ligação: a verificação e a
# abertura são a mesma operação, por isso não há intervalo para trocar nada.

_PASTA = os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW | os.O_CLOEXEC


class Recusado(OSError):
    """um caminho que se recusa abrir: passa por uma ligação simbólica, não é um ficheiro normal, é demasiado grande ou tem um
    componente inválido. É um OSError, por isso quem já apanha OSError (404, «saltada»…) apanha-o sem mais."""


def _recusa(e: OSError, nome) -> OSError:
    """ELOOP (ficheiro) e ENOTDIR (pasta) são o que o núcleo responde a O_NOFOLLOW diante de uma ligação simbólica."""
    if e.errno in (errno.ELOOP, errno.ENOTDIR, errno.EMLINK):
        return Recusado(e.errno, f"«{nome}» é uma ligação simbólica (ou não é uma pasta) — não a sigo", str(nome))
    return e


def _nome_simples(nome) -> str:
    if not isinstance(nome, str) or nome in ("", ".", "..") or "/" in nome or "\0" in nome:
        raise Recusado(errno.EINVAL, "componente de caminho inválido", str(nome))
    return nome


def _partes(rel, vazio_ok: bool = False) -> list[str]:
    """o caminho relativo (texto com `/`, ou lista de componentes) já validado: sem vazio, `.`, `..`, absoluto nem NUL."""
    if isinstance(rel, (list, tuple)):
        partes = [str(p) for p in rel]
    else:
        s = os.fspath(rel)
        if s.startswith("/"):
            raise Recusado(errno.EINVAL, "caminho absoluto", s)
        partes = s.split("/") if s != "" else []
    if not partes and not vazio_ok:
        raise Recusado(errno.EINVAL, "caminho vazio", "")
    for p in partes:
        _nome_simples(p)
    return partes


def abrir_subpasta(dfd: int, nome: str, criar: bool = False) -> int:
    """o descritor da pasta `nome` dentro da pasta `dfd`, sem seguir ligações; `criar` faz a pasta se faltar."""
    _nome_simples(nome)
    try:
        return os.open(nome, _PASTA, dir_fd=dfd)
    except FileNotFoundError:
        if not criar:
            raise
    except OSError as e:
        raise _recusa(e, nome) from e
    try:
        os.mkdir(nome, 0o755, dir_fd=dfd)
    except FileExistsError:
        pass
    try:
        return os.open(nome, _PASTA, dir_fd=dfd)
    except OSError as e:
        raise _recusa(e, nome) from e


def abrir_pasta(raiz, rel="", criar: bool = False) -> int:
    """o descritor da pasta `raiz`/`rel`. Só a ÂNCORA de `raiz` (ver `ancora_de`) é de confiança e pode ter ligações; da âncora
    para baixo — o resto de `raiz` e todo o `rel` — nenhum componente pode ser uma ligação simbólica (ronda 10, achado G4 da r3:
    antes a própria `raiz` abria-se seguindo ligações)."""
    partes = _partes(rel, vazio_ok=True)
    ancora, pre = ancora_de(raiz)
    partes = pre + partes
    if criar:
        os.makedirs(ancora, exist_ok=True)
    fd = os.open(ancora, os.O_RDONLY | os.O_DIRECTORY | os.O_CLOEXEC)
    try:
        for p in partes:
            novo = abrir_subpasta(fd, p, criar)
            os.close(fd)
            fd = novo
    except BaseException:
        os.close(fd)
        raise
    return fd


def abrir_ficheiro(dfd: int, nome: str, flags: int = os.O_RDONLY, modo: int = 0o644) -> int:
    """o descritor do ficheiro `nome` dentro da pasta `dfd`: sem seguir ligações e só se for um ficheiro normal (fstat sobre o
    próprio descritor aberto; uma fila/dispositivo abre sem bloquear e é recusado)."""
    _nome_simples(nome)
    f = flags | os.O_NOFOLLOW | os.O_CLOEXEC
    if not f & os.O_CREAT:
        f |= os.O_NONBLOCK
    try:
        fd = os.open(nome, f, modo, dir_fd=dfd)
    except OSError as e:
        raise _recusa(e, nome) from e
    if not flags & os.O_DIRECTORY:
        try:
            normal = stat.S_ISREG(os.fstat(fd).st_mode)
        except BaseException:
            os.close(fd)
            raise
        if not normal:
            os.close(fd)
            raise Recusado(errno.EINVAL, f"«{nome}» não é um ficheiro normal", nome)
    return fd


def abrir(raiz, rel, flags: int = os.O_RDONLY, modo: int = 0o644) -> int:
    """abre `raiz`/`rel` sem seguir NENHUMA ligação simbólica abaixo da raiz e devolve o descritor (quem chama fecha).
    Recusa (Recusado) `..`, caminhos absolutos, componentes vazios, ligações e o que não for um ficheiro normal. Com O_CREAT
    cria também as pastas que faltem."""
    partes = _partes(rel)
    dfd = abrir_pasta(raiz, partes[:-1], criar=bool(flags & os.O_CREAT))
    try:
        return abrir_ficheiro(dfd, partes[-1], flags, modo)
    finally:
        os.close(dfd)


def ler_bytes(raiz, rel, limite: int | None = None) -> bytes:
    """o conteúdo de `raiz`/`rel`, lido do descritor que foi verificado ao abrir. Com `limite`, recusa (EFBIG) o que o passe."""
    fd = abrir(raiz, rel)
    try:
        f = os.fdopen(fd, "rb")
    except BaseException:
        os.close(fd)
        raise
    with f:
        if limite is None:
            return f.read()
        if os.fstat(f.fileno()).st_size > limite:
            raise Recusado(errno.EFBIG, "ficheiro demasiado grande", str(rel))
        dados = f.read(limite + 1)
        if len(dados) > limite:
            raise Recusado(errno.EFBIG, "ficheiro demasiado grande", str(rel))
        return dados


def entradas(dfd: int) -> list[tuple[str, str, os.stat_result]]:
    """o que há na pasta `dfd`, por ordem de nome: (nome, tipo, stat sem seguir ligações). Tipo: «f» ficheiro normal,
    «d» pasta, «l» ligação simbólica, «o» outra coisa (fila, dispositivo…)."""
    out = []
    with os.scandir(dfd) as it:
        for e in it:
            try:
                st = e.stat(follow_symlinks=False)
            except OSError:                                   # desapareceu entretanto
                continue
            m = st.st_mode
            tipo = "l" if stat.S_ISLNK(m) else "d" if stat.S_ISDIR(m) else "f" if stat.S_ISREG(m) else "o"
            out.append((e.name, tipo, st))
    return sorted(out, key=lambda x: x[0])


def listar_dir(raiz, rel="") -> list[tuple[str, str, os.stat_result]]:
    """`entradas` da pasta `raiz`/`rel` (sem seguir ligações no caminho); lista vazia se a pasta não existir."""
    try:
        fd = abrir_pasta(raiz, rel)
    except FileNotFoundError:
        return []
    try:
        return entradas(fd)
    finally:
        os.close(fd)


def percorrer(raiz, ignorar=None):
    """percorre a árvore de `raiz` a pé, SEM seguir ligações simbólicas, e gera (caminho relativo com `/`, tipo, stat) pela ordem
    de `sorted(Path.rglob)`: uma pasta antes do que tem dentro. `ignorar(nome, tipo)` verdadeiro salta a entrada (e, se for uma
    pasta, tudo o que tem dentro). Uma pasta trocada por uma ligação depois de listada gera-se como «l»."""
    def desce(dfd, prefixo):
        for nome, tipo, st in entradas(dfd):
            if ignorar is not None and ignorar(nome, tipo):
                continue
            rel = prefixo + nome
            yield rel, tipo, st
            if tipo != "d":
                continue
            try:
                sub = abrir_subpasta(dfd, nome)
            except FileNotFoundError:
                continue
            except OSError:
                yield rel, "l", st
                continue
            try:
                yield from desce(sub, rel + "/")
            finally:
                os.close(sub)
    fd = abrir_pasta(raiz)
    try:
        yield from desce(fd, "")
    finally:
        os.close(fd)
