"""basalt.etiquetas — as #etiquetas das notas: extrair de um texto e contar num espaço (para o autocompletar e a procura).

A mesma regra da interface: `#palavra` fora de código, URLs, âncoras e [[ligações]], a começar por letra, com letras, dígitos,
`_`, `-` e `/` (etiquetas aninhadas, como no Obsidian); cores css (#fff, #1a2b3c) não contam. Conta também a lista `tags:`
do cabeçalho (formato do Obsidian: `tags: [a, b]` ou `tags: a, b`).
"""
from __future__ import annotations

import re
from collections import Counter
from pathlib import Path

from . import caminhos

TAG_RX = re.compile(r"(?<![\w#/\[])#([A-Za-z][A-Za-z0-9_\-/]*)")
_HEX = re.compile(r"^(?:[0-9a-fA-F]{3}|[0-9a-fA-F]{4}|[0-9a-fA-F]{6}|[0-9a-fA-F]{8})$")
_CERCA = re.compile(r"(?ms)^```.*?^```[ \t]*$|^~~~.*?^~~~[ \t]*$")
_INLINE = re.compile(r"`[^`\n]*`")
_URL = re.compile(r"\bhttps?://\S+|\bmailto:\S+")
_LIG = re.compile(r"!?\[\[[^\]]*\]\]")
_CAB = re.compile(r"(?s)\A---\n(.*?)\n---\n?")
_TAGS_CAB = re.compile(r"(?m)^tags:\s*(.*)$")


def _limpar(etq: str) -> str:
    return etq.strip("-/").rstrip("_")


def extrair(texto: str) -> list[str]:
    """as etiquetas de UMA nota, por ordem de aparição, sem repetir."""
    texto = texto or ""
    vistas, out = set(), []

    def pôr(e):
        e = _limpar(e)
        if e and not _HEX.match(e) and e.lower() not in vistas:
            vistas.add(e.lower())
            out.append(e)

    m = _CAB.match(texto)
    corpo = texto
    if m:
        corpo = texto[m.end():]
        for linha in _TAGS_CAB.findall(m.group(1)):
            for e in re.split(r"[,\s\[\]\"']+", linha):
                if e:
                    pôr(e.lstrip("#"))
    corpo = _CERCA.sub(" ", corpo)
    corpo = _INLINE.sub(" ", corpo)
    corpo = _LIG.sub(" ", corpo)
    corpo = _URL.sub(" ", corpo)
    for e in TAG_RX.findall(corpo):
        pôr(e)
    return out


def contar(textos) -> Counter:
    """quantas notas usam cada etiqueta (a chave é a etiqueta como aparece na 1.ª nota; a comparação ignora maiúsculas)."""
    c, nome = Counter(), {}
    for t in textos:
        for e in extrair(t):
            k = e.lower()
            nome.setdefault(k, e)
            c[k] += 1
    return Counter({nome[k]: n for k, n in c.items()})


def do_espaco(esp) -> list[dict]:
    """[{etiqueta, notas}] por ordem decrescente de uso, depois alfabética — para /api/etiquetas."""
    textos = []
    for p in caminhos.notas(esp.real):
        try:
            textos.append(p.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            continue
    c = contar(textos)
    return [{"etiqueta": e, "notas": n} for e, n in sorted(c.items(), key=lambda x: (-x[1], x[0].lower()))]
