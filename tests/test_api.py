"""API local e página: segurança, preferências, foco e fechamento do dia no modo planilha."""
from datetime import date, datetime

import pytest
from fastapi.testclient import TestClient

from trampotime import api, observador
from trampotime.armazenamento import Banco
from trampotime.config import Config
from trampotime.servico import Servico

HOJE = date.today().isoformat()
X = {"X-TrampoTime": "1"}


@pytest.fixture
def cliente(tmp_path, monkeypatch):
    banco = Banco(tmp_path / "t.db")
    monkeypatch.setattr(api, "_servico", Servico(Config(banco=tmp_path / "t.db"), None, banco, lambda *a: []))
    return TestClient(api.app, base_url="http://127.0.0.1:8765")  # sem "with": não liga o observador


def test_pagina_e_estado_inicial(cliente):
    assert "TrampoTime" in cliente.get("/").text
    e = cliente.get("/estado").json()
    assert e["modo"] == "planilha" and e["configurado"] is False and e["foco"] is None
    assert len(e["dias"]) == 5 and e["agenda"] == "sem_credenciais"


def test_so_aceita_o_proprio_computador(cliente):
    de_fora = TestClient(api.app, base_url="http://trampotime.ataque.com")
    assert de_fora.get("/estado").status_code == 403
    assert cliente.post("/foco", json={"chave": "DMANESTOQ-1"}).status_code == 403  # sem X-TrampoTime
    assert cliente.post("/foco", json={"chave": "DMANESTOQ-1"}, headers=X).status_code == 200


def test_preferencias_valem_na_hora(cliente):
    r = cliente.put("/preferencias", json={"projetos": "dmanestoq, dmanfat", "jornada_minutos": 480,
                                           "inicio_dia": "08:30", "ticket_reunioes": "dmanestoq-1"}, headers=X)
    assert r.status_code == 200
    e = cliente.get("/estado").json()
    assert e["configurado"] is True
    assert e["preferencias"]["projetos"] == ["DMANESTOQ", "DMANFAT"]
    assert e["preferencias"]["ticket_reunioes"] == "DMANESTOQ-1"
    assert e["atividade"]["jornada_minutos"] == 480
    assert cliente.put("/preferencias", json={"projetos": "1abc"}, headers=X).status_code == 422


def test_foco_recusa_texto_que_nao_e_chave(cliente):
    assert cliente.post("/foco", json={"chave": "reunião"}, headers=X).status_code == 422


def test_dia_ate_marcar_como_lancado(cliente):
    banco = api._servico.banco
    for i in range(30):
        observador.anotar(banco, "chrome.exe", "[DMANESTOQ-15684] Ajuste - Jira", 0, 60, 300, (),
                          datetime.now().replace(hour=10, minute=i, second=0))
    assert cliente.get("/estado").json()["atividade"]["observado_minutos"] == 30

    dados = cliente.post(f"/dias/{HOJE}/rascunho", headers=X).json()
    assert dados["total_minutos"] == 450
    linhas = [{**l, "minutos": "7h30"} for l in dados["linhas"]]
    assert cliente.put(f"/dias/{HOJE}/linhas", json=linhas, headers=X).json()["total_minutos"] == 450
    assert cliente.post(f"/dias/{HOJE}/lancado", headers=X).status_code == 409  # precisa aprovar antes
    assert cliente.post(f"/dias/{HOJE}/aprovar", headers=X).status_code == 200
    assert cliente.post(f"/dias/{HOJE}/lancado", headers=X).json()["status"] == "lancado"
    assert cliente.post(f"/dias/{HOJE}/rascunho", headers=X).status_code == 409  # dia fechado
    assert cliente.get("/estado").json()["dias"][0]["status"] in {"lancado", "pendente"}  # hoje pode ser fim de semana
    assert "DMANESTOQ-15684;" in cliente.get(f"/dias/{HOJE}/rascunho?formato=csv").text


def test_titulo_e_descricao_do_bloco(cliente):
    banco = api._servico.banco
    for i in range(30):
        observador.anotar(banco, "chrome.exe", "[DMANESTOQ-15684] Ajuste - Jira", 0, 60, 300, (),
                          datetime.now().replace(hour=10, minute=i, second=0))
    bloco = cliente.get(f"/dias/{HOJE}/atividade").json()["historico"][0]
    r = cliente.post(f"/dias/{HOJE}/rotulo", json={"de": bloco["de"], "ate": bloco["ate"],
                                                   "titulo": "Ajuste do saldo", "descricao": "Corrigi o cálculo do saldo"}, headers=X)
    assert r.status_code == 200
    bloco = cliente.get(f"/dias/{HOJE}/atividade").json()["historico"][0]
    assert bloco["titulo_editado"] == "Ajuste do saldo" and bloco["descricao_editada"] == "Corrigi o cálculo do saldo"
    linhas = cliente.post(f"/dias/{HOJE}/rascunho", headers=X).json()["linhas"]
    assert any(l["chave"] == "DMANESTOQ-15684" and l["acao"] == "Corrigi o cálculo do saldo" for l in linhas)

    cliente.post(f"/dias/{HOJE}/rotulo", json={"de": bloco["de"], "ate": bloco["ate"], "titulo": "", "descricao": ""}, headers=X)
    assert "titulo_editado" not in cliente.get(f"/dias/{HOJE}/atividade").json()["historico"][0]
    assert cliente.post(f"/dias/{HOJE}/rotulo", json={"de": "2001-01-01T10:00:00", "ate": "2001-01-01T11:00:00",
                                                      "titulo": "x"}, headers=X).status_code == 409
