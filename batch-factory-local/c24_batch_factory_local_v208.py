from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import c24_batch_factory_local_v207 as v207

core = v207.core
core.APP_VERSION = "2.0.8"
core.APP_NAME = "Complotto24 Batch Factory LOCAL v2.0.8"

_v203 = v207.v206.v205.v204.v203
_CURRENT_MODEL = ""


def _normalized_model(name: str) -> str:
    return (name or "").strip().lower()


def _purge_model_outputs(state: dict) -> bool:
    """Keep harvested candidates, purge only LLM/image dependent resume data."""
    changed = False
    for key in ("plans", "posts", "image_stats"):
        if key in state:
            state.pop(key, None)
            changed = True
    if state.get("stage") not in ("start", "trends"):
        state["stage"] = "trends" if state.get("candidates") else "start"
        changed = True
    work_dir = Path(str(state.get("work_dir", "")))
    if work_dir.exists():
        for dirname in ("images",):
            d = work_dir / dirname
            if d.exists():
                shutil.rmtree(d, ignore_errors=True)
                d.mkdir(parents=True, exist_ok=True)
                changed = True
        for name in ("c24-batch.json",):
            try:
                (work_dir / name).unlink(missing_ok=True)
            except Exception:
                pass
    return changed


def _prepare_latest_resume_for_model(self) -> None:
    path = self._load_latest_resume()
    if not path:
        return
    try:
        state = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(state, dict):
            return
        current = self.cfg.ollama_model.strip()
        stored = str(state.get("ollama_model", "")).strip()

        # Old states did not record model identity. Preserve the expensive trend pool
        # but never assume their generated plans/posts belong to the selected LLM.
        old_has_model_outputs = bool(state.get("plans") or state.get("posts") or state.get("image_stats"))
        mismatch = bool(stored and _normalized_model(stored) != _normalized_model(current))
        legacy_unknown = not stored and old_has_model_outputs

        if mismatch or legacy_unknown:
            _purge_model_outputs(state)
            why = f"modello cambiato: {stored} -> {current}" if mismatch else f"resume legacy senza identita LLM -> {current}"
            self.logger.log(f"Isolamento resume LLM: {why}; conservo trend/fonti e rigenero piano/articoli/immagini")

        state["ollama_model"] = current
        state["resume_schema"] = 2
        path.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    except Exception as exc:
        self.logger.log(f"Controllo isolamento resume LLM non riuscito: {exc}")


_original_save_state = core.BatchEngine._save_state


def _save_state_v208(self, state_path, payload):
    if isinstance(payload, dict):
        payload["ollama_model"] = self.cfg.ollama_model.strip()
        payload["resume_schema"] = 2
    return _original_save_state(self, state_path, payload)


core.BatchEngine._save_state = _save_state_v208


_original_generate_batch = core.BatchEngine.generate_batch


def _generate_batch_v208(self, allow_resume=True):
    global _CURRENT_MODEL
    _CURRENT_MODEL = self.cfg.ollama_model.strip()
    if allow_resume:
        _prepare_latest_resume_for_model(self)
    return _original_generate_batch(self, allow_resume=allow_resume)


core.BatchEngine.generate_batch = _generate_batch_v208


# Planning v2.0.3 has its own small partial-plan state. Make that model-aware too.
_original_planning_save = _v203._save_planning_state
_original_planning_load = _v203._load_planning_state


def _planning_save_v208(candidates, plans, targets):
    path = _v203._planning_state_path(candidates)
    path.write_text(
        json.dumps(
            {
                "version": "2.0.8",
                "fingerprint": _v203._planning_fingerprint(candidates),
                "ollama_model": _CURRENT_MODEL,
                "targets": targets,
                "plans": plans,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )


def _planning_load_v208(candidates):
    path = _v203._planning_state_path(candidates)
    if not path.exists():
        return {}
    try:
        obj = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(obj, dict):
            return {}
        stored = str(obj.get("ollama_model", "")).strip()
        if not stored or _normalized_model(stored) != _normalized_model(_CURRENT_MODEL):
            # Never mix partial planner output produced by another/unknown LLM.
            path.unlink(missing_ok=True)
            return {}
        return obj
    except Exception:
        return {}


_v203._save_planning_state = _planning_save_v208
_v203._load_planning_state = _planning_load_v208


# Version polish without rebuilding the whole v2.0.7 UI.
_previous_build = core.BatchFactoryGUI._build


def _build_v208(self):
    global _CURRENT_MODEL
    _previous_build(self)
    self.root.title(core.APP_NAME)
    _CURRENT_MODEL = self.cfg.ollama_model.strip()
    for widget in v207.v206.v205._walk_widgets(self.root):
        try:
            text = str(widget.cget("text"))
            if text == "v2.0.7  •  LOCAL":
                widget.configure(text="v2.0.8  •  LOCAL")
        except Exception:
            continue
    try:
        if getattr(self, "_banner_canvas", None) is not None:
            self._banner_cache_key = None
            self.root.after(50, lambda: v207._render_banner(self, self._banner_canvas, self._banner_canvas.winfo_width()))
    except Exception:
        pass


core.BatchFactoryGUI._build = _build_v208


# Keep model identity synchronized when the user changes dropdown selection.
_previous_model_selected = core.BatchFactoryGUI._on_model_selected


def _on_model_selected_v208(self, event=None):
    global _CURRENT_MODEL
    result = _previous_model_selected(self, event)
    try:
        _CURRENT_MODEL = self.cfg.ollama_model.strip()
    except Exception:
        pass
    return result


core.BatchFactoryGUI._on_model_selected = _on_model_selected_v208


def self_test() -> None:
    current = core.APP_VERSION
    try:
        core.APP_VERSION = "2.0.5"
        v207.v206.v205._self_test_v205()
    finally:
        core.APP_VERSION = current

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        wd = Path(td) / "work"
        (wd / "images").mkdir(parents=True)
        (wd / "images" / "x.jpg").write_bytes(b"x")
        state = {
            "work_dir": str(wd),
            "stage": "article-2",
            "candidates": [{"id": 1}],
            "plans": [{"title": "x"}],
            "posts": [{"title": "y"}],
            "image_stats": [{"file": "x.jpg"}],
        }
        assert _purge_model_outputs(state) is True
        assert state.get("candidates") and not state.get("plans") and not state.get("posts")
        assert state["stage"] == "trends"
        assert list((wd / "images").iterdir()) == []

    assert core.APP_VERSION == "2.0.8"
    print("V2.0.8 MODEL-AWARE RESUME ISOLATION SELF-TEST PASS")


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
