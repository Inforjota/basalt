"""basalt.caminhos — contenção no espaço: nada do que o Basalt lista ou serve segue ligações simbólicas (symlinks).

Os nomes já são filtrados (NOME_RX nas notas, nome_ok nos anexos), por isso pela web não se sai da pasta. Isto cobre o que
chega por outra via — um cofre Obsidian importado com ligações, um anexo posto à mão na pasta, uma nota simbólica na memória
real — e que as listagens por glob e as leituras seguiam sem perguntar, copiando ou servindo ficheiros de fora do espaço.
"""
from pathlib import Path


def regular(p) -> bool:
    """um ficheiro normal com esse nome: não é uma ligação simbólica (mesmo que aponte para dentro) nem uma pasta."""
    p = Path(p)
    try:
        return not p.is_symlink() and p.is_file()
    except OSError:
        return False


def notas(pasta, padrao="*.md") -> list[Path]:
    """os ficheiros normais de `pasta` que casam com o padrão, por ordem de nome — sem ligações simbólicas."""
    return [p for p in sorted(Path(pasta).glob(padrao)) if regular(p)]
