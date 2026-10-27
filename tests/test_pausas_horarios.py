"""Pausas (bloqueio, suspensão, ausência), agenda do dia, títulos reduzidos e limpeza do histórico."""
from datetime import date, datetime, time, timedelta

import pytest

from trampotime import motor, observador
from trampotime.armazenamento import Banco
from trampotime.config import Config, aplicar_preferencias
from trampotime.modelos import Linha
from trampotime.servico import Servico

DIA = date(2026, 10, 21)


def em(h, m, s=0, dia=DIA):
    return datetime.combine(dia, time(h, m, s))


def test_horarios_reuniao_fixa_e_tickets_em_volta_do_almoco():
    linhas = [Linha(None, "Meet: Daily", 30, "meet", "09:58"),  # arredonda para 10:00
              Linha("A-1", "a", 180, "janelas"),
              Linha("B-2", "b", 120, "janelas")]
    p = motor.horarios(linhas, DIA, em(8, 0), [(em(12, 0), em(13, 0))], 15)
    assert p[0] == [["10:00", "10:30"]]
    assert p[1] == [["08:00", "10:00"], ["10:30", "11:30"]]  # cruza a reunião: dois trechos
    assert p[2] == [["11:30", "12:00"], ["13:00", "14:30"]]  # cruza o almoço


def test_pausa_por_bloqueio_desfaz_os_minutos_parados(tmp_path):
    banco = Banco(tmp_path / "t.db")
    pausas = observador.Pausas(banco)
    jira = "[DMANESTOQ-1] X - Jira - Google Chrome"
    for m in range(0, 10):  # 12:00 a 12:09 trabalhando; último teclado às 12:05
        assert pausas.passo(em(12, m), False, False, max(0, (m - 5) * 60), 60)
        observador.anotar(banco, "chrome.exe", jira, 0, 60, 300, ("DMANESTOQ",), em(12, m))
    assert not pausas.passo(em(12, 10), True, False, 300, 60)  # Win+L percebido às 12:10
    assert [a["instante"][11:16] for a in banco.amostras(DIA)][-1] == "12:04"  # 12:05-12:09 desfeito
    assert not pausas.passo(em(12, 40), True, False, 2100, 60)
    assert pausas.passo(em(13, 2), False, False, 0, 60)
    assert banco.pausas(DIA) == [{"inicio": "2026-10-21T12:05:00", "fim": "2026-10-21T13:02:00", "motivo": "bloqueado"}]


def test_suspensao_vira_pausa_pelo_salto_no_relogio(tmp_path):
    banco = Banco(tmp_path / "t.db")
    pausas = observador.Pausas(banco)
    assert pausas.passo(em(15, 0), False, False, 0, 5)
    assert pausas.passo(em(15, 40), False, False, 0, 5)  # voltou da suspensão
    assert banco.pausas(DIA) == [{"inicio": "2026-10-21T15:00:00", "fim": "2026-10-21T15:40:00", "motivo": "suspenso"}]


def test_titulo_guarda_so_o_necessario():
    m = observador.titulo_minimo
    assert m("chrome.exe", "[DMANESTOQ-1] Ajuste no saldo - JIRA - Google Chrome", "DMANESTOQ-1", "titulo") == "Ajuste no saldo"
    assert m("chrome.exe", "Meet: Avaliação Paulo - Google Chrome", None, "reuniao") == "Meet: Avaliação Paulo"
    assert m("mstsc.exe", "VM-EST01 - Conexão de Área de Trabalho Remota", "X-1", "foco") == "VM-EST01"
    assert m("chrome.exe", "Fulano de Tal - Chat - Google Chrome", "X-1", "foco") == ""
    assert m("Code.exe", "segredo.py - estoque - feature/dmanestoq-1 - Visual Studio Code", "DMANESTOQ-1", "titulo") == "estoque - feature/dmanestoq-1"


def test_titulos_de_trabalho_sao_guardados():
    from trampotime.config import GUARDAR_TITULOS as G
    m = observador.titulo_minimo
    assert m("EXCEL.EXE", "rascunho - Excel", None, None, G) == "rascunho - Excel"
    assert m("POWERPNT.EXE", "Apresentação Estoque.pptx - PowerPoint", "X-1", "foco", G) == "Apresentação Estoque.pptx - PowerPoint"
    assert m("CamtasiaStudio.exe", "Tutorial menus - Camtasia", None, None, G) == "Tutorial menus - Camtasia"
    assert m("chrome.exe", "Paulo Padilha - Chat - Google Chrome", None, None, G) == "Paulo Padilha - Chat"
    assert m("chrome.exe", "Vídeo sem título - Google Vids - Google Chrome", None, None, G) == "Vídeo sem título - Google Vids"
    assert m("chrome.exe", "Notas - Google Docs - Google Chrome", None, None, G) == "Notas - Google Docs"
    assert m("chrome.exe", "TOTVS SA: agenda - Semana de 18 de outubro - Google Chrome", None, None, G) == "TOTVS SA: agenda - Semana de 18 de outubro"
    assert m("chrome.exe", "YouTube - Google Chrome", None, None, G) == ""  # fora da lista: descartado
    assert m("EXCEL.EXE", "rascunho - Excel", None, None, ()) == ""  # lista vazia: nada é guardado

    r = observador.rotulo_do_app
    assert r("EXCEL.EXE", "rascunho - Excel") == "Excel: rascunho"
    assert r("WINWORD.EXE", "") == "Word"
    assert r("chrome.exe", "Paulo Padilha - Chat") == "Paulo Padilha - Chat"


def test_vscode_guarda_pasta_e_branch_sem_o_arquivo():
    p = observador.projeto_do_vscode
    assert p("● servico.py - trampotime - feature/dmanestoq-1 - Visual Studio Code [Administrator]") == "trampotime - feature/dmanestoq-1"
    assert p("trampotime - main - Visual Studio Code") == "trampotime - main"  # sem arquivo aberto
    assert p("Visual Studio Code") == ""
    assert observador.rotulo_do_app("Code.exe", "trampotime - main") == "VS Code: trampotime (main)"


def test_onde_voce_trabalhou_em_cada_ticket(tmp_path):
    banco = Banco(tmp_path / "t.db")
    banco.definir_foco("DMANESTOQ-1")
    for m in range(0, 10):
        observador.anotar(banco, "Code.exe", "a.p - estoque - feature/dmanestoq-1 - Visual Studio Code", 0, 60, 300,
                          ("DMANESTOQ",), em(9, m))
    for m in range(10, 15):
        observador.anotar(banco, "mstsc.exe", "VM-EST01 - Conexão de Área de Trabalho Remota", 0, 60, 300,
                          ("DMANESTOQ",), em(9, m))
    for m in range(15, 17):
        observador.anotar(banco, "chrome.exe", "[DMANESTOQ-1] Menus - JIRA - Google Chrome", 0, 60, 300,
                          ("DMANESTOQ",), em(9, m))
    onde = observador.periodos(banco.amostras(DIA))["onde"]
    assert onde == {"DMANESTOQ-1": ["VS Code: estoque (feature/dmanestoq-1)", "VM VM-EST01", "Jira"]}


def test_url_de_aba_carregando_nao_vira_resumo(tmp_path):
    banco = Banco(tmp_path / "t.db")
    for m in range(0, 5):  # aba carregando: o título é o endereço
        observador.anotar(banco, "chrome.exe", "jiraproducao.totvs.com.br/browse/DMANESTOQ-15770 - Google Chrome",
                          0, 60, 300, ("DMANESTOQ",), em(9, m))
    for m in range(5, 7):
        observador.anotar(banco, "chrome.exe", "[DMANESTOQ-15770] Validação Menus - JIRA - Google Chrome",
                          0, 60, 300, ("DMANESTOQ",), em(9, m))
    assert observador.tickets_observados(banco.amostras(DIA), 0)[0].resumo == "Validação Menus"
    so_url = Banco(tmp_path / "u.db")
    observador.anotar(so_url, "chrome.exe", "jiraproducao.totvs.com.br/browse/DMANESTOQ-1 - Google Chrome", 0, 60, 300,
                      ("DMANESTOQ",), em(9, 0))
    assert observador.tickets_observados(so_url.amostras(DIA), 0)[0].resumo == "DMANESTOQ-1"
    assert observador.parece_url("dev.azure.com/totvstfs/Linha-Datasul/_git/EMS2/pullrequest/8774")
    assert not observador.parece_url("Validação Menus")
    assert observador.rotulo_do_app("chrome.exe", "") == "Chrome"


def test_historico_do_dia(tmp_path):
    from trampotime.config import GUARDAR_TITULOS as G
    banco = Banco(tmp_path / "t.db")
    banco.definir_foco("DMANESTOQ-1")
    for m in range(0, 20):
        observador.anotar(banco, "chrome.exe", "[DMANESTOQ-2] Menus - JIRA - Google Chrome", 0, 60, 300,
                          ("DMANESTOQ",), em(9, m), guardar=G)
    for m in range(20, 40):
        observador.anotar(banco, "EXCEL.EXE", "Validação menus - Excel", 0, 60, 300, ("DMANESTOQ",), em(9, m), guardar=G)
    for m in range(40, 55):
        observador.anotar(banco, "Code.exe", "x", 0, 60, 300, ("DMANESTOQ",), em(9, m),
                          chamada=("chrome.exe", "Meet: Daily - Google Chrome"), guardar=G)
    h = observador.periodos(banco.amostras(DIA))["historico"]
    assert [(b["inicio"], b["fim"], b["tipo"], b["nome"], b["detalhes"]) for b in h] == [
        ("09:00", "09:20", "ticket", "DMANESTOQ-2", []),
        ("09:20", "09:40", "ticket", "DMANESTOQ-1", ["Excel: Validação menus"]),
        ("09:40", "09:55", "reuniao", "Meet: Daily", []),
    ]


def test_reduzir_titulos_antigos_uma_vez(tmp_path):
    banco = Banco(tmp_path / "t.db")
    banco.registrar_amostra(em(9, 0), "chrome.exe", "Fulano de Tal - Chat - Google Chrome", 5, None, None)
    banco.registrar_amostra(em(9, 1), "chrome.exe", "Meet: Daily - Google Chrome", 5, None, "reuniao")
    banco.reduzir_titulos(observador.titulo_minimo)
    assert [a["titulo"] for a in banco.amostras(DIA)] == ["", "Meet: Daily"]
    assert observador.reunioes_observadas(banco.amostras(DIA), 0)[0].titulo == "Meet: Daily"  # continua funcionando


def test_limpeza_so_de_dias_lancados_e_com_3_dias_de_folga(tmp_path):
    banco = Banco(tmp_path / "t.db")
    hoje = date(2026, 10, 30)
    for d in (date(2026, 10, 20), date(2026, 10, 27), date(2026, 10, 22)):
        banco.registrar_amostra(em(9, 0, dia=d), "x.exe", "", 5, "A-1", "titulo")
    banco.salvar(date(2026, 10, 20), "lancado", 0, [])
    banco.salvar(date(2026, 10, 27), "lancado", 0, [])  # lançado, mas dentro dos 3 dias
    # 22/10 não foi lançado: fica
    assert banco.limpar_detalhes(hoje, 3) == 1
    assert banco.amostras(date(2026, 10, 20)) == []
    assert banco.amostras(date(2026, 10, 27)) and banco.amostras(date(2026, 10, 22))
    assert banco.carregar(date(2026, 10, 20))[0] == "lancado"  # a planilha do dia continua


def test_historico_minimo_de_3_dias():
    with pytest.raises(ValueError):
        aplicar_preferencias(Config(), {"manter_dias": 2})
    assert aplicar_preferencias(Config(), {"manter_dias": 7}).manter_dias == 7


def test_rascunho_traz_inicio_e_fim(tmp_path):
    banco = Banco(tmp_path / "t.db")
    for m in range(0, 30):
        observador.anotar(banco, "chrome.exe", "[DMANESTOQ-1] X - Jira", 0, 60, 300, ("DMANESTOQ",), em(8, 7 + m))
    for m in range(0, 20):
        observador.anotar(banco, "Code.exe", "x", 0, 60, 300, ("DMANESTOQ",), em(10, m),
                          chamada=("chrome.exe", "Meet: Daily - Google Chrome"))
    dados = Servico(Config(banco=tmp_path / "t.db", ticket_reunioes="DMANESTOQ-9"), None, banco, lambda *a: []).gerar(DIA)
    por_acao = {l["acao"]: l for l in dados["linhas"]}
    assert por_acao["Meet: Daily"]["periodos"] == [["10:00", "10:15"]]  # 20 min viram 15 no bloco
    assert por_acao["X"]["periodos"] == [["08:00", "10:00"], ["10:15", "15:30"]]  # começa às 08:07 -> 08:00
