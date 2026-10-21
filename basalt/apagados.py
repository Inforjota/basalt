"""basalt.apagados — o registo das contas apagadas e a eliminação de vez (r17).

`basalt utilizador apagar` desliga a conta logo (o bloco do basalt.toml, as sessões e as ligações das IAs) e move a pasta do
espaço para <dados>/apagados/. ANTES de mover, grava uma entrada em <dados>/contas-apagadas.json, já com o destino da pasta.
A partir daí, a linha de comandos `basalt apagados`:

  listar                          as entradas (o e-mail só enquanto a entrada não for eliminada)
  eliminar [--dias 30] [--sim]    apaga DE VEZ o que passou dos N dias: a pasta em apagados/, as ligações das IAs e os pedidos
                                  de autorização na base OAuth, as sessões; a entrada fica só com a data e o resumo do e-mail
  exportar                        JSON Lines {email_sha256, conta_id, apagado_em}: guardar FORA das cópias de segurança
  reaplicar --lista F [--sim]     depois de repor uma cópia antiga: volta a apagar as contas que a cópia trouxe de volta

Uma conta reconhece-se pelo resumo do e-mail (sha256 com um prefixo fixo) E pelo `conta_id` aleatório que ganha ao ser criada:
uma conta criada mais tarde com o mesmo e-mail tem outro `conta_id` e nunca é tocada. As contas criadas antes da r17 não têm
`conta_id` (fica vazio): para essas, o mesmo e-mail chega para as reconhecer (ver docs/INSTALAR.md).

O resumo do e-mail não é anónimo: quem já souber um e-mail consegue ver se está na lista. Serve para reconhecer a conta depois de
repor uma cópia sem guardar o e-mail em claro.

`eliminar` e `reaplicar` acabam sempre com uma linha `resumo: …` (a última do stdout, sem e-mails) para quem os vigia.
"""
from __future__ import annotations

import calendar
import contextlib
import errno
import fcntl
import hashlib
import json
import os
import re
import secrets
import shutil
import stat
import sys
import time
from pathlib import Path

from . import caminhos, config

REGISTO = "contas-apagadas.json"
TRINCO = "contas-apagadas.lock"
PASTA = "apagados"
DIAS = 30
PREFIXO = "basalt-conta-apagada:"
LIMITE_REGISTO = 64 * 1024 * 1024
_FMT = "%Y-%m-%dT%H:%M:%SZ"
_SHA_RX = re.compile(r"[0-9a-f]{64}")


class RegistoInvalido(Exception):
    """o contas-apagadas.json não se lê como uma lista de entradas: nunca se regrava por cima (perdia-se o que lá está)."""


class Recusa(Exception):
    """uma entrada que não se elimina por uma razão de segurança (pasta fora de apagados/, ligação simbólica…)."""


def _agora() -> float:
    """o relógio deste módulo (os testes trocam-no para «passar» os dias sem esperar)."""
    return time.time()


def carimbo(t: float | None = None) -> str:
    """AAAA-MM-DDTHH:MM:SSZ, sempre em UTC."""
    return time.strftime(_FMT, time.gmtime(_agora() if t is None else t))


def instante(s) -> float | None:
    """o carimbo em segundos desde a época (None = não é um carimbo válido)."""
    try:
        return float(calendar.timegm(time.strptime(s, _FMT)))
    except (TypeError, ValueError):
        return None


def email_sha256(email: str) -> str:
    return hashlib.sha256((PREFIXO + (email or "").strip().lower()).encode("utf-8")).hexdigest()


# ───────────────────────────── o registo: <dados>/contas-apagadas.json ─────────────────────────────
@contextlib.contextmanager
def trinco(dados):
    """um só escritor do registo de cada vez (flock em <dados>/contas-apagadas.lock, aberto sem seguir ligações). Criado por
    outro utilizador (a linha de comandos corrida como root), abre-se só para ler: o flock não precisa de escrita."""
    p = Path(dados) / TRINCO
    try:
        fd = caminhos.abrir_trinco(p)
    except PermissionError:
        fd = caminhos.abrir(p.parent, [p.name])
    try:
        fcntl.flock(fd, fcntl.LOCK_EX)
        yield
    finally:
        os.close(fd)


def _validar(lista) -> list[dict]:
    if not isinstance(lista, list):
        raise RegistoInvalido(f"{REGISTO} não é uma lista")
    vistos = set()
    for i, e in enumerate(lista, 1):
        ok = (isinstance(e, dict) and isinstance(e.get("id"), str) and e["id"] and e["id"] not in vistos
              and isinstance(e.get("email_sha256"), str) and _SHA_RX.fullmatch(e["email_sha256"])
              and isinstance(e.get("conta_id", ""), str) and instante(e.get("apagado_em")) is not None
              and (e.get("eliminado_em") is None or instante(e.get("eliminado_em")) is not None)
              and all(e.get(k) is None or isinstance(e.get(k), str) for k in ("email", "espaco", "pasta")))
        if not ok:
            raise RegistoInvalido(f"a entrada n.º {i} de {REGISTO} está estragada")
        vistos.add(e["id"])
    return lista


def ler(dados) -> list[dict]:
    """as entradas do registo (lista vazia se ainda não existe). Lido sem seguir ligações simbólicas; um ficheiro que não seja
    uma lista de entradas válida dá RegistoInvalido — e nunca se regrava como lista vazia."""
    try:
        b = caminhos.ler_bytes(Path(dados), [REGISTO], limite=LIMITE_REGISTO)
    except FileNotFoundError:
        return []
    try:
        lista = json.loads(b.decode("utf-8"))
    except (UnicodeDecodeError, ValueError) as e:
        raise RegistoInvalido(f"{REGISTO} não é JSON válido ({e.__class__.__name__})") from e
    return _validar(lista)


def gravar(dados, lista: list[dict]) -> None:
    """troca o registo de uma vez: temporário 0600 na mesma pasta → fsync → rename → fsync da pasta. Sem seguir ligações (um
    registo que seja uma ligação simbólica é recusado). O ficheiro fica com o dono do anterior, ou da pasta `dados`: a linha de
    comandos corrida como root não o deixa ilegível para o serviço."""
    _validar(lista)
    texto = (json.dumps(lista, ensure_ascii=False, indent=1) + "\n").encode("utf-8")
    dfd = caminhos.abrir_pasta(Path(dados), criar=True)
    try:
        try:
            st = os.stat(REGISTO, dir_fd=dfd, follow_symlinks=False)
            if not stat.S_ISREG(st.st_mode):
                raise caminhos.Recusado(errno.ELOOP, f"«{REGISTO}» é uma ligação simbólica (ou não é um ficheiro) — não o substituo",
                                        REGISTO)
        except FileNotFoundError:
            st = os.fstat(dfd)
        tmp = f".{REGISTO}.{os.getpid()}.tmp"
        with contextlib.suppress(FileNotFoundError):
            os.unlink(tmp, dir_fd=dfd)                     # sobra de um processo morto com o mesmo pid
        fd = caminhos.abrir_ficheiro(dfd, tmp, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        try:
            try:
                os.fchmod(fd, 0o600)
                with contextlib.suppress(OSError):         # sem privilégio para mudar o dono: fica o nosso
                    os.fchown(fd, st.st_uid, st.st_gid)
                vista = memoryview(texto)
                while vista:
                    vista = vista[os.write(fd, vista):]
                os.fsync(fd)
            finally:
                os.close(fd)
            os.replace(tmp, REGISTO, src_dir_fd=dfd, dst_dir_fd=dfd)
        except BaseException:
            with contextlib.suppress(OSError):
                os.unlink(tmp, dir_fd=dfd)
            raise
        os.fsync(dfd)
    finally:
        os.close(dfd)


def _novo_id(lista) -> str:
    usados = {e["id"] for e in lista}
    while True:
        i = secrets.token_hex(8)
        if i not in usados:
            return i


def destino_livre(dados, espaco: str) -> str:
    """o caminho (relativo a `dados`) para onde vai a pasta de um espaço apagado: apagados/<espaco>-<AAAAMMDD-HHMMSS> (hora local),
    com -2, -3… se já existir (dois apagamentos no mesmo segundo não se misturam numa pasta só)."""
    base = f"{espaco}-{time.strftime('%Y%m%d-%H%M%S')}"
    nome, i = base, 1
    while os.path.lexists(Path(dados) / PASTA / nome):
        i += 1
        nome = f"{base}-{i}"
    return f"{PASTA}/{nome}"


def registar(dados, email: str, conta_id: str, espaco: str, pasta: str | None, apagado_em: str | None = None):
    """`utilizador apagar` (e `reaplicar`): grava a entrada ANTES de a pasta sair do sítio. Devolve (entrada, anterior): `anterior`
    é None se a entrada é nova, ou a cópia da que foi reaberta — para `desfazer` se o basalt.toml não chegar a mudar.
    Reabre-se uma entrada que já exista para a MESMA conta (mesmo e-mail e mesmo conta_id não vazio; com `apagado_em`, também a
    mesma data): uma conta reposta de uma cópia e apagada outra vez guarda a data do primeiro apagamento."""
    sha = email_sha256(email)
    with trinco(dados):
        lista = ler(dados)
        if apagado_em is not None:
            iguais = [x for x in lista if x["email_sha256"] == sha and x.get("conta_id", "") == conta_id and x["apagado_em"] == apagado_em]
        elif conta_id:
            iguais = sorted((x for x in lista if x["email_sha256"] == sha and x.get("conta_id", "") == conta_id),
                            key=lambda x: instante(x["apagado_em"]))
        else:
            iguais = []
        if iguais:
            e = iguais[0]
            anterior = dict(e)
            e.update(email=email, espaco=espaco, pasta=pasta, eliminado_em=None)
        else:
            anterior = None
            e = {"id": _novo_id(lista), "email_sha256": sha, "email": email, "conta_id": conta_id or "", "espaco": espaco,
                 "pasta": pasta, "apagado_em": apagado_em or carimbo(), "eliminado_em": None}
            lista.append(e)
        gravar(dados, lista)
        return dict(e), anterior


def desfazer(dados, ident: str, anterior: dict | None) -> None:
    """o apagamento não chegou a mudar o basalt.toml: a entrada volta ao que era (ou sai, se era nova)."""
    with trinco(dados):
        lista = ler(dados)
        if anterior is None:
            lista = [x for x in lista if x["id"] != ident]
        else:
            lista = [anterior if x["id"] == ident else x for x in lista]
        gravar(dados, lista)


def _actualizar(dados, ident: str, **campos) -> None:
    with trinco(dados):
        lista = ler(dados)
        for x in lista:
            if x["id"] == ident:
                x.update(campos)
        gravar(dados, lista)


# ───────────────────────────── listar ─────────────────────────────
def _sem_registo(dados, lista) -> list[str]:
    """as pastas de apagados/ a que nenhuma entrada por eliminar se refere (ex.: apagadas antes da r17): nunca se lhes mexe."""
    conhecidas = {e["pasta"] for e in lista if e.get("pasta")}
    try:
        nomes = sorted(n for n, tipo, _ in caminhos.listar_dir(Path(dados), PASTA))
    except (FileNotFoundError, NotADirectoryError, caminhos.Recusado):
        return []
    return [n for n in nomes if f"{PASTA}/{n}" not in conhecidas]


def listar(caminho: Path, dizer=print) -> int:
    cfg = config.carregar(caminho)
    caminhos.definir_ancora(cfg.dados)
    try:
        lista = ler(cfg.dados)
    except (RegistoInvalido, OSError) as e:
        print(f"basalt: não consigo ler {cfg.dados / REGISTO}: {e}", file=sys.stderr)
        return 1
    t = _agora()
    if not lista:
        dizer("(nenhuma conta apagada no registo)")
    for e in lista:
        dias = int((t - instante(e["apagado_em"])) // 86400)
        estado = f"eliminada a {e['eliminado_em']}" if e.get("eliminado_em") else "por eliminar"
        extra = f"  {e['email']}" if e.get("email") else ""
        dizer(f"{e['id']}  {e['apagado_em']}  há {dias:3} dia(s)  {estado}{extra}")
    for n in _sem_registo(cfg.dados, lista):
        dizer(f"{PASTA}/{n}: sem registo — não mexo (apagada antes da r17, ou à mão)")
    return 0


# ───────────────────────────── eliminar de vez ─────────────────────────────
def _dentro_de_apagados(dados: Path, rel: str) -> bool:
    """`rel` é uma pasta DIRECTAMENTE dentro de <dados>/apagados/, com os caminhos resolvidos (nenhuma ligação leva para fora)."""
    partes = rel.split("/")
    if len(partes) != 2 or partes[0] != PASTA or partes[1] in ("", ".", "..") or "\0" in rel:
        return False
    base = (dados / PASTA).resolve()
    return (dados / rel).resolve().parent == base and (dados / rel).resolve() != base


def _pasta_alvo(dados: Path, e: dict) -> str | None:
    """o caminho (relativo a `dados`) da pasta a apagar, já verificado — ou None se não há pasta (espaço partilhado, nada a mover,
    ou já não existe). Recusa: fora de apagados/, ligação simbólica, não é uma pasta ou o nome não bate com a entrada."""
    rel = e.get("pasta")
    if rel is None:
        return None
    if not _dentro_de_apagados(dados, rel):
        raise Recusa("a pasta da entrada não está dentro de apagados/ — não lhe mexo")
    nome = rel.split("/")[1]
    if not e.get("espaco") or not nome.startswith(e["espaco"] + "-"):
        raise Recusa("o nome da pasta não bate com a entrada — não lhe mexo")
    for p in (dados / PASTA, dados / rel):
        try:
            st = os.lstat(p)
        except FileNotFoundError:
            return None
        if stat.S_ISLNK(st.st_mode) or not stat.S_ISDIR(st.st_mode):
            raise Recusa(f"{p.relative_to(dados)} é uma ligação simbólica (ou não é uma pasta) — não lhe mexo")
    return rel


def _apagar_pasta(dados: Path, rel: str) -> None:
    """rmtree a partir do descritor da pasta-mãe, aberta sem seguir ligações (nada se troca entre a verificação e o apagar)."""
    pai, nome = rel.rsplit("/", 1) if "/" in rel else ("", rel)
    dfd = caminhos.abrir_pasta(dados, pai)
    try:
        shutil.rmtree(nome, dir_fd=dfd)
    finally:
        os.close(dfd)


def _conta_viva(cfg, sha: str):
    return next((u for u in cfg.utilizadores if email_sha256(u.email) == sha), None)


def _eliminar_uma(caminho: Path, dados: Path, ident: str, dizer) -> str:
    """uma entrada: 'eliminada', 'voltou' ou 'ja' (já não está por eliminar). Uma recusa ou um erro sobe (a entrada fica por
    eliminar e volta a tentar-se na próxima vez)."""
    from . import oauth
    from .auth import revogar_sessoes, trinco_toml
    with trinco(dados):
        e = next((x for x in ler(dados) if x["id"] == ident), None)
    if e is None or e.get("eliminado_em"):
        return "ja"
    alvo = _pasta_alvo(dados, e)
    email = e.get("email") or ""
    with trinco_toml(caminho):          # ninguém cria nem repõe uma conta a meio (o servidor e a linha de comandos esperam)
        viva = _conta_viva(config.carregar(caminho), e["email_sha256"])
        if viva is not None and viva.conta_id == (e.get("conta_id") or ""):
            return "voltou"
        # há uma conta NOVA com o mesmo e-mail (outro conta_id): só sai da base o que nasceu antes do apagamento, e as sessões dela ficam
        ate = None if viva is None else instante(e["apagado_em"]) + 1
        ligacoes = oauth.eliminar_na_base(dados, email, ate) if email else 0
        sessoes = revogar_sessoes(dados / "sessoes.json", email) if email and viva is None else 0
    if alvo is not None:
        _apagar_pasta(dados, alvo)
    _actualizar(dados, ident, eliminado_em=carimbo(), email=None, espaco=None, pasta=None)
    pasta = "pasta apagada" if alvo else ("sem pasta" if e.get("pasta") is None else "a pasta já não existia")
    nova = "; há uma conta nova com o mesmo e-mail — não lhe mexi" if viva is not None else ""
    dizer(f"{ident}: eliminada ({pasta}, {ligacoes} ligação(ões) OAuth, {sessoes} sessão(ões)){nova}")
    return "eliminada"


def _sem_email(texto: str, emails) -> str:
    for m in emails:
        if m:
            texto = re.sub(re.escape(m), "‹e-mail›", texto, flags=re.I)
    return texto


def eliminar(caminho: Path, dias: int = DIAS, sim: bool = False, dizer=print) -> int:
    """apaga de vez as entradas com MAIS de `dias` dias ainda por eliminar (sem `sim` só diz quais). Nunca escreve e-mails.
    Cada entrada é independente: uma que falhe não impede as outras (rc 1 no fim). A última linha é sempre o resumo."""
    eliminadas = falhas = voltaram = a_eliminar = 0
    emails = []
    try:
        if caminho is None:
            raise ValueError("não encontro o basalt.toml")
        if dias < 0:
            raise ValueError("--dias não pode ser negativo")
        cfg = config.carregar(caminho)
        dados = cfg.dados
        caminhos.definir_ancora(dados)
        with trinco(dados):
            lista = ler(dados)
        emails = [e.get("email") for e in lista]
        limite = _agora() - dias * 86400
        pendentes = [e for e in lista if not e.get("eliminado_em") and instante(e["apagado_em"]) < limite]
        a_eliminar = len(pendentes)
        for e in pendentes:
            if not sim:
                dias_ = int((_agora() - instante(e["apagado_em"])) // 86400)
                dizer(f"{e['id']}: apagada a {e['apagado_em']} (há {dias_} dias) — seria eliminada (com --sim)")
                continue
            try:
                r = _eliminar_uma(caminho, dados, e["id"], dizer)
            except Recusa as x:
                falhas += 1
                dizer(f"{e['id']}: recusado — {x}")
                continue
            except Exception as x:          # OSError, sqlite3.Error, oauth.Ocupado, RegistoInvalido…: esta entrada fica, as outras seguem
                falhas += 1
                dizer(f"{e['id']}: falhou ({x.__class__.__name__}: {_sem_email(str(x), emails)}) — fica para a próxima")
                continue
            if r == "eliminada":
                eliminadas += 1
            elif r == "voltou":
                voltaram += 1
                sem_id = "" if e.get("conta_id") else (" A entrada é de uma conta sem conta_id (anterior à r17): se a conta do "
                                                        "basalt.toml for OUTRA pessoa com o mesmo e-mail, dá-lhe um conta_id.")
                dizer(f"{e['id']}: a conta voltou (o mesmo e-mail e o mesmo conta_id estão no basalt.toml) — não elimino; se veio "
                      f"de uma cópia reposta, corre `apagados reaplicar`.{sem_id}")
        n = len(_sem_registo(dados, lista))
        if n:
            dizer(f"{n} pasta(s) em {PASTA}/ sem registo — não mexo (ver `basalt apagados listar`)")
        if not pendentes:
            dizer(f"nada por eliminar com mais de {dias} dias")
    except Exception as x:                  # o basalt.toml ou o registo não se lêem: nada feito, mas o resumo sai na mesma
        falhas += 1
        print(f"basalt: {_sem_email(str(x), emails)} — não mexi no registo", file=sys.stderr)
    finally:
        if sim:
            dizer(f"resumo: eliminadas={eliminadas} falhas={falhas} voltaram={voltaram}")
        else:
            dizer(f"resumo: a_eliminar={a_eliminar}")
    return 1 if falhas or (sim and voltaram) else 0


# ───────────────────────────── exportar e reaplicar (cópias de segurança) ─────────────────────────────
def exportar(caminho: Path, saida=None) -> int:
    saida = saida or sys.stdout
    cfg = config.carregar(caminho)
    caminhos.definir_ancora(cfg.dados)
    try:
        with trinco(cfg.dados):
            lista = ler(cfg.dados)
    except (RegistoInvalido, OSError) as e:
        print(f"basalt: não consigo ler {cfg.dados / REGISTO}: {e}", file=sys.stderr)
        return 1
    for e in lista:
        saida.write(json.dumps({"email_sha256": e["email_sha256"], "conta_id": e.get("conta_id", ""),
                                "apagado_em": e["apagado_em"]}) + "\n")
    saida.flush()
    return 0


def ler_lista(ficheiro) -> list[dict]:
    """as linhas do `exportar` (JSON Lines; campos a mais ignoram-se). Uma linha inválida → ValueError, antes de mexer em nada."""
    with open(ficheiro, encoding="utf-8") as f:
        linhas = f.read().splitlines()
    vistas, out = set(), []
    for i, ln in enumerate(linhas, 1):
        if not ln.strip():
            continue
        try:
            d = json.loads(ln)
        except ValueError:
            raise ValueError(f"a linha {i} da lista não é JSON") from None
        if not isinstance(d, dict):
            raise ValueError(f"a linha {i} da lista não é um objecto")
        sha, cid, quando = d.get("email_sha256"), d.get("conta_id", ""), d.get("apagado_em")
        if not isinstance(sha, str) or not _SHA_RX.fullmatch(sha) or not isinstance(cid, str) or instante(quando) is None:
            raise ValueError(f"a linha {i} da lista não tem email_sha256/conta_id/apagado_em válidos")
        chave = (sha, cid, quando)
        if chave not in vistas:
            vistas.add(chave)
            out.append({"email_sha256": sha, "conta_id": cid, "apagado_em": quando})
    return sorted(out, key=lambda d: instante(d["apagado_em"]))


def reaplicar(caminho: Path, ficheiro, sim: bool, apagar, dizer=print) -> int:
    """depois de repor uma cópia de segurança: cada linha do `exportar` cuja conta (mesmo e-mail E mesmo conta_id) está outra
    vez no basalt.toml volta a ser apagada, pelo mesmo caminho do `utilizador apagar` e com a data ORIGINAL; uma conta com o mesmo
    e-mail mas outro conta_id foi criada depois e não se lhe mexe; uma linha que falte no registo (e cuja conta não exista)
    acrescenta-se já anonimizada. O único admin nunca se apaga. Idempotente. `apagar(email, conta_id, apagado_em)` é o núcleo do
    `utilizador apagar` (SystemExit = falhou)."""
    reapagadas = recriadas = acrescentadas = protegidas = 0
    falhas = 0
    try:
        if caminho is None:
            raise ValueError("não encontro o basalt.toml")
        if not ficheiro:
            raise ValueError("falta --lista FICHEIRO (a saída de `basalt apagados exportar`)")
        linhas = ler_lista(ficheiro)
        cfg = config.carregar(caminho)
        dados = cfg.dados
        caminhos.definir_ancora(dados)
        for ln in linhas:
            sha, cid, quando = ln["email_sha256"], ln["conta_id"], ln["apagado_em"]
            rotulo = f"{sha[:12]} ({quando})"
            cfg = config.carregar(caminho)          # o apagamento anterior mudou o basalt.toml
            viva = _conta_viva(cfg, sha)
            if viva is not None:
                if viva.conta_id != cid:
                    recriadas += 1
                    dizer(f"{rotulo}: recriada depois do apagamento (outro conta_id) — não mexo")
                    continue
                if viva.admin and sum(1 for u in cfg.utilizadores if u.admin) <= 1:
                    protegidas += 1
                    dizer(f"{rotulo}: é o único admin — não a apago (cria outro admin e repete)")
                    continue
                if not sim:
                    reapagadas += 1
                    dizer(f"{rotulo}: a conta voltou com a cópia — seria apagada outra vez (com --sim)")
                    continue
                try:
                    apagar(viva.email, cid, quando)
                except SystemExit as x:
                    falhas += 1
                    dizer(f"{rotulo}: falhou — {_sem_email(str(x), [viva.email])}")
                    continue
                reapagadas += 1
                dizer(f"{rotulo}: apagada outra vez (data original {quando}; corre `apagados eliminar --sim`)")
                continue
            with trinco(dados):
                lista = ler(dados)
                if any(x["email_sha256"] == sha and x.get("conta_id", "") == cid and x["apagado_em"] == quando for x in lista):
                    continue                        # já está no registo: nada a fazer
                acrescentadas += 1
                if not sim:
                    dizer(f"{rotulo}: falta no registo — seria acrescentada já eliminada (com --sim)")
                    continue
                lista.append({"id": _novo_id(lista), "email_sha256": sha, "email": None, "conta_id": cid, "espaco": None,
                              "pasta": None, "apagado_em": quando, "eliminado_em": carimbo()})
                gravar(dados, lista)
            dizer(f"{rotulo}: faltava no registo — acrescentada já eliminada")
    except Exception as x:                  # a lista, o basalt.toml ou o registo não se lêem: pára aqui, o resumo sai na mesma
        falhas += 1
        print(f"basalt: {x} — parei aqui", file=sys.stderr)
    finally:
        if sim:
            dizer(f"resumo: reapagadas={reapagadas} recriadas={recriadas} acrescentadas={acrescentadas} protegidas={protegidas}")
        else:
            dizer(f"resumo: reapagaria={reapagadas} recriadas={recriadas} acrescentaria={acrescentadas} protegidas={protegidas}")
    return 1 if falhas or (sim and protegidas) else 0
