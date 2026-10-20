"""basalt.duasvias — sincronização nos DOIS sentidos entre a memória verdadeira e a cópia editável (editor/Obsidian).

Regras de ouro (a memória não se pode perder):
 • Um REGISTO de hashes (ledger) diz o que mudou desde a última vez, para saber a direcção de cada nota.
 • Antes de escrever QUALQUER coisa na memória real → fotografia git (para reverter).
 • Uma nota da cópia com um SEGREDO em claro NÃO é empurrada para a real (fica retida + alerta).
 • CONFLITO (mudou dos dois lados) → não se perde nada: a versão da cópia entra na real como `<nota>.CONFLITO-<ts>.md`
   e a real fica como está; o utilizador resolve à mão. Alerta.
 • APAGAR pela cópia NÃO apaga na real (um editor a apagar é perigoso) — a nota volta na sincronização seguinte.
   Apagar é só pela memória real (essa direcção propaga-se).

Uso:
  python -m basalt.duasvias            um ciclo (pull real→cópia + push cópia→real, com as regras acima)
  python -m basalt.duasvias --semear   fixa o registo pelos hashes da memória (1.ª vez que se liga)
  python -m basalt.duasvias --teste    suite de cenários em pastas temporárias (não toca em nada real)
Espaço: BASALT_ESPACO (+ basalt.toml / BASALT_DADOS); BASALT_REAL, BASALT_COPIA, BASALT_LEDGER, BASALT_TRINCO mandam campo a campo.
BASALT_GIT=1 (omissão): fotografia git da memória real antes de a alterar.
"""
import contextlib, fcntl, hashlib, json, os, re, subprocess, sys, tempfile, time
from pathlib import Path

from . import avisos, caminhos, cas, config, segredos
from .config import Via
from .segredos import SEGREDO_RX, VerificacaoFalhou, mascarar, tem_segredo_nota, tem_segredo_texto  # noqa: F401  (SEGREDO_RX: basalt.unir importa daqui)

REAL = Via("real")          # PORTE: antes caminhos fixos + variáveis; agora a pasta do espaço (config.Espaco; as variáveis antigas ainda mandam)
COPIA = Via("copia")
LEDGER = Via("ledger")
GIT = os.environ.get("BASALT_GIT", "1") == "1"
# trinco partilhado com a web (basalt.web) e com o cron de sincronização — todos os
# que escrevem na cópia. O ciclo segura-o do princípio ao fim. O editor/Obsidian (e os agentes, na real) escrevem SEM
# trinco: cada escrita é um CAS (basalt.cas: troca atómica + confirmação do que saiu);
# se alguém escreveu nesse instante, a escrita dele fica e a nota passa ao ciclo seguinte («adiados»).
TRINCO = Via("trinco")
# ficheiros do próprio editor/índice que NÃO são notas da memória
IGNORA = {"index.md", "CONFIG.md", "MEMORY.md"}


def h(p: Path):
    return hb(ler(p))


def hb(b):
    return hashlib.sha1(b).hexdigest() if b is not None else None


def ler(p: Path):
    try:
        return caminhos.ler(p)           # ronda 10 (G4 da r3): pelo descritor verificado — nem a nota nem as pastas acima dela são ligações
    except FileNotFoundError:
        return None


@contextlib.contextmanager
def trinco(espera=300):
    f = caminhos.abrir_trinco(TRINCO)    # ronda 13: sem seguir ligações
    try:
        fim = time.time() + espera
        while True:
            try:
                fcntl.flock(f, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.time() > fim:
                    raise TimeoutError(f"trinco {TRINCO} ocupado há {espera} s")
                time.sleep(0.05)
        yield
    finally:
        os.close(f)


def nota(nome):
    # as cópias de conflito («x.CONFLITO-<data>.md») ficam só na memória real — antes eram puxadas
    return (nome.endswith(".md") and ".bak" not in nome and ".CONFLITO-" not in nome and nome not in IGNORA
            and not nome.startswith("."))


def notas_de(d: Path):
    return {p.name for p in caminhos.notas(d) if nota(p.name)}      # ronda 8 (ligações simbólicas): uma nota simbólica não entra na sincronização


def tem_segredo(p: Path):
    """True se a nota tiver um segredo em claro — o mesmo critério das outras entradas (basalt.segredos: chaves, tokens,
    chaves privadas E passwords escritas à mão). PORTE: antes consultava também um cofre de passwords externo.
    ronda 13: e o [segredos] verificador, se configurado (VerificacaoFalhou se ele falhar: o ciclo adia a nota)."""
    return tem_segredo_nota(caminhos.ler_texto(p))


_CULPADAS: dict = {}     # ronda 13c: por cópia, as notas em que o verificador de segredos FALHOU no último ciclo (ver _ciclo)


def culpadas() -> set:
    """as notas desta cópia em que o [segredos] verificador falhou no último ciclo — vão para o FIM do ciclo seguinte, para
    uma nota em que ele falha sempre não adiar em todos os ciclos as que vêm depois dela."""
    return _CULPADAS.get(str(COPIA), set())


def tem_segredo_bytes(b, nome):
    """o mesmo que tem_segredo(), mas sobre os BYTES já lidos — o que se verifica é exactamente o que se escreve."""
    with tempfile.TemporaryDirectory(prefix="basalt2v-seg-") as d:
        q = Path(d) / nome
        q.write_bytes(b)
        return tem_segredo(q)


def git_foto(msg):
    if not GIT:
        return
    try:
        subprocess.run(["git", "-C", str(REAL), "add", "-A"], capture_output=True, timeout=30)
        subprocess.run(["git", "-C", str(REAL), *config.git_c(), "commit", "-q", "-m", msg], capture_output=True, timeout=30)
    except Exception as e:
        print(f"aviso: fotografia git falhou ({str(e)[:60]})", file=sys.stderr)


ALERTAS = os.environ.get("BASALT_ALERTAS", "1") == "1"     # o --teste desliga-os (não mandar avisos a sério por causa de notas de rascunho)


def alerta(msg):
    # ronda 13: no stderr como antes (o journal do systemd / o cron guardam-no) e, com [avisos] comando, também num comando teu
    if ALERTAS:
        avisos.alerta(msg)
    else:
        print("ALERTA:", mascarar(str(msg)), file=sys.stderr)


def ciclo():
    COPIA.mkdir(parents=True, exist_ok=True)
    with trinco(), segredos.uma_falha_por_ciclo():   # ronda 13b: o verificador falhou → as outras notas ficam adiadas sem nova chamada
        return _ciclo()


def _ciclo():
    try:
        led = json.loads(caminhos.ler_texto(LEDGER))       # ronda 13: uma ligação simbólica é recusada, não seguida
    except FileNotFoundError:
        led = {}
    nomes = notas_de(REAL) | notas_de(COPIA)
    empurrados, puxados, conflitos, retidos, apagados, adiados = [], [], [], [], [], []
    fez_foto = False
    trocas = []     # (troca, nome, valor novo do registo, lista onde conta) — confirmadas todas no fim, numa graça só

    def trocar(alvo, novo, esperado, nome, valor_led, lista):
        """escreve/apaga por CAS (basalt.cas): se alguém escreveu no ficheiro desde que o lemos, nada se sobrepõe."""
        t, _motivo = cas.trocar(alvo, novo, esperado, "2v")
        if t is None:
            adiados.append(nome)
        else:
            trocas.append((t, nome, valor_led, lista))

    # ronda 13c: a nota em que o verificador falhou no ciclo anterior vai para o FIM. Com «uma chamada falhada por ciclo» (13b)
    # e a ordem sempre igual, uma nota em que ele falha sempre (tempo esgotado só com ela, um erro só com ela) adiava em TODOS os
    # ciclos as notas a seguir a ela — essas nunca chegavam a sincronizar.
    copia_actual, atras = str(COPIA), culpadas()
    _CULPADAS[copia_actual] = set()
    for nome in sorted(nomes - atras) + sorted(nomes & atras):
        # ronda 13: se o verificador de segredos falhar, a nota fica para o ciclo seguinte (nada muda, o registo não avança)
        try:
            pr, pc = REAL / nome, COPIA / nome
            br, bc = ler(pr), ler(pc)
            rh, ch, lh = hb(br), hb(bc), led.get(nome)

            # nota nova/alterada na CÓPIA e a REAL não mudou desde a última vez → empurrar
            if ch and ch != lh and (rh == lh or (rh is None and lh is None)):
                if tem_segredo_bytes(bc, nome):
                    retidos.append(nome); continue
                if not fez_foto:
                    git_foto(f"basalt duasvias: antes de aceitar edições da cópia ({time.strftime('%F %H:%M')})"); fez_foto = True
                trocar(pr, bc, rh, nome, ch, empurrados); continue

            # a REAL mudou e a cópia não → puxar (comportamento normal), MAS uma nota real com segredo em claro nunca vai
            # para a cópia (o editor está na web); se já lá estava, é removida. O original na memória fica intacto.
            if rh and rh != lh and ch == lh:
                if tem_segredo_bytes(br, nome):
                    if ch is None:
                        led[nome] = rh; retidos.append(nome)
                    else:
                        trocar(pc, None, ch, nome, rh, retidos)
                    continue
                trocar(pc, br, ch, nome, rh, puxados); continue

            # apagada só na CÓPIA → volta (a regra de ouro: apagar pela cópia não apaga). Antes ficava de fora para
            # sempre e a web passava a escondê-la como se tivesse segredo. Uma nota real com segredo continua fora da cópia.
            if rh and ch is None:
                if tem_segredo_bytes(br, nome):
                    led[nome] = rh; retidos.append(nome); continue
                trocar(pc, br, None, nome, rh, puxados); continue      # CAS: só se a cópia continuar a não existir

            # apagada na REAL (e a cópia não a alterou) → apagar na cópia
            if rh is None and ch is not None and ch == lh:
                trocar(pc, None, ch, nome, None, apagados); continue

            # CONFLITO: mudou dos dois lados e ficaram diferentes → guardar a versão da cópia à parte, não perder nada
            if rh and ch and rh != ch and rh != lh and ch != lh:
                if tem_segredo_bytes(bc, nome):
                    retidos.append(nome); continue
                seg_r = tem_segredo_bytes(br, nome)
                if not fez_foto:
                    git_foto(f"basalt duasvias: antes de guardar conflito ({time.strftime('%F %H:%M')})"); fez_foto = True
                alvo = REAL / f"{nome[:-3]}.CONFLITO-{time.strftime('%Y%m%d-%H%M%S')}.md"
                if cas.escrever(alvo, bc, None, "2v"):          # nome único; se já existe (2 no mesmo segundo) fica p/ depois
                    adiados.append(nome); continue
                # a cópia passa a mostrar a real (a versão da cópia já está a salvo no .CONFLITO). Antes a cópia ficava
                # diferente e o ciclo seguinte EMPURRAVA-A por cima da real — perdia-se a mudança do agente.
                trocar(pc, None if seg_r else br, ch, nome, rh, conflitos); continue

            # sem mudança relevante: alinhar o ledger com o estado actual (quando os dois lados já estão iguais)
            if rh and rh == ch:
                led[nome] = rh
        except VerificacaoFalhou as e:
            adiados.append(nome)
            if not e.adiada:                       # ronda 13c: foi ESTA que fez o verificador falhar — no próximo ciclo vai para o fim
                _CULPADAS[copia_actual].add(nome)

    # confirmar as trocas. A que falhar (alguém escreveu nesse instante) fica com a escrita dessa pessoa e o
    # registo NÃO avança → o ciclo seguinte vê as duas mudanças e trata-as (empurrar, puxar ou conflito), sem perder nada.
    falhas = {id(t) for t in cas.confirmar([x[0] for x in trocas])}
    salvos = []
    for t, nome, valor_led, lista in trocas:
        if id(t) in falhas:
            adiados.append(nome)
            if t.salvo:
                salvos.append(str(t.salvo))
            continue
        if valor_led is None:
            led.pop(nome, None)
        else:
            led[nome] = valor_led
        lista.append(nome)
    if salvos:
        alerta(f"escritas ao mesmo tempo na mesma nota — a outra versão ficou guardada em: {', '.join(salvos)}")

    caminhos.escrever_atomico(LEDGER, json.dumps(led, ensure_ascii=False, indent=0).encode("utf-8"))
    if retidos:
        alerta(f"{len(retidos)} nota(s) com segredo em claro NÃO aceites da cópia: {', '.join(retidos)}")
    if conflitos:
        alerta(f"{len(conflitos)} conflito(s) — versão da cópia guardada como *.CONFLITO-*.md na memória: {', '.join(conflitos)}")
    return dict(empurrados=empurrados, puxados=puxados, conflitos=conflitos, retidos=retidos, apagados=apagados, adiados=adiados)


# ─────────────────────────── testes (pastas temporárias; não tocam em nada real) ───────────────────────────
def _teste():
    import tempfile, shutil
    base = Path(tempfile.mkdtemp(prefix="basalt2v-"))
    real, copia = base / "real", base / "copia"
    real.mkdir(); copia.mkdir()
    (real / ".git").mkdir()  # marca para o git -C não explodir; usamos BASALT_GIT=0 no teste
    env = dict(REAL=real, COPIA=copia, LEDGER=base / "ledger.json")
    global REAL, COPIA, LEDGER, GIT, TRINCO, ALERTAS
    REAL, COPIA, LEDGER, GIT, TRINCO, ALERTAS = real, copia, env["LEDGER"], False, base / "copia.lock", False
    cas.SALVOS, cas.GRACA = base / "cas-salvos", 0.05
    ok = True

    def w(d, n, t): (d / n).write_text(t, encoding="utf-8")
    def existe(d, n): return (d / n).exists()
    def ler(d, n): return (d / n).read_text(encoding="utf-8")
    def check(cond, msg):
        nonlocal ok
        if not cond: ok = False; print("  FALHA:", msg)

    # começar: uma nota igual dos dois lados, ledger alinhado
    w(real, "a.md", "conteudo A"); w(copia, "a.md", "conteudo A")
    ciclo()

    # 1) editar na CÓPIA → empurra para a real
    w(copia, "a.md", "conteudo A editado no telemovel")
    r = ciclo()
    check("a.md" in r["empurrados"], "editar na cópia devia empurrar")
    check(ler(real, "a.md") == "conteudo A editado no telemovel", "a real devia ter a edição da cópia")

    # 2) editar na REAL (um agente) → puxa para a cópia
    w(real, "a.md", "conteudo A mudado por um agente")
    r = ciclo()
    check("a.md" in r["puxados"], "editar na real devia puxar")
    check(ler(copia, "a.md") == "conteudo A mudado por um agente", "a cópia devia receber a edição da real")

    # 3) nota NOVA na cópia → cria na real
    w(copia, "nova.md", "ideia nova escrita no editor")
    r = ciclo()
    check("nova.md" in r["empurrados"] and existe(real, "nova.md"), "nota nova na cópia devia ir para a real")

    # 4) CONFLITO: mudar dos dois lados entre ciclos
    w(real, "a.md", "versao REAL depois do conflito")
    w(copia, "a.md", "versao COPIA depois do conflito")
    r = ciclo()
    check("a.md" in r["conflitos"], "devia detectar conflito")
    check(ler(real, "a.md") == "versao REAL depois do conflito", "a real NÃO se perde num conflito")
    conf = list(real.glob("a.CONFLITO-*.md"))
    check(len(conf) == 1 and "versao COPIA" in conf[0].read_text(), "a versão da cópia fica guardada à parte")

    # 5) SEGREDO na cópia → retido, não vai para a real
    w(real, "seg.md", "nota normal"); w(copia, "seg.md", "nota normal"); ciclo()
    w(copia, "seg.md", "olha a chave sk-ant-" + "x" * 30)
    r = ciclo()
    check("seg.md" in r["retidos"] and "sk-ant" not in ler(real, "seg.md"), "segredo na cópia NÃO devia ir para a real")

    # 6) APAGAR na cópia NÃO apaga na real
    w(real, "b.md", "manter"); w(copia, "b.md", "manter"); ciclo()
    (copia / "b.md").unlink()
    r = ciclo()
    check(existe(real, "b.md"), "apagar na cópia NÃO devia apagar na real")

    # 7) APAGAR na real apaga na cópia
    w(real, "c.md", "x"); w(copia, "c.md", "x"); ciclo()
    (real / "c.md").unlink()
    r = ciclo()
    check(not existe(copia, "c.md") and "c.md" in r["apagados"], "apagar na real devia apagar na cópia")

    # 8) segredo numa nota da REAL NÃO chega à cópia (web); se lá estava, sai
    w(real, "d.md", "limpa"); w(copia, "d.md", "limpa"); ciclo()
    w(real, "d.md", "agora com AKIA" + "ABCDEFGHIJKLMNOP")   # padrão AWS
    r = ciclo()
    check(not existe(copia, "d.md"), "nota real com segredo NÃO devia ir/ficar na cópia")
    check(existe(real, "d.md"), "o original com segredo continua na memória real")

    # 9) depois de um CONFLITO, o ciclo seguinte NÃO pode empurrar a cópia por cima da real (perdia-se o agente)
    w(real, "e.md", "e0"); w(copia, "e.md", "e0"); ciclo()
    w(real, "e.md", "e AGENTE"); w(copia, "e.md", "e TELEMOVEL")
    r = ciclo()
    check("e.md" in r["conflitos"], "devia detectar o conflito em e.md")
    ciclo()
    check(ler(real, "e.md") == "e AGENTE", "a real perdeu a versão do agente no ciclo a seguir ao conflito")
    conf = list(real.glob("e.CONFLITO-*.md"))
    check(len(conf) == 1 and conf[0].read_text() == "e TELEMOVEL", "a versão da cópia devia ficar no .CONFLITO")

    # 10) escreve-se na cópia DURANTE a verificação de segredos de um «puxar» → essa escrita não se perde
    w(real, "f.md", "f0"); w(copia, "f.md", "f0"); ciclo()
    w(real, "f.md", "f AGENTE")
    original = globals()["tem_segredo"]
    estado = {"trinco_livre": None}

    def seg_com_escrita(q):
        import fcntl as _f
        with open(TRINCO, "a+") as t:                   # alguém que respeita o trinco não consegue entrar a meio
            try:
                _f.flock(t, _f.LOCK_EX | _f.LOCK_NB); estado["trinco_livre"] = True
            except BlockingIOError:
                estado["trinco_livre"] = False
        if q.name == "f.md" and not (copia / "f.md").read_text().startswith("f EDITOR"):
            w(copia, "f.md", "f EDITOR (editor a meio)")   # quem não usa o trinco (o editor) escreve na mesma
        return original(q)
    globals()["tem_segredo"] = seg_com_escrita
    try:
        r = ciclo()
    finally:
        globals()["tem_segredo"] = original
    check(estado["trinco_livre"] is False, "o trinco devia estar seguro durante a verificação de segredos")
    check(ler(copia, "f.md").startswith("f EDITOR"), "a escrita feita na cópia durante a verificação foi sobreposta")
    ciclo()
    guardada = ler(copia, "f.md").startswith("f EDITOR") or any(
        q.read_text().startswith("f EDITOR") for q in real.glob("f.CONFLITO-*.md"))
    check(guardada and ler(real, "f.md") == "f AGENTE", "a edição do editor ou a do agente perdeu-se")

    # 11) o editor grava DEPOIS de todas as verificações, mesmo antes da escrita do
    #     vigia → a escrita dele fica na cópia e, no ciclo seguinte, vira conflito (nada se perde dos dois lados)
    w(real, "g.md", "g0"); w(copia, "g.md", "g0"); ciclo()
    w(real, "g.md", "g AGENTE")
    nada = cas.antes_de_trocar, cas.durante_graca

    def ed_antes(alvo):
        if alvo == copia / "g.md":
            w(copia, "g.md", "g EDITOR NOVO")
    cas.antes_de_trocar = ed_antes
    try:
        r = ciclo()
    finally:
        cas.antes_de_trocar, cas.durante_graca = nada
    check("g.md" not in r["puxados"] and ler(copia, "g.md") == "g EDITOR NOVO",
          "a escrita do editor mesmo antes da troca foi sobreposta pelo puxar")
    ciclo()
    check(ler(real, "g.md") == "g AGENTE" and any(q.read_text() == "g EDITOR NOVO" for q in real.glob("g.CONFLITO-*.md")),
          "depois do 2.º ciclo, a versão do agente ou a do editor perdeu-se")

    # 12) quem já tinha o ficheiro aberto acaba de escrever no ficheiro ANTIGO depois da troca
    w(real, "k.md", "k0"); w(copia, "k.md", "k0"); ciclo()
    w(real, "k.md", "k AGENTE")
    aberto = {}

    def abrir(alvo):
        if alvo == copia / "k.md":
            aberto["f"] = open(alvo, "r+b")

    def escreve_no_antigo(alvo):
        f = aberto.pop("f", None)
        if f:
            f.seek(0); f.truncate(); f.write("k EDITOR LENTO".encode()); f.close()
    cas.antes_de_trocar, cas.durante_graca = abrir, escreve_no_antigo
    try:
        r = ciclo()
    finally:
        cas.antes_de_trocar, cas.durante_graca = nada
    check("k.md" in r["adiados"] and ler(copia, "k.md") == "k EDITOR LENTO",
          "a escrita no ficheiro antigo (aberto antes da troca) perdeu-se")

    # 13) um agente grava na REAL mesmo antes de o vigia empurrar a edição da cópia → a do agente fica
    w(real, "m.md", "m0"); w(copia, "m.md", "m0"); ciclo()
    w(copia, "m.md", "m TELEMOVEL")

    def agente_antes(alvo):
        if alvo == real / "m.md":
            w(real, "m.md", "m AGENTE AO MESMO TEMPO")
    cas.antes_de_trocar = agente_antes
    try:
        r = ciclo()
    finally:
        cas.antes_de_trocar, cas.durante_graca = nada
    check(ler(real, "m.md") == "m AGENTE AO MESMO TEMPO", "a escrita do agente na real foi sobreposta pelo empurrar")
    ciclo()
    check(any(q.read_text() == "m TELEMOVEL" for q in real.glob("m.CONFLITO-*.md")), "a edição da cópia perdeu-se")

    shutil.rmtree(base, ignore_errors=True)
    print("basalt.duasvias autoteste:", "OK" if ok else "FALHOU")
    return ok


def semear():
    """Fixa o registo (ledger) pelos hashes da memória REAL, sem copiar nada. Corre-se UMA vez ao ligar os 2 sentidos,
    para o 1.º ciclo não tratar notas já iguais como «mudadas dos dois lados» (falso conflito)."""
    led = {}
    for nome in notas_de(REAL):
        led[nome] = h(REAL / nome)
    for nome in notas_de(COPIA):                 # notas que só existem na cópia ficam por empurrar no 1.º ciclo
        if nome not in led:
            led.setdefault(nome, None)
    LEDGER.parent.mkdir(parents=True, exist_ok=True)
    LEDGER.write_text(json.dumps(led, ensure_ascii=False, indent=0))
    print(f"registo semeado com {sum(1 for v in led.values() if v)} notas da memória real")


def main(argv=None):
    argv = sys.argv[1:] if argv is None else argv
    if "--teste" in argv:
        return 0 if _teste() else 1
    config.definir_processo(config.espaco_de_processo())
    if "--semear" in argv:
        semear()
        return 0
    r = ciclo()
    print(" · ".join(f"{k}:{len(v)}" for k, v in r.items()))
    return 0


if __name__ == "__main__":
    sys.exit(main())
