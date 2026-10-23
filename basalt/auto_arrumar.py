"""basalt.auto_arrumar — arruma sozinho, de noite, as «notas muito parecidas» da memória.

Para cada par parecido (basalt.arrumar), a IA da tarefa «arrumar» decide ([ia.arrumar] por cima de [ia]; a fusão é da de [ia.unir]):
  ligar    mesmo projecto, partes diferentes → põe [[ligação]] nas duas (só acrescenta; nada se perde)
  arquivar uma é registo velho (sessão/snapshot) da outra → a principal ganha «Histórico: [[velha]]» e a velha sai do MEMORY.md,
           SÓ se: confiança ≥ 0.85, nenhuma mexida nas últimas 6 h, nenhuma ⭐⭐⭐, a velha não tem nenhum facto (número, data,
           caminho, `código`, URL — o mesmo contador do unir) que a principal não tenha; máx. 3 por dia. Senão → ligar (rondas 19 e 20).
  unir     dizem o mesmo → funde (basalt.unir, como o botão Unir), SÓ se: confiança ≥ 0.85, nenhuma mexida nas últimas 6 h,
           nenhuma ⭐⭐⭐, juntas ≤ 30 000 caracteres, verificação com 100 % dos factos; máx. 3 por noite. Senão → ligar.
  deixar   já ligadas / não relacionadas
Dúvida (confiança < 0.6, fora de [0, 1] ou resposta estragada) → não mexe; vai para o resumo, para o dono da memória decidir.
Pares com uma nota já arquivada não voltam à IA — seja com que nota for: as que têm a marca «🗄 Arquivada» e as que alguma nota
aponta por «Histórico: [[X]]» (ronda 20; a ronda 19 só via o par principal↔velha). Pares em que uma nota deixou de existir a meio
da noite (uma fusão apagou-a, ou alguém a apagou enquanto a IA pensava) saltam-se (rondas 19 e 20).
Cada alteração: fotografia git antes + commit + «trabalho» em <dados>/unir/ → aparece na página (Arrumar → últimas fusões) com
botão Desfazer. Resumo: <dados>/auto-arrumar/ultimo.json (e no stdout). Pares já decididos não voltam a ser avaliados durante 30 dias.

  python -m basalt.auto_arrumar [--simular]      --simular: só mostra o que faria (não grava)
Pensado para correr de noite (cron/systemd timer), depois de `python -m basalt.sig indexar`. Precisa de uma IA ([ia] ou [ia.arrumar] fornecedor ≠ «nenhum»);
sem IA não faz nada (e não deixa a memória a meio).
"""
import datetime, hashlib, json, os, re, subprocess, sys, time, uuid
from pathlib import Path

from . import arrumar as arr, caminhos, cas, config, retidas, unir
from . import ia as _ia
from .config import Via

REAL = Via("real")                      # PORTE: antes caminhos fixos; agora as pastas do espaço activo
ESTADO = Via("auto_arrumar")
JOBS = Via("jobs_unir")
MAX_UNIR, MAX_CHARS, HORAS_QUIETA, DIAS_DECIDIDO = 3, 30000, 6, 30
MAX_ARQUIVAR = 3                        # ronda 19: o arquivar tem os mesmos travões do unir
MARCA_ARQUIVADA = "🗄 Arquivada"
HIST_RX = re.compile(r"Histórico: \[\[([^\]|#]+)")   # o que arquivar() escreve na principal (e o único sinal dos arquivos de 25/09)
SIMULAR = False
HOJE = datetime.date.today().isoformat()


def ler(n):
    return caminhos.ler(REAL / f"{n}.md").decode("utf-8")


def _ler2(n):
    """(texto, hash do que se leu) — o hash é o «esperado» do CAS na escrita que se segue."""
    b = caminhos.ler(REAL / f"{n}.md")
    return b.decode("utf-8"), cas.sha(b)


def _escrever(n, texto, esperado):
    """troca a nota só se ninguém a mudou desde que a lemos (ronda 8, escrita sem sobrepor); se mudou, não sobrepõe nada e diz porquê."""
    motivo = cas.escrever(REAL / f"{n}.md", texto.encode("utf-8"), esperado, "auto-arrumar")
    if motivo:
        print(f"  não mexi em {n}: {motivo}", file=sys.stderr)
        return False
    return True


def desc(t):
    m = re.search(r"^description:\s*(.*)$", t, re.M)
    return (m.group(1).strip().strip('"') if m else "")[:300]


def corpo(t):
    return re.sub(r"^---\n.*?\n---\n", "", t, count=1, flags=re.S)


def _ja_arquivadas(a, b, ta, tb):
    """o par já foi decidido por quem arquivou: uma tem a marca «🗄 Arquivada», OU a outra tem «Histórico: [[esta]]» (é o que o
    arquivar() escreve na principal; os arquivos de 25/09 só deixaram este sinal — a velha não tem marca e voltava à IA)."""
    return MARCA_ARQUIVADA in ta or MARCA_ARQUIVADA in tb or f"Histórico: [[{b}]]" in ta or f"Histórico: [[{a}]]" in tb


def _nomes_arquivados():
    """ronda 20: as notas já arquivadas, vistas na memória TODA uma vez por corrida — as que têm a marca «🗄 Arquivada» e os alvos
    de «Histórico: [[X]]» em qualquer nota. Um par com uma delas não vai à IA, seja com que nota for: o `_ja_arquivadas` só vê o
    par (principal, velha), e uma velha arquivada sem marca (os arquivos de 25/09) emparelhada com uma TERCEIRA nota voltava à IA
    todas as noites e podia ser arquivada outra vez, debaixo da nota errada (par 20 do ensaio de 23/10)."""
    nomes = set()
    for p in caminhos.notas(REAL):
        try:
            t = caminhos.ler_texto(p)
        except OSError:
            continue
        if MARCA_ARQUIVADA in t:
            nomes.add(p.stem)
        nomes.update(x.strip() for x in HIST_RX.findall(t))
    return nomes


def _factos_so_na_velha(tv, tp):
    """ronda 20: os factos da velha (unir.factos: números, datas, caminhos, `código`, URLs) que não estão INTEIROS na principal.
    É a regra que o pedido à IA já enuncia («SÓ se todos os factos da velha também estão na principal») — mas o arquivar tira a
    nota do índice e só a IA a cumpria; o unir já tinha o mesmo contador (validar, 100 %). Também apanha a direcção invertida
    (a IA a mandar arquivar a principal debaixo da nota de sessão)."""
    return {x for x in unir.factos(tv) if not unir.tem_facto(x, tp)}


def estrela3(n):
    if not (REAL / "MEMORY.md").exists():          # PORTE: sem índice não há ⭐⭐⭐ (antes dava erro)
        return False
    idx = caminhos.ler(REAL / "MEMORY.md").decode("utf-8")
    return any(f"({n}.md)" in l and "⭐⭐⭐" in l for l in idx.splitlines())


def decidir(a, b):
    ta, tb = ler(a), ler(b)
    if retidas.retida(a, ta) or retidas.retida(b, tb):        # ronda 8 (notas retidas): o texto de uma nota retida nunca vai à IA
        return {"acao": "deixar", "manter": a, "confianca": 1.0, "motivo": "uma das notas está retida (segredo em claro ou excluída) — não a mando à IA"}
    prompt = f"""Duas notas da memória de trabalho parecem-se. Decide o que fazer. Responde SÓ com JSON numa linha:
{{"acao":"unir|arquivar|ligar|deixar","manter":"<nome da nota principal>","confianca":0.0-1.0,"motivo":"uma frase curta em português de Portugal"}}
- unir: as duas dizem essencialmente o mesmo (uma repete a outra ou é um subconjunto dela) e juntas ficam uma nota coerente.
- arquivar: uma é um registo velho (de uma sessão, snapshot, fotografia de estado) já ultrapassado pela outra, que é a principal.
  SÓ se todos os factos da velha (números, datas, nomes, comandos, ficheiros) também estão na principal. Se a velha tem factos
  que só ela tem, é ligar — mesmo que o nome tenha session_/snapshot/handoff.
- ligar: mesmo projecto/tema mas partes diferentes (funcionalidades, fases, ferramentas diferentes) — devem ficar separadas e ligadas.
- deixar: não têm relação útil.
Na dúvida entre unir e ligar, escolhe ligar. "manter" = a principal (a mais geral/actual, não a de sessão).

NOTA A: {a} ({len(ta)} caracteres) — {desc(ta)}
{corpo(ta)[:1800]}

NOTA B: {b} ({len(tb)} caracteres) — {desc(tb)}
{corpo(tb)[:1800]}"""
    for tent in range(2):
        try:
            d = _ia.actual("arrumar").chat_json(prompt, tempo=600)   # PORTE: antes um modelo local fixo; agora [ia.arrumar] ou [ia]
            if isinstance(d, dict) and d.get("acao") in ("unir", "arquivar", "ligar", "deixar") and d.get("manter") in (a, b):
                c = float(d.get("confianca", 0))
                if 0.0 <= c <= 1.0:             # ronda 19: json.loads aceita NaN/Infinity, e «nan < 0.85» é falso — passava todos os travões
                    d["confianca"] = c
                    return d
        except (_ia.IAIndisponivel, ValueError, TypeError):
            time.sleep(20)
    return {"acao": "duvida", "manter": a, "confianca": 0.0, "motivo": "a IA não deu uma resposta válida"}


def _hash(n):
    return hashlib.sha1(ler(n).encode()).hexdigest()[:12]


def _job(tipo, manter, fundir, commit, msg):
    j = {"id": uuid.uuid4().hex, "manter": manter, "fundir": fundir, "estado": "feita", "commit": commit, "passo": "",
         "mensagem": msg, "origem": "auto-arrumar", "tipo": tipo, "criado": datetime.datetime.now().isoformat(timespec="seconds")}
    caminhos.escrever_atomico(Path(JOBS) / f"{j['id']}.json", json.dumps(j, ensure_ascii=False).encode("utf-8"))   # ronda 13: sem ligações
    return j["id"]


def _acrescentar(n, linha, alvo):
    """acrescenta uma linha no fim da nota, se a nota ainda não ligar ao alvo."""
    t, h = _ler2(n)
    if alvo in unir.ligacoes(t):
        return False
    return _escrever(n, t.rstrip("\n") + f"\n\n{linha}\n", h)


def mudar(tipo, descricao, fn):
    """fotografia git → fn() → commit; devolve o hash (ou None se nada mudou). Mesma tranca do botão Unir."""
    with unir.trinco(REAL.parent):                     # ronda 13b: sem seguir ligações nem truncar
        unir._git(REAL, "add", "-A"); unir._git(REAL, "commit", "-q", "-m", f"basalt: antes de {descricao}")
        if not fn():
            return None
        unir._git(REAL, "add", "-A")
        c = unir._git(REAL, "commit", "-q", "-m", f"basalt auto-arrumar: {descricao}")
        return unir._git(REAL, "rev-parse", "HEAD").stdout.strip() if c.returncode == 0 else None


def ligar(a, b):
    def fn():
        x = _acrescentar(a, f"Relacionado: [[{b}]] (ligado pelo Basalt, {HOJE})", b)
        y = _acrescentar(b, f"Relacionado: [[{a}]] (ligado pelo Basalt, {HOJE})", a)
        return x or y
    return mudar("ligar", f"ligar {a} e {b}", fn)


def arquivar(principal, velha):
    """a principal passa a ter «Histórico: [[velha]]» (converte o «Relacionado» se já estava ligada — 25/09: sem isto, arquivar
    depois de ligar não fazia nada); a velha ganha no topo a marca de arquivada com a ligação à principal; sai do MEMORY.md."""
    def fn():
        mudou = False
        tp, hp = _ler2(principal)
        hist = f"Histórico: [[{velha}]] (registo antigo, arquivado pelo Basalt a {HOJE})"
        if f"Histórico: [[{velha}]]" not in tp:
            rel = re.compile(rf"^Relacionado: \[\[{re.escape(velha)}\]\] \(ligado pelo Basalt, [\d-]+\)$", re.M)
            tp2 = rel.sub(hist, tp, count=1) if rel.search(tp) else tp.rstrip("\n") + f"\n\n{hist}\n"
            mudou = _escrever(principal, tp2, hp) or mudou
        tv, hv = _ler2(velha)
        if MARCA_ARQUIVADA not in tv:
            marca = f"> 🗄 Arquivada pelo Basalt a {HOJE}: registo antigo. A informação actual está em [[{principal}]].\n\n"
            m = re.match(r"^---\n.*?\n---\n\n?", tv, flags=re.S)
            tv2 = tv[:m.end()] + marca + tv[m.end():] if m else marca + tv
            mudou = _escrever(velha, tv2, hv) or mudou
        idx = REAL / "MEMORY.md"
        b = caminhos.ler(idx) if idx.exists() else b""                    # PORTE: sem índice não há linha a tirar
        t = b.decode("utf-8")
        novo = "".join(l for l in t.splitlines(keepends=True) if f"({velha}.md)" not in l)
        if novo != t:
            mudou = _escrever("MEMORY", novo, cas.sha(b)) or mudou
        return mudou
    return mudar("arquivar", f"arquivar {velha} (histórico de {principal})", fn)


def main(argv=None):
    global SIMULAR
    a = sys.argv[1:] if argv is None else argv
    SIMULAR = "--simular" in a
    if "-h" in a or "--help" in a:
        return print(__doc__)
    try:
        config.definir_processo(config.espaco_de_processo())
        if not _ia.actual("arrumar").chat_activo:
            return print("auto_arrumar precisa de uma IA: defina [ia] fornecedor (ou [ia.arrumar]) no basalt.toml. Nada feito.", file=sys.stderr)
        _correr()
    except config.ConfigInvalida as e:
        sys.exit(f"configuração inválida: {e}")


def _correr():
    dec_f = Path(ESTADO) / "decididos.json"
    try:
        decididos = json.loads(caminhos.ler_texto(dec_f))       # ronda 13: sem seguir ligações
    except FileNotFoundError:
        decididos = {}
    agora = time.time()
    ret = retidas.nomes_retidos(REAL)
    pares = [(s, a, b) for s, a, b in arr.duplicados() if ":" not in a and ":" not in b and a not in ret and b not in ret
             and (REAL / f"{a}.md").exists() and (REAL / f"{b}.md").exists()]
    feitos, duvidas = [], []
    # ronda 20: o tecto «3 por noite» conta o DIA, pelo decididos.json — uma 2.ª corrida no mesmo dia (à mão, ou o timer com
    # Persistent=true depois de um reinício) começava do zero e dava mais 3 fusões e mais 3 arquivos
    unidas = sum(1 for v in decididos.values() if v.get("data") == HOJE and v.get("acao") == "unir")
    arquivadas = sum(1 for v in decididos.values() if v.get("data") == HOJE and v.get("acao") == "arquivar")
    arquiv = _nomes_arquivados()                                       # ronda 20: lidas uma vez; o que se arquiva esta noite junta-se
    for s, a, b in pares:
        chave = "|".join(sorted((a, b)))
        ant = decididos.get(chave)
        if ant and (agora - ant.get("ts", 0)) < DIAS_DECIDIDO * 86400:
            continue
        if not all((REAL / f"{n}.md").exists() for n in (a, b)):       # ronda 19: uma fusão desta noite apaga a nota fundida, e a
            print(f"= {a} ↔ {b}: uma delas já não existe — salto")     # mesma nota está em vários pares (antes: FileNotFoundError
            continue                                                   # matava o ciclo e perdia o decididos.json da noite)
        try:
            ta0, tb0 = ler(a), ler(b)
            if a in arquiv or b in arquiv or _ja_arquivadas(a, b, ta0, tb0):   # rondas 19/20: já decidido por quem arquivou — não volta à IA
                print(f"= {a} ↔ {b}: uma delas já está arquivada — não mexo")
                continue
            d = decidir(a, b)
            manter = d["manter"]; outra = b if manter == a else a
            acao, motivo = d["acao"], d.get("motivo", "")
            if acao == "duvida" or d["confianca"] < 0.6:
                duvidas.append({"notas": [a, b], "sugestao": acao, "motivo": motivo, "confianca": d["confianca"]})
                print(f"? {a} ↔ {b}: dúvida ({acao} {d['confianca']:.2f}) — {motivo}")
                continue
            if acao in ("unir", "arquivar"):                           # travões — qualquer um falha → ligar (ronda 19: também no arquivar)
                ta, tb = ler(manter), ler(outra)
                faltam = sorted(_factos_so_na_velha(tb, ta)) if acao == "arquivar" else []   # ronda 20: a velha (outra) contida na principal (manter)
                travao = ("já houve 3 fusões esta noite" if acao == "unir" and unidas >= MAX_UNIR else
                          "já houve 3 arquivos esta noite" if acao == "arquivar" and arquivadas >= MAX_ARQUIVAR else
                          "confiança < 0.85" if d["confianca"] < 0.85 else
                          "uma delas foi mexida nas últimas 6 h" if any(agora - (REAL / f"{n}.md").stat().st_mtime < HORAS_QUIETA * 3600 for n in (a, b)) else
                          "uma delas é ⭐⭐⭐" if estrela3(a) or estrela3(b) else
                          f"a velha tem factos que a principal não tem ({len(faltam)}; ex.: {', '.join(faltam[:5])})" if faltam else
                          "juntas ficavam grandes demais" if acao == "unir" and len(ta) + len(tb) > MAX_CHARS else "")
                if travao:
                    acao, motivo = "ligar", f"{motivo} (não {'uni' if acao == 'unir' else 'arquivei'}: {travao})"
                elif acao == "arquivar":
                    arquivadas += 1
            print(f"{'[simulação] ' if SIMULAR else ''}{acao.upper():8} {manter} ↔ {outra} ({d['confianca']:.2f}) — {motivo}")
            if SIMULAR:
                feitos.append({"acao": acao, "notas": [manter, outra], "motivo": motivo}); continue
            commit, msg = None, ""
            m = None
            if acao == "unir":                # ronda 12: a fusão é da IA de [ia.unir]; se ela está desligada ou não responde, liga-se
                if not _ia.actual("unir").chat_activo:
                    acao, motivo = "ligar", f"{motivo} (não uni: a IA de fundir está desligada — [ia.unir] fornecedor)"
                else:
                    try:
                        m = unir.fundir_llm(ta, tb, manter, outra)
                    except (_ia.IAIndisponivel, ValueError) as e:
                        acao, motivo = "ligar", f"{motivo} (não uni: a IA não fundiu — {str(e)[:160]})"
            if acao == "unir":
                ok, porque, est = unir.validar(ta, tb, m, manter, outra)
                if ok and est.get("cobertura") == 1.0:
                    try:
                        commit, _, feito = unir.aplicar(REAL, manter, outra, m, ta, tb)
                        commit = commit if feito else None
                        msg = f"Unidas (automático): «{outra}» entrou em «{manter}» ({est['factos']} factos, 0 perdidos). {motivo}"
                        unidas += 1
                    except RuntimeError as e:
                        acao, motivo = "ligar", f"{motivo} (não uni: {e})"
                else:
                    acao, motivo = "ligar", f"{motivo} (não uni: verificação {porque if not ok else 'abaixo de 100 %'})"
            if acao == "ligar":
                commit = ligar(manter, outra); msg = f"Ligadas (automático): «{manter}» ↔ «{outra}». {motivo}"
            elif acao == "arquivar":
                commit = arquivar(manter, outra); msg = f"Arquivada (automático): «{outra}» passou a histórico de «{manter}». {motivo}"
                if commit:
                    arquiv.add(outra)
        except FileNotFoundError as e:                                 # ronda 20: a nota desapareceu ENQUANTO a IA pensava (até 2×600 s) —
            if not str(e.filename or "").endswith(".md"):              # o salto de cima é antes da IA. Só notas: um `git` em falta continua a rebentar
                raise
            print(f"= {a} ↔ {b}: uma delas desapareceu a meio da noite ({Path(str(e.filename)).name}) — salto")
            continue
        job = _job(acao, manter, outra, commit, msg) if commit else None
        decididos[chave] = {"acao": acao, "ts": agora, "data": HOJE, "motivo": motivo}
        feitos.append({"acao": acao, "notas": [manter, outra], "motivo": motivo, "commit": commit, "job": job})
    if SIMULAR:
        return
    caminhos.escrever_atomico(dec_f, json.dumps(decididos, ensure_ascii=False, indent=1).encode("utf-8"))
    caminhos.escrever_atomico(Path(ESTADO) / "ultimo.json",
                              json.dumps({"data": HOJE, "feitos": feitos, "duvidas": duvidas}, ensure_ascii=False, indent=1).encode("utf-8"))
    if any(f.get("commit") for f in feitos):       # reindexar: tirar do índice o que se uniu/arquivou
        subprocess.run([sys.executable, "-m", "basalt.sig", "indexar"], capture_output=True, timeout=1800, cwd="/tmp",
                       env=config.ambiente_filho())
    if feitos or duvidas:
        conta = {k: sum(1 for f in feitos if f["acao"] == k and f.get("commit")) for k in ("unir", "arquivar", "ligar")}
        # PORTE: antes avisava por um alerta externo; agora é só o resumo no stdout (quem corre a tarefa decide para onde vai)
        print(f"Basalt arrumou a memória: {conta['unir']} unidas, {conta['arquivar']} arquivadas, {conta['ligar']} ligadas"
              + (f"; {len(duvidas)} caso(s) para decidir" if duvidas else "") + ". Desfazer: na página do Basalt → Arrumar.")


if __name__ == "__main__":
    main()
