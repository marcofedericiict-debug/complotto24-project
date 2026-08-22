from __future__ import annotations

import json
import os
import time
from typing import Any, Optional

import requests

import c24_batch_factory_local_v208 as v208

core = v208.core
v206 = v208.v207.v206
core.APP_VERSION = "2.0.9"
core.APP_NAME = "Complotto24 Batch Factory LOCAL v2.0.9"

_PROFILE_LOGGED_209: set[str] = set()


def _compat_profile_v209(self, model: str) -> dict[str, Any]:
    p = dict(v206._compat_profile(self, model))
    identity = (model + " " + v206._family_text(p.get("info") or {})).lower()

    # Reasoning models can spend the entire completion budget before producing the
    # final answer. Keep a larger but bounded budget and enough context for prompt +
    # reasoning + final JSON. This is generic; GPT-OSS gets the largest safe profile.
    if "gpt-oss" in identity:
        p["think"] = "low"
        p["think_label"] = "low"
        p["num_ctx"] = max(int(p.get("num_ctx", 12288)), 24576)
        p["num_predict"] = max(int(p.get("num_predict", 6144)), 12288)
        p["max_predict"] = 16384
        p["prefer_plain_json"] = True
    elif p.get("thinking"):
        p["num_ctx"] = max(int(p.get("num_ctx", 12288)), 16384)
        p["num_predict"] = max(int(p.get("num_predict", 4096)), 6144)
        p["max_predict"] = 12288
        p["prefer_plain_json"] = False
    else:
        p["max_predict"] = max(int(p.get("num_predict", 4096)), 8192)
        p["prefer_plain_json"] = False
    return p


def _variants_v209(profile: dict[str, Any], json_mode: bool) -> list[dict[str, Any]]:
    # GPT-OSS has shown that format=json can finish reasoning with an empty final
    # content. Prefer prompt-enforced JSON first, then structured JSON as fallback.
    think = profile.get("think")
    out: list[dict[str, Any]] = []

    def add(*, t_marker: str = "profile", fmt: bool = False):
        v: dict[str, Any] = {"format_json": fmt}
        if t_marker == "profile" and think is not None:
            v["think"] = think
        elif t_marker == "omit":
            pass
        key = (repr(v.get("think", "__omit__")), bool(v["format_json"]))
        if key not in {(repr(x.get("think", "__omit__")), bool(x.get("format_json"))) for x in out}:
            out.append(v)

    if json_mode and profile.get("prefer_plain_json"):
        add(t_marker="profile", fmt=False)
        add(t_marker="omit", fmt=False)
        add(t_marker="profile", fmt=True)
        add(t_marker="omit", fmt=True)
    else:
        for v in v206._build_variants(profile, json_mode):
            key = (repr(v.get("think", "__omit__")), bool(v.get("format_json", False)))
            if key not in {(repr(x.get("think", "__omit__")), bool(x.get("format_json", False))) for x in out}:
                out.append(dict(v))
    return out[:4]


def _stream_chat_v209(
    self,
    system: str,
    user: str,
    *,
    model: Optional[str] = None,
    json_mode: bool = False,
    temperature: float = 0.5,
    images: Optional[list[str]] = None,
) -> str:
    model = model or self.resolve_model()
    # Preserve model-aware legacy logging.
    try:
        v206._ACTIVE_MODEL = model
    except Exception:
        pass

    profile = self.compat_profile(model)
    if model not in _PROFILE_LOGGED_209:
        caps_txt = ",".join(sorted(profile.get("caps") or [])) or "non dichiarate"
        self.logger.log(
            f"Profilo LLM v2.0.9: {model} | capability={caps_txt} | thinking={profile.get('thinking')} | "
            f"think={profile.get('think_label')} | ctx={profile.get('num_ctx')} | "
            f"predict={profile.get('num_predict')}..{profile.get('max_predict')} | "
            f"json_preferito={'prompt' if profile.get('prefer_plain_json') else 'structured'}"
        )
        _PROFILE_LOGGED_209.add(model)

    variants = _variants_v209(profile, json_mode)
    last_exc: BaseException | None = None
    total_calls = 0
    max_calls = 6

    for vidx, variant in enumerate(variants, 1):
        if total_calls >= max_calls:
            break

        base_budget = int(profile.get("num_predict", 4096))
        max_budget = int(profile.get("max_predict", base_budget))
        budgets = [base_budget]
        if max_budget > base_budget:
            budgets.append(max_budget)

        for bidx, budget in enumerate(budgets, 1):
            if total_calls >= max_calls:
                break
            total_calls += 1

            messages: list[dict[str, Any]] = [
                {"role": "system", "content": system},
                {"role": "user", "content": user},
            ]
            if images:
                messages[-1]["images"] = images

            # Prompt-enforced JSON is intentionally used for models that are known to
            # be fragile with format=json, while still preserving strict parser QA.
            if json_mode and not variant.get("format_json"):
                messages[-1]["content"] += (
                    "\n\nFORMATO OBBLIGATORIO: restituisci esclusivamente JSON valido nel messaggio finale. "
                    "Niente markdown, spiegazioni o testo prima/dopo il JSON."
                )

            payload: dict[str, Any] = {
                "model": model,
                "messages": messages,
                "stream": True,
                "keep_alive": "15m",
                "options": {
                    "temperature": temperature,
                    "num_ctx": int(profile.get("num_ctx", 12288)),
                    "num_predict": budget,
                },
            }
            if variant.get("format_json"):
                payload["format"] = "json"
            if "think" in variant:
                payload["think"] = variant["think"]

            desc = (
                f"think={variant.get('think', 'omesso')}, "
                f"json={'on' if variant.get('format_json') else 'off'}, predict={budget}"
            )
            started = time.time()
            chunks: list[str] = []
            thinking_chars = 0
            last_obj: dict[str, Any] = {}

            try:
                with self.http.s.post(
                    self.base + "/api/chat",
                    json=payload,
                    stream=True,
                    timeout=(15, 240),
                ) as response:
                    if response.status_code >= 400:
                        raise RuntimeError(f"Ollama HTTP {response.status_code}: {response.text[:1200]}")

                    for line in response.iter_lines(decode_unicode=True):
                        if not line:
                            continue
                        obj = json.loads(line)
                        last_obj = obj if isinstance(obj, dict) else {}
                        if last_obj.get("error"):
                            raise RuntimeError(str(last_obj["error"]))
                        msg = last_obj.get("message") or {}
                        thinking = msg.get("thinking", "") or ""
                        piece = msg.get("content", "") or ""
                        thinking_chars += len(thinking)
                        if piece:
                            chunks.append(piece)
                        if last_obj.get("done") is True:
                            break

                content = "".join(chunks).strip()
                elapsed = time.time() - started
                if content:
                    self.logger.log(
                        f"Ollama {model} completato in {elapsed:.1f}s "
                        f"({len(content)} caratteri; profilo {desc}; thinking={thinking_chars})"
                    )
                    return content

                done_reason = str(last_obj.get("done_reason") or last_obj.get("stop_reason") or "n/d")
                eval_count = last_obj.get("eval_count", "n/d")
                empty = RuntimeError(
                    f"risposta finale vuota (thinking={thinking_chars} caratteri, done_reason={done_reason}, "
                    f"eval_count={eval_count}, profilo {desc})"
                )
                last_exc = empty

                # Important distinction: 'length' is not API incompatibility. Retry
                # the SAME profile once with a larger completion budget before trying
                # a different thinking/JSON mode.
                if done_reason.lower() in ("length", "max_tokens", "max token", "max_tokens_reached") and bidx < len(budgets):
                    self.logger.log(
                        f"Budget LLM esaurito con {model}: reasoning ha consumato predict={budget} senza risposta finale; "
                        f"ritento lo stesso profilo con predict={budgets[bidx]}"
                    )
                    time.sleep(1.0)
                    continue

                raise empty

            except (requests.Timeout, requests.ConnectionError, json.JSONDecodeError, RuntimeError) as exc:
                last_exc = exc
                text = str(exc).lower()
                if any(k in text for k in ("out of memory", "cuda", "oom")):
                    raise RuntimeError(
                        f"Ollama ({model}) ha esaurito memoria GPU/RAM. Prova un modello piu piccolo o riduci le applicazioni GPU."
                    ) from exc
                if isinstance(exc, requests.Timeout):
                    raise RuntimeError(
                        f"Ollama ({model}) non ha inviato dati per 240 secondi; richiesta interrotta."
                    ) from exc

                # If this was a length exhaustion and there is a larger budget queued,
                # let the inner loop retry the same variant instead of changing mode.
                if "done_reason=length" in text and bidx < len(budgets):
                    continue

                if vidx < len(variants):
                    nxt = variants[vidx]
                    self.logger.log(
                        f"Compatibilita LLM {model}: profilo {desc} non ha prodotto una risposta finale; "
                        f"passo a think={nxt.get('think', 'omesso')}, json={'on' if nxt.get('format_json') else 'off'}"
                    )
                    time.sleep(1.0)
                break

    raise RuntimeError(f"Ollama non ha prodotto una risposta finale utilizzabile con {model}: {last_exc}")


core.OllamaClient.compat_profile = _compat_profile_v209
core.OllamaClient.chat = _stream_chat_v209


# Version polish only; keep the v2.0.8 UI/banner/model-aware resume intact.
_previous_build = core.BatchFactoryGUI._build


def _build_v209(self):
    _previous_build(self)
    self.root.title(core.APP_NAME)
    try:
        for widget in v208.v207.v206.v205._walk_widgets(self.root):
            try:
                if str(widget.cget("text")) == "v2.0.8  •  LOCAL":
                    widget.configure(text="v2.0.9  •  LOCAL")
            except Exception:
                continue
    except Exception:
        pass


core.BatchFactoryGUI._build = _build_v209


def self_test() -> None:
    current = core.APP_VERSION
    try:
        core.APP_VERSION = "2.0.8"
        v208.self_test()
    finally:
        core.APP_VERSION = current

    class Dummy:
        def model_info(self, model):
            if "gpt-oss" in model:
                return {"capabilities": ["completion", "thinking", "tools"], "details": {"family": "gptoss"}}
            if "qwen3" in model:
                return {"capabilities": ["completion", "thinking"], "details": {"family": "qwen3"}}
            return {"capabilities": ["completion"], "details": {"family": "llama"}}

    d = Dummy()
    gp = _compat_profile_v209(d, "gpt-oss:20b")
    qp = _compat_profile_v209(d, "qwen3:8b")
    lp = _compat_profile_v209(d, "llama3.2:3b")
    assert gp["think"] == "low"
    assert gp["num_predict"] >= 12288 and gp["max_predict"] >= gp["num_predict"]
    assert gp["num_ctx"] >= 24576 and gp["prefer_plain_json"] is True
    gv = _variants_v209(gp, True)
    assert gv[0]["format_json"] is False and gv[0].get("think") == "low"
    assert qp["thinking"] is True and qp["max_predict"] >= qp["num_predict"]
    assert lp["thinking"] is False
    assert core.APP_VERSION == "2.0.9"
    print("V2.0.9 ADAPTIVE REASONING-BUDGET SELF-TEST PASS")


def main() -> None:
    if "--self-test" in os.sys.argv:
        self_test()
        return
    core.ensure_dirs()
    if core.tk is None:
        raise SystemExit("Tkinter non disponibile")
    core.BatchFactoryGUI().run()


if __name__ == "__main__":
    main()
