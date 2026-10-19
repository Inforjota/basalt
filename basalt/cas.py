"""basalt.cas — escrever uma nota SÓ se ela ainda tem o conteúdo que se verificou (compare-and-swap), à prova de quem
escreve sem trinco: o Obsidian (ou outro editor) na cópia e os agentes na memória real.

Princípio: «acrescentar outra leitura apenas desloca a janela». Aqui não há janela entre
verificar e escrever: troca-se o ficheiro inteiro de uma vez (renameat2 RENAME_EXCHANGE, atómico no ZFS ≥ 2.2) e verifica-se
o que SAIU. Se não era o que se esperava, alguém escreveu entretanto → destroca-se e o ficheiro volta a ter a escrita dessa
pessoa (a nossa fica sem efeito e quem chama trata-a como «adiada»). A graça (0,3 s) apanha também quem já tinha o ficheiro
aberto e acabou de escrever no ficheiro antigo depois da troca.
Usado por basalt.duasvias e basalt.web (o cron de sincronização passa pelo duasvias).
Reposição: verifica-se o que SAIU na troca de volta (uma 3.ª escrita que entre
nesse instante fica guardada, não se apaga às cegas); o que vai para SALVOS vai por rename (o inode inteiro, mesmo que ainda
estejam a escrever nele), e SALVOS tem limite (dias e ficheiros) e não repete o mesmo conteúdo.
A reposição também espera a graça antes de olhar ao que saiu (uma 3.ª
escrita que ainda estivesse a decorrer no inode trocado perdia-se); em SALVOS a idade conta desde que se guardou (o rename
trazia o mtime da nota — uma nota de 90 dias era podada no mesmo instante) e repete-se só o que tem o MESMO conteúdo agora
(antes comparava-se o hash no nome, dado quando o ficheiro podia estar a meio de ser escrito: apagava-se a versão completa).
"""
import ctypes, errno, hashlib, os, re, tempfile, time
from pathlib import Path

from . import caminhos
from .config import Via

_libc = ctypes.CDLL(None, use_errno=True)
TEM_RENAMEAT2 = hasattr(_libc, "renameat2")      # só Linux (glibc/musl); o macOS e o Windows não a têm → troca_suportada() explica
if TEM_RENAMEAT2:
    _libc.renameat2.argtypes = [ctypes.c_int, ctypes.c_char_p, ctypes.c_int, ctypes.c_char_p, ctypes.c_uint]
_AT_FDCWD, _NOREPLACE, _EXCHANGE = -100, 1, 2
GRACA = float(os.environ.get("BASALT_CAS_GRACA", "0.3"))
SALVOS = Via("cas_salvos")        # PORTE: antes BASALT_CAS_SALVOS ou ~/.local/share; agora a pasta do espaço (a variável ainda vence, ver config.Espaco.de)
SALVOS_DIAS = int(os.environ.get("BASALT_CAS_SALVOS_DIAS", "60"))     # cópias salvas com mais de 60 dias saem
SALVOS_MAX = int(os.environ.get("BASALT_CAS_SALVOS_MAX", "500"))      # e nunca mais de 500 (ficam as mais novas)


def _renameat2(a, b, flags):
    if not TEM_RENAMEAT2:
        raise OSError(errno.ENOSYS, "renameat2 não existe neste sistema (só Linux)", str(b))
    if _libc.renameat2(_AT_FDCWD, os.fsencode(str(a)), _AT_FDCWD, os.fsencode(str(b)), flags) != 0:
        e = ctypes.get_errno()
        raise OSError(e, os.strerror(e), str(b))


def troca_suportada(pasta) -> str | None:
    """Prova, na própria pasta, as duas trocas de que o CAS depende (criar sem sobrepor e trocar dois ficheiros de uma vez).
    None = tudo bem; senão o motivo, para o arranque parar com uma mensagem clara em vez de falhar na 1.ª gravação.
    Falham, p. ex.: macOS/Windows sem Linux, e no WSL as pastas do Windows (/mnt/c/…)."""
    if not TEM_RENAMEAT2:
        return "este sistema não é Linux (falta a renameat2)"
    d = Path(tempfile.mkdtemp(prefix=".basalt-troca-", dir=pasta))
    a, b, c = d / "a", d / "b", d / "c"
    try:
        a.write_bytes(b"a"); b.write_bytes(b"b")
        _renameat2(a, b, _EXCHANGE)
        if a.read_bytes() != b"b" or b.read_bytes() != b"a":
            return "a troca atómica não trocou os ficheiros"
        _renameat2(a, c, _NOREPLACE)
        if a.exists() or c.read_bytes() != b"b":
            return "o rename sem sobrepor não se comportou como devia"
        return None
    except OSError as e:
        return f"{e.strerror or e} ({errno.errorcode.get(e.errno, e.errno)})"
    finally:
        for f in (a, b, c):
            try:
                f.unlink()
            except FileNotFoundError:
                pass
        d.rmdir()


def sha(b):
    return hashlib.sha1(b).hexdigest() if b is not None else None


def _ler(p):
    try:
        return Path(p).read_bytes()
    except FileNotFoundError:
        return None


def antes_de_trocar(alvo):
    """gancho para os testes: simular quem escreve sem trinco mesmo antes da troca."""


def durante_graca(alvo):
    """gancho para os testes: simular quem tinha o ficheiro aberto e escreve no antigo depois da troca."""


def durante_reposicao(alvo):
    """gancho para os testes (reposição): simular quem escreve no ficheiro que saiu na troca de volta."""


class Troca:
    def __init__(self, alvo, esperado, novo_hash, saiu):
        self.alvo, self.esperado, self.novo_hash, self.saiu = Path(alvo), esperado, novo_hash, saiu
        self.intruso, self.salvo = None, None


def _tmp(alvo, rotulo):
    return alvo.with_name(f".{alvo.name}.{rotulo}-{os.getpid()}-{time.monotonic_ns()}.tmp")


def trocar(alvo, novo, esperado, rotulo="cas"):
    """põe `novo` (bytes; None = apagar) em `alvo`, que tinha o hash `esperado` (None = não existia).
    Devolve (Troca, "") — falta confirmar() — ou (None, motivo) se nem trocou (o alvo fica como estava)."""
    alvo = Path(alvo)
    # ronda 10 (achado G4 da r3): nem a nota nem nenhuma pasta acima dela (até à âncora, a raiz de dados) pode ser uma ligação
    # simbólica — `A/copia` → `B/copia` escrevia na conta B. A troca continua por caminho (renameat2); verifica-se imediatamente antes.
    try:
        caminhos.pasta_contida(alvo.parent)
        if os.path.islink(alvo):
            raise caminhos.Recusado(40, "ligação simbólica", str(alvo))
    except caminhos.Recusado:
        return None, "a nota ou a pasta dela é uma ligação simbólica — não escrevo"
    if esperado is None:
        if novo is None:
            return None, "nada a fazer"
        t = _tmp(alvo, rotulo)
        t.write_bytes(novo)
        antes_de_trocar(alvo)
        try:
            _renameat2(t, alvo, _NOREPLACE)       # criar: só se ninguém a criou entretanto
        except FileExistsError:
            t.unlink()
            return None, "a nota apareceu entretanto"
        return Troca(alvo, None, sha(novo), None), ""
    t = _tmp(alvo, rotulo)
    if novo is None:
        antes_de_trocar(alvo)
        try:
            os.rename(alvo, t)                      # apagar = tirar do sítio e confirmar o que saiu
        except FileNotFoundError:
            return None, "a nota já não existia"
    else:
        t.write_bytes(novo)
        antes_de_trocar(alvo)
        try:
            _renameat2(t, alvo, _EXCHANGE)
        except FileNotFoundError:
            t.unlink()
            return None, "a nota desapareceu entretanto"
    return Troca(alvo, esperado, sha(novo), t), ""


def _podar():
    """SALVOS não cresce sem limite — fora o que tem mais de SALVOS_DIAS dias e, acima de SALVOS_MAX, os mais antigos."""
    try:
        fich = sorted((q for q in SALVOS.iterdir() if q.is_file() and not q.name.startswith(".")), key=lambda q: q.stat().st_mtime)
    except OSError:
        return
    limite = time.time() - SALVOS_DIAS * 86400
    fora = [q for q in fich if q.stat().st_mtime < limite]
    resto = [q for q in fich if q not in fora]
    fora += resto[:max(0, len(resto) - SALVOS_MAX)]
    for q in fora:
        try:
            q.unlink()
        except OSError:
            pass


def _guardar_a_parte(t):
    """O ficheiro que saiu passa INTEIRO para SALVOS por rename (mesmo inode, sem copiar) — quem ainda o tiver aberto
    e acabar de escrever depois de o termos lido escreve já no sítio guardado. Antes copiava-se o que se tinha lido (podia
    estar a meio) e apagava-se o inode (a versão completa perdia-se). Devolve o caminho guardado (None se já não havia nada)."""
    SALVOS.mkdir(parents=True, exist_ok=True)
    tmp = SALVOS / f".{t.alvo.stem}.{os.getpid()}-{time.monotonic_ns()}.tmp"
    try:
        os.rename(t.saiu, tmp)
    except FileNotFoundError:
        return None
    except OSError:                                   # outro sistema de ficheiros: copiar, e só apagar o original se ficou igual
        dados = _ler(t.saiu)
        if dados is None:
            return None
        tmp.write_bytes(dados)
        if _ler(t.saiu) == dados:
            t.saiu.unlink()
    dados = _ler(tmp)
    h = (sha(dados) or "vazio")[:10]
    carimbo = time.strftime("%Y%m%d-%H%M%S")
    destino = SALVOS / f"{t.alvo.stem}.{carimbo}.{h}{t.alvo.suffix}"
    n = 0
    while destino.exists():
        n += 1
        destino = SALVOS / f"{t.alvo.stem}.{carimbo}-{n}.{h}{t.alvo.suffix}"
    os.rename(tmp, destino)
    try:
        os.utime(destino, None)                       # a idade em SALVOS conta desde AGORA, não desde o mtime da nota
    except OSError:
        pass
    # o mesmo conteúdo já estava guardado: fica só o mais novo. por CONTEÚDO (o hash no nome foi dado quando o
    # ficheiro podia estar a meio de ser escrito; o que lá está agora pode ser a versão completa) e só o que assentou há ≥ 1 s.
    dados = _ler(destino)
    limite = time.time() - max(1.0, 2 * GRACA)
    mesmo = re.compile(rf"^{re.escape(t.alvo.stem)}\.\d{{8}}-\d{{6}}(?:-\d+)?\.(?:[0-9a-f]{{10}}|vazio){re.escape(t.alvo.suffix)}$")
    for outro in SALVOS.iterdir():
        if outro == destino or not mesmo.match(outro.name):
            continue
        try:
            if outro.stat().st_mtime <= limite and _ler(outro) == dados:
                outro.unlink()
        except OSError:
            pass
    _podar()
    return destino


def confirmar(trocas, graca=None):
    """espera a graça UMA vez e confirma cada troca. Devolve as que falharam: aí o alvo voltou a ter a escrita do intruso
    (t.intruso), ou — se o alvo também já mudou outra vez — o intruso ficou guardado em t.salvo. Nada se perde."""
    pend = [t for t in trocas if t is not None and t.saiu is not None]
    for t in pend:
        durante_graca(t.alvo)
    if pend:
        time.sleep(GRACA if graca is None else graca)
    falhas = []
    for t in pend:
        saiu = _ler(t.saiu)
        if sha(saiu) == t.esperado:
            t.saiu.unlink()
            continue
        t.intruso = saiu
        falhas.append(t)
        reposto = False
        try:
            if sha(_ler(t.alvo)) == t.novo_hash:          # o alvo ainda tem o nosso → devolver-lhe o do intruso
                if t.novo_hash is None:
                    _renameat2(t.saiu, t.alvo, _NOREPLACE)
                    reposto = True
                else:
                    _renameat2(t.saiu, t.alvo, _EXCHANGE)
                    reposto = True
                    # a 3.ª escrita pode ainda estar A DECORRER (abriu o ficheiro antes desta troca e acaba de escrever uns ms
                    # depois, já no inode que saiu) — a mesma graça da 1.ª troca, antes de olhar ao que saiu. Antes lia-se logo e apagava-se.
                    durante_reposicao(t.alvo)
                    time.sleep(GRACA if graca is None else graca)
                    # entre a verificação de cima e esta troca pode ter entrado uma 3.ª escrita — é ela que saiu
                    # agora. Só se apaga o que saiu se for mesmo o nosso; senão fica guardado à parte (antes apagava-se às cegas).
                    if sha(_ler(t.saiu)) == t.novo_hash:
                        t.saiu.unlink()
                    else:
                        t.salvo = _guardar_a_parte(t)
        except OSError:
            pass
        if not reposto:                                   # o alvo mudou outra vez: o intruso fica guardado à parte
            t.salvo = _guardar_a_parte(t)
    return falhas


def escrever(alvo, novo, esperado, rotulo="cas", graca=None):
    """trocar + confirmar de uma vez. Devolve "" se ficou escrito, senão o motivo (e nada foi sobreposto)."""
    t, motivo = trocar(alvo, novo, esperado, rotulo)
    if t is None:
        return motivo
    f = confirmar([t], graca)
    if not f:
        return ""
    return "alguém gravou esta nota no mesmo instante" + (f" (guardada em {f[0].salvo})" if f[0].salvo else "")
