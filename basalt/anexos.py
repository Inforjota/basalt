"""basalt.anexos — os ficheiros anexos de um espaço (imagens, PDF, áudio…) que as notas referem com ![[nome.ext]].

Ficam em `Espaco.anexos` (<espaço>/anexos/), planos: sem subpastas, sem `..`, sufixos conhecidos, tamanho limitado.
Independente do servidor HTTP: `ler` devolve (estado, cabeçalhos, corpo) e quem serve envia; `guardar` nunca sobrepõe
(o mesmo conteúdo reutiliza o nome; conteúdo diferente com o mesmo nome fica `nome_2.ext`).
"""
from __future__ import annotations

import mimetypes
import os
import re
from pathlib import Path

from . import caminhos
from .segredos import SEGREDO_RX, SegredoEmClaro, tem_segredo_texto

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
    return bool(nome) and bool(NOME_RX.fullmatch(nome)) and Path(nome).suffix.lower() in SUFIXOS


def pasta(esp) -> Path:
    return Path(getattr(esp, "anexos", None) or (esp.real / "anexos"))


def tipo_de(nome: str) -> str:
    t = mimetypes.guess_type(nome)[0] or "application/octet-stream"
    return t + ("; charset=utf-8" if t.startswith("text/") or t == "application/json" else "")


# ronda 8: revalida sempre (o servidor responde 304 se não mudou) — com max-age o browser servia o anexo de outra conta
CACHE = "private, no-cache"


def ler(esp, nome: str):
    """(estado, cabeçalhos, corpo). 400 nome inválido · 404 não há · 200 com o tipo pelo sufixo (nunca pelo conteúdo)."""
    if not nome_ok(nome):
        return 400, {"Content-Type": _TEXTO}, "nome de anexo inválido".encode("utf-8")
    try:
        if not caminhos.regular(pasta(esp) / nome):      # ronda 8 (ligações simbólicas): uma ligação simbólica na pasta não se serve (podia apontar para fora)
            raise FileNotFoundError(nome)
        dados = (pasta(esp) / nome).read_bytes()
    except (FileNotFoundError, IsADirectoryError, NotADirectoryError, PermissionError):
        return 404, {"Content-Type": _TEXTO}, "anexo não encontrado".encode("utf-8")
    tipo = tipo_de(nome)
    cab = {"Content-Type": tipo, "Content-Length": str(len(dados)), "Cache-Control": CACHE,
           "X-Content-Type-Options": "nosniff"}
    if tipo.startswith("image/svg"):          # um SVG aberto directamente é um documento: sem scripts nem pedidos para fora
        cab["Content-Security-Policy"] = "default-src 'none'; style-src 'unsafe-inline'"
    return 200, cab, dados


def guardar(esp, nome: str, dados: bytes) -> str:
    """grava e devolve o nome final. Nunca sobrepõe: o mesmo conteúdo → o mesmo nome; outro conteúdo → nome_2.ext."""
    nome = (nome or "").strip()
    if not nome_ok(nome):
        raise ValueError("nome de anexo inválido (letras, números, ponto, espaço, - e _; sufixo conhecido)")
    if len(dados) > TAMANHO_MAX:
        raise ValueError("anexo demasiado grande (máximo %d MB)" % (TAMANHO_MAX // 1024 // 1024))
    txt = dados.decode("utf-8", errors="replace")
    if SEGREDO_RX.search(txt) or (Path(nome).suffix.lower() in TEXTO and tem_segredo_texto(txt)):
        raise SegredoEmClaro()
    d = pasta(esp)
    d.mkdir(parents=True, exist_ok=True)
    base, suf = Path(nome).stem, Path(nome).suffix
    final, i = nome, 1
    while True:
        try:
            fd = os.open(d / final, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o644)
        except FileExistsError:
            try:
                if caminhos.regular(d / final) and (d / final).read_bytes() == dados:
                    return final
            except OSError:
                pass
            i += 1
            final = f"{base}_{i}{suf}"
            continue
        with os.fdopen(fd, "wb") as f:
            f.write(dados)
        return final


def listar(esp) -> list[dict]:
    d = pasta(esp)
    if not d.is_dir():
        return []
    out = []
    for p in sorted(d.iterdir()):
        if p.is_file() and nome_ok(p.name):
            st = p.stat()
            out.append({"nome": p.name, "bytes": st.st_size, "quando": int(st.st_mtime), "tipo": tipo_de(p.name),
                        "imagem": p.suffix.lower() in IMAGENS})
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
