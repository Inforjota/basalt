"""basalt.segredos — o único guarda de passwords e chaves em claro, comum a TODAS as entradas.

Interface web, MCP (escrever/criar/acrescentar/guardar_ideia), `basalt.sig guardar`, importação Obsidian, anexos de texto e a
sincronização em duas vias (`duasvias`) passam por aqui. Antes cada entrada tinha a sua cópia do padrão e algumas não tinham
nenhuma (ronda 8, segredos em todas as entradas).

O que conta como segredo:
- formatos de chaves/tokens conhecidos (`SEGREDO_RX`);
- uma password escrita à mão a seguir a «password/senha/palavra-passe … :» com algarismo ou símbolo (`PASSWORD_RX`), excepto
  um valor com formato de data/hora quando a MESMA frase fala de validade («validade da senha: 2026-10-06»);
- uma password só de letras, com 12+ letras ou maiúsculas no meio («senha: minhapalavrasecreta»), também entre aspas, crases
  ou ênfase e seguida de uma observação («senha: `minhapalavrasecreta` (router)»), excepto palavras correntes («senha: alterada»,
  «password: unchanged»).

Falar de passwords sem as escrever continua a poder gravar-se: «senha: está no cofre», «password: ver_no_cofre»,
«mudar a password todos os meses», «senha do NAS: https://cofre.exemplo.pt».
"""
from __future__ import annotations

import re
from pathlib import Path

MOTIVO = "O texto tem o que parece uma password ou chave em claro — não gravei. Guarda-a num gestor de palavras-passe."


class SegredoEmClaro(ValueError):
    """a entrada recusou o texto por ter uma password ou chave em claro (a mensagem é MOTIVO)."""
    def __init__(self, motivo: str = MOTIVO):
        super().__init__(motivo)

SEGREDO_RX = re.compile(
    r"(sk-ant-[A-Za-z0-9_-]{20,}|sk-proj-[A-Za-z0-9_-]{20,}|sk-[A-Za-z0-9]{40,}|gh[pousr]_[A-Za-z0-9]{30,}|"
    r"github_pat_[A-Za-z0-9_]{30,}|AKIA[A-Z0-9]{16}|xox[baprs]-[A-Za-z0-9-]{20,}|-----BEGIN [A-Z ]*PRIVATE KEY-----|"
    r"AIza[0-9A-Za-z_-]{35}|[0-9]{8,10}:[A-Za-z0-9_-]{35}|bsl[tar]_[A-Za-z0-9_-]{30,})")
# `bslt_` token pessoal; ronda 13: `bsla_` (acesso) e `bslr_` (renovação) do OAuth também. O `bslc_` (id de cliente) é público.

# a declaração («senha do router:») não atravessa um fim de linha — nem o CR solto nem os outros (ronda 13)
_FINS_DE_LINHA = "\r\n\x0b\x0c\x85\u2028\u2029"
# password escrita à mão: só conta se o valor (6+ caracteres) tiver algarismo ou símbolo e não for caminho/URL
PASSWORD_RX = re.compile(r"(?i)\b(?:password|passwd|senha|palavra[- ]passe)\b[^" + _FINS_DE_LINHA + r":=]{0,40}[:=][ \t]*"
                         r"(?!https?://|[/~])"
                         r"(?=\S*[0-9!@#$%^&*])(\S{6,})")
# um valor com formato de data/hora («2026-10-06», «06/10/2026», «10:30:59») só deixa de contar como password quando a
# mesma frase fala de validade. A frase é o que está entre o último separador (; , . : ( ) — nova linha) e o valor:
# «data: 06/10 — senha: 2026-10-06» e «dia de instalação: terça; password: 10:30:59» são passwords (ronda 7, contexto de validade).
_DATA_HORA_RX = re.compile(r"^(?:\d{4}-\d{2}-\d{2}|\d{1,2}[/.-]\d{1,2}[/.-]\d{2,4}|\d{1,2}[:h]\d{2}(?::\d{2})?)[.,;:)]*$")
# ronda 7: a palavra tem de falar de PRAZO — «válida», «renovada», «valid» sozinhas não chegam («a senha válida: 2026-10-06»,
# «senha renovada: 06/10/2026» e «valid password: 10:30:59» são passwords); «válida até», «renovada em», «expira» sim.
_VALIDADE_RX = re.compile(r"(?i)\b(?:validade|expir\w*|venc\w*|prazo|caduc\w*|at[eé]|desde|renova[çc][ãa]o|rota[çc][ãa]o|"
                          r"(?:v[aá]lid[ao]s?|renovad[ao]s?|alterad[ao]s?|mudad[ao]s?|trocad[ao]s?)\s+(?:at[eé]|desde|em|a|por)|"
                          r"expires?|expiry|expiration|until|valid\s+(?:until|through|for|from)|validity|renewal|rotation|"
                          r"(?:renew\w*|rotat\w*|changed)\s+(?:on|at|every))\b")
# ronda 13: o CR solto e os outros fins de linha (VT, FF, NEL, LS, PS) também fecham a frase, como «!» e «?»:
# «senha expira amanhã\rsenha: 2026-10-06» passava (o prazo da linha de cima desculpava a data da de baixo).
_SEPARADOR_RX = re.compile(r"[" + _FINS_DE_LINHA + r";,.:!?()\[\]{}|—–]|\s-\s")

# password só de letras («senha: minhapalavrasecreta»). Ronda 11: o valor pode vir entre aspas («» “” ‘’ "" ''), crases ou marcas
# de ênfase, e acabar no fim da linha, numa pontuação seguida de espaço, num parêntese, num «|», num travessão ou num « - »:
# «senha: "minhapalavracomplicada"», «senha: `…`», «senha: … (router)», «senha: …; utilizador: ana» passavam como texto.
# Uma palavra seguida de outra palavra («senha: minhapalavrasecreta escrita no caderno») continua a ser prosa.
# O fim da linha também pode ser o do Windows (CR LF) ou um CR solto: o `$` em modo multilinha só vê o LF, e uma nota
# escrita ou sincronizada a partir do Windows («senha: minhapalavrasecreta\r\n») passava inteira como texto.
_ENVOLVE = "[\"'`\u201c\u201d\u2018\u2019\u00ab\u00bb*_~]{0,3}"
_FIM_LINHA = r"(?:[\r\x0b\x0c\x85\u2028\u2029]|$)"
# Ronda 13: também acaba num parêntese, parêntese recto ou chaveta de FECHAR, numa aspa, e num «;» ou «,» colado ao que
# vem a seguir — «(senha: minhapalavrasecreta)», «{"senha": "…"}» e «senha: …;utilizador: ana» passavam como texto.
# O «.», o «:», o «!» e o «?» continuam a pedir espaço ou fim de linha («senha: minhapalavrasecreta.txt» é um nome de ficheiro).
SO_LETRAS_RX = re.compile(r"(?im)\b(?:password|passwd|senha|palavra[- ]passe)\b[^" + _FINS_DE_LINHA + r":=]{0,40}[:=][ \t]*"
                          + _ENVOLVE + r"([^\W\d_]{8,})" + _ENVOLVE
                          + r"(?=[ \t]*(?:" + _FIM_LINHA + r"|[.:!?](?:[ \t]|" + _FIM_LINHA + r")|"
                          + r"[,;()\[\]{}|\"'\u2014\u2013\u00bb\u201d\u2019]|-[ \t]))")
_MAIUSCULA_NO_MEIO_RX = re.compile(r"[a-zà-ÿ][A-ZÀ-Þ]")
_PALAVRAS_CORRENTES = {
    "desconhecida", "desconhecido", "actualizada", "atualizada", "actualizado", "atualizado", "temporariamente",
    "provisoriamente", "obrigatoria", "obrigatória", "necessaria", "necessária", "indisponivel", "indisponível",
    "confidencial", "personalizada", "personalizado", "redefinida", "redefinido", "reinicializada", "verificada",
    "confirmada", "armazenada", "guardada", "protegida", "partilhada", "partilhado", "administrador", "administrativa",
    "desactivada", "desativada", "recuperada", "renovada", "expirada", "caducada", "alterada", "esquecida", "perdida",
    "unavailable", "unchanged", "temporarily", "confidential", "administrator", "unspecified", "uninitialized",
    "placeholder", "deactivated", "regenerated", "requested", "mandatory", "passphrase", "undefined", "unknown",
    # descrições soltas, uma a uma (antes cobertas pela regra dos sufixos, que deixava passar passwords)
    "criptografada", "criptografado", "encriptada", "encriptado", "sincronizada", "sincronizado", "alfanumerica",
    "alfanumérica", "alfanumerico", "alfanumérico", "automaticamente", "alphanumeric", "alphanumerical", "synchronized",
    "synchronised", "automatically",
    # ronda 13: descrições que vinham com uma observação a seguir («senha: desnecessária (já não usamos)»)
    "desnecessaria", "desnecessária", "desnecessario", "desnecessário", "desaconselhada", "desaconselhado",
    "dispensavel", "dispensável", "caseinsensitive", "casesensitive", "notrequired", "unnecessary",
}


# ronda 9 (contexto e descrições): a declaração começa na ÚLTIMA palavra-chave antes do «:» — em «senha válida até 2026-10-06 password: 2026-10-07»
# a primeira declaração não tem «:» e o prazo dela contaminava a segunda. O texto de antes corta-se no último separador,
# palavra-chave ou número/data/hora (cada um fecha a declaração anterior).
_PALAVRA_CHAVE_RX = re.compile(r"(?i)\b(?:password|passwd|senha|palavra[- ]passe)\b")
_DATA_HORA_SOLTA_RX = re.compile(r"\d+(?:[/.:h-]\d+)*")          # qualquer número fecha: «até 2027», «06/10», «10:30»
# ronda 9 (contexto e descrições): só para as passwords só de letras — a palavra-chave como complemento («estado DA senha», «tipo DE password», «type
# OF password») descreve a password, não a dá; e uma palavra longa com sufixo de adjectivo/advérbio («sincronizada»,
# «alfanumérica», «automaticamente») é descrição. Uma maiúscula no meio continua a contar como password.
# ronda 9 (passwords só de letras, outra vez): a excepção de cima deixava passar passwords — «senha: minhapalavracomplicada» (sufixo «-ada»), «O valor da senha:
# minhaSenhaSecreta» («da» antes, e a maiúscula no meio só se via depois), «The password: …». Agora falha fechada: uma maiúscula no
# meio conta SEMPRE; a descrição só se aceita com um substantivo de PROPRIEDADE desta lista antes do «da/de/of» E uma palavra com
# sufixo de adjectivo/advérbio de até 16 letras; o resto (de 12 letras para cima) é password. As palavras soltas que descrevem
# («senha: criptografada») vão para _PALAVRAS_CORRENTES, uma a uma.
_PROPRIEDADE_RX = re.compile(r"(?i)\b(?:estado|tipo|formato|for[çc]a|comprimento|tamanho|pol[ií]tica|regras?|requisitos?|n[ií]vel|"
                             r"qualidade|complexidade|status|state|type|format|strength|length|policy|rules?|requirements?|level|"
                             r"quality|complexity)\s+(?:d[aoe]s?|of)\s*$")
_DESCRICAO_MAX = 16
_DESCRITIVA_RX = re.compile(r"(?i)(?:[aií]d[ao]s?|ic[ao]s?|[áa]vel|[íi]vel|veis|mente|[çc][ãa]o|[çc][õo]es)$")


def _contexto(txt, m):
    """(texto de antes da declaração, declaração até ao valor) — a declaração é a da última palavra-chave do match."""
    inicio = m.start()
    for k in _PALAVRA_CHAVE_RX.finditer(txt, m.start(), m.start(1)):
        inicio = k.start()
    antes = txt[max(0, inicio - 60):inicio]
    cortes = [c.end() for rx in (_SEPARADOR_RX, _PALAVRA_CHAVE_RX, _DATA_HORA_SOLTA_RX) for c in rx.finditer(antes)]
    if cortes:
        antes = antes[max(cortes):]
    return antes, txt[inicio:m.start(1)]


def _password_em_claro(txt: str) -> bool:
    for m in PASSWORD_RX.finditer(txt):
        if _DATA_HORA_RX.match(m.group(1)):
            antes, decl = _contexto(txt, m)
            if _VALIDADE_RX.search(antes + decl):
                continue
        return True
    for m in SO_LETRAS_RX.finditer(txt):
        v = m.group(1)
        if _MAIUSCULA_NO_MEIO_RX.search(v):
            return True
        if v.lower() in _PALAVRAS_CORRENTES:
            continue
        antes, _ = _contexto(txt, m)                  # ronda 9 (contexto e descrições): «Estado da senha: …», «Tipo de password: …» descrevem-na
        if _PROPRIEDADE_RX.search(antes) and _DESCRITIVA_RX.search(v) and len(v) <= _DESCRICAO_MAX:
            continue
        if len(v) >= 12:
            return True
    return False


def tem_segredo_texto(txt: str) -> bool:
    return bool(SEGREDO_RX.search(txt) or _password_em_claro(txt))


def tem_segredo_bytes(b: bytes) -> bool:
    return tem_segredo_texto(b.decode("utf-8", errors="replace"))


def tem_segredo_ficheiro(p: Path) -> bool:
    from . import caminhos
    return tem_segredo_texto(caminhos.ler_texto(p))


OMITIDO = "(omitido: parecia ter uma password ou chave)"


def mascarar(txt: str, omitido: str = OMITIDO) -> str:
    """ronda 13: o texto com as chaves e os valores das passwords tapados (•••), para o registo, os avisos e os relatórios —
    os dois detectores (chaves conhecidas e passwords escritas à mão). Se ainda assim parecer ter um segredo, vai `omitido`."""
    t = SEGREDO_RX.sub("•••", str(txt or ""))
    for rx in (PASSWORD_RX, SO_LETRAS_RX):
        t = rx.sub(lambda m: m.group(0)[:m.start(1) - m.start()] + "•••" + m.group(0)[m.end(1) - m.start():], t)
    return omitido if tem_segredo_texto(t) else t
