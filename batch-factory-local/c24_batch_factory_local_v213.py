from __future__ import annotations

import json
import os
import re
from typing import Any

import c24_batch_factory_local_v212 as v212
import c24_batch_factory_local_v203 as v203

core = v212.core
core.APP_VERSION = "2.0.13"
core.APP_NAME = "Complotto24 Batch Factory LOCAL v2.0.13"

_REJECTED_PLAN_TITLES: list[str] = []

# Fake news must be plausible but harmless. These are not banned subjects in real
# reporting; they are banned as fabricated institutional/financial attributions.
FAKE_OFFICIAL_PATTERNS = (
    r"\blingua ufficiale\b",
    r"\bmoneta ufficiale\b",
    r"\bcittadinanza\b",
    r"\bpassaporto\b",
    r"\bsindac[oa]\b",
    r"\bconsiglio comunale\b",
    r"\bcomune di\b",
    r"\bregione\b",
    r"\bminister[oa]\b",
    r"\bgoverno\b",
    r"\bparlamento\b",
    r"\buniversit[àa]\b",
    r"\bateneo\b",
    r"\bprofessor(?:e|essa)?\b",
    r"\bpolizia\b",
    r"\bcarabinieri\b",
    r"\bospedale\b",
    r"\binps\b",
    r"\bagenzia delle entrate\b",
    r"\bcommissione europea\b",
    r"\bunione europea\b",
    r"\bfondi europe[io]\b",
    r"\bfinanziat[oaie]\b",
    r"\bstanziat[oaie]\b",
    r"\bdelibera\b",
    r"\bordinanza\b",
    r"\bdecreto\b",
    r"\blegge\b",
    r"\bautorit[àa]\b",
)

FAKE_QUOTE_PATTERNS = (
    r"[\"“”«»]",
    r"\bha dichiarato\b",
    r"\bha detto\b",
    r"\bha spiegato\b",
    r"\bsecondo il sindaco\b",
    r"\bsecondo la professoressa\b",
    r"\bsecondo il professore\b",
)

FAKE_FINANCE_PATTERNS = (
    r"\bmilion[ei]\b",
    r"\bmiliard[oi]\b",
    r"\beuro\b",
    r"€",
    r"\binvestiment[oi]\b",
    r"\bfinanziament[oi]\b",
)


def _fake_plan_hard_reason(plan: dict) -> str | None:
    if str(plan.get("status", "")).lower() != "fake":
        return None
    text = " ".join(str(plan.get(k, "") or "") for k in ("title", "angle"))
    low = text.lower()
    for pat in FAKE_OFFICIAL_PATTERNS:
        if re.search(pat, low, re.I):
            return f"attribuzione istituzionale/ufficiale non ammessa ({pat})"
    for pat in FAKE_QUOTE_PATTERNS:
        if re.search(pat, text, re.I):
            return f"citazione/attribuzione personale non ammessa ({pat})"
    for pat in FAKE_FINANCE_PATTERNS:
        if re.search(pat, low, re.I):
            return f"claim economico/finanziario non ammesso ({pat})"
    return None


_base_validate_plan = v203._validate_plan_v203


def _validate_plan_v213(self, p, candidates, corpus, accepted):
    clean, reason = _base_validate_plan(self, p, candidates, corpus, accepted)
    if not clean:
        return clean, reason
    hard = _fake_plan_hard_reason(clean)
    if hard:
        return None, "Plausibility Firewall: " + hard
    return clean, reason


# v203._select_plans_v203 resolves this global at runtime, so this tightens every
# incremental planning round without duplicating the whole planner implementation.
v203._validate_plan_v203 = _validate_plan_v213


def _plan_semantic_audit(self, plans: list[dict]) -> tuple[bool, list[str]]:
    targets = []
    for idx, p in enumerate(plans):
        if p.get("status") in ("fake", "unverifiable"):
            targets.append({
                "index": idx,
                "title": p.get("title", ""),
                "status": p.get("status", ""),
                "angle": p.get("angle", ""),
            })
    if not targets:
        return True, []

    system = (
        "Sei un senior editor italiano incaricato di scartare idee di media literacy troppo assurde, satiriche, "
        "grammaticalmente scadenti o rischiose. Valuta severamente. Restituisci SOLO JSON valido."
    )
    user = f'''Valuta queste idee PRIMA che diventino articoli:\n{json.dumps(targets, ensure_ascii=False)}\n\nCRITERI OBBLIGATORI:\n- Una fake deve sembrare una notizia plausibile almeno per alcuni minuti a un lettore adulto: plausibility_score minimo 7/10.\n- Scarta idee da satira/parodia o assurde per costruzione (es. un alimento dichiarato lingua ufficiale).\n- Scarta fake che inventano decisioni, leggi, ordinanze, finanziamenti, fondi, dichiarazioni o citazioni attribuite a persone, enti, universita, comuni, governi o istituzioni reali.\n- Scarta fake che richiederebbero nomi e cognomi inventati per sembrare credibili.\n- La grammatica e il lessico italiani devono essere naturali: language_score minimo 8/10.\n- unverifiable deve restare prudente, plausibile e non trasformare ipotesi in fatto.\n- safe deve essere false se la storia puo causare danno concreto, reputazionale, sanitario, finanziario o allarme.\n\nJSON ESATTO:\n{{"results":[{{"index":0,"plausibility_score":8,"language_score":9,"satire_like":false,"invented_attribution":false,"official_action":false,"safe":true,"pass":true,"reason":"..."}}]}}'''

    try:
        data = self.ollama.chat_json(system, user, temperature=0.1)
    except Exception as exc:
        return False, [f"audit LLM non disponibile: {exc}"]

    results = data.get("results", []) if isinstance(data, dict) else []
    by_index = {}
    for r in results if isinstance(results, list) else []:
        if not isinstance(r, dict):
            continue
        try:
            by_index[int(r.get("index"))] = r
        except Exception:
            continue

    failures: list[str] = []
    for item in targets:
        idx = int(item["index"])
        r = by_index.get(idx)
        if not r:
            failures.append(f"#{idx+1} {item['title']}: audit mancante")
            continue
        plaus = float(r.get("plausibility_score", 0) or 0)
        lang = float(r.get("language_score", 0) or 0)
        passed = bool(r.get("pass"))
        unsafe_flags = any(bool(r.get(k)) for k in ("satire_like", "invented_attribution", "official_action"))
        safe = bool(r.get("safe"))
        if not passed or plaus < 7 or lang < 8 or unsafe_flags or not safe:
            reason = str(r.get("reason", "non conforme"))[:240]
            failures.append(
                f"#{idx+1} {item['title']}: plaus={plaus:g}/10 lingua={lang:g}/10 - {reason}"
            )
    return not failures, failures


def _select_plans_v213(self, candidates, corpus):
    extended_corpus = list(corpus) + list(_REJECTED_PLAN_TITLES)
    plans = v203._select_plans_v203(self, candidates, extended_corpus)

    # Deterministic firewall first.
    hard_failures = []
    for p in plans:
        reason = _fake_plan_hard_reason(p)
        if reason:
            hard_failures.append(f"{p.get('title')}: {reason}")
    if hard_failures:
        for p in plans:
            if _fake_plan_hard_reason(p):
                _REJECTED_PLAN_TITLES.append(str(p.get("title", "")))
        raise RuntimeError("Plausibility Firewall: " + " | ".join(hard_failures[:5]))

    ok, failures = _plan_semantic_audit(self, plans)
    if not ok:
        for failure in failures:
            # Preserve enough text to prevent the same concept being selected on the retry.
            m = re.match(r"#\d+ (.*?): plaus=", failure)
            if m:
                _REJECTED_PLAN_TITLES.append(m.group(1))
        self.logger.log("Plausibility audit FAIL: " + " | ".join(failures[:5]))
        raise RuntimeError("Plausibility audit fallito: " + " | ".join(failures[:4]))

    self.logger.log("Plausibility & Attribution Firewall: PASS")
    try:
        qa = getattr(self, "_v203_plan_qa", {}) or {}
        qa["plausibility_firewall"] = "PASS"
        qa["attribution_firewall"] = "PASS"
        self._v203_plan_qa = qa
    except Exception:
        pass
    return plans


core.BatchEngine.select_plans = _select_plans_v213


def _fake_article_hard_reason(article: dict) -> str | None:
    if str(article.get("status", "")).lower() != "fake":
        return None
    text = " ".join(str(article.get(k, "") or "") for k in ("title", "excerpt", "content"))
    low = core.strip_html(text).lower()
    for pat in FAKE_OFFICIAL_PATTERNS:
        if re.search(pat, low, re.I):
            return f"attribuzione istituzionale/ufficiale nel testo ({pat})"
    for pat in FAKE_QUOTE_PATTERNS:
        if re.search(pat, core.strip_html(text), re.I):
            return f"citazione inventata/non verificabile nel testo ({pat})"
    for pat in FAKE_FINANCE_PATTERNS:
        if re.search(pat, low, re.I):
            return f"claim economico/finanziario nel testo ({pat})"
    return None


def _fake_article_semantic_audit(self, article: dict) -> tuple[bool, str]:
    if article.get("status") != "fake":
        return True, ""
    plain = core.strip_html(str(article.get("content", "")))
    packet = {
        "title": article.get("title", ""),
        "excerpt": article.get("excerpt", ""),
        "content": plain[:9000],
    }
    system = "Sei un revisore editoriale italiano severo. Restituisci SOLO JSON valido."
    user = f'''Valuta questa fake di media literacy:\n{json.dumps(packet, ensure_ascii=False)}\n\nDeve essere plausibile ma innocua, non satirica, italiano corretto. Non deve inventare citazioni, nomi/cognomi, decisioni ufficiali, enti reali, finanziamenti, leggi o atti pubblici.\nJSON ESATTO: {{"plausibility_score":8,"language_score":9,"satire_like":false,"invented_attribution":false,"official_action":false,"safe":true,"pass":true,"reason":"..."}}'''
    try:
        r = self.ollama.chat_json(system, user, temperature=0.1)
    except Exception as exc:
        return False, f"audit articolo non disponibile: {exc}"
    if not isinstance(r, dict):
        return False, "audit articolo non valido"
    plaus = float(r.get("plausibility_score", 0) or 0)
    lang = float(r.get("language_score", 0) or 0)
    bad = any(bool(r.get(k)) for k in ("satire_like", "invented_attribution", "official_action"))
    safe = bool(r.get("safe"))
    passed = bool(r.get("pass")) and plaus >= 7 and lang >= 8 and not bad and safe
    reason = str(r.get("reason", ""))[:300]
    return passed, f"plaus={plaus:g}/10 lingua={lang:g}/10 - {reason}"


_base_generate_article = v212._generate_article_v212


def _generate_article_v213(self, plan, candidates, used_slugs):
    strict_plan = dict(plan)
    if strict_plan.get("status") == "fake":
        strict_plan["angle"] = (
            str(strict_plan.get("angle", ""))
            + " REGOLA VINCOLANTE: la storia deve essere plausibile e sobria, non satirica. "
              "Non usare nomi e cognomi, citazioni, dichiarazioni, persone reali, enti pubblici o universita reali, "
              "decisioni ufficiali, leggi, ordinanze, fondi, finanziamenti o cifre economiche. Usa soggetti generici."
        )

    last = ""
    for attempt in range(1, 3):
        article = _base_generate_article(self, strict_plan, candidates, used_slugs)
        hard = _fake_article_hard_reason(article)
        if hard:
            last = hard
            self.logger.log(f"Plausibility articolo FAIL {attempt}/2: {article.get('title')} | {hard}")
            used_slugs.discard(str(article.get("slug", "")))
            strict_plan["angle"] += " La versione precedente ha violato il firewall: " + hard + ". Evita completamente questo elemento."
            continue
        ok, detail = _fake_article_semantic_audit(self, article)
        if not ok:
            last = detail
            self.logger.log(f"Plausibility articolo FAIL {attempt}/2: {article.get('title')} | {detail}")
            used_slugs.discard(str(article.get("slug", "")))
            strict_plan["angle"] += " La versione precedente non era abbastanza credibile o corretta: " + detail + ". Riscrivila in modo realistico e naturale."
            continue
        if article.get("status") == "fake":
            self.logger.log(f"Plausibility articolo PASS: {article.get('title')} | {detail}")
        return article
    raise RuntimeError(f"Plausibility Firewall articolo fallito: {plan.get('title')} | {last}")


core.BatchEngine.generate_article = _generate_article_v213


_base_generate_batch = core.BatchEngine.generate_batch


def _sanitize_resume_v213(self):
    try:
        state_path = self._load_latest_resume()
    except Exception:
        state_path = None
    if not state_path:
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return

    plans = state.get("plans") or []
    bad_plan = next((p for p in plans if isinstance(p, dict) and _fake_plan_hard_reason(p)), None)
    if bad_plan:
        self.logger.log(
            f"Resume v2.0.13: piano non conforme al Plausibility Firewall ({bad_plan.get('title')}); "
            "conservo i trend e rigenero piano/articoli"
        )
        state.pop("plans", None)
        state.pop("posts", None)
        state.pop("image_stats", None)
        state["stage"] = "trends"
        self._save_state(state_path, state)
        return

    posts = state.get("posts") or []
    for idx, article in enumerate(posts):
        if not isinstance(article, dict):
            continue
        reason = _fake_article_hard_reason(article)
        if reason:
            self.logger.log(
                f"Resume v2.0.13: articolo {idx+1} non conforme ({reason}); "
                f"mantengo i primi {idx} articoli e rigenero dal successivo"
            )
            state["posts"] = posts[:idx]
            state.pop("image_stats", None)
            state["stage"] = f"article-{idx}"
            self._save_state(state_path, state)
            return


def _generate_batch_v213(self, allow_resume=True):
    _sanitize_resume_v213(self)
    return _base_generate_batch(self, allow_resume=allow_resume)


core.BatchEngine.generate_batch = _generate_batch_v213


_previous_build = core.BatchFactoryGUI._build


def _walk(widget):
    yield widget
    try:
        for child in widget.winfo_children():
            yield from _walk(child)
    except Exception:
        return


def _build_v213(self):
    _previous_build(self)
    self.root.title(core.APP_NAME)
    for widget in _walk(self.root):
        try:
            text = str(widget.cget("text"))
        except Exception:
            continue
        if "v2.0.12" in text:
            try:
                widget.configure(text=text.replace("v2.0.12", "v2.0.13"))
            except Exception:
                pass


core.BatchFactoryGUI._build = _build_v213


def self_test() -> None:
    current = core.APP_VERSION
    try:
        core.APP_VERSION = "2.0.12"
        v212.self_test()
    finally:
        core.APP_VERSION = current

    stupid = {"status": "fake", "title": "In Puglia la pizza diventa lingua ufficiale della citta", "angle": "Il sindaco presenta il progetto finanziato con fondi europei"}
    assert _fake_plan_hard_reason(stupid)
    safe = {"status": "fake", "title": "Nei borghi arriva un pass digitale per collezionare timbri gastronomici", "angle": "Una rete privata di botteghe sperimenta una raccolta digitale innocua"}
    assert _fake_plan_hard_reason(safe) is None
    art = {"status": "fake", "title": "Titolo", "excerpt": "Test", "content": '<p>Il sindaco ha dichiarato “partiremo domani”.</p>'}
    assert _fake_article_hard_reason(art)
    assert core.APP_VERSION == "2.0.13"
    print("V2.0.13 PLAUSIBILITY + ATTRIBUTION FIREWALL SELF-TEST PASS")


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
