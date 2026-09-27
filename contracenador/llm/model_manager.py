"""Ciclo de vida local de processos llama-server."""
import json
import os
import shutil
import socket
import subprocess
import sys
import tempfile
import time


class ServerManager:
    """Inicia e encerra um llama-server para um modelo local ou Hugging Face."""

    ACTORS_MODEL = "Qwen/Qwen2.5-7B-Instruct-GGUF:Q4_K_M"
    SCREENWRITER_MODEL = "Qwen/Qwen2.5-14B-Instruct-GGUF:Q4_K_M"

    def __init__(self, model, port, slots=2, context=4096, threads=None,
                 embedding=False, gpu_layers=None):
        self.model = model
        self.port = port
        self.slots = slots
        self.context = context
        self.threads = threads
        self.embedding = embedding
        self.gpu_layers = gpu_layers
        self._proc = None
        self._log_file = None

    def start(self):
        """Inicia o servidor e aguarda o modelo ficar pronto."""
        if self._proc and self._proc.poll() is None:
            return
        if self._proc is not None:
            self.stop()

        self._validate_startup()
        cmd = self._build_cmd()
        print(f"\n[servidor] Subindo: {' '.join(cmd)}")
        flags = subprocess.CREATE_NEW_PROCESS_GROUP if sys.platform == "win32" else 0
        self._log_file = tempfile.TemporaryFile()
        try:
            self._proc = subprocess.Popen(
                cmd,
                stdout=self._log_file,
                stderr=subprocess.STDOUT,
                creationflags=flags,
            )
        except OSError as error:
            self.stop()
            raise RuntimeError(
                f"Não foi possível iniciar llama-server ({error}). "
                "Verifique a instalação e se o executável está no PATH."
            ) from error

        try:
            self._wait_ready()
        except Exception as error:
            log_tail = self._read_log_tail()
            self.stop()
            if log_tail:
                raise RuntimeError(f"{error}\nSaída recente do llama-server:\n{log_tail}") from error
            raise

    def stop(self):
        """Para o processo do servidor, se estiver em execução."""
        if self._proc is not None:
            if self._proc.poll() is None:
                print(f"[servidor] Encerrando servidor na porta {self.port}...")
                self._proc.terminate()
                try:
                    self._proc.wait(timeout=10)
                except subprocess.TimeoutExpired:
                    self._proc.kill()
            self._proc = None
        if self._log_file is not None:
            self._log_file.close()
            self._log_file = None

    def url(self):
        return f"http://127.0.0.1:{self.port}"

    def is_running(self):
        return self._proc is not None and self._proc.poll() is None

    def _validate_startup(self):
        if not self.model.strip():
            raise RuntimeError("Modelo vazio. Informe um modelo com --modelo-atores ou --modelo-roteirista.")
        if not 1 <= self.port <= 65535:
            raise RuntimeError(f"Porta inválida: {self.port}. Use um valor entre 1 e 65535.")
        if self.slots < 1:
            raise RuntimeError(f"Número de slots inválido: {self.slots}. Use pelo menos 1.")
        if self.context < 1:
            raise RuntimeError(f"Contexto inválido: {self.context}. Use pelo menos 1 token.")
        if self.threads is not None and self.threads < 1:
            raise RuntimeError(f"Número de threads inválido: {self.threads}. Use pelo menos 1.")
        if self.gpu_layers is not None and self.gpu_layers < 0:
            raise RuntimeError(f"Número de camadas GPU inválido: {self.gpu_layers}. Use 0 ou mais.")

        if self.model.strip().lower().endswith(".gguf") and not os.path.isfile(self.model):
            raise RuntimeError(
                f"Arquivo de modelo não encontrado: '{self.model}'. Confira o caminho informado."
            )
        if shutil.which("llama-server") is None:
            raise RuntimeError(
                "Executável 'llama-server' não encontrado no PATH. "
                "Instale/compile o llama.cpp e adicione a pasta do executável ao PATH."
            )

        with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as probe:
            if probe.connect_ex(("127.0.0.1", self.port)) == 0:
                if sys.platform == "win32":
                    inspect_process = (
                        f"Get-NetTCPConnection -LocalPort {self.port} -State Listen "
                        "| Select-Object OwningProcess"
                    )
                else:
                    inspect_process = f"lsof -nP -iTCP:{self.port} -sTCP:LISTEN"
                raise RuntimeError(
                    f"A porta {self.port} já está em uso por outro processo. "
                    f"Identifique-o com: {inspect_process}. Encerre-o se apropriado ou escolha "
                    "outra porta com --porta-atores/--porta-roteirista."
                )

    def _read_log_tail(self, max_bytes=8192):
        if self._log_file is None:
            return ""
        self._log_file.flush()
        self._log_file.seek(0, os.SEEK_END)
        end = self._log_file.tell()
        self._log_file.seek(max(0, end - max_bytes))
        return self._log_file.read(max_bytes).decode("utf-8", errors="replace").strip()

    def _build_cmd(self):
        model = self.model.strip()
        model_flag = ["-m", model] if model.endswith(".gguf") else ["-hf", model]
        gpu_options = ["--fit", "on"]
        if self.gpu_layers is not None:
            gpu_options += ["-ngl", str(self.gpu_layers)]

        cmd = (
            ["llama-server"] + model_flag + gpu_options
            + ["-c", str(self.context), "-np", str(self.slots), "--port", str(self.port)]
        )
        if self.threads:
            cmd += ["-t", str(self.threads)]
        if self.embedding:
            cmd += ["--embeddings"]
        return cmd

    def _wait_ready(self, attempts=120, interval=2.0):
        """Polls /health por até quatro minutos para modelos que demoram a carregar."""
        import urllib.request

        health_url = f"{self.url()}/health"
        print(f"[servidor] Aguardando o modelo carregar na porta {self.port}", end="", flush=True)
        for _ in range(attempts):
            time.sleep(interval)
            print(".", end="", flush=True)
            if self._proc.poll() is not None:
                print()
                raise RuntimeError(
                    "O llama-server encerrou antes de ficar pronto. "
                    "Verifique se o modelo existe e se há RAM suficiente."
                )
            try:
                with urllib.request.urlopen(health_url, timeout=3) as response:
                    data = json.load(response)
                    if data.get("status") == "ok":
                        print(f"\n[servidor] Pronto! ({self.model})")
                        return
            except Exception:
                pass
        print()
        raise RuntimeError(
            f"Tempo esgotado aguardando o modelo '{self.model}' na porta {self.port}. "
            "Confira RAM/VRAM disponível, conectividade/cache do Hugging Face e o log acima. "
            "Se necessário, reduza o contexto ou --camadas-gpu-*."
        )