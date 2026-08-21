from __future__ import annotations

import json
import time
from typing import Any, Optional

import requests

import c24_batch_factory_local as core

# v2.0.1 runtime hardening for large local Ollama models + 12 GB GPUs.
core.APP_VERSION = "2.0.1"
core.MAX_TEXT_RETRIES = 2


def _streaming_chat(self, system: str, user: str, *, model: Optional[str] = None,
                    json_mode: bool = False, temperature: float = 0.5,
                    images: Optional[list[str]] = None) -> str:
    """Use Ollama streaming so long generations do not hit a false whole-response timeout."""
    model = model or self.resolve_model()
    messages: list[dict[str, Any]] = [
        {"role": "system", "content": system},
        {"role": "user", "content": user},
    ]
    if images:
        messages[-1]["images"] = images

    payload: dict[str, Any] = {
        "model": model,
        "messages": messages,
        "stream": True,
        "keep_alive": "15m",
        "options": {
            "temperature": temperature,
            # 32k was wasteful for this workflow and can force extra CPU/RAM offload.
            "num_ctx": 12288,
            "num_predict": 3072,
        },
    }
    if json_mode:
        payload["format"] = "json"

    # Qwen-family thinking can dramatically extend JSON generation. Ollama supports
    # think=false on compatible models. If a custom model rejects it, retry without it.
    payload["think"] = False
    last: BaseException | None = None

    for attempt in range(1, 3):
        try:
            started = time.time()
            with self.http.s.post(
                self.base + "/api/chat",
                json=payload,
                stream=True,
                timeout=(15, 150),  # connect timeout, max silence between streamed chunks
            ) as response:
                if response.status_code >= 400:
                    text = response.text[:1000]
                    if response.status_code == 400 and "think" in payload:
                        payload.pop("think", None)
                        self.logger.log("Il modello non accetta think=false: riprovo in modalita compatibile")
                        continue
                    response.raise_for_status()
                    raise RuntimeError(f"Ollama HTTP {response.status_code}: {text}")

                chunks: list[str] = []
                for line in response.iter_lines(decode_unicode=True):
                    if not line:
                        continue
                    obj = json.loads(line)
                    if obj.get("error"):
                        raise RuntimeError(str(obj["error"]))
                    piece = (obj.get("message") or {}).get("content", "")
                    if piece:
                        chunks.append(piece)
                    if obj.get("done") is True:
                        break

                content = "".join(chunks).strip()
                if not content:
                    raise RuntimeError("Ollama ha restituito una risposta vuota")
                self.logger.log(f"Ollama completato in {time.time()-started:.1f}s ({len(content)} caratteri)")
                return content
        except (requests.Timeout, requests.ConnectionError, json.JSONDecodeError, RuntimeError) as exc:
            last = exc
            low = str(exc).lower()
            if any(k in low for k in ("out of memory", "cuda", "oom")):
                raise RuntimeError(
                    "Ollama ha esaurito memoria GPU/RAM. Chiudi applicazioni GPU oppure usa un modello piu piccolo."
                ) from exc
            # A timeout can mean the old server job is still winding down; do not stack many requests.
            if isinstance(exc, requests.Timeout):
                raise RuntimeError(
                    "Ollama non ha inviato dati per 150 secondi. La richiesta e stata interrotta senza avviare retry annidati."
                ) from exc
            if attempt < 2:
                self.logger.log(f"Ollama retry controllato {attempt}/2: {exc}")
                time.sleep(3)

    raise RuntimeError(f"Ollama non ha completato la richiesta: {last}")


def _unload(self, model: Optional[str] = None) -> None:
    """Explicitly free Ollama VRAM before ComfyUI loads an SDXL checkpoint."""
    name = model or self.cfg.ollama_model
    try:
        # Ollama unload convention: keep_alive=0. A tiny generate request is enough.
        r = self.http.s.post(
            self.base + "/api/generate",
            json={"model": name, "prompt": "", "stream": False, "keep_alive": 0},
            timeout=(10, 45),
        )
        r.raise_for_status()
        self.logger.log(f"Ollama scaricato dalla memoria prima di ComfyUI: {name}")
        time.sleep(2)
    except Exception as exc:
        # Not fatal: ComfyUI's own OOM handler remains available.
        self.logger.log(f"Avviso: unload Ollama non riuscito: {exc}")


def _candidate_packet_small(self, candidates, limit=42):
    # Preserve source diversity before sending a compact packet to the 27B model.
    by_source: dict[str, list[Any]] = {}
    for c in candidates:
        by_source.setdefault(c.source, []).append(c)
    chosen: list[Any] = []
    sources = list(by_source)
    cursor = 0
    while len(chosen) < limit and sources:
        source = sources[cursor % len(sources)]
        bucket = by_source[source]
        if bucket:
            chosen.append(bucket.pop(0))
        if not bucket:
            sources.remove(source)
            cursor = 0
        else:
            cursor += 1
    return "\n".join(
        f"[{c.id}] {c.title[:180]} | {c.source} | {c.published} | {c.url}"
        for c in chosen[:limit]
    )


_original_select_plans = core.BatchEngine.select_plans


def _select_plans_compact(self, candidates, corpus):
    # Temporarily pass a compact, representative history. The existing validator still
    # compares the generated titles against the complete corpus afterwards.
    compact_corpus = corpus
    if len(corpus) > 40:
        import random
        compact_corpus = random.sample(corpus, 40)
    return _original_select_plans(self, candidates, compact_corpus)


_original_generate_images = core.BatchEngine.generate_images


def _generate_images_vram_safe(self, posts, work_images):
    # Text generation and SDXL should never compete for the RTX 4070 Super's VRAM.
    self.ollama.unload()
    return _original_generate_images(self, posts, work_images)


_original_semantic_image_qa = core.BatchEngine.semantic_image_qa


def _semantic_image_qa_opt_in(self, path, expected_scene):
    # Avoid repeatedly swapping a large vision LLM into VRAM unless explicitly requested.
    if not self.cfg.require_vision_qa:
        return True, "QA vision locale disabilitato; QA tecnico e isolamento ComfyUI attivi"
    return _original_semantic_image_qa(self, path, expected_scene)


core.OllamaClient.chat = _streaming_chat
core.OllamaClient.unload = _unload
core.BatchEngine._candidate_packet = _candidate_packet_small
core.BatchEngine.select_plans = _select_plans_compact
core.BatchEngine.generate_images = _generate_images_vram_safe
core.BatchEngine.semantic_image_qa = _semantic_image_qa_opt_in


def main() -> None:
    core.main()


if __name__ == "__main__":
    main()
