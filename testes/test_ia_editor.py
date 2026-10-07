"""web.ia — a IA do editor (resumir, reescrever, corrigir, continuar) — e as recusas de /api/pergunta sem IA.

Contrato: IA desligada → 503 {"ia": false}; a selecção não passa de [ia] texto_maximo (nem em silêncio: 400 com os números);
há um limite por hora ([ia] pedidos_por_hora) que só as chamadas à IA gastam; um pedido de cada vez (409); nada que pareça uma
password ou uma chave vai à IA nem volta dela. Funções chamadas em processo com uma IA simulada (`conftest.IAFalsa`): nunca
há rede. Um teste liga o toml ao HTTP (limites lidos do ficheiro)."""
import time

import pytest

from basalt import ia as ia_mod, web
from conftest import usar_ia

SEGREDO = "a chave é sk-ant-" + "a1B2" * 8
TEXTO = "O barco sai às oito da manhã."


def texto_de(n):
    """um texto de n caracteres (sem espaços nas pontas, que o `web.ia` aparava) e sem nada que pareça um segredo."""
    return ("barco " * (n // 6 + 2))[:n - 1] + "x"


def pedir(texto=TEXTO, acao="resumir", contexto=""):
    return web.ia(acao, texto, contexto)


# ───────────────────────── recusas antes de chegar à IA ─────────────────────────
def test_ia_desligada_da_503_com_ia_false_e_nao_chama_nada():
    f = usar_ia(fornecedor="nenhum")
    c, r = pedir()
    assert c == 503 and r["ia"] is False and f.chamadas == 0


def test_acao_desconhecida_da_400_sem_chamar_a_ia():
    f = usar_ia("ok")
    c, r = pedir(acao="traduzir")
    assert c == 400 and "Acção" in r["erro"] and f.chamadas == 0


@pytest.mark.parametrize("texto", ["", "   \n\t ", None])
def test_sem_texto_seleccionado_da_400(texto):
    f = usar_ia("ok")
    c, r = pedir(texto)
    assert c == 400 and "Selecciona" in r["erro"] and f.chamadas == 0


def test_a_selecao_acima_do_maximo_por_omissao_e_recusada_com_os_numeros_e_no_limite_passa():
    f = usar_ia("Resumo.")
    c, r = pedir(texto_de(6001))
    assert c == 400 and "6001" in r["erro"] and "6000" in r["erro"] and f.chamadas == 0     # não corta em silêncio
    c, r = pedir(texto_de(6000))
    assert c == 200 and f.chamadas == 1
    assert len(f.prompts[0].split("=== TEXTO ===\n")[1]) == 6000        # a IA recebe a selecção INTEIRA


def test_o_texto_maximo_configurado_manda():
    f = usar_ia("Resumo.", texto_maximo=50)
    c, r = pedir(texto_de(51))
    assert c == 400 and "51" in r["erro"] and "50" in r["erro"] and f.chamadas == 0
    assert pedir(texto_de(50))[0] == 200 and f.chamadas == 1


def test_o_tamanho_conta_depois_de_aparar_os_espacos():
    f = usar_ia("Resumo.", texto_maximo=50)
    assert pedir("   " + texto_de(50) + "\n\n  ")[0] == 200 and f.chamadas == 1


@pytest.mark.parametrize("onde", ["texto", "contexto"])
def test_segredo_no_texto_ou_no_contexto_nunca_vai_a_ia(onde):
    f = usar_ia("ok")
    c, r = pedir(*((SEGREDO, "resumir", "") if onde == "texto" else (TEXTO, "resumir", "nota com " + SEGREDO)))
    assert c == 400 and "password ou chave" in r["erro"] and f.chamadas == 0
    assert not f.prompts


def test_password_escrita_como_senha_igual_valor_tambem_nao_vai_a_ia():
    f = usar_ia("ok")
    c, _ = pedir("o wifi: senha = Xy7!qwerty9")
    assert c == 400 and f.chamadas == 0


# ───────────────────────── a resposta da IA ─────────────────────────
def test_resposta_boa_devolve_texto_e_modelo_sem_cercas_de_codigo():
    usar_ia("```markdown\nTexto limpo.\n```")
    c, r = pedir()
    assert (c, r["texto"], r["modelo"]) == (200, "Texto limpo.", "modelo-de-teste")


def test_resposta_vazia_da_502():
    usar_ia("   ")
    c, r = pedir()
    assert c == 502 and "não respondeu" in r["erro"]


def test_ia_que_falha_da_502_e_o_pedido_seguinte_funciona():
    f = usar_ia(ia_mod.IAIndisponivel("sem rede"))
    c, r = pedir()
    assert c == 502 and "demorou" in r["erro"]
    f.resposta = "Resumo."
    assert pedir()[0] == 200                   # o trinco do editor largou-se mesmo com a falha


def test_resposta_com_segredo_nao_se_mostra():
    usar_ia("A chave é " + SEGREDO)
    c, r = pedir()
    assert c == 502 and "sk-ant" not in str(r)


def test_o_pedido_leva_a_acao_o_texto_e_so_os_primeiros_4000_do_contexto():
    f = usar_ia("ok")
    contexto = ("ctx " * 1000) + "FIMDOCONTEXTO"
    assert pedir(TEXTO, "corrigir", contexto)[0] == 200
    p = f.prompts[0]
    assert web.IA_ACOES["corrigir"] in p and TEXTO in p and "ctx ctx" in p and "FIMDOCONTEXTO" not in p
    assert pedir(TEXTO, "continuar", "nota curta FIM")[0] == 200
    assert "nota curta FIM" in f.prompts[1] and web.IA_ACOES["continuar"] in f.prompts[1]


# ───────────────────────── um de cada vez e por hora ─────────────────────────
def test_um_pedido_de_cada_vez_o_segundo_da_409_sem_chamar_a_ia():
    f = usar_ia("ok")
    assert web._ia_editor_lock.acquire(blocking=False)
    try:
        c, r = pedir()
    finally:
        web._ia_editor_lock.release()
    assert c == 409 and f.chamadas == 0
    assert pedir()[0] == 200


def test_o_limite_por_hora_segue_o_pedidos_por_hora_e_da_429():
    f = usar_ia("ok", pedidos_por_hora=3)
    assert [pedir()[0] for _ in range(3)] == [200, 200, 200]
    c, r = pedir()
    assert c == 429 and "3" in r["erro"] and f.chamadas == 3


def test_o_limite_por_omissao_e_40_por_hora():
    f = usar_ia("ok")
    assert [pedir()[0] for _ in range(41)].count(200) == 40 and f.chamadas == 40


def test_so_as_chamadas_a_ia_gastam_quota_as_recusas_nao():
    f = usar_ia("ok", pedidos_por_hora=2, texto_maximo=100)
    for _ in range(3):
        assert pedir("")[0] == 400 and pedir(texto_de(101))[0] == 400 and pedir(SEGREDO)[0] == 400 and pedir(acao="x")[0] == 400
    assert pedir()[0] == 200 and pedir()[0] == 200 and pedir()[0] == 429 and f.chamadas == 2


def test_a_quota_volta_passada_uma_hora_e_nao_antes():
    f = usar_ia("ok", pedidos_por_hora=2)
    agora = time.time()
    web._ia_usos[:] = [agora - 3601, agora - 7200, agora - 99999]      # tudo de há mais de uma hora
    assert pedir()[0] == 200 and pedir()[0] == 200 and pedir()[0] == 429
    web._ia_usos[:] = [agora - 3500, agora - 3000]                     # ainda dentro da hora
    assert pedir()[0] == 429 and f.chamadas == 2


# ───────────────────────── pelo HTTP: o toml manda, e sem IA ─────────────────────────
def test_sem_ia_o_editor_e_a_pergunta_respondem_503_com_ia_false(servidor_local):
    s = servidor_local()
    for caminho, corpo in (("/api/ia", {"acao": "resumir", "texto": TEXTO}), ("/api/pergunta", {"q": "quando sai o barco?"})):
        r = s.pedido("POST", caminho, corpo)
        assert r.codigo == 503 and r.json["ia"] is False, caminho
    assert s.get("/api/eu").json["ia"]["chat"] == "nenhum"


def test_pergunta_vazia_responde_sem_precisar_de_ia(servidor_local):
    s = servidor_local()
    r = s.pedido("POST", "/api/pergunta", {"q": "   "})
    assert r.codigo == 200 and r.json == {"resposta": "Escreve uma pergunta.", "fontes": []}


def test_os_limites_do_toml_chegam_ao_editor_pelo_http(servidor_local):
    s = servidor_local(extra='[ia]\nfornecedor = "openai"\nurl = "http://127.0.0.1:9/v1"\nmodelo_chat = "m"\n'
                             'texto_maximo = 50\npedidos_por_hora = 2\n')
    r = s.pedido("POST", "/api/ia", {"acao": "resumir", "texto": texto_de(51)})
    assert r.codigo == 400 and "50" in r.json["erro"]
    pedido = {"acao": "resumir", "texto": texto_de(50)}
    assert [s.pedido("POST", "/api/ia", pedido).codigo for _ in range(3)] == [502, 502, 429]   # o fornecedor não existe: 502 gasta quota
    assert s.get("/api/eu").json["ia"]["chat"] == "openai"
