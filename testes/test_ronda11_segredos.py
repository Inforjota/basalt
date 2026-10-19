"""Ronda 11 — passwords só de letras com aspas, crases, ênfase ou uma observação a seguir.

O guarda só reconhecia uma password só de letras «sozinha no fim da linha»: bastava escrevê-la entre aspas
(«senha: "minhapalavracomplicada"»), entre crases (código Markdown), a negrito, ou acrescentar uma observação
(«senha: minhapalavrasecreta (router)», «…; utilizador: ana») para ela ser gravada como texto.

Agora o valor pode vir envolvido em aspas («» “” '' ""), crases ou marcas de ênfase, e acabar no fim da linha, numa
pontuação, num parêntese, num «|», num travessão ou num « - ». A «falha fechada» mantém-se: uma maiúscula no meio
conta sempre, e só as palavras correntes da lista (e as descrições de uma propriedade) ficam como texto.
"""
import pytest

from basalt.segredos import tem_segredo_texto

# os casos que passavam como texto
G1 = [
    'senha: "minhapalavracomplicada"',
    "senha: `minhaSenhaSecreta`",
    "senha: minhapalavrasecreta (router)",
    "senha: minhapalavrasecreta; utilizador: ana",
]

# variantes do mesmo defeito: outras aspas, ênfase, outros separadores, a meio de uma nota
G1B = [
    "senha: 'minhapalavracomplicada'",
    "senha: «minhapalavracomplicada»",
    "senha: **minhapalavrasecreta**",
    "password: correcthorsebattery (wifi de casa)",
    "senha: minhapalavrasecreta, utilizador: ana",
    "senha: minhapalavrasecreta — router da sala",
    "senha: minhapalavrasecreta | user: ana",
    "senha: minhapalavrasecreta - mudar em março",
    "senha do router: `minhapalavrasecreta` (mudar em março)",
    "notas do router:\nsenha: minhapalavrasecreta; utilizador: ana\nfim",
    "senha: “OlaMundoGrande” (wifi)",
]

# o mesmo defeito por outro caminho: fins de linha do Windows (CR LF) ou um CR solto. O «fim da linha» da regra só via o LF,
# e uma nota escrita ou sincronizada a partir do Windows deixava passar QUALQUER password só de letras — até as que a regra
# já recusava com LF, e até as de maiúscula no meio (a «falha fechada»).
G1C = [
    "senha: minhapalavrasecreta\r\n",
    "senha: minhapalavrasecreta\r\nutilizador: ana",
    'senha: "minhapalavracomplicada"\r\n',
    "senha: `minhapalavrasecreta`\r\nfim",
    "senha: minhaSenhaSecreta\r\n",
    "senha: minhapalavrasecreta \r\n",
    "senha: minhapalavrasecreta;\r\nutilizador: ana",
    "notas do router:\r\nsenha: minhapalavrasecreta\r\nfim",
    "senha: minhapalavrasecreta\r",
]

# controlos com CR LF: falar de passwords sem as escrever continua a gravar, também numa nota do Windows
CONTROLO_TEXTO_CRLF = [
    "senha do router: está no cofre\r\n",
    "senha: desconhecida\r\n",
    "Estado da senha: sincronizada\r\n",
    "senha: minhapalavrasecreta escrita no caderno\r\n",
    "a senha: confirmada, obrigado\r\n",
]

# controlos: o que já era recusado continua a sê-lo
CONTROLO_SEGREDO = [
    "senha: minhapalavrasecreta",
    "password: correcthorsebatterystaple",
    "senha: TopSecreto",
    "senha: minhapalavrasecreta.",
    "password do router: Tr0ub4dor&3xyz",
]

# controlos: falar de passwords sem as escrever (também com aspas e com uma observação a seguir) continua a gravar
CONTROLO_TEXTO = [
    "senha do router: está no cofre",
    "a password: pede à Ana",
    "palavra-passe = guardada no segredo",
    "mudar a password todos os meses",
    "pwd: /home/utilizador/notas",
    "password: /root/.config/cofre",
    "senha do NAS: https://cofre.exemplo.pt",
    "password: ver_no_cofre",
    "Estado da senha: sincronizada",
    "Tipo de password: alfanumérica",
    "senha: criptografada",
    "senha: desconhecida",
    'senha: "criptografada"',
    "Estado da senha: sincronizada (verificado hoje)",
    "Tipo de password: alfanumérica (12 caracteres)",
    "senha: desconhecida; ver com a Ana",
    'senha: "está no cofre"',
    "senha: guardada no cofre (ver nota)",
    "senha: `confidencial`",
    "a senha: confirmada, obrigado",
    "senha: minhapalavrasecreta escrita no caderno",
]


@pytest.mark.parametrize("texto", G1)
def test_password_so_de_letras_com_aspas_crases_ou_observacao_e_segredo(texto):
    assert tem_segredo_texto(texto), f"«{texto}» devia ser recusado"


@pytest.mark.parametrize("texto", G1B)
def test_variantes_de_aspas_enfase_e_separadores_sao_segredo(texto):
    assert tem_segredo_texto(texto), f"«{texto}» devia ser recusado"


@pytest.mark.parametrize("texto", G1C)
def test_password_so_de_letras_com_fim_de_linha_do_windows_e_segredo(texto):
    assert tem_segredo_texto(texto), f"{texto!r} devia ser recusado"


@pytest.mark.parametrize("texto", CONTROLO_TEXTO_CRLF)
def test_controlo_falar_de_passwords_com_fim_de_linha_do_windows_continua_texto(texto):
    assert not tem_segredo_texto(texto), f"{texto!r} devia gravar-se"


@pytest.mark.parametrize("texto", CONTROLO_SEGREDO)
def test_controlo_password_sozinha_continua_segredo(texto):
    assert tem_segredo_texto(texto)


@pytest.mark.parametrize("texto", CONTROLO_TEXTO)
def test_controlo_falar_de_passwords_continua_texto(texto):
    assert not tem_segredo_texto(texto), f"«{texto}» devia gravar-se"
