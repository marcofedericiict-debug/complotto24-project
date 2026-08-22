from __future__ import annotations

import os
import threading

import c24_batch_factory_local_v204 as v204

core = v204.core
core.APP_VERSION = "2.0.5"
core.APP_NAME = "Complotto24 Batch Factory LOCAL v2.0.5"


def _walk_widgets(widget):
    yield widget
    try:
        for child in widget.winfo_children():
            yield from _walk_widgets(child)
    except Exception:
        return


_previous_build = core.BatchFactoryGUI._build


def _build_v205(self):
    _previous_build(self)
    self.root.title(core.APP_NAME)
    self._model_refresh_running = False
    self.model_refresh_btn = None

    # v2.0.4's branded builder hard-coded its version in two widgets. Upgrade them
    # without duplicating the whole UI implementation.
    for widget in _walk_widgets(self.root):
        try:
            text = str(widget.cget("text"))
        except Exception:
            continue
        if text == "v2.0.4  •  LOCAL":
            try:
                widget.configure(text="v2.0.5  •  LOCAL")
            except Exception:
                pass
        elif text == "Aggiorna modelli":
            self.model_refresh_btn = widget


def _paint_banner_v205(self, canvas, width):
    # Same C24 visual language as v2.0.4, but with the correct build number.
    canvas.delete("all")
    h = 112
    canvas.create_rectangle(0, 0, width, h, fill=v204.PANEL, outline="")
    canvas.create_rectangle(0, 0, 8, h, fill=v204.ACCENT, outline="")
    canvas.create_oval(27, 23, 91, 87, fill=v204.ACCENT, outline="")
    canvas.create_text(59, 55, text="C24", fill="white", font=("Segoe UI Black", 19, "bold"))
    canvas.create_text(111, 34, anchor="w", text="COMPLOTTO24", fill=v204.TEXT, font=("Segoe UI Black", 23, "bold"))
    canvas.create_text(112, 65, anchor="w", text="BATCH FACTORY  •  LOCAL AI EDITION", fill=v204.MUTED, font=("Segoe UI Semibold", 10))
    canvas.create_text(112, 88, anchor="w", text="Trend → Qwen / GPT-OSS / altri Ollama → QA editoriale → ComfyUI → ZIP WordPress", fill="#7f91aa", font=("Segoe UI", 9))
    if width > 720:
        canvas.create_rectangle(width-184, 28, width-112, 54, fill="#193d35", outline="")
        canvas.create_text(width-148, 41, text="LOCAL", fill=v204.SUCCESS, font=("Segoe UI Semibold", 9))
        canvas.create_rectangle(width-102, 28, width-25, 54, fill="#40252c", outline="")
        canvas.create_text(width-63, 41, text="v2.0.5", fill="#ff9da4", font=("Segoe UI Semibold", 9))


def _refresh_ollama_models_v205(self, silent=False):
    """Refresh model inventory with explicit UI feedback and without freezing Tk."""
    if getattr(self, "_model_refresh_running", False):
        if not silent:
            self.status.set("Aggiornamento modelli Ollama già in corso…")
        return

    # Read current controls on Tk's main thread before starting network I/O.
    self._sync_cfg()
    self._model_refresh_running = True

    if not silent:
        self.status.set("Aggiornamento modelli Ollama…")
        self.model_hint.set("Interrogo Ollama per leggere i modelli installati…")
        self.logger.log("Aggiornamento manuale modelli Ollama richiesto")
        try:
            if self.model_refresh_btn is not None:
                self.model_refresh_btn.configure(state="disabled", text="Aggiornamento…")
        except Exception:
            pass
        try:
            self.root.update_idletasks()
        except Exception:
            pass

    ollama_url = self.cfg.ollama_url
    current_requested = self.vars["ollama_model"].get().strip()

    def worker():
        try:
            # Use the existing hardened client, but do not touch Tk widgets from here.
            client = core.OllamaClient(self.cfg, core.HTTPClient(self.logger), self.logger)
            if not client.is_up():
                if not client.try_start():
                    raise RuntimeError(f"Ollama non raggiungibile su {ollama_url}")
            names = client.model_names()
            if not names:
                raise RuntimeError("Nessun modello Ollama installato")

            current = current_requested
            if current not in names:
                preferred = next(
                    (n for n in names if any(k in n.lower() for k in ("qwen", "gpt-oss", "llama", "gemma", "mistral"))),
                    names[0],
                )
                current = preferred

            info = client.model_info(current)
            details = info.get("details") or {}
            family = details.get("family") or details.get("families") or ""
            params = details.get("parameter_size") or ""
            quant = details.get("quantization_level") or ""
            meta = " • ".join(str(x) for x in (family, params, quant) if x)

            self.root.after(0, lambda: apply_success(names, current, meta))
        except Exception as exc:
            self.root.after(0, lambda exc=exc: apply_error(exc))

    def restore_button():
        self._model_refresh_running = False
        try:
            if self.model_refresh_btn is not None:
                self.model_refresh_btn.configure(state="normal", text="Aggiorna modelli")
        except Exception:
            pass

    def apply_success(names, current, meta):
        try:
            self.model_combo["values"] = names
            self.vars["ollama_model"].set(current)
            self.cfg.ollama_model = current
            self.cfg.save()
            self.model_hint.set(
                f"{len(names)} modelli Ollama disponibili"
                + (f"  •  {meta}" if meta else "")
            )
            self.status.set(f"Modelli Ollama aggiornati: {len(names)} disponibili • selezionato: {current}")
            self.logger.log(
                f"Modelli Ollama aggiornati: {len(names)} disponibili; selezionato '{current}'. "
                + " | ".join(names[:20])
            )
            if not silent:
                listing = "\n".join(f"• {name}" for name in names[:20])
                more = f"\n… e altri {len(names)-20}" if len(names) > 20 else ""
                core.messagebox.showinfo(
                    core.APP_NAME,
                    f"Aggiornamento modelli completato.\n\n"
                    f"Trovati: {len(names)}\n"
                    f"Selezionato: {current}\n\n"
                    f"{listing}{more}",
                )
        finally:
            restore_button()

    def apply_error(exc):
        try:
            self.model_hint.set(f"Impossibile leggere i modelli: {exc}")
            self.status.set("Aggiornamento modelli Ollama fallito")
            self.logger.log(f"Aggiornamento modelli Ollama FALLITO: {exc}")
            if not silent:
                core.messagebox.showwarning(
                    core.APP_NAME,
                    f"Impossibile aggiornare i modelli Ollama:\n\n{exc}\n\n"
                    f"URL configurato: {ollama_url}",
                )
        finally:
            restore_button()

    threading.Thread(target=worker, daemon=True, name="c24-model-refresh").start()


core.BatchFactoryGUI._build = _build_v205
core.BatchFactoryGUI._paint_banner = _paint_banner_v205
core.BatchFactoryGUI._refresh_ollama_models = _refresh_ollama_models_v205


def _self_test_v205():
    v204._self_test_v204()
    assert core.APP_VERSION == "2.0.5"
    assert callable(core.BatchFactoryGUI._refresh_ollama_models)
    print("V2.0.5 MODEL REFRESH UX SELF-TEST PASS")


def main():
    if "--self-test" in os.sys.argv:
        _self_test_v205()
        return
    core.ensure_dirs()
    if core.tk is None:
        raise SystemExit("Tkinter non disponibile")
    core.BatchFactoryGUI().run()


if __name__ == "__main__":
    main()
