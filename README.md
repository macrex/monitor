# monitor

Painel local que mostra, numa página só, o que os agentes de IA estão fazendo agora: sessões, subagentes e
workflows do Claude Code, do Codex, do Antigravity CLI (`agy`) e do Pi.

Ele lê em disco os transcripts que esses harnesses já gravam. Não usa hook, não pede nada ao modelo e não gasta
tokens. Um servidor Python (`monitor.py`, só biblioteca padrão) monta o estado e o serve a uma página
(`monitor.html`), que se atualiza a cada 2 s.

## Usar

Requer Python 3.9+.

```bash
python monitor.py                      # sobe o servidor em segundo plano e abre o painel
python monitor.py parar                # derruba o servidor
python monitor.py iniciar-no-login     # sobe no login do Windows (nao-iniciar-no-login desfaz)
```

| Opção | Efeito |
|---|---|
| `--porta N` | Outra porta (padrão 8765) |
| `--sem-navegador` | Não abre o painel |
| `--rede` | Atende também a rede local (celular), com chave |

## Opcionais

- **Responder aos agentes** pelo navegador (pergunta, texto, `/compact`, `/clear`, Esc): precisa do clawd-panel
  com o herdr na porta 8787 (`MONITOR_CLAWD` troca o endereço).
- **Vozes locais** para ler em voz alta, em `%LOCALAPPDATA%\monitor` (ou `MONITOR_VOZES`):
  - [Piper](https://github.com/rhasspy/piper/releases/tag/2023.11.14-2): `piper/piper/piper.exe` e as vozes
    ([pt_BR](https://huggingface.co/rhasspy/piper-voices/tree/main/pt/pt_BR)) em `piper/vozes/`.
  - [Kokoro](https://github.com/thewh1teagle/kokoro-onnx/releases/tag/model-files-v1.0): venv em `kokoro/venv`
    com `kokoro-onnx soundfile` (`onnxruntime-gpu[cuda,cudnn]` para a GPU) e os arquivos do modelo em `kokoro/`.
    Sobe quando o painel abre e sai da memória 150 s depois de ele fechar.

## Testar

```bash
python teste_monitor.py   # API, com transcripts de exemplo
python teste_tela.py      # tela, opcional: requer o Playwright
```
