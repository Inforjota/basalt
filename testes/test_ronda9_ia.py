"""Ronda 9 (achados G6 e G7 da ronda 2) — o que vai para a IA e o que volta dela nos erros.

G6 — «retida» tinha ainda três buracos:
  · a reconciliação de uma edição offline só olhava para o texto do utilizador, mas o prompt da IA leva as TRÊS versões: uma
    password na BASE (que o utilizador apagou offline) ou na versão ACTUAL ia para o fornecedor;
  · o índice só filtrava pedaço a pedaço: uma nota que ganhou um segredo DEPOIS de indexada continuava a dar os pedaços antigos
    (limpos) à pergunta, até à próxima indexação;
  · o NOME do ficheiro não contava: uma chave no nome ia para o índice, os embeddings, as categorias e a fusão.
G7 — os erros do fornecedor repetiam a mensagem dele: um 400 pode citar o texto da nota que lhe mandámos, e num 401 a chave era
cortada a 120 caracteres ANTES de a máscara a reconhecer; e a reconciliação escrevia no registo o motivo da recusa, que cita linhas.
Cada teste falha no código anterior a esta ronda pela razão certa, menos os controlos."""
import json
import os
import subprocess

import pytest

from basalt import categorias, ia, retidas, sig, unir, web
from basalt.config import IAConfig
from basalt.segredos import tem_segredo_texto
from conftest import FM, usar_ia
from test_ronda8_erros import _responde, _Servidor

PASSWORD = "Tr0ub4dor&3"
TOKEN = "sk-" + "proj-Ab3dEf9hIjKlMnOpQrStUvWxYz0123456789AbCd"   # montado aqui: o ficheiro de teste não o tem por inteiro
PRIVADO = "ideia privada XK-9931 sobre o barco"
CAB = FM.format(nome="n")
LIMPA = FM.format(nome="barcos") + "\nOs barcos do porto precisam de velas novas antes da maré de Outubro.\n"


@pytest.fixture(autouse=True)
def _trinco_curto(monkeypatch):
    monkeypatch.setattr(web, "TRINCO_ESPERA", 1)


def _conflito(memoria, base, teu, actual, resposta=""):
    """as três versões mudam a MESMA linha → conflito → (sem G6) proposta da IA."""
    memoria.nota("n", CAB + actual)
    f = usar_ia(resposta)
    c, r = web.reconciliar("n", CAB + base, CAB + teu)
    return c, r, f


# ───────────────────────── G6: reconciliação ─────────────────────────
class TestReconciliacao:
    TEU, ACTUAL = "Acesso: está no gestor de passwords.\n", "Acesso: pedir ao responsável.\n"

    def test_password_na_base_nao_vai_a_ia(self, memoria):
        base = f"Acesso: senha do router: {PASSWORD}\n"
        assert tem_segredo_texto(base) and not tem_segredo_texto(self.TEU) and not tem_segredo_texto(self.ACTUAL)
        c, r, f = _conflito(memoria, base, self.TEU, self.ACTUAL)
        assert c == 409 and r["estado"] == "conflito"                            # o utilizador escolhe na mesma
        assert f.chamadas == 0 and all(PASSWORD not in p for p in f.prompts), "a password da BASE foi à IA na proposta"

    def test_password_na_versao_actual_nao_vai_a_ia(self, memoria):
        actual = f"Acesso: senha do router: {PASSWORD}\n"
        c, r, f = _conflito(memoria, "Acesso: ver no caderno.\n", self.TEU, actual)
        assert c == 409 and f.chamadas == 0, "a password da versão do servidor foi à IA na proposta"

    def test_controlo_sem_segredos_a_ia_propoe(self, memoria):
        c, r, f = _conflito(memoria, "Acesso: ver no caderno.\n", self.TEU, self.ACTUAL)
        assert c == 409 and f.chamadas == 1

    def test_proposta_recusada_nao_escreve_linhas_da_nota_no_registo(self, memoria, capsys):
        """G7: o motivo da recusa cita a linha que a IA inventou (feita do texto da nota) — o registo diz só que foi recusada."""
        teu = f"Acesso: {PRIVADO}.\n"
        c, r, f = _conflito(memoria, "Acesso: ver no caderno.\n", teu, self.ACTUAL,
                            resposta=CAB + f"Acesso: {PRIVADO}, mudada pela IA.\n\n===RESUMO===\nok")
        out = capsys.readouterr()
        assert f.chamadas == 1 and c == 409 and "proposta" not in r
        assert "recusada" in out.out, out.out                                    # controlo: o registo diz que houve recusa
        assert "XK-9931" not in out.out + out.err, "o motivo da recusa levou texto da nota para o registo"


# ───────────────────────── G6: índice (a nota tal como está agora) ─────────────────────────
def _pedaco(c, nome, trecho):
    c.execute("INSERT INTO pedaco (fonte, ref, titulo, trecho, hash, vec) VALUES ('nota', ?, ?, ?, 'h', x'')",
              (f"{nome}#0", nome, trecho))
    c.commit()


def _refs(c):
    return {r.split("#")[0] for f, r, *_ in sig.carregar(c) if f == "nota"}


class TestIndice:
    def test_nota_que_ganhou_um_segredo_depois_de_indexada_nao_da_os_pedacos_antigos(self, memoria):
        (memoria.real / "barcos.md").write_text(LIMPA, encoding="utf-8")
        router = memoria.real / "router.md"
        router.write_text(FM.format(nome="router") + "\nO router do escritório reinicia às segundas.\n", encoding="utf-8")
        c = sig.db()
        _pedaco(c, "barcos", "[barcos] Os barcos do porto precisam de velas novas.")
        _pedaco(c, "router", "[router] O router do escritório reinicia às segundas.")   # indexado quando estava limpa
        assert _refs(c) == {"barcos", "router"}                                  # controlo
        antes = router.stat().st_mtime_ns
        router.write_text(router.read_text(encoding="utf-8") + f"senha do router: {PASSWORD}\n", encoding="utf-8")
        os.utime(router, ns=(antes + 2_000_000, antes + 2_000_000))
        assert _refs(c) == {"barcos"}, "os pedaços antigos (limpos) de uma nota que agora tem um segredo iam para a pergunta"
        # tirado o segredo, volta (a decisão não fica presa na memória)
        router.write_text(FM.format(nome="router") + "\nO router do escritório reinicia às segundas.\n", encoding="utf-8")
        os.utime(router, ns=(antes + 4_000_000, antes + 4_000_000))
        assert _refs(c) == {"barcos", "router"}
        c.close()

    def test_pedacos_de_uma_nota_que_ja_nao_existe_nao_vao_para_a_pergunta(self, memoria):
        """apagada (ou posta de parte por ter um segredo): até à próxima indexação, os pedaços dela não servem."""
        (memoria.real / "barcos.md").write_text(LIMPA, encoding="utf-8")
        c = sig.db()
        _pedaco(c, "barcos", "[barcos] velas")
        _pedaco(c, "apagada", "[apagada] o que lá estava")
        assert _refs(c) == {"barcos"}
        c.close()


# ───────────────────────── G6: o nome do ficheiro ─────────────────────────
class TestNome:
    def test_chave_no_nome_do_ficheiro_nao_entra_no_indice(self, memoria):
        (memoria.real / "barcos.md").write_text(LIMPA, encoding="utf-8")
        (memoria.real / f"{TOKEN}.md").write_text("\nUma nota sem nada de especial.\n", encoding="utf-8")
        refs = {r.split("#")[0] for f, r, *_ in sig.fontes() if f == "nota"}
        assert "barcos" in refs                                                  # controlo
        assert TOKEN not in refs and not any(TOKEN in (tr or "") for *_, tr in sig.fontes()), "a chave do nome foi para o índice"
        assert TOKEN in retidas.nomes_retidos(memoria.real)

    def test_chave_no_nome_do_ficheiro_nao_vai_as_categorias(self, memoria):
        (memoria.real / "barcos.md").write_text(LIMPA, encoding="utf-8")
        (memoria.real / f"{TOKEN}.md").write_text("\nUma nota sem nada de especial.\n", encoding="utf-8")
        f = usar_ia('{"categorias": ["Tecnologia"]}')
        categorias.main()
        assert f.chamadas == 1 and all(TOKEN not in p for p in f.prompts), "a chave do nome foi ao modelo das categorias"
        assert TOKEN not in (memoria.dados / "categorias.json").read_text(encoding="utf-8")

    def test_fusao_nao_manda_um_nome_com_chave_a_ia(self, memoria):
        f = usar_ia("---\nname: barcos\n---\nfundida\n")
        with pytest.raises(ValueError):
            unir.fundir_llm(LIMPA, "\nnada\n", "barcos", TOKEN)
        assert f.chamadas == 0
        assert unir.fundir_llm(LIMPA, "\nnada\n", "barcos", "velas") and f.chamadas == 1   # controlo


# ───────────────────────── G7: erros do fornecedor ─────────────────────────
def _ia(porta, chave_env="BASALT_TESTE_CHAVE"):
    return ia.IA(IAConfig(fornecedor="openai", url=f"http://127.0.0.1:{porta}/v1", modelo_chat="m", chave_env=chave_env,
                          embeddings_fornecedor="nenhum", tempo_limite=5))


def _erro_de(tratar, chave_env="BASALT_TESTE_CHAVE", prompt="olá"):
    s = _Servidor(tratar)
    try:
        with pytest.raises(ia.IAIndisponivel) as ex:
            _ia(s.porta, chave_env).chat(prompt)
    finally:
        s.parar()
    return str(ex.value)


class TestErrosDoFornecedor:
    def test_400_que_repete_o_texto_da_nota_nao_o_traz(self, monkeypatch):
        monkeypatch.setenv("BASALT_TESTE_CHAVE", "chave-qualquer-de-teste")

        # o fornecedor repete o que lhe mandámos (aqui, o texto da nota que foi no prompt)
        m = _erro_de(lambda h: _responde(h, 400, json.dumps({"error": {"message": "conteúdo inválido: " + PRIVADO,
                                                                       "type": "invalid_request_error"}}).encode()), prompt=PRIVADO)
        assert "XK-9931" not in m, f"a mensagem de erro repetiu o texto da nota: {m}"
        assert "400" in m and "invalid_request_error" in m, m                    # controlo: diz-se o código e o tipo
        assert "XK-9931" not in web._erro(ia.IAIndisponivel(m))

    def test_401_com_uma_chave_comprida_sem_prefixo_nao_sai_cortada(self, monkeypatch):
        chave = "Zq8" * 50                                                       # 150 caracteres, sem forma conhecida
        monkeypatch.setenv("BASALT_TESTE_CHAVE", chave)
        m = _erro_de(lambda h: _responde(h, 401, json.dumps(
            {"error": {"message": "Incorrect API key provided: " + h.headers.get("Authorization", "").split(" ", 1)[-1]}}).encode()))
        assert chave[:24] not in m, f"um bocado da chave (cortada antes da máscara) foi para a mensagem de erro: {m[:90]}"
        assert "401" in m and "chave foi recusada" in m, m

    def test_tipo_que_nao_e_um_identificador_nao_passa(self, monkeypatch):
        monkeypatch.setenv("BASALT_TESTE_CHAVE", "chave-qualquer-de-teste")
        m = _erro_de(lambda h: _responde(h, 400, json.dumps({"error": {"type": PRIVADO}}).encode()))
        assert "400" in m and "XK-9931" not in m, m

    def test_erro_do_claude_nao_repete_o_texto_da_nota(self, monkeypatch):
        r = subprocess.CompletedProcess([], 1, stdout=json.dumps({"is_error": True, "result": "prompt recusado: " + PRIVADO}), stderr="")
        monkeypatch.setattr(ia.subprocess, "run", lambda *a, **k: r)
        cli = ia.IA(IAConfig(fornecedor="claude-cli", modelo_chat="", embeddings_fornecedor="nenhum"))
        with pytest.raises(ia.IAIndisponivel) as ex:
            cli.chat(PRIVADO)
        assert "XK-9931" not in str(ex.value), str(ex.value)
        r.stdout = json.dumps({"is_error": True, "result": "Credit balance is too low"})
        with pytest.raises(ia.IAIndisponivel) as ex:                             # controlo: o que se reconhece diz-se
            cli.chat("olá")
        assert "saldo" in str(ex.value)

