"""Config & pipeline validation (`src/validation.py`) — A5, §6.1.2.

Two call sites (§4 task A5):
  * startup       -> validate_all(): every pipeline + providers.yaml + prompts.
  * job submit    -> validate_pipeline_file(): the one requested pipeline; C
                     calls this in POST /jobs and returns HTTP 400 on failure
                     (§8.5). run_pipeline() also calls it as defense-in-depth.

Checks (all must pass, fail-closed with explicit messages):
  1. Each Step.provider exists in providers.yaml.
  2. Each Step.prompt has a file prompts/{prompt}.md.
  3. Step keys are unique within a pipeline.
  4. Forward-reference: every {{<key>}} a prompt references must be
     {{user_question}} or a step key produced BEFORE that step. Referencing a
     later/own step key (output not yet produced) or an unknown name is an
     error — the classic V1 logic bug this guards against.

Note on (4) vs [修正-3]: this checks human-authored prompt TEMPLATES, where the
only sanctioned variables are user_question and step keys (§6.1.3 "仅两类变量").
[修正-3] concerns MODEL OUTPUTS containing literal {{ }} and is handled at
runtime by the Builder whitelist — it does not require templates to carry stray
braces. A bare {{identifier}} in a template is therefore always treated as a
variable reference; to embed literal braces, use spaced/expression forms (e.g.
`{{ x }}`), which the Builder/validator do not treat as variables.
"""

from __future__ import annotations

import os

import yaml

from src.builder import PLACEHOLDER_RE


class ValidationError(Exception):
    """Aggregated, human-readable config/pipeline validation failure."""

    def __init__(self, errors: list[str], where: str = ""):
        self.errors = errors
        prefix = f"{where}: " if where else ""
        super().__init__(prefix + "; ".join(errors))


def _load_yaml(path: str) -> dict:
    if not os.path.isfile(path):
        raise ValidationError([f"file not found: {path}"], where=path)
    with open(path, "r", encoding="utf-8") as f:
        data = yaml.safe_load(f)
    if not isinstance(data, dict):
        raise ValidationError([f"top-level YAML must be a mapping"], where=path)
    return data


def validate_providers_config(providers_cfg: dict, where: str = "providers.yaml") -> None:
    errors: list[str] = []
    providers = providers_cfg.get("providers")
    if not isinstance(providers, dict) or not providers:
        raise ValidationError(["'providers' must be a non-empty mapping"], where=where)
    for name, conf in providers.items():
        if not isinstance(conf, dict):
            errors.append(f"provider '{name}' must be a mapping")
            continue
        site = conf.get("site")
        if not isinstance(site, str) or not site.strip():
            errors.append(f"provider '{name}' missing non-empty 'site'")
        if "profile" not in conf:
            errors.append(f"provider '{name}' missing 'profile' (use \"\" for API)")
        elif not isinstance(conf["profile"], str):
            errors.append(f"provider '{name}' 'profile' must be a string")
        for opt in ("timeout_ms", "retries", "retry_backoff_ms"):
            if opt in conf and not isinstance(conf[opt], int):
                errors.append(f"provider '{name}' '{opt}' must be an integer")
    if errors:
        raise ValidationError(errors, where=where)


def _template_placeholders(prompt_path: str) -> list[str]:
    with open(prompt_path, "r", encoding="utf-8") as f:
        text = f.read()
    return PLACEHOLDER_RE.findall(text)


def validate_pipeline(
    pipeline_path: str, providers_cfg: dict, prompts_dir: str = "prompts"
) -> dict:
    """Validate a single pipeline against providers + prompts. Returns parsed YAML."""
    parsed = _load_yaml(pipeline_path)
    errors: list[str] = []

    steps = parsed.get("steps")
    if not isinstance(steps, list) or not steps:
        raise ValidationError(["'steps' must be a non-empty list"], where=pipeline_path)

    providers = providers_cfg.get("providers") or {}
    seen_keys: set[str] = set()
    produced_before: list[str] = []  # step keys available to step i (ordered)

    for i, step in enumerate(steps):
        loc = f"step[{i}]"
        if not isinstance(step, dict):
            errors.append(f"{loc} must be a mapping")
            continue
        key = step.get("key")
        provider = step.get("provider")
        prompt = step.get("prompt")

        if not isinstance(key, str) or not key.strip():
            errors.append(f"{loc} missing non-empty 'key'")
        else:
            loc = f"step[{i}]('{key}')"
            if key in seen_keys:
                errors.append(f"{loc} duplicate key")
            seen_keys.add(key)

        provider_list = step.get("providers")
        if isinstance(provider_list, list) and provider_list:
            for p in provider_list:
                if not isinstance(p, str) or not p.strip():
                    errors.append(f"{loc} contains invalid provider name in 'providers' list")
                elif p not in providers:
                    errors.append(f"{loc} candidate provider '{p}' not in providers.yaml")
        else:
            if not isinstance(provider, str) or not provider.strip():
                errors.append(f"{loc} missing non-empty 'provider'")
            elif provider not in providers:
                errors.append(f"{loc} provider '{provider}' not in providers.yaml")

        if not isinstance(prompt, str) or not prompt.strip():
            errors.append(f"{loc} missing non-empty 'prompt'")
        else:
            prompt_path = os.path.join(prompts_dir, f"{prompt}.md")
            if not os.path.isfile(prompt_path):
                errors.append(f"{loc} prompt file not found: {prompt_path}")
            else:
                allowed = {"user_question", *produced_before}
                all_keys = {
                    s.get("key")
                    for s in steps
                    if isinstance(s, dict) and isinstance(s.get("key"), str)
                }
                for tok in _template_placeholders(prompt_path):
                    if tok in allowed:
                        continue
                    if tok in all_keys:
                        errors.append(
                            f"{loc} prompt '{prompt}' references {{{{{tok}}}}} which is "
                            f"produced by a later/the-same step (forward reference)"
                        )
                    else:
                        errors.append(
                            f"{loc} prompt '{prompt}' references unknown variable "
                            f"{{{{{tok}}}}} (not user_question and not a prior step key)"
                        )

        if isinstance(key, str) and key.strip():
            produced_before.append(key)

    if errors:
        raise ValidationError(errors, where=pipeline_path)
    return parsed


def validate_pipeline_file(
    pipeline_path: str,
    providers_path: str = "config/providers.yaml",
    prompts_dir: str = "prompts",
) -> dict:
    """Convenience for C's submit path: load providers from disk, then validate."""
    providers_cfg = _load_yaml(providers_path)
    return validate_pipeline(pipeline_path, providers_cfg, prompts_dir)


def validate_all(
    pipelines_dir: str = "pipelines",
    providers_path: str = "config/providers.yaml",
    prompts_dir: str = "prompts",
) -> None:
    """Startup check: providers config + every pipeline in pipelines_dir."""
    providers_cfg = _load_yaml(providers_path)
    validate_providers_config(providers_cfg, where=providers_path)
    if not os.path.isdir(pipelines_dir):
        raise ValidationError(
            [f"pipelines dir not found: {pipelines_dir}"], where=pipelines_dir
        )
    all_errors: list[str] = []
    for fn in sorted(os.listdir(pipelines_dir)):
        if not fn.endswith((".yaml", ".yml")):
            continue
        path = os.path.join(pipelines_dir, fn)
        try:
            validate_pipeline(path, providers_cfg, prompts_dir)
        except ValidationError as e:
            all_errors.extend(f"{fn}: {m}" for m in e.errors)
    if all_errors:
        raise ValidationError(all_errors, where=pipelines_dir)


__all__ = [
    "ValidationError",
    "validate_providers_config",
    "validate_pipeline",
    "validate_pipeline_file",
    "validate_all",
]
