"""
llm.py - cliente mínimo para falar com o llama-server (llama.cpp).

PARTE DO PROJETO: a "ponte" entre os Ators e o modelo de 3B.

  * Usa só a biblioteca padrão do Python (nada para instalar).
  * O llama-server expõe uma API compatível com a da OpenAI. Usamos o endpoint
    /v1/chat/completions porque ele aplica sozinho o template de chat do modelo
    (ChatML, Llama, Gemma...). Assim o código funciona com qualquer modelo instruct.
  * UM servidor / UM modelo carregado atende TODOS os Ators: um Ator é só
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

    def gerar(self, mensagens, max_tokens=150, temperatura=0.7, slot=None, ao_vivo=False, response_format=None):
        """
        mensagens : lista no formato [{"role": "system"|"user"|"assistant", "content": "..."}]
        max_tokens: limite de tokens da resposta (respostas curtas = menos CPU)
        slot      : número do slot do servidor reservado a este Ator (ver abaixo)
        ao_vivo   : se True, imprime cada pedaço no terminal assim que ele chega
        response_format: formato esperado da resposta (ex.: {"type": "json_object"} ou schema)
        Retorna o texto completo da resposta.
        """
        corpo = {
            "messages": mensagens,
            "max_tokens": max_tokens,
            "temperature": temperatura,
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
            "stream": ao_vivo,
        }
        if slot is not None:
            # Economia de CPU nº 2: cada Ator usa sempre o mesmo slot, então o cache
            # do prefixo dele (a personalidade) não é sobrescrito pelos outros Ators.
            corpo["id_slot"] = slot
        if response_format is not None:
            corpo["response_format"] = response_format

        pedido = urllib.request.Request(
            self.url + "/v1/chat/completions",
            data=json.dumps(corpo).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(pedido, timeout=self.timeout) as resposta:
                if not ao_vivo:
                    dados = json.load(resposta)
                    return dados["choices"][0]["message"]["content"].strip()
                return self._ler_stream(resposta)
        except urllib.error.HTTPError as erro:
            # O servidor respondeu, mas com erro (ex.: prompt maior que o contexto do slot).
            detalhe = erro.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"O llama-server respondeu com erro {erro.code}: {detalhe}")
        except OSError as erro:
            # Servidor desligado, porta errada, timeout...
            raise RuntimeError(
                f"Não consegui falar com o llama-server em {self.url}. Ele está rodando? ({erro})"
            )

    def embedding(self, texto):
        """
        Pede ao llama-server o vetor de embedding de `texto`, usando o MESMO servidor/modelo já
        carregado para falar - sem modelo nem dependência extra (roadmap, seção 8: memória
        semântica). Requer o servidor iniciado com --embeddings (ver GerenciadorServidor em
        main.py); levanta RuntimeError se o endpoint não existir ou o servidor não responder -
        quem chama decide o que fazer (ver Ator.recordar_semantico, que cai para a busca
        lexical de sempre nesse caso).

        Tenta primeiro o endpoint compatível com OpenAI (/v1/embeddings, formato estável entre
        versões do llama.cpp); se o servidor não tiver essa rota, cai para o endpoint nativo
        (/embedding), cujo formato já mudou entre versões (às vezes devolve o vetor direto,
        às vezes uma lista de resultados, às vezes um vetor por "pooling") - por isso o
        tratamento defensivo abaixo.
        """
        try:
            dados = self._pedir("/v1/embeddings", {"input": texto})
            return dados["data"][0]["embedding"]
        except RuntimeError:
            pass  # servidor sem rota OpenAI-compatível; tenta a nativa

        dados = self._pedir("/embedding", {"content": texto})
        if isinstance(dados, list):  # algumas versões devolvem uma lista de resultados
            dados = dados[0]
        vetor = dados["embedding"]
        if vetor and isinstance(vetor[0], list):
            # Servidor com pooling 'none': um vetor POR TOKEN, não um só pro texto inteiro.
            # Faz a média entre os tokens (mean pooling) em vez de pegar só o primeiro token,
            # que não representaria o texto sozinho.
            dimensao = len(vetor[0])
            vetor = [sum(tok[i] for tok in vetor) / len(vetor) for i in range(dimensao)]
        return vetor

    def _pedir(self, caminho, corpo):
        """POST genérico ao llama-server, para endpoints que não precisam de streaming."""
        pedido = urllib.request.Request(
            self.url + caminho,
            data=json.dumps(corpo).encode("utf-8"),
            headers={"Content-Type": "application/json"},
        )
        try:
            with urllib.request.urlopen(pedido, timeout=self.timeout) as resposta:
                return json.load(resposta)
        except urllib.error.HTTPError as erro:
            detalhe = erro.read().decode("utf-8", errors="replace")[:300]
            raise RuntimeError(f"O llama-server respondeu com erro {erro.code}: {detalhe}")
        except OSError as erro:
            raise RuntimeError(
                f"Não consegui falar com o llama-server em {self.url}. Ele está rodando? ({erro})"
            )

    def _ler_stream(self, resposta):
        """Lê a resposta em streaming (SSE): linhas 'data: {json}' até 'data: [DONE]'."""
        partes = []
        for linha in resposta:
            linha = linha.decode("utf-8").strip()
            if not linha.startswith("data:"):
                continue
            dado = linha[5:].strip()
            if dado == "[DONE]":
                break
            try:
                pedaco = json.loads(dado)["choices"][0]["delta"].get("content") or ""
            except (json.JSONDecodeError, KeyError, IndexError):
                continue
            partes.append(pedaco)
            print(pedaco, end="", flush=True)  # o texto aparece no terminal enquanto é gerado
        print()
        return "".join(partes).strip()
