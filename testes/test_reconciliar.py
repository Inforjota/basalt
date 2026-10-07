"""web.reconciliar / web.gravar — a sincronização de uma edição feita SEM internet.

Contrato: nenhuma versão aceite se perde e NADA se grava sozinho quando alguém mexeu na nota entretanto: a junção linha a linha
e a proposta da IA ficam como PROPOSTA (409) para o utilizador confirmar. Em pastas temporárias, com uma IA simulada
(`conftest.IAFalsa`): nunca há rede."""
import threading
import time
from pathlib import Path

import pytest

from basalt import cas, web
from conftest import FM, sha, usar_ia

CAB = FM.format(nome="n")
RESUMO = "\n===RESUMO===\nok"


@pytest.fixture(autouse=True)
def _trinco_curto(monkeypatch):
    monkeypatch.setattr(web, "TRINCO_ESPERA", 1)


def reconciliar(memoria, base, teu, actual, ia="", nota="n"):
    """a cópia está em `actual`; devolve (código, resposta, ia falsa)."""
    memoria.nota(nota, actual)
    f = usar_ia(ia)
    c, r = web.reconciliar(nota, base, teu)
    return c, r, f


def intacta(memoria, actual, nota="n"):
    return memoria.ler("copia", nota) == actual


# ───────────────────────── o que a IA propõe nunca se grava sozinho ─────────────────────────
class TestIANaoGravaSozinha:
    def test_juncao_da_ia_que_muda_dose_permissao_e_ligacoes_nao_se_grava(self, memoria):
        base = CAB + "Dose 20 mg. Acesso proibido. [[alpha]] [[beta]]\n"
        teu, actual = base + "Observação do utilizador.\n", base + "Regra nova dos agentes.\n"
        ia = (CAB + "Dose 200 mg. Acesso permitido. [[beta|alpha]] [[alpha|beta]]\nObservação do utilizador.\n"
              "Regra nova dos agentes.\n") + RESUMO
        c, r, f = reconciliar(memoria, base, teu, actual, ia)
        assert f.chamadas == 1                                 # a IA foi mesmo consultada…
        assert c == 409 and r["estado"] == "conflito" and intacta(memoria, actual)
        assert not r.get("proposta")                           # …e a resposta dela, inválida, nem sequer é oferecida
        assert "200 mg" not in memoria.ler("copia", "n")

    def test_regra_inteira_apagada_pela_ia_nao_se_grava(self, memoria):
        base = CAB + "# Regras\n- Nunca apagar notas da memória.\n- Tudo com fotografia git.\n"
        teu, actual = base + "- Nova do utilizador.\n", base + "- Nova dos agentes.\n"
        ia = CAB + "# Regras\n- Tudo com fotografia git.\n- Nova do utilizador.\n- Nova dos agentes.\n" + RESUMO
        c, r, f = reconciliar(memoria, base, teu, actual, ia)
        assert c == 409 and intacta(memoria, actual) and not r.get("proposta")

    def test_proposta_boa_da_ia_fica_como_proposta_e_nunca_e_gravada(self, memoria):
        base = CAB + "Dose 20 mg.\n"
        teu, actual = base + "Observação do utilizador.\n", base + "Regra nova dos agentes.\n"
        boa = CAB + "Dose 20 mg.\nRegra nova dos agentes.\nObservação do utilizador.\n===RESUMO===\njuntei as duas"
        c, r, f = reconciliar(memoria, base, teu, actual, boa)
        assert c == 409 and intacta(memoria, actual)
        assert r["proposta_tipo"] == "ia" and r["proposta_hash"] == r["hash_actual"] == sha(actual)
        assert "Observação do utilizador." in r["proposta"] and r["resumo"] == "juntei as duas"
        assert r["teu"] == teu and r["actual"] == actual

    def test_proposta_com_a_marca_a_confirmar_e_aceite(self, memoria):
        base = CAB + "- primeira\n- segunda\n"
        teu, actual = base + "- linha nova do utilizador\n", base + "- linha dos agentes\n"
        ia = CAB + "- primeira\n- segunda\n- linha dos agentes\n- linha nova do utilizador " + web.MARCA_CONFIRMAR + "\n" + RESUMO
        c, r, f = reconciliar(memoria, base, teu, actual, ia)
        assert c == 409 and r["proposta_tipo"] == "ia" and web.MARCA_CONFIRMAR in r["proposta"]

    def test_resposta_da_ia_sem_frontmatter_nao_e_oferecida(self, memoria):
        base = CAB + "x\n"
        c, r, f = reconciliar(memoria, base, base + "A\n", base + "B\n", "isto não é uma nota")
        assert c == 409 and not r.get("proposta") and f.chamadas == 1


# ───────────────────────── a junção linha a linha (sem IA) ─────────────────────────
class TestJuncaoLinhas:
    BASE = CAB + "linha 1\nlinha 2\nlinha 3\nlinha 4\nlinha 5\n"

    def test_mudancas_em_sitios_diferentes_dao_proposta_de_linhas_sem_chamar_a_ia(self, memoria):
        teu = self.BASE.replace("linha 1", "linha 1 corrigida pelo utilizador")
        actual = self.BASE + "linha 6 dos agentes\n"
        c, r, f = reconciliar(memoria, self.BASE, teu, actual)
        assert c == 409 and r["estado"] == "conflito" and r["proposta_tipo"] == "linhas" and intacta(memoria, actual)
        assert "linha 1 corrigida pelo utilizador" in r["proposta"] and "linha 6 dos agentes" in r["proposta"]
        assert f.chamadas == 0
        assert r["proposta_hash"] == r["hash_actual"] == sha(actual)

    def test_juncao_limpa_que_muda_o_sentido_nao_se_grava_sozinha(self, memoria):
        base = (CAB + "# Serviços permitidos\n\nTexto intermédio um.\nTexto intermédio dois.\nTexto intermédio três.\n"
                "Texto intermédio quatro.\n\n- DNS\n- NTP\n")
        teu = base.replace("# Serviços permitidos", "# Serviços proibidos")
        actual = base + "- SSH\n"
        c, r, f = reconciliar(memoria, base, teu, actual)
        assert c == 409 and r["estado"] == "conflito" and intacta(memoria, actual)
        assert "proibidos" in r["proposta"] and "- SSH" in r["proposta"]
        assert f.chamadas == 0

    def test_a_proposta_de_linhas_grava_se_com_o_hash_que_ela_traz(self, memoria):
        teu = self.BASE.replace("linha 1", "linha 1 corrigida")
        actual = self.BASE + "linha 6 dos agentes\n"
        c, r, _ = reconciliar(memoria, self.BASE, teu, actual)
        cod, g = web.gravar("n", r["proposta"], r["proposta_hash"])
        assert cod == 200 and g["hash"] == sha(r["proposta"])
        assert memoria.ler("copia", "n") == r["proposta"]


# ───────────────────────── sem conflito: grava; sem IA: só o conflito ─────────────────────────
class TestSemConflito:
    def test_ninguem_mexeu_grava_e_diz_gravado(self, memoria):
        base = CAB + "x\n"
        c, r, f = reconciliar(memoria, base, base + "teu\n", base)
        assert c == 200 and r["estado"] == "gravado" and r["hash"] == sha(base + "teu\n")
        assert memoria.ler("copia", "n") == base + "teu\n" and f.chamadas == 0

    def test_a_nota_ja_esta_igual_ao_que_se_escreveu_nao_da_conflito(self, memoria):
        base = CAB + "x\n"
        c, r, _ = reconciliar(memoria, base, base + "teu\n", base + "teu\n")
        assert c == 200 and r["estado"] == "gravado"
        assert not list(memoria.esp.versoes_offline.glob("*.md"))          # ninguém mexeu: não há versão offline a guardar

    def test_ninguem_mexeu_nao_guarda_versao_offline(self, memoria):
        base = CAB + "x\n"
        reconciliar(memoria, base, base + "teu\n", base)
        assert not list(memoria.esp.versoes_offline.glob("*.md"))

    def test_segredo_no_texto_com_a_nota_ja_mexida_pelos_agentes_nao_chega_a_disco_nem_a_ia(self, memoria):
        base = CAB + "x\n"
        memoria.nota("n", base + "agentes\n")
        f = usar_ia(CAB + "x\n" + RESUMO)
        c, r = web.reconciliar("n", base, base + "chave sk-ant-" + "a" * 30 + "\n")
        assert c == 400 and f.chamadas == 0
        assert not list(memoria.esp.versoes_offline.glob("*.md"))
        assert memoria.ler("copia", "n") == base + "agentes\n"

    def test_sem_ia_o_conflito_nao_traz_proposta_e_nao_chama_nada(self, memoria):
        base = CAB + "x\n"
        memoria.nota("n", base + "agentes\n")
        f = usar_ia("", fornecedor="nenhum")
        c, r = web.reconciliar("n", base, base + "teu\n")
        assert c == 409 and r["estado"] == "conflito" and not r.get("proposta") and f.chamadas == 0
        assert r["actual"] == base + "agentes\n" and r["hash_actual"] == sha(base + "agentes\n")
        assert memoria.ler("copia", "n") == base + "agentes\n"

    def test_nota_que_nao_existe_e_404_e_nao_a_cria(self, memoria):
        usar_ia("")
        c, r = web.reconciliar("nao_existe", "a", "b")
        assert c == 404 and not (memoria.copia / "nao_existe.md").exists()

    def test_nota_com_segredo_e_recusada_antes_de_guardar_cópias(self, memoria):
        base = CAB + "x\n"
        memoria.nota("n", base)
        usar_ia("")
        c, r = web.reconciliar("n", base, base + "chave sk-ant-" + "a" * 30 + "\n")
        assert c == 400 and memoria.ler("copia", "n") == base
        assert not list(memoria.esp.versoes_offline.glob("*.md"))

    def test_versao_offline_fica_guardada_uma_so_vez_por_conteudo(self, memoria, monkeypatch):
        n = iter(range(100))
        monkeypatch.setattr(web.time, "strftime", lambda fmt, *a: f"20260101-0000{next(n):02d}")      # cada pedido, «um segundo» depois
        base = CAB + "x\n"
        teu = base + "teu\n"
        memoria.nota("n", base + "agentes\n")
        usar_ia("")
        c1, r1 = web.reconciliar("n", base, teu)
        c2, r2 = web.reconciliar("n", base, teu)
        assert c1 == c2 == 409 and r1["copia"] == r2["copia"]
        guardadas = list(memoria.esp.versoes_offline.glob("*.md"))
        assert len(guardadas) == 1 and guardadas[0].read_text(encoding="utf-8") == teu


# ───────────────────────── limites: uma de cada vez e quota da IA ─────────────────────────
class TestLimites:
    def test_com_outra_sincronizacao_a_correr_responde_depressa_e_nao_acumula_copias(self, memoria, monkeypatch):
        monkeypatch.setattr(web, "REC_ESPERA", 0.3)
        base = CAB + "x\n"
        memoria.nota("n", base + "agentes\n")
        usar_ia("")
        out = {}
        web._rec_lock.acquire()
        try:
            fio = threading.Thread(target=lambda: out.update(r=web.reconciliar("n", base, base + "teu\n")), daemon=True)
            t0 = time.time()
            fio.start()
            fio.join(6)
            preso = fio.is_alive()
            depressa = time.time() - t0
            copias = list(memoria.esp.versoes_offline.glob("*.md"))
        finally:
            web._rec_lock.release()
        fio.join(10)
        assert not preso and depressa < 3
        c, r = out["r"]
        assert c == 503 and r["estado"] == "ocupado" and not copias         # recusa ANTES de guardar cópias

    def test_o_limite_horario_de_propostas_da_ia_segue_o_toml(self, memoria):
        base = CAB + "Dose 20 mg.\n"
        f = usar_ia(CAB + "inventado\n" + RESUMO, propostas_por_hora=3)
        for i in range(8):
            memoria.nota("n", base + f"agentes {i}\n")
            web.reconciliar("n", base, base + f"teu {i}\n")
        assert f.chamadas == 3

    def test_com_o_limite_por_omissao_a_ia_nao_passa_de_12_em_15(self, memoria):
        base = CAB + "Dose 20 mg.\n"
        f = usar_ia(CAB + "inventado\n" + RESUMO)
        for i in range(15):
            memoria.nota("n", base + f"agentes {i}\n")
            web.reconciliar("n", base, base + f"teu {i}\n")
        assert f.chamadas == 12

    def test_ia_que_falha_ou_demora_da_conflito_com_o_texto_actual_e_o_hash_dele(self, memoria):
        from basalt import ia
        base = CAB + "x\n"
        actual = base + "agentes\n"
        c, r, f = reconciliar(memoria, base, base + "teu\n", actual, ia.IAIndisponivel("sem resposta"))
        assert f.chamadas == 1
        assert c == 409 and r["estado"] == "conflito" and not r.get("proposta")
        assert r["hash_actual"] == sha(actual) and r["actual"] == actual


# ───────────────────────── gravar / reconciliar com escritas a meio ─────────────────────────
class TestEscritasAMeio:
    def test_atalho_ninguem_mexeu_com_escrita_a_meio_da_409_com_o_texto_actual_e_o_hash(self, memoria, monkeypatch):
        base = CAB + "x\n"
        memoria.nota("n", base)
        usar_ia("")
        original, feito = web.gravar, []

        def gravar_com_corrida(nome, texto, h):
            if not feito:
                feito.append(1)
                (memoria.copia / "n.md").write_text(base + "outra escrita\n", encoding="utf-8")
            return original(nome, texto, h)
        monkeypatch.setattr(web, "gravar", gravar_com_corrida)
        c, r = web.reconciliar("n", base, base + "teu\n")
        agora = (memoria.copia / "n.md").read_bytes()
        assert c == 409 and r["estado"] == "conflito"
        assert r["actual"] == agora.decode() and r["hash_actual"] == sha(agora)

    def test_gravar_com_base_antiga_da_409_conflito_com_o_texto_actual_e_o_hash(self, memoria):
        memoria.nota("n", "n agora")
        c, r = web.gravar("n", "n do utilizador", sha("n antigo"))
        assert c == 409 and r["estado"] == "conflito" and r["actual"] == "n agora" and r["hash_actual"] == sha("n agora")
        assert memoria.ler("copia", "n") == "n agora"

    def test_escrita_de_outro_editor_no_mesmo_instante_ganha_e_a_web_responde_409(self, memoria, monkeypatch):
        memoria.nota("s", "s0")
        escreveu = []

        def editor_externo(alvo):
            if Path(alvo) == memoria.copia / "s.md" and not escreveu:
                escreveu.append(1)
                (memoria.copia / "s.md").write_text("s EDITOR", encoding="utf-8")
        monkeypatch.setattr(cas, "antes_de_trocar", editor_externo)       # a última janela antes de trocar
        c, r = web.gravar("s", "s WEB", sha("s0"))
        assert escreveu and c == 409 and r["estado"] == "conflito"
        assert memoria.ler("copia", "s") == "s EDITOR" and r["actual"] == "s EDITOR" and r["hash_actual"] == sha("s EDITOR")

    def test_nao_grava_por_cima_de_mudanca_da_memoria_real_ainda_por_trazer(self, memoria):
        memoria.nota("b", "b0")
        (memoria.real / "b.md").write_text("b AGENTE", encoding="utf-8")
        c, r = web.gravar("b", "b WEB", sha("b0"))
        assert c == 409 and r["estado"] == "ocupado" and r["pendente"] is True
        assert r["actual"] == "b0" and r["hash_actual"] == sha("b0")       # o 409 «pendente» traz também o texto actual e o hash dele
        assert memoria.ler("copia", "b") == "b0" and memoria.ler("real", "b") == "b AGENTE"

    def test_corrida_que_ja_passou_ainda_assim_devolve_texto_e_hash_do_que_esta_la(self, memoria, monkeypatch):
        base = CAB + "x\n"
        memoria.nota("n", base)
        usar_ia("")
        monkeypatch.setattr(web, "gravar", lambda *a: (409, {"estado": "conflito", "erro": "A nota mudou outra vez."}))
        c, r = web.reconciliar("n", base, base + "teu\n")
        assert c == 409 and r["estado"] == "conflito"
        assert r["actual"] == base and r["hash_actual"] == sha(base) and r["teu"] == base + "teu\n" and r["copia"]

    def test_a_segunda_tentativa_ocupada_responde_503_e_guarda_a_versao_offline(self, memoria, monkeypatch):
        base = CAB + "x\n"
        memoria.nota("n", base)
        usar_ia("")
        respostas = iter([(409, {"estado": "conflito", "erro": "x"}),
                          (409, {"estado": "ocupado", "erro": "y", "pendente": True, "actual": base, "hash_actual": sha(base)})])
        monkeypatch.setattr(web, "gravar", lambda *a: next(respostas))
        c, r = web.reconciliar("n", base, base + "teu\n")
        assert c == 503 and r["estado"] == "ocupado"
        assert (memoria.esp.versoes_offline / r["copia"]).read_text(encoding="utf-8") == base + "teu\n"

    def test_reconciliar_com_mudanca_da_real_por_trazer_responde_ocupado(self, memoria):
        base = CAB + "x\n"
        memoria.nota("n", base)
        (memoria.real / "n.md").write_text(base + "agentes\n", encoding="utf-8")       # o vigia ainda não trouxe
        usar_ia("")
        c, r = web.reconciliar("n", base, base + "teu\n")
        assert c == 503 and r["estado"] == "ocupado" and memoria.ler("copia", "n") == base
        assert "copia" not in r and not list(memoria.esp.versoes_offline.glob("*.md"))       # recusa logo, o cliente repete


# ───────────────────────── o validador da junção ─────────────────────────
def validar(base, teu, actual, final):
    return web._validar_juncao(base, teu, actual, final)


def _caso_codigo():
    b = CAB + "Código:\n    x = 1\n    y = 2\nfim do bloco\n"
    return b, b + "Nota do utilizador.\n", b + "Nota dos agentes.\n"


def _caso_marca():
    b = CAB + "- primeira\n- segunda\n"
    return b, b + "- linha nova do utilizador\n", b + "- linha dos agentes\n"


def _caso_lojas():
    b = CAB + "## Loja A\n- backup activo\n## Loja B\n- caixa nova\n"
    t = CAB + "## Loja A\n- backup activo\n## Loja B\n- caixa nova\n- backup activo\n"
    return b, t, b + "## Loja C\n- aberta\n"


def _caso_regra():
    b = CAB + "- Regra: nunca apagar.\n- Outra regra.\n"
    return b, b + "- Do utilizador.\n", b + "- Dos agentes.\n"


def _caso_frontmatter():
    b = CAB + "Exemplo de frontmatter:\ntype: admin\nfim.\n"
    return b, b + "Do utilizador.\n", b + "Dos agentes.\n"


def _caso_ordem_agentes():
    b = CAB + "- primeira\n- segunda\n"
    return b, b + "- do utilizador\n", b + "- terceira dos agentes\n"


def _caso_ordem_utilizador():
    b = CAB + "- primeira\n"
    return b, b + "- u1\n- u2\n", b + "- agentes\n"


def _caso_curto():
    return "a\n", "a\nb\n", "a\nc\n"


def _caso_segredo_dos_agentes():
    b = CAB + "x\n"
    return b, b + "teu\n", b + "chave sk-ant-" + "a" * 30 + "\n"


MAUS = {
    "indentação de código tirada": (_caso_codigo, lambda b, t, a: CAB + "Código:\nx = 1\n    y = 2\nfim do bloco\nNota dos agentes.\nNota do utilizador.\n"),
    "type: admin do corpo copiado para o frontmatter": (
        _caso_frontmatter,
        lambda b, t, a: CAB.replace("  type: project\n", "  type: project\ntype: admin\n") + "Exemplo de frontmatter:\ntype: admin\nfim.\nDos agentes.\nDo utilizador.\n"),
    "regra duplicada": (_caso_regra, lambda b, t, a: CAB + "- Regra: nunca apagar.\n- Regra: nunca apagar.\n- Outra regra.\n- Dos agentes.\n- Do utilizador.\n"),
    "marcador de lista tirado": (_caso_marca, lambda b, t, a: CAB + "- primeira\n- segunda\n- linha dos agentes\nlinha nova do utilizador " + web.MARCA_CONFIRMAR + "\n"),
    "ocorrência nova engolida pela antiga de outra secção": (_caso_lojas, lambda b, t, a: a),
    "linha dos agentes perdida": (_caso_marca, lambda b, t, a: CAB + "- primeira\n- segunda\n- linha nova do utilizador\n"),
    "linha do utilizador perdida": (_caso_marca, lambda b, t, a: a),
    "linhas do servidor fora de ordem": (
        _caso_ordem_agentes, lambda b, t, a: CAB + "- segunda\n- primeira\n- terceira dos agentes\n- do utilizador\n"),
    "linhas novas do utilizador fora de ordem": (
        _caso_ordem_utilizador, lambda b, t, a: CAB + "- primeira\n- agentes\n- u2\n- u1\n"),
    "marca numa linha dos agentes": (
        _caso_marca, lambda b, t, a: CAB + "- primeira\n- segunda\n- linha dos agentes " + web.MARCA_CONFIRMAR + "\n- linha nova do utilizador\n"),
    "marca colada à palavra, sem espaço": (
        _caso_marca, lambda b, t, a: CAB + "- primeira\n- segunda\n- linha dos agentes\n- linha nova do utilizador" + web.MARCA_CONFIRMAR + "\n"),
    "frontmatter perdido": (_caso_codigo, lambda b, t, a: "Código:\n    x = 1\n    y = 2\nfim do bloco\nNota dos agentes.\nNota do utilizador.\n"),
    "menos de 20 caracteres": (_caso_curto, lambda b, t, a: "a\nb\nc\n"),
    "segredo que já vinha nos agentes": (
        _caso_segredo_dos_agentes, lambda b, t, a: CAB + "x\nchave sk-ant-" + "a" * 30 + "\nteu\n"),
}
BONS = {
    "junção certa de código": (_caso_codigo, lambda b, t, a: b + "Nota dos agentes.\nNota do utilizador.\n"),
    "marca a confirmar com marcador": (_caso_marca, lambda b, t, a: CAB + "- primeira\n- segunda\n- linha dos agentes\n- linha nova do utilizador " + web.MARCA_CONFIRMAR + "\n"),
    "as duas ocorrências da Loja B": (_caso_lojas, lambda b, t, a: t + "## Loja C\n- aberta\n"),
    "ordem diferente das linhas novas": (_caso_marca, lambda b, t, a: CAB + "- primeira\n- segunda\n- linha nova do utilizador\n- linha dos agentes\n"),
}


class TestValidador:
    @pytest.mark.parametrize("nome", list(MAUS))
    def test_recusa_a_juncao_que_estraga(self, nome):
        caso, junta = MAUS[nome]
        b, t, a = caso()
        assert validar(b, t, a, junta(b, t, a)), f"aceitou: {nome}"

    @pytest.mark.parametrize("nome", list(BONS))
    def test_aceita_a_juncao_certa(self, nome):
        caso, junta = BONS[nome]
        b, t, a = caso()
        assert not validar(b, t, a, junta(b, t, a)), validar(b, t, a, junta(b, t, a))

    def test_o_motivo_da_recusa_e_um_texto_para_o_registo(self):
        b, t, a = _caso_marca()
        assert isinstance(validar(b, t, a, a), str) and validar(b, t, a, a)

    def test_juntar_linhas_devolve_o_numero_de_sobreposicoes(self):
        base = CAB + "l1\nl2\nl3\nl4\nl5\n"
        limpa, n = web._juntar_linhas(base, base.replace("l1", "L1"), base + "l6\n")
        assert n == 0 and "L1" in limpa and "l6" in limpa
        suja, n2 = web._juntar_linhas(base, base.replace("l3", "teu"), base.replace("l3", "deles"))
        assert n2 > 0
