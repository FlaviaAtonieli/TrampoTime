"""Cliente do Jira Data Center e do Tempo Timesheets (mesmo servidor, mesmo token)."""
from datetime import date, datetime, timedelta

import httpx

from .modelos import TicketDoDia


class Jira:
    def __init__(self, url: str, token: str, cliente: httpx.Client | None = None):
        self.http = cliente or httpx.Client(
            base_url=url,
            headers={"Authorization": f"Bearer {token}", "Accept": "application/json"},
            timeout=30,
        )
        self._eu: dict | None = None

    def _get(self, caminho: str, **params) -> dict:
        r = self.http.get(caminho, params=params)
        r.raise_for_status()
        return r.json()

    def _post(self, caminho: str, corpo: dict):
        r = self.http.post(caminho, json=corpo)
        r.raise_for_status()
        return r.json() if r.content else None

    # Jira

    def eu(self) -> dict:
        """Usuário dono do token: name (login) e key (usado pelo Tempo)."""
        if self._eu is None:
            self._eu = self._get("/rest/api/2/myself")
        return self._eu

    def ticket(self, chave: str) -> dict:
        return self._get(f"/rest/api/2/issue/{chave}", fields="summary")

    def tickets_do_dia(self, dia: date) -> list[TicketDoDia]:
        """Tickets em que você mexeu no dia, com peso = número de ações suas."""
        amanha = dia + timedelta(days=1)
        jql = (
            f'updated >= "{dia:%Y-%m-%d}" AND updated < "{amanha:%Y-%m-%d}" '
            f"AND (assignee = currentUser() OR reporter = currentUser() OR watcher = currentUser())"
        )
        resposta = self._get(
            "/rest/api/2/search",
            jql=jql,
            fields="summary,comment",
            expand="changelog",
            maxResults=100,
        )
        eu = self.eu()
        tickets = []
        for issue in resposta.get("issues", []):
            peso = _acoes_minhas(issue, eu, dia)
            if peso:
                tickets.append(TicketDoDia(issue["key"], issue["fields"]["summary"], peso))
        return tickets

    # Tempo Timesheets

    def minutos_ja_lancados(self, dia: date) -> int:
        worklogs = self._post(
            "/rest/tempo-timesheets/4/worklogs/search",
            {"from": dia.isoformat(), "to": dia.isoformat(), "worker": [self.eu()["key"]]},
        ) or []
        return sum(w.get("timeSpentSeconds", 0) for w in worklogs) // 60

    def lancar(self, chave: str, inicio: datetime, minutos: int, comentario: str) -> str:
        """Cria um apontamento no Tempo e devolve o ID dele."""
        issue = self.ticket(chave)
        resposta = self._post(
            "/rest/tempo-timesheets/4/worklogs/",
            {
                "worker": self.eu()["key"],
                "originTaskId": issue["id"],
                "started": inicio.strftime("%Y-%m-%dT%H:%M:%S.000"),
                "timeSpentSeconds": minutos * 60,
                "comment": comentario,
            },
        )
        criado = resposta[0] if isinstance(resposta, list) else (resposta or {})
        return str(criado.get("tempoWorklogId") or criado.get("id") or "")


def _acoes_minhas(issue: dict, eu: dict, dia: date) -> int:
    meus = {eu.get("name"), eu.get("key")}
    prefixo = dia.isoformat()
    acoes = 0
    for h in issue.get("changelog", {}).get("histories", []):
        autor = h.get("author", {})
        if h.get("created", "").startswith(prefixo) and (autor.get("name") in meus or autor.get("key") in meus):
            acoes += 1
    for c in issue["fields"].get("comment", {}).get("comments", []):
        autor = c.get("author", {})
        if c.get("created", "").startswith(prefixo) and (autor.get("name") in meus or autor.get("key") in meus):
            acoes += 1
    return acoes
