"""basalt.segredos — o único guarda de passwords e chaves em claro, comum a TODAS as entradas.

Interface web, MCP (escrever/criar/acrescentar/guardar_ideia), `basalt.sig guardar`, importação Obsidian, anexos de texto e a
sincronização em duas vias (`duasvias`) passam por aqui. Antes cada entrada tinha a sua cópia do padrão e algumas não tinham
nenhuma (ronda 8, segredos em todas as entradas).

O que conta como segredo:
- formatos de chaves/tokens conhecidos (`SEGREDO_RX`);
- uma password escrita à mão a seguir a «password/senha/palavra-passe … :» com algarismo ou símbolo (`PASSWORD_RX`), excepto
  um valor com formato de data/hora quando a MESMA frase fala de validade («validade da senha: 2026-10-06»);
- uma password só de letras, sozinha no fim da linha, com 12+ letras ou maiúsculas no meio («senha: minhapalavrasecreta»),
  excepto palavras correntes («senha: alterada», «password: unchanged»).

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
    r"AIza[0-9A-Za-z_-]{35}|[0-9]{8,10}:[A-Za-z0-9_-]{35}|bslt_[A-Za-z0-9_-]{30,})")

# password escrita à mão: só conta se o valor (6+ caracteres) tiver algarismo ou símbolo e não for caminho/URL
PASSWORD_RX = re.compile(r"(?i)\b(?:password|passwd|senha|palavra[- ]passe)\b[^\n:=]{0,40}[:=][ \t]*"
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
_SEPARADOR_RX = re.compile(r"[\n;,.:()\[\]{}|—–]|\s-\s")

# password só de letras, sozinha no fim da linha («senha: minhapalavrasecreta»)
SO_LETRAS_RX = re.compile(r"(?im)\b(?:password|passwd|senha|palavra[- ]passe)\b[^\n:=]{0,40}[:=][ \t]*([^\W\d_]{8,})"
                          r"[ \t]*[.,;!]?[ \t]*$")
_MAIUSCULA_NO_MEIO_RX = re.compile(r"[a-zà-ÿ][A-ZÀ-Þ]")
_PALAVRAS_CORRENTES = {
    "desconhecida", "desconhecido", "actualizada", "atualizada", "actualizado", "atualizado", "temporariamente",
    "provisoriamente", "obrigatoria", "obrigatória", "necessaria", "necessária", "indisponivel", "indisponível",
    "confidencial", "personalizada", "personalizado", "redefinida", "redefinido", "reinicializada", "verificada",
    "confirmada", "armazenada", "guardada", "protegida", "partilhada", "partilhado", "administrador", "administrativa",
    "desactivada", "desativada", "recuperada", "renovada", "expirada", "caducada", "alterada", "esquecida", "perdida",
    "unavailable", "unchanged", "temporarily", "confidential", "administrator", "unspecified", "uninitialized",
    "placeholder", "deactivated", "regenerated", "requested", "mandatory", "passphrase", "undefined", "unknown",
}


def _password_em_claro(txt: str) -> bool:
    for m in PASSWORD_RX.finditer(txt):
        if _DATA_HORA_RX.match(m.group(1)):
            antes = txt[max(0, m.start() - 60):m.start()]
            cortes = [c.end() for c in _SEPARADOR_RX.finditer(antes)]
            if cortes:
                antes = antes[cortes[-1]:]
            if _VALIDADE_RX.search(antes + txt[m.start():m.start(1)]):
                continue
        return True
    for m in SO_LETRAS_RX.finditer(txt):
        v = m.group(1)
        if v.lower() in _PALAVRAS_CORRENTES:
            continue
        if len(v) >= 12 or _MAIUSCULA_NO_MEIO_RX.search(v):
            return True
    return False


def tem_segredo_texto(txt: str) -> bool:
    return bool(SEGREDO_RX.search(txt) or _password_em_claro(txt))


def tem_segredo_bytes(b: bytes) -> bool:
    return tem_segredo_texto(b.decode("utf-8", errors="replace"))


def tem_segredo_ficheiro(p: Path) -> bool:
    return tem_segredo_texto(Path(p).read_text(encoding="utf-8", errors="replace"))
