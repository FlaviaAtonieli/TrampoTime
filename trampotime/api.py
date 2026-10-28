"""API HTTP local e página do TrampoTime. Rode com: python -m trampotime (abre o navegador e liga o observador)."""
import os
import sys
import threading
from contextlib import asynccontextmanager
from datetime import date
from pathlib import Path

from fastapi import Body, FastAPI, HTTPException, Request
from fastapi.responses import FileResponse, JSONResponse, PlainTextResponse

from . import agenda, observador, planilha, sugestao
from . import config as cfg
from .armazenamento import Banco
from .jira import Jira
from .modelos import Linha, separar_issue
from .servico import ErroDeFluxo, Servico

PAGINA = Path(__file__).with_name("pagina.html")
HOSTS_LOCAIS = {"127.0.0.1", "localhost"}

_servico: Servico | None = None


def servico() -> Servico:
    global _servico
    if _servico is None:
        c = cfg.carregar()
        banco = Banco(c.banco)
        c = cfg.aplicar_preferencias(c, banco.preferencias())
        _servico = Servico(c, Jira(c.jira_url, c.jira_token) if c.usar_jira else None, banco)
    return _servico


@asynccontextmanager
async def ciclo(app: FastAPI):
    """Liga o observador de janelas junto com o app (só no Windows; OBSERVAR=nao desliga)."""
    parar = threading.Event()
    s = servico()
    s.banco.reduzir_titulos(observador.titulo_minimo)
    s.banco.limpar_detalhes(date.today(), s.config.manter_dias)
    if sys.platform == "win32" and os.environ.get("OBSERVAR", "sim").lower() != "nao":
        banco = Banco(servico().config.banco)  # conexão própria para a thread
        threading.Thread(target=observador.observar, args=(banco, lambda: servico().config, parar),
                         daemon=True, name="observador").start()
    yield
    parar.set()


app = FastAPI(title="TrampoTime", version="0.3.0", lifespan=ciclo)


@app.middleware("http")
async def so_local(request: Request, call_next):
    """Só atende quem chama por 127.0.0.1/localhost (barra DNS rebinding) e exige o cabeçalho
    X-TrampoTime em toda alteração, que um site qualquer não consegue mandar sem CORS."""
    host = (request.headers.get("host") or "").rsplit(":", 1)[0]
    if host not in HOSTS_LOCAIS:
        return JSONResponse({"detail": "Acesso permitido só pelo próprio computador."}, status_code=403)
    if request.method not in {"GET", "HEAD"} and request.headers.get("x-trampotime") != "1":
        return JSONResponse({"detail": "Cabeçalho X-TrampoTime ausente."}, status_code=403)
    return await call_next(request)


def _rodar(fn, *args, **kwargs):
    try:
        return fn(*args, **kwargs)
    except ErroDeFluxo as e:
        raise HTTPException(status_code=409, detail=str(e))


@app.get("/", include_in_schema=False)
def pagina():
    return FileResponse(PAGINA, media_type="text/html; charset=utf-8")


@app.get("/estado")
def estado():
    s = servico()
    hoje = date.today()
    return {
        "modo": "jira" if s.jira else "planilha",
        "hoje": hoje.isoformat(),
        "configurado": bool(s.banco.preferencias()),
        "preferencias": cfg.preferencias(s.config),
        "foco": s.banco.foco_atual(),
        "fixo": s.banco.foco_fixo(),
        "issues": s.banco.nomes_de_issues(),
        "observador": observador.estado,
        "agenda": agenda.situacao(s.config.google_credentials, s.config.google_token),
        "atividade": s.atividade(hoje),
        "dias": s.dias_recentes(hoje, s.config.manter_dias),  # o mesmo tanto que o histórico guarda
    }


@app.put("/preferencias")
def salvar_preferencias(prefs: dict = Body(...)):
    s = servico()
    try:
        novo = cfg.aplicar_preferencias(s.config, prefs)
    except (ValueError, TypeError) as e:
        raise HTTPException(status_code=422, detail=str(e))
    s.config = novo
    s.banco.salvar_preferencias(cfg.preferencias(novo))
    return cfg.preferencias(novo)


def _chave(corpo: dict) -> str | None:
    """Chave da issue do campo "chave". Aceita "DMANESTOQ-15650 - VacationFlow": guarda o nome e devolve a chave."""
    texto = (corpo.get("chave") or "").strip()
    if not texto:
        return None
    chave, nome = separar_issue(texto)
    if not chave:
        raise HTTPException(status_code=422, detail="Use a chave da issue, como DMANESTOQ-15684 ou DMANESTOQ-15684 - Nome.")
    if nome:
        servico().banco.nomear_issue(chave, nome)
    return chave


@app.post("/foco")
def foco(corpo: dict = Body(...)):
    chave = _chave(corpo)
    servico().banco.definir_foco(chave, fixo=bool(corpo.get("fixo")))
    return {"foco": chave, "fixo": servico().banco.foco_fixo()}


@app.get("/perguntas")
def listar_perguntas():
    return servico().perguntas(date.today())


@app.post("/perguntas/responder")
def responder_pergunta(corpo: dict = Body(...)):
    return _rodar(servico().responder, date.today(), str(corpo.get("id") or ""), bool(corpo.get("sim")))


@app.get("/rotinas")
def listar_rotinas():
    return servico().rotinas(date.today())


@app.post("/dias/{dia}/reatribuir")
def reatribuir(dia: date, corpo: dict = Body(...)):
    """Correção pelo calendário: um bloco troca de ticket, ou vários viram um só (preenchendo o meio)."""
    chave = _chave(corpo)
    return _rodar(servico().reatribuir_intervalo, dia, str(corpo.get("de") or ""), str(corpo.get("ate") or ""), chave,
                  bool(corpo.get("preencher")), bool(corpo.get("incluir_reunioes")))


@app.post("/dias/{dia}/reservar")
def reservar(dia: date, corpo: dict = Body(...)):
    """Reserva um horário (de/ate em HH:MM) como trabalho fora do computador."""
    de, ate = str(corpo.get("de") or ""), str(corpo.get("ate") or "")
    return _rodar(servico().reservar, dia, f"{dia.isoformat()}T{de}:00", f"{dia.isoformat()}T{ate}:00",
                  _chave(corpo), str(corpo.get("descricao") or ""))


@app.post("/reserva")
def iniciar_reserva(corpo: dict = Body(...)):
    """Reserva a partir de agora, até você encerrar."""
    return servico().iniciar_reserva(_chave(corpo), str(corpo.get("descricao") or ""))


@app.delete("/reserva")
def encerrar_reserva():
    return _rodar(servico().encerrar_reserva)


@app.post("/dias/{dia}/ajustar")
def ajustar(dia: date, corpo: dict = Body(...)):
    """Novo início e fim (HH:MM) para um bloco do histórico."""
    hora = lambda h: f"{dia.isoformat()}T{h}:00"
    return _rodar(servico().ajustar_horario, dia, str(corpo.get("de") or ""), str(corpo.get("ate") or ""),
                  hora(str(corpo.get("novo_de") or "")), hora(str(corpo.get("novo_ate") or "")),
                  str(corpo.get("tipo") or "ticket"), corpo.get("nome"), _chave(corpo), bool(corpo.get("incluir_reunioes")))


@app.post("/dias/{dia}/excluir")
def excluir(dia: date, corpo: dict = Body(...)):
    """Tira do histórico o que foi registrado entre de e ate."""
    return _rodar(servico().excluir_intervalo, dia, str(corpo.get("de") or ""), str(corpo.get("ate") or ""),
                  bool(corpo.get("incluir_reunioes")))


@app.post("/dias/{dia}/mover")
def mover(dia: date, corpo: dict = Body(...)):
    """Junta, no dia, tudo de um ticket, reunião ou app sem ticket em outro ticket."""
    para = _chave({"chave": corpo.get("para")})
    if not para:
        raise HTTPException(status_code=422, detail="Informe a issue de destino.")
    return _rodar(servico().mover, dia, str(corpo.get("tipo") or ""), str(corpo.get("nome") or ""), para)


@app.post("/issues")
def nomear_issue(corpo: dict = Body(...)):
    """Guarda o nome de uma issue: {"chave": "DMANESTOQ-15650 - VacationFlow"} ou {"chave": ..., "nome": ...}."""
    chave, nome = separar_issue(str(corpo.get("chave") or ""))
    nome = (corpo.get("nome") or nome or "").strip()
    if not chave or not nome:
        raise HTTPException(status_code=422, detail="Informe a issue e o nome, como DMANESTOQ-15650 - VacationFlow.")
    servico().banco.nomear_issue(chave, nome)
    return {"chave": chave, "nome": nome}


@app.get("/agora")
def agora():
    """Estado leve do observador, para o card Agora (a página pede a cada 3 s)."""
    b = servico().banco
    return {**observador.estado, "foco": b.foco_atual(), "fixo": b.foco_fixo(), "reserva": observador.reserva_ativa(b)}


@app.post("/corrigir")
def corrigir(corpo: dict = Body(...)):
    """'Não é isso': o trecho atual vai para a chave certa e ela fica fixada."""
    chave = _chave(corpo)
    if not chave:
        raise HTTPException(status_code=422, detail="Informe a issue certa.")
    return servico().corrigir_agora(chave)


@app.get("/sugerir")
def sugerir(texto: str = ""):
    """Tickets prováveis para uma descrição (KNN sobre o que já foi ligado a tickets)."""
    exemplos = [(t, c) for t, c in servico().banco.exemplos_de_tickets(observador.NAVEGADORES) if not observador.parece_url(t)]
    return sugestao.sugerir(texto, exemplos)


@app.post("/agenda/conectar")
def conectar_agenda():
    c = servico().config
    if not c.google_credentials.exists():
        raise HTTPException(status_code=409, detail=f"Coloque o arquivo {c.google_credentials} na pasta do TrampoTime primeiro.")
    try:
        agenda.conectar(c.google_credentials, c.google_token)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"O Google recusou a conexão: {e}")
    return {"agenda": agenda.situacao(c.google_credentials, c.google_token)}


@app.get("/saude")
def saude():
    if servico().jira is None:
        return {"ok": True, "modo": "planilha"}
    eu = servico().jira.eu()
    return {"ok": True, "usuario": eu.get("name"), "nome": eu.get("displayName")}


@app.get("/dias/{dia}/atividade")
def atividade(dia: date):
    return servico().atividade(dia)


@app.post("/dias/{dia}/rascunho")
def gerar(dia: date):
    return _rodar(servico().gerar, dia)


@app.get("/dias/{dia}/rascunho")
def ver(dia: date, formato: str = "json"):
    dados = _rodar(servico().ver, dia)
    if formato == "csv":
        linhas = [Linha.de_dict(l) for l in dados["linhas"]]
        return PlainTextResponse(planilha.para_csv(linhas, [l["periodos"] for l in dados["linhas"]]), media_type="text/csv",
                                 headers={"Content-Disposition": f'attachment; filename="trampotime-{dia.isoformat()}.csv"'})
    return dados


@app.delete("/dias/{dia}/rascunho")
def limpar(dia: date):
    return _rodar(servico().limpar, dia)


@app.put("/dias/{dia}/rascunho")
def revisar(dia: date, csv: str = Body(..., media_type="text/csv")):
    return _rodar(servico().revisar, dia, planilha.de_csv(csv))


def _linhas_da_pagina(linhas: list[dict]) -> list[Linha]:
    novas = []
    for l in linhas:
        texto = (l.get("chave") or "").strip()
        chave, nome = separar_issue(texto)
        if nome:
            servico().banco.nomear_issue(chave, nome)
        try:
            minutos = planilha.minutos(str(l.get("minutos") or 0))
        except ValueError:
            raise HTTPException(status_code=422, detail="Minutos inválidos. Use 90, 1h30 ou 1:30.")
        novas.append(Linha(chave or (texto.upper() or None), (l.get("acao") or "").strip() or (nome or ""),
                           minutos, l.get("origem") or "manual", l.get("inicio") or None))
    return novas


@app.put("/dias/{dia}/linhas")
def revisar_linhas(dia: date, linhas: list[dict] = Body(...)):
    """Linhas editadas na página; minutos aceitam 90, 1h30 ou 1:30."""
    return _rodar(servico().revisar, dia, _linhas_da_pagina(linhas))


@app.post("/dias/{dia}/horarios")
def horarios(dia: date, linhas: list[dict] = Body(...)):
    """Recalcula início e fim das linhas enquanto você edita, sem salvar."""
    return servico().horarios(dia, _linhas_da_pagina(linhas))


@app.post("/dias/{dia}/aprovar")
def aprovar(dia: date):
    return _rodar(servico().aprovar, dia)


@app.post("/dias/{dia}/lancado")
def marcar_lancado(dia: date):
    return _rodar(servico().marcar_lancado, dia)


@app.post("/dias/{dia}/publicar")
def publicar(dia: date, simular: bool = True):
    return _rodar(servico().publicar, dia, simular)


@app.get("/dias/{dia}/tempo")
def tempo(dia: date):
    if servico().jira is None:
        raise HTTPException(status_code=409, detail="Modo planilha: o TrampoTime não consulta o Tempo.")
    return {"data": dia.isoformat(), "ja_lancado_minutos": servico().jira.minutos_ja_lancados(dia)}
