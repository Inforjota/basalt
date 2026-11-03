"""r22 — achado R5 (menor) da revisão externa da r13b (23/10): categorias das fontes externas (basalt/categorias.py).

  a) a chave sem «#» que a ronda 13 gravava para um «repo#12» sozinho («pedidos:repo») tapava o «repo#13» quando o #12 saía do
     índice e o #13 ficava sozinho: nunca ganhava categoria. Agora a chave de antes só vale para o item com o mesmo texto (hash).
  b) «repo#12» e «repo#13» com o MESMO título (dois pedidos chamados «Bug») juntavam-se sob «pedidos:repo» como se fossem uma nota
     partida. Agora uma nota partida tem de começar no «#0» (como as do basalt.sig): cada pedido fica com a sua chave exacta.

Cada teste de comportamento falha no 15beb01 por asserção; os controlos passam nos dois.
"""
import hashlib
import json

from basalt import config, sig
from test_ronda13_servidor import _cats_lidas, _por_no_indice, com_notas  # noqa: F401 (a fixture)


def _tirar_do_indice(esp, refs):
    with config.dentro(esp):
        c = sig.db()
        c.executemany("DELETE FROM pedaco WHERE ref=?", [(r,) for r in refs])
        c.commit()
        c.close()


def _gravar(memoria, d):
    memoria.esp.categorias.parent.mkdir(parents=True, exist_ok=True)
    memoria.esp.categorias.write_text(json.dumps(d), encoding="utf-8")


def _h(texto):
    return hashlib.sha1(texto.encode()).hexdigest()


def test_chave_generica_do_repo12_que_saiu_nao_tapa_o_repo13_sozinho(com_notas):
    from basalt import categorias
    _por_no_indice(com_notas.esp, [("pedidos", "repo#13", "Pedido 13", "b")])        # o #12 já saiu; o #13 ficou sozinho
    # o que a ronda 13 gravou para o «repo#12» sozinho (texto «a»): a chave sem «#», com o hash do texto dele
    _gravar(com_notas, {"pedidos:repo": {"cats": ["Pessoal"], "hash": _h("a")}})
    categorias.main()
    d = _cats_lidas(com_notas)
    assert "pedidos:repo#13" in d, ("o repo#13 ficou sem categoria, tapado pela chave do repo#12", sorted(d))


def test_controlo_chave_generica_do_proprio_item_continua_a_valer(com_notas):
    """controlo: o «repo#12» que lá estava (o mesmo texto) não volta a ir ao modelo — a chave de antes é a dele."""
    from basalt import categorias
    _por_no_indice(com_notas.esp, [("pedidos", "repo#12", "Pedido 12", "a")])
    _gravar(com_notas, {"pedidos:repo": {"cats": ["Pessoal"], "hash": _h("a")}})
    categorias.main()
    d = _cats_lidas(com_notas)
    assert d["pedidos:repo"]["cats"] == ["Pessoal"] and "pedidos:repo#12" not in d, sorted(d)


def test_dois_pedidos_com_o_mesmo_titulo_ficam_cada_um_com_a_chave_exacta(com_notas):
    from basalt import categorias
    _por_no_indice(com_notas.esp, [("pedidos", "repo#12", "Bug", "a"), ("pedidos", "repo#13", "Bug", "b")])
    categorias.main()
    d = _cats_lidas(com_notas)
    assert {"pedidos:repo#12", "pedidos:repo#13"} <= set(d) and "pedidos:repo" not in d, sorted(d)


def test_controlo_nota_partida_do_0_continua_a_ser_um_item_so(com_notas):
    """controlo: «doc#0», «doc#1» com o mesmo título é uma nota partida — uma chave só, «manual:doc»."""
    from basalt import categorias
    _por_no_indice(com_notas.esp, [("manual", "doc#0", "Manual", "parte 0"), ("manual", "doc#1", "Manual", "parte 1")])
    categorias.main()
    d = _cats_lidas(com_notas)
    assert "manual:doc" in d and "manual:doc#0" not in d and "manual:doc#1" not in d, sorted(d)


def test_controlo_o_repo13_categorizado_mantem_se_depois_da_chave_generica(com_notas):
    """controlo: depois de o #13 ganhar a sua chave, outra corrida não mexe (nada vai outra vez ao modelo)."""
    from basalt import categorias
    _por_no_indice(com_notas.esp, [("pedidos", "repo#12", "Pedido 12", "a")])
    _gravar(com_notas, {"pedidos:repo": {"cats": ["Pessoal"], "hash": _h("a")}})
    _tirar_do_indice(com_notas.esp, ["repo#12"])
    _por_no_indice(com_notas.esp, [("pedidos", "repo#13", "Pedido 13", "b")])
    categorias.main()
    primeira = _cats_lidas(com_notas)
    categorias.main()
    assert _cats_lidas(com_notas) == primeira
