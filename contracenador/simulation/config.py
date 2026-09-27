"""Configuração validada e serializável para experimentos de simulação."""
from dataclasses import dataclass


@dataclass(frozen=True)
class RuntimeConfig:
    model_actors: str
    model_screenwriter: str
    port_actors: int = 8080
    port_screenwriter: int = 8081
    slots_actors: int = 2
    slots_screenwriter: int = 1
    threads: int = None
    gpu_layers_actors: int = None
    gpu_layers_screenwriter: int = None
    context_actors: int = 4096
    context_screenwriter: int = 8192
    embeddings: bool = True
    actor_temperature: float = 0.7
    actor_max_tokens: int = 150
    investigator_temperature: float = 0.3
    investigator_max_tokens: int = 20
    screenwriter_temperature: float = 0.7
    screenwriter_max_tokens: int = 1800
    investigation_rounds: int = 10
    dialogue_rounds: int = 10
    simultaneous_conversations: int = 2
    influence_enabled: bool = True
    influence_weight: float = 0.25
    evidence_delta: float = 0.2
    direct_reliability: float = 0.7
    relayed_reliability: float = 0.35
    deduction_threshold: float = 0.75
    interrogation_fatigue: float = 0.12
    conversation_turns: int = 4
    actors_folder: str = "atores"
    scenario_folder: str = "cenario"
    theme: str = None
    seed: int = None
    verify_environment: bool = False
    show_config: bool = False

    def validate(self):
        for name in ("model_actors", "model_screenwriter"):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.strip():
                raise ValueError(f"{name} não pode ficar vazio")
        for name in ("actors_folder", "scenario_folder"):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f"{name} não pode ficar vazio")
        if self.theme is not None and not isinstance(self.theme, str):
            raise ValueError("theme precisa ser texto")
        for name in ("embeddings", "influence_enabled", "verify_environment", "show_config"):
            if not isinstance(getattr(self, name), bool):
                raise ValueError(f"{name} precisa ser booleano")
        for name in (
            "port_actors", "port_screenwriter", "slots_actors", "slots_screenwriter",
            "context_actors", "context_screenwriter", "actor_max_tokens",
            "investigator_max_tokens", "screenwriter_max_tokens", "investigation_rounds",
            "dialogue_rounds", "conversation_turns", "simultaneous_conversations",
        ):
            value = getattr(self, name)
            if not isinstance(value, int) or isinstance(value, bool):
                raise ValueError(f"{name} precisa ser inteiro")
        if self.port_actors == self.port_screenwriter:
            raise ValueError("Os servidores de atores e roteirista precisam de portas diferentes")
        for name in ("port_actors", "port_screenwriter"):
            value = getattr(self, name)
            if not 1 <= value <= 65535:
                raise ValueError(f"{name} precisa estar entre 1 e 65535")
        for name in ("slots_actors", "slots_screenwriter", "context_actors",
                     "context_screenwriter", "actor_max_tokens", "investigator_max_tokens",
                     "screenwriter_max_tokens", "investigation_rounds", "dialogue_rounds",
                     "conversation_turns"):
            if getattr(self, name) < 1:
                raise ValueError(f"{name} precisa ser pelo menos 1")
        if self.threads is not None and self.threads < 1:
            raise ValueError("threads precisa ser pelo menos 1")
        for name in ("threads", "gpu_layers_actors", "gpu_layers_screenwriter", "seed"):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, int) or isinstance(value, bool)):
                raise ValueError(f"{name} precisa ser inteiro")
        for name in ("gpu_layers_actors", "gpu_layers_screenwriter"):
            value = getattr(self, name)
            if value is not None and value < 0:
                raise ValueError(f"{name} não pode ser negativo")
        if self.simultaneous_conversations not in (1, 2):
            raise ValueError("simultaneous_conversations deve ser 1 ou 2")
        for name in ("actor_temperature", "investigator_temperature", "screenwriter_temperature"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 2:
                raise ValueError(f"{name} precisa estar entre 0 e 2")
        for name in ("influence_weight", "direct_reliability", "relayed_reliability",
                     "deduction_threshold"):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not 0 <= value <= 1:
                raise ValueError(f"{name} precisa estar entre 0 e 1")
        if (not isinstance(self.evidence_delta, (int, float))
                or isinstance(self.evidence_delta, bool) or not 0 <= self.evidence_delta <= 1):
            raise ValueError("evidence_delta precisa estar entre 0 e 1")
        if (not isinstance(self.interrogation_fatigue, (int, float))
                or isinstance(self.interrogation_fatigue, bool) or self.interrogation_fatigue < 0):
            raise ValueError("interrogation_fatigue não pode ser negativo")
        return self