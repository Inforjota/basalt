"""basalt.retidas — que notas NÃO saem do espaço: nem para a IA (embeddings, pergunta, categorias, arrumar, unir, entrada
automática), nem para o índice, nem para a cópia.

Uma nota está RETIDA quando (a) o seu nome está em `excluidas.txt` (a web calcula-o: nota que está na memória real mas não na
cópia, porque o guarda de segredos do 2vias a reteve) OU (b) o próprio texto tem o que parece um segredo em claro
(basalt.segredos). A (b) é o critério de raiz e não depende de o servidor estar a correr nem de a lista estar em dia — «retida»
tem de querer dizer «não sai para a IA» em todos os caminhos (ronda 8, notas retidas).
"""
from pathlib import Path

from . import caminhos

from .config import Via
from .segredos import retidos, tem_segredo_texto

EXCLUIR = Via("excluir")
IGNORA = ("MEMORY.md",)                 # índice de memórias do Claude Code: não é uma nota


def lista():
    """os nomes em `excluidas.txt` (vazio se não há ficheiro)."""
    try:
        txt = caminhos.ler_texto(EXCLUIR)              # ronda 13: sem seguir ligações
    except FileNotFoundError:
        return set()
    except caminhos.Recusado:
        raise
    except OSError:
        return set()
    return {l.strip() for l in txt.splitlines() if l.strip()}


def retida(nome, texto, excl=None):
    """True se a nota `nome` com este `texto` não pode sair (está na lista ou tem segredo em claro — no texto OU no nome: ronda 9,
    achado G6 da ronda 2 — uma chave no nome do ficheiro ia para o índice, os embeddings e as categorias com o nome)."""
    # ronda 13: o texto passa também pelo verificador configurado ([segredos] verificador); se ele falhar, fica retida
    return nome in (lista() if excl is None else excl) or tem_segredo_texto(nome) or retidos([texto])[0]


def notas(pasta, ignora=IGNORA):
    """[(Path, texto)] das notas de `pasta` que PODEM sair — por ordem de nome; sem `ignora`, cópias `.bak`/`.CONFLITO-`,
    nem retidas. Quem manda texto de notas para a IA ou para o índice lê-as DAQUI."""
    excl = lista()
    lidas = []
    for p in caminhos.notas(pasta):             # ronda 8 (ligações simbólicas)
        if p.name in ignora or ".bak" in p.name or ".CONFLITO-" in p.name:
            continue
        try:
            t = caminhos.ler_texto(p)
        except OSError:
            continue
        if p.stem in excl or tem_segredo_texto(p.stem):
            continue
        lidas.append((p, t))
    # ronda 13: o verificador configurado vê-as todas de uma vez (em lotes), não uma chamada por nota
    return [pt for pt, r in zip(lidas, retidos([t for _, t in lidas])) if not r]


def nomes_retidos(pasta):
    """os nomes (sem .md) das notas de `pasta` que NÃO podem sair — para filtrar o que já vem do índice ou de pares."""
    excl = lista()
    out = set(excl)
    lidas = []
    for p in caminhos.notas(pasta):
        if p.stem in out or p.name in IGNORA:
            continue
        if tem_segredo_texto(p.stem):           # ronda 9 (achado G6 da ronda 2): o nome também
            out.add(p.stem)
            continue
        try:
            lidas.append((p.stem, caminhos.ler_texto(p)))
        except OSError:
            continue
    out.update(n for (n, _), r in zip(lidas, retidos([t for _, t in lidas])) if r)     # ronda 13: em lote, com o verificador
    return out
