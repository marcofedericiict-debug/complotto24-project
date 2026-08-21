from __future__ import annotations

import hashlib
import json
import random
from pathlib import Path

import c24_batch_factory_local_v201 as v201

core = v201.core
core.APP_VERSION = "2.0.2"


def _planning_fingerprint(candidates) -> str:
    raw = "\n".join(f"{c.title}|{c.url}|{c.source}" for c in candidates)
    return hashlib.sha256(raw.encode("utf-8", "ignore")).hexdigest()[:20]


def _planning_state_path(candidates) -> Path:
    return core.STATE_DIR / f"planning-{_planning_fingerprint(candidates)}.json"


def _load_partial_plans(candidates):
    path = _planning_state_path(candidates)
    if not path.exists():
        return []
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        plans = obj.get("plans", []) if isinstance(obj, dict) else []
        return plans if isinstance(plans, list) else []
    except Exception:
        return []


def _save_partial_plans(candidates, plans):
    path = _planning_state_path(candidates)
    path.write_text(
        json.dumps({"fingerprint": _planning_fingerprint(candidates), "plans": plans}, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _delete_partial_plans(candidates):
    _planning_state_path(candidates).unlink(missing_ok=True)


def _validate_plan(self, p, candidates, corpus, accepted_titles):
    if not isinstance(p, dict):
        return None, "elemento non oggetto"
    status = str(p.get("status", "")).strip().lower()
    if status not in ("real", "fake", "unverifiable"):
        return None, "status non valido"

    ids = {c.id for c in candidates}
    src = []
    for x in p.get("source_ids", []) or []:
        try:
            n = int(x)
        except Exception:
            continue
        if n in ids and n not in src:
            src.append(n)
    src = src[:3]
    if status == "real" and not src:
        return None, "reale senza fonte valida"

    title = str(p.get("title", "")).strip()
    if len(title) < 20:
        return None, "titolo troppo corto"
    if any(core.similarity(title, old) > 0.76 for old in corpus):
        return None, "troppo simile allo storico"
    if any(core.similarity(title, old) > 0.72 for old in accepted_titles):
        return None, "duplicato nel batch"
    if status == "fake" and any(b in title.lower() for b in core.SAFE_FAKE_BANS):
        return None, "simulazione potenzialmente dannosa"

    scene = str(p.get("image_scene", "")).strip()
    forbidden_scene = ("dashboard", "tabella", "collage", "articolo", "seo", "wordpress", "json", "quiz")
    if len(scene) < 20 or any(w in scene.lower() for w in forbidden_scene):
        return None, "scena immagine non isolata"

    category = str(p.get("category", "Societa")).strip() or "Societa"
    category_slug = core.slugify(str(p.get("category_slug") or category))[:60]
    angle = str(p.get("angle", "")).strip()

    clean = {
        "title": title,
        "status": status,
        "source_ids": src,
        "category": category[:60],
        "category_slug": category_slug,
        "image_scene": scene,
        "angle": angle[:500],
    }
    return clean, "OK"


def _select_plans_incremental(self, candidates, corpus):
    """Build exactly 10 plans without discarding useful partial LLM output.

    Requests at most 5 plans per round, validates each independently, persists accepted
    plans on disk, then asks only for the missing count. This is deliberately tolerant
    of a model returning fewer items than requested.
    """
    accepted = _load_partial_plans(candidates)
    accepted_titles = []
    clean_accepted = []

    # Revalidate persisted partial plans against current candidate pool/history.
    for p in accepted:
        clean, reason = _validate_plan(self, p, candidates, corpus, accepted_titles)
        if clean:
            clean_accepted.append(clean)
            accepted_titles.append(clean["title"])
    accepted = clean_accepted[:10]

    if accepted:
        self.logger.log(f"Pianificazione resume: recuperati {len(accepted)}/10 temi validi")

    system = (
        "Sei il caporedattore di un progetto italiano di media literacy. "
        "Seleziona idee editoriali recenti, varie, innocue e verificabili quando reali. "
        "Restituisci SOLO JSON valido, senza markdown e senza testo fuori dal JSON."
    )

    max_rounds = 6
    for round_no in range(1, max_rounds + 1):
        if len(accepted) >= 10:
            break

        need = 10 - len(accepted)
        ask = min(5, need)
        avoid_hist = corpus
        if len(avoid_hist) > 35:
            avoid_hist = random.sample(avoid_hist, 35)
        avoid = "\n".join(f"- {x[:160]}" for x in avoid_hist) if avoid_hist else "(nessuno)"
        already = "\n".join(f"- {x}" for x in accepted_titles) if accepted_titles else "(nessuno)"

        user = f'''Genera ESATTAMENTE {ask} nuove idee editoriali, diverse tra loro e diverse da quelle gia accettate.

STATUS ammessi: real, fake, unverifiable.
REGOLE:
- real: source_ids deve contenere 1-3 ID realmente presenti nel POOL.
- fake: solo simulazioni innocue; vietati morte, reati, malattie, emergenze, bonus/pagamenti, banche, danni reputazionali o istruzioni pericolose riferite a persone/enti reali.
- unverifiable: solo temi prudenti senza conseguenze concrete.
- Titoli SEO naturali e credibili, non trash.
- image_scene: UNA sola scena fisica fotografabile. Niente testo, UI, collage, dashboard, tabella, articolo, SEO, WordPress, JSON o quiz.
- Non ripetere temi gia accettati.

GIA ACCETTATI:\n{already}

STORICO DA EVITARE:\n{avoid}

POOL RECENTE:\n{self._candidate_packet(candidates)}

Restituisci ESCLUSIVAMENTE questo schema JSON:
{{"plans":[{{"title":"...","status":"real|fake|unverifiable","source_ids":[1],"category":"...","category_slug":"...","image_scene":"...","angle":"..."}}]}}
Il campo plans deve contenere {ask} elementi.'''

        self.logger.log(
            f"Pianificazione incrementale round {round_no}/{max_rounds}: richiesti {ask}, gia validi {len(accepted)}/10"
        )
        try:
            data = self.ollama.chat_json(system, user, temperature=0.55)
        except Exception as exc:
            self.logger.log(f"Pianificazione round {round_no}: errore Ollama/JSON: {exc}")
            continue

        raw_plans = data.get("plans", []) if isinstance(data, dict) else []
        if not isinstance(raw_plans, list):
            raw_plans = []
        self.logger.log(f"Pianificazione round {round_no}: Qwen ha restituito {len(raw_plans)} elementi")

        added = 0
        rejected = 0
        for p in raw_plans:
            if len(accepted) >= 10:
                break
            clean, reason = _validate_plan(self, p, candidates, corpus, accepted_titles)
            if clean:
                accepted.append(clean)
                accepted_titles.append(clean["title"])
                added += 1
                _save_partial_plans(candidates, accepted)
            else:
                rejected += 1
                self.logger.log(f"Piano scartato: {reason}")

        self.logger.log(
            f"Pianificazione round {round_no}: +{added} validi, {rejected} scartati; totale {len(accepted)}/10"
        )

        # If Qwen returned nothing useful, next round still requests only the missing set.
        if added == 0 and round_no >= 3 and len(accepted) == 0:
            # Give a clearer failure instead of spending six full rounds with zero progress.
            raise RuntimeError("Qwen non ha prodotto alcun piano editoriale valido dopo 3 round")

    if len(accepted) != 10:
        _save_partial_plans(candidates, accepted)
        raise RuntimeError(
            f"Pianificazione incompleta: ottenuti {len(accepted)}/10 temi validi. "
            "I temi validi sono stati salvati e verranno ripresi al prossimo avvio."
        )

    _delete_partial_plans(candidates)
    self.logger.log("Pianificazione completata: 10/10 temi validi")
    return accepted


core.BatchEngine.select_plans = _select_plans_incremental


def main() -> None:
    core.main()


if __name__ == "__main__":
    main()
