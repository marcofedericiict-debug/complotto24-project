from __future__ import annotations

import json
import random
import re
import shutil
import sys
from collections import Counter
from pathlib import Path
from urllib.parse import urlparse, urlunparse

import c24_batch_factory_local_v202 as v202

core = v202.core
core.APP_VERSION = "2.0.3"

STOPWORDS = {
    "a","ad","al","alla","alle","allo","ai","agli","all","anche","che","chi","con","da","dal","dalla","dalle","dallo",
    "dei","del","della","delle","dello","di","e","ed","è","gli","ha","il","in","la","le","lo","ma","nei","nel","nella",
    "nelle","nello","non","o","per","piu","più","su","sul","sulla","sulle","sullo","tra","un","una","uno","usa","italia",
    "oggi","domani","ieri","nuovo","nuova","nuovi","nuove","dopo","prima","contro","verso","come","sui","sue","sua","suo",
}


def _canonical_url(url: str) -> str:
    try:
        p = urlparse(url.strip())
        host = p.netloc.lower().replace("www.", "")
        path = re.sub(r"/+", "/", p.path or "/").rstrip("/") or "/"
        return urlunparse((p.scheme.lower() or "https", host, path, "", "", ""))
    except Exception:
        return url.strip().lower()


def _event_tokens(text: str) -> set[str]:
    s = core.normalize_for_similarity(text)
    toks = re.findall(r"[a-zàèéìòù0-9]{3,}", s)
    return {t for t in toks if t not in STOPWORDS and not re.fullmatch(r"20\d{2}", t)}


def _event_duplicate(a: str, b: str) -> bool:
    ta, tb = _event_tokens(a), _event_tokens(b)
    if not ta or not tb:
        return False
    common = ta & tb
    if len(common) < 3:
        return False
    jacc = len(common) / max(1, len(ta | tb))
    contain = len(common) / max(1, min(len(ta), len(tb)))
    seq = core.similarity(a, b)
    return jacc >= 0.42 or contain >= 0.68 or seq >= 0.64


def _valid_distributions():
    out = []
    for real in range(3, 7):
        for fake in range(2, 6):
            for unver in range(1, 4):
                if real + fake + unver == 10:
                    out.append({"real": real, "fake": fake, "unverifiable": unver})
    return out


def _planning_fingerprint(candidates) -> str:
    return v202._planning_fingerprint(candidates)


def _planning_state_path(candidates) -> Path:
    return core.STATE_DIR / f"planning-v203-{_planning_fingerprint(candidates)}.json"


def _load_planning_state(candidates) -> dict:
    path = _planning_state_path(candidates)
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        return obj if isinstance(obj, dict) else {}
    except Exception:
        return {}


def _save_planning_state(candidates, plans, targets):
    _planning_state_path(candidates).write_text(
        json.dumps({
            "version": "2.0.3",
            "fingerprint": _planning_fingerprint(candidates),
            "targets": targets,
            "plans": plans,
        }, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )


def _delete_planning_state(candidates):
    _planning_state_path(candidates).unlink(missing_ok=True)


def _source_urls_for_plan(plan, candidates) -> set[str]:
    by_id = {c.id: c for c in candidates}
    out = set()
    for sid in plan.get("source_ids", []) or []:
        try:
            c = by_id.get(int(sid))
        except Exception:
            c = None
        if c and c.url:
            out.add(_canonical_url(c.url))
    return out


def _event_text_for_plan(plan, candidates) -> str:
    by_id = {c.id: c for c in candidates}
    bits = [str(plan.get("title", "")), str(plan.get("angle", ""))]
    for sid in plan.get("source_ids", []) or []:
        try:
            c = by_id.get(int(sid))
        except Exception:
            c = None
        if c:
            bits.append(c.title)
    return " | ".join(x for x in bits if x)


def _validate_plan_v203(self, p, candidates, corpus, accepted):
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
    if status == "fake":
        src = []

    title = str(p.get("title", "")).strip()
    if len(title) < 20:
        return None, "titolo troppo corto"
    if status == "fake" and any(b in title.lower() for b in core.SAFE_FAKE_BANS):
        return None, "simulazione potenzialmente dannosa"

    # Stronger cross-history event dedupe: title/event similarity, not just exact wording.
    for old in corpus:
        if _event_duplicate(title, old):
            return None, "evento troppo simile allo storico recente"

    scene = str(p.get("image_scene", "")).strip()
    forbidden_scene = ("dashboard", "tabella", "collage", "articolo", "seo", "wordpress", "json", "quiz", "screenshot", "interfaccia")
    if len(scene) < 20 or any(w in scene.lower() for w in forbidden_scene):
        return None, "scena immagine non isolata"

    clean = {
        "title": title,
        "status": status,
        "source_ids": src,
        "category": (str(p.get("category", "Societa")).strip() or "Societa")[:60],
        "category_slug": core.slugify(str(p.get("category_slug") or p.get("category") or "societa"))[:60],
        "image_scene": scene,
        "angle": str(p.get("angle", "")).strip()[:500],
    }

    new_urls = _source_urls_for_plan(clean, candidates)
    new_event = _event_text_for_plan(clean, candidates)
    for other in accepted:
        old_urls = _source_urls_for_plan(other, candidates)
        if new_urls and old_urls and new_urls & old_urls:
            return None, "fonte URL/source_id gia usata nello stesso batch"
        old_event = _event_text_for_plan(other, candidates)
        if _event_duplicate(new_event, old_event):
            return None, "stesso evento gia presente nel batch"

    return clean, "OK"


def _choose_targets(existing_targets=None):
    valid = _valid_distributions()
    if isinstance(existing_targets, dict) and existing_targets in valid:
        return existing_targets
    return random.choice(valid)


def _remaining_statuses(targets, accepted, count):
    current = Counter(p.get("status") for p in accepted)
    pool = []
    for status in ("real", "fake", "unverifiable"):
        pool.extend([status] * max(0, int(targets[status]) - int(current.get(status, 0))))
    # Interleave status types so one request is not five identical tasks unless necessary.
    random.shuffle(pool)
    return pool[:count]


def _select_plans_v203(self, candidates, corpus):
    state = _load_planning_state(candidates)
    targets = _choose_targets(state.get("targets"))
    accepted = []

    for p in state.get("plans", []) if isinstance(state.get("plans"), list) else []:
        clean, _ = _validate_plan_v203(self, p, candidates, corpus, accepted)
        if clean and Counter(x["status"] for x in accepted + [clean])[clean["status"]] <= targets[clean["status"]]:
            accepted.append(clean)
        if len(accepted) >= 10:
            break

    self.logger.log(
        f"Mix editoriale target: real={targets['real']}, fake={targets['fake']}, unverifiable={targets['unverifiable']}"
    )
    if accepted:
        self.logger.log(f"Pianificazione resume: recuperati {len(accepted)}/10 temi validi")

    system = (
        "Sei il caporedattore di un progetto italiano di media literacy. "
        "Devi creare temi distinti, credibili e sicuri. Le notizie reali usano solo fonti del pool; "
        "le simulazioni devono essere innocue e non accusare persone, aziende o enti reali di fatti dannosi. "
        "Restituisci SOLO JSON valido, senza markdown."
    )

    max_rounds = 8
    for round_no in range(1, max_rounds + 1):
        if len(accepted) >= 10:
            break

        need = 10 - len(accepted)
        ask = min(5, need)
        requested = _remaining_statuses(targets, accepted, ask)
        if not requested:
            break

        avoid_hist = corpus if len(corpus) <= 45 else random.sample(corpus, 45)
        avoid = "\n".join(f"- {x[:170]}" for x in avoid_hist) if avoid_hist else "(nessuno)"
        already = "\n".join(f"- {p['title']} [{p['status']}]" for p in accepted) if accepted else "(nessuno)"
        requested_text = ", ".join(requested)

        user = f'''Genera ESATTAMENTE {ask} idee editoriali NUOVE.
Gli status devono seguire ESATTAMENTE questo ordine: {requested_text}.

REGOLE OBBLIGATORIE:
- real: usa 1-3 source_ids realmente presenti nel POOL e riferiti allo stesso fatto centrale.
- fake: source_ids deve essere []; crea una simulazione plausibile ma innocua. Vietati falsa morte, reato, malattia, emergenza, bonus/pagamento, banca, danno reputazionale, accuse o pericoli attribuiti a persone/aziende/enti reali.
- unverifiable: contenuto prudente, senza conseguenze concrete e senza presentare ipotesi come fatti certi.
- OGNI idea deve riguardare un EVENTO/TEMA DIVERSO da tutte le altre.
- Vietato riusare la stessa source_id o lo stesso URL in due idee.
- Non riscrivere lo stesso evento con un titolo diverso.
- Titoli SEO naturali e credibili, non trash.
- image_scene: una sola scena fisica fotografabile, nessun testo/UI/collage/dashboard/tabella/screenshot.

GIA ACCETTATI:\n{already}

STORICO RECENTE DA EVITARE:\n{avoid}

POOL RECENTE:\n{self._candidate_packet(candidates)}

JSON ESATTO:
{{"plans":[{{"title":"...","status":"real|fake|unverifiable","source_ids":[1],"category":"...","category_slug":"...","image_scene":"...","angle":"..."}}]}}
plans deve contenere {ask} elementi e gli status devono essere nell'ordine richiesto.'''

        self.logger.log(
            f"Pianificazione v2.0.3 round {round_no}/{max_rounds}: richiesti {ask} ({requested_text}), validi {len(accepted)}/10"
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
        for idx, p in enumerate(raw_plans[:ask]):
            expected = requested[idx] if idx < len(requested) else None
            if expected and str(p.get("status", "")).strip().lower() != expected:
                rejected += 1
                self.logger.log(f"Piano scartato: status {p.get('status')} ma richiesto {expected}")
                continue
            clean, reason = _validate_plan_v203(self, p, candidates, corpus, accepted)
            if clean:
                counts = Counter(x["status"] for x in accepted)
                if counts[clean["status"]] >= targets[clean["status"]]:
                    rejected += 1
                    self.logger.log(f"Piano scartato: quota {clean['status']} gia raggiunta")
                    continue
                accepted.append(clean)
                added += 1
                _save_planning_state(candidates, accepted, targets)
            else:
                rejected += 1
                self.logger.log(f"Piano scartato: {reason}")

        counts = Counter(x["status"] for x in accepted)
        self.logger.log(
            f"Pianificazione round {round_no}: +{added}, scartati {rejected}; totale {len(accepted)}/10 "
            f"(real={counts['real']}, fake={counts['fake']}, unverifiable={counts['unverifiable']})"
        )

    counts = Counter(p["status"] for p in accepted)
    if len(accepted) != 10 or any(counts[s] != targets[s] for s in targets):
        _save_planning_state(candidates, accepted, targets)
        raise RuntimeError(
            f"Pianificazione incompleta: {len(accepted)}/10. Mix attuale real={counts['real']}, fake={counts['fake']}, "
            f"unverifiable={counts['unverifiable']}; target real={targets['real']}, fake={targets['fake']}, "
            f"unverifiable={targets['unverifiable']}. I piani validi sono salvati per il resume."
        )

    # Final independent event/source firewall.
    all_urls = set()
    for i, p in enumerate(accepted):
        urls = _source_urls_for_plan(p, candidates)
        if urls & all_urls:
            raise RuntimeError("Event dedupe firewall: una fonte URL e stata riutilizzata nel batch")
        all_urls |= urls
        for prev in accepted[:i]:
            if _event_duplicate(_event_text_for_plan(p, candidates), _event_text_for_plan(prev, candidates)):
                raise RuntimeError("Event dedupe firewall: rilevati due articoli sullo stesso evento")

    self._v203_plan_qa = {
        "status_distribution": dict(counts),
        "status_target": targets,
        "event_dedupe": "PASS",
        "source_reuse": "PASS",
        "distinct_events": 10,
    }
    _delete_planning_state(candidates)
    self.logger.log(
        f"Pianificazione completata: 10 eventi distinti; mix real={counts['real']}, fake={counts['fake']}, unverifiable={counts['unverifiable']}"
    )
    return accepted


def _state_plans_valid_v203(plans) -> bool:
    if not isinstance(plans, list) or len(plans) != 10:
        return False
    counts = Counter(str(p.get("status", "")) for p in plans if isinstance(p, dict))
    if not (3 <= counts["real"] <= 6 and 2 <= counts["fake"] <= 5 and 1 <= counts["unverifiable"] <= 3):
        return False
    # Source IDs cannot be reused among real plans; catches the exact duplication seen in prior batches.
    used = set()
    for p in plans:
        if not isinstance(p, dict):
            return False
        for sid in p.get("source_ids", []) or []:
            try:
                n = int(sid)
            except Exception:
                continue
            if n in used:
                return False
            used.add(n)
    titles = [str(p.get("title", "")) for p in plans]
    for i, title in enumerate(titles):
        if any(_event_duplicate(title, old) for old in titles[:i]):
            return False
    return True


def _sanitize_legacy_resume(self):
    state_path = self._load_latest_resume()
    if not state_path:
        return
    try:
        state = json.loads(state_path.read_text(encoding="utf-8"))
    except Exception:
        return
    plans = state.get("plans")
    if plans and not _state_plans_valid_v203(plans):
        self.logger.log("Resume legacy: piani precedenti non conformi al nuovo mix/dedupe; conservo i trend e rigenero il piano editoriale")
        state.pop("plans", None)
        state.pop("posts", None)
        state.pop("image_stats", None)
        state["stage"] = "trends"
        state["version"] = "2.0.3"
        state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
        work = Path(state.get("work_dir", ""))
        if work.exists():
            shutil.rmtree(work / "images", ignore_errors=True)
            (work / "images").mkdir(exist_ok=True)


_original_generate_batch = core.BatchEngine.generate_batch


def _generate_batch_v203(self, allow_resume=True):
    if allow_resume:
        _sanitize_legacy_resume(self)
    result = _original_generate_batch(self, allow_resume=allow_resume)
    zip_path, qa = result

    # Final QA also verifies the actual exported posts, not only the plans.
    try:
        import zipfile
        with zipfile.ZipFile(zip_path, "r") as z:
            batch = json.loads(z.read("c24-batch.json").decode("utf-8"))
        posts = batch.get("posts", [])
        counts = Counter(str(p.get("status", "")) for p in posts)
        if len(posts) != 10:
            raise RuntimeError("QA editoriale finale: articoli != 10")
        if not (3 <= counts["real"] <= 6 and 2 <= counts["fake"] <= 5 and 1 <= counts["unverifiable"] <= 3):
            raise RuntimeError(
                f"QA status distribution FAIL: real={counts['real']}, fake={counts['fake']}, unverifiable={counts['unverifiable']}"
            )
        titles = [str(p.get("title", "")) for p in posts]
        for i, t in enumerate(titles):
            if any(_event_duplicate(t, old) for old in titles[:i]):
                raise RuntimeError(f"QA event dedupe FAIL: possibile duplicato '{t}'")

        qa["status_distribution"] = dict(counts)
        qa["editorial_mix"] = "PASS"
        qa["event_dedupe"] = "PASS"
        qa["source_reuse"] = "PASS"
        qa["distinct_events"] = 10
        qa_path = Path(self.cfg.output_dir) / f"{qa['batch_id']}-QA.json"
        qa_path.write_text(json.dumps(qa, ensure_ascii=False, indent=2), encoding="utf-8")
        self.logger.log(
            f"QA editoriale v2.0.3 PASS: 10 eventi distinti; real={counts['real']}, fake={counts['fake']}, unverifiable={counts['unverifiable']}"
        )
    except Exception:
        # Never leave a ZIP that failed the new editorial QA looking publishable.
        try:
            Path(zip_path).unlink(missing_ok=True)
        except Exception:
            pass
        raise
    return zip_path, qa


core.BatchEngine.select_plans = _select_plans_v203
core.BatchEngine.generate_batch = _generate_batch_v203


def _self_test_v203():
    core.self_test()
    assert len(_valid_distributions()) > 0
    for d in _valid_distributions():
        assert sum(d.values()) == 10
        assert 3 <= d["real"] <= 6 and 2 <= d["fake"] <= 5 and 1 <= d["unverifiable"] <= 3
    assert _event_duplicate(
        "Perugia: crollo del tetto in una palazzina dopo un'esplosione",
        "Perugia, esplosione in palazzina: crolla il tetto"
    )
    assert _event_duplicate(
        "Nintendo chiude 400 repository GitHub legati agli emulatori Switch",
        "Nintendo contro gli emulatori Switch: rimossi 400 repo da GitHub"
    )
    assert not _event_duplicate(
        "Microsoft aggiorna Intune con nuove policy Windows",
        "Juventus valuta un nuovo esterno per il mercato estivo"
    )
    print("V2.0.3 EDITORIAL SELF-TEST PASS")


def main() -> None:
    if "--self-test" in sys.argv:
        _self_test_v203()
        return
    core.main()


if __name__ == "__main__":
    main()
