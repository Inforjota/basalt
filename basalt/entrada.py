"""basalt.entrada — a Entrada (ideias soltas; uma linha JSON por ideia em <dados do espaço>/entrada.jsonl) partilhada por vários
processos: a interface web, o MCP, o CLI (`basalt.sig guardar` / `arquivar`) e a arrumação automática.

Um só protocolo de escrita: acrescentar uma ideia (append) e reescrever o ficheiro (arquivar, apagar, decidir, desfazer) passam
TODOS pelo mesmo trinco `.entrada.lock` (flock), e a reescrita é atómica (ficheiro temporário + replace). Sem isto a web lia a
Entrada, o MCP acrescentava uma ideia, e a web substituía o ficheiro pela fotografia anterior — a ideia desaparecia depois de
já ter sido confirmada a quem a guardou.

Cada ideia tem um `t` único (carimbo ISO aos segundos; se o segundo já estiver ocupado, `~2`, `~3`…), porque a página, o
«juntar», o «apagar» e o «desfazer» identificam a ideia por ele. Um carimbo nunca se emite duas vezes no mesmo espaço: o último
emitido fica em `.entrada.ultimo` (ao lado da Entrada, gravado sob o mesmo trinco) e o seguinte é sempre maior que ele e que
todos os presentes — apagar uma ideia não liberta o seu carimbo. Uma linha ilegível (um processo morto a meio do append) é
saltada na leitura e não derruba a Entrada; o append seguinte começa sempre numa linha nova.
"""
from __future__ import annotations

import contextlib
import datetime
import fcntl
import json
from pathlib import Path

from .config import Via

INBOX = Via("inbox")


def caminho() -> Path:
    return Path(INBOX)


@contextlib.contextmanager
def trinco():
    """o trinco comum a todas as escritas (e às leituras que precisam de uma fotografia coerente)."""
    p = caminho()
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p.with_name(".entrada.lock"), "w") as lk:
        fcntl.flock(lk, fcntl.LOCK_EX)
        yield p


def ler(p: Path | None = None) -> list[dict]:
    """todos os registos (arquivados incluídos), pela ordem do ficheiro; linhas ilegíveis são saltadas."""
    p = p or caminho()
    if not p.exists():
        return []
    out = []
    for l in p.read_text(encoding="utf-8", errors="replace").splitlines():
        if not l.strip():
            continue
        try:
            r = json.loads(l)
        except ValueError:
            continue
        if isinstance(r, dict):
            out.append(r)
    return out


def pendentes() -> list[dict]:
    return [r for r in ler() if not r.get("arquivado")]


def _escrever(p: Path, regs) -> None:
    tmp = p.with_name(".entrada.jsonl.tmp")
    tmp.write_text("".join(json.dumps(r, ensure_ascii=False) + "\n" for r in regs), encoding="utf-8")
    tmp.replace(p)


def _agora() -> str:
    return datetime.datetime.now().isoformat(timespec="seconds")


def _chave(t: str) -> tuple[str, int]:
    """a ordem de um carimbo: (data aos segundos, número) — `…:05` é 1, `…:05~2` é 2, `…:05~10` vem DEPOIS de `…:05~9`."""
    base, _, n = t.partition("~")
    try:
        return base, (int(n) if n else 1)
    except ValueError:
        return t, 1


def _ficheiro_ultimo(p: Path) -> Path:
    return p.with_name(".entrada.ultimo")


def _ler_ultimo(p: Path) -> str:
    try:
        return _ficheiro_ultimo(p).read_text(encoding="utf-8").strip()
    except OSError:
        return ""


def _gravar_ultimo(p: Path, t: str) -> None:
    """ronda 9 (carimbos nunca repetidos): o último carimbo emitido fica ao lado da Entrada (atómico: temporário + replace)."""
    f = _ficheiro_ultimo(p)
    tmp = f.with_name(f.name + ".tmp")
    tmp.write_text(t + "\n", encoding="utf-8")
    tmp.replace(f)


def novo_t(ocupados, ultimo: str = "") -> str:
    """um carimbo ESTRITAMENTE maior que o último emitido e que todos os presentes. ronda 9 (carimbos nunca repetidos): antes
    escolhia-se «o primeiro livre desse segundo», e apagada a ideia `…:05` (ou a `…:05~2`) o seguinte pedido voltava a emitir
    o mesmo carimbo — quem ainda guardava o antigo (a página aberta, um «desfazer») passava a mexer noutra ideia."""
    chaves = [_chave(t) for t in ocupados if isinstance(t, str) and t]
    if ultimo:
        chaves.append(_chave(ultimo))
    base = _agora()
    if not chaves or base > max(chaves)[0]:
        return base
    b, n = max(chaves)                                  # o segundo já foi usado (ou o relógio andou para trás): o seguinte do maior
    return f"{b}~{n + 1}"


def acrescentar(texto: str, de: str, **extra) -> dict:
    """guarda uma ideia nova e devolve o registo (com o `t` único). Quem chama já recusou o que parecia um segredo em claro."""
    with trinco() as p:
        reg = {"t": novo_t({r.get("t") for r in ler(p)}, _ler_ultimo(p)), "de": de, **extra, "texto": texto.strip(), "arquivado": False}
        _gravar_ultimo(p, reg["t"])                     # primeiro o carimbo, depois a ideia: se falhar a meio, sobra um carimbo por usar, nunca um repetido
        with open(p, "a+b") as f:
            f.seek(0, 2)
            if f.tell():
                f.seek(-1, 2)
                if f.read(1) != b"\n":          # um append interrompido deixou a última linha sem fim
                    f.write(b"\n")
            f.write((json.dumps(reg, ensure_ascii=False) + "\n").encode("utf-8"))
    return reg


def mudar(t: str, fn, sob_trinco: Path | None = None) -> dict | None:
    """aplica fn(registo) → registo | None (None = apagar) à ideia PENDENTE com o carimbo t; devolve o registo como estava,
    ou None se já não estava na Entrada. Ronda 9 (achado M2 da ronda 2): quem já segura o trinco (uma operação que mexe também
    numa nota e tem de ser atómica com a Entrada) passa o caminho que o `trinco()` lhe deu em `sob_trinco` — o flock não é
    reentrante, tomá-lo outra vez ficava preso."""
    if sob_trinco is not None:
        return _mudar(sob_trinco, t, fn)
    with trinco() as p:
        return _mudar(p, t, fn)


def _mudar(p: Path, t: str, fn) -> dict | None:
    regs = ler(p)
    alvo = next((r for r in regs if r.get("t") == t and not r.get("arquivado")), None)
    if alvo is None:
        return None
    novo = fn(dict(alvo))
    _escrever(p, [(novo if r is alvo else r) for r in regs if not (r is alvo and novo is None)])
    return alvo


def mudar_todos(fn) -> int:
    """aplica fn(registo) → registo a todos os registos (uma só reescrita, sob o trinco); devolve quantos mudaram."""
    with trinco() as p:
        regs = ler(p)
        out, n = [], 0
        for r in regs:
            novo = fn(dict(r))
            n += novo != r
            out.append(novo)
        if n:
            _escrever(p, out)
        return n


def arquivar_n(n: int) -> dict:
    """marca como arrumada a n-ésima ideia pendente (1 = a mais antiga), como o CLI e a página as numeram.
    IndexError(quantas há) se o número não existir."""
    with trinco() as p:
        regs = ler(p)
        pend = [r for r in regs if not r.get("arquivado")]
        if not 1 <= n <= len(pend):
            raise IndexError(len(pend))
        alvo = pend[n - 1]
        alvo["arquivado"] = True
        _escrever(p, regs)
        return alvo
