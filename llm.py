"""
llm.py - cliente mínimo para falar com o llama-server (llama.cpp).

PARTE DO PROJETO: a "ponte" entre os Atores e o modelo carregado.

  * Usa só a biblioteca padrão do Python (nada para instalar).
  * O llama-server expõe uma API compatível com a da OpenAI. Usamos o endpoint
    /v1/chat/completions porque ele aplica sozinho o template de chat do modelo
    (ChatML, Llama, Gemma...). Assim o código funciona com qualquer modelo instruct.
  * UM servidor / UM modelo carregado atende TODOS os Atores: um Ator é só
    dados (um arquivo .db) + um prompt, não um processo nem um modelo separado.
"""
import json
import urllib.error
import urllib.request


class LLM:
    def __init__(self, url="http://127.0.0.1:8080", timeout=600):
        self.url = url.rstrip("/")
        # Timeout alto de propósito: em CPU, ler um prompt longo pode levar vários
        # segundos antes de sair o primeiro token.
        self.timeout = timeout

    def generate(self, messages, max_tokens=150, temperature=0.7, slot=None, live=False, response_format=None):
        """
        messages  : lista no formato [{"role": "system"|"user"|"assistant", "content": "..."}]
        max_tokens: limite de tokens da resposta (respostas curtas = menos CPU)
        slot      : número do slot do servidor reservado a este Ator (ver abaixo)
        live      : se True, imprime cada pedaço no terminal assim que ele chega
        response_format: formato esperado da resposta (ex.: {"type": "json_object"} ou schema)
        Retorna o texto completo da resposta.
        """
        body = {
            "messages": messages,
            "max_tokens": max_tokens,
            "temperature": temperature,
            # Penalidades de repetição: evitam que o modelo repita as mesmas palavras/frases.
            # presence_penalty: penaliza qualquer token que já apareceu no texto (incentiva novos assuntos).
            # frequency_penalty: penaliza proporcionalmente à frequência do token (quanto mais repetiu, pior).
            # repeat_penalty: penalidade nativa do llama.cpp para n-gramas repetidos.
            "presence_penalty": 0.5,
            "frequency_penalty": 0.3,
            "repeat_penalty": 1.15,
            # Economia de CPU nº 1: o servidor guarda o prompt já processado e, na
            # próxima chamada, só processa a parte que mudou (o prefixo igual é reaproveitado).
            "cache_prompt": True,
            "stream": live,
        }
        if slot is not None:
            # Economia de CPU nº 2: cada Ator usa sempre o mesmo slot, então o cache
            # do prefixo dele (a personalidade) não é sobrescrito pelos outros Atores.
            body["id_slot"] = slot
        if response_format is not None:
            body["response_format"] = response_format

        request = urllib.request.Request(
            self.url + "/v1/chat/completions",
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                if not live:
                    data = json.load(response)
                    return data["choices"][0]["message"]["content"].strip()
                return self._read_stream(response)
        except urllib.error.HTTPError as error:
            # O servidor respondeu, mas com erro (ex.: prompt maior que o contexto do slot).
            detail = error.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"O llama-server respondeu com erro {error.code}: {detail}")
        except OSError as error:
            # Servidor desligado, porta errada, timeout...
            raise RuntimeError(
                f"Não consegui falar com o llama-server em {self.url}. Ele está rodando? ({error})"
            )

    def embedding(self, text):
        """
        Pede ao llama-server o vetor de embedding de `text`, usando o MESMO servidor/modelo já
        carregado para falar - sem modelo nem dependência extra (roadmap, seção 8: memória
        semântica). Requer o servidor iniciado com --embeddings (ver ServerManager em
        main.py); levanta RuntimeError se o endpoint não existir ou o servidor não responder -
        quem chama decide o que fazer (ver Actor.recall_semantic, que cai para a busca
        lexical de sempre nesse caso).

        Tenta primeiro o endpoint compatível com OpenAI (/v1/embeddings, formato estável entre
        versões do llama.cpp); se o servidor não tiver essa rota, cai para o endpoint nativo
        (/embedding), cujo formato já mudou entre versões (às vezes devolve o vetor direto,
        às vezes uma lista de resultados, às vezes um vetor por "pooling") - por isso o
        tratamento defensivo abaixo.
        """
        try:
            data = self._request("/v1/embeddings", {"input": text})
            return data["data"][0]["embedding"]
        except RuntimeError:
            pass  # servidor sem rota OpenAI-compatível; tenta a nativa

        data = self._request("/embedding", {"content": text})
        if isinstance(data, list):  # algumas versões devolvem uma lista de resultados
            data = data[0]
        vector = data["embedding"]
        if vector and isinstance(vector[0], list):
            # Servidor com pooling 'none': um vetor POR TOKEN, não um só pro texto inteiro.
            # Faz a média entre os tokens (mean pooling) em vez de pegar só o primeiro token,
            # que não representaria o texto sozinho.
            dimension = len(vector[0])
            vector = [sum(tok[i] for tok in vector) / len(vector) for i in range(dimension)]
        return vector

    def _request(self, path, body):
        """POST genérico ao llama-server, para endpoints que não precisam de streaming."""
        request = urllib.request.Request(
            self.url + path,
            data=json.dumps(body).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(request, timeout=self.timeout) as response:
                return json.load(response)
        except urllib.error.HTTPError as error:
            detail = error.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"O llama-server respondeu com erro {error.code}: {detail}")
        except OSError as error:
            raise RuntimeError(
                f"Não consegui falar com o llama-server em {self.url}. Ele está rodando? ({error})"
            )

    def _read_stream(self, response):
        """Lê a resposta em streaming (SSE): linhas 'data: {json}' até 'data: [DONE]'."""
        parts = []
        for line in response:
            line = line.decode("utf-8").strip()
            if not line.startswith("data:"):
                continue
            piece_data = line[5:].strip()
            if piece_data == "[DONE]":
                break
            try:
                piece = json.loads(piece_data)["choices"][0]["delta"].get("content") or ""
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            parts.append(piece)
            print(piece, end="", flush=True)  # o texto aparece no terminal enquanto é gerado
        print()
        return "".join(parts).strip()
