"""Rascunhos, apontamentos enviados e amostras do observador, guardados num SQLite local."""
import json
import sqlite3
import threading
from contextlib import contextmanager
from datetime import date, datetime
from pathlib import Path

from .modelos import Linha


class Banco:
    def __init__(self, caminho: Path | str):
        # Cada comando grava na hora (isolation_level=None): nenhuma transação fica aberta segurando o banco
        # se algo falhar no meio. WAL deixa a página ler enquanto o observador grava, e o busy_timeout espera
        # em vez de falhar com "database is locked". Lotes usam _lote(), que desfaz tudo se der erro.
        self.con = sqlite3.connect(str(caminho), check_same_thread=False, timeout=30, isolation_level=None)
        self._trava = threading.RLock()
        self.con.execute("PRAGMA journal_mode=WAL")
        self.con.execute("PRAGMA busy_timeout=30000")
        self.con.executescript(
            """
            CREATE TABLE IF NOT EXISTS dias (
                data TEXT PRIMARY KEY,
                status TEXT NOT NULL,
                ja_lancado INTEGER NOT NULL,
                linhas TEXT NOT NULL,
                atualizado_em TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS envios (
                data TEXT NOT NULL,
                chave TEXT NOT NULL,
                minutos INTEGER NOT NULL,
                worklog_id TEXT,
                enviado_em TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS amostras (
                instante TEXT NOT NULL,
                app TEXT NOT NULL,
                titulo TEXT NOT NULL,
                segundos INTEGER NOT NULL,
                chave TEXT,
                fonte TEXT
            );
            CREATE INDEX IF NOT EXISTS amostras_instante ON amostras (instante);
            CREATE TABLE IF NOT EXISTS foco (
                desde TEXT NOT NULL,
                chave TEXT
            );
            CREATE TABLE IF NOT EXISTS pausas (
                inicio TEXT NOT NULL,
                fim TEXT NOT NULL,
                motivo TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS meta (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS associacoes (
                rotulo TEXT PRIMARY KEY,
                chave TEXT NOT NULL,
                criada_em TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS issues (
                chave TEXT PRIMARY KEY,
                nome TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS uso (
                data TEXT NOT NULL,
                rotulo TEXT NOT NULL,
                chave TEXT NOT NULL,
                minutos INTEGER NOT NULL,
                PRIMARY KEY (data, rotulo, chave)
            );
            CREATE TABLE IF NOT EXISTS respostas (
                id TEXT PRIMARY KEY,
                resposta TEXT NOT NULL,
                em TEXT DEFAULT CURRENT_TIMESTAMP
            );
            CREATE TABLE IF NOT EXISTS preferencias (
                chave TEXT PRIMARY KEY,
                valor TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS rotulos (
                de TEXT NOT NULL,
                ate TEXT NOT NULL,
                titulo TEXT NOT NULL DEFAULT '',
                descricao TEXT NOT NULL DEFAULT ''
            );
            CREATE INDEX IF NOT EXISTS rotulos_de ON rotulos (de);
            """
        )
        if "fixo" not in {c[1] for c in self.con.execute("PRAGMA table_info(foco)")}:
            self.con.execute("ALTER TABLE foco ADD COLUMN fixo INTEGER NOT NULL DEFAULT 0")
            self.con.commit()

    @contextmanager
    def _lote(self):
        """Vários comandos numa transação só: tudo ou nada."""
        with self._trava:
            self.con.execute("BEGIN IMMEDIATE")
            try:
                yield
            except BaseException:
                self.con.execute("ROLLBACK")
                raise
            self.con.execute("COMMIT")

    def salvar(self, dia: date, status: str, ja_lancado: int, linhas: list[Linha]) -> None:
        self.con.execute(
            "INSERT INTO dias (data, status, ja_lancado, linhas) VALUES (?, ?, ?, ?) "
            "ON CONFLICT(data) DO UPDATE SET status=excluded.status, ja_lancado=excluded.ja_lancado, "
            "linhas=excluded.linhas, atualizado_em=CURRENT_TIMESTAMP",
            (dia.isoformat(), status, ja_lancado, json.dumps([l.para_dict() for l in linhas], ensure_ascii=False)),
        )
        self.con.commit()

    def apagar_dia(self, dia: date) -> None:
        self.con.execute("DELETE FROM dias WHERE data = ?", (dia.isoformat(),))
        self.con.execute("DELETE FROM meta WHERE chave = ?", (f"rascunho_base:{dia.isoformat()}",))
        self.con.commit()

    def carregar(self, dia: date) -> tuple[str, int, list[Linha]] | None:
        row = self.con.execute("SELECT status, ja_lancado, linhas FROM dias WHERE data = ?", (dia.isoformat(),)).fetchone()
        if not row:
            return None
        return row[0], row[1], [Linha(**d) for d in json.loads(row[2])]

    def registrar_envio(self, dia: date, chave: str, minutos: int, worklog_id: str) -> None:
        self.con.execute(
            "INSERT INTO envios (data, chave, minutos, worklog_id) VALUES (?, ?, ?, ?)",
            (dia.isoformat(), chave, minutos, worklog_id),
        )
        self.con.commit()

    def envios(self, dia: date) -> list[dict]:
        rows = self.con.execute(
            "SELECT chave, minutos, worklog_id, enviado_em FROM envios WHERE data = ?", (dia.isoformat(),)
        ).fetchall()
        return [dict(zip(("chave", "minutos", "worklog_id", "enviado_em"), r)) for r in rows]

    def exemplos_de_tickets(self, navegadores: set[str]) -> list[tuple[str, str]]:
        """Textos já ligados a um ticket: resumo das abas do Jira e linhas de planilhas anteriores."""
        exemplos = [
            (titulo, chave) for app, titulo, chave in self.con.execute(
                "SELECT DISTINCT app, titulo, chave FROM amostras WHERE fonte = 'titulo' AND chave IS NOT NULL AND titulo != ''")
            if app.lower() in navegadores
        ]
        for (linhas,) in self.con.execute("SELECT linhas FROM dias"):
            exemplos += [(l["acao"], l["chave"]) for l in json.loads(linhas)
                         if l.get("chave") and l.get("acao") and l.get("origem") != "completar"]
        return exemplos

    def dias(self, desde: date) -> dict[str, str]:
        """Status de cada dia com rascunho a partir de uma data."""
        rows = self.con.execute("SELECT data, status FROM dias WHERE data >= ?", (desde.isoformat(),)).fetchall()
        return dict(rows)

    # Preferências

    def preferencias(self) -> dict:
        return {k: json.loads(v) for k, v in self.con.execute("SELECT chave, valor FROM preferencias")}

    def salvar_preferencias(self, prefs: dict) -> None:
        with self._lote():
            self.con.executemany(
                "INSERT INTO preferencias (chave, valor) VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
                [(k, json.dumps(v, ensure_ascii=False)) for k, v in prefs.items()],
            )
        self.con.commit()

    # Observador de janelas

    def registrar_amostra(self, instante: datetime, app: str, titulo: str, segundos: int,
                          chave: str | None, fonte: str | None) -> None:
        self.con.execute(
            "INSERT INTO amostras (instante, app, titulo, segundos, chave, fonte) VALUES (?, ?, ?, ?, ?, ?)",
            (instante.isoformat(timespec="seconds"), app, titulo, segundos, chave, fonte),
        )
        self.con.commit()

    def amostras(self, dia: date) -> list[dict]:
        rows = self.con.execute(
            "SELECT instante, app, titulo, segundos, chave, fonte FROM amostras WHERE instante LIKE ? ORDER BY instante, rowid",
            (dia.isoformat() + "%",),
        ).fetchall()
        return [dict(zip(("instante", "app", "titulo", "segundos", "chave", "fonte"), r)) for r in rows]

    def apagar_amostras_desde(self, instante: datetime) -> int:
        """Desfaz amostras gravadas durante uma ausência que só foi percebida depois (reuniões ficam)."""
        cur = self.con.execute("DELETE FROM amostras WHERE instante >= ? AND COALESCE(fonte, '') != 'reuniao'",
                               (instante.isoformat(timespec="seconds"),))
        self.con.commit()
        return cur.rowcount

    def registrar_pausa(self, inicio: datetime, fim: datetime, motivo: str) -> None:
        self.con.execute("INSERT INTO pausas (inicio, fim, motivo) VALUES (?, ?, ?)",
                         (inicio.isoformat(timespec="seconds"), fim.isoformat(timespec="seconds"), motivo))
        self.con.commit()

    def pausas(self, dia: date) -> list[dict]:
        rows = self.con.execute("SELECT inicio, fim, motivo FROM pausas WHERE inicio LIKE ? ORDER BY inicio",
                                (dia.isoformat() + "%",)).fetchall()
        return [dict(zip(("inicio", "fim", "motivo"), r)) for r in rows]

    def limpar_detalhes(self, hoje: date, manter_dias: int) -> int:
        """Apaga amostras e pausas de dias já lançados, guardando pelo menos manter_dias de histórico.
        O rascunho do dia (linhas por ticket) continua guardado."""
        limite = date.fromordinal(hoje.toordinal() - manter_dias).isoformat()
        fechados = "SELECT data FROM dias WHERE status IN ('lancado', 'publicado') AND data < ?"
        n = self.con.execute(f"DELETE FROM amostras WHERE substr(instante, 1, 10) IN ({fechados})", (limite,)).rowcount
        self.con.execute(f"DELETE FROM pausas WHERE substr(inicio, 1, 10) IN ({fechados})", (limite,))
        self.con.execute(f"DELETE FROM rotulos WHERE substr(de, 1, 10) IN ({fechados})", (limite,))
        self.con.commit()
        return n

    def rotulos(self, dia: date) -> list[dict]:
        """Título e descrição que você deu a trechos do histórico do dia."""
        rows = self.con.execute("SELECT de, ate, titulo, descricao FROM rotulos WHERE de LIKE ? ORDER BY de",
                                (dia.isoformat() + "%",)).fetchall()
        return [dict(zip(("de", "ate", "titulo", "descricao"), r)) for r in rows]

    def definir_rotulo(self, de: str, ate: str, titulo: str, descricao: str) -> None:
        """Troca o rótulo do trecho: apaga os que se sobrepõem e grava o novo (vazio = volta ao observado)."""
        with self._lote():
            self.con.execute("DELETE FROM rotulos WHERE de < ? AND ate > ?", (ate, de))
            if titulo or descricao:
                self.con.execute("INSERT INTO rotulos (de, ate, titulo, descricao) VALUES (?, ?, ?, ?)",
                                 (de, ate, titulo, descricao))

    def reduzir_titulos(self, reduzir) -> None:
        """Uma vez só: troca títulos completos já gravados pelo mínimo necessário."""
        if self.con.execute("SELECT 1 FROM meta WHERE chave = 'titulos_reduzidos'").fetchone():
            return
        rows = self.con.execute("SELECT rowid, app, titulo, chave, fonte FROM amostras").fetchall()
        with self._lote():
            self.con.executemany("UPDATE amostras SET titulo = ? WHERE rowid = ?",
                                 [(reduzir(app, titulo, chave, fonte), rowid) for rowid, app, titulo, chave, fonte in rows])
            self.con.execute("INSERT INTO meta (chave, valor) VALUES ('titulos_reduzidos', '1')")
        self.con.commit()

    def definir_foco(self, chave: str | None, desde: datetime | None = None, fixo: bool = False) -> None:
        """fixo: o foco ganha até da chave no título (para quando o TrampoTime atribuiu errado)."""
        self.con.execute("INSERT INTO foco (desde, chave, fixo) VALUES (?, ?, ?)",
                         ((desde or datetime.now()).isoformat(timespec="seconds"), chave, int(bool(fixo and chave))))
        self.con.commit()

    def foco_atual(self) -> str | None:
        row = self.con.execute("SELECT chave FROM foco ORDER BY rowid DESC LIMIT 1").fetchone()
        return row[0] if row else None

    def foco_fixo(self) -> bool:
        row = self.con.execute("SELECT fixo FROM foco ORDER BY rowid DESC LIMIT 1").fetchone()
        return bool(row and row[0])

    def reatribuir_desde(self, instante: str, chave: str) -> int:
        """Corrige o ticket das amostras a partir de um instante (reuniões ficam)."""
        cur = self.con.execute("UPDATE amostras SET chave = ?, fonte = 'foco' WHERE instante >= ? AND COALESCE(fonte, '') != 'reuniao'",
                               (chave, instante))
        self.con.commit()
        return cur.rowcount

    def planilhas_confirmadas(self) -> list[tuple[str, list[dict]]]:
        rows = self.con.execute("SELECT data, linhas FROM dias WHERE status IN ('aprovado', 'lancado', 'publicado') ORDER BY data")
        return [(d, json.loads(linhas)) for d, linhas in rows]

    def gravar_uso(self, dia: date, uso: dict[tuple[str, str], int]) -> None:
        """Histórico compacto (atividade, ticket, minutos) de um dia fechado; sobrevive à limpeza do detalhe."""
        with self._lote():
            self.con.execute("DELETE FROM uso WHERE data = ?", (dia.isoformat(),))
            self.con.executemany("INSERT INTO uso (data, rotulo, chave, minutos) VALUES (?, ?, ?, ?)",
                                 [(dia.isoformat(), r, c, m) for (r, c), m in uso.items() if m > 0])
        self.con.commit()

    def uso_anterior(self, antes: date) -> dict[tuple[str, str], int]:
        """Em quantos dias anteriores cada atividade foi usada com cada ticket."""
        rows = self.con.execute("SELECT rotulo, chave, COUNT(DISTINCT data) FROM uso WHERE data < ? GROUP BY rotulo, chave",
                                (antes.isoformat(),))
        return {(r, c): n for r, c, n in rows}

    def nomes_de_issues(self) -> dict[str, str]:
        """Nome que você deu a cada issue (ex. DMANESTOQ-15650 -> VacationFlow); vale mais que o resumo da aba do Jira."""
        return dict(self.con.execute("SELECT chave, nome FROM issues"))

    def nomear_issue(self, chave: str, nome: str) -> None:
        self.con.execute("INSERT INTO issues (chave, nome) VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET nome=excluded.nome",
                         (chave, nome))

    def associacoes(self) -> dict[str, str]:
        return dict(self.con.execute("SELECT rotulo, chave FROM associacoes"))

    def associar(self, rotulo: str, chave: str) -> None:
        self.con.execute("INSERT INTO associacoes (rotulo, chave) VALUES (?, ?) ON CONFLICT(rotulo) DO UPDATE SET chave=excluded.chave",
                         (rotulo, chave))
        self.con.commit()

    def desassociar(self, rotulo: str) -> None:
        self.con.execute("DELETE FROM associacoes WHERE rotulo = ?", (rotulo,))
        self.con.commit()

    def respondidas(self) -> set[str]:
        return {i for (i,) in self.con.execute("SELECT id FROM respostas")}

    def responder(self, id_: str, resposta: str) -> None:
        self.con.execute("INSERT INTO respostas (id, resposta) VALUES (?, ?) ON CONFLICT(id) DO UPDATE SET resposta=excluded.resposta",
                         (id_, resposta))
        self.con.commit()

    def reatribuir_entre(self, inicio: str, fim: str, chave: str, fonte: str, incluir_reunioes: bool = False) -> int:
        filtro = "" if incluir_reunioes else " AND COALESCE(fonte, '') != 'reuniao'"
        cur = self.con.execute("UPDATE amostras SET chave = ?, fonte = ? WHERE instante >= ? AND instante < ?" + filtro,
                               (chave, fonte, inicio, fim))
        self.con.commit()
        return cur.rowcount

    def apagar_amostras_entre(self, inicio: str, fim: str, incluir_reunioes: bool = False) -> int:
        filtro = "" if incluir_reunioes else " AND COALESCE(fonte, '') != 'reuniao'"
        return self.con.execute("DELETE FROM amostras WHERE instante >= ? AND instante < ?" + filtro, (inicio, fim)).rowcount

    def reatribuir_instantes(self, instantes: list[str], chave: str, fonte: str) -> int:
        n = 0
        with self._lote():
            for instante in instantes:
                n += self.con.execute("UPDATE amostras SET chave = ?, fonte = ? WHERE instante = ?", (chave, fonte, instante)).rowcount
        self.con.commit()
        return n

    def apagar_pausas_entre(self, inicio: str, fim: str) -> None:
        self.con.execute("DELETE FROM pausas WHERE inicio >= ? AND fim <= ?", (inicio, fim))
        self.con.commit()

    def segundos_do_dia(self, dia: date) -> int:
        row = self.con.execute("SELECT COALESCE(SUM(segundos), 0) FROM amostras WHERE instante LIKE ?", (dia.isoformat() + "%",)).fetchone()
        return row[0]

    def dias_pendentes(self, hoje: date, desde: date) -> list[str]:
        """Dias antes de hoje com atividade observada ou rascunho e ainda não lançados, do mais recente ao mais antigo."""
        com_dados = {d for (d,) in self.con.execute(
            "SELECT DISTINCT substr(instante, 1, 10) FROM amostras WHERE instante >= ? AND instante < ?",
            (desde.isoformat(), hoje.isoformat()))}
        status = dict(self.con.execute("SELECT data, status FROM dias WHERE data >= ? AND data < ?",
                                       (desde.isoformat(), hoje.isoformat())))
        com_dados |= set(status)
        return sorted((d for d in com_dados if status.get(d) not in {"lancado", "publicado"}), reverse=True)

    def meta(self, chave: str) -> str | None:
        row = self.con.execute("SELECT valor FROM meta WHERE chave = ?", (chave,)).fetchone()
        return row[0] if row else None

    def apagar_meta(self, chave: str) -> None:
        self.con.execute("DELETE FROM meta WHERE chave = ?", (chave,))

    def definir_meta(self, chave: str, valor: str) -> None:
        self.con.execute("INSERT INTO meta (chave, valor) VALUES (?, ?) ON CONFLICT(chave) DO UPDATE SET valor=excluded.valor",
                         (chave, valor))
        self.con.commit()
