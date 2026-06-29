"""Provider Manager (`src/manager.py`) — A4, §6.3.

Dynamic Provider loading by `site`, per-instance parameter resolution (merging
global defaults), and a module cache. The dispatch contract is fixed:

    module.run(profile, prompt, timeout_ms=...) -> str

New account  -> add a few lines to providers.yaml.
New website  -> add src/providers/{site}.py.
Manager / Runtime / Builder stay unchanged (FR-04, §14 DoD).

Hardening over the reference skeleton: the provider package root is
parameterized (`provider_package`, default "src.providers") so tests can point
the Manager at a stub provider package WITHOUT polluting src/providers or
touching the function-signature contract. Default behaviour is identical to the
reference, so B is unaffected.
"""

from __future__ import annotations

import importlib

import yaml

DEFAULTS = {"timeout_ms": 120000, "retries": 2, "retry_backoff_ms": 3000}


class ProviderManager:
    def __init__(
        self,
        config_path: str = "config/providers.yaml",
        provider_package: str = "src.providers",
    ):
        self.config_path = config_path
        self.provider_package = provider_package
        with open(config_path, "r", encoding="utf-8") as f:
            cfg = yaml.safe_load(f) or {}
        self.config = cfg
        self.defaults = {**DEFAULTS, **(cfg.get("defaults") or {})}
        self.providers = cfg.get("providers") or {}
        self._module_cache: dict[str, object] = {}
        from src.account_pool import AccountPoolManager
        self.pool = AccountPoolManager.get_instance(self.config)

    def get_pool(self):
        return self.pool

    def resolve(self, provider_name: str) -> dict:
        conf = self.providers.get(provider_name)
        if not conf:
            raise ValueError(
                f"Provider '{provider_name}' not found in {self.config_path}"
            )
        if "site" not in conf:
            raise ValueError(
                f"Provider '{provider_name}' is missing required field 'site'"
            )
        return {
            "site": conf["site"],
            "profile": conf.get("profile", ""),
            "model": conf.get("model"),              # actual model ID (None = provider default)
            "label": conf.get("label", provider_name), # display name for frontend/SSE
            "base_url": conf.get("base_url"),          # API endpoint override
            "api_key_env": conf.get("api_key_env"),    # env-var name for the API key
            "timeout_ms": int(conf.get("timeout_ms", self.defaults["timeout_ms"])),
            "retries": int(conf.get("retries", self.defaults["retries"])),
            "retry_backoff_ms": int(
                conf.get("retry_backoff_ms", self.defaults["retry_backoff_ms"])
            ),
        }

    def _load(self, site: str):
        if site not in self._module_cache:
            try:
                self._module_cache[site] = importlib.import_module(
                    f"{self.provider_package}.{site}"
                )
            except ImportError as e:
                raise ValueError(
                    f"Provider site module "
                    f"'{self.provider_package}.{site}' could not be imported: {e}"
                ) from e
        return self._module_cache[site]

    def run(self, provider_name: str, prompt: str, **options) -> str:
        conf = self.resolve(provider_name)
        module = self._load(conf["site"])
        if not hasattr(module, "run"):
            raise ValueError(
                f"Provider site '{conf['site']}' does not expose a run() function "
                f"(contract 1 violated)"
            )
        # Contract: run(profile, prompt, **options) -> str
        # Pass through model/base_url/api_key_env from providers.yaml so each
        # instance can target a different model/endpoint.
        extra: dict = {}
        if conf.get("model"):
            extra["model"] = conf["model"]
        if conf.get("base_url"):
            extra["base_url"] = conf["base_url"]
        if conf.get("api_key_env"):
            extra["api_key_env"] = conf["api_key_env"]
        return module.run(conf["profile"], prompt, timeout_ms=conf["timeout_ms"], **extra, **options)


__all__ = ["ProviderManager", "DEFAULTS"]
