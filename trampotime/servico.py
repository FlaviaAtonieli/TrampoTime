"""Fluxo do dia: gerar rascunho, revisar, aprovar e enviar ao Tempo.

Sem Jira (modo planilha), os tickets vêm do observador de janelas e o envio ao Tempo fica com você.
Reuniões vêm da Google Agenda quando conectada, mais as chamadas observadas que não estão nela
(reunião instantânea do Meet, ligação pelo Chat); sem agenda, só das chamadas observadas.
"""
from datetime import date, datetime, time, timedelta
import json
from typing import Callable

from . import agenda, motor, observador, perguntas, rotinas
from .armazenamento import Banco
from .config import Config
from .jira import Jira
from .modelos import Linha


FINAIS = {"publicado": "já foi enviado ao Tempo", "lancado": "já foi marcado como lançado no Tempo"}


class ErroDeFluxo(Exception):
    pass


class Servico:
    def __init__(self, config: Config, jira: Jira | None, banco: Banco, ler_agenda: Callable = agenda.reunioes_do_dia):
        self.config = config
        self.jira = jira
        self.banco = banco
        self.ler_agenda = ler_agenda

    def gerar(self, dia: date) -> dict:
        self._nao_final(dia)
        c = self.config
        amostras = self.banco.amostras(dia)
        avisos = []
        try:
            reunioes = self.ler_agenda(dia, c.google_credentials, c.google_token, c.fuso, c.ignorar_eventos)
        except Exception as e:  # agenda é opcional: sem ela, segue com o Meet
            reunioes = []
            avisos.append(f"Não consegui ler a Google Agenda ({type(e).__name__}); usei o tempo na aba do Meet.")
        reunioes = reunioes + _fora_da_agenda(observador.reunioes_observadas(amostras), reunioes)
        associacoes = self.banco.associacoes()  # rotinas confirmadas: a reunião já vem com o ticket
        for r in reunioes:
            r.chave = r.chave or associacoes.get(r.titulo) or associacoes.get(f"Meet: {r.titulo}")
        if self.jira:
            tickets = self.jira.tickets_do_dia(dia)
            ja_lancado = self.jira.minutos_ja_lancados(dia)
        else:
            tickets = observador.tickets_observados(amostras)
            ja_lancado = 0
        nomes = self.banco.nomes_de_issues()
        editadas = _descricoes_por_ticket(rotular(observador.periodos(amostras).get("historico", []), self.banco.rotulos(dia)))
        for t in tickets:
            t.resumo = editadas.get(t.chave) or nomes.get(t.chave, t.resumo)
        linhas = motor.montar_rascunho(reunioes, tickets, ja_lancado, c.jornada_minutos, c.bloco_minutos, c.ticket_reunioes)
        self.banco.salvar(dia, "rascunho", ja_lancado, linhas)
        self.banco.definir_meta(f"rascunho_base:{dia.isoformat()}", self._base(amostras))
        return {**self.ver(dia), "avisos": avisos}

    def limpar(self, dia: date) -> dict:
        """Apaga a planilha do dia (volta a pendente). O histórico observado não muda."""
        self._nao_final(dia)
        self.banco.apagar_dia(dia)
        return {"data": dia.isoformat(), "status": "pendente"}

    @staticmethod
    def _base(amostras: list[dict]) -> str:
        """Retrato do que o histórico tinha quando a planilha foi gerada, para saber se ela ficou desatualizada."""
        tickets = sorted((t.chave, t.peso) for t in observador.tickets_observados(amostras))
        reunioes = sorted((r.titulo, r.minutos, r.chave or "") for r in observador.reunioes_observadas(amostras))
        return json.dumps([tickets, reunioes], ensure_ascii=False)

    def atividade(self, dia: date) -> dict:
        """O que o observador viu no dia, para o contador da página."""
        amostras = self.banco.amostras(dia)
        p = observador.periodos(amostras)
        quando = p["atividades"]
        nomes = self.banco.nomes_de_issues()
        pausas = [{"inicio": x["inicio"][11:16], "fim": x["fim"][11:16], "motivo": x["motivo"],
                   "minutos": round((datetime.fromisoformat(x["fim"]) - datetime.fromisoformat(x["inicio"])).total_seconds() / 60)}
                  for x in self.banco.pausas(dia)]
        return {
            "data": dia.isoformat(),
            "jornada_minutos": self.config.jornada_minutos,
            "observado_minutos": round(sum(a["segundos"] for a in amostras) / 60),
            "inicio": p["inicio"],
            "fim": p["fim"],
            "pausas": pausas,
            "historico": rotular(p.get("historico", []), self.banco.rotulos(dia)),
            "pausa_minutos": sum(x["minutos"] for x in pausas),
            # Na página aparece tudo o que foi visto; o mínimo de minutos vale só para o rascunho.
            "tickets": [{"chave": t.chave, "resumo": nomes.get(t.chave, t.resumo), "minutos": t.peso, "periodos": quando.get(f"ticket:{t.chave}", []),
                         "onde": p.get("onde", {}).get(t.chave, [])}
                        for t in observador.tickets_observados(amostras, minimo_minutos=0)],
            "reunioes": [{"titulo": r.titulo, "chave": r.chave, "minutos": r.minutos, "periodos": quando.get(f"reuniao:{r.titulo}", [])}
                         for r in observador.reunioes_observadas(amostras, minimo_minutos=0)],
            "sem_ticket": [{**s, "periodos": quando.get(f"sem:{s['app']}", [])} for s in observador.sem_ticket(amostras)],
        }

    def perguntas(self, dia: date) -> list[dict]:
        agora = datetime.now()
        return perguntas.gerar(self.banco.amostras(dia), self.banco.pausas(dia), self.banco.associacoes(),
                               self.banco.respondidas(), rotinas.reunioes_recorrentes(self.banco.planilhas_confirmadas(), dia),
                               self.banco.uso_anterior(dia), agora if dia == agora.date() else None,
                               self.banco.nomes_de_issues())

    def rotinas(self, dia: date) -> list[dict]:
        return rotinas.reunioes_recorrentes(self.banco.planilhas_confirmadas(), dia)

    def responder(self, dia: date, id_: str, sim: bool) -> dict:
        p = next((p for p in self.perguntas(dia) if p["id"] == id_), None)
        if p is None:
            raise ErroDeFluxo("Essa pergunta não vale mais (o dia mudou desde que ela apareceu).")
        self.banco.responder(id_, "sim" if sim else "nao")
        if not sim:
            return {"feito": "Ok, não pergunto de novo."}
        if p["id"].startswith("rotina-faltou:"):
            inicio = datetime.fromisoformat(p["inicio"])
            for m in range(p["minutos"]):
                self.banco.registrar_amostra(inicio + timedelta(minutes=m), "trampotime", p["rotulo"], 60, p["chave"], "reuniao")
            return {"feito": f"\"{p['rotulo']}\" entrou no seu dia às {inicio:%H:%M}."}
        if p["tipo"] == "rotina":
            self.banco.associar(p["rotulo"], p["chave"])
            return {"feito": f"\"{p['rotulo']}\" vai sempre para {p['chave']}."}
        if p["tipo"] == "juntar":
            self.reatribuir_intervalo(dia, p["inicio"], p["fim"], p["chave"], preencher=True, fonte="juntado")
            return {"feito": f"Juntado: o intervalo foi para {p['chave']}."}
        self.banco.associar(p["rotulo"], p["chave"])
        n = 0
        for a in self.banco.amostras(dia):
            if a["fonte"] not in {"reuniao", "titulo"} and observador.rotulo_do_app(a["app"], a["titulo"]) == p["rotulo"]:
                n += self.banco.reatribuir_entre(a["instante"], a["instante"] + "~", p["chave"], "associado")
        return {"feito": f"\"{p['rotulo']}\" agora conta para {p['chave']}, hoje e nos próximos dias."}

    def rotular_intervalo(self, dia: date, de: str, ate: str, titulo: str, descricao: str) -> dict:
        """Título e descrição próprios para um bloco do histórico (vazios = volta ao que foi observado)."""
        if not de.startswith(dia.isoformat()) or not ate or ate <= de:
            raise ErroDeFluxo("Esse bloco não é deste dia.")
        titulo, descricao = titulo.strip()[:120], descricao.strip()[:500]
        self.banco.definir_rotulo(de, ate, titulo, descricao)
        return {"de": de, "ate": ate, "titulo": titulo, "descricao": descricao}

    def reatribuir_intervalo(self, dia: date, inicio: str, fim: str, chave: str | None, preencher: bool = False,
                             incluir_reunioes: bool = False, fonte: str = "manual") -> dict:
        """Corrige o histórico: tudo entre início e fim vai para a chave (None = sem ticket).

        preencher: o tempo sem nenhuma amostra no intervalo também passa a contar (um registro por minuto)
        e as pausas de dentro dele são apagadas. Reuniões só mudam com incluir_reunioes."""
        if not (inicio.startswith(dia.isoformat()) and inicio < fim):
            raise ErroDeFluxo("Intervalo inválido.")
        n = self.banco.reatribuir_entre(inicio, fim, chave, fonte if chave else None, incluir_reunioes)
        criados = 0
        if preencher and chave:
            instantes = sorted(datetime.fromisoformat(a["instante"]) for a in self.banco.amostras(dia)
                               if inicio <= a["instante"] < fim)
            cursor, ate = datetime.fromisoformat(inicio), datetime.fromisoformat(fim)
            for proximo in instantes + [ate]:
                while (proximo - cursor).total_seconds() >= 60:
                    self.banco.registrar_amostra(cursor, "trampotime", "", 60, chave, fonte)
                    cursor += timedelta(minutes=1)
                    criados += 1
                cursor = max(cursor, proximo + timedelta(seconds=self.config.intervalo_segundos))
            self.banco.apagar_pausas_entre(inicio, fim)
        minutos = round((n * self.config.intervalo_segundos + criados * 60) / 60)
        return {"chave": chave, "inicio": inicio[11:16], "fim": fim[11:16], "minutos": minutos}

    def reservar(self, dia: date, inicio: str, fim: str, chave: str | None, descricao: str) -> dict:
        """Horário reservado: trabalho fora do computador (retrospectiva, ajudando um colega).
        Substitui o que houver no intervalo e entra como compromisso de horário fixo."""
        if not (inicio.startswith(dia.isoformat()) and inicio < fim):
            raise ErroDeFluxo("Intervalo inválido: o fim precisa ser depois do início.")
        descricao = descricao.strip() or "Horário reservado"
        self.banco.apagar_amostras_entre(inicio, fim, incluir_reunioes=True)
        self.banco.apagar_pausas_entre(inicio, fim)
        cursor, ate = datetime.fromisoformat(inicio), datetime.fromisoformat(fim)
        minutos = 0
        while cursor < ate:
            self.banco.registrar_amostra(cursor, observador.APP_RESERVA, descricao, 60, chave, "reuniao")
            cursor += timedelta(minutes=1)
            minutos += 1
        return {"inicio": inicio[11:16], "fim": fim[11:16], "minutos": minutos, "descricao": descricao, "chave": chave}

    def iniciar_reserva(self, chave: str | None, descricao: str, agora: datetime | None = None) -> dict:
        agora = agora or datetime.now()
        r = {"inicio": agora.isoformat(timespec="seconds"), "chave": chave, "descricao": descricao.strip() or "Horário reservado"}
        self.banco.definir_meta("reserva_ativa", json.dumps(r, ensure_ascii=False))
        return r

    def encerrar_reserva(self, agora: datetime | None = None) -> dict:
        r = observador.reserva_ativa(self.banco)
        if not r:
            raise ErroDeFluxo("Não há horário reservado ativo.")
        self.banco.apagar_meta("reserva_ativa")
        minutos = round(((agora or datetime.now()) - datetime.fromisoformat(r["inicio"])).total_seconds() / 60)
        return {**r, "minutos": minutos}

    def ajustar_horario(self, dia: date, de: str, ate: str, novo_de: str, novo_ate: str, tipo: str, nome: str | None,
                        chave: str | None, incluir_reunioes: bool = False) -> dict:
        """Muda o início e o fim de um bloco do histórico. O que sai do novo horário deixa de contar; o que entra
        passa para o bloco (ticket, ou reunião com o mesmo nome), inclusive o tempo vazio."""
        if not (novo_de.startswith(dia.isoformat()) and novo_de < novo_ate and de < ate):
            raise ErroDeFluxo("Horário inválido: o fim precisa ser depois do início.")
        reuniao = tipo == "reuniao"
        # encolheu: tira as pontas
        if novo_de > de:
            self.banco.apagar_amostras_entre(de, min(novo_de, ate), incluir_reunioes=reuniao)
        if novo_ate < ate:
            self.banco.apagar_amostras_entre(max(novo_ate, de), ate, incluir_reunioes=reuniao)
        # esticou: preenche as pontas novas
        for ini, fim in ((novo_de, min(de, novo_ate)), (max(ate, novo_de), novo_ate)):
            if ini >= fim:
                continue
            if reuniao:
                self.reservar(dia, ini, fim, chave, nome or "Reunião")
            else:
                self.reatribuir_intervalo(dia, ini, fim, chave, preencher=True, incluir_reunioes=incluir_reunioes)
        if not reuniao and chave:  # o ticket também pode ter mudado no mesmo painel
            self.banco.reatribuir_entre(novo_de, novo_ate, chave, "manual", incluir_reunioes=False)
        minutos = round((datetime.fromisoformat(novo_ate) - datetime.fromisoformat(novo_de)).total_seconds() / 60)
        return {"inicio": novo_de[11:16], "fim": novo_ate[11:16], "minutos": minutos, "chave": chave}

    def excluir_intervalo(self, dia: date, inicio: str, fim: str, incluir_reunioes: bool = False) -> dict:
        """Tira do dia o que foi registrado no intervalo (não conta mais em nenhum ticket)."""
        if not (inicio.startswith(dia.isoformat()) and inicio < fim):
            raise ErroDeFluxo("Intervalo inválido.")
        n = self.banco.apagar_amostras_entre(inicio, fim, incluir_reunioes)
        return {"inicio": inicio[11:16], "fim": fim[11:16], "minutos": round(n * self.config.intervalo_segundos / 60)}

    def mover(self, dia: date, tipo: str, nome: str, para: str) -> dict:
        """'Tickets vistos': tudo de hoje de um ticket, reunião ou app sem ticket passa para outro ticket."""
        alvo = []
        for a in observador.resolver(self.banco.amostras(dia)):
            if tipo == "ticket" and a["fonte"] != "reuniao" and a["chave"] == nome:
                alvo.append(a["instante"])
            elif tipo == "reuniao" and a["fonte"] == "reuniao" and a["reuniao"] == nome:
                alvo.append(a["instante"])
            elif (tipo == "sem" and not a["chave"] and a["fonte"] != "reuniao"
                  and observador.rotulo_do_app(a["app"], a["titulo"]) == nome):
                alvo.append(a["instante"])
        if not alvo:
            raise ErroDeFluxo("Não achei esse item no dia de hoje.")
        n = self.banco.reatribuir_instantes(alvo, para, "manual")
        return {"de": nome, "para": para, "minutos": round(n * self.config.intervalo_segundos / 60)}

    def corrigir_agora(self, chave: str, agora: datetime | None = None) -> dict:
        """'Não é isso': o trecho atual (desde a última troca de ticket) passa para a chave certa,
        e ela fica fixada daqui para frente, ganhando até da chave no título."""
        agora = agora or datetime.now()
        amostras = [a for a in self.banco.amostras(agora.date()) if a["fonte"] != "reuniao"]
        desde = None
        if amostras:
            ultima = amostras[-1]
            anterior = datetime.fromisoformat(ultima["instante"])
            desde = ultima["instante"]
            for a in reversed(amostras[:-1]):
                instante = datetime.fromisoformat(a["instante"])
                if a["chave"] != ultima["chave"] or (anterior - instante).total_seconds() > observador.EMENDA_MINUTOS * 60:
                    break
                desde, anterior = a["instante"], instante
        n = self.banco.reatribuir_desde(desde, chave) if desde else 0
        self.banco.definir_foco(chave, agora, fixo=True)
        return {"chave": chave, "desde": desde[11:16] if desde else None,
                "minutos": round(n * self.config.intervalo_segundos / 60)}

    def dias_recentes(self, hoje: date, quantos: int = 3) -> list[dict]:
        """Últimos dias úteis com o status, para não esquecer de lançar nenhum."""
        dias, d = [], hoje
        while len(dias) < quantos:
            if d.weekday() < 5:
                dias.append(d)
            d -= timedelta(days=1)
        status = self.banco.dias(dias[-1])
        return [{"data": d.isoformat(), "status": status.get(d.isoformat(), "pendente")} for d in dias]

    def marcar_lancado(self, dia: date) -> dict:
        """Você lançou as linhas no Tempo à mão: o dia fecha e não pode mais ser alterado."""
        status, ja_lancado, linhas = self._carregar(dia)
        if status != "aprovado":
            raise ErroDeFluxo(f"{dia} está como '{status}'. Aprove o rascunho antes de marcar como lançado.")
        self.banco.salvar(dia, "lancado", ja_lancado, linhas)
        self.banco.gravar_uso(dia, perguntas.uso_do_dia(self.banco.amostras(dia)))  # antes de o detalhe ser apagado
        self.banco.limpar_detalhes(date.today(), self.config.manter_dias)
        return self.ver(dia)

    def horarios(self, dia: date, linhas: list[Linha]) -> list[list[list[str]]]:
        """Início e fim de cada linha no dia (reuniões no horário real, o resto preenche em volta)."""
        amostras = self.banco.amostras(dia)
        bloco = timedelta(minutes=self.config.bloco_minutos)
        if amostras:
            primeira = datetime.fromisoformat(amostras[0]["instante"])
            inicio = datetime.combine(dia, time.min) + ((primeira - datetime.combine(dia, time.min)) // bloco) * bloco
        else:
            inicio = datetime.combine(dia, self.config.inicio_dia)
        pausas = [(datetime.fromisoformat(p["inicio"]), datetime.fromisoformat(p["fim"])) for p in self.banco.pausas(dia)]
        pausas = [(a, b) for a, b in pausas if (b - a) >= timedelta(minutes=15)]  # café rápido não muda a agenda
        return motor.horarios(linhas, dia, inicio, pausas, self.config.bloco_minutos)

    def ver(self, dia: date) -> dict:
        status, ja_lancado, linhas = self._carregar(dia)
        amostras = self.banco.amostras(dia)
        return {
            "data": dia.isoformat(),
            "status": status,
            "ja_lancado_minutos": ja_lancado,
            "linhas": [{**l.para_dict(), "periodos": p} for l, p in zip(linhas, self.horarios(dia, linhas))],
            "total_minutos": sum(l.minutos for l in linhas) + ja_lancado,
            "problemas": motor.validar(linhas, ja_lancado, self.config.jornada_minutos),
            "desatualizado": status == "rascunho" and self.banco.meta(f"rascunho_base:{dia.isoformat()}") not in (None, self._base(amostras)),
            "envios": self.banco.envios(dia),
            "observado_minutos": round(sum(a["segundos"] for a in amostras) / 60),
            "sem_ticket": observador.sem_ticket(amostras),
        }

    def revisar(self, dia: date, linhas: list[Linha]) -> dict:
        self._nao_final(dia)
        _, ja_lancado, _ = self._carregar(dia)
        self.banco.salvar(dia, "rascunho", ja_lancado, linhas)
        return self.ver(dia)

    def aprovar(self, dia: date) -> dict:
        self._nao_final(dia)
        _, ja_lancado, linhas = self._carregar(dia)
        problemas = motor.validar(linhas, ja_lancado, self.config.jornada_minutos)
        if problemas:
            raise ErroDeFluxo("Corrija antes de aprovar: " + " ".join(problemas))
        self.banco.salvar(dia, "aprovado", ja_lancado, linhas)
        return self.ver(dia)

    def publicar(self, dia: date, simular: bool = True) -> dict:
        if self.jira is None:
            raise ErroDeFluxo("Modo planilha (USAR_JIRA=nao): o TrampoTime não envia ao Tempo. Lance as linhas da planilha à mão.")
        status, ja_lancado, linhas = self._carregar(dia)
        if status != "aprovado":
            raise ErroDeFluxo(f"{dia} está como '{status}'. Aprove o rascunho antes de enviar.")

        # Confere de novo no Tempo: se alguém lançou horas depois do rascunho, não envia em dobro.
        agora_lancado = self.jira.minutos_ja_lancados(dia)
        if agora_lancado != ja_lancado:
            raise ErroDeFluxo(
                f"O Tempo mudou desde o rascunho ({ja_lancado} min antes, {agora_lancado} min agora). Gere o rascunho de novo."
            )

        envios = []
        inicio = datetime.combine(dia, self.config.inicio_dia) + timedelta(minutes=ja_lancado)
        for l in linhas:
            envio = {"ticket": l.chave, "inicio": inicio.isoformat(timespec="minutes"), "minutos": l.minutos, "comentario": l.acao}
            if not simular:
                envio["worklog_id"] = self.jira.lancar(l.chave, inicio, l.minutos, l.acao)
                self.banco.registrar_envio(dia, l.chave, l.minutos, envio["worklog_id"])
            envios.append(envio)
            inicio += timedelta(minutes=l.minutos)

        if not simular:
            self.banco.salvar(dia, "publicado", ja_lancado, linhas)
        return {"data": dia.isoformat(), "simulacao": simular, "envios": envios}

    def _nao_final(self, dia: date) -> None:
        existente = self.banco.carregar(dia)
        if existente and existente[0] in FINAIS:
            raise ErroDeFluxo(f"{dia} {FINAIS[existente[0]]}.")

    def _carregar(self, dia: date) -> tuple[str, int, list[Linha]]:
        dados = self.banco.carregar(dia)
        if not dados:
            raise ErroDeFluxo(f"Não há rascunho para {dia}. Gere um primeiro.")
        return dados


def rotular(historico: list[dict], rotulos: list[dict]) -> list[dict]:
    """Põe nos blocos do histórico o título e a descrição que você deu. Cada rótulo vai para o bloco com que
    mais se sobrepõe (o bloco pode ter mudado um pouco de tamanho desde a edição)."""
    blocos = [dict(b) for b in historico]
    for r in rotulos:
        def sobreposicao(b):
            return (min(b["ate"], r["ate"]) > max(b["de"], r["de"]),
                    (datetime.fromisoformat(min(b["ate"], r["ate"])) - datetime.fromisoformat(max(b["de"], r["de"]))).total_seconds())
        candidatos = [b for b in blocos if sobreposicao(b)[0]]
        if not candidatos:
            continue
        alvo = max(candidatos, key=lambda b: sobreposicao(b)[1])
        if r["titulo"]:
            alvo["titulo_editado"] = r["titulo"]
        if r["descricao"]:
            alvo["descricao_editada"] = r["descricao"]
    return blocos


def _descricoes_por_ticket(historico: list[dict]) -> dict[str, str]:
    """Descrições editadas dos blocos de cada ticket, juntas, para a linha da planilha."""
    por_ticket: dict[str, list[str]] = {}
    for b in historico:
        texto = b.get("descricao_editada") or b.get("titulo_editado")
        if b["tipo"] == "ticket" and texto and texto not in por_ticket.setdefault(b["nome"], []):
            por_ticket[b["nome"]].append(texto)
    return {chave: "; ".join(textos) for chave, textos in por_ticket.items() if textos}


def _fora_da_agenda(observadas: list, da_agenda: list) -> list:
    """Chamadas observadas que não são eventos da agenda. Uma reunião agendada aparece no Meet com o
    título do evento ("Meet: Daily"); a instantânea aparece com o código ("Meet: abc-defg-hij")."""
    titulos = {r.titulo.strip().lower() for r in da_agenda}
    if not titulos:
        return observadas
    return [r for r in observadas
            if r.titulo.removeprefix("Meet: ").strip().lower() not in titulos and r.titulo != "Reunião no Meet"]
