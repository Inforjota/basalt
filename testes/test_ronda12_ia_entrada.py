"""Ronda 12, parte G — `[ia.entrada]`: a IA que classifica a Entrada separada da que decide os pares de notas parecidas.

  · `[ia.entrada]` é opcional, com as mesmas chaves e a mesma validação de `[ia.unir]`/`[ia.arrumar]`.
  · A classificação da Entrada (o vigia do servidor, `web.processar_entrada`, e a Entrada do `arrumar --auto`) usa, por esta ordem,
    `[ia.entrada]`, `[ia.arrumar]`, `[ia]`; os pares do `arrumar --auto` continuam em `[ia.arrumar]` → `[ia]`.
  · Uma desligada não leva a outra: com `[ia.arrumar] fornecedor = "nenhum"` e a Entrada com IA, o `arrumar --auto` classifica a
    Entrada, não decide pares e continua a reindexar e a refazer as categorias (antes saltava a Entrada também).
  · O `estado` (e a descrição da IA no /api/eu) mostra as três tarefas e de onde vem cada uma.
  · A ajuda do `entrada` explica o `--` para um texto que comece por «-».
Cada teste de comportamento falha no código anterior a esta parte (41b982e) por asserção e pela razão certa (menos os controlos,
marcados como tal)."""
import json
import subprocess
import sys

import pytest

from basalt import __main__ as cli
from basalt import auto_arrumar, categorias, config, ia, sig, web
from basalt.config import ConfigInvalida
from conftest import IAFalsa, toml_de
from ollama_falso import OllamaFalso
from test_ronda12_servidor import ANA, LIXO, T, _basalt, _cfg, _toml

IA_GERAL = '[ia]\nfornecedor = "openai"\nurl = "http://127.0.0.1:9/v1"\nmodelo_chat = "geral"\n'


class _Parar(BaseException):
    """sai do ciclo infinito do vigia (o vigia apanha Exception, não isto)."""


def _ia_por_tarefa(monkeypatch, geral, **tarefas):
    """a IA do processo com uma IA diferente por tarefa (sem configuração): `actual("entrada")` → tarefas["entrada"], etc."""
    monkeypatch.setattr(ia, "actual", lambda tarefa=None: tarefas.get(tarefa, geral) if tarefa else geral)


# ───────────────────────── configuração ─────────────────────────
def test_ia_entrada_e_aceite_com_as_mesmas_chaves_das_outras(tmp_path):
    c = _cfg(tmp_path, IA_GERAL + '[ia.entrada]\nmodelo_chat = "pequeno"\ntempo_limite = 30\n')
    e = c.ia.para("entrada")
    assert (e.fornecedor, e.url, e.modelo_chat, e.tempo_limite, e.seccao) == ("openai", "http://127.0.0.1:9/v1", "pequeno", 30, "ia.entrada")


@pytest.mark.parametrize("texto,comeco", [
    ('[ia.entrada]\nfornecedor = "inventado"\n', "[ia.entrada] fornecedor tem de ser"),
    ('[ia.entrada]\nfornecedor = "openai"\nmodelo_chat = "m"\n', "[ia.entrada] fornecedor openai precisa de url"),
    ('[ia.entrada]\npedidos_por_hora = 3\n', "[ia.entrada] pedidos_por_hora: aqui só se pode mudar"),
    ('[ia.entrada]\ntempo_limite = "muito"\n', "[ia.entrada] tempo_limite tem de ser"),
    ('[ia.entrada]\nmodelo_chat = 7\n', "[ia.entrada] modelo_chat tem de ser texto"),
])
def test_ia_entrada_e_validada_como_as_outras(tmp_path, texto, comeco):
    with pytest.raises(ConfigInvalida) as e:
        _cfg(tmp_path, texto)
    assert str(e.value).startswith(comeco), str(e.value)


def test_a_entrada_usa_a_sua_depois_a_de_arrumar_depois_a_de_ia(tmp_path):
    so_arrumar = _cfg(tmp_path, IA_GERAL + '[ia.arrumar]\nmodelo_chat = "classificar"\n').ia
    e = so_arrumar.para("entrada")
    assert (e.modelo_chat, e.seccao) == ("classificar", "ia.arrumar"), "sem [ia.entrada] a Entrada devia usar a de [ia.arrumar]"
    as_duas = _cfg(tmp_path, IA_GERAL + '[ia.arrumar]\nfornecedor = "nenhum"\n[ia.entrada]\nmodelo_chat = "pequeno"\n').ia
    assert (as_duas.para("entrada").modelo_chat, as_duas.para("entrada").fornecedor) == ("pequeno", "openai")
    assert as_duas.para("arrumar").fornecedor == "nenhum"                  # os pares continuam na de [ia.arrumar]
    assert as_duas.para("unir") is as_duas                                 # e o unir na de [ia]
    so_ia = _cfg(tmp_path, IA_GERAL).ia
    assert so_ia.para("entrada") is so_ia


def test_a_ia_do_processo_da_entrada_e_a_de_arrumar_quando_nao_ha_a_sua(tmp_path):
    i = ia.IA(_cfg(tmp_path, IA_GERAL + '[ia.arrumar]\nmodelo_chat = "classificar"\n').ia)
    ia.definir(i)
    assert ia.actual("entrada") is ia.actual("arrumar") and ia.actual("entrada") is not i
    d = i.descricao()["tarefas"]
    assert set(d) == {"unir", "arrumar", "entrada"}, d
    assert d["entrada"]["modelo"] == "classificar" and d["entrada"]["de"] == "ia.arrumar" and d["unir"]["de"] == "ia", d


# ───────────────────────── o `arrumar --auto` ─────────────────────────
def _arrumar_auto(monkeypatch, tmp_path, *extra):
    """corre `basalt arrumar --auto` no processo e diz o que foi chamado (a Entrada, os pares, o índice, as categorias). A IA de
    cada tarefa é a que o teste puser com `_ia_por_tarefa` (a do toml não conta)."""
    feito = []
    monkeypatch.setattr(web, "processar_entrada", lambda: feito.append("entrada") or 0)
    monkeypatch.setattr(auto_arrumar, "_correr", lambda: feito.append("pares"))
    monkeypatch.setattr(sig, "indexar", lambda *a, **k: feito.append("indexar"))
    monkeypatch.setattr(categorias, "main", lambda *a, **k: feito.append("categorias"))
    toml = tmp_path / "basalt.toml"
    toml.write_text(toml_de(0, tmp_path / "dados", "aberto", None, [{"email": ANA, "espaco": "t", "admin": True}]), encoding="utf-8")
    cli.main(["--config", str(toml), "arrumar", "--auto", *extra])
    return feito


def test_arrumar_auto_nao_salta_a_entrada_quando_os_pares_estao_desligados(monkeypatch, tmp_path, capsys):
    _ia_por_tarefa(monkeypatch, IAFalsa(fornecedor="nenhum"), arrumar=IAFalsa(fornecedor="nenhum", seccao="ia.arrumar"),
                   entrada=IAFalsa(LIXO, seccao="ia.entrada"))
    feito = _arrumar_auto(monkeypatch, tmp_path)
    err = capsys.readouterr().err
    assert "entrada" in feito, f"a Entrada foi saltada por os pares estarem desligados: {feito}\n{err}"
    assert "pares" not in feito, feito
    assert feito[-2:] == ["indexar", "categorias"], feito
    assert "[ia.arrumar]" in err and "par" in err, err                # diz porque não decidiu pares


def test_arrumar_auto_simular_conta_a_entrada_e_nao_reindexa(monkeypatch, tmp_path, capsys):
    _ia_por_tarefa(monkeypatch, IAFalsa(fornecedor="nenhum"), arrumar=IAFalsa(fornecedor="nenhum"), entrada=IAFalsa(LIXO))
    feito = _arrumar_auto(monkeypatch, tmp_path, "--simular")
    out = capsys.readouterr().out
    assert "[simulação]" in out and "Entrada" in out, out
    assert feito == [], feito                                           # nada à IA, sem pares, sem índice


def test_com_a_entrada_desligada_os_pares_continuam(monkeypatch, tmp_path, capsys):
    _ia_por_tarefa(monkeypatch, IAFalsa(fornecedor="nenhum"), arrumar=IAFalsa(LIXO), entrada=IAFalsa(fornecedor="nenhum"))
    feito = _arrumar_auto(monkeypatch, tmp_path)
    assert feito == ["pares", "indexar", "categorias"], feito


def test_com_os_pares_desligados_o_arrumar_auto_classifica_a_entrada_com_a_sua_ia(tmp_path):
    """de ponta a ponta, com a configuração a sério: [ia.arrumar] desligado e [ia.entrada] num modelo pequeno."""
    geral, pequena = OllamaFalso(), OllamaFalso()
    pequena.resposta_chat = LIXO                      # a geral responde texto (não JSON): se fosse ela a decidir, a ideia ficava
    try:
        toml = _toml(tmp_path, geral, f'[ia.arrumar]\nfornecedor = "nenhum"\n'
                                      f'[ia.entrada]\nurl = "{pequena.url_chat}"\nmodelo_chat = "modelo-pequeno"\n')
        esp = config.carregar(toml).espaco("t").criar()
        esp.inbox.write_text(json.dumps({"t": T, "texto": "ola", "de": "web"}) + "\n", encoding="utf-8")
        rc, out, err = _basalt(tmp_path, toml, "arrumar", "--auto")
        assert rc == 0, out + err
        assert [p["model"] for p in pequena.pedidos_chat] == ["modelo-pequeno"] and geral.pedidos_chat == [], \
            f"a Entrada foi à IA errada (geral {len(geral.pedidos_chat)}, [ia.entrada] {len(pequena.pedidos_chat)})\n{out}{err}"
        reg = json.loads(esp.inbox.read_text(encoding="utf-8").splitlines()[0])
        assert reg.get("arquivado") and reg["auto"]["acao"] == "lixo", reg
        assert not (esp.auto_arrumar / "ultimo.json").exists(), "os pares correram com [ia.arrumar] desligado"
        assert esp.semantica.exists() and esp.categorias.exists(), "o índice e as categorias deviam ter sido refeitos"
    finally:
        geral.parar(); pequena.parar()


# ───────────────────────── o vigia do servidor ─────────────────────────
def test_o_vigia_do_servidor_classifica_a_entrada_com_os_pares_desligados(monkeypatch, tmp_path):
    cfg = config.carregar(_toml(tmp_path, None))
    _ia_por_tarefa(monkeypatch, IAFalsa(fornecedor="nenhum"), arrumar=IAFalsa(fornecedor="nenhum"), entrada=IAFalsa(LIXO))
    voltas = []
    monkeypatch.setattr(web, "processar_entrada", lambda: voltas.append(1) or 0)

    def dormir(_):
        raise _Parar
    monkeypatch.setattr(web.time, "sleep", dormir)
    try:
        web.vigia_entrada(cfg)
    except _Parar:
        pass
    assert voltas, "o vigia não arrancou: olhou para a IA dos pares em vez da da Entrada"


def test_controlo_sem_ia_para_a_entrada_o_vigia_nem_arranca(monkeypatch, tmp_path):
    """controlo (já era assim): sem IA nenhuma a Entrada automática não faz nada."""
    cfg = config.carregar(_toml(tmp_path, None))
    desligada = IAFalsa(fornecedor="nenhum")
    _ia_por_tarefa(monkeypatch, desligada, arrumar=desligada, entrada=desligada)
    monkeypatch.setattr(web, "processar_entrada", lambda: pytest.fail("não devia tratar a Entrada sem IA"))
    assert web.vigia_entrada(cfg) is None


# ───────────────────────── o `estado` ─────────────────────────
def test_o_estado_mostra_as_tres_tarefas_e_de_onde_vem_cada_uma(tmp_path):
    toml = _toml(tmp_path, None, IA_GERAL + '[ia.arrumar]\nfornecedor = "nenhum"\n[ia.entrada]\nmodelo_chat = "pequeno"\n')
    rc, out, err = _basalt(tmp_path, toml, "estado")
    assert rc == 0, out + err
    linhas = {l.split()[0]: l for l in out.splitlines() if l.startswith("  ia.")}
    assert set(linhas) == {"ia.unir", "ia.arrumar", "ia.entrada"}, out
    assert "geral" in linhas["ia.unir"] and "(a de [ia])" in linhas["ia.unir"], out
    assert "nenhum" in linhas["ia.arrumar"], out
    assert "pequeno" in linhas["ia.entrada"] and "(a de" not in linhas["ia.entrada"], out
    toml.write_text(toml.read_text(encoding="utf-8").replace('[ia.entrada]\nmodelo_chat = "pequeno"\n', "")
                    .replace('[ia.arrumar]\nfornecedor = "nenhum"\n', '[ia.arrumar]\nmodelo_chat = "classificar"\n'), encoding="utf-8")
    rc, out, err = _basalt(tmp_path, toml, "estado")
    linha = next(l for l in out.splitlines() if l.startswith("  ia.entrada"))
    assert "classificar" in linha and "(a de [ia.arrumar])" in linha, out


def test_controlo_so_com_ia_arrumar_os_pares_e_o_unir_ficam_como_estavam(tmp_path):
    """controlo (já era assim): com [ia.arrumar] e sem [ia.entrada], os pares usam [ia.arrumar] e o unir usa [ia]."""
    c = _cfg(tmp_path, IA_GERAL + '[ia.arrumar]\nmodelo_chat = "classificar"\n').ia
    assert (c.para("arrumar").modelo_chat, c.para("arrumar").seccao) == ("classificar", "ia.arrumar")
    assert c.para("unir") is c


# ───────────────────────── a ajuda do `entrada` ─────────────────────────
def test_a_ajuda_do_entrada_explica_o_traco_duplo(tmp_path):
    toml = _toml(tmp_path, None)
    rc, out, err = _basalt(tmp_path, toml, "entrada", "--help")
    assert rc == 0, out + err
    assert '-- "-' in " ".join(out.split()), out                 # ex.: basalt entrada -- "-urgente"


def test_controlo_com_traco_duplo_um_texto_que_comeca_por_hifen_grava(tmp_path):
    """controlo (já era assim): `entrada --de X -- "-urgente"` grava o texto tal como está."""
    toml = _toml(tmp_path, None)
    rc, out, err = _basalt(tmp_path, toml, "entrada", "--de", "telegram", "--", "-urgente")
    assert rc == 0, out + err
    esp = config.carregar(toml).espaco("t")
    regs = [json.loads(l) for l in esp.inbox.read_text(encoding="utf-8").splitlines() if l.strip()]
    assert [(r["texto"], r["de"]) for r in regs] == [("-urgente", "telegram")], regs
