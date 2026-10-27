# TrampoTime

Ferramenta pessoal da Flavia (TOTVS) para apontar horas no **Tempo Timesheets** do Jira Data Center (`https://jiraproducao.totvs.com.br`). Monta o dia de **7h30** a partir do que ela realmente fez, ela revisa, e só então os apontamentos são enviados. Responda e escreva textos de interface em português do Brasil.

## Regras que não mudam

- **O token do Jira fica só no `.env` local.** Nunca pedir o token no chat, nunca imprimir em log, nunca commitar, nunca colocar em GitHub Secrets. `.env`, `*.db`, `*.csv`, `credentials.json` e `token.json` estão no `.gitignore`.
- **Nada vai ao Tempo sem aprovação explícita.** O fluxo é sempre rascunho → revisão → aprovar → simular → enviar (com confirmação).
- **Tudo roda local.** API só em `127.0.0.1`; dados de atividade nunca saem da máquina. O que sai é apenas o apontamento aprovado.
- **Tudo gratuito.** Sem serviços pagos.
- Antes de enviar ao Tempo, conferir de novo as horas já lançadas no dia e abortar se mudaram; nunca enviar o mesmo dia duas vezes.
- Os testes usam Jira e Tempo falsos (`httpx.MockTransport`); nenhum teste pode chamar o Jira real.

## Estado atual (versão 0.2)

**Primeiro teste real (21/10/2026):** a API do Jira está bloqueada pelo Cloudflare da TOTVS (403 "Attention Required!" para qualquer cliente que não seja navegador, com ou sem token, com ou sem a VPN Cisco; o DNS aponta para o Cloudflare mesmo na VPN). A Flavia decidiu **não acionar a TI** e seguir independente: o padrão agora é o **modo planilha** (`USAR_JIRA=nao`). O TrampoTime observa as janelas e gera a planilha, e ela lança no Tempo à mão. Não tentar driblar o Cloudflare (por exemplo, fingindo ser um navegador). O código do Jira e do Tempo continua lá para o caso de o acesso abrir.

Núcleo em Python com página local, 74 testes passando. Roda em segundo plano (`python -m trampotime iniciar`/`parar`) e liga com o Windows (`instalar-inicio`: atalho `TrampoTime.lnk` na pasta Inicializar com `pythonw -m trampotime iniciar`; `remover-inicio` desfaz). Logs em `trampotime.log`/`trampotime-erros.log` (fora do git). `python -m trampotime` sobe a API em 127.0.0.1:8765, liga o observador numa thread e abre a página.

| Arquivo | Papel |
| --- | --- |
| `trampotime/config.py` | Lê `.env` (USAR_JIRA, PROJETOS, observador, JIRA_URL/TOKEN, jornada, bloco, ticket de reuniões, agenda) |
| `trampotime/observador.py` | Windows (ctypes/winreg): app e título da janela em foco a cada 5 s, pula ociosidade; chave no título (aceita minúsculas quando PROJETOS está definido) > ticket em foco. **Reunião** = microfone em uso pelo navegador/Teams/Zoom (registro `CapabilityAccessManager\ConsentStore\microphone`, `LastUsedTimeStop == 0`) ou aba do Meet em foco; o nome vem da janela do Meet visível ou da última aba do Meet vista (retomada do banco ao reiniciar). Em reunião não há ociosidade e a reunião ganha da janela em foco. Página mostra tudo; rascunho exige mín. 3 min |
| `trampotime/jira.py` | Jira DC REST (`/rest/api/2/myself`, `/search`, `/issue`) e Tempo (`POST /rest/tempo-timesheets/4/worklogs/`, `POST .../worklogs/search`), auth `Bearer` com token pessoal |
| `trampotime/agenda.py` | Google Agenda opcional (OAuth de app desktop, `credentials.json`), lida só depois de conectada; ignora dia inteiro, recusadas e IGNORAR_EVENTOS. Eventos dela ganham das chamadas observadas com o mesmo título; chamadas fora da agenda (Meet instantâneo, ligação pelo Chat) são somadas |
| `trampotime/motor.py` | Regras das 7h30: desconta o já lançado, reunião com chave vai para o ticket, sem chave vai para TICKET_REUNIOES ou fica sem ticket, resto dividido entre tickets do dia por peso, arredondamento em blocos de 15 min (maiores restos) |
| `trampotime/servico.py` | Fluxo gerar, revisar, aprovar, publicar (simular ou enviar) |
| `trampotime/armazenamento.py` | SQLite local (`trampotime.db`): dias, envios, amostras do observador, foco e preferências (valem por cima do `.env`) |
| `trampotime/planilha.py` | Rascunho em CSV com `;` (aceita 90, 1h30, 1:30) |
| `trampotime/api.py` | FastAPI + página: `/`, `/estado`, `/preferencias`, `/foco`, `/agenda/conectar`, `/dias/{data}/rascunho|linhas|aprovar|lancado|publicar|atividade|tempo`. Só aceita Host 127.0.0.1/localhost e exige `X-TrampoTime: 1` em toda alteração |
| `trampotime/avisos.py` | Notificações do Windows (toast via PowerShell, AppID do PowerShell, clique abre a página): 15 min sem ticket (repete a cada 30), dia anterior com dados e não lançado, jornada completa sem planilha aprovada (uma vez por dia, marcado na tabela `meta`). Preferência `avisos` |
| `trampotime/perguntas.py` | Perguntas do TrampoTime (card entre Hoje e Histórico; `GET /perguntas`, `POST /perguntas/responder`): **juntar** dois trechos do mesmo ticket com até 30 min entre eles, sem reunião nem outro ticket de 5+ min no meio (sim = reatribui o meio e cria amostras `juntado` para o tempo vazio, apaga pausas ali); **associar** atividade identificável (documento, aba guardada, VM, pasta do VS Code) com 10+ min sem ticket próprio a uma demanda do dia: nota = 0,6·texto (cosseno com o resumo) + 0,4·uso perto da issue aberta + 0,15·dias anteriores (tabela `uso`), mínimo 30% (sim = tabela `associacoes`, que vira regra em `atribuir`: reunião > fixado > título > associação > foco); **rotinas** (ver `rotinas.py`). Não = tabela `respostas`, não pergunta de novo |
| `trampotime/rotinas.py` | Análise preditiva de rotinas a partir das planilhas confirmadas (aprovado/lançado, guardadas para sempre): reunião com o mesmo nome em 3+ dias e 60%+ dos dias confirmados dos últimos 15, com horário mediano, duração mediana e ticket mais usado. Vira pergunta "lançar sempre nesse ticket?" (associação aplicada às reuniões do rascunho) e "não vi a reunião hoje, você participou?" (sim = amostras de reunião no horário típico). Histórico compacto `uso` (atividade, ticket, minutos por dia, sem Chat) gravado ao marcar o dia como lançado |
| `trampotime/sugestao.py` | Sugere o ticket pela descrição: KNN (k=5) sobre TF-IDF de palavras e trigramas sem acento; exemplos = resumo das abas do Jira + linhas de planilhas anteriores (`Banco.exemplos_de_tickets`); rota `GET /sugerir?texto=` |
| `trampotime/pagina.html` | Página única (HTML/JS puro, sem CDN): contador, foco, tickets vistos, dias para lançar, planilha editável, configuração no primeiro uso |
| `trampotime/__main__.py` | CLI: `python -m trampotime observar|foco|testar|gerar|ver|revisar|aprovar|simular|enviar [AAAA-MM-DD]` |

**Mesma atividade, um registro só:** `observador.resolver` olha a sequência no tempo. Trecho de reunião sem nome (sala de espera, "Chamada no navegador") recebe o nome da reunião com nome a menos de 10 min; trecho sem ticket de até 15 min herda o ticket de antes (ou o de depois, no começo). `periodos` dá o início e o fim do dia e os períodos de cada atividade, que aparecem na página. Na VM (mstsc), só se vê o nome da máquina no título, não o que acontece dentro dela.

**Pausas:** tela bloqueada (`OpenInputDesktop`/`SwitchDesktop`, ou LockApp.exe em foco), suspensão (salto no relógio entre voltas do observador) e ausência sem teclado/mouse viram registros na tabela `pausas`; a pausa começa no último uso do teclado/mouse e as amostras gravadas depois disso são apagadas (reuniões ficam). Em reunião não há ausência.

**Card Agora (dentro de Hoje até agora):** `GET /agora` a cada 3 s mostra o ticket atribuído e o motivo (`observador.explicar`: branch do VS Code, aba do Jira, microfone, foco, foco fixado, sem ticket). "Não é isso" (`POST /corrigir`) passa o trecho atual (desde a última troca de ticket) para a chave certa e fixa o foco (`foco.fixo`), que ganha até da chave no título, menos de reunião; limpar o foco desfaz. Ordem de atribuição: reunião > foco fixado > chave no título > foco.

**Correção pelo calendário (Histórico, modo Observado):** clicar num bloco abre um painel para trocar o ticket (com sugestões do KNN) ou juntar com o anterior/próximo; Ctrl + clique seleciona vários e junta do primeiro ao último, preenchendo o tempo vazio e apagando pausas do meio (reuniões só com "incluir reuniões do meio"); Esc cancela. Rota `POST /dias/{d}/reatribuir {de, ate, chave, preencher, incluir_reunioes}` → `Servico.reatribuir_intervalo` (fonte `manual`), que a pergunta de juntar também usa. Os blocos do histórico trazem `de`/`ate` exatos.

**Duas telas:** `SegundaTela` usa a janela sob o mouse quando o mouse mexe sobre outra janela sem clicar (lendo/rolando na outra tela) e volta para a janela em foco quando há teclado sem mouse.

**Banco:** SQLite em autocommit (`isolation_level=None`), WAL e `busy_timeout` de 30 s; lotes em `Banco._lote()` (BEGIN IMMEDIATE/ROLLBACK). Corrigiu o "database is locked" que derrubava o observador. Cada volta do observador (`_volta`) é protegida: erro vai para o log e o laço segue.

**Reserva de horário:** trabalho fora do computador (retrospectiva, ajudando colega). "Agora, até eu voltar" (`POST/DELETE /reserva`, meta `reserva_ativa`): o observador grava amostras de reunião (`app=trampotime-reserva`) mesmo com tela bloqueada e sem pausa; encerra sozinha depois de 8 h ou na virada do dia. "Um horário" (`POST /dias/{d}/reservar {de, ate}` HH:MM): substitui o intervalo e apaga pausas. Entra na planilha como compromisso de horário fixo.

**Ruído de reunião:** trecho de reunião de até 5 min entre dois trechos do mesmo ticket é absorvido pelo ticket em `resolver` (`RUIDO_REUNIAO_MINUTOS`).

**Issue (nome na interface; no código continua `chave`/`ticket`):** todo campo de issue aceita "DMANESTOQ-15650 - VacationFlow" (`modelos.separar_issue`; também `:` ou `|`): a chave vai para o campo e o nome é guardado na tabela `issues` (`POST /issues`, ou automático em `api._chave` e nas linhas da planilha). O nome dado vale mais que o resumo da aba do Jira: resumo da issue em `atividade`, descrição da linha no `gerar`, texto das perguntas e sugestões dos campos (`<option label>`). No calendário o título do bloco é a chave e o nome vem como descrição.

**Página:** cabeçalho só com ícones: tema (só claro/escuro, sol/lua, `data-tema` no `<html>`; a primeira vez segue o Windows), cadeado (Privacidade: o que é observado e guardado) e engrenagem (Configuração). Reservar horário e Exportar (CSV com `Content-Disposition`) ficam no topo do Histórico; Validar dia no rodapé dele. O calendário cresce com o dia (sem barra própria); Ações visualizadas tem rolagem própria. Coluna principal: Hoje até agora (com o card Agora, foco e Limpar foco), Perguntas e Histórico do dia (em destaque, com ◀ dia ▶: outro dia substitui o histórico; manutenção toda aqui: trocar ticket, juntar, Excluir `POST /dias/{d}/excluir`). A planilha saiu da página e mora na janela Validar dia. Coluna lateral: Dias para lançar e Ações visualizadas (antigo Tickets e reuniões vistos: grupos Issues, Reuniões e Sem issue com quantidade e total, um cartão por ação do maior tempo ao menor, com resumo, onde, quando e barra do peso no dia; Mover aparece ao passar o mouse). Todo card (`.secao` com `.cab`) tem Esconder/Mostrar, há Esconder tudo e Esconder painel (lembrado no `localStorage`); Dias para lançar mostra `manter_dias` dias úteis. URLs longas quebram linha; título de aba carregando (só endereço, `parece_url`) não vira resumo nem exemplo da sugestão. A planilha salva sozinha 1 s depois da última tecla; linhas sem ticket mostram sugestões clicáveis com a confiança.

**Início e fim das linhas:** `motor.horarios` monta a agenda do dia para lançar no Tempo: reuniões ficam no horário real (arredondado ao bloco), o resto preenche a partir da primeira atividade na ordem em que apareceu, pulando reuniões e pausas de 15 min ou mais (uma linha pode virar dois trechos). A página recalcula ao editar (`POST /dias/{d}/horarios`) e o CSV tem `inicio` e `fim`.

**Privacidade (LGPD):** do título da janela só se grava o nome da reunião, o resumo da aba do Jira, o nome da VM e o título dos apps e abas de trabalho da preferência `guardar_titulos` (padrão `config.GUARDAR_TITULOS`: Office, Camtasia, Google Chat, Docs/Planilhas/Apresentações, Vids e qualquer título com TOTVS; casar pela URL exige a extensão do Chrome; `.exe` casa pelo app, o resto por parte do título) (`observador.titulo_minimo`). A página tem o **Histórico do dia** como calendário no estilo da visão diária do Tempo (horas na lateral, bloco com altura pela duração, pausas hachuradas, linha de agora), alternando **Observado** (`observador._historico`, blocos de 3 min ou mais) e **Planilha** (os trechos de `motor.horarios`); cada ticket mostra onde foi trabalhado (`observador._onde`: VS Code com pasta e branch, VM, Excel...); títulos antigos foram reduzidos uma vez (`meta.titulos_reduzidos`). Amostras e pausas de dias lançados são apagadas depois de `manter_dias` (mínimo 3, configurável na página); a planilha do dia fica. Ao diagnosticar, mostrar só números agregados, nunca títulos.

**Branch do VS Code:** o padrão da equipe é a branch ter a chave da issue. O título do VS Code não mostra a branch por padrão; foi configurado em `%APPDATA%\Code\User\settings.json` o `window.title` com `${activeRepositoryBranchName}`. Num `.exe` para outras pessoas, a configuração inicial deve oferecer isso.

**Abas do Chrome:** o Windows só expõe a aba ativa de cada janela. Ler todas as abas (e a URL) exige a extensão do Chrome (fase 1); conferir se o Chrome da TOTVS permite extensões em modo desenvolvedor.

No modo planilha, `gerar` usa os tickets observados (peso = minutos), `ja_lancado` é 0 e `publicar` recusa; o dia fecha com "Marquei no Tempo" (status `lancado`). As reuniões dela são no Google Meet. A JQL de `tickets_do_dia` nunca foi validada contra o Jira real.

## Arquitetura alvo

Um programa único local. Sinais de atividade entram num receptor, viram blocos numa linha do tempo (SQLite), a **atribuição** decide o ticket de cada bloco e o motor fecha o dia.

Fontes: extensão do Chrome (aba ativa e URL, só domínios permitidos), extensão do VS Code (pasta, branch, arquivo), observador de janelas do Windows (app e título a cada 5 s, pausa em inatividade; cobre VM), Google Agenda, Jira e Tempo.

**Atribuição em modo misto, nesta ordem:**
1. Regra clara: chave de ticket (`[A-Z][A-Z0-9]+-\d+`) na branch, na URL do Jira ou no título.
2. Cérebro com confiança alta (fase 2).
3. Ticket em foco declarado pela usuária (vale até trocar).
4. Sem ticket, destacado na revisão.

**Reunião aceita com trabalho em paralelo:** a reunião ganha por padrão; se houver atividade forte em outro ticket durante a reunião, mostrar aviso na revisão para ela decidir. Nunca contar em dobro.

Segurança da API local: escutar só em `127.0.0.1` e exigir um código compartilhado (gerado na instalação) nas chamadas das extensões, para nenhum site do navegador conseguir ler ou gravar dados.

## Fases

0. **Executável de teste:** `TrampoTime.exe` (PyInstaller) com núcleo, observador de janelas, ticket em foco e página local com contador ("5h10 de 7h30"), rascunho editável na tela, Simular e Enviar. Pronto quando: uma semana de dias fechados com poucos ajustes.
1. **Extensões:** Chrome (Manifest V3, carregada sem loja) e VS Code (`.vsix` local). Pronto quando: código no VS Code cai no ticket certo sem declarar foco.
2. **Cérebro:** grafo (NetworkX) ligando tickets, branches, pastas, janelas, VMs e reuniões + classificador (scikit-learn) treinado com as planilhas aprovadas; sugestões com confiança, ex. "DMANESTOQ-15684 (87%)".
3. **App instalável:** Inno Setup, início com o Windows, ícone na bandeja (pystray) com contador; Ollama opcional para tickets novos.

## Ideias registradas (não desenvolver ainda)

- **Avaliado em 21/10/2026: não é necessário agora.** O observador já cobre bloqueio, suspensão, ausência, reuniões e VM ao vivo. Se um dia precisar preencher dias sem o TrampoTime aberto, usar o log do Sistema (legível sem admin): Kernel-Power 506/507 (entrou/saiu do modo de espera), 42/107 (suspensão/retomada) e o boot do dia. O log de Segurança (4800/4801 bloqueio) só abre como admin.
- **Validar com dados de processos do Windows ("logs do Gerenciador de Tarefas"):** o Gerenciador não guarda histórico próprio. Fontes possíveis: o SRUM (`C:\Windows\System32\sru\SRUDB.dat`, uso por app por hora dos últimos ~30 dias; precisa de admin para ler), o "Histórico de aplicativos" (só apps da Store) e o log de segurança 4688 (precisa de política de auditoria, ou seja, TI). A ideia é usar isso como conferência: preencher dias passados sem observador e validar o rascunho (ex. "VS Code aberto 3h, mas só 1h atribuída"). O caminho mais barato é o próprio observador anotar a lista de processos abertos a cada minuto.

- **RNN para sugerir o ticket:** a Flavia pediu "RNN"; foi feito KNN porque uma rede recorrente precisa de centenas/milhares de exemplos e do PyTorch. Reavaliar (RNN ou o classificador da fase 2) quando houver umas 200+ linhas aprovadas.

## Decisões

- Nome: **TrampoTime**. Repositório privado `FlaviaAtonieli/trampotime`.
- Núcleo continua em Python.
- Pendentes de confirmação da usuária: modo misto (recomendado), página com contador antes da bandeja (recomendado), sistema operacional (provavelmente Windows).
- A validar com a TI da TOTVS: `.exe` sem assinatura (SmartScreen/antivírus), extensões fora da loja no Chrome corporativo, `.vsix` no VS Code, política de registro de atividade (LGPD), RAM para Ollama, e se pode manter a ferramenta em conta pessoal do GitHub.

## Comandos

```bash
python -m venv .venv && .venv\Scripts\activate   # Windows
pip install -r requirements.txt
pytest                                           # testes, sem rede
python -m trampotime testar                          # confere o token do .env
uvicorn trampotime.api:app --port 8765               # API local, docs em /docs
```

## Próximo passo

Usar a página por um dia real e conferir a planilha. Depois: Meet em segundo plano (ela troca de janela durante a reunião) não conta como reunião; avisar na página se o observador da CLI e o do app rodarem juntos; `.exe` da fase 0 (incluir `pagina.html` nos dados do PyInstaller).
