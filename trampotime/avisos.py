"""Avisos do Windows (notificação no canto da tela) para não esquecer de registrar.

- Mais de 15 min sem ticket: "Em que você está?" (no máximo um a cada 30 min).
- Dia anterior com atividade e ainda não lançado no Tempo (uma vez por dia).
- Jornada completa e a planilha de hoje ainda não aprovada (uma vez por dia).
Clicar no aviso abre a página do TrampoTime.
"""
import base64
import subprocess
import sys
from datetime import date, datetime, timedelta
from xml.sax.saxutils import escape

from .armazenamento import Banco

PAGINA = "http://127.0.0.1:8765"
SEM_TICKET_MINUTOS = 15
REPETIR_SEM_TICKET_MINUTOS = 30
# Notificações saem em nome do Windows PowerShell, que já é registrado no Windows (não precisa instalar nada).
APP_ID = r"{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe"


def notificar(titulo: str, texto: str) -> None:
    """Mostra uma notificação do Windows; falhas (ex. notificações bloqueadas) são ignoradas."""
    if sys.platform != "win32":
        return
    xml = (f'<toast activationType="protocol" launch="{PAGINA}"><visual><binding template="ToastGeneric">'
           f"<text>{escape(titulo)}</text><text>{escape(texto)}</text></binding></visual></toast>")
    script = f"""
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$x = New-Object Windows.Data.Xml.Dom.XmlDocument
$x.LoadXml('{xml.replace("'", "''")}')
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('{APP_ID}').Show([Windows.UI.Notifications.ToastNotification]::new($x))
"""
    try:
        subprocess.Popen(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-EncodedCommand",
             base64.b64encode(script.encode("utf-16-le")).decode()],
            stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=0x08000000,  # CREATE_NO_WINDOW
        )
    except OSError:
        pass


def _hhmm(minutos: int) -> str:
    return f"{minutos // 60}h{minutos % 60:02d}"


def _dia_bonito(dia: date) -> str:
    return f"{['seg', 'ter', 'qua', 'qui', 'sex', 'sáb', 'dom'][dia.weekday()]}., {dia:%d/%m}"


class Avisos:
    def __init__(self, banco: Banco, enviar=notificar):
        self.banco = banco
        self.enviar = enviar
        self.ultimo_sem_ticket: datetime | None = None
        self.proxima_checagem: datetime | None = None

    def passo(self, agora: datetime, config, sem_ticket_desde: datetime | None) -> None:
        if not config.avisos:
            return
        if sem_ticket_desde and agora - sem_ticket_desde >= timedelta(minutes=SEM_TICKET_MINUTOS):
            if self.ultimo_sem_ticket is None or agora - self.ultimo_sem_ticket >= timedelta(minutes=REPETIR_SEM_TICKET_MINUTOS):
                minutos = int((agora - sem_ticket_desde).total_seconds() // 60)
                self.enviar("Em que você está?", f"Há {minutos} min sem issue. Clique para escolher no TrampoTime.")
                self.ultimo_sem_ticket = agora

        if self.proxima_checagem and agora < self.proxima_checagem:
            return
        self.proxima_checagem = agora + timedelta(minutes=1)
        hoje = agora.date()

        chave = f"aviso:pendente:{hoje.isoformat()}"
        if not self.banco.meta(chave):
            pendentes = self.banco.dias_pendentes(hoje, desde=hoje - timedelta(days=config.manter_dias + 4))
            if pendentes:
                nomes = ", ".join(_dia_bonito(date.fromisoformat(d)) for d in pendentes[:3])
                verbo = "ainda não foi lançado" if len(pendentes) == 1 else "ainda não foram lançados"
                self.enviar("Dia sem lançar no Tempo", f"{nomes} {verbo}. Clique para abrir a planilha.")
            self.banco.definir_meta(chave, "1")

        chave = f"aviso:jornada:{hoje.isoformat()}"
        if not self.banco.meta(chave):
            minutos = self.banco.segundos_do_dia(hoje) // 60
            dia = self.banco.carregar(hoje)
            if minutos >= config.jornada_minutos and (not dia or dia[0] == "rascunho"):
                self.enviar("Jornada completa", f"Hoje: {_hhmm(minutos)} observados. Gere, revise e lance a planilha.")
                self.banco.definir_meta(chave, "1")
