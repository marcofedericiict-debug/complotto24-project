from __future__ import annotations

import json
import os
import random
import re

import c24_batch_factory_local_v211 as v211

core = v211.core
v210 = v211.v210
core.APP_VERSION = "2.0.12"
core.APP_NAME = "Complotto24 Batch Factory LOCAL v2.0.12"

MAX_ANTI_SPOILER_REPAIRS = 2


def _anti_spoiler_hits(obj: dict) -> list[tuple[str, str, str]]:
    hits: list[tuple[str, str, str]] = []
    for field in ("title", "excerpt", "content"):
        text = str(obj.get(field, "") or "")
        for pat in core.ANTI_SPOILER:
            m = re.search(pat, text, re.I)
            if m:
                hits.append((field, pat, m.group(0)))
    return hits


def _lexical_cleanup(text: str) -> str:
    """Last-resort local cleanup for pre-quiz spoiler lexemes.

    This deliberately preserves the underlying claim and only replaces words that
    are forbidden before reveal. The LLM repair runs first; this is a safety net.
    """
    rules = [
        (r"\bplausibile ma inventat[ao]\b", "plausibile ma ipotetico"),
        (r"\bscenario inventato\b", "scenario ipotetico"),
        (r"\bcreat[ao] per complotto24\b", "preparato per il progetto"),
        (r"\bnon verificabile\b", "ancora da chiarire"),
        (r"\bnon reale\b", "ricostruito"),
        (r"\bsimulazioni\b", "test"),
        (r"\bsimulazione\b", "test"),
        (r"\bfittizie\b", "ipotetiche"),
        (r"\bfittizi\b", "ipotetici"),
        (r"\bfittizia\b", "ipotetica"),
        (r"\bfittizio\b", "ipotetico"),
        (r"\binventate\b", "ipotetiche"),
        (r"\binventati\b", "ipotetici"),
        (r"\binventata\b", "ipotetica"),
        (r"\binventato\b", "ipotetico"),
        (r"\bfake\b", "ingannevole"),
        (r"\bcomplotto24\b", "il progetto"),
    ]
    out = text
    for pat, repl in rules:
        out = re.sub(pat, repl, out, flags=re.I)
    return out


def _repair_anti_spoiler(self, obj: dict, plan: dict, sources: list[dict]) -> dict:
    current = dict(obj)
    hits = _anti_spoiler_hits(current)
    if not hits:
        return current

    source_packet = json.dumps(sources, ensure_ascii=False)
    for repair_no in range(1, MAX_ANTI_SPOILER_REPAIRS + 1):
        hits = _anti_spoiler_hits(current)
        if not hits:
            return current

        hit_text = "; ".join(f"{field}: {token!r} ({pat})" for field, pat, token in hits[:12])
        self.logger.log(
            f"Repair anti-spoiler {repair_no}/{MAX_ANTI_SPOILER_REPAIRS}: "
            f"{current.get('title')} | {hit_text}"
        )

        system = (
            "Sei un editor italiano. Devi rimuovere parole che anticipano la classificazione editoriale, "
            "senza cambiare fatti, significato, tono o struttura HTML. Restituisci SOLO JSON valido."
        )
        user = f'''ARTICOLO DA CORREGGERE:
Titolo: {current.get("title", "")}
Excerpt: {current.get("excerpt", "")}
Content: {current.get("content", "")}

STATUS INTERNO (NON DEVE MAI COMPARIRE NEL TESTO PRE-QUIZ): {plan.get("status")}
FONTI CONSENTITE se status=real:
{source_packet}

TERMINI/PATTERN TROVATI DAL QA:
{hit_text}

REGOLE:
- Riscrivi SOLO le frasi necessarie a eliminare i termini vietati.
- Mantieni invariati tutti i fatti, numeri, nomi, date e relazioni causali.
- Se il termine e usato in senso tecnico (per esempio una simulazione scientifica), sostituiscilo con una formulazione neutra come test, prova, ambiente sperimentale o scenario ricreato, scegliendo quella grammaticalmente corretta.
- Non aggiungere spiegazioni sul progetto, sul quiz, sul reveal o sul processo editoriale.
- Mantieni content in HTML con gli stessi paragrafi, per quanto possibile.
- Non usare in title/excerpt/content: fake, inventata/o, fittizia/o, simulazione/i, non reale, non verificabile, Complotto24.

JSON ESATTO:
{{"title":"...","excerpt":"...","content":"<p>...</p>..."}}'''
        try:
            repaired = self.ollama.chat_json(system, user, temperature=0.2)
        except Exception as exc:
            self.logger.log(f"Repair anti-spoiler {repair_no}: errore LLM/JSON: {exc}")
            continue

        if isinstance(repaired, dict):
            for key in ("title", "excerpt", "content"):
                value = repaired.get(key)
                if isinstance(value, str) and value.strip():
                    current[key] = value.strip()
            remaining = _anti_spoiler_hits(current)
            self.logger.log(
                f"Repair anti-spoiler {repair_no}: "
                f"{'PASS' if not remaining else f'{len(remaining)} occorrenze residue'}"
            )
            if not remaining:
                return current

    # Deterministic safety net: do not kill a whole batch because a model insists on
    # repeating a forbidden technical word after two targeted repair attempts.
    before = len(_anti_spoiler_hits(current))
    for key in ("title", "excerpt", "content"):
        current[key] = _lexical_cleanup(str(current.get(key, "") or ""))
    after_hits = _anti_spoiler_hits(current)
    self.logger.log(
        f"Fallback anti-spoiler locale: {before} -> {len(after_hits)} occorrenze per {current.get('title')}"
    )
    return current


def _generate_article_v212(self, plan, candidates, used_slugs):
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
- Non usare mai fake, inventata/o, fittizia/o, simulazione/i, scenario inventato, non reale, non verificabile, Complotto24.
- Se devi descrivere una simulazione tecnica/scientifica usa parole neutrali come test, prova, ambiente sperimentale o scenario ricreato.
- Non suggerire che sia un test editoriale.
- content deve avere {v210.ARTICLE_TARGET_MIN}-{v210.ARTICLE_TARGET_MAX} parole REALI, 5-7 paragrafi HTML <p>...</p>.
- Non comprimere l'articolo in un riassunto: sviluppa contesto, fatto centrale, dettagli disponibili e conseguenze prudenti.
- Non inventare dettagli nelle notizie reali non supportati dalle fonti.
- Per fake: storia innocua senza perdite economiche, allarmi sanitari, accuse, danni reputazionali o rischi concreti.
- image_prompt: pura scena fisica, una sola fotografia, nessun testo visibile.

Restituisci ESATTAMENTE:
{{"title":"...","slug":"...","excerpt":"...","content":"<p>...</p>...","reveal_explanation":"...","image_alt":"...","image_prompt":"...","categories":[["Nome","slug"]]}}'''

    user = base_user
    last_reason = "nessuna risposta valida"
    for generation_no in range(1, v210.MAX_ARTICLE_GENERATIONS + 1):
        obj = self.ollama.chat_json(system, user, temperature=0.55)
        if not isinstance(obj, dict):
            last_reason = "risposta non-oggetto"
            self.logger.log(f"QA articolo {generation_no}: {last_reason}")
            user = base_user + "\n\nCORREZIONE: la risposta precedente non era un oggetto JSON valido."
            continue

        obj = v210._prepare_article_obj(obj, plan, sources, used_slugs)
        wc_before = v210._word_count(str(obj.get("content", "")))
        self.logger.log(
            f"QA lunghezza articolo: {obj.get('title')} | {wc_before} parole "
            f"(minimo {v210.ARTICLE_MIN_WORDS}, target {v210.ARTICLE_TARGET_MIN}-{v210.ARTICLE_TARGET_MAX})"
        )
        if wc_before < v210.ARTICLE_MIN_WORDS:
            obj = v210._repair_length(self, obj, plan, sources)

        # New in 2.0.12: fix spoiler lexemes before the strict validator sees them.
        if _anti_spoiler_hits(obj):
            obj = _repair_anti_spoiler(self, obj, plan, sources)

        wc_after = v210._word_count(str(obj.get("content", "")))
        reason = v210._article_precheck_reason(obj)
        if reason == "OK" and self.validate_article(obj):
            self.logger.log(f"QA articolo PASS: {obj.get('title')} | {wc_after} parole")
            used_slugs.add(obj["slug"])
            obj["reveal"] = self.build_reveal(obj)
            obj.pop("source_urls", None)
            obj.pop("reveal_explanation", None)
            return obj

        last_reason = reason if reason != "OK" else "validator core non superato"
        self.logger.log(
            f"QA articolo FAIL generazione {generation_no}/{v210.MAX_ARTICLE_GENERATIONS}: "
            f"{obj.get('title')} | {last_reason}"
        )
        user = base_user + (
            f"\n\nCORREZIONE OBBLIGATORIA: la versione precedente ha fallito il QA per: {last_reason}. "
            "Rigenera l'articolo completo correggendo specificamente questo problema."
        )

    raise RuntimeError(
        f"Articolo non valido dopo {v210.MAX_ARTICLE_GENERATIONS} generazioni con repair mirati: "
        f"{plan['title']} | ultimo motivo: {last_reason}"
    )


core.BatchEngine.generate_article = _generate_article_v212


_previous_build = core.BatchFactoryGUI._build


def _walk(widget):
    yield widget
    try:
        for child in widget.winfo_children():
            yield from _walk(child)
    except Exception:
        return


def _build_v212(self):
    _previous_build(self)
    self.root.title(core.APP_NAME)
    for widget in _walk(self.root):
        try:
            text = str(widget.cget("text"))
        except Exception:
            continue
        if "v2.0.11" in text:
            try:
                widget.configure(text=text.replace("v2.0.11", "v2.0.12"))
            except Exception:
                pass


core.BatchFactoryGUI._build = _build_v212


def self_test() -> None:
    current = core.APP_VERSION
    try:
        core.APP_VERSION = "2.0.11"
        v211.self_test()
    finally:
        core.APP_VERSION = current

    sample = {
        "title": "Startup italiana prova coltivazioni spaziali",
        "excerpt": "Una simulazione scientifica di microgravita viene usata per i test.",
        "content": "<p>Le simulazioni al computer accompagnano una simulazione di microgravita.</p>",
    }
    assert _anti_spoiler_hits(sample)
    cleaned = {k: _lexical_cleanup(v) for k, v in sample.items()}
    assert not _anti_spoiler_hits(cleaned)
    assert "test" in cleaned["content"].lower()
    assert core.APP_VERSION == "2.0.12"
    print("V2.0.12 TARGETED ANTI-SPOILER REPAIR SELF-TEST PASS")


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
