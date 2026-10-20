"""Ronda 13 — o guarda de passwords e chaves: fins de valor que faltavam, o CR solto, descrições e os tokens do OAuth.

- Uma password só de letras dentro de parênteses, chavetas ou parênteses rectos («(senha: minhapalavrasecreta)») ou colada
  ao separador seguinte («senha: minhapalavrasecreta;utilizador: ana») passava como texto: o valor só acabava no fim da
  linha, numa pontuação seguida de espaço ou num parêntese de ABRIR.
- Um CR solto (nota antiga do Mac, ou colada de outra aplicação) não fechava a frase: o prazo da linha de cima («senha
  expira amanhã») deixava passar a data da linha de baixo como se fosse uma validade.
- Descrições correntes com uma observação a seguir («senha: desnecessária (já não usamos)») eram recusadas.
- Os tokens de acesso (`bsla_`) e de renovação (`bslr_`) do OAuth não eram reconhecidos como o token pessoal (`bslt_`).
"""
import secrets

import pytest

from basalt.segredos import SEGREDO_RX, tem_segredo_texto

# item 5: fins de valor que faltavam (valores fictícios)
FIM_DO_VALOR = [
    "(senha: minhapalavrasecreta)",
    "senha: minhapalavrasecreta;utilizador: ana",
    "senha: minhapalavrasecreta,utilizador: ana",
    "[senha: minhapalavrasecreta]",
    "{senha: minhapalavrasecreta}",
    '{"senha": "minhapalavrasecreta"}',
    "senha: minhapalavrasecreta|utilizador: ana",
    "| router | senha: minhapalavrasecreta|",
    'config = {"password": "correcthorsebattery", "user": "ana"}',
    "(password: correcthorsebattery).",
]

# item 6: o CR solto (e outros fins de linha) fecha a frase do prazo
CR_SOLTO = [
    "senha expira amanhã\rsenha: 2026-10-06",
    "a senha expira amanhã\rpassword: 10:30:59",
    "senha expira amanhã senha: 2026-10-06",
    "senha expira amanhã\x0bsenha: 2026-10-06",
    "senha expira amanhã! senha: 2026-10-06",
]

# item 7: descrições com uma observação a seguir não são passwords
DESCRICOES = [
    "senha: desnecessária (já não usamos)",
    "password: caseinsensitive (legacy system)",
    "Senha: desnecessária (acesso por certificado).",
    "A senha: desaconselhada; usar uma chave SSH.",
]

# controlos: o que já era password continua a ser, e o que já era texto continua a ser
CONTROLO_SEGREDO = [
    "senha: minhapalavrasecreta (router)",
    "senha: minhapalavrasecreta; utilizador: ana",
    "password: correcthorsebattery (wifi de casa)",
    "senha do router: `minhapalavrasecreta` (mudar em março)",
    "senha: minhaSenhaSecreta\r\n",
    "senha: 2026-10-06",
    "data: 06/10 — senha: 2026-10-06",
]
CONTROLO_TEXTO = [
    "senha expira amanhã: 2026-10-06",
    "validade da senha: 2026-10-06",
    "senha: está no cofre",
    "(senha: desconhecida)",
    "senha: minhapalavrasecreta escrita no caderno",
    "o ficheiro senha: minhapalavrasecreta.txt fica no cofre",
]


@pytest.mark.parametrize("txt", FIM_DO_VALOR)
def test_password_so_de_letras_com_outro_fim_e_recusada(txt):
    assert tem_segredo_texto(txt), f"passou como texto: {txt!r}"


@pytest.mark.parametrize("txt", CR_SOLTO)
def test_o_cr_solto_fecha_a_frase_do_prazo(txt):
    assert tem_segredo_texto(txt), f"o prazo da linha de cima desculpou a password da de baixo: {txt!r}"


@pytest.mark.parametrize("txt", DESCRICOES)
def test_descricoes_com_observacao_nao_sao_passwords(txt):
    assert not tem_segredo_texto(txt), f"falso positivo: {txt!r}"


@pytest.mark.parametrize("txt", CONTROLO_SEGREDO)
def test_controlo_continua_segredo(txt):
    assert tem_segredo_texto(txt)


@pytest.mark.parametrize("txt", CONTROLO_TEXTO)
def test_controlo_continua_texto(txt):
    assert not tem_segredo_texto(txt)


@pytest.mark.parametrize("prefixo", ["bsla_", "bslr_"])
def test_tokens_de_acesso_e_renovacao_do_oauth_sao_segredos(prefixo):
    token = prefixo + secrets.token_urlsafe(32)              # o comprimento que o servidor OAuth gera
    assert tem_segredo_texto(f"o token é {token}")
    assert SEGREDO_RX.sub("•••", f"/api/x/{token}") == "/api/x/•••"


def test_controlo_o_token_pessoal_ja_era_segredo_e_o_id_de_cliente_nao_e():
    assert tem_segredo_texto("bslt_" + secrets.token_urlsafe(32))
    assert not tem_segredo_texto("bslc_" + secrets.token_urlsafe(16))   # o id de cliente é público


def test_a_mascara_do_registo_http_apaga_os_tokens_do_oauth():
    from basalt import web
    token = "bsla_" + secrets.token_urlsafe(32)
    assert token not in web.caminho_para_registo(f"/api/anexo/{token}")
