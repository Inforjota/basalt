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
from .segredos import tem_segredo_texto

EXCLUIR = Via("excluir")
IGNORA = ("MEMORY.md",)                 # índice de memórias do Claude Code: não é uma nota


def lista():
    """os nomes em `excluidas.txt` (vazio se não há ficheiro)."""
    f = Path(EXCLUIR)
    try:
        return {l.strip() for l in f.read_text(encoding="utf-8").splitlines() if l.strip()} if f.exists() else set()
    except OSError:
        return set()


def retida(nome, texto, excl=None):
    """True se a nota `nome` com este `texto` não pode sair (está na lista ou tem segredo em claro)."""
    return nome in (lista() if excl is None else excl) or tem_segredo_texto(texto)


def notas(pasta, ignora=IGNORA):
    """[(Path, texto)] das notas de `pasta` que PODEM sair — por ordem de nome; sem `ignora`, cópias `.bak`/`.CONFLITO-`,
    nem retidas. Quem manda texto de notas para a IA ou para o índice lê-as DAQUI."""
    excl = lista()
    out = []
    for p in caminhos.notas(pasta):             # ronda 8 (ligações simbólicas)
        if p.name in ignora or ".bak" in p.name or ".CONFLITO-" in p.name:
            continue
        try:
            t = p.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if retida(p.stem, t, excl):
            continue
        out.append((p, t))
    return out


def nomes_retidos(pasta):
    """os nomes (sem .md) das notas de `pasta` que NÃO podem sair — para filtrar o que já vem do índice ou de pares."""
    excl = lista()
    out = set(excl)
    for p in caminhos.notas(pasta):
        if p.stem in out or p.name in IGNORA:
            continue
        try:
            if tem_segredo_texto(p.read_text(encoding="utf-8", errors="replace")):
                out.add(p.stem)
        except OSError:
            continue
    return out
