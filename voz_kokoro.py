#!/usr/bin/env python3
"""Kokoro residente para o monitor: carrega o modelo uma vez e atende os pedidos pela entrada padrao.

  <python do venv do Kokoro> voz_kokoro.py <pasta do modelo>

Cada pedido e uma linha JSON {"voz", "texto"}; a resposta, na saida padrao, e o tamanho do WAV em 8 bytes (0 = falhou)
seguido do WAV. Usa a GPU (CUDA) quando o onnxruntime-gpu esta instalado, senao a CPU. Termina quando a entrada fecha,
isto e, quando o monitor sai. Roda no venv que tem kokoro-onnx e soundfile, nunca no Python do monitor.
"""
import io
import json
import os
import sys

# A saida padrao e o canal do protocolo: o que as bibliotecas imprimirem vai para o stderr.
protocolo = os.fdopen(os.dup(1), "wb")
os.dup2(2, 1)
sys.stdout = sys.stderr

from pathlib import Path  # noqa: E402

import onnxruntime  # noqa: E402
import soundfile  # noqa: E402
from kokoro_onnx import Kokoro  # noqa: E402


onnxruntime.set_default_logger_severity(3)  # so erros: os avisos do CUDA a cada fala enchiam o log do monitor


def sessao(modelo):
    if "CUDAExecutionProvider" in onnxruntime.get_available_providers():
        try:
            onnxruntime.preload_dlls()  # as DLLs do CUDA e do cuDNN que o pip instalou
            return onnxruntime.InferenceSession(modelo, providers=["CUDAExecutionProvider", "CPUExecutionProvider"])
        except Exception as e:  # driver ou biblioteca faltando: segue na CPU
            print("Kokoro sem GPU:", e, file=sys.stderr)
    return onnxruntime.InferenceSession(modelo, providers=["CPUExecutionProvider"])


pasta = Path(sys.argv[1])
kokoro = Kokoro.from_session(sessao(str(pasta / "kokoro-v1.0.onnx")), str(pasta / "voices-v1.0.bin"))
kokoro.create("Olá.", voice="pf_dora", lang="pt-br")  # aquece: a primeira inferencia e a mais lenta
for linha in sys.stdin.buffer:
    try:
        pedido = json.loads(linha)
        amostras, taxa = kokoro.create(pedido["texto"], voice=pedido["voz"], speed=1.0, lang="pt-br")
        wav = io.BytesIO()
        soundfile.write(wav, amostras, taxa, format="WAV")
        wav = wav.getvalue()
    except Exception as e:  # um pedido ruim nao derruba o processo
        print("Kokoro falhou:", e, file=sys.stderr)
        wav = b""
    protocolo.write(len(wav).to_bytes(8, "big") + wav)
    protocolo.flush()
