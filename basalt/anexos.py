"""basalt.anexos — os ficheiros anexos de um espaço (imagens, PDF, áudio…) que as notas referem com ![[nome.ext]].

Ficam em `Espaco.anexos` (<espaço>/anexos/), planos: sem subpastas, sem `..`, sufixos conhecidos, tamanho limitado.
Independente do servidor HTTP: `ler` devolve (estado, cabeçalhos, corpo) e quem serve envia; `guardar` nunca sobrepõe
(o mesmo conteúdo reutiliza o nome; conteúdo diferente com o mesmo nome fica `nome_2.ext`).
"""
from __future__ import annotations

import errno
import mimetypes
import os
import re
import secrets
from pathlib import Path

from . import caminhos
from .segredos import SEGREDO_RX, SegredoEmClaro, tem_segredo_nota, tem_segredo_texto

NOME_RX = re.compile(r"[A-Za-z0-9\u00C0-\u024F][A-Za-z0-9\u00C0-\u024F._ \-]{0,118}[A-Za-z0-9]")
SUFIXOS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg", ".pdf", ".mp3", ".m4a", ".wav", ".ogg", ".mp4", ".webm",
           ".csv", ".json", ".txt"}
IMAGENS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".svg"}
TAMANHO_MAX = 25 * 1024 * 1024
_EMBED_RX = re.compile(r"!\[\[([^\]|#]+)(?:[|#][^\]]*)?\]\]")
_TEXTO = "text/plain; charset=utf-8"
TEXTO = {".txt", ".csv", ".json", ".svg"}      # anexos de texto: guarda inteiro (chaves E passwords escritas à mão)


def nome_ok(nome: str) -> bool:
    # a regex não deixa passar `/` nem `\\`: não há como sair da pasta (os testes têm um mutante para isto)
    # ronda 10 (achado G7 da r3): um nome com o que parece uma chave ou password também não serve — nem para gravar, nem para
    # listar, nem para servir (um anexo assim posto à mão na pasta fica de fora, como uma nota retida)
    return (bool(nome) and bool(NOME_RX.fullmatch(nome)) and Path(nome).suffix.lower() in SUFIXOS
            and not nome_com_segredo(nome))


def nome_com_segredo(nome: str) -> bool:
    return bool(SEGREDO_RX.search(nome or "")) or tem_segredo_texto(nome or "")


def pasta(esp) -> Path:
    return Path(getattr(esp, "anexos", None) or (esp.real / "anexos"))


def _raiz(esp):
    """(raiz de confiança, componentes abaixo dela até à pasta dos anexos). ronda 9 (anexos sem ligações simbólicas): a raiz é
    a memória do espaço e `anexos` conta como componente a verificar (uma pasta `anexos` que seja uma ligação não se segue);
    se a pasta estiver noutro sítio, é ela própria a raiz."""
    d = pasta(esp)
    try:
        return Path(esp.real), Path(d).relative_to(Path(esp.real)).parts
    except (AttributeError, ValueError):
        return d, ()


def tipo_de(nome: str) -> str:
    t = mimetypes.guess_type(nome)[0] or "application/octet-stream"
    return t + ("; charset=utf-8" if t.startswith("text/") or t == "application/json" else "")


# ronda 8: revalida sempre (o servidor responde 304 se não mudou) — com max-age o browser servia o anexo de outra conta
CACHE = "private, no-cache"


def ler(esp, nome: str):
    """(estado, cabeçalhos, corpo). 400 nome inválido · 404 não há · 200 com o tipo pelo sufixo (nunca pelo conteúdo)."""
    if not nome_ok(nome):
        return 400, {"Content-Type": _TEXTO}, "nome de anexo inválido".encode("utf-8")
    raiz, pre = _raiz(esp)
    try:
        # ronda 9 (anexos sem ligações simbólicas): verificar e abrir são uma só operação, sem seguir ligações em NENHUM componente
        # (a pasta `anexos` incluída) — antes só se olhava para o último e depois abria-se o caminho outra vez
        dados = caminhos.ler_bytes(raiz, [*pre, nome])
    except OSError:                                      # não há · é uma ligação · não é um ficheiro normal · sem permissão
        return 404, {"Content-Type": _TEXTO}, "anexo não encontrado".encode("utf-8")
    tipo = tipo_de(nome)
    cab = {"Content-Type": tipo, "Content-Length": str(len(dados)), "Cache-Control": CACHE,
           "X-Content-Type-Options": "nosniff"}
    if tipo.startswith("image/svg"):          # um SVG aberto directamente é um documento: sem scripts nem pedidos para fora
        cab["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return 200, cab, dados


def _validar(nome: str, dados: bytes) -> str:
    """o nome limpo, ou ValueError / SegredoEmClaro — o mesmo critério para gravar e para prever."""
    nome = (nome or "").strip()
    if nome_com_segredo(nome):              # ronda 10 (achado G7 da r3): antes só se olhava para os bytes
        raise SegredoEmClaro()
    if not nome_ok(nome):
        raise ValueError("nome de anexo inválido (letras, números, ponto, espaço, - e _; sufixo conhecido)")
    if len(dados) > TAMANHO_MAX:
        raise ValueError("anexo demasiado grande (máximo %d MB)" % (TAMANHO_MAX // 1024 // 1024))
    txt = dados.decode("utf-8", errors="replace")
    # ronda 13: um anexo de texto é lido como uma nota → também passa pelo [segredos] verificador (VerificacaoFalhou se falhar)
    if SEGREDO_RX.search(txt) or (Path(nome).suffix.lower() in TEXTO and tem_segredo_nota(txt)):
        raise SegredoEmClaro()
    return nome


def _igual(dfd: int, nome: str, dados: bytes) -> bool:
    """o ficheiro `nome` da pasta `dfd` existe, é normal e tem exactamente estes bytes."""
    try:
        fd = caminhos.abrir_ficheiro(dfd, nome)
    except OSError:
        return False
    with os.fdopen(fd, "rb") as f:
        return os.fstat(f.fileno()).st_size == len(dados) and f.read(len(dados) + 1) == dados


# erros de os.link em sistemas de ficheiros sem ligações duras (alguns volumes de rede): grava-se então directamente com O_EXCL
_SEM_LIGACAO = {errno.EPERM, errno.EOPNOTSUPP, errno.ENOTSUP, errno.EXDEV, errno.ENOSYS, errno.EMLINK}


def _escrever_fd(fd: int, dados: bytes) -> None:
    with os.fdopen(fd, "wb") as f:
        f.write(dados)
        f.flush()
        os.fsync(f.fileno())


def _publicar(dfd: int, nome: str, dados: bytes) -> str:
    """ronda 9 (anexos sem ligações simbólicas): os bytes vão para um temporário escondido na própria pasta (fsync) e só depois
    aparecem com o nome final por `link` — que nunca sobrepõe: ou cria o nome ou falha com «já existe». Assim ninguém vê um
    anexo a meio e dois a gravar o mesmo nome não se atropelam (o que chegou primeiro fica intacto)."""
    base, suf = Path(nome).stem, Path(nome).suffix
    tmp = f".{os.getpid()}-{secrets.token_hex(8)}.tmp"
    _escrever_fd(caminhos.abrir_ficheiro(dfd, tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644), dados)
    try:
        final, i = nome, 1
        while True:
            try:
                os.link(tmp, final, src_dir_fd=dfd, dst_dir_fd=dfd, follow_symlinks=False)
                return final
            except FileExistsError:
                if _igual(dfd, final, dados):             # o mesmo conteúdo reutiliza o nome
                    return final
                i += 1
                final = f"{base}_{i}{suf}"
            except OSError as e:
                if e.errno not in _SEM_LIGACAO:
                    raise
                return _publicar_directo(dfd, nome, dados)
    finally:
        try:
            os.unlink(tmp, dir_fd=dfd)
        except OSError:
            pass


def _publicar_directo(dfd: int, nome: str, dados: bytes) -> str:
    base, suf = Path(nome).stem, Path(nome).suffix
    final, i = nome, 1
    while True:
        try:
            fd = caminhos.abrir_ficheiro(dfd, final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            if _igual(dfd, final, dados):
                return final
            i += 1
            final = f"{base}_{i}{suf}"
            continue
        _escrever_fd(fd, dados)
        return final


def guardar(esp, nome: str, dados: bytes) -> str:
    """grava e devolve o nome final. Nunca sobrepõe: o mesmo conteúdo → o mesmo nome; outro conteúdo → nome_2.ext."""
    nome = _validar(nome, dados)
    raiz, pre = _raiz(esp)
    try:
        dfd = caminhos.abrir_pasta(raiz, pre, criar=True)
    except caminhos.Recusado as e:                       # a pasta dos anexos (ou um ancestral) é uma ligação simbólica
        raise ValueError("a pasta dos anexos não é segura (passa por uma ligação simbólica) — não guardei nada") from e
    try:
        return _publicar(dfd, nome, dados)
    finally:
        os.close(dfd)


MAX_NOMES = 10000          # ronda 10 (achado M2 da r3): nome_2 … nome_10000 ocupados → desiste em vez de procurar para sempre


def prever(esp, nome: str, dados: bytes, reservados: dict | None = None) -> str:
    """o nome que `guardar` daria a estes bytes — sem escrever nada (a importação em simulação). `reservados` (nome → bytes) é o que
    a própria simulação já «pôs»: assim dois anexos diferentes com o mesmo nome dão `nome` e `nome_2`, tal como na gravação.
    Levanta o mesmo que `guardar` para nome inválido, tamanho, segredo e pasta insegura.
    ronda 10 (achado M2 da r3): antes qualquer OSError contava como «nome ocupado» — uma pasta (ou um ancestral) que é uma ligação
    simbólica, ou sem permissão, dava erro em TODOS os nomes e a simulação ficava presa em nome_2, nome_3… para sempre. Agora a
    pasta abre-se uma vez (recusada como na gravação); só um ficheiro com esse nome conta como ocupado; o resto é erro."""
    nome = _validar(nome, dados)
    reservados = {} if reservados is None else reservados
    raiz, pre = _raiz(esp)
    try:
        dfd = caminhos.abrir_pasta(raiz, pre)
    except FileNotFoundError:
        dfd = None                                       # a pasta ainda não existe: todos os nomes estão livres
    except caminhos.Recusado as e:
        raise ValueError("a pasta dos anexos não é segura (passa por uma ligação simbólica) — não guardei nada") from e
    base, suf = Path(nome).stem, Path(nome).suffix
    final, i = nome, 1
    try:
        while True:
            if final in reservados:
                ocupado = reservados[final] != dados
            elif dfd is None:
                ocupado = False
            else:
                try:
                    fd = caminhos.abrir_ficheiro(dfd, final)
                except FileNotFoundError:
                    ocupado = False
                except caminhos.Recusado:                # uma ligação ou algo que não é um ficheiro normal com este nome
                    ocupado = True
                else:
                    with os.fdopen(fd, "rb") as f:
                        ocupado = not (os.fstat(f.fileno()).st_size == len(dados) and f.read(len(dados) + 1) == dados)
            if not ocupado:
                reservados[final] = dados
                return final
            i += 1
            if i > MAX_NOMES:
                raise ValueError(f"há demasiados anexos com o nome «{nome}» — muda-lhe o nome")
            final = f"{base}_{i}{suf}"
    finally:
        if dfd is not None:
            os.close(dfd)


def listar(esp) -> list[dict]:
    raiz, pre = _raiz(esp)
    try:
        entradas = caminhos.listar_dir(raiz, pre)        # ronda 9 (anexos sem ligações simbólicas): sem ligações, nem na pasta nem acima
    except OSError:
        return []
    out = []
    for nome, tipo, st in entradas:
        if tipo == "f" and nome_ok(nome):
            out.append({"nome": nome, "bytes": st.st_size, "quando": int(st.st_mtime), "tipo": tipo_de(nome),
                        "imagem": Path(nome).suffix.lower() in IMAGENS})
    return out


def referidos(texto: str) -> set[str]:
    """os anexos que uma nota refere com ![[nome.ext]] (só nomes válidos; ![[nota]] de notas não conta)."""
    return {a.strip() for a in _EMBED_RX.findall(texto or "") if nome_ok(a.strip())}


def orfaos(esp, textos) -> list[str]:
    """anexos que nenhuma das notas (`textos`: iterável de str) refere — para a arrumação mostrar, nunca apagar sozinha."""
    usados = set()
    for t in textos:
        usados |= referidos(t)
    return [a["nome"] for a in listar(esp) if a["nome"] not in usados]
