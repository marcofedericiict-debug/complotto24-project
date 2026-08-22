from __future__ import annotations

import json
import os
import random
import re
import uuid

import c24_batch_factory_local_v209 as v209

core = v209.core
core.APP_VERSION = "2.0.10"
core.APP_NAME = "Complotto24 Batch Factory LOCAL v2.0.10"

ARTICLE_MIN_WORDS = 320
ARTICLE_TARGET_MIN = 450
ARTICLE_TARGET_MAX = 650
MAX_ARTICLE_GENERATIONS = 2
MAX_LENGTH_REPAIRS = 2


def _word_count(html_text: str) -> int:
    return len(core.strip_html(html_text or "").split())


def _article_precheck_reason(obj: dict) -> str:
    required = ("title", "slug", "excerpt", "content", "image_alt", "image_prompt", "categories", "status")
    missing = [k for k in required if not obj.get(k)]
    if missing:
        return "campi mancanti: " + ", ".join(missing)

    wc = _word_count(str(obj.get("content", "")))
    if wc < ARTICLE_MIN_WORDS:
        return f"contenuto troppo corto: {wc} parole, minimo editoriale {ARTICLE_MIN_WORDS}"

    pre = " ".join(str(obj.get(k, "")) for k in ("title", "excerpt", "content"))
    for pat in core.ANTI_SPOILER:
        if re.search(pat, pre, re.I):
            return f"anti-spoiler: pattern {pat}"

    if obj.get("status") == "real" and not obj.get("source_urls"):
        return "notizia reale senza fonti"

    if obj.get("status") == "fake":
        plain = core.strip_html(pre).lower()
        if any(b in plain for b in core.SAFE_FAKE_BANS):
            return "simulazione non conforme alle regole safety"

    cats = obj.get("categories")
    if not isinstance(cats, list) or not cats:
        return "categorie mancanti o non valide"
    if not any(isinstance(c, list) and len(c) >= 2 for c in cats[:2]):
        return "formato categorie non valido"
    return "OK"


def _prepare_article_obj(obj: dict, plan: dict, sources: list[dict], used_slugs: set[str]) -> dict:
    obj = dict(obj)
    obj["title"] = str(obj.get("title") or plan["title"]).strip()
    slug = core.slugify(str(obj.get("slug") or obj["title"]))
    if slug in used_slugs:
        slug += "-" + uuid.uuid4().hex[:6]
    obj["slug"] = slug
    obj["status"] = plan["status"]
    obj["editor"] = random.choice(core.EDITORS)
    obj["source_urls"] = [s["url"] for s in sources]
    obj["image"] = slug + ".jpg"
    return obj


def _repair_length(self, obj: dict, plan: dict, sources: list[dict]) -> dict:
    """Expand only article body, preserving all metadata and factual boundaries."""
    current = dict(obj)
    source_packet = json.dumps(sources, ensure_ascii=False)

    for repair_no in range(1, MAX_LENGTH_REPAIRS + 1):
        wc = _word_count(str(current.get("content", "")))
        if wc >= ARTICLE_MIN_WORDS:
            return current

        self.logger.log(
            f"Repair lunghezza {repair_no}/{MAX_LENGTH_REPAIRS}: {current.get('title')} | "
            f"{wc} parole -> target {ARTICLE_TARGET_MIN}-{ARTICLE_TARGET_MAX}"
        )

        system = (
            "Sei un editor italiano. Devi espandere SOLO il corpo HTML di un articolo senza cambiare titolo, "
            "tesi, status o fatti. Restituisci SOLO JSON valido con la chiave content."
        )
        user = f'''ARTICOLO DA ESPANDERE:
Titolo: {current.get("title", "")}
Status interno: {plan.get("status")}
Contenuto attuale ({wc} parole):
{current.get("content", "")}

FONTI CONSENTITE (unica base fattuale se status=real):
{source_packet}

REGOLE OBBLIGATORIE:
- Riscrivi il SOLO campo content in {ARTICLE_TARGET_MIN}-{ARTICLE_TARGET_MAX} parole, 5-7 paragrafi HTML <p>...</p>.
- Mantieni il significato dell'articolo e non aggiungere fatti, numeri, citazioni o dettagli non supportati se status=real.
- Se status=fake, resta nella stessa simulazione innocua: niente falsa morte, reati, salute, denaro, emergenze, accuse o danni reputazionali.
- Nel testo pre-quiz non usare mai: fake, inventata/o, fittizia/o, simulazione, scenario inventato, non reale, non verificabile, Complotto24.
- Non parlare del quiz, del reveal, delle regole editoriali o del processo di generazione.
- Non inserire heading, fonti o note fuori dai paragrafi.

JSON ESATTO:
{{"content":"<p>...</p><p>...</p>"}}'''

        try:
            repaired = self.ollama.chat_json(system, user, temperature=0.35)
        except Exception as exc:
            self.logger.log(f"Repair lunghezza {repair_no} fallito lato LLM/JSON: {exc}")
            continue

        if isinstance(repaired, dict) and str(repaired.get("content", "")).strip():
            current["content"] = str(repaired["content"]).strip()
            new_wc = _word_count(current["content"])
            self.logger.log(
                f"Repair lunghezza {repair_no}: risultato {new_wc} parole per {current.get('title')}"
            )
        else:
            self.logger.log(f"Repair lunghezza {repair_no}: risposta senza content utilizzabile")

    return current


def _generate_article_v210(self, plan, candidates, used_slugs):
    sources = self._sources_for_plan(plan, candidates)
    safe_source = json.dumps(sources, ensure_ascii=False)
    system = (
        "Sei un giornalista italiano. Scrivi contenuti chiari, plausibili, SEO-friendly e adatti a un progetto "
        "di media literacy. Rispetta rigidamente la separazione tra testo pre-quiz e reveal. Restituisci SOLO JSON."
    )
    base_user = f'''Crea UN articolo completo a partire da questo piano:
{json.dumps(plan, ensure_ascii=False)}

FONTI DISPONIBILI (unica base fattuale se status=real):
{safe_source}

REGOLE PRE-QUIZ:
- Non usare mai fake, inventata/o, fittizia/o, simulazione, scenario inventato, non reale, non verificabile, Complotto24.
- Non suggerire che sia un test.
- content deve avere {ARTICLE_TARGET_MIN}-{ARTICLE_TARGET_MAX} parole REALI, 5-7 paragrafi HTML <p>...</p>.
- Non comprimere l'articolo in un riassunto: sviluppa contesto, fatto centrale, dettagli disponibili e conseguenze prudenti.
- Non inventare dettagli nelle notizie reali non supportati dalle fonti.
- Per fake: storia innocua senza perdite economiche, allarmi sanitari, accuse, danni reputazionali o rischi concreti.
- image_prompt: pura scena fisica, una sola fotografia, nessun testo visibile.

Restituisci ESATTAMENTE:
{{"title":"...","slug":"...","excerpt":"...","content":"<p>...</p>...","reveal_explanation":"...","image_alt":"...","image_prompt":"...","categories":[["Nome","slug"]]}}'''

    user = base_user
    last_reason = "nessuna risposta valida"

    for generation_no in range(1, MAX_ARTICLE_GENERATIONS + 1):
        obj = self.ollama.chat_json(system, user, temperature=0.55)
        if not isinstance(obj, dict):
            last_reason = "risposta non-oggetto"
            self.logger.log(f"QA articolo {generation_no}: {last_reason}")
            user = base_user + "\n\nCORREZIONE: la risposta precedente non era un oggetto JSON valido."
            continue

        obj = _prepare_article_obj(obj, plan, sources, used_slugs)
        wc_before = _word_count(str(obj.get("content", "")))
        self.logger.log(
            f"QA lunghezza articolo: {obj.get('title')} | {wc_before} parole "
            f"(minimo {ARTICLE_MIN_WORDS}, target {ARTICLE_TARGET_MIN}-{ARTICLE_TARGET_MAX})"
        )

        if wc_before < ARTICLE_MIN_WORDS:
            obj = _repair_length(self, obj, plan, sources)

        wc_after = _word_count(str(obj.get("content", "")))
        reason = _article_precheck_reason(obj)
        if reason == "OK" and self.validate_article(obj):
            self.logger.log(f"QA articolo PASS: {obj.get('title')} | {wc_after} parole")
            used_slugs.add(obj["slug"])
            obj["reveal"] = self.build_reveal(obj)
            obj.pop("source_urls", None)
            obj.pop("reveal_explanation", None)
            return obj

        last_reason = reason if reason != "OK" else "validator core non superato"
        self.logger.log(
            f"QA articolo FAIL generazione {generation_no}/{MAX_ARTICLE_GENERATIONS}: "
            f"{obj.get('title')} | {last_reason}"
        )
        user = base_user + (
            f"\n\nCORREZIONE OBBLIGATORIA: la versione precedente ha fallito il QA per: {last_reason}. "
            "Rigenera l'articolo completo correggendo specificamente questo problema."
        )

    raise RuntimeError(
        f"Articolo non valido dopo {MAX_ARTICLE_GENERATIONS} generazioni con repair mirato: "
        f"{plan['title']} | ultimo motivo: {last_reason}"
    )


core.BatchEngine.generate_article = _generate_article_v210


# Version polish while keeping v2.0.9 adaptive LLM + v2.0.8 resume/banner UI.
_previous_build = core.BatchFactoryGUI._build


def _build_v210(self):
    _previous_build(self)
    self.root.title(core.APP_NAME)
    try:
        walker = v209.v208.v207.v206.v205._walk_widgets
        for widget in walker(self.root):
            try:
                if str(widget.cget("text")) == "v2.0.9  •  LOCAL":
                    widget.configure(text="v2.0.10  •  LOCAL")
            except Exception:
                continue
    except Exception:
        pass


core.BatchFactoryGUI._build = _build_v210


def self_test() -> None:
    current = core.APP_VERSION
    try:
        core.APP_VERSION = "2.0.9"
        v209.self_test()
    finally:
        core.APP_VERSION = current

    assert _word_count("<p>uno due tre</p><p>quattro cinque</p>") == 5
    sample = {
        "title": "Titolo sufficientemente lungo per test",
        "slug": "titolo-test",
        "excerpt": "Excerpt",
        "content": "<p>" + "parola " * ARTICLE_MIN_WORDS + "</p>",
        "image_alt": "alt",
        "image_prompt": "single physical scene in an Italian city street",
        "categories": [["Societa", "societa"]],
        "status": "fake",
        "source_urls": [],
    }
    assert _article_precheck_reason(sample) == "OK"
    short = dict(sample)
    short["content"] = "<p>troppo corto</p>"
    assert "troppo corto" in _article_precheck_reason(short)
    assert core.APP_VERSION == "2.0.10"
    print("V2.0.10 TARGETED ARTICLE REPAIR SELF-TEST PASS")


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
