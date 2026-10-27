"""Sugestão de ticket pela descrição (KNN)."""
from datetime import date, datetime

from trampotime import observador, sugestao
from trampotime.armazenamento import Banco
from trampotime.modelos import Linha

EXEMPLOS = [
    ("[DTS EST] Validação Nomes Menus dos Módulos Estoque", "DMANESTOQ-15770"),
    ("Pacote - Entrega 21/10 - Expedição 26/10", "DMANESTOQ-15684"),
    ("Diária + reabastecimento do pacote", "DMANESTOQ-15684"),
    ("Geração de documentação de APIs", "DMANESTOQ-15765"),
    ("TS 12.1.2611 - Central de Requisição", "DMANESTOQ-15761"),
]


def test_sugere_pelo_texto_parecido_sem_acento_e_com_palavras_trocadas():
    s = sugestao.sugerir("validacao dos menus do estoque", EXEMPLOS)
    assert s[0]["chave"] == "DMANESTOQ-15770" and s[0]["confianca"] >= 0.3
    assert sugestao.sugerir("reabastecimento pacote expedição", EXEMPLOS)[0]["chave"] == "DMANESTOQ-15684"
    assert sugestao.sugerir("investigando relatório da central de requisição", EXEMPLOS)[0]["chave"] == "DMANESTOQ-15761"


def test_sem_parecido_nao_sugere():
    assert sugestao.sugerir("almoço com a família", EXEMPLOS) == []
    assert sugestao.sugerir("", EXEMPLOS) == []
    assert sugestao.sugerir("qualquer coisa", []) == []


def test_exemplos_vem_do_jira_e_das_planilhas(tmp_path):
    banco = Banco(tmp_path / "t.db")
    observador.anotar(banco, "chrome.exe", "[DMANESTOQ-15770] Validação Nomes Menus - JIRA - Google Chrome", 0, 60, 300,
                      ("DMANESTOQ",), datetime(2026, 10, 21, 9, 0))
    observador.anotar(banco, "Code.exe", "a.p - estoque - feature/dmanestoq-1 - Visual Studio Code", 0, 60, 300,
                      ("DMANESTOQ",), datetime(2026, 10, 21, 9, 1))  # VS Code não vira exemplo
    banco.salvar(date(2026, 10, 20), "lancado", 0, [Linha("DMANESTOQ-15684", "Diária + reabastecimento", 60, "manual"),
                                                    Linha(None, "Completar a jornada", 30, "completar")])
    assert sorted(banco.exemplos_de_tickets(observador.NAVEGADORES)) == [
        ("Diária + reabastecimento", "DMANESTOQ-15684"), ("Validação Nomes Menus", "DMANESTOQ-15770")]
