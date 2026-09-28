#!/usr/bin/env python3
"""Teste da tela no navegador (opcional: precisa do Playwright e do Chromium dele).

  pip install playwright && python -m playwright install chromium
  python teste_tela.py

Sobe o monitor numa porta livre com uma home vazia e troca o /api/estado e o /api/eventos por um estado montado aqui:
uma sessao esperando com pergunta, tres trabalhando (uma do Codex, uma com subagentes) e duas finalizadas (uma com
falha de ferramenta e arquivos escritos). Confere a grade, o cabecalho, as gavetas, o filtro da atividade, o favicon
e o destaque da palavra lida, em 1400, 720 e 390 px. Sem o Playwright, avisa e sai 0.
"""
import os
import shutil
import socket
import subprocess
import sys
import tempfile
from datetime import datetime, timedelta, timezone

try:
    from playwright.sync_api import sync_playwright
except ImportError:
    print("pulado: sem o Playwright (pip install playwright && python -m playwright install chromium)")
    sys.exit(0)

SCRIPT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "monitor.py")
FALHAS = []


def confere(cond, msg):
    (print if cond else FALHAS.append)(("ok: " if cond else "") + msg.encode("ascii", "backslashreplace").decode("ascii"))


def porta_livre():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    p = s.getsockname()[1]
    s.close()
    return p


def iso(atras_s):
    return (datetime.now(timezone.utc) - timedelta(seconds=atras_s)).strftime("%Y-%m-%dT%H:%M:%S.000Z")


def sessao(id_, projeto, urgente, harness="claude-code", **mais):
    s = {"id": id_, "nome": projeto, "funcao": None, "modelo": "claude-opus-5-5", "status": urgente, "inicio": iso(3600),
         "ultimo": iso(5), "turno_inicio": iso(300), "prompt": "pedido de " + projeto, "agora": None, "fazendo": "trabalhando em " + projeto,
         "pergunta": False, "tokens": 420000, "janela": 1000000, "plano": None, "falha": None, "harness": harness, "projeto": projeto,
         "titulo": projeto, "subagentes": [], "workflows": [], "pane": None, "bloqueio": None, "urgente": urgente}
    s.update(mais)
    return s


def sub(id_, nome, st):
    return sessao(id_, nome, st, nome=nome, modelo="claude-haiku-4-5-20251001", tokens=30000, janela=200000)


ESTADO = {
    "gerado": iso(0), "versao": "teste",
    "harnesses": [{"id": "claude-code", "nome": "Claude Code", "ativas": 5}, {"id": "codex", "nome": "Codex", "ativas": 1}],
    "limites": [{"grupo": "Claude", "nome": "5 h", "pct": 8, "renova": iso(-3600)}, {"grupo": "Claude", "nome": "semana", "pct": 98, "renova": iso(-7200)},
                {"grupo": "Claude", "nome": "Fable", "pct": 91, "renova": None}],
    "sessoes": [
        sessao("s-esp", "datalake", "esperando", pane="w1:p1", pergunta=True,
               bloqueio={"pergunta": "Posso rodar os testes de integração?", "opcoes": [{"n": 1, "rotulo": "Sim"}, {"n": 2, "rotulo": "Não"}]}),
        sessao("s-trab", "monitor", "trabalhando", tokens=710000, esforco="high", subagentes=[sub("a1", "opus-Explore", "trabalhando"), sub("a2", "haiku-docs", "finalizado")]),
        sessao("s-cx", "beta", "trabalhando", harness="codex", modelo="gpt-5.6-terra", tokens=60000, janela=258000),
        sessao("s-t3", "gama", "trabalhando"),
        sessao("s-fim", "radar", "finalizado", fazendo="Relatório gerado.", falha={"ts": iso(60), "texto": "exit 1"}),
        sessao("s-fim2", "wireguard", "finalizado", fazendo="Commitei a leva."),
    ],
    "atividade": [{"ts": iso(10), "tipo": "ferramenta", "nome": "Bash", "texto": "cargo test", "sessao": "s-cx", "projeto": "beta", "quem": None},
                  {"ts": iso(20), "tipo": "ferramenta", "nome": "Edit", "texto": "monitor.html", "sessao": "s-trab", "projeto": "monitor", "quem": None}],
}
for s in ESTADO["sessoes"]:
    if s["status"] == "finalizado":
        s["ultimo"] = iso(120)
FALA = "Commitei a **leva inteira** de uma vez com `git add -A`.\n\nDepois conferi os seis achados do varredor."
EVENTOS = {"agente": "x", "fala": FALA, "eventos": [], "arquivos": [r"D:\work\radar\relatorio.py", r"D:\work\radar\README.md"]}


def main():
    home = tempfile.mkdtemp(prefix="monitor-tela-")
    ambiente = dict(os.environ, HOME=home, USERPROFILE=home, MONITOR_VOZES=os.path.join(home, "vozes"),
                    MONITOR_CLAWD="http://127.0.0.1:%d" % porta_livre())
    porta = porta_livre()
    subprocess.run([sys.executable, SCRIPT, "--porta", str(porta), "--sem-navegador"], env=ambiente, capture_output=True, timeout=60)
    try:
        with sync_playwright() as p:
            try:
                nav = p.chromium.launch()
            except Exception:  # sem o Chromium do Playwright: o Chrome instalado serve
                nav = p.chromium.launch(channel="chrome")
            for largura in (1400, 720, 390):
                confere_largura(nav, porta, largura)
            nav.close()
    finally:
        subprocess.run([sys.executable, SCRIPT, "parar", "--porta", str(porta)], env=ambiente, capture_output=True, timeout=60)
        shutil.rmtree(home, ignore_errors=True)
    if FALHAS:
        print("\n".join("FALHA " + f for f in FALHAS))
        sys.exit(1)
    print("tela ok")


def confere_largura(nav, porta, largura):
    pag = nav.new_page(viewport={"width": largura, "height": 1000})
    erros = []
    pag.on("pageerror", lambda e: erros.append(str(e)))
    pag.route("**/api/estado", lambda r: r.fulfill(json=ESTADO))
    pag.route("**/api/eventos?*", lambda r: r.fulfill(json=EVENTOS))
    pag.goto("http://127.0.0.1:%d/" % porta)
    pag.wait_for_selector(".grade-sessoes .cartao")
    m = pag.evaluate("""() => {
      const topo = e => Math.round(e.getBoundingClientRect().top), larg = s => Math.round(document.querySelector(s).getBoundingClientRect().width);
      const ativos = [...document.querySelectorAll('.grade-sessoes:not(.finalizadas) > .cartao')].map(topo);
      const g = document.querySelector('.limites-grade').getBoundingClientRect(), c = [...document.querySelectorAll('.cartao-limite')].pop().getBoundingClientRect();
      return { lateral: document.documentElement.scrollWidth - document.documentElement.clientWidth,
        porLinha: Math.max(...Object.values(ativos.reduce((n, t) => (n[t] = (n[t] || 0) + 1, n), {}))),
        espera: larg('#s-esp') === larg('.grade-sessoes'),
        altAtivo: larg('#s-cx') && Math.round(document.getElementById('s-cx').getBoundingClientRect().height),
        altFim: Math.round(document.getElementById('s-fim').getBoundingClientRect().height),
        modelo: document.querySelector('#s-trab > .modelo').textContent, secoes: [...document.querySelectorAll('#principal .titulo-painel')].map(x => x.textContent),
        limites: Math.round(g.right - c.right), cabecalho: Math.round(document.querySelector('header').getBoundingClientRect().height),
        falhouNoCartao: document.getElementById('s-fim').textContent.includes('falhou'),
        favicon: document.querySelector('link[rel=icon][sizes="256x256"]').getAttribute('href') };
    }""")
    colunas = 3 if largura >= 1100 else 2 if largura >= 700 else 1
    confere(m["lateral"] <= 0, "%d px: sem rolagem lateral" % largura)
    confere(m["porLinha"] == colunas and m["espera"], "%d px: %d cartoes por linha e a espera na largura toda (%r)" % (largura, colunas, m["porLinha"]))
    confere(m["altFim"] < m["altAtivo"], "%d px: finalizada menor que a ativa (%r < %r)" % (largura, m["altFim"], m["altAtivo"]))
    confere(m["modelo"] == "Opus 5.5 · high · 1M" and m["secoes"] == ["Em andamento · 4", "Finalizadas · 2"], "%d px: modelo e secoes (%r)" % (largura, (m["modelo"], m["secoes"])))
    confere(m["limites"] <= 1 and m["cabecalho"] <= 64,
            "%d px: uso do plano na largura toda e cabecalho numa linha (%r)" % (largura, (m["limites"], m["cabecalho"])))
    icone = pag.evaluate("""href => new Promise(ok => { const i = new Image(); i.onload = () => ok([i.naturalWidth, i.naturalHeight]);
      i.onerror = () => ok(null); i.src = href; })""", m["favicon"])
    confere(not m["falhouNoCartao"] and m["favicon"].startswith("data:image/png") and icone == [256, 256],
            "%d px: falha fora do cartao e favicon da logo em PNG de 256 px (%r)" % (largura, icone))
    pag.click("#abrir-voz")
    pag.eval_on_selector("#volume", "e => { e.value = '40'; e.dispatchEvent(new Event('input')); }")
    vol = pag.evaluate("[volume, localStorage.getItem('monitor.volume'), document.getElementById('volume').title]")
    confere(vol == [0.4, "0.4", "Volume: 40%"], "%d px: o controle de volume muda e guarda o volume (%r)" % (largura, vol))
    pag.click("#abrir-voz")

    pag.click("#abrir\\:s-fim")
    pag.wait_for_selector("#falhas\\:s-fim")
    fechadas = pag.locator("#s-fim .falhas").count() == 0 and pag.locator("#s-fim .arquivos").count() == 0
    pag.click("#falhas\\:s-fim")
    pag.click("#arquivos\\:s-fim")
    links = pag.eval_on_selector_all("#s-fim .arquivo", "els => els.map(e => e.getAttribute('href'))")
    cor = pag.evaluate("getComputedStyle(document.querySelector('#s-fim .falha-item')).color")
    confere(fechadas and cor == "rgb(124, 133, 176)" and links == ["vscode://file/D:/work/radar/relatorio.py", "vscode://file/D:/work/radar/README.md"],
            "%d px: falhas e arquivos atras dos botoes; falha em cinza; link do VS Code (%r)" % (largura, links))

    # A palavra lida: o alinhamento da fala com a tela, na conclusao aberta.
    r = pag.evaluate("""fala => {
      const partes = partesDe(fala);
      leitura = { dono: 'fala:s-fim', rotulo: 'teste', partes, i: 1, palavra: 2, pausado: false, fila: new Map() };
      aplicaDestaque();
      const segundo = CSS.highlights.get('lendo') ? [...CSS.highlights.get('lendo')][0].toString() : null;
      leitura.i = 0; leitura.palavra = 3; aplicaDestaque();
      const negrito = CSS.highlights.get('lendo') ? [...CSS.highlights.get('lendo')][0].toString() : null;
      const fracao = [palavraNaFracao(partes[0], 0), palavraNaFracao(partes[0], 1)];
      leitura = null; CSS.highlights.delete('lendo');
      return { segundo, negrito, fracao, total: partes[0].palavras.length };
    }""", FALA)
    confere(r["segundo"] == "os" and r["negrito"] == "inteira" and r["fracao"] == [0, r["total"] - 1],
            "%d px: a palavra lida casa com a tela, dentro do negrito e no segundo paragrafo (%r)" % (largura, r))

    if pag.locator("#filtro\\:h\\:codex").count():
        pag.click("#filtro\\:h\\:codex")
        feed = pag.eval_on_selector_all(".item-feed .projeto-feed", "els => els.map(e => e.textContent)")
        confere(feed == ["beta"], "%d px: a atividade obedece ao filtro (%r)" % (largura, feed))
    confere(not erros, "%d px: sem erro na pagina (%r)" % (largura, erros))
    pag.close()


if __name__ == "__main__":
    main()
