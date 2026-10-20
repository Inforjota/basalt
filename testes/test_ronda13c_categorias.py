"""Ronda 13c — categorias das fontes externas: uma ref SEM «#» que chega depois de uma ref com «#» da mesma base.

Ronda 13b: «repo#12» sozinho fica com a chave exacta («pedidos:repo#12»). Se a seguir a fonte traz a ref «repo» (uma página
própria, sem «#», com outro título), o item «pedidos:repo» era saltado para sempre: a regra que dá uma nota partida como feita
quando o seu pedaço único de antes tinha a chave exacta confundia a ref real sem «#» com a chave de uma nota partida.
"""
from test_ronda13_servidor import _cats_lidas, _por_no_indice, com_notas  # noqa: F401 (a fixture)


def test_ref_sem_cardinal_que_chega_depois_de_uma_com_cardinal_ganha_categoria(com_notas):
    from basalt import categorias
    _por_no_indice(com_notas.esp, [("pedidos", "repo#12", "Pedido 12", "a")])
    categorias.main()
    d = _cats_lidas(com_notas)
    assert "pedidos:repo#12" in d and "pedidos:repo" not in d, sorted(d)
    _por_no_indice(com_notas.esp, [("pedidos", "repo", "Página do repo", "b")])      # a ref real sem «#», noutra indexação
    categorias.main()
    d = _cats_lidas(com_notas)
    assert "pedidos:repo" in d and "pedidos:repo#12" in d, sorted(d)


def test_controlo_ref_sem_cardinal_e_com_cardinal_na_mesma_indexacao_ganham_as_duas(com_notas):
    from basalt import categorias
    _por_no_indice(com_notas.esp, [("pedidos", "repo#12", "Pedido 12", "a"), ("pedidos", "repo", "Página do repo", "b")])
    categorias.main()
    d = _cats_lidas(com_notas)
    assert "pedidos:repo" in d and "pedidos:repo#12" in d, sorted(d)


def test_controlo_nota_partida_cujo_pedaco_unico_de_antes_tinha_a_chave_exacta_continua_feita(com_notas):
    """o que o 3.º termo da regra protege: «doc#0» sozinho (chave exacta) e depois «doc#1» com o MESMO título — uma nota partida,
    com a chave «manual:doc», que já está feita pela chave do pedaço de antes."""
    from basalt import categorias
    _por_no_indice(com_notas.esp, [("manual", "doc#0", "Manual", "parte 0")])
    categorias.main()
    d = _cats_lidas(com_notas)
    assert "manual:doc#0" in d, sorted(d)
    _por_no_indice(com_notas.esp, [("manual", "doc#1", "Manual", "parte 1")])
    categorias.main()
    d = _cats_lidas(com_notas)
    assert "manual:doc#0" in d and "manual:doc" not in d, sorted(d)
