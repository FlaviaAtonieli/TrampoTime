"""Fluxo completo contra um Jira e um Tempo falsos (nenhuma chamada real)."""
import json
from datetime import date, time

import httpx
import pytest

from trampotime import planilha
from trampotime.agenda import filtrar_eventos
from trampotime.armazenamento import Banco
from trampotime.config import Config
from trampotime.jira import Jira
from trampotime.modelos import Linha, Reuniao
from trampotime.servico import ErroDeFluxo, Servico

DIA = date(2026, 10, 21)


class JiraFalso:
    def __init__(self):
        self.criados = []
        self.ja_lancado_segundos = 0

    def __call__(self, req: httpx.Request) -> httpx.Response:
        assert req.headers["Authorization"] == "Bearer token-de-teste"
        p = req.url.path
        if p == "/rest/api/2/myself":
            return httpx.Response(200, json={"name": "flavia", "key": "JIRAUSER1", "displayName": "Flavia"})
        if p == "/rest/api/2/search":
            eu = {"name": "flavia", "key": "JIRAUSER1"}
            outro = {"name": "joao", "key": "JIRAUSER2"}
            return httpx.Response(200, json={"issues": [
                {"key": "DMANESTOQ-15684", "fields": {"summary": "Ajuste no saldo", "comment": {"comments": [
                    {"author": eu, "created": "2026-10-21T10:00:00.000-0300"}]}},
                 "changelog": {"histories": [{"author": eu, "created": "2026-10-21T11:00:00.000-0300"}]}},
                {"key": "DMANESTOQ-15700", "fields": {"summary": "Só o João mexeu", "comment": {"comments": []}},
                 "changelog": {"histories": [{"author": outro, "created": "2026-10-21T11:00:00.000-0300"}]}},
            ]})
        if p.startswith("/rest/api/2/issue/"):
            return httpx.Response(200, json={"id": "10001", "key": p.rsplit("/", 1)[1]})
        if p == "/rest/tempo-timesheets/4/worklogs/search":
            return httpx.Response(200, json=[{"timeSpentSeconds": self.ja_lancado_segundos}] if self.ja_lancado_segundos else [])
        if p == "/rest/tempo-timesheets/4/worklogs/":
            corpo = json.loads(req.content)
            self.criados.append(corpo)
            return httpx.Response(200, json=[{"tempoWorklogId": len(self.criados)}])
        return httpx.Response(404)


@pytest.fixture
def ambiente(tmp_path):
    falso = JiraFalso()
    cliente = httpx.Client(base_url="https://jira.exemplo", transport=httpx.MockTransport(falso),
                           headers={"Authorization": "Bearer token-de-teste"})
    config = Config(jira_url="https://jira.exemplo", jira_token="token-de-teste", inicio_dia=time(9, 0),
                    ticket_reunioes=None, banco=tmp_path / "t.db")
    agenda = lambda *a: [Reuniao("Daily DMANESTOQ-15684", 15, "DMANESTOQ-15684"), Reuniao("1:1", 30)]
    s = Servico(config, Jira(config.jira_url, config.jira_token, cliente), Banco(config.banco), agenda)
    return s, falso


def test_fluxo_completo(ambiente):
    s, falso = ambiente
    dados = s.gerar(DIA)
    assert dados["total_minutos"] == 450
    chaves = {l["chave"] for l in dados["linhas"]}
    assert "DMANESTOQ-15700" not in chaves  # você não mexeu nele
    assert None in chaves  # 1:1 sem ticket

    with pytest.raises(ErroDeFluxo, match="sem issue"):
        s.aprovar(DIA)

    # Revisão: preenche o ticket da 1:1 pela planilha
    csv = planilha.para_csv([Linha.de_dict(l) for l in dados["linhas"]]).replace("\n;1:1;", "\nDMANESTOQ-1;1:1;")
    s.revisar(DIA, planilha.de_csv(csv))
    s.aprovar(DIA)

    simulado = s.publicar(DIA, simular=True)
    assert falso.criados == []
    assert simulado["envios"][0]["inicio"] == "2026-10-21T09:00"

    enviado = s.publicar(DIA, simular=False)
    assert len(falso.criados) == len(enviado["envios"])
    assert sum(c["timeSpentSeconds"] for c in falso.criados) == 450 * 60
    assert all(c["worker"] == "JIRAUSER1" and c["originTaskId"] == "10001" for c in falso.criados)

    with pytest.raises(ErroDeFluxo):
        s.publicar(DIA, simular=False)  # não envia duas vezes


def test_nao_envia_se_o_tempo_mudou(ambiente):
    s, falso = ambiente
    s.gerar(DIA)
    dados = s.ver(DIA)
    linhas = planilha.de_csv(planilha.para_csv([Linha.de_dict(l) for l in dados["linhas"]]))
    for l in linhas:
        l.chave = l.chave or "DMANESTOQ-1"
    s.revisar(DIA, linhas)
    s.aprovar(DIA)
    falso.ja_lancado_segundos = 3600
    with pytest.raises(ErroDeFluxo, match="mudou"):
        s.publicar(DIA, simular=False)
    assert falso.criados == []


def test_planilha_aceita_horas():
    linhas = planilha.de_csv("ticket;acao;minutos;origem\nx-1;Algo;1h30;manual\nX-2;Outro;0:45;\n")
    assert [(l.chave, l.minutos) for l in linhas] == [("X-1", 90), ("X-2", 45)]


def test_agenda_ignora_dia_inteiro_recusadas_e_almoco():
    eventos = [
        {"summary": "Feriado", "start": {"date": "2026-10-21"}, "end": {"date": "2026-10-22"}},
        {"summary": "Almoço", "start": {"dateTime": "2026-10-21T12:00:00-03:00"}, "end": {"dateTime": "2026-10-21T13:00:00-03:00"}},
        {"summary": "Recusada", "attendees": [{"self": True, "responseStatus": "declined"}],
         "start": {"dateTime": "2026-10-21T14:00:00-03:00"}, "end": {"dateTime": "2026-10-21T15:00:00-03:00"}},
        {"summary": "Revisão", "description": "Sobre DMANESTOQ-15684",
         "start": {"dateTime": "2026-10-21T15:00:00-03:00"}, "end": {"dateTime": "2026-10-21T15:45:00-03:00"}},
    ]
    r = filtrar_eventos(eventos, ("almoço",))
    assert [(x.titulo, x.minutos, x.chave) for x in r] == [("Revisão", 45, "DMANESTOQ-15684")]
