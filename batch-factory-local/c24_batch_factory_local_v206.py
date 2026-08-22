from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import requests

import c24_batch_factory_local_v205 as v205

core = v205.core
core.APP_VERSION = "2.0.6"
core.APP_NAME = "Complotto24 Batch Factory LOCAL v2.0.6"

_ACTIVE_MODEL = ""
_PROFILE_LOGGED: set[str] = set()


def _norm_caps(info: dict) -> set[str]:
    caps = info.get("capabilities") or []
    return {str(x).strip().lower() for x in caps if str(x).strip()}


def _family_text(info: dict) -> str:
    details = info.get("details") or {}
    vals = [details.get("family"), details.get("families"), details.get("parameter_size"), details.get("quantization_level")]
    return " ".join(str(x) for x in vals if x).lower()


def _compat_profile(self, model: str) -> dict[str, Any]:
    """Return a conservative compatibility profile for any Ollama chat model.

    Prefer capabilities from /api/show. Name/family heuristics are only fallbacks for
    models that do not advertise enough metadata.
    """
    info = self.model_info(model)
    caps = _norm_caps(info)
    low = model.lower()
    family = _family_text(info)
    identity = f"{low} {family}"

    # Embedding-only models should never reach chat generation.
    if "embedding" in caps and not any(x in caps for x in ("completion", "chat", "thinking", "tools")):
        raise RuntimeError(f"Il modello '{model}' sembra embedding-only e non e adatto alla generazione editoriale")

    thinking = "thinking" in caps or any(
        k in identity for k in ("gpt-oss", "qwen3", "deepseek-r1", "deepseek r1", "deepseek-v3.1", "deepseek v3.1")
    )

    if "gpt-oss" in identity:
        think_value: Any = "low"  # GPT-OSS requires a level, not a boolean.
        think_label = "low"
        predict = 6144
    elif thinking:
        # Most Ollama thinking models support booleans; disable reasoning for this
        # deterministic editorial workflow. Automatic fallback removes the field if
        # a specific model rejects it.
        think_value = False
        think_label = "false"
        predict = 4096
    else:
        think_value = None
        think_label = "auto/omesso"
        predict = 4096

    return {
        "model": model,
        "info": info,
        "caps": caps,
        "thinking": thinking,
        "think": think_value,
        "think_label": think_label,
        "num_ctx": 12288,
        "num_predict": predict,
    }


def _variant_key(v: dict[str, Any]) -> tuple:
    return (repr(v.get("think", "__omit__")), bool(v.get("format_json", False)))


def _build_variants(profile: dict[str, Any], json_mode: bool) -> list[dict[str, Any]]:
    """Compatibility ladder: preferred profile, then safe fallbacks.

    This intentionally avoids blind nested retries. Each variant changes one API
    assumption (thinking or structured JSON) and is attempted at most once.
    """
    variants: list[dict[str, Any]] = []

    base: dict[str, Any] = {"format_json": bool(json_mode)}
    if profile.get("think") is not None:
        base["think"] = profile["think"]
    variants.append(base)

    if "think" in base:
        variants.append({"format_json": bool(json_mode)})

    if json_mode:
        no_format: dict[str, Any] = {"format_json": False}
        if profile.get("think") is not None:
            no_format["think"] = profile["think"]
        variants.append(no_format)
        variants.append({"format_json": False})

    out: list[dict[str, Any]] = []
    seen = set()
    for v in variants:
        k = _variant_key(v)
        if k not in seen:
            seen.add(k)
            out.append(v)
    return out[:4]


def _stream_chat_adaptive(
    self,
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    json_mode: bool = False,
    temperature: float = 0.5,
    images: Optional[list[str]] = None,
) -> str:
    global _ACTIVE_MODEL

    model = model or self.resolve_model()
    _ACTIVE_MODEL = model
    profile = self.compat_profile(model)

    if model not in _PROFILE_LOGGED:
        caps_txt = ",".join(sorted(profile["caps"])) if profile["caps"] else "non dichiarate"
        self.logger.log(
            f"Profilo LLM: {model} | capability={caps_txt} | thinking={profile['thinking']} | "
            f"think={profile['think_label']} | ctx={profile['num_ctx']} | predict={profile['num_predict']}"
        )
        _PROFILE_LOGGED.add(model)

    variants = _build_variants(profile, json_mode)
    last_exc: BaseException | None = None

    for idx, variant in enumerate(variants, 1):
        messages: list[dict[str, Any]] = [
            {"role": "system", "content": system},
            {"role": "user", "content": user},
        ]
        if images:
            messages[-1]["images"] = images

        # If we had to drop structured JSON, reinforce the contract in the prompt.
        if json_mode and not variant.get("format_json"):
            messages[-1]["content"] += "\n\nRESTITUISCI SOLO JSON VALIDO, SENZA MARKDOWN O TESTO AGGIUNTIVO."

        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": "15m",
            "options": {
                "temperature": temperature,
                "num_ctx": int(profile["num_ctx"]),
                "num_predict": int(profile["num_predict"]),
            },
        }
        if variant.get("format_json"):
            payload["format"] = "json"
        if "think" in variant:
            payload["think"] = variant["think"]

        desc = f"think={variant.get('think', 'omesso')}, json={'on' if variant.get('format_json') else 'off'}"
        started = time.time()
        thinking_chars = 0
        chunks: list[str] = []
        last_obj: dict[str, Any] = {}

        try:
            with self.http.s.post(
                self.base + "/api/chat",
                json=payload,
                stream=True,
                timeout=(15, 180),
            ) as response:
                if response.status_code >= 400:
                    body = response.text[:1200]
                    raise RuntimeError(f"Ollama HTTP {response.status_code}: {body}")

                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    obj = json.loads(line)
                    last_obj = obj if isinstance(obj, dict) else {}
                    if last_obj.get("error"):
                        raise RuntimeError(str(last_obj["error"]))
                    msg = last_obj.get("message") or {}
                    piece = msg.get("content", "") or ""
                    thinking = msg.get("thinking", "") or ""
                    if thinking:
                        thinking_chars += len(thinking)
                    if piece:
                        chunks.append(piece)
                    if last_obj.get("done") is True:
                        break

            content = "".join(chunks).strip()
            elapsed = time.time() - started
            if content:
                self.logger.log(
                    f"Ollama {model} completato in {elapsed:.1f}s ({len(content)} caratteri; profilo {desc})"
                )
                return content

            done_reason = last_obj.get("done_reason") or last_obj.get("stop_reason") or "n/d"
            eval_count = last_obj.get("eval_count", "n/d")
            raise RuntimeError(
                f"risposta finale vuota (thinking={thinking_chars} caratteri, done_reason={done_reason}, "
                f"eval_count={eval_count}, profilo {desc})"
            )

        except (requests.Timeout, requests.ConnectionError, json.JSONDecodeError, RuntimeError) as exc:
            last_exc = exc
            low_exc = str(exc).lower()
            if any(k in low_exc for k in ("out of memory", "cuda", "oom")):
                raise RuntimeError(
                    f"Ollama ({model}) ha esaurito memoria GPU/RAM. Chiudi applicazioni GPU o prova un modello piu piccolo."
                ) from exc
            if isinstance(exc, requests.Timeout):
                raise RuntimeError(
                    f"Ollama ({model}) non ha inviato dati per 180 secondi; richiesta interrotta senza retry annidati."
                ) from exc

            if idx < len(variants):
                next_v = variants[idx]
                next_desc = f"think={next_v.get('think', 'omesso')}, json={'on' if next_v.get('format_json') else 'off'}"
                self.logger.log(
                    f"Compatibilita LLM {model}: tentativo {idx}/{len(variants)} fallito: {exc}; "
                    f"provo {next_desc}"
                )
                time.sleep(1.5)
                continue

    raise RuntimeError(f"Ollama non ha completato la richiesta con {model}: {last_exc}")


def _chat_json_single_retry(self, system: str, user: str, *, temperature: float = 0.4) -> Any:
    """One parse retry only; transport/compatibility retries are owned by chat()."""
    try:
        raw = self.chat(system, user, json_mode=True, temperature=temperature)
        return core.json_extract(raw)
    except (ValueError, json.JSONDecodeError) as exc:
        self.logger.log(f"JSON non valido da {self.cfg.ollama_model}; un solo retry di correzione: {exc}")
        raw = self.chat(
            system,
            user + "\n\nCORREZIONE OBBLIGATORIA: restituisci esclusivamente JSON sintatticamente valido, senza markdown.",
            json_mode=True,
            temperature=min(temperature, 0.25),
        )
        return core.json_extract(raw)
    except Exception:
        # Do not stack another whole request after chat() has already exhausted its
        # compatibility ladder.
        raise


core.OllamaClient.compat_profile = _compat_profile
core.OllamaClient.chat = _stream_chat_adaptive
core.OllamaClient.chat_json = _chat_json_single_retry


# Replace stale Qwen-specific wording in legacy log messages with the active model.
_original_log = core.AppLogger.log


def _model_aware_log(self, msg: str) -> None:
    if _ACTIVE_MODEL:
        msg = msg.replace("con Qwen locale", f"con {_ACTIVE_MODEL}")
        msg = msg.replace("Qwen ha restituito", f"{_ACTIVE_MODEL} ha restituito")
        msg = msg.replace("Fact-check semantico Qwen", f"Fact-check semantico {_ACTIVE_MODEL}")
    _original_log(self, msg)


core.AppLogger.log = _model_aware_log


# ---------- v2.0.6 UI version / compatibility wording ----------
_previous_build = core.BatchFactoryGUI._build


def _build_v206(self):
    _previous_build(self)
    self.root.title(core.APP_NAME)
    for widget in v205._walk_widgets(self.root):
        try:
            text = str(widget.cget("text"))
        except Exception:
            continue
        if text == "v2.0.5  •  LOCAL":
            try:
                widget.configure(text="v2.0.6  •  LOCAL")
            except Exception:
                pass
    try:
        self.model_hint.set("Seleziona un modello Ollama: compatibilita think/JSON adattata automaticamente")
    except Exception:
        pass


def _paint_banner_v206(self, canvas, width):
    v204 = v205.v204
    canvas.delete("all")
    h = 112
    canvas.create_rectangle(0, 0, width, h, fill=v204.PANEL, outline="")
    canvas.create_rectangle(0, 0, 8, h, fill=v204.ACCENT, outline="")
    canvas.create_oval(27, 23, 91, 87, fill=v204.ACCENT, outline="")
    canvas.create_text(59, 55, text="C24", fill="white", font=("Segoe UI Black", 19, "bold"))
    canvas.create_text(111, 34, anchor="w", text="COMPLOTTO24", fill=v204.TEXT, font=("Segoe UI Black", 23, "bold"))
    canvas.create_text(112, 65, anchor="w", text="BATCH FACTORY  •  ADAPTIVE LOCAL AI", fill=v204.MUTED, font=("Segoe UI Semibold", 10))
    canvas.create_text(
        112, 88, anchor="w",
        text="Trend → LLM Ollama adattivo → QA editoriale → ComfyUI → ZIP WordPress",
        fill="#7f91aa", font=("Segoe UI", 9),
    )
    if width > 720:
        canvas.create_rectangle(width-184, 28, width-112, 54, fill="#193d35", outline="")
        canvas.create_text(width-148, 41, text="LOCAL", fill=v204.SUCCESS, font=("Segoe UI Semibold", 9))
        canvas.create_rectangle(width-102, 28, width-25, 54, fill="#40252c", outline="")
        canvas.create_text(width-63, 41, text="v2.0.6", fill="#ff9da4", font=("Segoe UI Semibold", 9))


def _on_model_selected_v206(self, _event=None):
    self._sync_cfg()
    try:
        self.cfg.save()
    except Exception:
        pass
    self.model_hint.set(f"Modello selezionato: {self.cfg.ollama_model} • compatibilita automatica al primo uso")
    self.logger.log(f"Modello LLM selezionato: {self.cfg.ollama_model}")


core.BatchFactoryGUI._build = _build_v206
core.BatchFactoryGUI._paint_banner = _paint_banner_v206
core.BatchFactoryGUI._on_model_selected = _on_model_selected_v206


# Rebind selection because the older builder bound the method object that existed then.
_actual_build = core.BatchFactoryGUI._build


def _build_and_rebind_v206(self):
    _actual_build(self)
    try:
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_selected)
    except Exception:
        pass


core.BatchFactoryGUI._build = _build_and_rebind_v206


def _self_test_v206():
    v205._self_test_v205()

    class Dummy:
        def model_info(self, model):
            if "gpt-oss" in model:
                return {"capabilities": ["completion", "thinking"], "details": {"family": "gptoss"}}
            if "qwen3" in model:
                return {"capabilities": ["completion", "thinking"], "details": {"family": "qwen3"}}
            return {"capabilities": ["completion"], "details": {"family": "llama"}}

    d = Dummy()
    p1 = _compat_profile(d, "gpt-oss:20b")
    p2 = _compat_profile(d, "qwen3:8b")
    p3 = _compat_profile(d, "llama3.2:3b")
    assert p1["think"] == "low" and p1["num_predict"] >= 6144
    assert p2["think"] is False
    assert p3["think"] is None
    assert len(_build_variants(p1, True)) >= 3
    assert len(_build_variants(p3, True)) >= 2
    print("V2.0.6 ADAPTIVE LLM COMPATIBILITY SELF-TEST PASS")


def main():
    if "--self-test" in os.sys.argv:
        _self_test_v206()
        return
    core.ensure_dirs()
    if core.tk is None:
        raise SystemExit("Tkinter non disponibile")
    core.BatchFactoryGUI().run()


if __name__ == "__main__":
    main()
