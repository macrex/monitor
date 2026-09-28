#!/usr/bin/env python3
"""Autoteste do monitor pela interface publica: a CLI e a API HTTP que ela serve.

  python teste_monitor.py

Monta transcripts de exemplo dos quatro harnesses num diretorio temporario que faz
o papel de home (HOME e USERPROFILE do subprocesso), sobe o servidor pela CLI numa
porta livre, confere o JSON de /api/estado e /api/eventos e derruba com `parar`.
Nunca le os seus transcripts reais. Sai com 1 se algo falhar.
"""
import atexit
import http.client
import http.server
import json
import os
import re
import shutil
import socket
import subprocess
import sys
import tempfile
import threading
import time
from datetime import datetime, timezone

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.py")
FALHAS = []
HOME = tempfile.mkdtemp(prefix="monitor-home-")
atexit.register(shutil.rmtree, HOME, ignore_errors=True)  # sai mesmo se o teste lancar excecao
AMBIENTE = dict(os.environ, HOME=HOME, USERPROFILE=HOME, MONITOR_VOZES=os.path.join(HOME, "vozes"))


def confere(cond, msg):
    msg = msg.encode("ascii", "backslashreplace").decode("ascii")  # o JSON da API traz acento; o console e ASCII
    (print if cond else FALHAS.append)(("ok: " if cond else "") + msg)


def porta_livre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def ts(atras_s):
    """Timestamp ISO de `atras_s` segundos atras, como os harnesses gravam."""
    d = datetime.fromtimestamp(time.time() - atras_s, timezone.utc)
    return d.strftime("%Y-%m-%dT%H:%M:%S.") + "%03dZ" % (d.microsecond // 1000)


def grava(rel, registros, fim="\n"):
    caminho = os.path.join(HOME, *rel.split("/"))
    os.makedirs(os.path.dirname(caminho), exist_ok=True)
    with open(caminho, "a", encoding="utf-8") as f:
        for r in registros:
            f.write((r if isinstance(r, str) else json.dumps(r, ensure_ascii=False)) + fim)
    return caminho


def cli(*args, ambiente=None):
    p = subprocess.run([sys.executable, SCRIPT] + list(args), env=ambiente or AMBIENTE, capture_output=True, timeout=60)
    return p.returncode, p.stdout + p.stderr


def pede(porta, rota, metodo="GET", cabecalhos=None, corpo=None):
    c = http.client.HTTPConnection("127.0.0.1", porta, timeout=10)
    try:
        c.request(metodo, rota, body=corpo, headers=cabecalhos or {})
        r = c.getresponse()
        corpo = r.read()
    finally:
        c.close()
    try:
        return r.status, json.loads(corpo)
    except ValueError:
        return r.status, corpo


def ascii_puro(b):
    try:
        b.decode("ascii")
        return True
    except UnicodeDecodeError:
        return False


# ---------------------------------------------------------------- Claude Code

def cc_user(atras, texto, cwd="/work/alpha", **extra):
    r = {"type": "user", "timestamp": ts(atras), "cwd": cwd, "entrypoint": extra.pop("entrypoint", "cli"),
         "message": {"role": "user", "content": texto}}
    r.update(extra)
    return r


def cc_assist(atras, blocos, mid="m1", modelo="claude-opus-5-5", entrypoint="cli", usage=None, stop=None):
    return {"type": "assistant", "timestamp": ts(atras), "cwd": "/work/alpha", "entrypoint": entrypoint,
            "message": {"id": mid, "model": modelo, "role": "assistant", "content": blocos, "stop_reason": stop,
                        "usage": usage or {"input_tokens": 10, "cache_read_input_tokens": 0,
                                           "cache_creation_input_tokens": 0}}}


def cc_tool(tid, nome, entrada):
    return {"type": "tool_use", "id": tid, "name": nome, "input": entrada}


def cc_result(atras, tid, conteudo="ok", erro=False, entrypoint="cli"):
    return {"type": "user", "timestamp": ts(atras), "cwd": "/work/alpha", "entrypoint": entrypoint,
            "message": {"role": "user", "content": [{"type": "tool_result", "tool_use_id": tid,
                                                      "content": conteudo, "is_error": erro}]}}


def cc_fim(atras):
    return {"type": "system", "subtype": "turn_duration", "timestamp": ts(atras)}


def cc_sessao(sid, registros, slug="-work-alpha"):
    return grava("/".join([".claude", "projects", slug, sid + ".jsonl"]), registros)


def cc_viva(numero, sid, status, **extra):
    """Arquivo de sessao viva; o pid e o deste teste, que esta vivo enquanto o servidor le."""
    d = {"pid": os.getpid(), "sessionId": sid, "status": status, "entrypoint": "cli"}
    d.update(extra)
    grava(".claude/sessions/%d.json" % numero, [d], fim="")


def pid_morto():
    p = subprocess.Popen([sys.executable, "-c", "pass"])
    p.wait()
    return p.pid


def monta_claude():
    # trabalhando pelo sinal de vida; projeto de abertura mesmo depois do cd; tokens da ultima resposta
    cc_sessao("cc-busy", [
        {"type": "custom-title", "customTitle": "minha sessao"},
        cc_user(60, "implementa o parser"),
        cc_assist(40, [{"type": "text", "text": "vou rodar os testes"}], mid="m1"),
        cc_assist(30, [{"type": "thinking", "thinking": "..."}], mid="m2",
                  usage={"input_tokens": 100, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0}),
        cc_assist(30, [cc_tool("t1", "Bash", {"command": "pytest -q"})], mid="m2",
                  usage={"input_tokens": 100, "cache_read_input_tokens": 900, "cache_creation_input_tokens": 0}),
        cc_assist(25, [{"type": "text", "text": "API Error"}], mid="m3", modelo="<synthetic>",
                  usage={"input_tokens": 0, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}),
        dict(cc_user(20, "x"), type="attachment", cwd="/work/alpha/sub"),
    ])
    cc_viva(111, "cc-busy", "busy", name="nome-vivo")
    grava(".claude/settings.json", [{"model": "opus[1m]"}], fim="")  # janela de 1M para a familia opus

    # finalizado pelo sinal de vida, turno fechado; titulo pelo ai-title; fazendo = 1a linha da resposta, sem markdown
    cc_sessao("cc-idle", [
        {"type": "ai-title", "aiTitle": "titulo da ia"},
        cc_user(300, "revisa o README"),
        cc_assist(290, [{"type": "text", "text": "\n**Pronto**: [README](file:///w/README.md) `revisado`.\n\n- detalhe"}], stop="end_turn"),
        cc_fim(289),
    ])
    cc_viva(222, "cc-idle", "idle", name="idle-vivo")

    # titulo pelo name da sessao viva
    cc_sessao("cc-nome", [cc_user(100, "oi"), cc_assist(90, [{"type": "text", "text": "ola"}]), cc_fim(89)])
    cc_viva(333, "cc-nome", "waiting", waitingFor="input needed", name="nome-da-viva")

    # CLI sem arquivo de sessao viva: encerrada
    cc_sessao("cc-morta", [cc_user(200, "coisa velha"), cc_assist(190, [{"type": "text", "text": "feito"}]), cc_fim(189)])
    # sessao morta a forca: o arquivo de sessao viva fica para tras, mas o processo nao existe mais
    cc_sessao("cc-orfa", [cc_user(200, "morreu a forca"), cc_fim(190)])
    cc_viva(888, "cc-orfa", "idle", pid=pid_morto())
    if os.name == "nt":
        # PID reaproveitado por outro processo: o procStart (criacao do processo) nao bate
        cc_sessao("cc-reusada", [cc_user(200, "pid reusado"), cc_fim(190)])
        cc_viva(889, "cc-reusada", "idle", procStart="1")

    d = "claude-desktop"
    # desktop, turno aberto sem evento ha 5 min e sem ferramenta rodando: travou, esperando voce
    cc_sessao("cc-parada", [cc_user(600, "pedido parado", entrypoint=d),
                            cc_assist(300, [{"type": "text", "text": "pensando"}], entrypoint=d)])
    # desktop, ferramenta sem resultado ha 5 min: trabalhando; sem fala no turno, fazendo = descricao do passo
    cc_sessao("cc-ferramenta", [cc_user(600, "roda a suite", entrypoint=d),
                                cc_assist(300, [cc_tool("t9", "Bash", {"command": "make test",
                                                                       "description": "Roda a suite"})],
                                          entrypoint=d)])
    # desktop, ferramenta que ja devolveu resultado ha 5 min: travou, esperando voce
    cc_sessao("cc-resultado", [cc_user(600, "roda", entrypoint=d),
                               cc_assist(310, [cc_tool("t8", "Bash", {"command": "ls"})], entrypoint=d),
                               cc_result(300, "t8", "linha\n" * 1000, entrypoint=d)])
    # desktop, evento recente: trabalhando
    cc_sessao("cc-recente", [cc_user(30, "pedido novo", entrypoint=d),
                             cc_assist(20, [{"type": "text", "text": "lendo"}], entrypoint=d)])
    # desktop, turno fechado ha 5 min: finalizado; ha 40 min: encerrada
    cc_sessao("cc-finalizado", [cc_user(400, "p", entrypoint=d), cc_assist(310, [{"type": "text", "text": "r"}],
                                                                           entrypoint=d), cc_fim(300)])
    cc_sessao("cc-velha", [cc_user(3000, "p", entrypoint=d), cc_assist(2410, [{"type": "text", "text": "r"}],
                                                                        entrypoint=d), cc_fim(2400)])
    # desktop, comando local (/model) e modo bash (!) depois do turno fechado: a saida deles fecha o turno
    cc_sessao("cc-local", [
        cc_user(700, "p", entrypoint=d), cc_assist(690, [{"type": "text", "text": "r"}], entrypoint=d, stop="end_turn"),
        cc_user(300, "<local-command-caveat>Caveat: The messages below were generated by the user while running"
                     " local commands.</local-command-caveat>", entrypoint=d, isMeta=True),
        cc_user(300, "<command-name>/model</command-name>\n<command-message>model</command-message>\n"
                     "<command-args>opus</command-args>", entrypoint=d),
        cc_user(299, "<local-command-stdout>Set model to opus</local-command-stdout>", entrypoint=d),
    ])
    cc_sessao("cc-bash", [
        cc_user(700, "p", entrypoint=d), cc_assist(690, [{"type": "text", "text": "r"}], entrypoint=d, stop="end_turn"),
        cc_user(300, "<bash-input>ls</bash-input>", entrypoint=d),
        cc_user(299, "<bash-stdout>a.txt</bash-stdout><bash-stderr></bash-stderr>", entrypoint=d),
    ])

    # filtro de prompt: nada disto e prompt
    cc_sessao("cc-ruido", [
        cc_user(500, "pedido real"),
        cc_user(490, "<task-notification>\n<task-id>abc</task-id>\n<status>completed</status>\n</task-notification>"),
        cc_user(480, "Another Claude session sent a message:\n<teammate-message teammate_id=\"x\">oi</teammate-message>"),
        cc_user(470, "This session is being continued from a previous conversation.", isCompactSummary=True),
        cc_user(465, "<artifact-content-authored-by-others/>\nThe summarized conversation included Artifact content"),
        cc_user(463, "The summarized conversation included Artifact content written by others."),
        cc_user(460, [{"type": "text", "text": "Base directory for this skill: /x"}], isMeta=True),
        cc_user(455, "<local-command-stdout>Goal set</local-command-stdout>"),
        cc_user(450, [{"type": "text", "text": "[Request interrupted by user]"}]),
    ])
    cc_viva(444, "cc-ruido", "idle")
    cc_sessao("cc-comando", [
        cc_user(50, "<command-name>/goal</command-name>\n<command-message>goal</command-message>\n"
                    "<command-args>rode a leva</command-args>"),
    ])
    cc_viva(555, "cc-comando", "busy")
    # texto colado chega embrulhado em <pasted_content>, e e o prompt do usuario
    cc_sessao("cc-colado", [
        cc_user(40, "\n\n<pasted_content id=\"2c3b\">\n/goal rode a leva colada\n</pasted_content id=\"2c3b\">\n"),
    ])
    cc_viva(556, "cc-colado", "busy")
    # sessao recem-aberta: o arquivo de sessao viva existe, o transcript so nasce no primeiro prompt
    cc_viva(557, "cc-nova", "idle", cwd="/work/alpha", name="aba nova", startedAt=int((time.time() - 30) * 1000))

    # fora da janela de 24 h e sem sinal de vida: nao aparece
    velho = cc_sessao("cc-fora", [cc_user(90000, "antigo"), cc_fim(89990)])
    os.utime(velho, (time.time() - 90000, time.time() - 90000))


def cc_sub(sid, agente, meta, registros, pasta="subagents"):
    base = "/".join([".claude", "projects", "-work-alpha", sid, pasta, "agent-" + agente])
    grava(base + ".meta.json", [meta], fim="")
    return grava(base + ".jsonl", registros)


def monta_subagentes():
    # a sessao-mae para dois subagentes e lanca um workflow
    cc_sessao("cc-mae", [
        cc_user(900, "orquestra a leva"),
        cc_assist(800, [cc_tool("ts1", "TaskStop", {"task_id": "opus-Painel"})]),
        cc_result(799, "ts1"),
        {"type": "queue-operation", "operation": "enqueue", "timestamp": ts(700),
         "content": "<task-notification>\n<task-id>a3</task-id>\n<status>killed</status>\n"
                    "<summary>Agent was stopped</summary>\n</task-notification>"},
        cc_user(650, "<task-notification>\n<task-id>a6</task-id>\n<status>completed</status>\n</task-notification>"),
        dict(cc_result(600, "wf1"), toolUseResult={"status": "async_launched", "workflowName": "leva-tickets",
                                                   "runId": "wf_123"}),
        cc_assist(10, [cc_tool("t2", "Bash", {"command": "sleep 5"})]),
    ])
    cc_viva(777, "cc-mae", "busy")
    cc_sub("cc-mae", "a1", {"agentType": "Explore", "name": "opus-Explore", "description": "Mapeia transcripts"},
           [cc_user(100, "mapeie os transcripts"), cc_assist(20, [cc_tool("r1", "Read", {"file_path": "/x/y.jsonl"})],
                                                            modelo="claude-opus-5-5")])
    cc_sub("cc-mae", "a2", {"agentType": "general-purpose", "name": "opus-Painel", "description": "Painel"},
           [cc_user(1000, "faz o painel"), cc_assist(950, [{"type": "text", "text": "comecando"}])])
    cc_sub("cc-mae", "a3", {"agentType": "general-purpose", "description": "Janela"},
           [cc_user(1000, "faz a janela"), cc_assist(900, [{"type": "text", "text": "comecando"}])])
    cc_sub("cc-mae", "a4", {"agentType": "general-purpose", "name": "opus-Spec", "description": "Revisao Spec"},
           [cc_user(400, "revise"), cc_assist(300, [{"type": "text", "text": "relatorio enviado"}], stop="end_turn")])
    cc_sub("cc-mae", "a5", {"agentType": "general-purpose", "name": "velho", "description": "antigo"},
           [cc_user(5000, "x"), cc_assist(4000, [{"type": "text", "text": "fim"}], stop="end_turn")])
    cc_sub("cc-mae", "afable-X-cb06", {"agentType": "general-purpose", "name": "fable-X", "model": "claude-fable-5-1",
                                       "description": "Correcoes"},
           [cc_user(60, "corrija")])
    cc_sub("cc-mae", "a6", {"agentType": "general-purpose", "name": "opus-Avisado", "description": "Aviso"},
           [cc_user(900, "avise"), cc_assist(700, [{"type": "text", "text": "trabalhando"}])])
    # workflow em andamento: nome pelo resultado da ferramenta na mae, fase pelo journal
    wf = "subagents/workflows/wf_123"
    grava(".claude/projects/-work-alpha/cc-mae/%s/journal.jsonl" % wf, [
        {"type": "launched"},
        {"type": "started", "agentId": "w0", "label": "ticket 00", "phase": "Implementacao"},
        {"type": "result", "agentId": "w0", "result": {"done": True}},
        {"type": "started", "agentId": "w1", "label": "ticket 01", "phase": "Implementacao"},
        {"type": "result", "agentId": "w1", "result": {"done": True}},
        {"type": "started", "agentId": "w2", "label": "reparo 01 (1)", "phase": "Reparo"},
    ])
    cc_sub("cc-mae", "w0", {"agentType": "workflow-subagent"}, [cc_user(3000, "ticket 00"),
                                                                  cc_assist(2400, [{"type": "text", "text": "ok"}])], pasta=wf)
    cc_sub("cc-mae", "w1", {"agentType": "workflow-subagent"}, [cc_user(500, "ticket 01"),
                                                                  cc_assist(400, [{"type": "text", "text": "ok"}])], pasta=wf)
    cc_sub("cc-mae", "w2", {"agentType": "workflow-subagent"}, [cc_user(30, "reparo"),
                                                                  cc_assist(5, [cc_tool("b", "Bash", {"command": "go test"})])],
           pasta=wf)
    # agente que o journal ainda nao registrou: a funcao vem da workflowPhase do meta, nao do rotulo
    cc_sub("cc-mae", "w3", {"agentType": "workflow-subagent", "description": "ticket 02", "workflowPhase": "Revisao"},
           [cc_user(20, "ticket 02")], pasta=wf)
    # workflow que acabou agora: nome pelo workflows/<runId>.json, fase concluido
    wf2 = "subagents/workflows/wf_456"
    grava(".claude/projects/-work-alpha/cc-mae/%s/journal.jsonl" % wf2,
          [{"type": "started", "agentId": "v1", "label": "revisa", "phase": "Revisao"}])
    cc_sub("cc-mae", "v1", {"agentType": "workflow-subagent"}, [cc_user(200, "revise"),
                                                                  cc_assist(190, [{"type": "text", "text": "..."}])], pasta=wf2)
    grava(".claude/projects/-work-alpha/cc-mae/workflows/wf_456.json",
          [{"runId": "wf_456", "workflowName": "revisao", "status": "completed"}], fim="")


def testes_subagentes(porta):
    e = pede(porta, "/api/estado")[1]
    mae = por_id(e).get("claude-code:cc-mae", {})
    subs = {s["nome"]: s for s in mae.get("subagentes", [])}
    ex = subs.get("opus-Explore", {})
    confere(ex.get("status") == "trabalhando" and ex.get("funcao") == "Mapeia transcripts"
            and ex.get("modelo") == "claude-opus-5-5" and (ex.get("agora") or {}).get("nome") == "Read",
            "subagente trabalhando com nome, funcao, modelo e ferramenta (%r)" % ex)
    confere(ex.get("prompt") == "mapeie os transcripts", "o prompt do subagente e o primeiro registro")
    confere(subs.get("opus-Painel", {}).get("status") == "finalizado", "TaskStop pelo name termina o subagente")
    confere(subs.get("general-purpose", {}).get("status") == "finalizado",
            "task-notification killed (queue-operation) termina o subagente sem name")
    confere(subs.get("opus-Avisado", {}).get("status") == "finalizado",
            "task-notification como mensagem user termina o subagente (%r)" % subs.get("opus-Avisado", {}).get("status"))
    confere(subs.get("opus-Spec", {}).get("status") == "finalizado", "turno fechado finaliza o subagente")
    confere("velho" not in subs, "subagente terminado ha mais de 30 min sai da tabela")
    fx = subs.get("fable-X", {})
    confere(fx.get("modelo") == "claude-fable-5-1", "modelo do meta quando o transcript nao tem (%r)" % fx.get("modelo"))
    confere(fx.get("janela") == 200000, "modelo fora da familia do [1m]: janela de 200 mil (%r)" % fx.get("janela"))
    ids = [s["id"] for s in mae.get("subagentes", [])]
    confere(len(set(ids)) == len(ids) and all(i.startswith("claude-code:cc-mae:") for i in ids),
            "ids unicos, prefixados pela sessao")
    wfs = {w["id"]: w for w in mae.get("workflows", [])}
    w = wfs.get("wf_123", {})
    ags = {a["nome"]: a for a in w.get("agentes", [])}
    confere(w.get("nome") == "leva-tickets" and w.get("fase") == "Reparo",
            "workflow com o nome da ferramenta e a fase atual (%r)" % {k: w.get(k) for k in ("nome", "fase")})
    confere(ags.get("ticket 01", {}).get("status") == "finalizado" and ags.get("ticket 01", {}).get("funcao") == "Implementacao",
            "agente do workflow com rotulo e fase, terminado pelo result do journal")
    confere(ags.get("reparo 01 (1)", {}).get("status") == "trabalhando", "agente do workflow trabalhando")
    confere("ticket 00" not in ags, "agente de workflow terminado ha mais de 30 min sai da tabela (%r)" % list(ags))
    confere(ags.get("workflow-subagent", {}).get("funcao") == "Revisao",
            "sem registro no journal, a funcao do agente de workflow e a workflowPhase (%r)"
            % ags.get("workflow-subagent", {}).get("funcao"))
    w2 = wfs.get("wf_456", {})
    confere(w2.get("nome") == "revisao" and w2.get("fase") == "concluído"
            and all(a["status"] == "finalizado" for a in w2.get("agentes", [])),
            "workflow terminado: nome do runId.json, fase concluido, agentes finalizados (%r)" % w2)

    st, ev = pede(porta, "/api/eventos?agente=claude-code:cc-busy")
    evs = ev.get("eventos", []) if st == 200 else []
    confere(st == 200 and ev.get("agente") == "claude-code:cc-busy", "eventos de um agente")
    confere([x["tipo"] for x in evs][:1] == ["usuario"] and evs[-1]["nome"] == "Bash" and "pytest" in evs[-1]["texto"],
            "eventos do mais antigo ao mais novo (%r)" % [(x["tipo"], x["nome"]) for x in evs])
    st, ev = pede(porta, "/api/eventos?agente=" + ids[0]) if ids else (0, {})
    confere(st == 200 and ev.get("eventos"), "eventos de um subagente")
    st, _ = pede(porta, "/api/eventos?agente=nao-existe")
    confere(st == 404, "agente desconhecido: 404")


def monta_espera_e_falha():
    d = "claude-desktop"
    # pergunta pendente, Plano pelo ultimo TodoWrite e falha mais recente
    cc_sessao("cc-pergunta", [
        cc_user(200, "decide o layout", entrypoint=d),
        cc_assist(190, [cc_tool("td1", "TodoWrite", {"todos": [{"content": "velho", "status": "pending"}]})], entrypoint=d),
        cc_result(189, "td1", entrypoint=d),
        cc_assist(180, [cc_tool("td2", "TodoWrite", {"todos": [
            {"content": "ler transcripts", "status": "completed"},
            {"content": "montar a tela", "status": "in_progress"},
            {"content": "testar", "status": "pending"}]})], entrypoint=d),
        cc_result(179, "td2", entrypoint=d),
        cc_assist(170, [cc_tool("b1", "Bash", {"command": "go test"})], entrypoint=d),
        cc_result(160, "b1", "FAIL TestAntigo", erro=True, entrypoint=d),
        cc_assist(150, [cc_tool("b2", "Bash", {"command": "go vet"})], entrypoint=d),
        cc_result(140, "b2", "FAIL TestNovo", erro=True, entrypoint=d),
        cc_assist(130, [cc_tool("q1", "AskUserQuestion", {"questions": [{"question": "A ou B?"}]})], entrypoint=d),
    ])
    # pergunta respondida nao prende em esperando
    cc_sessao("cc-respondida", [
        cc_user(60, "outra", entrypoint=d),
        cc_assist(50, [cc_tool("q2", "AskUserQuestion", {"questions": []})], entrypoint=d),
        cc_result(40, "q2", "A", entrypoint=d),
        cc_assist(30, [{"type": "text", "text": "seguindo com A"}], entrypoint=d),
    ])
    # falha de um turno anterior nao fica no agente
    cc_sessao("cc-falha-velha", [
        cc_user(300, "roda o make", entrypoint=d),
        cc_assist(290, [cc_tool("b3", "Bash", {"command": "make"})], entrypoint=d),
        cc_result(280, "b3", "FAIL velho", erro=True, entrypoint=d),
        cc_user(100, "de novo", entrypoint=d),
        cc_assist(90, [{"type": "text", "text": "pronto"}], entrypoint=d),
        cc_fim(85),
    ])
    # dentro de 48 h mas fora de 24 h
    velho = cc_sessao("cc-ontem", [cc_user(30 * 3600, "ontem", entrypoint=d), cc_fim(30 * 3600 - 10)])
    os.utime(velho, (time.time() - 30 * 3600, time.time() - 30 * 3600))


def testes_espera_e_falha(porta):
    e = pede(porta, "/api/estado")[1]
    s = por_id(e)
    p = s.get("claude-code:cc-pergunta", {})
    confere(p.get("status") == "esperando" and p.get("pergunta") is True, "pergunta pendente: esperando com pergunta")
    confere(p.get("fazendo") == "A ou B?", "esperando com pergunta: fazendo e a pergunta (%r)" % p.get("fazendo"))
    confere(s.get("claude-code:cc-respondida", {}).get("status") == "trabalhando"
            and s.get("claude-code:cc-respondida", {}).get("pergunta") is False, "pergunta respondida solta o agente")
    plano = p.get("plano") or {}
    confere([i.get("estado") for i in plano.get("itens", [])] == ["feito", "fazendo", "pendente"]
            and plano.get("proximo") == "montar a tela", "Plano do ultimo TodoWrite com o proximo passo (%r)" % plano)
    confere(s.get("claude-code:cc-busy", {}).get("plano") is None, "sem TodoWrite, sem Plano")

    confere("atencao" not in e, "sem lista a parte de quem espera: a sessao esperando vem no topo da lista")
    feed = e.get("atividade") or []
    ids = {x["id"] for x in e["sessoes"]}
    quando = [datetime.fromisoformat(x["ts"].replace("Z", "+00:00")) for x in feed]
    confere(0 < len(feed) <= 30 and quando == sorted(quando, reverse=True)
            and all(x["tipo"] != "resultado" and x["sessao"] in ids and x["projeto"] for x in feed),
            "feed de atividade: ate 30 eventos das sessoes abertas, do mais novo ao mais antigo, sem as saidas (%d)" % len(feed))
    confere(all(x["status"] != "encerrada" for x in e["sessoes"]), "sessao encerrada nao aparece no estado")
    rank = {"esperando": 0, "trabalhando": 1, "finalizado": 2}
    ordem = [min(rank[a["status"]] for a in [x] + x["subagentes"] + [b for w in x["workflows"] for b in w["agentes"]])
             for x in e["sessoes"]]
    confere(ordem == sorted(ordem) and ordem[0] == 0, "sessoes por urgencia: esperando, trabalhando, finalizado (%r)" % ordem)
    par = s.get("claude-code:cc-parada", {})
    confere(par.get("status") == "esperando" and par.get("pergunta") is False
            and s.get("claude-code:cc-idle", {}).get("status") != "esperando",
            "quem travou fica esperando, sem pergunta; quem finalizou nao (%r)" % par.get("status"))
    f = p.get("falha") or {}
    confere(f.get("texto") == "FAIL TestNovo" and f.get("ts"),
            "falha mais recente do turno no proprio agente, fora da lateral (%r)" % f)
    velha = s.get("claude-code:cc-falha-velha")
    confere(velha is not None and velha.get("falha") is None, "falha de turno anterior sai do agente (%r)" % velha)

    confere("claude-code:cc-ontem" not in s, "janela padrao de 24 h exclui a sessao de ontem")
    cc = [h for h in e["harnesses"] if h["id"] == "claude-code"][0]
    confere(cc["ativas"] == sum(1 for x in e["sessoes"] if x["harness"] == "claude-code") and "encerradas" not in cc,
            "o harness conta so as sessoes abertas (%r)" % cc)
    evs = pede(porta, "/api/eventos?agente=claude-code:cc-pergunta")[1].get("eventos", [])
    par = [x for x in evs if x.get("id") == "b1"]
    confere([x["tipo"] for x in par] == ["ferramenta", "erro"] and par[0].get("completo") == "go test"
            and par[1].get("completo") == "FAIL TestAntigo",
            "chamada e resultado levam o mesmo id e o texto inteiro, para a linha expandida (%r)" % par)


# ---------------------------------------------------------------- Codex

def cx(atras, tipo, payload):
    return {"timestamp": ts(atras), "type": tipo, "payload": payload}


def cx_msg(atras, papel, texto):
    blocos = [{"type": "input_text" if papel == "user" else "output_text", "text": texto}]
    return cx(atras, "response_item", {"type": "message", "role": papel, "content": blocos})


def cx_call(atras, cid, nome, args):
    return cx(atras, "response_item", {"type": "function_call", "name": nome, "arguments": json.dumps(args),
                                       "call_id": cid})


def cx_out(atras, cid, saida, tipo="function_call_output"):
    return cx(atras, "response_item", {"type": tipo, "call_id": cid, "output": saida})


def monta_codex():
    import sqlite3
    agora_ms = int(time.time() * 1000)
    threads, arestas = [], []

    def thread(tid, atras_ms, regs, **extra):
        caminho = grava(".codex/sessions/2026/09/26/rollout-%s.jsonl" % tid, regs)
        prefixo = "\\\\?\\" if os.name == "nt" else ""
        d = {"id": tid, "rollout_path": prefixo + caminho, "title": "titulo " + tid, "model": "gpt-5.4",
             "updated_at_ms": agora_ms - atras_ms, "archived": 0, "cwd": "/work/beta",
             "agent_nickname": None, "agent_role": None, "source": "cli"}
        d.update(extra)
        threads.append(d)
        return caminho

    trab = thread("cx-trab", 10 * 1000, [
        cx(400, "session_meta", {"id": "cx-trab", "cwd": "/work/beta"}),
        cx(400, "event_msg", {"type": "task_started"}),
        cx_msg(399, "user", "# AGENTS.md instructions for /work/beta\n\n<INSTRUCTIONS>regras</INSTRUCTIONS>"),
        cx_msg(399, "user", "<environment_context>cwd</environment_context>"),
        cx_msg(398, "user", "corrige o bug do parser"),
        cx(397, "turn_context", {"model": "gpt-5.6-terra", "cwd": "/work/beta/sub", "effort": "medium"}),
        cx_msg(335, "assistant", "vou corrigir o parser e rodar os testes"),
        cx_call(330, "p1", "update_plan", {"plan": [{"step": "ler o parser", "status": "completed"},
                                                    {"step": "corrigir", "status": "in_progress"},
                                                    {"step": "testar", "status": "pending"}]}),
        cx_out(329, "p1", "Plan updated"),
        cx_call(310, "c1", "shell_command", {"command": "cargo build"}),
        cx_out(300, "c1", "Exit code: 1\nWall time: 2 seconds\nOutput:\nerror[E0425]"),
        cx(295, "response_item", {"type": "custom_tool_call", "call_id": "c2", "name": "apply_patch",
                                 "input": "*** Begin Patch"}),
        cx_out(290, "c2", json.dumps({"output": "patch falhou", "metadata": {"exit_code": 2}}),
               tipo="custom_tool_call_output"),
        "\x00\x00\x00 lixo que nao e json",
        cx(50, "event_msg", {"type": "token_count", "info": {"last_token_usage": {"input_tokens": 5000},
                                                             "model_context_window": 258400},
                             "rate_limits": {"primary": {"used_percent": 25.0, "window_minutes": 43200,
                                                         "resets_at": int(time.time()) + 86400},
                                             "secondary": {"used_percent": 80.0, "window_minutes": 300,
                                                           "resets_at": int(time.time()) - 60}}}),
        cx_call(15, "c3", "shell_command", {"command": "cargo test"}),
    ])
    velho = time.time() - 2 * 86400
    os.utime(trab, (velho, velho))  # no Windows o mtime do rollout atrasa: a atividade vem do indice
    thread("cx-filho", 20 * 1000, [
        cx(100, "session_meta", {"id": "cx-filho", "cwd": "/work/beta"}),
        cx(100, "event_msg", {"type": "task_started"}),
        cx_msg(99, "user", "corrige o bug do parser"),
        cx_msg(98, "user", "Tarefa: vasculhar os testes"),
        cx_msg(40, "assistant", "achei dois testes quebrados"),
        cx(30, "event_msg", {"type": "task_complete"}),
    ], agent_nickname="Einstein")
    arestas.append(("cx-trab", "cx-filho", "open"))
    thread("cx-esp", 5 * 60 * 1000, [
        cx(400, "session_meta", {"id": "cx-esp", "cwd": "/work/gama"}),
        cx(400, "event_msg", {"type": "task_started"}), cx_msg(399, "user", "explica o modulo"),
        cx_msg(310, "assistant", "explicado"), cx(300, "event_msg", {"type": "task_complete"}),
    ])
    thread("cx-enc", 40 * 60 * 1000, [
        cx(2500, "session_meta", {"id": "cx-enc", "cwd": "/work/gama"}),
        cx(2500, "event_msg", {"type": "task_started"}), cx_msg(2499, "user", "coisa antiga"),
        cx(2400, "event_msg", {"type": "turn_aborted"}),
    ])
    thread("cx-limite", 10 * 60 * 1000, [
        cx(620, "session_meta", {"id": "cx-limite", "cwd": "/work/gama"}),
        cx(620, "event_msg", {"type": "task_started"}), cx_msg(619, "user", "roda ls"),
        cx(610, "event_msg", {"type": "task_complete", "last_agent_message": None,
                               "error": {"message": "You have hit your usage limit."}}),
    ])
    # codex exec: a execucao sem interacao acaba com o turno; a janela de encerrada e curta
    for tid, atras in (("cx-exec", 300), ("cx-exec-novo", 60)):
        thread(tid, atras * 1000, [
            cx(atras + 10, "session_meta", {"id": tid, "cwd": "/work/gama"}),
            cx(atras + 10, "event_msg", {"type": "task_started"}), cx_msg(atras + 9, "user", "roda ls sem interacao"),
            cx_msg(atras + 1, "assistant", "4 arquivos"), cx(atras, "event_msg", {"type": "task_complete"}),
        ], source="exec")
    thread("cx-arq", 60 * 1000, [cx(60, "session_meta", {"id": "cx-arq", "cwd": "/w"})], archived=1)
    thread("cx-velho", 30 * 3600 * 1000, [cx(30 * 3600, "session_meta", {"id": "cx-velho", "cwd": "/w"})])

    con = sqlite3.connect(os.path.join(HOME, ".codex", "state_5.sqlite"))
    con.execute("create table threads (id text, rollout_path text, title text, model text, updated_at_ms integer,"
                " archived integer, cwd text, agent_nickname text, agent_role text, source text)")
    con.execute("create table thread_spawn_edges (parent_thread_id text, child_thread_id text, status text)")
    con.executemany("insert into threads values (:id, :rollout_path, :title, :model, :updated_at_ms, :archived,"
                    " :cwd, :agent_nickname, :agent_role, :source)", threads)
    con.executemany("insert into thread_spawn_edges values (?, ?, ?)", arestas)
    con.commit()
    con.close()


def testes_codex(porta):
    e = pede(porta, "/api/estado")[1]
    s = por_id(e)
    t = s.get("codex:cx-trab", {})
    confere(t.get("status") == "trabalhando" and t.get("projeto") == "beta",
            "Codex trabalhando pelo indice, mesmo com o mtime velho (%r)" % {k: t.get(k) for k in ("status", "projeto")})
    confere(t.get("prompt") == "corrige o bug do parser", "contexto injetado do Codex nao e prompt (%r)" % t.get("prompt"))
    confere(t.get("modelo") == "gpt-5.6-terra" and t.get("tokens") == 5000 and t.get("titulo") == "titulo cx-trab"
            and t.get("janela") == 258400, "modelo do turn_context, tokens e janela do token_count e titulo do indice")
    confere((t.get("agora") or {}).get("nome") == "shell_command" and "cargo test" in t["agora"]["resumo"],
            "ferramenta atual do Codex")
    confere((t.get("plano") or {}).get("proximo") == "corrigir", "Plano do update_plan com o proximo passo")
    confere(t.get("fazendo") == "vou corrigir o parser e rodar os testes",
            "fazendo do Codex e a fala do modelo, nao o comando (%r)" % t.get("fazendo"))
    filhos = t.get("subagentes", [])
    confere(len(filhos) == 1 and filhos[0].get("nome") == "Einstein" and filhos[0].get("status") == "finalizado"
            and filhos[0].get("prompt") == "Tarefa: vasculhar os testes", "subagente do Codex pela aresta (%r)" % filhos)
    confere("codex:cx-filho" not in s, "o subagente nao aparece como sessao")
    confere(s.get("codex:cx-esp", {}).get("status") == "finalizado", "Codex, turno fechado ha 5 min: finalizado")
    confere("codex:cx-enc" not in s, "Codex, turno abortado ha 40 min: encerrada, fora do painel")
    confere("codex:cx-arq" not in s and "codex:cx-velho" not in s, "arquivada e fora da janela nao aparecem")
    evs = pede(porta, "/api/eventos?agente=codex:cx-trab")[1].get("eventos", [])
    erros = [x["texto"] for x in evs if x["tipo"] == "erro"]
    confere(len(erros) == 2 and "error[E0425]" in erros[0] and "patch falhou" in erros[1],
            "falha por Exit code e por exit_code (%r)" % erros)
    lim = s.get("codex:cx-limite", {})
    evs_lim = pede(porta, "/api/eventos?agente=codex:cx-limite")[1].get("eventos", [])
    confere(lim.get("status") == "finalizado" and any(x["tipo"] == "erro" and "usage limit" in x["texto"] for x in evs_lim),
            "erro que encerra o turno do Codex vira evento de erro (%r)" % [(x["tipo"], x["texto"][:30]) for x in evs_lim])
    cx_h = [h for h in e["harnesses"] if h["id"] == "codex"][0]
    confere("codex:cx-exec" not in s, "codex exec com o turno fechado ha 5 min: encerrada, fora do painel")
    confere(s.get("codex:cx-exec-novo", {}).get("status") == "finalizado",
            "codex exec com o turno fechado ha 1 min: finalizado, ainda nao encerrada (%r)" % s.get("codex:cx-exec-novo", {}).get("status"))
    confere(cx_h["ativas"] == 4 and "encerradas" not in cx_h, "Codex com 4 sessoes abertas (%r)" % cx_h)


# ---------------------------------------------------------------- Pi

def pi_msg(atras, papel, conteudo, **extra):
    m = {"role": papel, "content": conteudo}
    m.update(extra)
    return {"type": "message", "id": "m%d" % atras, "timestamp": ts(atras), "message": m}


def pi_sessao(nome, regs):
    return grava(".pi/agent/sessions/--work-delta--/2026-09-26T00-00-00-000Z_%s.jsonl" % nome, regs)


def monta_pi():
    mae = pi_sessao("pi-mae", [
        {"type": "session", "version": 3, "id": "pi-mae", "timestamp": ts(300), "cwd": "/work/delta"},
        {"type": "model_change", "timestamp": ts(300), "provider": "deepseek", "modelId": "deepseek-flash"},
        {"type": "session_info", "timestamp": ts(299), "name": "sessao do pi"},
        pi_msg(290, "user", [{"type": "text", "text": "revisa o pi"}]),
        pi_msg(280, "assistant", [{"type": "text", "text": "rodando"},
                                 {"type": "toolCall", "id": "k1", "name": "bash", "arguments": {"command": "npm test"}}],
               stopReason="toolUse", model="deepseek-pro",
               usage={"input": 1000, "output": 10, "cacheRead": 500, "cacheWrite": 0}),
        pi_msg(270, "toolResult", [{"type": "text", "text": "1 failing"}], toolCallId="k1", toolName="bash", isError=True),
        pi_msg(260, "assistant", [{"type": "text", "text": "um teste falhou"}], stopReason="stop"),
    ])
    pi_sessao("pi-filho", [
        {"type": "session", "version": 3, "id": "pi-filho", "timestamp": ts(95), "cwd": "/work/delta",
         "parentSession": mae},
        pi_msg(94, "user", [{"type": "text", "text": "sub tarefa pelo caminho"}]),
        pi_msg(85, "assistant", [{"type": "text", "text": "feito"}], stopReason="stop"),
    ])
    pi_sessao("pi-filho2", [
        {"type": "session", "version": 3, "id": "pi-filho2", "timestamp": ts(40), "cwd": "/work/delta",
         "parentSession": "pi-mae"},
        pi_msg(30, "user", [{"type": "text", "text": "sub tarefa pelo id"}]),
        pi_msg(20, "assistant", [{"type": "toolCall", "id": "k2", "name": "read", "arguments": {"path": "a.ts"}}],
               stopReason="toolUse"),
    ])
    pi_sessao("pi-neto", [
        {"type": "session", "version": 3, "id": "pi-neto", "timestamp": ts(25), "cwd": "/work/delta",
         "parentSession": "pi-filho2"},
        pi_msg(24, "user", [{"type": "text", "text": "sub tarefa do neto"}]),
        pi_msg(15, "assistant", [{"type": "toolCall", "id": "k3", "name": "grep", "arguments": {"pattern": "x"}}],
               stopReason="toolUse"),
    ])
    pi_sessao("pi-velha", [
        {"type": "session", "version": 3, "id": "pi-velha", "timestamp": ts(3000), "cwd": "/work/delta"},
        pi_msg(2990, "user", [{"type": "text", "text": "antiga"}]),
        pi_msg(2400, "assistant", [{"type": "text", "text": "ok"}], stopReason="stop"),
    ])


def testes_pi(porta):
    e = pede(porta, "/api/estado")[1]
    s = por_id(e)
    m = s.get("pi:pi-mae", {})
    confere(m.get("status") == "finalizado" and m.get("projeto") == "delta" and m.get("titulo") == "sessao do pi"
            and m.get("fazendo") == "um teste falhou", "sessao do Pi finalizada, com projeto, titulo e a ultima fala (%r)"
            % {k: m.get(k) for k in ("status", "projeto", "titulo", "fazendo")})
    confere(m.get("modelo") == "deepseek-pro" and m.get("tokens") == 1500 and m.get("prompt") == "revisa o pi",
            "modelo da mensagem, tokens com cache e prompt do Pi")
    subs = {x["prompt"]: x for x in m.get("subagentes", [])}
    confere(subs.get("sub tarefa pelo caminho", {}).get("status") == "finalizado",
            "subagente do Pi pelo parentSession com caminho, finalizado")
    confere(subs.get("sub tarefa pelo id", {}).get("status") == "trabalhando",
            "subagente do Pi pelo parentSession com id, trabalhando (%r)" % list(subs))
    confere("pi:pi-filho" not in s and "pi:pi-filho2" not in s, "subagente do Pi nao aparece como sessao")
    neto = subs.get("sub tarefa do neto", {})
    confere(neto.get("id") == "pi:pi-mae:pi-neto" and neto.get("status") == "trabalhando" and "pi:pi-neto" not in s,
            "subagente de subagente do Pi sobe ate a sessao de topo (%r)" % list(subs))
    confere("pi:pi-velha" not in s, "Pi, turno fechado ha 40 min: encerrada, fora do painel")
    confere(m.get("janela") is None, "Pi sem janela de contexto conhecida")
    evs = pede(porta, "/api/eventos?agente=pi:pi-mae")[1].get("eventos", [])
    confere([x["texto"] for x in evs if x["tipo"] == "erro"] == ["1 failing"], "falha do Pi pelo isError")


# ---------------------------------------------------------------- Antigravity CLI (agy)

AGY_A = "aaaaaaaa-0000-4000-8000-000000000001"
AGY_B = "bbbbbbbb-0000-4000-8000-000000000002"
AGY_C = "cccccccc-0000-4000-8000-000000000003"
AGY_D = "dddddddd-0000-4000-8000-000000000004"
AGY_E = "eeeeeeee-0000-4000-8000-000000000005"
AGY_F = "ffffffff-0000-4000-8000-000000000006"
AGY_G = "99999999-0000-4000-8000-000000000007"


def ag(atras, passo, tipo, **extra):
    d = datetime.fromtimestamp(time.time() - atras, timezone.utc)
    r = {"step_index": passo, "source": "MODEL", "type": tipo, "status": "DONE",
         "created_at": d.strftime("%Y-%m-%dT%H:%M:%SZ")}
    r.update(extra)
    return r


def ag_pede(atras, passo, texto):
    return ag(atras, passo, "USER_INPUT", source="USER_EXPLICIT",
              content="<USER_REQUEST>\n%s\n</USER_REQUEST>\n<ADDITIONAL_METADATA>\nhora local\n</ADDITIONAL_METADATA>" % texto)


def ag_chama(atras, passo, nome, args, texto=None):
    r = ag(atras, passo, "PLANNER_RESPONSE", tool_calls=[{"name": nome, "args": args}])
    if texto:
        r["content"] = texto
    return r


def monta_agy():
    import sqlite3
    base = ".gemini/antigravity-cli"

    def conversa(cid, regs):
        grava("%s/brain/%s/.system_generated/logs/transcript_full.jsonl" % (base, cid), regs)

    conversa(AGY_A, [
        ag_pede(1000, 0, "implementa a aba de PRs"),
        ag(999, 1, "EPHEMERAL_MESSAGE", source="SYSTEM", content="nao sou o usuario"),
        ag_chama(990, 2, "run_command", {"CommandLine": "pytest", "toolSummary": "roda os testes"}),
        ag(985, 3, "RUN_COMMAND", status="ERROR", content="Encountered error in step execution: exit 1"),
        ag_chama(950, 4, "invoke_subagent", {"toolSummary": "Despacha o backend", "Subagents": [
            {"Model": "inherit", "Prompt": "faz o backend", "Role": "Backend Implementer", "TypeName": "self"}]}),
        ag(949, 5, "GENERIC", content='Created the following subagents:\n{\n  "conversationId":  "%s",\n'
                                       '  "workspaceUris": ["file:///D:/workspace/sigad-web"]\n}\n' % AGY_B),
        ag_chama(940, 6, "ask_question", {"questions": [{"question": "A ou B?"}], "toolSummary": "pergunta o foco"}),
    ])
    conversa(AGY_B, [
        ag_pede(945, 0, "tarefas 1 e 2"),
        ag_chama(20, 1, "view_file", {"AbsolutePath": "/x/pr_service.py", "toolSummary": "le o servico"}),
    ])
    conversa(AGY_C, [ag_pede(3000, 0, "antiga"), ag(2400, 1, "PLANNER_RESPONSE", content="pronto")])
    conversa(AGY_D, [ag_pede(100, 0, "sem history"), ag(90, 1, "PLANNER_RESPONSE", content="ok")])
    conversa(AGY_E, [ag_pede(80, 0, "rodado com -p"), ag(70, 1, "PLANNER_RESPONSE", content="ok")])
    # modo -p: a permissao negada encerra a execucao sem resposta final; o banco diz que o turno acabou
    conversa(AGY_F, [ag_pede(600, 0, "negado"), ag_chama(590, 1, "run_command", {"CommandLine": "ls"}),
                     ag(589, 2, "GENERIC", status="ERROR", content="permission check failed")])
    # agy -p: o log da execucao diz "Print mode"; a janela de encerrada e curta
    conversa(AGY_G, [ag_pede(320, 0, "outro -p"), ag(300, 1, "PLANNER_RESPONSE", content="ok")])
    for nome, conversa_id in (("cli-20260926_000000.log", AGY_E), ("cli-20260926_000100.log", AGY_G)):
        grava("%s/log/%s" % (base, nome), [
            "I0926 08:48:55.024278 1 server.go:315] Creating CLI server backend: product=antigravity "
            r"workspaceDirs=[D:\workspace\monitor] appDataDir=C:\x cascadeManager=true",
            'I0926 08:48:56.230251 1 printmode.go:181] Print mode: starting (promptLength=79, model="")',
            "I0926 08:48:59.319606 1 server.go:1239] Created conversation %s" % conversa_id,
        ])
    grava("%s/history.jsonl" % base, [
        {"display": "sem id", "timestamp": 1, "workspace": "D:\\outro"},
        {"conversationId": AGY_A, "display": "implementa", "timestamp": 2, "workspace": "D:\\workspace\\sigad-web"},
        {"conversationId": AGY_C, "display": "antiga", "timestamp": 3, "workspace": "D:\\workspace\\velho"},
    ])
    con = sqlite3.connect(os.path.join(HOME, *base.split("/"), "conversation_summaries.db"))
    con.execute("create table conversation_summaries (conversation_id text, title text, workspace_uris text,"
                " status text, not_fully_idle integer)")
    run, idle = "CASCADE_RUN_STATUS_RUNNING", "CASCADE_RUN_STATUS_IDLE"
    con.executemany("insert into conversation_summaries values (?, ?, ?, ?, ?)",
                    [(AGY_A, "Aba de PRs", "", idle, 0), (AGY_B, "", "", run, 1), (AGY_C, "Velha", "", idle, 0),
                     (AGY_D, "", '["file:///D:/workspace%20intelliJ/sol"]', idle, 0), (AGY_E, "", "", idle, 0),
                     (AGY_F, "Negado", "", idle, 0), (AGY_G, "", "", idle, 0)])
    con.commit()
    con.close()
    os.makedirs(os.path.join(HOME, *base.split("/"), "conversations"), exist_ok=True)
    for cid, modelo in ((AGY_A, b"gemini-3.1-pro"), (AGY_B, b"gemini-3.8-flash-high")):
        con = sqlite3.connect(os.path.join(HOME, *base.split("/"), "conversations", cid + ".db"))
        con.execute("create table executor_metadata (idx integer, data blob)")
        con.execute("insert into executor_metadata values (1, ?)", (b"\x00\x12claude-velho\x00",))
        con.execute("insert into executor_metadata values (2, ?)", (b"\x08\x01\x12\x0e" + modelo + b"\x1a\x02ok",))
        con.commit()
        con.close()


def testes_agy(porta):
    e = pede(porta, "/api/estado")[1]
    s = por_id(e)
    a = s.get("agy:" + AGY_A, {})
    confere(a.get("status") == "esperando" and a.get("pergunta") is True,
            "agy com ask_question pendente: esperando com pergunta (%r)" % a.get("status"))
    confere(a.get("prompt") == "implementa a aba de PRs", "prompt do agy sem a marcacao USER_REQUEST (%r)" % a.get("prompt"))
    confere(a.get("titulo") == "Aba de PRs" and a.get("projeto") == "sigad-web" and a.get("modelo") == "gemini-3.1-pro",
            "titulo, projeto pelo history.jsonl e modelo do blob (%r)"
            % {k: a.get(k) for k in ("titulo", "projeto", "modelo")})
    subs = a.get("subagentes", [])
    b = subs[0] if subs else {}
    confere(len(subs) == 1 and b.get("funcao") == "Backend Implementer" and b.get("status") == "trabalhando"
            and b.get("modelo") == "gemini-3.8-flash-high" and (b.get("agora") or {}).get("resumo") == "le o servico"
            and b.get("fazendo") == "le o servico",
            "subagente do agy pelo invoke_subagent (%r)" % subs)
    confere("agy:" + AGY_B not in s, "subagente do agy nao aparece como sessao")
    confere("agy:" + AGY_C not in s, "agy, turno fechado ha 40 min: encerrada, fora do painel")
    confere(s.get("agy:" + AGY_D, {}).get("projeto") == "sol",
            "agy sem history.jsonl: projeto pelo workspace_uris (%r)" % s.get("agy:" + AGY_D, {}).get("projeto"))
    confere(s.get("agy:" + AGY_E, {}).get("projeto") == "monitor",
            "agy rodado com -p: projeto pelo log do CLI (%r)" % s.get("agy:" + AGY_E, {}).get("projeto"))
    confere(s.get("agy:" + AGY_F, {}).get("status") == "finalizado",
            "agy com a execucao ociosa no banco: turno fechado, finalizado (%r)" % s.get("agy:" + AGY_F, {}).get("status"))
    evs = pede(porta, "/api/eventos?agente=agy:" + AGY_A)[1].get("eventos", [])
    confere(any(x["tipo"] == "erro" and "exit 1" in x["texto"] for x in evs), "falha do agy pelo status ERROR")
    confere(not any("nao sou o usuario" in x["texto"] for x in evs), "mensagem de sistema do agy ignorada")
    h = [x for x in e["harnesses"] if x["id"] == "agy"][0]
    confere(s.get("agy:" + AGY_E, {}).get("status") == "finalizado",
            "agy -p com o turno fechado ha pouco mais de 1 min: finalizado, ainda nao encerrada")
    confere("agy:" + AGY_G not in s, "agy -p com o turno fechado ha 5 min: encerrada, fora do painel")
    confere(h["ativas"] == 4 and "encerradas" not in h, "agy com 4 sessoes abertas (%r)" % h)


def por_id(estado):
    return {s["id"]: s for s in estado["sessoes"]}


def testes_claude(porta):
    st, e = pede(porta, "/api/estado")
    confere(st == 200, "GET /api/estado responde 200")
    confere([h["id"] for h in e["harnesses"]] == ["claude-code", "codex", "agy", "pi"],
            "faixa traz os quatro harnesses na ordem")
    s = {x["id"].split(":", 1)[1]: x for x in e["sessoes"] if x["harness"] == "claude-code"}

    b = s.get("cc-busy") or {}
    confere(b.get("status") == "trabalhando", "sinal de vida busy: trabalhando")
    confere(b.get("projeto") == "alpha", "projeto e a pasta de abertura, nao o cd (%r)" % b.get("projeto"))
    confere(b.get("titulo") == "minha sessao", "custom-title vence o name da sessao viva")
    confere(b.get("modelo") == "claude-opus-5-5", "modelo ignora <synthetic> (%r)" % b.get("modelo"))
    confere(b.get("tokens") == 1000, "tokens da ultima resposta, sem somar os blocos repetidos (%r)" % b.get("tokens"))
    confere(b.get("prompt") == "implementa o parser", "prompt do turno")
    confere((b.get("agora") or {}).get("nome") == "Bash" and "pytest" in (b.get("agora") or {}).get("resumo", ""),
            "agora mostra a ferramenta sem resultado")
    confere(b.get("fazendo") == "vou rodar os testes", "fazendo e a fala do modelo no turno, nao o comando (%r)" % b.get("fazendo"))
    confere(b.get("subagentes") == [] and b.get("workflows") == [] and b.get("janela") == 1000000,
            "sessao viva sem subagentes nem workflows, janela de 1M pelo [1m] do settings (%r)" % b.get("janela"))
    confere(e.get("versao"), "o estado diz a versao do monitor")
    evs = pede(porta, "/api/eventos?agente=claude-code:cc-ferramenta")[1].get("eventos", [])
    t9 = [x for x in evs if x.get("id") == "t9"]
    confere(t9 and t9[0].get("descricao") == "Roda a suite" and t9[0].get("completo") == "make test",
            "a chamada guarda a descricao e o comando inteiro (%r)" % t9)
    evs = pede(porta, "/api/eventos?agente=claude-code:cc-resultado")[1].get("eventos", [])
    t8 = [x for x in evs if x.get("id") == "t8" and x["tipo"] == "resultado"]
    confere(t8 and len(t8[0]["completo"]) == 4000 and len(t8[0]["texto"]) <= 200,
            "a saida longa fica inteira ate 4000 caracteres, e a linha curta ate 200")
    confere("eventos" not in b and "fala" not in b, "o estado nao carrega a linha do tempo nem a fala inteira")

    confere(s.get("cc-idle", {}).get("status") == "finalizado", "sinal de vida idle: finalizado")
    confere(s.get("cc-idle", {}).get("fazendo") == "Pronto: README revisado.",
            "finalizado: fazendo e a 1a linha da resposta, sem markdown (%r)" % s.get("cc-idle", {}).get("fazendo"))
    st, ev = pede(porta, "/api/eventos?agente=claude-code:cc-idle")
    confere(st == 200 and ev.get("fala") == "**Pronto**: [README](file:///w/README.md) `revisado`.\n\n- detalhe",
            "eventos trazem a fala inteira do turno, para o bloco da conclusao (%r)" % ev.get("fala"))
    confere(s.get("cc-idle", {}).get("titulo") == "titulo da ia", "ai-title vence o name da sessao viva")
    confere(s.get("cc-nome", {}).get("titulo") == "nome-da-viva", "name da sessao viva como ultimo recurso")
    confere(s.get("cc-nome", {}).get("status") == "esperando", "sinal de vida waiting: esperando")
    orfa = s.get("cc-orfa", {})
    confere(not orfa, "arquivo de sessao viva de processo morto: encerrada, fora do painel (%r)" % orfa.get("status"))
    if os.name == "nt":
        confere("cc-reusada" not in s, "PID reaproveitado (procStart diferente): encerrada, fora do painel")
    m = s.get("cc-morta", {})
    confere(not m, "CLI sem sessao viva: encerrada, fora do painel")
    confere(s.get("cc-parada", {}).get("status") == "esperando", "turno aberto sem evento ha 5 min: travou, esperando")
    confere(s.get("cc-ferramenta", {}).get("status") == "trabalhando", "ferramenta sem resultado: trabalhando")
    confere(s.get("cc-ferramenta", {}).get("fazendo") == "Roda a suite",
            "sem fala no turno, fazendo e a descricao do passo (%r)" % s.get("cc-ferramenta", {}).get("fazendo"))
    confere(s.get("cc-resultado", {}).get("status") == "esperando", "ferramenta com resultado nao segura trabalhando")
    confere(s.get("cc-recente", {}).get("status") == "trabalhando", "evento recente: trabalhando")
    confere(s.get("cc-finalizado", {}).get("status") == "finalizado", "desktop, turno fechado ha 5 min: finalizado")
    v = s.get("cc-velha", {})
    confere(not v, "desktop, turno fechado ha 40 min: encerrada, fora do painel")
    confere(s.get("cc-ruido", {}).get("prompt") == "pedido real",
            "notificacao, outra sessao, compactacao, meta, comando local e interrupcao nao sao prompt (%r)"
            % s.get("cc-ruido", {}).get("prompt"))
    confere(s.get("cc-comando", {}).get("prompt") == "/goal rode a leva", "comando de barra vira o prompt digitado")
    confere(s.get("cc-colado", {}).get("prompt") == "/goal rode a leva colada",
            "texto colado e o prompt, sem a marcacao pasted_content (%r)" % s.get("cc-colado", {}).get("prompt"))
    nova = s.get("cc-nova", {})
    confere(nova.get("status") == "finalizado" and nova.get("projeto") == "alpha" and nova.get("titulo") == "aba nova"
            and nova.get("inicio"),
            "sessao viva sem transcript ainda aparece finalizada, com projeto e titulo (%r)"
            % {k: nova.get(k) for k in ("status", "projeto", "titulo", "inicio")})
    lo = s.get("cc-local", {})
    confere(lo.get("status") == "finalizado" and lo.get("prompt") == "/model opus" and lo.get("fazendo") is None,
            "comando local: a saida fecha o turno, o prompt e o comando digitado e o prompt novo zera a fala (%r)"
            % {k: lo.get(k) for k in ("status", "prompt", "fazendo")})
    confere(s.get("cc-bash", {}).get("status") == "finalizado",
            "modo bash: a saida fecha o turno (%r)" % s.get("cc-bash", {}).get("status"))
    confere("cc-fora" not in s, "sessao fora da janela e sem sinal de vida nao aparece")
    confere(all(x["id"].startswith("claude-code:") for x in e["sessoes"] if x["harness"] == "claude-code"),
            "id do agente prefixado pelo harness")


def testes_incremental(porta):
    caminho = cc_sessao("cc-cresce", [cc_user(40, "primeiro pedido")])
    cc_viva(666, "cc-cresce", "busy")
    pega = lambda: por_id(pede(porta, "/api/estado")[1]).get("claude-code:cc-cresce", {}).get("prompt")
    confere(pega() == "primeiro pedido", "leitura inicial")
    with open(caminho, "a", encoding="utf-8") as f:
        f.write("isto nao e json\n")
        f.write(json.dumps(cc_user(20, "segundo pedido")) + "\n")
        parcial = json.dumps(cc_user(10, "terceiro pedido"))
        f.write(parcial[:25])
    confere(pega() == "segundo pedido", "linhas novas entram; linha nao JSON pulada; parcial espera")
    with open(caminho, "a", encoding="utf-8") as f:
        f.write(parcial[25:] + "\n")
    confere(pega() == "terceiro pedido", "linha parcial entra quando completa")


def testes_arquivos(porta):
    """Os arquivos escritos no turno atual: Edit e Write contam, Read nao, o turno anterior nao, e o repetido vem uma
    vez, na posicao da ultima escrita."""
    ed = lambda tid, nome, arq: cc_assist(0, [cc_tool(tid, nome, {"file_path": arq})])
    regs = [cc_user(300, "turno velho"), ed("e0", "Edit", "/work/alpha/velho.py"), cc_result(289, "e0"), cc_fim(288),
            cc_user(100, "turno novo"), ed("e1", "Edit", "/work/alpha/a.py"), cc_result(89, "e1"),
            ed("e2", "Read", "/work/alpha/lido.py"), cc_result(85, "e2"), ed("e3", "Write", "/work/alpha/b.md"),
            cc_result(79, "e3"), ed("e4", "Edit", "/work/alpha/a.py"), cc_result(69, "e4"), cc_fim(60)]
    for r, atras in zip(regs, (300, 290, 289, 288, 100, 90, 89, 86, 85, 80, 79, 70, 69, 60)):
        r["timestamp"] = ts(atras)
    cc_sessao("cc-arquivos", regs)
    cc_viva(667, "cc-arquivos", "idle")
    pede(porta, "/api/estado")  # indexa a sessao nova
    arq = pede(porta, "/api/eventos?agente=claude-code:cc-arquivos")[1].get("arquivos")
    esperado = [os.path.normpath("/work/alpha/a.py"), os.path.normpath("/work/alpha/b.md")]
    confere(arq == esperado, "arquivos do turno: so os escritos, do ultimo tocado ao primeiro, sem o turno velho (%r)" % arq)


def testes_janela(porta):
    """A janela de contexto do Claude: a statusline diz a % de cada sessao, e tokens / % da a janela (vale para o
    modelo inteiro); sem ela, tokens acima de 200 mil ja provam 1M. Nada passa de 100% por janela errada."""
    uso = lambda n: {"input_tokens": n, "cache_read_input_tokens": 0, "cache_creation_input_tokens": 0}
    for n, (sid, modelo, tokens) in enumerate((("cc-janela-grande", "claude-sonnet-9", 300000),
                                               ("cc-janela-sl", "claude-haiku-9", 100000),
                                               ("cc-janela-200", "claude-fable-9", 100000),
                                               ("cc-janela-irma", "claude-haiku-9", 50000))):
        cc_sessao(sid, [cc_user(60, "pedido"), cc_assist(50, [{"type": "text", "text": "ok"}], modelo=modelo, usage=uso(tokens))])
        cc_viva(680 + n, sid, "idle")
    for n, esforco in ((0, "high"), (1, "xhigh")):  # o do ultimo registro vale
        grava(".claude/projects/-work-alpha/cc-janela-sl.jsonl", [dict(cc_assist(40 - n, [{"type": "text", "text": "ok"}], modelo="claude-haiku-9",
              usage=uso(100000)), perTurnEffort=esforco)])
    s = por_id(pede(porta, "/api/estado")[1])
    confere(s.get("claude-code:cc-janela-sl", {}).get("esforco") == "xhigh" and s.get("codex:cx-trab", {}).get("esforco") == "medium",
            "esforco do turno: o do ultimo registro do Claude e o do turn_context do Codex (%r, %r)"
            % (s.get("claude-code:cc-janela-sl", {}).get("esforco"), s.get("codex:cx-trab", {}).get("esforco")))
    j = {k: s.get("claude-code:" + k, {}).get("janela") for k in ("cc-janela-grande", "cc-janela-sl", "cc-janela-200", "cc-janela-irma")}
    confere(j == {"cc-janela-grande": 1000000, "cc-janela-sl": 1000000, "cc-janela-200": 200000, "cc-janela-irma": 1000000},
            "janela: 300 mil tokens ja e 1M; o percentual da statusline da 1M ou 200 mil, e vale para o mesmo modelo (%r)" % j)


def testes_rede():
    """--rede: a maquina entra como sempre; de fora (o IP da rede local, que o servidor ve como outro cliente), so com o
    cookie da chave, que o link com ?chave= grava. Parar, so desta maquina; nome de host continua recusado."""
    s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        s.connect(("10.255.255.255", 1))
        ip = s.getsockname()[0]
    except OSError:
        ip = None
    finally:
        s.close()
    porta = porta_livre()
    rc, out = cli("--porta", str(porta), "--sem-navegador", "--rede")
    try:
        achado = re.search(rb"\?chave=([A-Za-z0-9_-]+)", out)
        chave = achado.group(1).decode() if achado else ""
        confere(rc == 0 and len(chave) >= 32 and pede(porta, "/api/saude")[1].get("rede") is True,
                "--rede sobe atendendo a rede e imprime o link com a chave (%r)" % out[-300:])
        confere(pede(porta, "/api/estado", cabecalhos={"Host": "evil.example:%d" % porta})[0] == 403,
                "na rede, nome de host continua recusado (DNS rebinding)")
        if not ip or ip.startswith("127."):
            confere(True, "sem rede local nesta maquina: pulei o acesso de fora")
            return
        de_fora = lambda rota, metodo="GET", cab=None: pede_em(ip, porta, rota, metodo, dict({"Host": "%s:%d" % (ip, porta)}, **(cab or {})))
        confere(de_fora("/")[0] == 401 and de_fora("/api/estado")[0] == 401, "de fora, sem a chave: 401")
        confere(de_fora("/?chave=errada")[0] == 401, "de fora, com a chave errada: 401")
        st, cab = de_fora("/?chave=" + chave)[0], ultimos_cabecalhos.get("Set-Cookie", "")
        confere(st == 303 and ("monitor_chave=" + chave) in cab and "HttpOnly" in cab and "SameSite=Strict" in cab,
                "o link com a chave grava o cookie e volta para / (%s %r)" % (st, cab))
        biscoito = {"Cookie": "monitor_chave=" + chave}
        confere(de_fora("/api/estado", cab=biscoito)[0] == 200 and de_fora("/", cab=biscoito)[0] == 200,
                "de fora, com o cookie: a pagina e a API respondem")
        confere(de_fora("/api/parar", "POST", dict(biscoito, **{"X-Monitor": "parar"}))[0] == 403,
                "parar so desta maquina, mesmo com o cookie")
    finally:
        cli("parar", "--porta", str(porta))


ultimos_cabecalhos = {}


def pede_em(host, porta, rota, metodo="GET", cabecalhos=None):
    """Como pede(), mas no endereco dado; guarda os cabecalhos da resposta em ultimos_cabecalhos."""
    c = http.client.HTTPConnection(host, porta, timeout=10)
    try:
        c.request(metodo, rota, headers=cabecalhos or {})
        r = c.getresponse()
        r.read()
        ultimos_cabecalhos.clear()
        ultimos_cabecalhos.update(r.getheaders())
        return r.status, None
    finally:
        c.close()


def testes_api(porta):
    st, s = pede(porta, "/api/saude")
    confere(st == 200 and s.get("monitor") is True and s.get("versao") and s.get("pid"), "saude identifica o monitor")
    st, _ = pede(porta, "/api/estado", cabecalhos={"Host": "evil.example:%d" % porta})
    confere(st == 403, "Host estranho recebe 403 (%s)" % st)
    st, _ = pede(porta, "/api/parar", metodo="POST")
    confere(st == 403, "POST /api/parar sem X-Monitor e recusado (%s)" % st)
    st, _ = pede(porta, "/nada")
    confere(st == 404, "rota desconhecida: 404")
    st, _ = pede(porta, "/")
    confere(st == 200, "a pagina e servida")


# ---------------------------------------------------------------- vozes locais

def monta_vozes():
    """Motores falsos (arquivos vazios): so a deteccao importa aqui; rodar um deles falha."""
    exe, py = ("piper.exe", "venv/Scripts/python.exe") if os.name == "nt" else ("piper", "venv/bin/python")
    for rel in ("piper/piper/" + exe, "piper/vozes/pt_BR-faber-medium.onnx", "piper/vozes/pt_BR-faber-medium.onnx.json",
                "piper/vozes/sem-json.onnx", "kokoro/" + py, "kokoro/kokoro-v1.0.onnx", "kokoro/voices-v1.0.bin"):
        grava("vozes/" + rel, [""], fim="")


def testes_vozes(porta):
    st, v = pede(porta, "/api/vozes")
    vozes = v.get("vozes", []) if st == 200 else []
    confere([x["id"] for x in vozes] == ["piper:pt_BR-faber-medium", "kokoro:pf_dora", "kokoro:pm_alex", "kokoro:pm_santa"],
            "vozes locais: Piper com o .json ao lado e as tres pt-BR do Kokoro (%r)" % vozes)
    confere(vozes and vozes[0]["nome"] == "faber · pt-BR" and vozes[0]["motor"] == "Piper", "nome e motor da voz (%r)" % vozes[:1])
    json_h = {"Content-Type": "application/json"}
    st, _ = pede(porta, "/api/falar", "POST", {"Content-Type": "text/plain"}, b'{"voz": "kokoro:pf_dora", "texto": "oi"}')
    confere(st == 415, "falar sem JSON: 415, a outra origem nao passa sem preflight (%s)" % st)
    st, _ = pede(porta, "/api/falar", "POST", json_h, b"nao e json")
    confere(st == 400, "falar com corpo invalido: 400 (%s)" % st)
    st, _ = pede(porta, "/api/falar", "POST", json_h, json.dumps({"voz": "piper:../../segredo", "texto": "oi"}).encode())
    confere(st == 404, "voz fora da lista: 404, nenhum caminho sai do pedido (%s)" % st)
    st, _ = pede(porta, "/api/falar", "POST", json_h, json.dumps({"voz": "piper:pt_BR-faber-medium", "texto": "oi"}).encode())
    confere(st == 500 and pede(porta, "/api/saude")[0] == 200, "motor que falha: 500, e o servidor segue no ar (%s)" % st)
    st, _ = pede(porta, "/api/falar", "POST", json_h, json.dumps({"voz": "kokoro:pf_dora", "texto": "oi"}).encode())
    confere(st == 500 and pede(porta, "/api/saude")[0] == 200, "Kokoro residente que nao sobe: 500, e o servidor segue no ar (%s)" % st)


def testes_kokoro_ocioso():
    """Sem pagina consultando, o Kokoro residente sai pela entrada fechada; com pagina, fica."""
    sys.path.insert(0, os.path.dirname(SCRIPT))
    import monitor
    falso = subprocess.Popen([sys.executable, "-c", "import sys; sys.stdin.read()"], stdin=subprocess.PIPE)
    monitor.KOKORO["processo"] = falso
    monitor.ULTIMA_CONSULTA["t"] = time.monotonic()
    monitor.solta_kokoro_ocioso()
    confere(falso.poll() is None, "Kokoro fica enquanto a pagina consulta")
    monitor.ULTIMA_CONSULTA["t"] = time.monotonic() - monitor.OCIOSO_KOKORO_S - 1
    monitor.solta_kokoro_ocioso()
    confere(falso.poll() == 0 and monitor.KOKORO["processo"] is None, "Kokoro sai sem pagina consultando (%r)" % falso.poll())


# ---------------------------------------------------------------- CLI

class Falso(http.server.BaseHTTPRequestHandler):
    """Servidor que finge ser outro programa (404) ou um monitor de outra versao."""
    versao = None

    def do_GET(self):
        if self.versao and self.path == "/api/saude":
            corpo = json.dumps({"monitor": True, "versao": self.versao, "pid": 1}).encode()
            self.send_response(200)
        else:
            corpo = b"nao sou o monitor"
            self.send_response(404)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_POST(self):
        self.send_response(200 if self.versao else 404)
        self.send_header("Content-Length", "0")
        self.end_headers()
        if self.versao:
            srv = self.server

            def desliga():
                srv.shutdown()
                srv.server_close()
            threading.Thread(target=desliga).start()

    def log_message(self, *_):
        pass


class ClawdFalso(http.server.BaseHTTPRequestHandler):
    """O clawd-panel (porta 8787) de mentira: o /status com os limites que a statusline publica, o /panes com a
    sessao cc-pergunta num pane bloqueado, e os POSTs de acao guardados em `pedidos`."""
    status = {"session_pct": 42, "session_known": True, "session_resets_in": 3600,
              "week_pct": 96.4, "week_known": True, "week_resets_in": 7200,
              "fable_pct": 91, "fable_known": True, "fable_fonte": "cota"}
    panes = {"online": True, "panes": [{"pane_id": "w9:p1", "agent": "claude", "state": "blocked", "sessao": "cc-pergunta",
                                        "cwd": "/work/alpha", "bloqueio": {"pergunta": "A ou B?", "opcoes": [
                                            {"n": 1, "rotulo": "A", "texto": False}, {"n": 2, "rotulo": "B", "texto": False}]}}]}
    pedidos = []  # (rota, cabecalho X-Monitor, corpo) de cada POST
    # A % de contexto que a statusline publica por sessao (o /sessions): e dela que sai a janela de verdade.
    sessions = {"sessions": [{"session_id": "cc-janela-sl", "context_pct": 10}, {"session_id": "cc-janela-200", "context_pct": 50}]}

    def responde(self, corpo, st=200):
        corpo = json.dumps(corpo).encode()
        self.send_response(st)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def do_GET(self):
        if self.path == "/ler?pane_id=w9%3Ap1":
            return self.responde({"pane": "w9:p1", "texto": "tela do agente\n> _"})
        corpo = {"/status": self.status, "/panes": self.panes, "/sessions": self.sessions}.get(self.path)
        self.responde(corpo or {}, 200 if corpo else 404)

    def do_POST(self):
        dados = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
        ClawdFalso.pedidos.append((self.path, self.headers.get("X-Monitor"), dados))
        self.responde({"enviado": True, "pane": dados.get("pane_id")})

    def log_message(self, *_):
        pass


def testes_acoes(porta):
    s = por_id(pede(porta, "/api/estado")[1]).get("claude-code:cc-pergunta", {})
    confere(s.get("pane") == "w9:p1" and [o["rotulo"] for o in (s.get("bloqueio") or {}).get("opcoes", [])] == ["A", "B"],
            "sessao dentro do herdr traz o pane e a pergunta da tela (%r)" % {k: s.get(k) for k in ("pane", "bloqueio")})
    js = {"Content-Type": "application/json"}
    corpo = lambda d: json.dumps(d).encode()
    pagina = pede(porta, "/")[1]
    achado = re.search(rb'name="monitor-token" content="([^"]+)"', pagina)
    token = achado.group(1).decode() if achado else ""
    confere(len(token) >= 16 and token != "__TOKEN__", "a pagina traz o token desta execucao do servidor")
    confere(pede(porta, "/api/estado")[1].get("token") == token,
            "o /api/estado traz o mesmo token: a pagina aberta antes de um reinicio pega o novo sem recarregar")
    oi = corpo({"sessao": "claude-code:cc-pergunta", "texto": "oi"})
    confere(pede(porta, "/api/enviar", "POST", js, oi)[0] == 403, "sem o cabecalho X-Monitor ninguem digita no agente")
    confere(pede(porta, "/api/enviar", "POST", dict(js, **{"X-Monitor": "acao"}), oi)[0] == 403,
            "cabecalho sem o token desta execucao e recusado")
    confere(pede(porta, "/api/enviar", "POST", dict(js, **{"X-Monitor": token, "Origin": "http://mal.example"}), oi)[0] == 403,
            "pedido de outra origem e recusado, mesmo com o token")
    cab = dict(js, **{"X-Monitor": token})
    ClawdFalso.pedidos.clear()
    invalidos = [pede(porta, "/api/enviar", "POST", cab, corpo({"sessao": "claude-code:cc-pergunta", "texto": "a\nb"}))[0],
                 pede(porta, "/api/responder", "POST", cab, corpo({"sessao": "claude-code:cc-pergunta", "n": "2", "rotulo": "B"}))[0],
                 pede(porta, "/api/enviar", "POST", dict(cab, **{"Content-Length": "-1"}))[0]]
    confere(invalidos == [400, 400, 400] and not ClawdFalso.pedidos,
            "texto de varias linhas, opcao sem numero e Content-Length negativo: 400, nada repassado (%r)" % invalidos)
    sts = [pede(porta, "/api/enviar", "POST", cab, corpo({"sessao": "claude-code:cc-pergunta", "texto": "roda de novo"}))[0],
           pede(porta, "/api/responder", "POST", cab, corpo({"sessao": "claude-code:cc-pergunta", "n": 2, "rotulo": "B"}))[0],
           pede(porta, "/api/interromper", "POST", cab, corpo({"sessao": "claude-code:cc-pergunta"}))[0]]
    confere(sts == [200, 200, 200] and ClawdFalso.pedidos == [
        ("/enviar", "1", {"pane_id": "w9:p1", "texto": "roda de novo"}),
        ("/responder", "1", {"pane_id": "w9:p1", "n": 2, "rotulo": "B"}),
        ("/enviar", "1", {"pane_id": "w9:p1", "tecla": "Escape"})],
        "escrever, responder e interromper vao ao clawd-panel com o pane da sessao (%r)" % ClawdFalso.pedidos)
    confere(pede(porta, "/api/enviar", "POST", cab, corpo({"sessao": "claude-code:cc-idle", "texto": "oi"}))[0] == 404,
            "sessao fora do herdr fica so leitura")
    st, tela = pede(porta, "/api/tela?sessao=claude-code:cc-pergunta")
    confere(st == 200 and tela.get("texto") == "tela do agente\n> _", "espelho do terminal pelo /ler do clawd-panel (%r)" % tela)
    confere(pede(porta, "/api/tela?sessao=claude-code:cc-idle")[0] == 404, "sem pane, sem espelho")


def testes_limites(porta):
    lim = pede(porta, "/api/estado")[1].get("limites") or []
    por = {(l["grupo"], l["nome"]): l for l in lim}
    cinco, semana = por.get(("Claude", "5 h"), {}), por.get(("Claude", "semana"), {})
    confere(cinco.get("pct") == 42 and 3500 < seg_de(cinco.get("renova")) - time.time() < 3700 and semana.get("pct") == 96,
            "limites do Claude pelo /status do clawd-panel, com quando renova (%r)" % lim)
    confere(por.get(("Claude", "Fable"), {}).get("pct") == 91, "cota do Fable so quando a fonte e a cota")
    confere(por.get(("Codex", "30 dias"), {}).get("pct") == 25 and ("Codex", "5 h") not in por,
            "limite do Codex pelo rate_limits; a janela que ja renovou some (%r)" % lim)
    confere(seg_de(cinco.get("renova")) % 60 == 0, "a renovacao do Claude vai ao minuto: o JSON nao muda a cada leitura")
    lim = estado_com_clawd({"session_known": False, "session_pct": 10, "week_known": True, "week_pct": 5,
                            "fable_known": True, "fable_fonte": "gasto", "fable_pct": 50}).get("limites") or []
    nomes = [(l["grupo"], l["nome"]) for l in lim]
    confere(("Claude", "semana") in nomes and ("Claude", "5 h") not in nomes and ("Claude", "Fable") not in nomes,
            "janela nao conhecida e Fable pelo gasto (e nao pela cota) ficam de fora (%r)" % nomes)
    e = estado_com_clawd(None)
    confere(not any(l["grupo"] == "Claude" for l in e.get("limites") or []) and e.get("sessoes")
            and any(l["grupo"] == "Codex" for l in e.get("limites") or []),
            "clawd-panel fora do ar: some o Claude, ficam o Codex e a lista (%r)" % e.get("limites"))


def seg_de(iso_txt):
    return datetime.strptime(iso_txt[:19], "%Y-%m-%dT%H:%M:%S").replace(tzinfo=timezone.utc).timestamp() if iso_txt else 0


def estado_com_clawd(status):
    """/api/estado de um monitor a parte, com um clawd-panel que devolve `status` no /status (None: fora do ar)."""
    falso_srv = None
    if status is None:
        url = "http://127.0.0.1:%d" % porta_livre()  # ninguem escuta nessa porta
    else:
        falso_srv = http.server.ThreadingHTTPServer(("127.0.0.1", porta_livre()), type("C", (ClawdFalso,), {"status": status}))
        threading.Thread(target=falso_srv.serve_forever, daemon=True).start()
        url = "http://127.0.0.1:%d" % falso_srv.server_address[1]
    ambiente, porta = dict(AMBIENTE, MONITOR_CLAWD=url), porta_livre()
    cli("--porta", str(porta), "--sem-navegador", ambiente=ambiente)
    try:
        return pede(porta, "/api/estado")[1]
    finally:
        cli("parar", "--porta", str(porta), ambiente=ambiente)
        if falso_srv:
            falso_srv.shutdown()


def falso(porta, versao):
    classe = type("F", (Falso,), {"versao": versao})
    srv = http.server.ThreadingHTTPServer(("127.0.0.1", porta), classe)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    return srv


def testes_cli():
    porta = porta_livre()
    srv = falso(porta, None)
    try:
        rc, out = cli("--porta", str(porta), "--sem-navegador")
        confere(rc == 1 and b"ocupada" in out, "porta ocupada por outro programa: sai 1 com aviso (%r)" % out[-200:])
    finally:
        srv.shutdown()
        srv.server_close()

    porta = porta_livre()
    srv = falso(porta, "0.0")
    try:
        rc, out = cli("--porta", str(porta), "--sem-navegador")
        st, s = pede(porta, "/api/saude")
        confere(rc == 0 and s.get("versao") not in (None, "0.0"), "monitor de outra versao e trocado (%r)" % out[-200:])
    finally:
        srv.server_close()
        cli("parar", "--porta", str(porta))


def main():
    monta_claude()
    monta_subagentes()
    monta_espera_e_falha()
    monta_codex()
    monta_pi()
    monta_agy()
    monta_vozes()
    clawd = http.server.ThreadingHTTPServer(("127.0.0.1", porta_livre()), ClawdFalso)
    threading.Thread(target=clawd.serve_forever, daemon=True).start()
    AMBIENTE["MONITOR_CLAWD"] = "http://127.0.0.1:%d" % clawd.server_address[1]
    porta = porta_livre()
    try:
        rc, out = cli("--porta", str(porta), "--sem-navegador")
        confere(rc == 0, "sobe o servidor (%r)" % out[-300:])
        confere(ascii_puro(out), "saida da CLI so em ASCII")
        confere(("http://127.0.0.1:%d/" % porta).encode() in out, "imprime a URL")
        pid = pede(porta, "/api/saude")[1].get("pid")
        rc, out = cli("--porta", str(porta), "--sem-navegador")
        confere(rc == 0 and pede(porta, "/api/saude")[1].get("pid") == pid, "segunda chamada reaproveita o servidor")
        testes_claude(porta)
        testes_subagentes(porta)
        testes_espera_e_falha(porta)
        testes_codex(porta)
        testes_pi(porta)
        testes_agy(porta)
        testes_incremental(porta)
        testes_arquivos(porta)
        testes_janela(porta)
        testes_api(porta)
        testes_vozes(porta)
        testes_limites(porta)
        testes_acoes(porta)
    finally:
        rc, out = cli("parar", "--porta", str(porta))
    confere(rc == 0, "parar derruba o servidor")
    try:
        pede(porta, "/api/saude")
        confere(False, "o servidor deveria ter caido")
    except OSError:
        confere(True, "servidor fora do ar depois do parar")
    rc, out = cli("parar", "--porta", str(porta))
    confere(rc == 0 and b"nenhum" in out.lower(), "parar sem servidor sai 0 e avisa (%r)" % out)
    testes_cli()
    testes_rede()
    testes_kokoro_ocioso()

    if FALHAS:
        print("\n".join("FALHA " + f for f in FALHAS))
        sys.exit(1)
    print("monitor ok")


if __name__ == "__main__":
    main()
