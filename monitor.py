#!/usr/bin/env python3
"""Monitor: painel local do que os agentes de IA estao fazendo, em todos os harnesses.

  python3 monitor.py [--porta 8765] [--sem-navegador]   sobe (ou reaproveita) o servidor e abre o painel
  python3 monitor.py parar [--porta 8765]               derruba o servidor
  python3 monitor.py servir [--porta 8765]              o processo do servidor, em primeiro plano
  python3 monitor.py --rede                             tambem atende a rede local, com chave (o celular)
  python3 monitor.py iniciar-no-login [--rede]          sobe o painel quando voce entra no Windows
  python3 monitor.py nao-iniciar-no-login               desfaz o anterior

Le em disco, sem hook e sem pedir nada ao modelo, os transcripts que Claude Code,
Codex, Antigravity CLI (agy) e Pi ja gravam, e serve monitor.html e a API em
http://127.0.0.1:<porta>/. So biblioteca padrao, Python 3.9+. A saida no console
e so ASCII: o console cp1252 do Windows derruba o print de qualquer outra coisa.
"""
import argparse
import hmac
import http.client
import json
import os
import re
import secrets
import shutil
import socket
import sqlite3
import subprocess
import sys
import tempfile
import threading
import time
import traceback
import webbrowser
from collections import OrderedDict, deque
from datetime import datetime, timezone
from http.cookies import CookieError, SimpleCookie
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, quote, unquote, urlsplit

VERSAO = "1.2"
PORTA = 8765
PARADA_S = 120          # turno aberto sem evento ha mais que isso, e sem ferramenta rodando: travou, esperando voce
ENCERRADA_S = 30 * 60   # harness sem sinal de vida: turno fechado ha mais que isso = sessao encerrada
ENCERRADA_SEM_INTERACAO_S = 2 * 60  # codex exec e agy -p: o processo acaba com o turno, ninguem espera resposta
VARREDURA_S = 24 * 3600  # so le o transcript mudado ha menos que isso (a sessao viva do Claude e lida sempre)
MAX_EVENTOS = 150       # eventos guardados por agente
LIMITE_COMPLETO = 4000  # caracteres guardados de cada evento para a linha expandida (comando, saida, fala)
EVENTOS_API = 50        # eventos devolvidos por /api/eventos
HOME = Path.home()
AQUI = Path(__file__).resolve().parent
# Vozes locais opcionais (Piper e Kokoro), fora do repositorio: MONITOR_VOZES ou a pasta de dados do usuario.
VOZES = Path(os.environ.get("MONITOR_VOZES") or (Path(os.environ["LOCALAPPDATA"]) / "monitor" if os.environ.get("LOCALAPPDATA")
                                                 else HOME / ".local" / "share" / "monitor"))
KOKORO_VOZES = {"pf_dora": "Dora", "pm_alex": "Alex", "pm_santa": "Santa"}  # as vozes pt-BR do Kokoro-82M v1.0
LIMITE_FALA = 5000      # caracteres por pedido de /api/falar
# ponytail: uma trava para a varredura inteira; /api/eventos espera a coleta em curso (1,6 s na primeira
# leitura dos arquivos grandes, 0,3 s nas seguintes). Trava por arquivo se essa espera incomodar.
TRAVA = threading.Lock()
# ponytail: CACHE e INDICE nunca podam: todo arquivo lido desde que o servidor subiu fica, com ate
# MAX_EVENTOS eventos. Podar as chaves que a ultima varredura nao tocou se o servidor ficar semanas de pe.
CACHE = {}    # caminho -> Leitura: offset, linha parcial e o agente acumulado do arquivo
INDICE = {}   # id do agente -> Agente, para /api/eventos
# A API do clawd-panel (o painel ESP32): limites de uso do Claude e a mao que age nos panes do herdr.
CLAWD = os.environ.get("MONITOR_CLAWD") or "http://127.0.0.1:8787"
LIMITES_CODEX = {}  # o rate_limits mais novo que um token_count do Codex gravou: {'ts', 'rate'}


def diz(msg):
    print(msg.encode("ascii", "backslashreplace").decode("ascii"), flush=True)


# ---------------------------------------------------------------- tempo e texto

def seg(ts):
    """Epoch em segundos de um ISO 8601 ('...Z') ou de um numero em s ou ms."""
    if not ts:
        return 0.0
    if isinstance(ts, (int, float)):
        return ts / 1000.0 if ts > 1e11 else float(ts)
    m = re.match(r"(\d{4}-\d\d-\d\dT\d\d:\d\d:\d\d)(\.\d+)?", str(ts))
    if not m:
        return 0.0
    base = datetime.strptime(m.group(1), "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp()
    return base + float(m.group(2) or 0)


def iso(s):
    return datetime.fromtimestamp(s, timezone.utc).isoformat().replace("+00:00", "Z") if s else None


def texto_de(valor):
    if isinstance(valor, list):
        return " ".join(texto_de(i) for i in valor)
    if isinstance(valor, dict):
        return texto_de(valor.get("text") or valor.get("content") or "")
    return "" if valor is None else str(valor)


def curto(valor, n=200):
    s = valor if isinstance(valor, str) else json.dumps(valor, ensure_ascii=False)
    return re.sub(r"\s+", " ", s).strip()[:n]


def resumo_args(args):
    if isinstance(args, dict):
        perguntas = args.get("questions")  # AskUserQuestion: a pergunta, nao o JSON dela
        if isinstance(perguntas, list) and perguntas and isinstance(perguntas[0], dict) and perguntas[0].get("question"):
            return curto(perguntas[0]["question"], 160)
        for k in ("command", "CommandLine", "cmd", "file_path", "path", "pattern", "description",
                  "query", "url", "prompt", "toolSummary"):
            if args.get(k):
                return curto(args[k], 160)
    return curto(args if isinstance(args, str) else args or "", 160)


def completo_args(args):
    """A chamada inteira para a linha expandida: o comando, se houver; senao os argumentos em JSON."""
    if isinstance(args, dict):
        for k in ("command", "CommandLine", "cmd"):
            if isinstance(args.get(k), str):
                return args[k]
        return json.dumps(args, ensure_ascii=False, indent=2)
    return texto_de(args)


# As ferramentas que escrevem arquivo, nos quatro harnesses: o caminho vai para a lista "arquivos do turno" do card.
ESCRITA = {"Edit", "Write", "MultiEdit", "NotebookEdit",                          # Claude Code
           "apply_patch",                                                        # Codex
           "edit", "write",                                                      # Pi
           "write_to_file", "replace_file_content", "multi_replace_file_content"}  # agy
PATCH_ARQUIVO = re.compile(r"^\*\*\* (?:Add|Update|Delete) File: (.+?)\s*$", re.M)


def arquivos_escritos(nome, args):
    """Os caminhos que a chamada escreve: o argumento de caminho da ferramenta de edicao, ou os do patch do Codex."""
    if nome not in ESCRITA:
        return []
    if isinstance(args, dict) and isinstance(args.get("input"), str):
        args = args["input"]  # apply_patch chamado como function_call: {"input": "*** Begin Patch ..."}
    if isinstance(args, str):
        return PATCH_ARQUIVO.findall(args)
    for k in ("file_path", "notebook_path", "path", "TargetFile"):
        v = args.get(k) if isinstance(args, dict) else None
        if isinstance(v, str) and v.strip():
            if v.startswith('"'):  # o agy grava o caminho como string JSON dentro da string
                try:
                    v = json.loads(v)
                except ValueError:
                    v = v.strip('"')
            return [v]
    return []


def primeira_linha(texto):
    """A 1a linha nao vazia de uma fala do modelo, sem a marcacao de markdown."""
    linha = next((l for l in texto.splitlines() if l.strip()), "")
    linha = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", linha)  # [texto](link): fica o texto
    return curto(re.sub(r"\*|`|^\s*[#>-]+\s+", "", linha), 200)


def sem_prefixo(caminho):
    """Tira o prefixo de caminho longo do Windows (\\\\?\\) que o Codex grava."""
    return caminho[4:] if caminho.startswith("\\\\?\\") else caminho


def pasta(cwd):
    """Nome da pasta de um caminho de qualquer sistema (o servidor pode ler caminho Windows no Linux)."""
    if not cwd:
        return "?"
    return re.split(r"[\\/]", sem_prefixo(cwd).rstrip("\\/"))[-1] or cwd


# ---------------------------------------------------------------- agente

class Agente:
    """O estado de um agente, acumulado evento a evento a partir do transcript dele."""

    def __init__(self):
        self.modelo = self.titulo = self.cwd = self.prompt = self.tokens = self.plano = None
        self.janela = None     # janela de contexto em tokens, quando o transcript a grava (Codex)
        self.inicio = self.ultimo = self.turno_inicio = 0.0
        self.aberto = False
        self.pendentes = {}    # id da chamada -> {'nome', 'resumo'}: ferramenta ainda sem resultado
        self.fala = None       # a ultima fala inteira do modelo no turno: o que ele diz que faz, ou a conclusao
        self.descricao = None  # a descricao humana da ultima chamada (description, toolSummary), nunca o comando
        self.pergunta = None   # id da pergunta ao usuario ainda sem resposta
        self.falha = None      # ultima falha de ferramenta: {'texto', 'ts'}
        self.esforco = None    # o esforco de raciocinio do ultimo turno (low, medium, high, xhigh, max)
        self.terminado = False
        self.encerra_s = ENCERRADA_S  # sem sinal de vida: turno fechado ha mais que isso = sessao encerrada
        self.eventos = deque(maxlen=MAX_EVENTOS)

    def toca(self, t):
        if t:
            self.inicio = self.inicio or t
            self.ultimo = max(self.ultimo, t)

    def evento(self, t, tipo, nome, texto, completo=None, **extra):
        """texto: a linha curta; completo: o que a linha expandida mostra (padrao: o texto inteiro)."""
        t = t or self.ultimo
        self.toca(t)
        s = texto_de(texto)
        completo = s if completo is None else completo
        self.eventos.append(dict(extra, ts=t, tipo=tipo, nome=nome, texto=curto(s), completo=completo[:LIMITE_COMPLETO]))

    def abre(self, t, prompt=None):
        """Prompt humano abre um turno novo; sem prompt (notificacao, mensagem) so reabre o fechado."""
        if prompt is not None:
            self.prompt = prompt
            self.fala = self.descricao = None
            self.evento(t, "usuario", "prompt", prompt)
        if prompt is not None or not self.aberto:
            self.aberto, self.turno_inicio = True, t or self.ultimo

    def fecha(self, t=0):
        self.toca(t)
        self.aberto = False
        self.pendentes.clear()

    def texto(self, t, texto):
        if texto and texto.strip():
            self.fala = texto.strip()
            self.evento(t, "texto", "texto", texto)

    def chama(self, t, chave, nome, args, pendente=True, descricao=None, arquivos=None):
        """pendente=False: chamada que nao tem resultado proprio no transcript (a busca web do Codex). arquivos: os
        caminhos que ela escreve, quando `args` nao os traz (o agy passa so o toolSummary)."""
        passo = {"nome": nome or "?", "resumo": resumo_args(args)}
        if pendente:
            self.pendentes[chave or len(self.pendentes)] = passo
        descricao = descricao or (args.get("description") if isinstance(args, dict) else None)
        self.descricao = curto(descricao, 160) if descricao else None
        self.evento(t, "ferramenta", passo["nome"], passo["resumo"], completo_args(args), id=chave,
                    descricao=self.descricao, arquivos=arquivos if arquivos is not None else arquivos_escritos(nome, args))

    def resultado(self, t, chave, conteudo, erro=False, nome="resultado"):
        if chave is None:
            self.pendentes.clear()
        else:
            self.pendentes.pop(chave, None)
        if chave is not None and chave == self.pergunta:
            self.pergunta = None
        self.evento(t, "erro" if erro else "resultado", nome, conteudo, id=chave)  # o id casa com a chamada
        if erro:
            self.falha = {"texto": self.eventos[-1]["texto"], "ts": self.eventos[-1]["ts"]}

    def planeja(self, itens):
        """Plano como [(texto, status do harness)]; completed e in_progress valem nos dois que o registram."""
        estados = {"completed": "feito", "in_progress": "fazendo"}
        self.plano = [{"texto": curto(texto or "", 160), "estado": estados.get(st, "pendente")} for texto, st in itens]


def arquivos_do_turno(a):
    """Os arquivos que o agente escreveu no turno atual, do ultimo tocado ao primeiro, com o caminho absoluto (o
    relativo e o do patch do Codex, contra a pasta da sessao). ponytail: sai do que cabe em MAX_EVENTOS; num turno
    muito longo, a escrita mais antiga some da lista. Guardar os caminhos no Agente se isso fizer falta."""
    vistos = {}
    for e in a.eventos:
        if e["ts"] >= (a.turno_inicio or a.inicio):
            for c in e.get("arquivos") or []:
                caminho = os.path.normpath(os.path.join(a.cwd, c) if a.cwd and not os.path.isabs(c) else c)
                vistos.pop(caminho, None)
                vistos[caminho] = True
    return list(reversed(vistos))


def status(a, agora, vivo=None, filho=False):
    """Os estados do herdr: trabalhando; esperando voce (pergunta, permissao ou turno travado); finalizado (turno
    fechado); e, so na sessao, encerrada. vivo: None = harness sem sinal de vida; 'morta' = sessao sem processo;
    senao o status da sessao viva (busy, idle ou waiting)."""
    if a.terminado:
        return "finalizado"
    if vivo == "morta":
        return "encerrada"
    if a.pergunta or vivo == "waiting":
        return "esperando"
    if vivo == "busy":
        return "trabalhando"
    if vivo == "idle" or not a.aberto:
        if not filho and vivo is None and agora - a.ultimo > a.encerra_s:
            return "encerrada"
        return "finalizado"
    if a.pendentes or agora - a.ultimo < PARADA_S:
        return "trabalhando"
    return "esperando"  # turno aberto e mudo, sem ferramenta rodando: travou


def fazendo(a, st):
    """O que o agente faz, no nivel macro: a pergunta que ele espera, senao a ultima fala dele no turno, senao a
    descricao da ultima chamada. O comando fica so nos eventos."""
    if st == "esperando" and a.pergunta in a.pendentes:
        return a.pendentes[a.pergunta]["resumo"]
    return (primeira_linha(a.fala) if a.fala else None) or a.descricao


CONFIG_CLAUDE = {}  # mtime do ~/.claude/settings.json -> familia do modelo pedido com [1m] ("opus"), ou ""
JANELAS = (200000, 1000000)  # as janelas de contexto do Claude
# Modelo do Claude -> a janela que a statusline mostrou para ele: o Claude Code diz a % de contexto de cada sessao, e
# tokens / % da a janela de verdade (o Opus 5.5 ja vem com 1M, sem o [1m] no nome nem no settings.json). Vale para os
# subagentes do mesmo modelo e para quando o clawd-panel sai do ar, enquanto o servidor estiver de pe.
JANELA_MODELO = {}


def janela_claude(modelo, tokens=None):
    """Janela de contexto de um modelo Claude; None para os outros harnesses. Na ordem: a que a statusline mostrou
    para o modelo (JANELA_MODELO); 1M quando os tokens ja passam de 200 mil, que nao caberiam; 1M quando o model do
    settings.json pede [1m] na mesma familia; senao 200 mil. ponytail: o transcript nao grava a janela; ler dele se o
    Claude Code passar a grava-la."""
    if not modelo or not modelo.startswith("claude-"):
        return None
    if modelo in JANELA_MODELO:
        return JANELA_MODELO[modelo]
    if tokens and tokens > JANELAS[0]:
        return JANELAS[1]
    f = HOME / ".claude" / "settings.json"
    try:
        mtime = f.stat().st_mtime
        if mtime not in CONFIG_CLAUDE:
            padrao = str(json.loads(f.read_text("utf-8")).get("model") or "")
            CONFIG_CLAUDE.clear()
            CONFIG_CLAUDE[mtime] = padrao.split("[")[0] if "[1m]" in padrao else ""
        familia = CONFIG_CLAUDE[mtime]
    except (OSError, ValueError, AttributeError):
        familia = ""
    return 1000000 if familia and familia in modelo else 200000


def resumo(a, id_, agora, vivo=None, filho=False, nome=None, funcao=None, pergunta=False):
    INDICE[id_] = a
    ferramenta_atual = list(a.pendentes.values())[-1] if a.pendentes and a.aberto else None
    plano = None
    if a.plano:
        plano = {"itens": a.plano, "proximo": next((i["texto"] for i in a.plano if i["estado"] != "feito"), None)}
    st = status(a, agora, vivo, filho)
    falha = a.falha if a.falha and a.falha["ts"] >= (a.turno_inicio or a.inicio) else None  # so a do turno atual
    return {"id": id_, "nome": nome, "funcao": funcao, "modelo": a.modelo, "status": st,
            "inicio": iso(a.inicio), "ultimo": iso(a.ultimo), "turno_inicio": iso(a.turno_inicio or a.inicio),
            "prompt": a.prompt, "agora": ferramenta_atual, "fazendo": fazendo(a, st),
            "pergunta": bool(a.pergunta or pergunta), "tokens": a.tokens, "janela": a.janela or janela_claude(a.modelo, a.tokens),
            "plano": plano, "falha": falha and {"texto": falha["texto"], "ts": iso(falha["ts"])}, "esforco": a.esforco}


def sessao(a, harness, id_, agora, vivo=None, titulo=None, pergunta=False):
    s = resumo(a, "%s:%s" % (harness, id_), agora, vivo, nome=pasta(a.cwd), pergunta=pergunta)
    s.update(harness=harness, projeto=pasta(a.cwd), titulo=titulo or a.titulo,
             subagentes=[], workflows=[])
    return s


def raiz(tid, pais, lidos):
    """A sessao de topo de um subagente: sobe pelas maes enquanto elas foram lidas nesta janela."""
    vistos = set()
    while pais.get(tid) in lidos and tid not in vistos:
        vistos.add(tid)
        tid = pais[tid]
    return tid


def arvore(harness, agentes, pais, agora):
    """As sessoes de topo com os subagentes embaixo; o neto sobe ate a sessao de topo lida na janela.
    agentes: id -> (Agente, nome, funcao do subagente); pais: id -> id da mae."""
    sessoes = {i: sessao(a, harness, i, agora) for i, (a, _, _) in agentes.items() if raiz(i, pais, agentes) == i}
    for i, (a, nome, funcao) in agentes.items():
        mae = raiz(i, pais, agentes)
        if mae == i:
            continue
        d = resumo(a, "%s:%s:%s" % (harness, mae, i), agora, filho=True, nome=nome, funcao=funcao)
        if recente(d, agora):
            sessoes[mae]["subagentes"].append(d)
    return list(sessoes.values())


# ---------------------------------------------------------------- leitura incremental

class Leitura:
    def __init__(self):
        self.pos, self.resto, self.agente, self.extra = 0, b"", Agente(), {}


def novas(caminho):
    """Registros novos do arquivo desde a ultima leitura; a linha parcial do fim espera a proxima."""
    chave = str(caminho)
    c = CACHE.get(chave)
    try:
        tamanho = os.path.getsize(chave)
        if c is None or tamanho < c.pos:
            c = CACHE[chave] = Leitura()
        if tamanho == c.pos:
            return c, []
        with open(chave, "rb") as f:
            f.seek(c.pos)
            bloco = f.read(tamanho - c.pos)
    except OSError:
        return c or Leitura(), []
    c.pos += len(bloco)
    bloco = c.resto + bloco
    fim = bloco.rfind(b"\n") + 1
    c.resto = bloco[fim:]
    registros = []
    for linha in bloco[:fim].splitlines():
        try:
            r = json.loads(linha)
        except ValueError:
            continue  # rollout com bytes nulos, linha corrompida
        if isinstance(r, dict):
            registros.append(r)
    return c, registros


def mudou_desde(caminho, limite):
    """O arquivo mudou depois de `limite` (epoch); arquivo sumido conta como velho."""
    try:
        return caminho.stat().st_mtime >= limite
    except OSError:
        return False


def consulta(db, sql, parametros=()):
    """Linhas (dicts) de um SQLite aberto so para leitura; banco ausente, trancado ou de outra versao = nenhuma."""
    try:
        con = sqlite3.connect(db.as_uri() + "?mode=ro", uri=True, timeout=0.5)
        con.row_factory = sqlite3.Row
        try:
            return [dict(r) for r in con.execute(sql, parametros)]
        finally:
            con.close()
    except sqlite3.Error:
        return []


# ---------------------------------------------------------------- Claude Code

def prompt_claude(texto):
    """O texto digitado pelo usuario, ou None quando o registro user nao e um prompt humano."""
    s = re.sub(r"</?pasted_content[^>]*>", "", texto).strip()  # texto colado e prompt; a marcacao sai
    if s.startswith(("[Request interrupted", "Another Claude session sent a message", "The summarized conversation")):
        return None
    if s.startswith("<command-"):
        nome = re.search(r"<command-name>\s*(.*?)\s*</command-name>", s, re.S)
        args = re.search(r"<command-args>(.*?)</command-args>", s, re.S)
        if nome:
            return (nome.group(1) + " " + (args.group(1).strip() if args else "")).strip()
    if s.startswith("<"):
        return None  # notificacao, mensagem de outra sessao, resumo de compactacao, saida de comando local
    return s


def claude_registro(a, r, extra):
    t, tipo = seg(r.get("timestamp")), r.get("type")
    if r.get("entrypoint") and "entrypoint" not in extra:
        extra["entrypoint"] = r["entrypoint"]
    if r.get("cwd") and not a.cwd:
        a.cwd = r["cwd"]  # o cwd de abertura; um cd depois nao muda o projeto
    esforco = r.get("perTurnEffort") or r.get("effort")  # cada registro grava o do turno em que esta
    if isinstance(esforco, str) and esforco:
        a.esforco = esforco
    if tipo == "custom-title":
        extra["custom"] = r.get("customTitle")
    elif tipo == "ai-title":
        extra["ai"] = r.get("aiTitle")
    elif tipo == "system" and r.get("subtype") == "turn_duration":
        a.fecha(t)
    elif tipo == "queue-operation":
        claude_notificacao(r.get("content"), extra)
    elif tipo == "user":
        claude_usuario(a, r, t, extra)
    elif tipo == "assistant":
        claude_assistente(a, r, t, extra)


def claude_notificacao(texto, extra):
    """<task-notification> de subagente (qualquer status) o da por terminado; o task-id e o agentId."""
    if isinstance(texto, str) and "<task-notification>" in texto:
        extra.setdefault("fim", set()).update(re.findall(r"<task-id>\s*(.*?)\s*</task-id>", texto))


def claude_usuario(a, r, t, extra):
    resultado_ferramenta = r.get("toolUseResult")
    if isinstance(resultado_ferramenta, dict) and resultado_ferramenta.get("runId"):
        extra.setdefault("workflows", {})[resultado_ferramenta["runId"]] = resultado_ferramenta.get("workflowName")
    if r.get("isMeta") or r.get("isCompactSummary"):
        return
    c = (r.get("message") or {}).get("content")
    textos = []
    if isinstance(c, list):
        for b in c:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "tool_result":
                a.resultado(t, b.get("tool_use_id"), b.get("content"), bool(b.get("is_error")))
            elif b.get("type") == "text":
                textos.append(b.get("text") or "")
    elif isinstance(c, str):
        textos.append(c)
    texto = "\n".join(textos)
    if not texto.strip():
        return
    p = prompt_claude(texto)
    if p is not None:
        a.abre(t, p)
    elif texto.lstrip().startswith("[Request interrupted"):
        a.evento(t, "usuario", "interrompido", texto)
        a.fecha(t)
    elif texto.lstrip().startswith(("<local-command-stdout>", "<bash-stdout>")):
        a.evento(t, "resultado", "comando local", texto)
        a.fecha(t)  # comando local (/model, !ls) nao chama o modelo: a saida dele fecha o turno
    else:
        claude_notificacao(texto, extra)
        a.abre(t)  # notificacao ou mensagem de outra sessao: o agente volta a trabalhar, o prompt fica
        a.evento(t, "mensagem", "mensagem", texto)


def claude_assistente(a, r, t, extra):
    m = r.get("message") or {}
    if m.get("model") == "<synthetic>":
        return  # erro de API e afins, gerados pelo proprio harness
    a.modelo = m.get("model") or a.modelo
    u = m.get("usage") or {}
    total = sum(u.get(k) or 0 for k in ("input_tokens", "cache_read_input_tokens", "cache_creation_input_tokens"))
    a.tokens = total or a.tokens  # a ultima resposta; os blocos que repetem o message.id repetem o usage
    for b in m.get("content") or []:
        if not isinstance(b, dict):
            continue
        if b.get("type") == "text":
            a.texto(t, b.get("text"))
        elif b.get("type") == "tool_use":
            a.chama(t, b.get("id"), b.get("name"), b.get("input"))
            entrada = b.get("input") if isinstance(b.get("input"), dict) else {}
            if b.get("name") == "TaskStop":
                extra.setdefault("fim", set()).add(entrada.get("task_id"))
            elif b.get("name") == "AskUserQuestion":
                a.pergunta = b.get("id")
            elif b.get("name") == "TodoWrite":
                a.planeja([(i.get("content"), i.get("status")) for i in entrada.get("todos") or []
                           if isinstance(i, dict)])
    if m.get("stop_reason") == "end_turn":
        a.fecha(t)


def recente(d, agora):
    """Subagente ou agente de workflow finalizado ha mais de 30 min sai da tabela da sessao."""
    return d["status"] != "finalizado" or agora - seg(d["ultimo"]) < ENCERRADA_S


def claude_filho(g, id_, agora, fim, nome=None, funcao=None, acabou=False):
    """Um subagente (ou agente de workflow): transcript agent-<agentId>.jsonl e o .meta.json ao lado."""
    c, registros = novas(g)
    a = c.agente
    for r in registros:
        claude_registro(a, r, c.extra)
    if "meta" not in c.extra:
        try:
            c.extra["meta"] = json.loads(g.with_name(g.name[:-len(".jsonl")] + ".meta.json").read_text("utf-8"))
        except (OSError, ValueError):
            pass  # o meta pode ainda nao existir; tenta de novo na proxima consulta
    meta = c.extra.get("meta") or {}
    a.modelo = a.modelo or meta.get("model")
    if acabou or g.stem[len("agent-"):] in fim or (meta.get("name") and meta["name"] in fim):
        a.terminado = True
    return resumo(a, id_, agora, filho=True, nome=nome or meta.get("name") or meta.get("agentType") or "subagente",
                  funcao=funcao or meta.get("workflowPhase") or meta.get("description"))


def claude_workflow(w, pasta_sessao, sid, agora, extra):
    """Uma execucao de Workflow: os agentes da pasta do run, com rotulo e fase pelo journal.jsonl."""
    fim_run = pasta_sessao / "workflows" / (w.name + ".json")
    acabou = fim_run.exists()
    if acabou:
        try:
            if agora - fim_run.stat().st_mtime > ENCERRADA_S:
                return None
        except OSError:
            return None
        nomes = extra.setdefault("workflows", {})
        if not nomes.get(w.name):
            try:
                nomes[w.name] = json.loads(fim_run.read_text("utf-8")).get("workflowName")
            except (OSError, ValueError):
                pass
    j, registros = novas(w / "journal.jsonl")
    rotulos, feitos = j.extra.setdefault("rotulos", {}), j.extra.setdefault("feitos", set())
    for r in registros:
        if r.get("type") == "started":
            rotulos[r.get("agentId")] = (r.get("label"), r.get("phase"))
            j.extra["fase"] = r.get("phase")
        elif r.get("type") == "result":
            feitos.add(r.get("agentId"))
    agentes = []
    for g in sorted(w.glob("agent-*.jsonl")):
        aid = g.stem[len("agent-"):]
        rotulo, fase = rotulos.get(aid, (None, None))
        d = claude_filho(g, "claude-code:%s:%s:%s" % (sid, w.name, aid), agora, feitos, rotulo, fase,
                         acabou or aid in feitos)
        if recente(d, agora):
            agentes.append(d)
    return {"id": w.name, "nome": extra.get("workflows", {}).get(w.name) or w.name,
            "fase": "concluído" if acabou else j.extra.get("fase") or "?", "agentes": agentes}


def processo_vivo(pid, criacao=None):
    """Se o PID do arquivo de sessao viva ainda e o processo que o gravou.

    Sessao morta a forca deixa o arquivo para tras. No Windows a pergunta vai a OpenProcess e GetProcessTimes,
    e o horario de criacao (FILETIME) tem de bater com o procStart do arquivo, contra PID reaproveitado; ali
    os.kill(pid, 0) mataria o processo. Nos outros sistemas, os.kill(pid, 0) so pergunta.
    """
    try:
        pid = int(pid)
    except (TypeError, ValueError):
        return True  # arquivo sem pid: confia na presenca dele
    if os.name != "nt":
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return False
        except OSError:
            return True  # PermissionError: existe, de outro usuario
        return True
    import ctypes
    from ctypes import wintypes
    k = ctypes.WinDLL("kernel32", use_last_error=True)
    k.OpenProcess.restype = wintypes.HANDLE
    k.OpenProcess.argtypes = (wintypes.DWORD, wintypes.BOOL, wintypes.DWORD)
    k.GetExitCodeProcess.argtypes = (wintypes.HANDLE, ctypes.POINTER(wintypes.DWORD))
    k.GetProcessTimes.argtypes = (wintypes.HANDLE,) + (ctypes.POINTER(wintypes.FILETIME),) * 4
    k.CloseHandle.argtypes = (wintypes.HANDLE,)
    h = k.OpenProcess(0x1000, False, pid)  # PROCESS_QUERY_LIMITED_INFORMATION
    if not h:
        return ctypes.get_last_error() == 5  # acesso negado: existe; qualquer outro erro: nao existe
    try:
        codigo = wintypes.DWORD()
        if not k.GetExitCodeProcess(h, ctypes.byref(codigo)) or codigo.value != 259:  # 259 = STILL_ACTIVE
            return False
        tempos = [wintypes.FILETIME() for _ in range(4)]
        if criacao and k.GetProcessTimes(h, *(ctypes.byref(x) for x in tempos)):
            nasceu = (tempos[0].dwHighDateTime << 32) | tempos[0].dwLowDateTime
            try:
                return abs(nasceu - int(criacao)) < 10000000  # 1 s, em unidades de 100 ns
            except ValueError:
                return True
        return True
    finally:
        k.CloseHandle(h)


def claude(agora, limite):
    base = HOME / ".claude"
    vivas = {}
    for f in (base / "sessions").glob("*.json"):
        try:
            d = json.loads(f.read_text("utf-8"))
            if processo_vivo(d.get("pid"), d.get("procStart")):
                vivas[d["sessionId"]] = d
        except (OSError, ValueError, KeyError, TypeError):
            pass
    out = []
    # ponytail: stat em todo projects/*/*.jsonl a cada consulta, dentro ou fora da janela (20 ms em 305 arquivos,
    # linear). Passando de milhares, reler so as sessoes ja na janela e varrer a pasta a cada N consultas.
    for f in (base / "projects").glob("*/*.jsonl"):
        sid = f.stem
        if sid not in vivas and not mudou_desde(f, limite):
            continue
        c, registros = novas(f)
        a, extra = c.agente, c.extra
        for r in registros:
            claude_registro(a, r, extra)
        v = vivas.get(sid)
        if v:
            # O PID do arquivo nunca e sondado com os.kill: no Windows, os.kill(pid, 0) mata o processo.
            vivo = v.get("status") or "idle"
        else:
            vivo = "morta" if extra.get("entrypoint") == "cli" else None  # desktop e SDK nao gravam sessao viva
        titulo = extra.get("custom") or extra.get("ai") or (v or {}).get("name")
        s = sessao(a, "claude-code", sid, agora, vivo, titulo, pergunta=bool((v or {}).get("waitingFor")))
        pasta_sessao, fim = f.with_suffix(""), extra.get("fim", set())
        subs = [claude_filho(g, "claude-code:%s:%s" % (sid, g.stem[len("agent-"):]), agora, fim)
                for g in sorted((pasta_sessao / "subagents").glob("agent-*.jsonl"))]
        s["subagentes"] = [d for d in subs if recente(d, agora)]
        workflows = (claude_workflow(w, pasta_sessao, sid, agora, extra)
                     for w in sorted((pasta_sessao / "subagents" / "workflows").glob("wf_*")))
        s["workflows"] = [w for w in workflows if w]
        out.append(s)
    lidas = {s["id"].split(":", 1)[1] for s in out}
    for sid, v in vivas.items():
        if sid in lidas:
            continue
        # Sessao recem-aberta: o transcript so nasce no primeiro prompt, mas o arquivo de sessao viva ja existe.
        a = Agente()
        a.cwd = v.get("cwd")
        a.toca(seg(v.get("startedAt")))
        out.append(sessao(a, "claude-code", sid, agora, v.get("status") or "idle", v.get("name"),
                          pergunta=bool(v.get("waitingFor"))))
    return out


# ---------------------------------------------------------------- Codex

def codex_falhou(saida):
    """O Codex marca a falha pelo codigo de saida: 'Exit code: N' no texto, ou metadata.exit_code no JSON."""
    m = re.search(r"Exit code:\s*(-?\d+)", saida) or re.search(r'"exit_code"\s*:\s*(-?\d+)', saida)
    return bool(m and int(m.group(1)) != 0)


def codex_registro(a, r):
    t, tipo = seg(r.get("timestamp")), r.get("type")
    p = r.get("payload") or {}
    tipo_payload = p.get("type")
    if tipo == "session_meta":
        a.cwd = a.cwd or p.get("cwd")
    elif tipo == "turn_context":
        a.modelo = p.get("model") or a.modelo
        a.esforco = p.get("effort") or a.esforco
        a.cwd = a.cwd or p.get("cwd")
    elif tipo == "event_msg":
        if tipo_payload == "task_started":
            a.abre(t)
        elif tipo_payload in ("task_complete", "turn_aborted"):
            erro = p.get("error") if isinstance(p.get("error"), dict) else {}
            if erro.get("message"):
                a.resultado(t, None, erro["message"], True, "erro")  # o turno acabou num erro (limite de uso, API)
            a.fecha(t)
        elif tipo_payload == "token_count":
            info = p.get("info") or {}
            a.tokens = (info.get("last_token_usage") or {}).get("input_tokens") or a.tokens
            a.janela = info.get("model_context_window") or a.janela
            if isinstance(p.get("rate_limits"), dict) and t >= LIMITES_CODEX.get("ts", 0):
                LIMITES_CODEX.update(ts=t, rate=p["rate_limits"])
    elif tipo != "response_item":
        return
    elif tipo_payload == "message":
        texto = texto_de(p.get("content")).strip()
        if p.get("role") == "assistant":
            a.texto(t, texto)
        elif p.get("role") == "user" and texto and not texto.startswith(("<", "# AGENTS.md instructions")):
            a.abre(t, texto)  # '<' e o AGENTS.md sao contexto injetado pelo Codex, nao prompt
    elif tipo_payload == "function_call":
        try:
            args = json.loads(p.get("arguments") or "{}")
        except ValueError:
            args = p.get("arguments")
        a.chama(t, p.get("call_id"), p.get("name"), args)
        if p.get("name") == "update_plan" and isinstance(args, dict):
            a.planeja([(s.get("step"), s.get("status")) for s in args.get("plan") or [] if isinstance(s, dict)])
    elif tipo_payload == "custom_tool_call":
        a.chama(t, p.get("call_id"), p.get("name"), p.get("input"))
    elif tipo_payload == "web_search_call":
        a.chama(t, None, "web_search", (p.get("action") or {}).get("query"), pendente=False)
    elif tipo_payload in ("function_call_output", "custom_tool_call_output"):
        saida = texto_de(p.get("output"))
        a.resultado(t, p.get("call_id"), saida, codex_falhou(saida))


def codex(agora, limite):
    db = HOME / ".codex" / "state_5.sqlite"
    threads = consulta(db, "select * from threads where updated_at_ms > ? and archived = 0", (limite * 1000,))
    pais = {r["child_thread_id"]: r["parent_thread_id"]
            for r in consulta(db, "select parent_thread_id, child_thread_id from thread_spawn_edges")}
    agentes = {}
    for th in threads:
        # A janela e a atividade vem de updated_at_ms: no Windows o mtime do rollout atrasa ate 47 min.
        c, registros = novas(sem_prefixo(th.get("rollout_path") or ""))
        a = c.agente
        for r in registros:
            codex_registro(a, r)
        a.titulo, a.modelo, a.cwd = th.get("title") or a.titulo, a.modelo or th.get("model"), a.cwd or th.get("cwd")
        if th.get("source") == "exec":
            a.encerra_s = ENCERRADA_SEM_INTERACAO_S
        agentes[th["id"]] = (a, th.get("agent_nickname") or "subagente",
                             th.get("agent_role") or curto(a.prompt or "", 80))
    return arvore("codex", agentes, pais, agora)


# ---------------------------------------------------------------- Pi

def pi_registro(a, r, extra):
    tipo, m = r.get("type"), r.get("message") or {}
    t = seg(r.get("timestamp") or m.get("timestamp"))
    if tipo == "session":
        a.cwd = a.cwd or r.get("cwd")
        extra["id"], extra["pai"] = r.get("id"), r.get("parentSession")
    elif tipo == "model_change":
        a.modelo = r.get("modelId") or a.modelo
    elif tipo == "session_info":
        a.titulo = r.get("name") or a.titulo
    elif tipo != "message":
        return
    elif m.get("role") == "user":
        texto = texto_de(m.get("content")).strip()
        a.abre(t, texto or None)
    elif m.get("role") == "assistant":
        a.modelo = m.get("model") or a.modelo
        u = m.get("usage") or {}
        a.tokens = sum(u.get(k) or 0 for k in ("input", "cacheRead", "cacheWrite")) or a.tokens
        for b in m.get("content") or []:
            if not isinstance(b, dict):
                continue
            if b.get("type") == "text":
                a.texto(t, b.get("text"))
            elif b.get("type") == "toolCall":
                a.chama(t, b.get("id"), b.get("name"), b.get("arguments"))
        if m.get("stopReason") in ("stop", "aborted", "error", "length"):
            a.fecha(t)
    elif m.get("role") == "toolResult":
        a.resultado(t, m.get("toolCallId"), m.get("content"), bool(m.get("isError")), m.get("toolName") or "resultado")


def pi(agora, limite):
    agentes, ids, pais = {}, {}, {}   # ids: caminho normalizado ou id -> id da sessao
    for f in (HOME / ".pi" / "agent" / "sessions").glob("*/*.jsonl"):
        if not mudou_desde(f, limite):
            continue
        c, registros = novas(f)
        a = c.agente
        for r in registros:
            pi_registro(a, r, c.extra)
        sid = c.extra.get("id") or f.stem.split("_", 1)[-1]
        agentes[sid] = (a, a.titulo or "subagente", curto(a.prompt or "", 80))
        ids[os.path.normcase(str(f))] = ids[sid] = sid
        pais[sid] = c.extra.get("pai")
    # parentSession traz o caminho do arquivo da mae ou o id dela
    pais = {sid: ids.get(os.path.normcase(pai)) or ids.get(pai) for sid, pai in pais.items() if pai}
    return arvore("pi", agentes, pais, agora)


# ---------------------------------------------------------------- Antigravity CLI (agy)

AGY_IGNORA = {"SYSTEM_MESSAGE", "EPHEMERAL_MESSAGE", "CHECKPOINT", "CONVERSATION_HISTORY", "DIRECTORY_RULES"}
# ponytail: o modelo sai por regex do blob protobuf do executor_metadata (o ultimo nome casado); um modelo fora
# das familias gemini, claude e gpt fica sem nome. Decodificar o campo do protobuf se o agy trouxer outra familia.
AGY_MODELO = re.compile(rb"(?:gemini|claude|gpt)-[a-z0-9.\-]{2,40}")


def agy_registro(a, r, extra):
    t, tipo = seg(r.get("created_at")), r.get("type")
    if tipo == "USER_INPUT":
        conteudo = r.get("content") or ""
        m = re.search(r"<USER_REQUEST>\s*(.*?)\s*(?:</USER_REQUEST>|$)", conteudo, re.S)
        a.abre(t, (m.group(1) if m else conteudo).strip() or None)
    elif tipo == "PLANNER_RESPONSE":
        a.texto(t, r.get("content"))
        chamadas = [c for c in r.get("tool_calls") or [] if isinstance(c, dict)]
        for n, c in enumerate(chamadas):
            args = c.get("args") if isinstance(c.get("args"), dict) else {}
            chave = "%s:%d" % (r.get("step_index"), n)
            a.chama(t, chave, c.get("name"), args.get("toolSummary") or args, descricao=args.get("toolSummary"),
                    arquivos=arquivos_escritos(c.get("name"), args))
            if c.get("name") == "ask_question":
                a.pergunta = chave
            elif c.get("name") == "invoke_subagent":
                extra.setdefault("metas", []).extend(s for s in args.get("Subagents") or [] if isinstance(s, dict))
        if not chamadas:
            a.fecha(t)
    elif tipo and tipo not in AGY_IGNORA:
        conteudo = r.get("content") or r.get("error") or ""
        # ponytail: cada conversationId de resultado casa, em ordem, com o proximo Subagents[] despachado; dois
        # despachos com os resultados fora de ordem trocam Role e TypeName entre os filhos. Casar pelo id da
        # chamada se o agy passar a grava-lo no resultado.
        for filho in re.findall(r'"conversationId"\s*:\s*"([0-9a-f-]{36})"', conteudo):
            metas = extra.get("metas") or []
            extra.setdefault("filhos", {})[filho] = metas.pop(0) if metas else {}
        if tipo == "ASK_QUESTION":
            a.pergunta = None
        erro = tipo == "ERROR_MESSAGE" or r.get("status") == "ERROR"
        # ponytail: um passo de resultado por chamada, na ordem: fecha a mais antiga ainda sem resultado. Resultado
        # fora de ordem deixa a chamada errada como "agora"; casar pelo id se o agy passar a grava-lo no resultado.
        a.resultado(t, next(iter(a.pendentes), None), conteudo, erro, tipo.lower())


def agy_workspace(uris):
    """A pasta do primeiro workspace_uris (JSON com URIs file:///), para a conversa que o history.jsonl nao cita."""
    try:
        lista = json.loads(uris or "[]")
        return unquote(urlsplit(lista[0]).path) if lista else None
    except (ValueError, TypeError, IndexError, AttributeError):
        return None


AGY_PASTA = re.compile(r"workspaceDirs=\[([^\]]+)\]")
AGY_CONVERSA = re.compile(r"Created conversation ([0-9a-f-]{36})")
LOGS_AGY = {}   # log do CLI -> (mtime, {conversa: (pasta, sem_interacao)})


def agy_execucoes_dos_logs(base):
    """Por conversa, a pasta e se a execucao foi -p ("Print mode"), pelos logs do CLI, um por execucao.

    A conversa do modo -p nao entra no history.jsonl, e so o log diz que ninguem vai responder a ela.
    ponytail: pega o conteudo inteiro de workspaceDirs=[...]; com mais de um diretorio (separados por espaco, como
    um caminho com espaco) a pasta sai errada. Separar pelos prefixos de unidade se o --add-dir virar comum.
    """
    execucoes = {}
    for f in (base / "log").glob("cli-*.log"):
        chave = str(f)
        try:
            mtime = f.stat().st_mtime
            if LOGS_AGY.get(chave, (None,))[0] != mtime:
                pasta, conversas, sem_interacao = None, [], False
                for linha in f.read_text("utf-8", "replace").splitlines():
                    m = AGY_PASTA.search(linha)
                    pasta = m.group(1) if m else pasta
                    sem_interacao = sem_interacao or "Print mode:" in linha
                    m = AGY_CONVERSA.search(linha)
                    if m:
                        conversas.append((m.group(1), pasta))
                LOGS_AGY[chave] = (mtime, {c: (p, sem_interacao) for c, p in conversas})
        except OSError:
            continue
        execucoes.update(LOGS_AGY[chave][1])
    return execucoes


def agy(agora, limite):
    base = HOME / ".gemini" / "antigravity-cli"
    if not (base / "brain").is_dir():
        return []
    resumos = {r.get("conversation_id"): r for r in consulta(base / "conversation_summaries.db",
                                                               "select * from conversation_summaries")}
    h, registros = novas(base / "history.jsonl")
    cwds = h.extra.setdefault("cwds", {})
    for r in registros:
        if r.get("conversationId"):
            cwds[r["conversationId"]] = r.get("workspace")
    execucoes = agy_execucoes_dos_logs(base)
    convs = {}
    for f in (base / "brain").glob("*/.system_generated/logs/transcript_full.jsonl"):
        if not mudou_desde(f, limite):
            continue
        cid = f.parents[2].name
        c, registros = novas(f)
        a = c.agente
        for r in registros:
            agy_registro(a, r, c.extra)
        resumo_agy = resumos.get(cid) or {}
        a.titulo = resumo_agy.get("title") or a.titulo
        pasta_log, sem_interacao = execucoes.get(cid, (None, False))
        a.cwd = a.cwd or cwds.get(cid) or agy_workspace(resumo_agy.get("workspace_uris")) or pasta_log
        if sem_interacao:
            a.encerra_s = ENCERRADA_SEM_INTERACAO_S
        if str(resumo_agy.get("status") or "").endswith("_IDLE") and not resumo_agy.get("not_fully_idle"):
            a.fecha()  # a execucao parou: no modo -p, uma permissao negada encerra sem resposta final
        # O modelo so existe num blob do banco da conversa, gravado no fim do turno: relido quando ele muda.
        db = base / "conversations" / (cid + ".db")
        try:
            mtime = db.stat().st_mtime
        except OSError:
            mtime = None
        if mtime and c.extra.get("db_mtime") != mtime:
            c.extra["db_mtime"] = mtime
            linha = consulta(db, "select * from executor_metadata order by rowid desc limit 1")
            achados = [m.group(0) for v in (linha[0].values() if linha else ()) if isinstance(v, bytes)
                       for m in AGY_MODELO.finditer(v)]
            a.modelo = achados[-1].decode() if achados else a.modelo
        convs[cid] = c
    pais, metas = {}, {}
    for cid, c in convs.items():
        for filho, meta in (c.extra.get("filhos") or {}).items():
            pais[filho], metas[filho] = cid, meta
    agentes = {}
    for cid, c in convs.items():
        a, meta = c.agente, metas.get(cid) or {}   # so o subagente tem meta
        if not a.modelo and meta.get("Model") not in (None, "", "inherit"):
            a.modelo = meta["Model"]
        tipo = meta.get("TypeName")
        agentes[cid] = (a, tipo if tipo and tipo != "self" else "subagente", meta.get("Role"))
    return arvore("agy", agentes, pais, agora)


# ---------------------------------------------------------------- estado

HARNESSES = (("claude-code", "Claude Code", claude), ("codex", "Codex", codex), ("agy", "Antigravity", agy),
             ("pi", "Pi", pi))


# ---------------------------------------------------------------- clawd-panel e limites de uso

def clawd(rota, corpo=None, timeout=0.5):
    """Pede ao clawd-panel: GET, ou POST com `corpo` em JSON. (status, json), ou (0, None) com ele fora do ar."""
    u = urlsplit(CLAWD)
    c = http.client.HTTPConnection(u.hostname, u.port or 80, timeout=timeout)
    try:
        dados = None if corpo is None else json.dumps(corpo).encode("utf-8")
        c.request("GET" if dados is None else "POST", rota, body=dados,
                  headers={"Content-Type": "application/json", "X-Monitor": "1"} if dados else {})
        r = c.getresponse()
        return r.status, json.loads(r.read() or b"null")
    except (OSError, ValueError, http.client.HTTPException):
        return 0, None
    finally:
        c.close()


def janela_minutos(m):
    """A janela de um limite do Codex em palavras: 30 -> '30 min', 300 -> '5 h', 10080 -> 'semana', 43200 -> '30 dias'.
    Sem janela (o campo e opcional no rate_limits), so 'limite'."""
    if not isinstance(m, int) or m <= 0:
        return "limite"
    if m == 7 * 1440:
        return "semana"
    if m < 60:
        return "%d min" % m
    return "%d h" % (m // 60) if m < 1440 else "%d dias" % (m // 1440)


# O que o clawd-panel respondeu por ultimo em cada rota lida: o /status (limites) vale 10 s, o ritmo da statusline, e o
# /panes (pane e pergunta de cada agente) vale 2 s. Fora do ar, so tenta de novo em 10 s: o timeout nao pesa em toda
# consulta.
CLAWD_LIDO = {}  # rota -> (quando leu, json; None se nao veio)


def clawd_lido(rota, validade, agora):
    """(quando leu, json) da rota do clawd-panel, relida so depois de `validade` s; {} com ele fora do ar."""
    quando, dados = CLAWD_LIDO.get(rota, (0.0, None))
    if agora - quando >= (validade if dados is not None else 10):
        st, d = clawd(rota)
        quando, dados = agora, (d if st == 200 and isinstance(d, dict) else None)
        CLAWD_LIDO[rota] = (quando, dados)
    return quando, dados or {}


def limites(agora):
    """O uso do plano: Claude (5 h, semana e a cota do Fable) pelo clawd-panel, que os recebe da statusline, e
    Codex pelo rate_limits do transcript. Some o que nao se sabe: janela vencida, clawd-panel fora do ar."""
    lido, s = clawd_lido("/status", 10, agora)
    num = lambda v: isinstance(v, (int, float)) and not isinstance(v, bool)
    lista = []
    for nome, pct, conhecido, falta in (("5 h", "session_pct", "session_known", "session_resets_in"),
                                        ("semana", "week_pct", "week_known", "week_resets_in")):
        if s.get(conhecido) and num(s.get(pct)):
            # O clawd-panel diz quanto falta; somado a hora da leitura, oscila 1 s de uma leitura para outra. Ao minuto,
            # o JSON fica igual entre leituras e a pagina nao se refaz a toa.
            lista.append({"grupo": "Claude", "nome": nome, "pct": round(s[pct]),
                          "renova": iso(round((lido + s[falta]) / 60) * 60) if num(s.get(falta)) else None})
    if s.get("fable_known") and s.get("fable_fonte") == "cota" and num(s.get("fable_pct")):
        lista.append({"grupo": "Claude", "nome": "Fable", "pct": round(s["fable_pct"]), "renova": None})
    rate = LIMITES_CODEX.get("rate") or {}
    for chave in ("primary", "secondary"):
        j = rate.get(chave)
        if isinstance(j, dict) and num(j.get("used_percent")) and num(j.get("resets_at")) and j["resets_at"] > agora:
            lista.append({"grupo": "Codex", "nome": janela_minutos(j.get("window_minutes")), "pct": round(j["used_percent"]),
                          "renova": iso(j["resets_at"])})
    return lista


def aprende_janelas(sessoes, agora):
    """A janela de verdade de cada modelo do Claude, pela % de contexto que a statusline de cada sessao publica no
    clawd-panel: tokens / % da a janela, arredondada para a mais proxima de JANELAS. Corrige na hora a sessao e os
    agentes do mesmo modelo nesta consulta, e fica em JANELA_MODELO para as proximas."""
    publicadas = {c.get("session_id"): c.get("context_pct") for c in clawd_lido("/sessions", 10, agora)[1].get("sessions") or []
                  if isinstance(c, dict)}
    for s in sessoes:
        pct = publicadas.get(id_cli(s["id"]))
        if (s["harness"] == "claude-code" and isinstance(pct, (int, float)) and not isinstance(pct, bool) and pct >= 1
                and s.get("tokens") and s.get("modelo")):
            JANELA_MODELO[s["modelo"]] = min(JANELAS, key=lambda j: abs(j - s["tokens"] * 100 / pct))
    for s in sessoes:
        for d in [s] + s["subagentes"] + [a for w in s["workflows"] for a in w["agentes"]]:
            if d.get("modelo") in JANELA_MODELO:
                d["janela"] = JANELA_MODELO[d["modelo"]]


def id_cli(sessao):
    """O id da sessao como a CLI (e o herdr) o conhece, sem o prefixo do harness: 'claude-code:abc' -> 'abc'."""
    return sessao.split(":", 1)[-1]


def panes(agora):
    """{id da sessao na CLI: {pane_id, bloqueio}} dos agentes que o herdr ve, pelo /panes do clawd-panel. A pergunta ja
    sai no formato que a pagina desenha, conferido aqui: texto e opcoes com numero e rotulo."""
    ligados = {}
    for p in clawd_lido("/panes", 2, agora)[1].get("panes") or []:
        if not (isinstance(p, dict) and p.get("sessao") and p.get("pane_id")):
            continue
        b = p.get("bloqueio") if isinstance(p.get("bloqueio"), dict) else None
        opcoes = b.get("opcoes") if b and isinstance(b.get("opcoes"), list) else []
        ligados[p["sessao"]] = {"pane_id": p["pane_id"], "bloqueio": b and {
            "pergunta": str(b.get("pergunta") or ""),
            "opcoes": [{"n": o["n"], "rotulo": o["rotulo"]} for o in opcoes
                       if isinstance(o, dict) and isinstance(o.get("n"), int) and isinstance(o.get("rotulo"), str)]}}
    return ligados


def corpo_responder(pedido):
    """A opcao escolhida: numero e o rotulo que estava no botao (o clawd-panel confere que a tela ainda o mostra)."""
    n, rotulo = pedido.get("n"), pedido.get("rotulo")
    ok = isinstance(n, int) and not isinstance(n, bool) and isinstance(rotulo, str) and rotulo
    return {"n": n, "rotulo": rotulo} if ok else None


def corpo_enviar(pedido):
    """Uma linha imprimivel de ate 4000 caracteres, o mesmo que o /enviar do clawd-panel aceita."""
    texto = pedido.get("texto")
    ok = isinstance(texto, str) and texto.strip() and len(texto) <= 4000 and texto.isprintable()
    return {"texto": texto} if ok else None


# Rota da pagina -> (rota do clawd-panel, o corpo que ela leva a partir do pedido; None quando o pedido e invalido).
ACOES = {"/api/responder": ("/responder", corpo_responder),
         "/api/enviar": ("/enviar", corpo_enviar),
         "/api/interromper": ("/enviar", lambda pedido: {"tecla": "Escape"})}
TOKEN = secrets.token_urlsafe(16)  # um por processo do servidor: vai na pagina e volta no X-Monitor de cada acao
# Na rede (--rede), quem vem de fora da maquina (o celular) entra com a chave: o link com ?chave= grava o cookie, e todo
# pedido de fora o traz. Ela fica na pasta de dados, para o link do celular valer depois de reiniciar o servidor.
ARQUIVO_CHAVE = VOZES / "chave-rede.txt"


def chave_rede():
    """A chave do celular, gerada na primeira vez e guardada so para o usuario."""
    try:
        chave = ARQUIVO_CHAVE.read_text("ascii").strip()
        if len(chave) >= 32:
            return chave
    except OSError:
        pass
    chave = secrets.token_urlsafe(24)
    ARQUIVO_CHAVE.parent.mkdir(parents=True, exist_ok=True)
    ARQUIVO_CHAVE.write_text(chave, "ascii")
    if os.name != "nt":
        os.chmod(ARQUIVO_CHAVE, 0o600)
    return chave


def ip_local():
    """O IP desta maquina na rede local: o da rota padrao (o connect de UDP nao manda pacote nenhum)."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        return s.getsockname()[0]
    except OSError:
        return "127.0.0.1"
    finally:
        s.close()


def atividade(sessoes, n=30):
    """O feed da tela: os ultimos `n` eventos de todos os agentes abertos, do mais novo ao mais antigo. O resultado de
    uma ferramenta fica de fora: a chamada ja conta o passo, e a saida esta no card aberto."""
    linhas = []
    for s in sessoes:
        for d in [s] + s["subagentes"] + [a for w in s["workflows"] for a in w["agentes"]]:
            a = INDICE.get(d["id"])
            for e in (list(a.eventos)[-n:] if a else []):
                if e["tipo"] != "resultado":
                    linhas.append({"ts": e["ts"], "tipo": e["tipo"], "nome": e.get("nome"), "texto": e.get("descricao") or e["texto"],
                                   "sessao": s["id"], "projeto": s["projeto"], "quem": None if d is s else d["nome"]})
    linhas.sort(key=lambda x: x["ts"], reverse=True)
    return [dict(x, ts=iso(x["ts"])) for x in linhas[:n]]


URGENCIA = ("esperando", "trabalhando", "finalizado")


def mais_urgente(s):
    """O estado mais urgente da sessao: o do agente mais urgente, subagente incluso, e esperando se o herdr ve uma
    pergunta na tela. E por ele que a sessao se ordena e se sinaliza (borda, aba, cabecalho, avisos)."""
    if s.get("bloqueio"):
        return "esperando"
    estados = {d["status"] for d in [s] + s["subagentes"] + [a for w in s["workflows"] for a in w["agentes"]]}
    return next((e for e in URGENCIA if e in estados), s["status"])


def estado():
    with TRAVA:
        agora = time.time()
        limite = agora - VARREDURA_S
        sessoes = []
        for _, _, fonte in HARNESSES:
            try:
                sessoes += fonte(agora, limite)
            except Exception:
                traceback.print_exc()  # um harness com defeito nao derruba os outros
        # A sessao encerrada (processo morto, ou sem sinal ha 30 min) sai do painel: o que acabou de rodar numa
        # sessao aberta ja aparece como finalizado.
        sessoes = sorted((s for s in sessoes if s["status"] != "encerrada"), key=lambda s: s["ultimo"] or "",
                         reverse=True)
        harnesses = [{"id": hid, "nome": nome, "ativas": sum(1 for s in sessoes if s["harness"] == hid)}
                     for hid, nome, _ in HARNESSES]
        feed = atividade(sessoes)
    # Fora da trava: a espera pelo clawd-panel nao segura o /api/eventos. As sessoes sao dicionarios novos desta consulta.
    ligados = panes(agora)
    aprende_janelas(sessoes, agora)
    for s in sessoes:  # a sessao dentro do herdr ganha o pane (e a pergunta da tela): da para responder por aqui
        p = ligados.get(id_cli(s["id"])) or {}
        s["pane"], s["bloqueio"] = p.get("pane_id"), p.get("bloqueio")
        s["urgente"] = mais_urgente(s)
    sessoes.sort(key=lambda s: URGENCIA.index(s["urgente"]) if s["urgente"] in URGENCIA else len(URGENCIA))  # estavel
    return {"gerado": iso(agora), "versao": VERSAO, "harnesses": harnesses, "sessoes": sessoes, "limites": limites(agora),
            "atividade": feed}


# ---------------------------------------------------------------- vozes locais

def executavel(caminho):
    """O executavel no caminho (com .exe no Windows), ou None."""
    f = caminho.with_name(caminho.name + ".exe") if os.name == "nt" else caminho
    return f if f.is_file() else None


def python_kokoro():
    """O Python do venv do Kokoro, com o modelo ao lado; None sem o Kokoro instalado."""
    k = VOZES / "kokoro"
    py = executavel(k / "venv" / ("Scripts" if os.name == "nt" else "bin") / "python")
    return py if py and (k / "kokoro-v1.0.onnx").is_file() and (k / "voices-v1.0.bin").is_file() else None


def vozes_locais():
    """As vozes locais instaladas, [{id, nome, motor}]; o id e '<motor>:<voz>'."""
    vozes = []
    if executavel(VOZES / "piper" / "piper" / "piper"):
        for f in sorted((VOZES / "piper" / "vozes").glob("*.onnx")):
            if f.with_name(f.name + ".json").is_file():
                partes = f.stem.split("-")  # pt_BR-faber-medium
                nome = partes[1] + " · " + partes[0].replace("_", "-") if len(partes) > 1 else f.stem
                vozes.append({"id": "piper:" + f.stem, "nome": nome, "motor": "Piper"})
    if python_kokoro():
        vozes += [{"id": "kokoro:" + v, "nome": n + " · pt-BR", "motor": "Kokoro"} for v, n in KOKORO_VOZES.items()]
    return vozes


KOKORO = {"processo": None}   # o Kokoro residente (voz_kokoro.py): carrega o modelo uma vez
TRAVA_KOKORO = threading.Lock()  # um pedido por vez no Kokoro, na ordem em que chegam
CACHE_FALA = OrderedDict()       # (voz, texto) -> WAV das ultimas falas: repetir ou adiantar sai na hora
TRAVA_CACHE = threading.Lock()


def fala_kokoro(voz, texto):
    """WAV do Kokoro residente; sobe o processo no primeiro pedido e de novo se ele tiver morrido."""
    with TRAVA_KOKORO:
        p = KOKORO["processo"]
        if p is None or p.poll() is not None:
            p = KOKORO["processo"] = subprocess.Popen(
                [str(python_kokoro()), str(AQUI / "voz_kokoro.py"), str(VOZES / "kokoro")], stdin=subprocess.PIPE,
                stdout=subprocess.PIPE, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        vigia = threading.Timer(120, p.kill)  # travou: morre, e o proximo pedido sobe outro
        vigia.start()
        try:
            p.stdin.write(json.dumps({"voz": voz, "texto": texto}).encode("ascii") + b"\n")
            p.stdin.flush()
            cabecalho = p.stdout.read(8)
            tamanho = int.from_bytes(cabecalho, "big") if len(cabecalho) == 8 else 0
            wav = p.stdout.read(tamanho) if tamanho else b""
        finally:
            vigia.cancel()
    if not wav or len(wav) != tamanho:
        raise OSError("o Kokoro nao gerou a fala")
    return wav


def fala_local(voz_id, texto):
    """O WAV da fala por um motor local, ou None se a voz nao esta instalada. So vale id da lista: nenhum caminho
    sai do pedido. Levanta OSError ou SubprocessError se o motor falhar."""
    if voz_id not in {v["id"] for v in vozes_locais()}:
        return None
    texto = re.sub(r"\s+", " ", texto).strip()  # o Piper le uma frase por linha e regrava o arquivo a cada linha
    chave = (voz_id, texto)
    with TRAVA_CACHE:
        if chave in CACHE_FALA:
            CACHE_FALA.move_to_end(chave)
            return CACHE_FALA[chave]
    motor, _, voz = voz_id.partition(":")
    if motor == "kokoro":
        wav = fala_kokoro(voz, texto)
    else:
        with tempfile.TemporaryDirectory() as pasta:
            saida = os.path.join(pasta, "fala.wav")
            subprocess.run([str(executavel(VOZES / "piper" / "piper" / "piper")), "--model",
                            str(VOZES / "piper" / "vozes" / (voz + ".onnx")), "--output_file", saida],
                           input=texto.encode("utf-8"), capture_output=True, timeout=120, check=True,
                           creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
            wav = Path(saida).read_bytes()
    with TRAVA_CACHE:
        CACHE_FALA[chave] = wav
        while len(CACHE_FALA) > 64:
            CACHE_FALA.popitem(last=False)
    return wav


# ---------------------------------------------------------------- servidor

class Servidor(ThreadingHTTPServer):
    daemon_threads = True
    allow_reuse_address = os.name != "nt"  # no Windows, SO_REUSEADDR deixa dois servidores na mesma porta
    rede, chave = False, None             # --rede: atende a rede local, e quem vem de fora traz a chave


class Pedido(BaseHTTPRequestHandler):
    def responde(self, codigo, corpo, tipo="application/json; charset=utf-8"):
        if not isinstance(corpo, bytes):
            corpo = json.dumps(corpo, ensure_ascii=False).encode("utf-8")
        try:
            self.send_response(codigo)
            self.send_header("Content-Type", tipo)
            self.send_header("Content-Length", str(len(corpo)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(corpo)
        except ConnectionError:
            pass  # a pagina fechou ou recarregou no meio da resposta; nao e erro do servidor

    def host_ok(self):
        """So 127.0.0.1 e localhost na porta do servidor: fecha a porta ao DNS rebinding. Na rede, tambem um IP (o da
        maquina na rede local ou no Tailscale): o rebinding precisa de um nome de host, e nome nao passa."""
        porta = self.server.server_address[1]
        host = self.headers.get("Host") or ""
        if host in ("127.0.0.1:%d" % porta, "localhost:%d" % porta) or (
                self.server.rede and re.fullmatch(r"\d{1,3}(?:\.\d{1,3}){3}:%d" % porta, host)):
            return True
        self.responde(403, {"erro": "host recusado"})
        return False

    def local(self):
        return self.client_address[0] in ("127.0.0.1", "::1")

    def autorizado(self):
        """Desta maquina, sempre; de fora (so existe com --rede), com o cookie da chave."""
        if self.local():
            return True
        try:
            c = SimpleCookie(self.headers.get("Cookie") or "").get("monitor_chave")
        except CookieError:
            c = None
        return bool(c and self.server.chave and hmac.compare_digest(c.value.encode(), self.server.chave.encode()))

    def entra_com_chave(self, q):
        """GET /?chave=<a chave>: grava o cookie por um ano e tira a chave da barra de endereco. True se entrou."""
        chave = q.get("chave", [""])[0]
        if not (self.server.chave and chave and hmac.compare_digest(chave.encode(), self.server.chave.encode())):
            return False
        self.send_response(303)
        self.send_header("Set-Cookie", "monitor_chave=%s; Path=/; Max-Age=31536000; HttpOnly; SameSite=Strict" % chave)
        self.send_header("Location", "/")
        self.send_header("Content-Length", "0")
        self.end_headers()
        return True

    def do_GET(self):
        if not self.host_ok():
            return
        url = urlsplit(self.path)
        q = parse_qs(url.query)
        if url.path == "/" and not self.local() and self.entra_com_chave(q):
            return
        if not self.autorizado():
            return self.responde(401, "Abra o link com a chave que o monitor mostra: python monitor.py --rede".encode(),
                                 "text/plain; charset=utf-8")
        if url.path == "/":
            pagina = (AQUI / "monitor.html").read_bytes().replace(b"__TOKEN__", TOKEN.encode())
            self.responde(200, pagina, "text/html; charset=utf-8")
        elif url.path == "/api/saude":
            self.responde(200, {"monitor": True, "versao": VERSAO, "pid": os.getpid(), "rede": self.server.rede})
        elif url.path == "/api/estado":
            # O token vai junto: a pagina aberta antes de um reinicio pega o novo (so a mesma origem le esta resposta).
            self.responde(200, dict(estado(), token=TOKEN))
        elif url.path == "/api/vozes":
            self.responde(200, {"vozes": vozes_locais()})
        elif url.path == "/api/tela":
            # O espelho do terminal: a tela visivel do pane da sessao, lida pelo clawd-panel. So leitura.
            p = panes(time.time()).get(id_cli(q.get("sessao", [""])[0]))
            if not p:
                self.responde(404, {"erro": "sessao fora do herdr"})
            else:
                self.repassa(*clawd("/ler?pane_id=" + quote(p["pane_id"]), timeout=3))
        elif url.path == "/api/eventos":
            id_ = q.get("agente", [""])[0]
            with TRAVA:
                a = INDICE.get(id_)
                eventos = [dict(e, ts=iso(e["ts"])) for e in list(a.eventos)[-EVENTOS_API:]] if a else None
                fala = a.fala if a else None
                arquivos = arquivos_do_turno(a) if a else None
            if eventos is None:
                self.responde(404, {"erro": "agente desconhecido"})
            else:
                self.responde(200, {"agente": id_, "eventos": eventos, "fala": fala, "arquivos": arquivos})
        else:
            self.responde(404, {"erro": "rota desconhecida"})

    def do_POST(self):
        if not self.host_ok():
            return
        if not self.autorizado():
            return self.responde(401, {"erro": "falta a chave da rede"})
        if urlsplit(self.path).path == "/api/falar":
            return self.falar()
        if urlsplit(self.path).path in ACOES:
            return self.age(*ACOES[urlsplit(self.path).path])
        if urlsplit(self.path).path != "/api/parar":
            return self.responde(404, {"erro": "rota desconhecida"})
        if self.headers.get("X-Monitor") != "parar" or not self.local():
            return self.responde(403, {"erro": "so desta maquina, com o cabecalho X-Monitor: parar"})
        self.responde(200, {"parando": True})
        threading.Thread(target=self.server.shutdown).start()

    def falar(self):
        """POST {voz, texto} -> audio/wav de um motor local. So aceita JSON: de outra origem, o navegador so manda
        isso depois de um preflight que o servidor nao responde."""
        if not (self.headers.get("Content-Type") or "").startswith("application/json"):
            return self.responde(415, {"erro": "mande JSON"})
        pedido = self.corpo_json()
        try:
            voz, texto = str(pedido["voz"]), str(pedido["texto"])[:LIMITE_FALA]
        except (KeyError, TypeError):
            return self.responde(400, {"erro": "pedido invalido"})
        try:
            wav = fala_local(voz, texto)
        except (OSError, subprocess.SubprocessError):
            traceback.print_exc()
            return self.responde(500, {"erro": "o motor de voz falhou"})
        if wav is None:
            return self.responde(404, {"erro": "voz desconhecida"})
        self.responde(200, wav, "audio/wav")

    def corpo_json(self):
        """O corpo JSON do pedido, ate 64 KiB; None com tamanho ausente, negativo ou grande demais, ou JSON invalido."""
        try:
            n = int(self.headers.get("Content-Length") or 0)
            return json.loads(self.rfile.read(n)) if 0 < n <= 64 * 1024 else None
        except ValueError:
            return None

    def repassa(self, st, r):
        """A resposta do clawd-panel como veio; fora do ar, 502."""
        self.responde(st or 502, r if isinstance(r, dict) else {"erro": "clawd-panel fora do ar"})

    def age(self, rota, monta):
        """Responde, escreve ou interrompe o agente de uma sessao. Quem age no herdr e o clawd-panel; o monitor so
        acha o pane da sessao e repassa. Esta rota digita num agente, entao so a pagina servida por este processo
        passa: ela traz o TOKEN no X-Monitor (outra origem nao le a pagina, e o cabecalho proprio obriga o preflight
        que o servidor nao responde), e a Origin, se vier, tem que ser a dele: a do Host que o host_ok ja conferiu."""
        if (not hmac.compare_digest((self.headers.get("X-Monitor") or "").encode(), TOKEN.encode())
                or self.headers.get("Origin") not in (None, "http://" + (self.headers.get("Host") or ""))):
            return self.responde(403, {"erro": "so a pagina do monitor age nos agentes"})
        pedido = self.corpo_json()
        corpo = monta(pedido) if isinstance(pedido, dict) else None
        if corpo is None:
            return self.responde(400, {"erro": "pedido invalido"})
        p = panes(time.time()).get(id_cli(str(pedido.get("sessao") or "")))
        if not p:
            return self.responde(404, {"erro": "sessao fora do herdr: so leitura"})
        resposta = clawd(rota, dict(corpo, pane_id=p["pane_id"]), timeout=8)
        CLAWD_LIDO.pop("/panes", None)  # a proxima consulta rele o /panes: a pergunta respondida some na hora
        self.repassa(*resposta)

    def log_message(self, *_):
        pass


def servir(porta, rede=False):
    sys.stderr.reconfigure(errors="backslashreplace")
    srv = Servidor(("0.0.0.0" if rede else "127.0.0.1", porta), Pedido)
    srv.rede, srv.chave = rede, chave_rede() if rede else None
    # A primeira varredura le os transcripts grandes do zero (1,6 s); feita ja, a pagina que abre em seguida nao espera.
    threading.Thread(target=estado, daemon=True).start()
    try:
        srv.serve_forever()
    finally:
        srv.server_close()


# ---------------------------------------------------------------- CLI

def pede(porta, metodo, rota, cabecalhos=None):
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=2)
    try:
        c.request(metodo, rota, headers=cabecalhos or {})
        r = c.getresponse()
        return r.status, r.read()
    finally:
        c.close()


def saude(porta):
    """O /api/saude do monitor na porta, ou None se nao ha monitor ali."""
    try:
        st, corpo = pede(porta, "GET", "/api/saude")
        d = json.loads(corpo) if st == 200 else None
    except (OSError, ValueError, http.client.HTTPException):
        return None
    return d if isinstance(d, dict) and d.get("monitor") is True else None


def porta_livre(porta):
    s = socket.socket()
    try:
        s.bind(("127.0.0.1", porta))
        return True
    except OSError:
        return False
    finally:
        s.close()


def espera(cond, segundos):
    fim = time.time() + segundos
    while time.time() < fim:
        if cond():
            return True
        time.sleep(0.1)
    return cond()


def parar(porta):
    if saude(porta) is None:
        diz("Nenhum monitor na porta %d." % porta)
        return 0
    try:
        pede(porta, "POST", "/api/parar", {"X-Monitor": "parar"})
    except (OSError, http.client.HTTPException):
        pass
    if espera(lambda: saude(porta) is None, 5):
        diz("Monitor parado na porta %d." % porta)
        return 0
    diz("ERRO: o monitor na porta %d nao parou." % porta)
    return 1


def navegador_app():
    """O Edge ou o Chrome, que abrem uma pagina como janela propria (--app); None sem nenhum dos dois."""
    pastas = [os.environ.get(v) for v in ("ProgramFiles(x86)", "ProgramFiles", "LOCALAPPDATA")]
    candidatos = [Path(p) / sub for p in pastas if p
                  for sub in ("Microsoft/Edge/Application/msedge.exe", "Google/Chrome/Application/chrome.exe")]
    candidatos += [Path(w) for w in map(shutil.which, ("microsoft-edge", "google-chrome", "chromium")) if w]
    return next((str(c) for c in candidatos if c.is_file()), None)


def abre_janela(url):
    """O painel numa janela so dele, sem abas nem barra de endereco; sem Edge nem Chrome, no navegador padrao."""
    exe = navegador_app()
    if exe:
        subprocess.Popen([exe, "--app=" + url], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    else:
        webbrowser.open(url)


def subir(porta, navegador, rede=False):
    log = os.path.join(tempfile.gettempdir(), "monitor-%d.log" % porta)
    s = saude(porta)
    if s and s.get("versao") != VERSAO:
        diz("Monitor da versao %s na porta %d; trocando pela %s." % (s.get("versao"), porta, VERSAO))
        parar(porta)
        s = None
    elif s and rede and not s.get("rede"):  # pediu a rede e o que esta no ar so atende esta maquina; o contrario fica
        diz("Monitor na porta %d so atende esta maquina; subindo de novo com a rede." % porta)
        parar(porta)
        s = None
    if s is None:
        if not espera(lambda: porta_livre(porta), 3):
            diz("ERRO: a porta %d esta ocupada por outro programa. Use --porta." % porta)
            return 1
        opcoes = {"start_new_session": True}
        if os.name == "nt":
            opcoes = {"creationflags": subprocess.DETACHED_PROCESS | subprocess.CREATE_NEW_PROCESS_GROUP
                      | subprocess.CREATE_NO_WINDOW}
        exe = Path(sys.executable)
        if exe.name.lower() == "pythonw.exe" and exe.with_name("python.exe").is_file():
            exe = exe.with_name("python.exe")  # subiu pelo login (pythonw): o servidor quer um stderr para o log
        with open(log, "wb") as erros:  # o log e so do servidor que esta subindo
            subprocess.Popen([str(exe), str(Path(__file__).resolve()), "servir", "--porta", str(porta)]
                             + (["--rede"] if rede else []),
                             stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=erros,
                             close_fds=True, **opcoes)
        if not espera(lambda: saude(porta) is not None, 10):
            diz("ERRO: o monitor nao subiu na porta %d. Veja o log: %s" % (porta, log))
            return 1
        s = saude(porta)
    url = "http://127.0.0.1:%d/" % porta
    if navegador:
        abre_janela(url)
    diz("Monitor em %s (pid %s)" % (url, s.get("pid")))
    if s.get("rede"):
        diz("No celular, na mesma rede (ou pelo IP do Tailscale): http://%s:%d/?chave=%s" % (ip_local(), porta, chave_rede()))
    diz("Log: %s" % log)
    diz('Para parar: python "%s" parar' % Path(__file__).resolve())
    return 0


RUN = r"Software\Microsoft\Windows\CurrentVersion\Run"


def inicio_no_login(ligar, rede=False):
    """Liga ou desliga a subida do painel quando voce entra no Windows: o valor 'monitor' no Run do usuario, com o
    pythonw (sem janela de console)."""
    if os.name != "nt":
        diz('So no Windows. No macOS e no Linux, ponha nos itens de login: python3 "%s"' % Path(__file__).resolve())
        return 1
    import winreg
    with winreg.OpenKey(winreg.HKEY_CURRENT_USER, RUN, 0, winreg.KEY_SET_VALUE) as k:
        if ligar:
            exe = Path(sys.executable)
            exe = exe.with_name("pythonw.exe") if exe.with_name("pythonw.exe").is_file() else exe
            comando = '"%s" "%s"%s' % (exe, Path(__file__).resolve(), " --rede" if rede else "")
            winreg.SetValueEx(k, "monitor", 0, winreg.REG_SZ, comando)
            diz("O painel sobe quando voce entrar no Windows: " + comando)
        else:
            try:
                winreg.DeleteValue(k, "monitor")
            except FileNotFoundError:
                pass
            diz("O painel nao sobe mais no login.")
    return 0


def main():
    p = argparse.ArgumentParser(description="Painel local dos agentes de IA de todos os harnesses.")
    p.add_argument("acao", nargs="?", default="subir",
                   choices=("subir", "parar", "servir", "iniciar-no-login", "nao-iniciar-no-login"))
    p.add_argument("--porta", type=int, default=PORTA)
    p.add_argument("--sem-navegador", action="store_true", help="nao abre o navegador")
    p.add_argument("--rede", action="store_true", help="atende tambem a rede local, com chave (o celular)")
    a = p.parse_args()
    if a.acao == "servir":
        servir(a.porta, a.rede)
        return 0
    if a.acao in ("iniciar-no-login", "nao-iniciar-no-login"):
        return inicio_no_login(a.acao == "iniciar-no-login", a.rede)
    return parar(a.porta) if a.acao == "parar" else subir(a.porta, not a.sem_navegador, a.rede)


if __name__ == "__main__":
    sys.exit(main())
