"""Ronda 12 («actualizar sem perder nada») — o lado do servidor.

  · B — `python -m basalt.categorias` reescrevia o categorias.json só com as notas do espaço: as chaves das fontes externas
    (`<fonte>:<ref>`, de [fontes_extra] ou de uma instalação anterior) caíam a cada volta. Agora ficam enquanto a fonte as tiver
    no índice (ou, sem índice, sempre); só sai a chave de uma nota do espaço que já não existe.
  · C — IA por tarefa: [ia.unir] e [ia.arrumar] sobrepõem fornecedor, url, modelo_chat, chave e tempo_limite de [ia]
    (o resto vem de [ia]; os limites por hora continuam os de [ia]). O botão Unir e o `arrumar --auto` usam a da tarefa;
    o `estado` e o /api/eu dizem qual está em uso.
  · E — `python -m basalt entrada "texto" [--de FONTE]`: guardar na Entrada pela linha de comandos com o MESMO caminho e trinco
    da página (segredos recusados com código de saída ≠ 0, aviso para fontes que não são de confiança). Um programa de fora que
    escreva directamente no entrada.jsonl perde linhas numa corrida com a página; pelo comando não perde.
Cada teste de comportamento falha no código anterior a esta ronda (aa346d2) por asserção e pela razão certa (menos os controlos,
marcados como tal)."""
import json
import os
import sqlite3
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest

from basalt import auto_arrumar, categorias, config, entrada, ia, unir, web
from basalt.config import ConfigInvalida, IAConfig
from basalt.segredos import MOTIVO
from conftest import FM, toml_de, usar_ia
from ollama_falso import OllamaFalso
from test_unir import A, B, BOA

RAIZ = Path(__file__).resolve().parent.parent
ANA = "ana@exemplo.pt"
T = "2026-10-08T10:00:00"
LIXO = json.dumps({"acao": "lixo", "nota": "", "confianca": 0.95, "motivo": "só uma saudação"})


def _env_limpo(tmp_path):
    """o ambiente de um subprocesso sem nenhuma variável BASALT_* (só a --config manda), com HOME temporário."""
    env = {k: v for k, v in os.environ.items() if not k.startswith("BASALT_") or k in ("BASALT_SIG_ISOLADO", "BASALT_CAS_GRACA")}
    env["HOME"] = str(tmp_path)
    return env


def _basalt(tmp_path, toml, *args, entrada_txt=None):
    r = subprocess.run([sys.executable, "-m", "basalt", "--config", str(toml), *args], cwd=RAIZ, env=_env_limpo(tmp_path),
                       input=entrada_txt, capture_output=True, text=True, timeout=120)
    return r.returncode, r.stdout, r.stderr


def _toml(tmp_path, ollama=None, extra=""):
    p = tmp_path / "basalt.toml"
    p.write_text(toml_de(0, tmp_path / "dados", "aberto", ollama, [{"email": ANA, "espaco": "t", "admin": True}], extra), encoding="utf-8")
    return p


def _git(real, *args):
    return subprocess.run(["git", "-C", str(real), "-c", "user.name=t", "-c", "user.email=t@t", *args],
                          capture_output=True, text=True, check=True).stdout


# ═════════════════════════ B — as categorias das fontes externas não se perdem ═════════════════════════
def _cats(memoria, chaves):
    memoria.esp.categorias.parent.mkdir(parents=True, exist_ok=True)
    memoria.esp.categorias.write_text(json.dumps({k: {"cats": ["Trabalho"], "hash": "x"} for k in chaves}), encoding="utf-8")


def _cats_lidas(memoria):
    return json.loads(memoria.esp.categorias.read_text(encoding="utf-8"))


def _indice(memoria, pedacos):
    """um índice com estes (fonte, ref) — o mesmo esquema que o basalt.sig cria."""
    memoria.esp.semantica.parent.mkdir(parents=True, exist_ok=True)
    c = sqlite3.connect(memoria.esp.semantica)
    c.execute("CREATE TABLE IF NOT EXISTS pedaco (fonte TEXT, ref TEXT, titulo TEXT, trecho TEXT, hash TEXT, "
              "vec BLOB, atualizado TEXT DEFAULT (datetime('now')), PRIMARY KEY (fonte, ref))")
    c.executemany("INSERT INTO pedaco (fonte, ref, titulo, trecho, hash) VALUES (?, ?, ?, ?, ?)",
                  [(f, r, r, "texto", "h") for f, r in pedacos])
    c.commit(); c.close()


@pytest.fixture
def com_notas(memoria):
    usar_ia(fornecedor="nenhum")                 # só as regras pelo nome: nada vai à IA
    memoria.nota("nota_viva", FM.format(nome="nota_viva") + "\nainda cá está\n")
    return memoria


def test_sem_indice_as_chaves_das_fontes_externas_ficam(com_notas):
    _cats(com_notas, ["nota_viva", "outro_projecto:feedback_x", "agenda:decisao-1"])
    categorias.main()
    d = _cats_lidas(com_notas)
    assert {"outro_projecto:feedback_x", "agenda:decisao-1"} <= set(d), f"as categorias das fontes externas caíram: {sorted(d)}"
    assert d["agenda:decisao-1"]["cats"] == ["Trabalho"]          # ficam como estavam (não se recategorizam)


def test_com_indice_fica_o_que_a_fonte_ainda_tem_e_sai_o_que_ela_deixou_de_ter(com_notas):
    _indice(com_notas, [("nota", "nota_viva#0"), ("agenda", "decisao-1"), ("agenda", "decisao-2#0"), ("diario", "diario:2026-10-01#1")])
    _cats(com_notas, ["nota_viva", "agenda:decisao-1", "agenda:decisao-2", "agenda:decisao-9", "diario:2026-10-01",
                      "outro_projecto:feedback_x"])
    categorias.main()
    d = set(_cats_lidas(com_notas))
    assert {"agenda:decisao-1", "agenda:decisao-2", "diario:2026-10-01"} <= d, f"caiu uma chave que o índice ainda tem: {sorted(d)}"
    assert "agenda:decisao-9" not in d, "a fonte «agenda» está no índice e já não tem a decisao-9: a categoria dela devia sair"
    assert "outro_projecto:feedback_x" in d, "uma fonte que o índice não conhece (instalação anterior) não pode perder as categorias"


def test_categorizar_tudo_de_novo_tambem_mantem_as_fontes_externas(com_notas):
    _cats(com_notas, ["nota_viva", "agenda:decisao-1"])
    categorias.main(tudo=True)
    assert "agenda:decisao-1" in _cats_lidas(com_notas)


def test_controlo_uma_nota_apagada_perde_a_categoria(com_notas):
    """controlo (já era assim): a chave de uma nota do espaço que já não existe sai; a da nota viva fica."""
    _cats(com_notas, ["nota_viva", "nota_apagada"])
    categorias.main()
    d = _cats_lidas(com_notas)
    assert "nota_apagada" not in d and "nota_viva" in d


# ═════════════════════════ C — IA por tarefa ([ia.unir], [ia.arrumar]) ═════════════════════════
def _cfg(tmp_path, texto_ia):
    p = tmp_path / "basalt.toml"
    p.write_text(toml_de(0, tmp_path / "dados", "aberto", None, [{"email": ANA, "espaco": "t", "admin": True}], texto_ia), encoding="utf-8")
    return config.carregar(p)


def test_a_tarefa_sobrepoe_o_que_declara_e_herda_o_resto(tmp_path):
    c = _cfg(tmp_path, '[ia]\nfornecedor = "openai"\nurl = "http://127.0.0.1:9/v1"\nmodelo_chat = "barato"\ntempo_limite = 60\n'
                       'chave_env = "CHAVE_GERAL"\npedidos_por_hora = 7\n'
                       '[ia.unir]\nmodelo_chat = "forte"\ntempo_limite = 900\n'
                       '[ia.arrumar]\nfornecedor = "claude-cli"\nmodelo_chat = "sonnet"\n')
    u, a = c.ia.para("unir"), c.ia.para("arrumar")
    assert (u.fornecedor, u.url, u.modelo_chat, u.tempo_limite, u.chave_env) == ("openai", "http://127.0.0.1:9/v1", "forte", 900, "CHAVE_GERAL")
    assert (a.fornecedor, a.modelo_chat, a.tempo_limite) == ("claude-cli", "sonnet", 60)
    assert u.pedidos_por_hora == a.pedidos_por_hora == 7                      # os limites por hora são os de [ia]


def test_a_chave_de_ia_nao_vai_para_outro_endereco_nem_outro_fornecedor(tmp_path):
    c = _cfg(tmp_path, '[ia]\nfornecedor = "openai"\nurl = "http://127.0.0.1:9/v1"\nmodelo_chat = "m"\nchave_env = "CHAVE_GERAL"\n'
                       '[ia.unir]\nurl = "http://127.0.0.1:8/v1"\n'
                       '[ia.arrumar]\nfornecedor = "anthropic"\n')
    assert c.ia.para("unir").chave_env == "" and c.ia.para("unir").chave_ficheiro == ""
    assert c.ia.para("arrumar").chave_env == "" and c.ia.para("arrumar").chave_ficheiro == ""


@pytest.mark.parametrize("texto,parte", [
    ('[ia.unir]\nfornecedor = "inventado"\n', "[ia.unir]"),
    ('[ia.arrumar]\nfornecedor = "openai"\nmodelo_chat = "m"\n', "[ia.arrumar]"),          # openai sem url (o [ia] está desligado)
    ('[ia.unir]\npropostas_por_hora = 3\n', "propostas_por_hora"),                          # os limites por hora são só de [ia]
    ('[ia.unir]\ntempo_limite = "muito"\n', "tempo_limite"),
    ('[ia.resumir]\nmodelo_chat = "m"\n', "[ia.resumir]"),                                  # tarefa que não existe (gralha)
])
def test_a_sub_tabela_e_validada_como_o_ia(tmp_path, texto, parte):
    with pytest.raises(ConfigInvalida) as e:
        _cfg(tmp_path, texto)
    assert parte in str(e.value)


def test_sem_sub_tabelas_as_tarefas_usam_o_proprio_ia(tmp_path):
    c = _cfg(tmp_path, '[ia]\nfornecedor = "openai"\nurl = "http://127.0.0.1:9/v1"\nmodelo_chat = "m"\n')
    for t in ("unir", "arrumar"):
        assert c.ia.para(t) is c.ia
    i = ia.IA(c.ia)
    ia.definir(i)
    assert ia.actual("unir") is i and ia.actual("arrumar") is i and ia.actual() is i


def test_controlo_sem_sub_tabelas_o_arrumar_auto_e_o_estado_ficam_iguais(tmp_path):
    """controlo (já era assim): só com [ia], o `arrumar --auto` classifica a Entrada com ela e o `estado` não ganha linhas novas."""
    geral = OllamaFalso()
    geral.resposta_chat = LIXO
    try:
        toml = _toml(tmp_path, geral)
        esp = config.carregar(toml).espaco("t").criar()
        esp.inbox.write_text(json.dumps({"t": T, "texto": "ola", "de": "web"}) + "\n", encoding="utf-8")
        rc, out, err = _basalt(tmp_path, toml, "arrumar", "--auto")
        assert rc == 0, out + err
        assert len(geral.pedidos_chat) == 1 and geral.pedidos_chat[0]["model"] == "modelo-de-teste"
        assert json.loads(esp.inbox.read_text(encoding="utf-8").splitlines()[0]).get("arquivado")
        rc, out, err = _basalt(tmp_path, toml, "estado")
        assert rc == 0 and " ia openai modelo-de-teste" in out, out + err
        assert "ia.unir" not in out and "ia.arrumar" not in out
    finally:
        geral.parar()


def test_arrumar_auto_classifica_a_entrada_com_a_ia_da_tarefa(tmp_path):
    geral, barata = OllamaFalso(), OllamaFalso()
    barata.resposta_chat = LIXO                       # a geral responde texto (não JSON): se fosse ela a decidir, a ideia ficava
    try:
        toml = _toml(tmp_path, geral, f'[ia.arrumar]\nurl = "{barata.url_chat}"\nmodelo_chat = "modelo-barato"\n')
        esp = config.carregar(toml).espaco("t").criar()
        esp.inbox.write_text(json.dumps({"t": T, "texto": "ola", "de": "web"}) + "\n", encoding="utf-8")
        rc, out, err = _basalt(tmp_path, toml, "arrumar", "--auto")
        assert rc == 0, out + err
        assert len(barata.pedidos_chat) == 1 and geral.pedidos_chat == [], \
            f"a Entrada foi à IA errada (geral {len(geral.pedidos_chat)}, [ia.arrumar] {len(barata.pedidos_chat)})\n{out}{err}"
        assert barata.pedidos_chat[0]["model"] == "modelo-barato"
        reg = json.loads(esp.inbox.read_text(encoding="utf-8").splitlines()[0])
        assert reg.get("arquivado") and reg["auto"]["acao"] == "lixo", reg
    finally:
        geral.parar(); barata.parar()


def test_o_estado_diz_que_ia_usa_cada_tarefa(tmp_path):
    toml = _toml(tmp_path, None, '[ia]\nfornecedor = "openai"\nurl = "http://127.0.0.1:9/v1"\nmodelo_chat = "barato"\n'
                                 '[ia.unir]\nfornecedor = "claude-cli"\nmodelo_chat = "sonnet"\n')
    rc, out, err = _basalt(tmp_path, toml, "estado")
    assert rc == 0, out + err
    linhas = out.splitlines()
    assert any("ia.unir" in l and "claude-cli" in l and "sonnet" in l for l in linhas), out
    assert any("ia.arrumar" in l and "openai" in l and "barato" in l for l in linhas), out


def test_o_botao_unir_usa_a_ia_de_unir_mesmo_com_o_ia_desligado(servidor_local, monkeypatch):
    forte = OllamaFalso(); forte.resposta_chat = BOA
    monkeypatch.setattr(web, "_reindexar", lambda esp=None: None)
    try:
        s = servidor_local(modo="cabecalho", extra=f'[ia]\nfornecedor = "nenhum"\n'
                                                    f'[ia.unir]\nfornecedor = "openai"\nurl = "{forte.url_chat}"\nmodelo_chat = "modelo-forte"\n')
        esp = s.espaco("t").criar()
        real = esp.real
        for pasta in (real, esp.copia):                    # na memória e na cópia (só na memória ficava «retida»)
            (pasta / "nota_a.md").write_text(A); (pasta / "nota_b.md").write_text(B)
        _git(real, "init", "-q"); _git(real, "add", "-A"); _git(real, "commit", "-q", "-m", "inicio")
        r = s.pedido("POST", "/api/unir", {"manter": "nota_a", "fundir": "nota_b"})
        assert r.codigo == 202, r.corpo
        jid, fim = r.json["job"], time.time() + 20
        while time.time() < fim:
            j = s.get(f"/api/unir?job={jid}").json
            if j["estado"] in ("feita", "recusada", "falhou"):
                break
            time.sleep(0.05)
        assert j["estado"] == "feita", j
        assert len(forte.pedidos_chat) == 1 and forte.pedidos_chat[0]["model"] == "modelo-forte"
        tarefas = s.get("/api/eu").json["ia"].get("tarefas", {})          # o diagnóstico da página diz qual está em uso
        assert (tarefas["unir"]["chat"], tarefas["unir"]["modelo"]) == ("openai", "modelo-forte"), tarefas
        assert tarefas["arrumar"]["chat"] == "nenhum", tarefas
    finally:
        forte.parar()


def test_controlo_sem_ia_nenhuma_o_botao_unir_continua_a_dizer_que_esta_desligada(memoria):
    """controlo: [ia] desligado e sem [ia.unir] → 503 como antes (e a mensagem continua a falar de [ia])."""
    ia.definir(ia.IA(IAConfig(fornecedor="nenhum")))
    cod, r = web.iniciar_fusao("nota_a", "nota_b")
    assert cod == 503 and r["ia"] is False and "[ia]" in r["erro"]


def test_arrumar_auto_funde_com_a_ia_de_unir_e_se_ela_falha_so_liga(memoria, tmp_path, monkeypatch):
    """a decisão (unir) é da IA de [ia.arrumar]/[ia]; a fusão é da de [ia.unir]. Se esta não responde, o par fica ligado
    (nada a meio) e a fusão não vai parar à IA geral."""
    geral, forte = OllamaFalso(), OllamaFalso()
    forte.em_baixo = True
    try:
        c = _cfg(tmp_path, f'[ia]\nfornecedor = "openai"\nurl = "{geral.url_chat}"\nmodelo_chat = "barato"\n'
                           f'[ia.unir]\nurl = "{forte.url_chat}"\nmodelo_chat = "forte"\n')
        ia.definir(ia.IA(c.ia))
        real = memoria.real
        (real / "nota_a.md").write_text(A); (real / "nota_b.md").write_text(B)
        velho = time.time() - 86400
        for n in ("nota_a", "nota_b"):
            os.utime(real / f"{n}.md", (velho, velho))
        _git(real, "init", "-q"); _git(real, "add", "-A"); _git(real, "commit", "-q", "-m", "inicio")
        monkeypatch.setattr(auto_arrumar.arr, "duplicados", lambda *a, **k: [(0.97, "nota_a", "nota_b")])
        monkeypatch.setattr(auto_arrumar, "decidir", lambda a, b: {"acao": "unir", "manter": "nota_a", "confianca": 0.95, "motivo": "iguais"})
        correr = subprocess.run                    # o reindexar no fim fica de fora (o git das notas corre a sério)
        monkeypatch.setattr(subprocess, "run", lambda args, *a, **k: subprocess.CompletedProcess(args, 0, "", "")
                            if "basalt.sig" in args else correr(args, *a, **k))
        monkeypatch.setattr(auto_arrumar, "SIMULAR", False)
        auto_arrumar._correr()
        assert geral.pedidos_chat == [], "a fusão foi pedida à IA geral, não à de [ia.unir]"
        ultimo = json.loads((memoria.esp.auto_arrumar / "ultimo.json").read_text())
        f = ultimo["feitos"][0]
        assert f["acao"] == "ligar" and "não uni" in f["motivo"], f
        assert (real / "nota_b.md").exists() and "[[nota_b]]" in (real / "nota_a.md").read_text()
    finally:
        geral.parar(); forte.parar()


# ═════════════════════════ E — `python -m basalt entrada` (o mesmo trinco da página) ═════════════════════════
def _espaco(toml):
    return config.carregar(toml).espaco("t").criar()


def _regs(esp):
    return [json.loads(l) for l in esp.inbox.read_text(encoding="utf-8").splitlines() if l.strip()] if esp.inbox.exists() else []


def test_entrada_pela_linha_de_comandos_guarda_com_a_fonte(tmp_path):
    toml = _toml(tmp_path)
    esp = _espaco(toml)
    rc, out, err = _basalt(tmp_path, toml, "entrada", "comprar velas para os barcos", "--de", "bot")
    assert rc == 0, out + err
    regs = _regs(esp)
    assert [(r["texto"], r["de"], r["arquivado"]) for r in regs] == [("comprar velas para os barcos", "bot", False)], regs
    assert "guardado" in out
    assert "confiança" in err and "bot" in err, "uma fonte fora de [entrada] fontes_de_confianca devia avisar que a ideia fica à espera"


def test_entrada_sem_de_e_do_terminal_e_nao_avisa(tmp_path):
    toml = _toml(tmp_path)
    esp = _espaco(toml)
    rc, out, err = _basalt(tmp_path, toml, "entrada", "ideia", "solta")
    assert rc == 0, out + err
    assert [(r["texto"], r["de"]) for r in _regs(esp)] == [("ideia solta", "terminal")]
    assert "confiança" not in err


def test_entrada_le_o_texto_do_stdin_com_hifen(tmp_path):
    """com «-» o texto vem do stdin (não aparece na lista de processos) — é o que um bot deve usar."""
    toml = _toml(tmp_path)
    esp = _espaco(toml)
    rc, out, err = _basalt(tmp_path, toml, "entrada", "-", "--de", "telegram", entrada_txt="linha um\nlinha dois\n")
    assert rc == 0, out + err
    assert [(r["texto"], r["de"]) for r in _regs(esp)] == [("linha um\nlinha dois", "telegram")]


def test_entrada_com_segredo_e_recusada_e_nao_escreve(tmp_path):
    toml = _toml(tmp_path)
    esp = _espaco(toml)
    rc, out, err = _basalt(tmp_path, toml, "entrada", "a chave é sk-ant-" + "a" * 30, "--de", "bot")
    assert rc == 2 and MOTIVO in err, (rc, out, err)
    assert _regs(esp) == []


def test_entrada_vazia_e_recusada(tmp_path):
    toml = _toml(tmp_path)
    esp = _espaco(toml)
    rc, out, err = _basalt(tmp_path, toml, "entrada", "-", entrada_txt="   \n")
    assert rc != 0 and "vazi" in err, (rc, out, err)
    assert _regs(esp) == []


def test_entrada_sem_texto_lista_as_ideias_por_arrumar(tmp_path):
    toml = _toml(tmp_path)
    esp = _espaco(toml)
    esp.inbox.write_text(json.dumps({"t": T, "texto": "uma ideia antiga", "de": "web"}) + "\n", encoding="utf-8")
    rc, out, err = _basalt(tmp_path, toml, "entrada")
    assert rc == 0 and "1 por arrumar" in out and "uma ideia antiga" in out, out + err


def _a_espera_do_trinco(proc, lock: Path, segundos=20):
    """espera até o processo `proc` estar BLOQUEADO no flock de `lock` (/proc/locks: «->» = pedido à espera). Sem /proc/locks
    (outro sistema) espera 3 s. Devolve False se o processo acabou antes."""
    proc_locks = Path("/proc/locks")
    if not proc_locks.exists():
        time.sleep(3)
        return proc.poll() is None
    ino, pid = os.stat(lock).st_ino, proc.pid
    fim = time.time() + segundos
    while time.time() < fim and proc.poll() is None:
        for l in proc_locks.read_text().splitlines():
            p = l.split()
            if "->" in p and str(pid) in p and any(x.endswith(f":{ino}") for x in p):
                return True
        time.sleep(0.02)
    return False


def _corrida(tmp_path, monkeypatch, escritor_de_fora):
    """a página (aqui, no processo do teste) reescreve a Entrada (arquivar a ideia T) enquanto um programa de fora guarda uma ideia
    nova. `escritor_de_fora(esp)` corre ENTRE a página ler a Entrada e a página gravar a fotografia — o pior momento."""
    toml = _toml(tmp_path)
    esp = _espaco(toml)
    esp.inbox.write_text(json.dumps({"t": T, "texto": "ideia que a página arquiva", "de": "web"}) + "\n", encoding="utf-8")
    original = entrada._escrever

    def escrever_a_meio(p, regs):
        escritor_de_fora(esp, toml)
        return original(p, regs)
    monkeypatch.setattr(entrada, "_escrever", escrever_a_meio)
    with config.dentro(esp):
        assert entrada.mudar(T, lambda r: dict(r, arquivado=True)) is not None
    return esp


def test_controlo_escrever_directamente_no_ficheiro_perde_a_ideia_numa_corrida(tmp_path, monkeypatch):
    """controlo (o problema que o comando resolve): um append directo, sem o trinco, no meio da reescrita da página, desaparece."""
    def directo(esp, toml):
        with open(esp.inbox, "a", encoding="utf-8") as f:
            f.write(json.dumps({"t": "2026-10-08T10:00:01", "texto": "escrita directa", "de": "bot", "arquivado": False}) + "\n")
    esp = _corrida(tmp_path, monkeypatch, directo)
    assert "escrita directa" not in esp.inbox.read_text(encoding="utf-8")


def test_pelo_comando_a_ideia_nao_se_perde_numa_corrida_com_a_pagina(tmp_path, monkeypatch):
    procs = []

    def pelo_comando(esp, toml):
        p = subprocess.Popen([sys.executable, "-m", "basalt", "--config", str(toml), "entrada", "ideia de fora", "--de", "bot"],
                             cwd=RAIZ, env=_env_limpo(tmp_path), stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True)
        procs.append(p)
        bloqueado = _a_espera_do_trinco(p, esp.inbox.with_name(".entrada.lock"))
        procs.append(bloqueado)
    esp = _corrida(tmp_path, monkeypatch, pelo_comando)
    p, bloqueado = procs
    out, err = p.communicate(timeout=60)
    assert p.returncode == 0, out + err
    assert bloqueado, "o comando não esperou pelo trinco da Entrada (escreveu sem ele ou acabou antes)"
    regs = _regs(esp)
    assert [r["texto"] for r in regs] == ["ideia que a página arquiva", "ideia de fora"], regs
    assert regs[0]["arquivado"] and not regs[1]["arquivado"]
