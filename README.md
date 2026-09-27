# monitor

Painel local do que os agentes de IA estão fazendo agora, em todos os harnesses, numa página só:
sessões, subagentes e workflows do Claude Code, do Codex, do Antigravity CLI (`agy`) e do Pi. Ele
lê em disco os transcripts que os harnesses já gravam: não tem hook, não pede nada ao modelo e não
gasta tokens.

## Usar

Requisito: Python 3.9+. Não há dependência além da biblioteca padrão.

```bash
python monitor.py            # sobe o servidor em segundo plano (ou reaproveita o que já está no ar) e abre o painel numa janela própria
python monitor.py parar      # derruba o servidor
python monitor.py --rede     # também atende a rede local, com chave: o celular (veja abaixo)
python monitor.py iniciar-no-login [--rede]   # sobe o painel quando você entra no Windows
python monitor.py nao-iniciar-no-login        # desfaz o anterior
```

O painel fica em `http://127.0.0.1:8765/`. A janela própria é o Edge ou o Chrome com `--app`, sem
abas nem barra de endereço; sem nenhum dos dois, abre no navegador padrão. O servidor sobrevive ao
terminal que o subiu, só escuta em `127.0.0.1` (com `--rede`, também na rede local) e grava os erros num
log no diretório temporário do sistema (o caminho sai na tela). Ao subir, ele já faz a primeira leitura dos
transcripts, e a página que abre em seguida não espera por ela.

O `iniciar-no-login` grava o valor `monitor` em `HKCU\Software\Microsoft\Windows\CurrentVersion\Run`, com
o `pythonw` (sem janela de console); no login o painel sobe e abre a janela própria.

| Opção | Efeito |
|---|---|
| `--porta N` | Usa outra porta (padrão 8765) |
| `--sem-navegador` | Não abre o navegador |
| `--rede` | Atende também a rede local (e o Tailscale), com chave |

## O que a tela mostra

Só as sessões abertas, em duas grades de cartões: "Em andamento" no alto e "Finalizadas" embaixo (duas
colunas no monitor em pé, três na tela larga, uma no celular); com mais de um harness aberto, cada card diz o
seu. Cada card mostra projeto, modelo (pelo nome falado, `Opus 5.5`, `Haiku 4.5`, `GPT 5.6 Terra`, numa
etiqueta com a cor da família: opus, sonnet, haiku, fable, gpt, gemini), status, contexto (a porcentagem da janela, verde até 50%,
laranja até 80% e vermelha acima; os tokens ficam no título), tempo de sessão e de turno, o prompt e
o que o agente está fazendo. A sessão que fecha sai do painel. Os status e as cores são os do herdr: trabalhando
(amarelo), esperando você (vermelho: pergunta, permissão ou turno travado sem sinal há 2 min) e
finalizado (teal: o turno fechou). O "fazendo" é macro: a última fala do agente no turno ou, sem
ela, a descrição do passo (`description`, `toolSummary`); o comando fica nos eventos.

- **Ordem:** quem espera você vem primeiro, com borda rosa e na largura toda da grade, já com a pergunta,
  as opções e a caixa de escrever; depois quem trabalha; depois quem terminou, a mais recente antes. Vale o
  agente mais urgente da sessão, subagente incluso.
- **Cartão:** estado e tempo do turno, projeto, modelo, o contexto em número grande com a barra, o que está
  fazendo (três linhas) e os subagentes vivos em etiquetas. O que trabalha tem destaque: fundo elevado, faixa
  amarela no alto e o círculo girando. Um clique abre a sessão na largura toda da grade.
- **Cabeçalho:** a marca, a versão, uma pílula por estado com a contagem de sessões e o ponto da conexão
  (verde; vermelho quando o servidor some) com a idade do dado.
- **Filtros:** harnesses e modelos abertos, com a contagem por estado; um clique filtra a lista. Só
  aparecem quando há o que escolher (dois harnesses, ou dois modelos, com sessão aberta).
- **Finalizada:** um cartão menor e sem destaque: diz há quanto a sessão terminou, mostra o começo da
  conclusão, o modelo e o contexto, e leva a marca "nova" até você abrir.
- **Subagentes e workflows:** na sessão aberta, uma linha por subagente, com modelo, contexto e tempo. Os
  finalizados se recolhem sozinhos numa linha que abre.
- **Agente aberto:** um clique na linha mostra a última fala inteira (a conclusão, quando o turno
  fechou). Os eventos, o terminal e os arquivos do turno ficam fechados, cada um atrás do seu botão. Cada
  evento se expande: a chamada mostra a descrição, o comando inteiro e a saída (até 4000 caracteres).
- **Arquivos do turno:** os que o agente escreveu no turno atual (`Edit`, `Write`, `MultiEdit` e
  `NotebookEdit` do Claude, o `apply_patch` do Codex, `edit`/`write` do Pi e as edições do agy), do último
  tocado ao primeiro; o clique abre no VS Code (`vscode://file/...`).
- **Falha:** falha de ferramenta quase sempre o agente contorna sozinho, então não marca o card. A última
  do turno atual, do agente principal ou de um subagente, fica no card aberto atrás do botão "Falhas de
  ferramenta", em cinza; some quando o agente começa outro turno. Na atividade recente, o "erro" também é cinza.
- **Uso do plano:** um cartão por limite, que se esticam até ocupar a largura toda, com a porcentagem, a barra na cor do nível (laranja a
  partir de 70%, vermelha a partir de 90%) e a contagem até renovar: o Claude (5 h, semana e a cota
  do Fable) e o Codex (o `rate_limits` do transcript). Os do Claude vêm do `/status` do
  clawd-panel, que os recebe da statusline; sem ele no ar, só aparecem os do Codex.
- **Atividade recente:** os últimos 30 passos das sessões abertas, do mais novo ao mais antigo (chamada
  de ferramenta, fala, pedido, erro), com o filtro de harness ou modelo valendo aqui também; um clique abre
  a sessão. De 1700 px para cima,
  o uso do plano e a atividade ficam numa coluna à direita; abaixo disso, acima e abaixo das sessões.
- **Aba:** o ícone é a logo na cor do estado mais urgente, e o título diz quem espera e quem trabalha, com
  a aba em segundo plano. A sessão que espera você há 5 min faz a aba piscar.
- **Ouvir e avisos:** o botão de alto-falante lê em voz alta a linha, a conclusão ou a saída. O botão
  "Voz" do cabeçalho abre a voz, a velocidade (0,8× a 3×, também no player), o "Testar" e os avisos:
  por voz e por notificação do sistema quando uma sessão termina ou passa a esperar você. A que continua
  esperando repete o aviso a cada 5 min.
- **A palavra lida:** enquanto lê, a palavra atual fica marcada no próprio campo (a conclusão, o "fazendo",
  o evento, a saída) e na frase que o player mostra. A voz do navegador diz a palavra exata (o evento
  `boundary`); a voz local manda só o áudio, e a palavra sai da parte já tocada do trecho, pesada pelo tamanho
  das palavras e pelas pausas da pontuação. Sem `boundary` (as vozes de rede do Chrome), sai do tempo. O
  campo é marcado pela CSS Custom Highlight API (Edge, Chrome e Safari 17.2+); sem ela, fica a frase do player.

## Responder e escrever pelo navegador

Como no painel ESP32: a sessão que roda num pane do herdr ganha, no card, a pergunta que está na tela com as opções, uma caixa para escrever ao agente (uma
linha, Enter envia), `/compact`, `/clear` e "Interromper" (Esc), os dois últimos com confirmação. O
card aberto ainda mostra o terminal (a tela visível, só leitura) e, no Edge e no Chrome, um botão de
ditado (o áudio vai para o serviço de voz do navegador).

Quem age no pane é o clawd-panel (porta 8787, `MONITOR_CLAWD` troca o endereço): o monitor acha o
pane da sessão pelo `GET /panes` e repassa a ação a `POST /responder` e `POST /enviar`; o terminal
vem de `GET /ler`. A resposta a uma opção confere, na tela, se a pergunta ainda é a mesma antes de
apertar Enter. As rotas que digitam só aceitam a página servida pelo próprio servidor: ela traz um
token que muda a cada execução e o manda no cabeçalho `X-Monitor` (outra origem não lê a página, e o
cabeçalho força o preflight que o servidor não atende), e a `Origin`, se vier, tem de ser a do
monitor. O texto é uma linha de até 4000 caracteres. Sessão fora do herdr (Claude Desktop, extensão
do VS Code, `codex exec`) fica só leitura.

**Atalhos** (fora de uma caixa de texto): `j`/`k` andam entre as sessões, `o` ouve, `/` vai para a
caixa de escrever e `1` a `9` põem o foco na opção da pergunta, que o `Enter` confirma.

## Celular (`--rede`)

`python monitor.py --rede` faz o servidor atender também a rede local e imprime o link do celular, com a
chave: `http://<IP da máquina>:8765/?chave=...`. Aberto uma vez, o link grava um cookie (`HttpOnly`,
`SameSite=Strict`, um ano) e tira a chave da barra de endereço; daí em diante, o endereço sem a chave basta.
A chave é gerada na primeira vez e fica em `chave-rede.txt`, na pasta de dados (a mesma das vozes); apagar o
arquivo invalida os celulares já pareados. Pelo Tailscale, use o IP `100.x` da máquina.

- Desta máquina, nada muda: não pede chave.
- De fora, sem o cookie, tudo responde 401. Com ele, o celular vê, ouve, responde e escreve como a página local.
- Só um IP passa no cabeçalho `Host`: nome de host continua recusado, contra o DNS rebinding.
- `parar` só vale desta máquina.
- A conexão é HTTP puro: na rede local ou no Tailscale (que cifra o caminho), não numa rede pública.
- O Windows pergunta, na primeira vez, se o Python pode receber conexões da rede.

Um monitor já no ar sem a rede é trocado quando você pede `--rede`; o contrário não acontece (para tirar a
rede, `parar` e suba de novo).

## Vozes

A leitura usa as vozes do navegador (Web Speech API) e, se estiverem instaladas, vozes locais que o
servidor gera em WAV. No Microsoft Edge aparecem as vozes "Natural" (Francisca, Antônio), as mais
naturais, mas elas mandam o texto aos servidores da Microsoft. As locais não saem da máquina.

As vozes locais ficam fora do repositório, em `%LOCALAPPDATA%\monitor` (Windows) ou
`~/.local/share/monitor`, ou na pasta de `MONITOR_VOZES`:

| Motor | O que instalar | Como soa |
|---|---|---|
| Piper | `piper/piper/piper.exe` ([release do Windows](https://github.com/rhasspy/piper/releases/tag/2023.11.14-2)) e, em `piper/vozes/`, o `.onnx` e o `.onnx.json` de cada voz ([pt_BR](https://huggingface.co/rhasspy/piper-voices/tree/main/pt/pt_BR)) | Rápido (0,1 s por frase), um pouco sintético |
| Kokoro | um venv em `kokoro/venv` com `pip install kokoro-onnx soundfile`, e em `kokoro/` o `kokoro-v1.0.onnx` e o `voices-v1.0.bin` ([model-files-v1.0](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0)). Para a GPU NVIDIA, troque `onnxruntime` por `onnxruntime-gpu[cuda,cudnn]` | Mais natural; vozes Dora, Alex e Santa |

O monitor acha os motores sozinho. O Kokoro fica residente: o `voz_kokoro.py` roda no venv dele,
carrega o modelo uma vez, usa a GPU quando há CUDA (senão a CPU) e sai junto com o monitor; o
`monitor.py` continua só com a biblioteca padrão. A leitura vai por frases: o áudio começa depois da
primeira, as seguintes são geradas em fila enquanto ela toca, e as últimas 64 falas ficam em cache.

## Testar

```bash
python teste_monitor.py
```

O teste monta transcripts de exemplo dos quatro harnesses num diretório temporário, sobe o servidor
numa porta livre e confere o que a API devolve. Ele nunca lê os seus transcripts reais.

```bash
pip install playwright && python -m playwright install chromium   # uma vez
python teste_tela.py
```

O teste da tela é opcional (sem o Playwright, ele avisa e sai 0). Ele sobe o monitor com uma home vazia,
troca o `/api/estado` e o `/api/eventos` por um estado montado e confere, em 1400, 720 e 390 px:

- a grade e o cabeçalho;
- as gavetas de falhas e de arquivos;
- o filtro da atividade e o favicon;
- o alinhamento da palavra lida com o texto da tela.
