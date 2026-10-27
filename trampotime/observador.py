"""Observador de janelas do Windows: a cada poucos segundos anota o app e o título da janela em foco.

Tudo fica no trampotime.db local. O ticket de cada amostra vem da chave no título (ex. a aba do Jira
"[DMANESTOQ-15684] Ajuste no saldo - Jira", ou a branch no título do VS Code) ou, se não houver,
do ticket em foco declarado.

Reunião: aba do Google Meet em foco, ou microfone em uso pelo navegador, Teams ou Zoom (mesmo com a
chamada em segundo plano). Em reunião não vale a regra de ociosidade, e a reunião ganha da janela em foco.

Pausas (café, almoço): tela bloqueada, computador suspenso ou ausência sem teclado e mouse. A pausa começa
no último uso do teclado ou mouse; o que foi anotado depois disso é desfeito.

Duas telas: vale a janela em foco (onde está o teclado); se você mexe o mouse sobre uma janela de outra
tela sem clicar nela (lendo, rolando uma planilha), vale a janela sob o mouse até você digitar.

Privacidade: do título da janela só se guarda o necessário (nome da reunião, resumo da aba do Jira, nome
da VM) e o título dos apps e abas de trabalho da lista GUARDAR_TITULOS (Office, Camtasia, Google Chat,
Docs, Vids...). O resto do título é descartado na hora.
"""
import re
import threading
from collections import Counter, defaultdict
from datetime import date, datetime, timedelta
from pathlib import Path

from .armazenamento import Banco
from .modelos import CHAVE_TICKET, Reuniao, TicketDoDia

MINIMO_MINUTOS = 3  # menos que isso num ticket ou reunião é só uma olhada, não vira linha
EMENDA_MINUTOS = 10  # amostras com intervalo menor que isso são a mesma sequência
HERANCA_MINUTOS = 15  # trecho sem ticket até esse tamanho herda o ticket vizinho
APP_RESERVA = "trampotime-reserva"  # amostras de horário reservado (trabalho fora do computador)
RESERVA_MAXIMA_HORAS = 8
RUIDO_REUNIAO_MINUTOS = 5  # reunião até esse tamanho entre dois trechos do mesmo ticket é ruído (microfone, aba aberta)
NOMES_GENERICOS = {"Chamada no navegador", "Reunião no Meet", "Chamada"}
NAVEGADORES = {"chrome.exe", "msedge.exe", "firefox.exe", "brave.exe"}
APPS_CHAMADA = {"ms-teams.exe": "Teams", "teams.exe": "Teams", "zoom.exe": "Zoom"}
_CHAVE_QUALQUER_CAIXA = re.compile(r"\b([A-Za-z][A-Za-z0-9]+-\d+)\b")
_NOME_NAVEGADOR = re.compile(r"\s+[-–]\s+(google chrome|microsoft edge|mozilla firefox|brave)\b.*$", re.IGNORECASE)
_MEET = re.compile(r"^\s*(google\s+)?meet\s*[-–:|]\s*|\bgoogle meet\b", re.IGNORECASE)

# Último estado do observador que roda dentro do app, para a página.
estado = {"ativo": False, "instante": None, "app": None, "chave": None, "ausente": False, "reuniao": False,
          "bloqueado": False, "motivo": None, "detalhe": None, "pelo_mouse": False, "sem_ticket_desde": None}


def chave_no_titulo(titulo: str, projetos: tuple[str, ...] = ()) -> str | None:
    """Primeira chave do título. Com projetos definidos, só aceita chaves desses projetos (evita UTF-8,
    SHA-256...) e aceita minúsculas, como na branch feature/dmanestoq-15684."""
    if not projetos:
        m = CHAVE_TICKET.search(titulo or "")
        return m.group(1) if m else None
    for m in _CHAVE_QUALQUER_CAIXA.finditer(titulo or ""):
        chave = m.group(1).upper()
        if chave.split("-")[0] in projetos:
            return chave
    return None


def e_reuniao(app: str, titulo: str) -> bool:
    return app.lower() in NAVEGADORES and bool(_MEET.search(titulo or ""))


def nome_da_reuniao(titulo: str) -> str:
    """'Meet - Daily Estoque - Google Chrome' vira 'Daily Estoque'."""
    texto = re.sub(r"^\s*(google\s+)?meet\s*[-–:|]\s*", "", _NOME_NAVEGADOR.sub("", titulo), flags=re.IGNORECASE)
    texto = re.split(r"\s+[-–]\s+", texto)[0].strip()
    if not _MEET.search(titulo):
        return texto or "Chamada"
    return f"Meet: {texto}" if texto and texto.lower() not in {"google meet", "meet"} else "Reunião no Meet"


def chamada_em_andamento(microfone: set[str], janelas: list[tuple[str, str]],
                         ultimo_meet: str | None) -> tuple[str, str] | None:
    """(app, título) da chamada em andamento, ou None.

    Com o microfone em uso pelo navegador, a reunião é a janela do Meet visível ou, se o Meet estiver numa
    aba em segundo plano, a última aba do Meet que você abriu."""
    navegador = next((a for a in microfone if a in NAVEGADORES), None)
    if navegador:
        meet = next(((a, t) for a, t in janelas if e_reuniao(a, t)), None)
        if meet:
            return meet
        return navegador, ultimo_meet or "Chamada no navegador"
    app = next((a for a in microfone if a in APPS_CHAMADA), None)
    return (app, f"Chamada no {APPS_CHAMADA[app]}") if app else None


APPS_AMIGAVEIS = {"powerpnt.exe": "PowerPoint", "winword.exe": "Word", "excel.exe": "Excel",
                  "camtasiastudio.exe": "Camtasia", "code.exe": "VS Code", "ms-teams.exe": "Teams",
                  "chrome.exe": "Chrome", "msedge.exe": "Edge", "firefox.exe": "Firefox", "brave.exe": "Brave",
                  "explorer.exe": "Explorador de Arquivos", "snippingtool.exe": "Ferramenta de Captura"}
_PARECE_URL = re.compile(r"^(https?://)?[\w-]+(\.[\w-]+)+(/\S*)?$")


def parece_url(texto: str) -> bool:
    """Aba ainda carregando mostra o endereço no título (ex. jiraproducao.totvs.com.br/browse/...)."""
    return bool(_PARECE_URL.match((texto or "").strip()))


def e_de_trabalho(app: str, titulo: str, guardar: tuple[str, ...]) -> bool:
    """O app (.exe) ou a aba (parte do título) está na lista de títulos que você quer guardar."""
    for regra in guardar:
        if regra.lower().endswith(".exe"):
            if app.lower() == regra.lower():
                return True
        elif regra.lower() in (titulo or "").lower():
            return True
    return False


def titulo_minimo(app: str, titulo: str, chave: str | None, fonte: str | None, guardar: tuple[str, ...] = ()) -> str:
    """O mínimo do título que o TrampoTime usa; o resto não é gravado."""
    if fonte == "reuniao":
        return nome_da_reuniao(titulo)
    if app.lower() == "mstsc.exe":
        return re.split(r"\s+[-–]\s+", titulo or "")[0].strip()
    if app.lower() == "code.exe":
        return projeto_do_vscode(titulo)
    if chave and fonte == "titulo" and app.lower() in NAVEGADORES:
        return resumo_do_titulo(titulo, chave)  # aba do Jira: resumo da issue, para a descrição da linha
    if e_de_trabalho(app, titulo, guardar):
        return _NOME_NAVEGADOR.sub("", titulo or "").strip()
    return ""


def projeto_do_vscode(titulo: str) -> str:
    """'● servico.py - trampotime - feature/dmanestoq-1 - Visual Studio Code' vira 'trampotime - feature/dmanestoq-1'.
    Guarda a pasta e a branch; o nome do arquivo é descartado."""
    partes = [p.strip() for p in re.split(r"\s+[-–]\s+", titulo or "") if p.strip()]
    if partes and "visual studio code" in partes[-1].lower():
        partes = partes[:-1]
    if len(partes) >= 3:
        partes = partes[1:]  # tira o arquivo aberto
    return " - ".join(partes[:2])


def atribuir(banco: Banco, app: str, titulo: str, projetos: tuple[str, ...] = (),
             chamada: tuple[str, str] | None = None, guardar: tuple[str, ...] = ()) -> tuple[str, str, str | None, str | None]:
    """(app, título, chave, fonte) de uma amostra:
    reunião > foco fixado > chave no título > associação (Perguntas do TrampoTime) > ticket em foco."""
    if chamada:
        app, titulo = chamada
    if chamada or e_reuniao(app, titulo):
        return app, titulo, chave_no_titulo(titulo, projetos), "reuniao"
    if banco.foco_fixo():
        return app, titulo, banco.foco_atual(), "foco"
    chave = chave_no_titulo(titulo, projetos)
    if chave:
        return app, titulo, chave, "titulo"
    associacoes = banco.associacoes()
    if associacoes:
        rotulo = rotulo_do_app(app, titulo_minimo(app, titulo, None, None, guardar))
        if rotulo in associacoes:
            return app, titulo, associacoes[rotulo], "associado"
    chave = banco.foco_atual()
    return app, titulo, chave, "foco" if chave else None


def explicar(app: str, chave: str | None, fonte: str | None, fixo: bool, chamada: bool) -> str:
    """Por que a amostra foi para esse ticket, em português, para o card Agora."""
    if fonte == "reuniao":
        return f"pelo microfone do {APPS_AMIGAVEIS.get(app.lower(), app)}" if chamada else "pela aba do Meet"
    if fonte == "foco":
        return "pelo ticket fixado" if fixo else "pelo ticket em foco"
    if fonte == "associado":
        return "pela associação que você confirmou"
    if fonte in {"manual", "juntado"}:
        return "pela sua correção"
    if fonte == "titulo":
        if app.lower() == "code.exe":
            return "pela branch do VS Code"
        if app.lower() in NAVEGADORES:
            return "pela aba do Jira"
        if app.lower() == "mstsc.exe":
            return "pelo nome da VM"
        return "pelo título da janela"
    return "sem issue: use Não é isso ou abra a issue"


def anotar(banco: Banco, app: str, titulo: str, ocioso_s: float, intervalo_s: int, ocioso_max_s: int,
           projetos: tuple[str, ...] = (), agora: datetime | None = None,
           chamada: tuple[str, str] | None = None, guardar: tuple[str, ...] = ()) -> str | None:
    """Grava uma amostra se você está ativa (ou numa reunião). Devolve o ticket atribuído (ou None)."""
    app, titulo, chave, fonte = atribuir(banco, app, titulo, projetos, chamada, guardar)
    if ocioso_s >= ocioso_max_s and fonte != "reuniao":
        return None
    banco.registrar_amostra(agora or datetime.now(), app, titulo_minimo(app, titulo, chave, fonte, guardar),
                            intervalo_s, chave, fonte)
    return chave


class Pausas:
    """Decide quando começa e termina uma pausa e grava no banco."""

    def __init__(self, banco: Banco):
        self.banco = banco
        self.atual: dict | None = None  # {"inicio": datetime, "motivo": str}
        self.ultimo_passo: datetime | None = None

    def passo(self, agora: datetime, bloqueado: bool, ausente: bool, ocioso_s: float, intervalo_s: int) -> bool:
        """Chamado a cada volta do observador. Devolve True se a amostra desta volta deve ser anotada."""
        # Um salto no relógio entre duas voltas é o computador suspenso (ou hibernado).
        if self.ultimo_passo and (agora - self.ultimo_passo).total_seconds() > max(60, 3 * intervalo_s) and not self.atual:
            self.banco.registrar_pausa(self.ultimo_passo, agora, "suspenso")
        self.ultimo_passo = agora

        inativo = bloqueado or ausente
        if inativo and not self.atual:
            inicio = agora - timedelta(seconds=ocioso_s)  # último teclado ou mouse
            self.banco.apagar_amostras_desde(inicio)
            self.atual = {"inicio": inicio, "motivo": "bloqueado" if bloqueado else "ausente"}
        elif inativo and bloqueado:
            self.atual["motivo"] = "bloqueado"
        elif not inativo and self.atual:
            self.encerrar(agora)
        return not inativo

    def encerrar(self, agora: datetime) -> None:
        if self.atual:
            if (agora - self.atual["inicio"]).total_seconds() >= 60:
                self.banco.registrar_pausa(self.atual["inicio"], agora, self.atual["motivo"])
            self.atual = None


def resolver(amostras: list[dict]) -> list[dict]:
    """Junta o que é a mesma atividade olhando a sequência no tempo, não só o nome.

    - Trecho de reunião sem nome (sala de espera, Meet em segundo plano antes de ver a aba) recebe o
      nome da reunião com nome mais próxima, se estiver a menos de EMENDA_MINUTOS.
    - Trecho curto sem ticket (olhar a agenda, o próprio TrampoTime) herda o ticket de antes ou, no começo,
      o de depois. Trechos longos continuam sem ticket para a revisão.
    Devolve cópias das amostras com "chave", "fonte" e "reuniao" (nome) resolvidos."""
    itens = [{**a, "reuniao": nome_da_reuniao(a["titulo"]) if a["fonte"] == "reuniao" else None} for a in amostras]
    trechos = _trechos(itens)

    for i, t in enumerate(trechos):
        if t["tipo"] == "reuniao" and t["nome"] in NOMES_GENERICOS:
            vizinho = _vizinho(trechos, i, lambda v: v["tipo"] == "reuniao" and v["nome"] not in NOMES_GENERICOS)
            if vizinho:
                for a in t["itens"]:
                    a["reuniao"] = vizinho["nome"]
                    a["chave"] = a["chave"] or vizinho["itens"][0]["chave"]

    for i, t in enumerate(trechos):
        if t["tipo"] == "sem" and t["segundos"] <= HERANCA_MINUTOS * 60:
            vizinho = (_vizinho(trechos, i, lambda v: v["tipo"] == "ticket", depois=False)
                       or _vizinho(trechos, i, lambda v: v["tipo"] == "ticket", antes=False))
            if vizinho:
                for a in t["itens"]:
                    a["chave"], a["fonte"] = vizinho["itens"][0]["chave"], "herdado"

    # Reunião curtinha no meio do mesmo ticket (microfone ligado um instante, aba do Meet aberta de passagem)
    # não quebra o trabalho em dois: vira parte do ticket. Os trechos são refeitos depois da herança acima.
    trechos = _trechos(itens)
    for i, t in enumerate(trechos):
        if t["tipo"] != "reuniao" or t["segundos"] > RUIDO_REUNIAO_MINUTOS * 60 or not 0 < i < len(trechos) - 1:
            continue
        antes, depois = trechos[i - 1], trechos[i + 1]
        if (antes["tipo"] == depois["tipo"] == "ticket" and antes["nome"] == depois["nome"]
                and t["emendado"] and depois["emendado"]):
            for a in t["itens"]:
                a["chave"], a["fonte"], a["reuniao"] = antes["nome"], "herdado", None
    return itens


def _trechos(itens: list[dict]) -> list[dict]:
    """Sequências seguidas do mesmo tipo (ticket X, reunião Y, sem ticket), quebradas por pausas longas."""
    trechos = []
    anterior = None
    for a in itens:
        instante = datetime.fromisoformat(a["instante"])
        if a["fonte"] == "reuniao":
            tipo, id_ = "reuniao", a["reuniao"]
        elif a["chave"]:
            tipo, id_ = "ticket", a["chave"]
        else:
            tipo, id_ = "sem", None
        emenda = anterior is not None and (instante - anterior).total_seconds() <= EMENDA_MINUTOS * 60
        if trechos and emenda and trechos[-1]["tipo"] == tipo and trechos[-1]["nome"] == id_:
            trechos[-1]["itens"].append(a)
            trechos[-1]["segundos"] += a["segundos"]
            trechos[-1]["fim"] = instante
        else:
            trechos.append({"tipo": tipo, "nome": id_, "itens": [a], "segundos": a["segundos"],
                            "inicio": instante, "fim": instante, "emendado": emenda})
        anterior = instante
    return trechos


def _vizinho(trechos: list[dict], i: int, serve, antes: bool = True, depois: bool = True) -> dict | None:
    """Trecho mais próximo que serve, sem atravessar uma pausa longa."""
    candidatos = []
    if antes:
        j = i
        while j > 0 and trechos[j]["emendado"]:
            j -= 1
            if serve(trechos[j]):
                candidatos.append((trechos[i]["inicio"] - trechos[j]["fim"], trechos[j]))
                break
    if depois:
        j = i
        while j + 1 < len(trechos) and trechos[j + 1]["emendado"]:
            j += 1
            if serve(trechos[j]):
                candidatos.append((trechos[j]["inicio"] - trechos[i]["fim"], trechos[j]))
                break
    candidatos = [(d, t) for d, t in candidatos if d.total_seconds() <= EMENDA_MINUTOS * 60]
    return min(candidatos, key=lambda c: c[0])[1] if candidatos else None


def tickets_observados(amostras: list[dict], minimo_minutos: int = MINIMO_MINUTOS) -> list[TicketDoDia]:
    """Tickets do dia com peso = minutos observados."""
    amostras = resolver(amostras)
    segundos: Counter = Counter()
    titulos: dict[str, Counter] = defaultdict(Counter)
    for a in amostras:
        if a["chave"] and a["fonte"] != "reuniao":
            segundos[a["chave"]] += a["segundos"]
            if a["fonte"] == "titulo" and a["titulo"]:
                titulos[a["chave"]][a["titulo"]] += 1
    tickets = []
    for chave, s in segundos.items():  # ordem em que apareceram no dia
        minutos = round(s / 60)
        if minutos >= minimo_minutos:
            # o título mais visto, preferindo um resumo de verdade a um endereço de aba carregando
            candidatos = [t for t, _ in titulos[chave].most_common()]
            titulo = next((t for t in candidatos if not parece_url(t)), "")  # só endereço: fica a chave
            tickets.append(TicketDoDia(chave, resumo_do_titulo(titulo, chave), minutos, "janelas"))
    return tickets


def sem_ticket(amostras: list[dict]) -> list[dict]:
    """Minutos ativos sem ticket, por app (a VM aparece pelo nome), para lembrar na revisão."""
    amostras = resolver(amostras)
    segundos: Counter = Counter()
    for a in amostras:
        if not a["chave"] and a["fonte"] != "reuniao":
            segundos[rotulo_do_app(a["app"], a["titulo"])] += a["segundos"]
    return [{"app": app, "minutos": round(s / 60)} for app, s in segundos.most_common() if round(s / 60) > 0]


def rotulo_do_app(app: str, titulo: str) -> str:
    """Nome legível da atividade: 'VM VM-EST01', 'Excel: rascunho', 'Paulo - Chat' ou o próprio app."""
    if app.lower() == "mstsc.exe":
        maquina = re.split(r"\s+[-–]\s+", titulo or "")[0].strip()
        return f"VM {maquina}" if maquina and "remot" not in maquina.lower() else "VM (área de trabalho remota)"
    nome = APPS_AMIGAVEIS.get(app.lower())
    if titulo and app.lower() in NAVEGADORES:
        return titulo
    if titulo and app.lower() == "code.exe":
        pasta, _, branch = titulo.partition(" - ")
        return f"VS Code: {pasta} ({branch})" if branch else f"VS Code: {pasta}"
    if titulo and nome:
        # 'Proposta.docx - Word' vira 'Word: Proposta.docx'
        documento = re.split(r"\s+[-–]\s+", titulo)[0].strip()
        return f"{nome}: {documento}" if documento and documento.lower() != nome.lower() else nome
    return nome or app


def periodos(amostras: list[dict]) -> dict:
    """Início e fim de cada atividade, depois de juntar o que é a mesma coisa.

    Devolve {"inicio", "fim"} do dia e, por atividade ("ticket:X", "reuniao:Y", "sem:app"),
    a lista de períodos [início, fim] em HH:MM."""
    itens = resolver(amostras)
    if not itens:
        return {"inicio": None, "fim": None, "atividades": {}}
    atividades: dict[str, list[list[datetime]]] = defaultdict(list)
    anterior_chave, anterior_fim = None, None
    for a in itens:
        inicio = datetime.fromisoformat(a["instante"])
        fim = inicio + timedelta(seconds=a["segundos"])
        if a["fonte"] == "reuniao":
            chave = f"reuniao:{a['reuniao']}"
        elif a["chave"]:
            chave = f"ticket:{a['chave']}"
        else:
            chave = f"sem:{rotulo_do_app(a['app'], a['titulo'])}"
        continua = (chave == anterior_chave and anterior_fim is not None
                    and (inicio - anterior_fim).total_seconds() <= EMENDA_MINUTOS * 60)
        if continua:
            atividades[chave][-1][1] = fim
        else:
            atividades[chave].append([inicio, fim])
        anterior_chave, anterior_fim = chave, fim
    hhmm = lambda d: d.strftime("%H:%M")
    return {
        "inicio": hhmm(datetime.fromisoformat(itens[0]["instante"])),
        "fim": hhmm(datetime.fromisoformat(itens[-1]["instante"]) + timedelta(seconds=itens[-1]["segundos"])),
        "atividades": {k: [[hhmm(i), hhmm(f)] for i, f in v] for k, v in atividades.items()},
        "historico": _historico(itens),
        "onde": _onde(itens),
    }


def _onde(itens: list[dict]) -> dict[str, list[str]]:
    """Onde você trabalhou em cada ticket: apps, documentos, VM e projeto do VS Code (os 4 com mais tempo)."""
    por_ticket: dict[str, Counter] = defaultdict(Counter)
    for a in itens:
        if a["chave"] and a["fonte"] != "reuniao":
            jira = a["fonte"] == "titulo" and a["app"].lower() in NAVEGADORES
            por_ticket[a["chave"]]["Jira" if jira else rotulo_do_app(a["app"], a["titulo"])] += a["segundos"]
    return {chave: [d for d, _ in c.most_common(4)] for chave, c in por_ticket.items()}


def _historico(itens: list[dict]) -> list[dict]:
    """Sequência do dia: um bloco por ticket, reunião ou trecho sem ticket, com o que estava aberto nele."""
    blocos: list[dict] = []
    for a in itens:
        inicio = datetime.fromisoformat(a["instante"])
        fim = inicio + timedelta(seconds=a["segundos"])
        if a["fonte"] == "reuniao":
            tipo, nome = "reuniao", a["reuniao"]
        elif a["chave"]:
            tipo, nome = "ticket", a["chave"]
        else:
            tipo, nome = "sem", None
        ultimo = blocos[-1] if blocos else None
        if ultimo and (ultimo["tipo"], ultimo["nome"]) == (tipo, nome) \
                and (inicio - ultimo["_fim"]).total_seconds() <= EMENDA_MINUTOS * 60:
            ultimo["_fim"] = fim
        else:
            ultimo = {"tipo": tipo, "nome": nome, "_inicio": inicio, "_fim": fim, "_detalhes": Counter()}
            blocos.append(ultimo)
        jira = a["fonte"] == "titulo" and a["app"].lower() in NAVEGADORES  # o resumo já está no ticket
        if tipo != "reuniao" and not jira:
            ultimo["_detalhes"][rotulo_do_app(a["app"], a["titulo"])] += a["segundos"]
    return [{"inicio": b["_inicio"].strftime("%H:%M"), "fim": b["_fim"].strftime("%H:%M"),
             "de": b["_inicio"].isoformat(timespec="seconds"), "ate": b["_fim"].isoformat(timespec="seconds"),
             "tipo": b["tipo"],
             "nome": b["nome"], "minutos": round((b["_fim"] - b["_inicio"]).total_seconds() / 60),
             "detalhes": [d for d, _ in b["_detalhes"].most_common(3)]} for b in blocos]


def reunioes_observadas(amostras: list[dict], minimo_minutos: int = MINIMO_MINUTOS) -> list[Reuniao]:
    """Tempo com a aba do Meet em foco, por reunião."""
    amostras = resolver(amostras)
    segundos: Counter = Counter()
    chaves: dict[str, str | None] = {}
    inicios: dict[str, str] = {}
    for a in amostras:
        if a["fonte"] == "reuniao":
            nome = a["reuniao"]
            segundos[nome] += a["segundos"]
            chaves[nome] = chaves.get(nome) or a["chave"]
            inicios.setdefault(nome, a["instante"][11:16])
    return [Reuniao(nome, round(s / 60), chaves[nome], inicios[nome], "meet") for nome, s in segundos.most_common()
            if round(s / 60) >= minimo_minutos]


def resumo_do_titulo(titulo: str, chave: str) -> str:
    """'[X-1] Ajuste no saldo - Jira - Google Chrome' vira 'Ajuste no saldo'."""
    texto = titulo.replace(f"[{chave}]", "").replace(chave, "").strip(" -:|")
    return texto.split(" - ")[0].strip() or chave


# Windows

def janela_em_foco() -> tuple[str, str] | None:
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    hwnd = user32.GetForegroundWindow()
    if not hwnd:
        return None
    return _app_da_janela(hwnd), _titulo(hwnd)


def tela_bloqueada() -> bool:
    """True com a tela de bloqueio (Win+L) ativa."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    desktop = user32.OpenInputDesktop(0, False, 0x0100)  # DESKTOP_SWITCHDESKTOP
    if not desktop:
        return True
    try:
        return not user32.SwitchDesktop(desktop)  # só funciona no desktop do usuário desbloqueado
    finally:
        user32.CloseDesktop(desktop)


def janela_sob_o_mouse() -> tuple[tuple[str, str] | None, tuple[int, int]]:
    """((app, título) da janela sob o cursor, posição do cursor). Serve para a segunda tela."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    ponto = wintypes.POINT()
    if not user32.GetCursorPos(ctypes.byref(ponto)):
        return None, (0, 0)
    user32.WindowFromPoint.restype = wintypes.HWND
    user32.WindowFromPoint.argtypes = [wintypes.POINT]
    user32.GetAncestor.restype = wintypes.HWND
    hwnd = user32.WindowFromPoint(ponto)
    raiz = user32.GetAncestor(hwnd, 2) if hwnd else None  # GA_ROOT
    if not raiz:
        return None, (ponto.x, ponto.y)
    titulo = _titulo(raiz)
    app = _app_da_janela(raiz)
    if not titulo or (app.lower() == "explorer.exe" and titulo in {"Program Manager", ""}):
        return None, (ponto.x, ponto.y)  # área de trabalho ou barra de tarefas
    return (app, titulo), (ponto.x, ponto.y)


class SegundaTela:
    """Decide entre a janela em foco e a janela sob o mouse.

    Mouse mexendo sobre outra janela (sem clicar) = você está nela. Mouse parado com teclado ou mouse
    usados há pouco = digitando, vale a janela em foco. Mouse parado e sem uso = continua onde estava."""

    def __init__(self):
        self.posicao: tuple[int, int] | None = None
        self.janela_mouse: tuple[str, str] | None = None

    def escolher(self, em_foco: tuple[str, str] | None, sob_mouse: tuple[str, str] | None,
                 posicao: tuple[int, int], ocioso_s: float, intervalo_s: int) -> tuple[tuple[str, str] | None, bool]:
        moveu = self.posicao is not None and posicao != self.posicao
        self.posicao = posicao
        if moveu:
            self.janela_mouse = sob_mouse if sob_mouse and sob_mouse != em_foco else None
        elif ocioso_s < intervalo_s:
            self.janela_mouse = None  # usou o teclado sem mexer o mouse: digitando na janela em foco
        if self.janela_mouse:
            return self.janela_mouse, True
        return em_foco, False


def janelas_visiveis() -> list[tuple[str, str]]:
    """(app, título) de todas as janelas visíveis com título. No navegador, só a aba ativa de cada janela."""
    import ctypes
    from ctypes import wintypes

    user32 = ctypes.windll.user32
    achadas = []

    @ctypes.WINFUNCTYPE(wintypes.BOOL, wintypes.HWND, wintypes.LPARAM)
    def visitar(hwnd, _):
        if user32.IsWindowVisible(hwnd) and (titulo := _titulo(hwnd)):
            achadas.append((_app_da_janela(hwnd), titulo))
        return True

    user32.EnumWindows(visitar, 0)
    return achadas


def microfone_em_uso() -> set[str]:
    """Apps (nome do .exe, minúsculo) usando o microfone agora, pelo registro de privacidade do Windows."""
    import winreg

    base = r"Software\Microsoft\Windows\CurrentVersion\CapabilityAccessManager\ConsentStore\microphone\NonPackaged"
    apps = set()
    try:
        raiz = winreg.OpenKey(winreg.HKEY_CURRENT_USER, base)
    except OSError:
        return apps
    with raiz:
        i = 0
        while True:
            try:
                nome = winreg.EnumKey(raiz, i)
            except OSError:
                break
            i += 1
            try:
                with winreg.OpenKey(raiz, nome) as app:
                    if winreg.QueryValueEx(app, "LastUsedTimeStop")[0] == 0:
                        apps.add(nome.split("#")[-1].lower())
            except OSError:
                continue
    return apps


def _titulo(hwnd) -> str:
    import ctypes

    user32 = ctypes.windll.user32
    n = user32.GetWindowTextLengthW(hwnd)
    titulo = ctypes.create_unicode_buffer(n + 1)
    user32.GetWindowTextW(hwnd, titulo, n + 1)
    return titulo.value


def _app_da_janela(hwnd) -> str:
    import ctypes
    from ctypes import wintypes

    user32, kernel32 = ctypes.windll.user32, ctypes.windll.kernel32
    pid = wintypes.DWORD()
    user32.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
    app = ""
    processo = kernel32.OpenProcess(0x1000, False, pid.value)  # PROCESS_QUERY_LIMITED_INFORMATION
    if processo:
        try:
            tamanho = wintypes.DWORD(1024)
            caminho = ctypes.create_unicode_buffer(1024)
            if kernel32.QueryFullProcessImageNameW(processo, 0, caminho, ctypes.byref(tamanho)):
                app = Path(caminho.value).name
        finally:
            kernel32.CloseHandle(processo)
    return app


def segundos_ocioso() -> float:
    """Tempo desde o último teclado ou mouse."""
    import ctypes
    from ctypes import wintypes

    class LASTINPUTINFO(ctypes.Structure):
        _fields_ = [("cbSize", wintypes.UINT), ("dwTime", wintypes.DWORD)]

    info = LASTINPUTINFO(ctypes.sizeof(LASTINPUTINFO))
    ctypes.windll.user32.GetLastInputInfo(ctypes.byref(info))
    return ((ctypes.windll.kernel32.GetTickCount() - info.dwTime) & 0xFFFFFFFF) / 1000


def observar(banco: Banco, obter_config, parar: threading.Event | None = None, mostrar: bool = False) -> None:
    """Laço do observador. obter_config() é lido a cada volta, para valer o que mudar na página.
    Um erro numa volta (banco ocupado, janela que sumiu no meio da leitura) vai para o log e o laço segue."""
    import traceback

    from .avisos import Avisos

    parar = parar or threading.Event()
    estado["ativo"] = True
    laco = {
        "pausas": Pausas(banco), "tela": SegundaTela(), "avisos": Avisos(banco),
        "ultima_limpeza": None, "ultima": None,
        # Se o app reiniciar no meio da chamada, retoma o nome da última reunião do Meet de hoje.
        "ultimo_meet": next((a["titulo"] for a in reversed(banco.amostras(date.today()))
                             if a["fonte"] == "reuniao" and _MEET.search(a["titulo"])), None),
    }
    try:
        while not parar.is_set():
            c = obter_config()
            try:
                _volta(banco, c, laco, mostrar)
            except Exception:
                traceback.print_exc()
            parar.wait(c.intervalo_segundos)
    finally:
        laco["pausas"].encerrar(datetime.now())
        estado["ativo"] = False


def reserva_ativa(banco: Banco) -> dict | None:
    import json
    valor = banco.meta("reserva_ativa")
    return json.loads(valor) if valor else None


def _reserva(banco: Banco, c, laco: dict, agora: datetime) -> bool:
    """Com um horário reservado ativo, a volta só registra a reserva: conta mesmo sem teclado e com a tela bloqueada."""
    r = reserva_ativa(banco)
    if not r:
        return False
    inicio = datetime.fromisoformat(r["inicio"])
    if agora.date() != inicio.date() or agora - inicio > timedelta(hours=RESERVA_MAXIMA_HORAS):
        banco.apagar_meta("reserva_ativa")  # esquecida ligada: encerra sozinha
        return False
    laco["pausas"].encerrar(agora)
    laco["pausas"].ultimo_passo = agora
    banco.registrar_amostra(agora, APP_RESERVA, r["descricao"], c.intervalo_segundos, r.get("chave"), "reuniao")
    estado.update(instante=agora.isoformat(timespec="seconds"), ausente=False, bloqueado=False, reuniao=True,
                  pelo_mouse=False, app=APP_RESERVA, chave=r.get("chave"), fonte="reuniao",
                  motivo=f"horário reservado desde {inicio:%H:%M}", detalhe=r["descricao"], sem_ticket_desde=None)
    return True


def _volta(banco: Banco, c, laco: dict, mostrar: bool) -> None:
    """Uma volta do observador: lê a tela, decide o ticket e grava uma amostra."""
    agora = datetime.now()
    if laco["ultima_limpeza"] is None or (agora - laco["ultima_limpeza"]).total_seconds() > 3600:
        banco.limpar_detalhes(agora.date(), c.manter_dias)
        laco["ultima_limpeza"] = agora
    if _reserva(banco, c, laco, agora):
        return
    janela, ocioso = janela_em_foco(), segundos_ocioso()
    bloqueado = tela_bloqueada() or (janela is not None and janela[0].lower() == "lockapp.exe")
    sob_mouse, posicao = janela_sob_o_mouse()
    janela, pelo_mouse = laco["tela"].escolher(janela, sob_mouse, posicao, ocioso, c.intervalo_segundos)
    janelas = janelas_visiveis()
    microfone = microfone_em_uso()
    meet_visivel = next((t for a, t in janelas if e_reuniao(a, t)), None)
    # Lembra a última aba do Meet enquanto o navegador estiver com o microfone; zera quando a chamada acaba.
    if any(a in NAVEGADORES for a in microfone):
        laco["ultimo_meet"] = meet_visivel or laco["ultimo_meet"]
    else:
        laco["ultimo_meet"] = meet_visivel
    chamada = None if bloqueado else chamada_em_andamento(microfone, janelas, laco["ultimo_meet"])
    reuniao = bool(chamada) or (janela is not None and e_reuniao(*janela))
    ausente = ocioso >= c.ocioso_minutos * 60 and not reuniao
    anotar_agora = laco["pausas"].passo(agora, bloqueado, ausente, ocioso, c.intervalo_segundos)
    estado.update(instante=agora.isoformat(timespec="seconds"), ausente=not anotar_agora, reuniao=reuniao,
                  bloqueado=bloqueado, pelo_mouse=pelo_mouse)
    if janela and anotar_agora:
        app, titulo = janela
        chave = anotar(banco, app, titulo, ocioso, c.intervalo_segundos, c.ocioso_minutos * 60, c.projetos,
                       chamada=chamada, guardar=c.guardar_titulos)
        app_r, titulo_r, _, fonte = atribuir(banco, app, titulo, c.projetos, chamada, c.guardar_titulos)
        sem = chave is None and fonte != "reuniao"
        estado.update(app=app_r, chave=chave, fonte=fonte,
                      motivo=explicar(app_r, chave, fonte, banco.foco_fixo(), bool(chamada)),
                      detalhe=rotulo_do_app(app_r, titulo_minimo(app_r, titulo_r, chave, fonte, c.guardar_titulos)),
                      sem_ticket_desde=(estado.get("sem_ticket_desde") or agora.isoformat(timespec="seconds")) if sem else None)
    elif not anotar_agora:
        estado["sem_ticket_desde"] = None
    desde = estado.get("sem_ticket_desde")
    laco["avisos"].passo(agora, c, datetime.fromisoformat(desde) if desde else None)
    if janela and mostrar:
        linha = ("(bloqueado)" if bloqueado else "(ausente)" if not anotar_agora
                 else f"{'reunião ' if reuniao else ''}{estado['chave'] or '(sem ticket)'}  [{janela[0]}]")
        if linha != laco["ultima"]:
            print(f"{datetime.now():%H:%M}  {linha}", flush=True)
        laco["ultima"] = linha
