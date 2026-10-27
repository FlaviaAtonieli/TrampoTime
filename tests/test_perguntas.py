"""Perguntas do TrampoTime: juntar trechos, associar atividades a demandas e rotinas."""
from datetime import date, datetime, time, timedelta

from trampotime import observador, perguntas, rotinas
from trampotime.armazenamento import Banco
from trampotime.config import GUARDAR_TITULOS, Config
from trampotime.modelos import Linha
from trampotime.servico import Servico

DIA = date(2026, 10, 22)  # quinta
P = ("DMANESTOQ",)
JIRA = "[DMANESTOQ-15770] Validação Nomes Menus dos Módulos Estoque - JIRA - Google Chrome"


def em(h, m, dia=DIA):
    return datetime.combine(dia, time(h, m))


def anotar(banco, app, titulo, de, ate, chamada=None):
    t = de
    while t < ate:
        observador.anotar(banco, app, titulo, 0, 60, 300, P, t, chamada=chamada, guardar=GUARDAR_TITULOS)
        t += timedelta(minutes=1)


def servico(tmp_path, banco):
    return Servico(Config(banco=tmp_path / "t.db", intervalo_segundos=60, projetos=P), None, banco, lambda *a: [])


def test_juntar_dois_trechos_da_mesma_demanda(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(8, 30), em(8, 48))
    anotar(banco, "chrome.exe", "YouTube - Google Chrome", em(8, 48), em(8, 51))  # 3 min no meio
    # 08:51 a 09:00 sem nada (parada)
    anotar(banco, "chrome.exe", JIRA, em(9, 0), em(9, 19))
    s = servico(tmp_path, banco)
    ps = [p for p in s.perguntas(DIA) if p["tipo"] == "juntar"]
    assert len(ps) == 1
    # os 3 min de YouTube já herdaram a demanda (trecho curto); o buraco real é 08:51-09:00
    assert "08:30–08:51 e 09:00–09:19" in ps[0]["pergunta"] and "9 min do meio" in ps[0]["pergunta"]
    assert "sem atividade 9 min" in ps[0]["detalhe"]

    s.responder(DIA, ps[0]["id"], True)
    t = observador.tickets_observados(banco.amostras(DIA), 0)
    assert [(x.chave, x.peso) for x in t] == [("DMANESTOQ-15770", 49)]  # 18 + 3 + 9 + 19
    assert [p for p in s.perguntas(DIA) if p["tipo"] == "juntar"] == []


def test_nao_junta_se_tem_reuniao_no_meio(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(8, 30), em(8, 48))
    anotar(banco, "Code.exe", "x", em(8, 48), em(9, 0), chamada=("chrome.exe", "Meet: Daily - Google Chrome"))
    anotar(banco, "chrome.exe", JIRA, em(9, 0), em(9, 19))
    assert [p for p in servico(tmp_path, banco).perguntas(DIA) if p["tipo"] == "juntar"] == []


def test_associar_planilha_a_demanda_vira_regra(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(9, 0), em(9, 10))
    anotar(banco, "EXCEL.EXE", "Validação menus estoque - Excel", em(9, 10), em(9, 35))
    anotar(banco, "chrome.exe", JIRA, em(9, 35), em(9, 40))
    s = servico(tmp_path, banco)
    ps = [p for p in s.perguntas(DIA) if p["tipo"] == "associar"]
    assert len(ps) == 1 and ps[0]["chave"] == "DMANESTOQ-15770" and ps[0]["rotulo"] == "Excel: Validação menus estoque"
    assert "texto parecido" in ps[0]["detalhe"] and "usada junto hoje" in ps[0]["detalhe"]

    s.responder(DIA, ps[0]["id"], True)
    assert banco.associacoes() == {"Excel: Validação menus estoque": "DMANESTOQ-15770"}
    # daqui para frente a planilha conta sozinha, mesmo com outro foco
    banco.definir_foco("DMANESTOQ-1")
    assert observador.anotar(banco, "EXCEL.EXE", "Validação menus estoque - Excel", 0, 60, 300, P, em(10, 0),
                             guardar=GUARDAR_TITULOS) == "DMANESTOQ-15770"


def test_responder_nao_nao_pergunta_de_novo(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(9, 0), em(9, 10))
    anotar(banco, "EXCEL.EXE", "Validação menus estoque - Excel", em(9, 10), em(9, 35))
    s = servico(tmp_path, banco)
    p = next(p for p in s.perguntas(DIA) if p["tipo"] == "associar")
    s.responder(DIA, p["id"], False)
    assert all(x["id"] != p["id"] for x in s.perguntas(DIA))
    assert banco.associacoes() == {}


def test_atividade_sem_relacao_nao_pergunta(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(9, 0), em(9, 10))
    anotar(banco, "EXCEL.EXE", "Orçamento festa - Excel", em(14, 0), em(14, 30))  # longe no tempo e no texto
    assert [p for p in servico(tmp_path, banco).perguntas(DIA) if p["tipo"] == "associar"] == []


def _dias_confirmados(banco, n, daily=True):
    d = DIA
    for _ in range(n):
        d -= timedelta(days=1)
        while d.weekday() >= 5:
            d -= timedelta(days=1)
        linhas = [Linha("DMANESTOQ-15770", "Validação", 420, "janelas")]
        if daily:
            linhas.append(Linha("DMANESTOQ-1", "Meet: Daily Estoque", 15, "meet", "09:00"))
        banco.salvar(d, "lancado", 0, linhas)


def test_rotina_de_reuniao_diaria(tmp_path):
    banco = Banco(tmp_path / "t.db")
    _dias_confirmados(banco, 4)
    r = rotinas.reunioes_recorrentes(banco.planilhas_confirmadas(), DIA)
    assert r == [{"nome": "Meet: Daily Estoque", "dias": 4, "de": 4, "inicio": "09:00", "minutos": 15,
                  "chave": "DMANESTOQ-1", "chave_constante": True}]
    assert rotinas.reunioes_recorrentes(banco.planilhas_confirmadas()[:2], DIA) == []  # 2 dias ainda não é rotina


def test_rotina_vira_pergunta_e_aviso_de_reuniao_que_faltou(tmp_path):
    banco = Banco(tmp_path / "t.db")
    _dias_confirmados(banco, 4)
    anotar(banco, "chrome.exe", JIRA, em(8, 0), em(8, 30))
    ps = perguntas.gerar(banco.amostras(DIA), [], {}, set(),
                         rotinas.reunioes_recorrentes(banco.planilhas_confirmadas(), DIA), {}, em(9, 40))
    ids = [p["id"] for p in ps if p["tipo"] == "rotina"]
    assert "rotina-fixar:Meet: Daily Estoque:DMANESTOQ-1" in ids
    assert "rotina-faltou:2026-10-22:Meet: Daily Estoque" in ids
    # antes do horário dela terminar, ainda não pergunta se faltou
    cedo = perguntas.gerar(banco.amostras(DIA), [], {}, set(),
                           rotinas.reunioes_recorrentes(banco.planilhas_confirmadas(), DIA), {}, em(9, 10))
    assert not any(p["id"].startswith("rotina-faltou") for p in cedo)


def test_responder_rotina(tmp_path, monkeypatch):
    banco = Banco(tmp_path / "t.db")
    _dias_confirmados(banco, 4)
    s = servico(tmp_path, banco)
    monkeypatch.setattr(s, "perguntas", lambda dia: perguntas.gerar(
        banco.amostras(dia), [], banco.associacoes(), banco.respondidas(),
        rotinas.reunioes_recorrentes(banco.planilhas_confirmadas(), dia), {}, em(10, 0)))
    s.responder(DIA, "rotina-faltou:2026-10-22:Meet: Daily Estoque", True)
    r = observador.reunioes_observadas(banco.amostras(DIA), 0)
    assert [(x.titulo, x.minutos, x.inicio) for x in r] == [("Meet: Daily Estoque", 15, "09:00")]
    s.responder(DIA, "rotina-fixar:Meet: Daily Estoque:DMANESTOQ-1", True)
    assert banco.associacoes()["Meet: Daily Estoque"] == "DMANESTOQ-1"
    linhas = s.gerar(DIA)["linhas"]
    assert next(l for l in linhas if l["acao"] == "Meet: Daily Estoque")["chave"] == "DMANESTOQ-1"


def test_uso_anterior_aumenta_a_confianca(tmp_path):
    banco = Banco(tmp_path / "t.db")
    ontem = date(2026, 10, 21)
    banco.gravar_uso(ontem, {("VM VM-EST01", "DMANESTOQ-15770"): 40})
    banco.gravar_uso(date(2026, 10, 20), {("VM VM-EST01", "DMANESTOQ-15770"): 30, ("Fulano - Chat", "X-1"): 5})
    anotar(banco, "chrome.exe", JIRA, em(9, 0), em(9, 5))
    anotar(banco, "mstsc.exe", "VM-EST01 - Conexão de Área de Trabalho Remota", em(11, 0), em(11, 20))  # longe e sem texto
    s = servico(tmp_path, banco)
    p = next(p for p in s.perguntas(DIA) if p["tipo"] == "associar")
    assert p["chave"] == "DMANESTOQ-15770" and "usada junto em 2 dias anteriores" in p["detalhe"]


def test_uso_do_dia_nao_guarda_chat(tmp_path):
    banco = Banco(tmp_path / "t.db")
    banco.definir_foco("DMANESTOQ-1")
    anotar(banco, "chrome.exe", "Paulo Padilha - Chat - Google Chrome", em(9, 0), em(9, 10))
    anotar(banco, "EXCEL.EXE", "Custos - Excel", em(9, 10), em(9, 20))
    assert perguntas.uso_do_dia(banco.amostras(DIA)) == {("Excel: Custos", "DMANESTOQ-1"): 10}


def test_corrigir_pelo_calendario(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(8, 0), em(8, 20))
    anotar(banco, "chrome.exe", "[DMANESTOQ-15684] Pacote - JIRA - Google Chrome", em(8, 20), em(8, 40))
    anotar(banco, "Code.exe", "x", em(8, 40), em(8, 50), chamada=("chrome.exe", "Meet: Daily - Google Chrome"))
    # 08:50-09:00 sem nada
    anotar(banco, "chrome.exe", JIRA, em(9, 0), em(9, 10))
    s = servico(tmp_path, banco)
    h = observador.periodos(banco.amostras(DIA))["historico"]
    assert [(b["inicio"], b["nome"]) for b in h] == [("08:00", "DMANESTOQ-15770"), ("08:20", "DMANESTOQ-15684"),
                                                      ("08:40", "Meet: Daily"), ("09:00", "DMANESTOQ-15770")]

    # um bloco troca de ticket
    r = s.reatribuir_intervalo(DIA, h[1]["de"], h[1]["ate"], "DMANESTOQ-15770")
    assert r["minutos"] == 20
    assert observador.explicar("chrome.exe", "X", "manual", False, False) == "pela sua correção"

    # juntar do primeiro ao último: reunião do meio fica, o tempo vazio é preenchido
    s.reatribuir_intervalo(DIA, h[0]["de"], h[3]["ate"], "DMANESTOQ-15770", preencher=True)
    t = observador.tickets_observados(banco.amostras(DIA), 0)
    assert [(x.chave, x.peso) for x in t] == [("DMANESTOQ-15770", 60)]  # 20 + 20 + 10 vazio + 10
    assert [r.titulo for r in observador.reunioes_observadas(banco.amostras(DIA), 0)] == ["Meet: Daily"]

    # incluindo a reunião
    s.reatribuir_intervalo(DIA, h[0]["de"], h[3]["ate"], "DMANESTOQ-15770", preencher=True, incluir_reunioes=True)
    assert observador.reunioes_observadas(banco.amostras(DIA), 0) == []
    assert observador.tickets_observados(banco.amostras(DIA), 0)[0].peso == 70


def test_api_reatribuir_valida(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from trampotime import api
    banco = Banco(tmp_path / "t.db")
    monkeypatch.setattr(api, "_servico", servico(tmp_path, banco))
    c = TestClient(api.app, base_url="http://127.0.0.1:8765")
    X = {"X-TrampoTime": "1"}
    assert c.post("/dias/2026-10-22/reatribuir", json={"de": "2026-10-22T10:00:00", "ate": "2026-10-22T09:00:00",
                                                         "chave": "DMANESTOQ-1"}, headers=X).status_code == 409
    assert c.post("/dias/2026-10-22/reatribuir", json={"de": "2026-10-22T09:00:00", "ate": "2026-10-22T10:00:00",
                                                         "chave": "reunião"}, headers=X).status_code == 422
    ok = c.post("/dias/2026-10-22/reatribuir", json={"de": "2026-10-22T09:00:00", "ate": "2026-10-22T09:30:00",
                                                       "chave": "dmanestoq-1", "preencher": True}, headers=X)
    assert ok.status_code == 200 and ok.json()["minutos"] == 30


def test_mover_ticket_reuniao_e_app_para_outro_ticket(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(8, 0), em(8, 20))
    anotar(banco, "chrome.exe", "[DMANESTOQ-15684] Pacote - JIRA - Google Chrome", em(8, 20), em(8, 40))
    anotar(banco, "Code.exe", "x", em(8, 40), em(8, 50), chamada=("chrome.exe", "Meet: Daily - Google Chrome"))
    anotar(banco, "EXCEL.EXE", "Custos - Excel", em(14, 0), em(14, 30))
    s = servico(tmp_path, banco)
    assert s.mover(DIA, "ticket", "DMANESTOQ-15684", "DMANESTOQ-15770")["minutos"] == 20
    assert s.mover(DIA, "reuniao", "Meet: Daily", "DMANESTOQ-15770")["minutos"] == 10
    assert s.mover(DIA, "sem", "Excel: Custos", "DMANESTOQ-15770")["minutos"] == 30
    t = observador.tickets_observados(banco.amostras(DIA), 0)
    assert [(x.chave, x.peso) for x in t] == [("DMANESTOQ-15770", 80)]
    assert observador.reunioes_observadas(banco.amostras(DIA), 0) == []


def test_limpar_e_planilha_desatualizada(tmp_path):
    from trampotime.servico import ErroDeFluxo
    import pytest
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(8, 0), em(8, 30))
    s = servico(tmp_path, banco)
    assert s.gerar(DIA)["desatualizado"] is False
    anotar(banco, "chrome.exe", "[DMANESTOQ-15684] Pacote - JIRA - Google Chrome", em(9, 0), em(9, 20))
    assert s.ver(DIA)["desatualizado"] is True  # o histórico mudou depois do rascunho
    assert s.gerar(DIA)["desatualizado"] is False

    assert s.limpar(DIA)["status"] == "pendente"
    with pytest.raises(ErroDeFluxo):
        s.ver(DIA)
    assert banco.amostras(DIA)  # o histórico fica


def test_excluir_do_historico(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(8, 0), em(8, 30))
    anotar(banco, "Code.exe", "x", em(8, 30), em(8, 40), chamada=("chrome.exe", "Meet: Daily - Google Chrome"))
    s = servico(tmp_path, banco)
    assert s.excluir_intervalo(DIA, em(8, 10).isoformat(), em(8, 40).isoformat())["minutos"] == 20  # reunião fica
    assert observador.tickets_observados(banco.amostras(DIA), 0)[0].peso == 10
    assert observador.reunioes_observadas(banco.amostras(DIA), 0)[0].minutos == 10
    s.excluir_intervalo(DIA, em(8, 0).isoformat(), em(8, 40).isoformat(), incluir_reunioes=True)
    assert banco.amostras(DIA) == []


def test_reuniao_curtinha_no_meio_do_mesmo_ticket_nao_quebra_o_card(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(8, 28), em(8, 46))
    anotar(banco, "Code.exe", "x", em(8, 46), em(8, 49), chamada=("chrome.exe", "Meet: Daily - Google Chrome"))  # 3 min
    anotar(banco, "chrome.exe", JIRA, em(8, 49), em(9, 50))
    anotar(banco, "Code.exe", "x", em(10, 0), em(10, 15), chamada=("chrome.exe", "Meet: Retro - Google Chrome"))  # 15 min
    anotar(banco, "chrome.exe", JIRA, em(10, 15), em(10, 30))
    h = observador.periodos(banco.amostras(DIA))["historico"]
    assert [(b["inicio"], b["fim"], b["tipo"]) for b in h] == [
        ("08:28", "09:50", "ticket"), ("10:00", "10:15", "reuniao"), ("10:15", "10:30", "ticket")]


def test_reservar_um_horario(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(14, 0), em(14, 10))  # mexeu um pouco no computador durante a retro
    banco.registrar_pausa(em(14, 10), em(14, 50), "bloqueado")
    s = servico(tmp_path, banco)
    r = s.reservar(DIA, em(14, 0).isoformat(), em(15, 0).isoformat(), "DMANESTOQ-1", "Retrospectiva")
    assert r["minutos"] == 60
    assert banco.pausas(DIA) == []
    reunioes = observador.reunioes_observadas(banco.amostras(DIA))
    assert [(x.titulo, x.minutos, x.chave, x.inicio) for x in reunioes] == [("Retrospectiva", 60, "DMANESTOQ-1", "14:00")]
    linha = next(l for l in s.gerar(DIA)["linhas"] if l["acao"] == "Retrospectiva")
    assert linha["chave"] == "DMANESTOQ-1" and linha["periodos"] == [["14:00", "15:00"]]


def test_reserva_ativa_conta_mesmo_com_tela_bloqueada(tmp_path):
    banco = Banco(tmp_path / "t.db")
    s = servico(tmp_path, banco)
    s.iniciar_reserva("DMANESTOQ-1", "Ajudando um colega", em(10, 0))
    laco = {"pausas": observador.Pausas(banco)}
    c = Config(intervalo_segundos=60)
    for m in range(0, 30):
        assert observador._reserva(banco, c, laco, em(10, m))  # nem lê a tela: a reserva vale
    assert banco.pausas(DIA) == []
    assert observador.estado["motivo"] == "horário reservado desde 10:00"
    r = s.encerrar_reserva(em(10, 30))
    assert r["minutos"] == 30 and observador.reserva_ativa(banco) is None
    assert [(x.titulo, x.minutos) for x in observador.reunioes_observadas(banco.amostras(DIA))] == [("Ajudando um colega", 30)]


def test_reserva_esquecida_encerra_sozinha(tmp_path):
    banco = Banco(tmp_path / "t.db")
    servico(tmp_path, banco).iniciar_reserva(None, "Treinamento", em(8, 0))
    assert not observador._reserva(banco, Config(), {"pausas": observador.Pausas(banco)}, em(17, 0))
    assert observador.reserva_ativa(banco) is None


def test_ajustar_horario_de_um_bloco(tmp_path):
    banco = Banco(tmp_path / "t.db")
    anotar(banco, "chrome.exe", JIRA, em(8, 13), em(8, 25))
    anotar(banco, "chrome.exe", "[DMANESTOQ-15684] Pacote - JIRA - Google Chrome", em(8, 25), em(8, 40))
    s = servico(tmp_path, banco)
    d = lambda h, m: em(h, m).isoformat()
    # encolhe o começo (08:13 -> 08:20) e estica o fim (08:25 -> 08:30, por cima do vizinho)
    r = s.ajustar_horario(DIA, d(8, 13), d(8, 25), d(8, 20), d(8, 30), "ticket", None, "DMANESTOQ-15770")
    assert r["minutos"] == 10
    t = {x.chave: x.peso for x in observador.tickets_observados(banco.amostras(DIA), 0)}
    assert t == {"DMANESTOQ-15770": 10, "DMANESTOQ-15684": 10}

    # reunião esticada continua reunião
    anotar(banco, "Code.exe", "x", em(9, 0), em(9, 15), chamada=("chrome.exe", "Meet: Daily - Google Chrome"))
    s.ajustar_horario(DIA, d(9, 0), d(9, 15), d(9, 0), d(9, 30), "reuniao", "Meet: Daily", None)
    assert [(x.titulo, x.minutos) for x in observador.reunioes_observadas(banco.amostras(DIA), 0)] == [("Meet: Daily", 30)]


def test_issue_com_nome(tmp_path, monkeypatch):
    from fastapi.testclient import TestClient
    from trampotime import api
    from trampotime.modelos import separar_issue
    assert separar_issue("DMANESTOQ-15650 - VacationFlow") == ("DMANESTOQ-15650", "VacationFlow")
    assert separar_issue("dmanestoq-15650: Vacation Flow 2") == ("DMANESTOQ-15650", "Vacation Flow 2")
    assert separar_issue("DMANESTOQ-15650") == ("DMANESTOQ-15650", None)
    assert separar_issue("Reunião") == (None, None)

    banco = Banco(tmp_path / "t.db")
    s = servico(tmp_path, banco)
    monkeypatch.setattr(api, "_servico", s)
    c = TestClient(api.app, base_url="http://127.0.0.1:8765")
    X = {"X-TrampoTime": "1"}
    anotar(banco, "Code.exe", "x", em(9, 0), em(9, 30))
    r = c.post(f"/dias/{DIA}/reatribuir", json={"de": em(9, 0).isoformat(), "ate": em(9, 30).isoformat(),
                                              "chave": "DMANESTOQ-15650 - VacationFlow"}, headers=X)
    assert r.status_code == 200 and r.json()["chave"] == "DMANESTOQ-15650"
    assert banco.nomes_de_issues() == {"DMANESTOQ-15650": "VacationFlow"}
    assert s.atividade(DIA)["tickets"][0]["resumo"] == "VacationFlow"
    linha = s.gerar(DIA)["linhas"][0]
    assert (linha["chave"], linha["acao"]) == ("DMANESTOQ-15650", "VacationFlow")

    # na planilha, "CHAVE - Nome" no campo da issue também separa
    linhas = c.put(f"/dias/{DIA}/linhas", json=[{"chave": "DMANESTOQ-1 - Daily", "acao": "", "minutos": "7h30", "origem": "manual"}],
                   headers=X).json()["linhas"]
    assert (linhas[0]["chave"], linhas[0]["acao"]) == ("DMANESTOQ-1", "Daily")
    assert c.post("/foco", json={"chave": "não é issue"}, headers=X).status_code == 422
