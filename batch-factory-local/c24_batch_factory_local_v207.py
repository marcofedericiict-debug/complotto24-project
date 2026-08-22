from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path

from PIL import Image, ImageOps, ImageTk

import c24_batch_factory_local_v206 as v206

core = v206.core
core.APP_VERSION = "2.0.7"
core.APP_NAME = "Complotto24 Batch Factory LOCAL v2.0.7"

BRANDING_DIR = core.APP_DIR / "branding"
CUSTOM_BANNER = BRANDING_DIR / "banner.png"


def _banner_candidates() -> list[Path]:
    out = [CUSTOM_BANNER]
    try:
        exe_dir = Path(sys.executable).resolve().parent
        out += [
            exe_dir / "complotto24-banner.png",
            exe_dir / "complotto24-banner.jpg",
            exe_dir / "banner.png",
            exe_dir / "banner.jpg",
        ]
    except Exception:
        pass
    return out


def _find_banner() -> Path | None:
    for p in _banner_candidates():
        try:
            if p.exists() and p.is_file() and p.stat().st_size > 1000:
                return p
        except Exception:
            continue
    return None


def _save_custom_banner(src: str | Path) -> Path:
    BRANDING_DIR.mkdir(parents=True, exist_ok=True)
    source = Path(src)
    with Image.open(source) as im:
        im = im.convert("RGB")
        # Keep all pixels/aspect; only limit excessive dimensions for a lightweight GUI asset.
        if max(im.size) > 2200:
            im.thumbnail((2200, 2200), Image.Resampling.LANCZOS)
        im.save(CUSTOM_BANNER, "PNG", optimize=True)
    return CUSTOM_BANNER


def _render_banner(self, canvas, width: int) -> None:
    """Use the user's banner when present; otherwise keep the generated C24 fallback."""
    v204 = v206.v205.v204
    width = max(640, int(width or 900))
    height = 180
    canvas.configure(height=height)
    canvas.delete("all")
    canvas.create_rectangle(0, 0, width, height, fill="#05070a", outline="")

    src = _find_banner()
    if src:
        try:
            cache_key = (str(src), src.stat().st_mtime_ns, width, height)
            if getattr(self, "_banner_cache_key", None) != cache_key:
                with Image.open(src) as im:
                    im = im.convert("RGB")
                    # The supplied artwork is poster-like; crop centrally to a header strip
                    # without stretching. The logo/tagline remain sharp and proportional.
                    target_ratio = width / height
                    src_ratio = im.width / im.height
                    if src_ratio < target_ratio:
                        crop_h = max(1, int(im.width / target_ratio))
                        top = max(0, (im.height - crop_h) // 2)
                        im = im.crop((0, top, im.width, top + crop_h))
                    elif src_ratio > target_ratio:
                        crop_w = max(1, int(im.height * target_ratio))
                        left = max(0, (im.width - crop_w) // 2)
                        im = im.crop((left, 0, left + crop_w, im.height))
                    im = im.resize((width, height), Image.Resampling.LANCZOS)
                    self._banner_photo = ImageTk.PhotoImage(im)
                    self._banner_cache_key = cache_key
            canvas.create_image(0, 0, anchor="nw", image=self._banner_photo)
            # Subtle lower gradient replacement: a translucent-like dark strip rendered solid
            # for Tk compatibility, improving readability against very bright artwork.
            canvas.create_rectangle(0, height - 27, width, height, fill="#05070a", outline="")
            canvas.create_text(
                16, height - 13, anchor="w",
                text=f"BATCH FACTORY LOCAL • v{core.APP_VERSION}",
                fill="#b7beca", font=("Segoe UI Semibold", 9),
            )
            canvas.create_text(
                width - 16, height - 13, anchor="e",
                text="ADAPTIVE OLLAMA • COMFYUI",
                fill="#d9464f", font=("Segoe UI Semibold", 9),
            )
            return
        except Exception as exc:
            try:
                self.logger.log(f"Banner personalizzato non caricabile: {exc}; uso fallback C24")
            except Exception:
                pass

    # Fallback branding: no external asset needed.
    canvas.create_rectangle(0, 0, 8, height, fill=v204.ACCENT, outline="")
    canvas.create_oval(28, 43, 100, 115, fill=v204.ACCENT, outline="")
    canvas.create_text(64, 79, text="C24", fill="white", font=("Segoe UI Black", 21, "bold"))
    canvas.create_text(122, 58, anchor="w", text="COMPLOTTO24", fill=v204.TEXT, font=("Segoe UI Black", 25, "bold"))
    canvas.create_text(123, 91, anchor="w", text="BATCH FACTORY • ADAPTIVE LOCAL AI", fill=v204.MUTED, font=("Segoe UI Semibold", 10))
    canvas.create_text(123, 119, anchor="w", text="Ollama adattivo → QA editoriale → ComfyUI → ZIP WordPress", fill="#7f91aa", font=("Segoe UI", 9))
    canvas.create_text(width - 18, 153, anchor="e", text="v2.0.7", fill="#ff9da4", font=("Segoe UI Semibold", 9))


def _choose_banner(self) -> None:
    try:
        path = core.filedialog.askopenfilename(
            title="Seleziona banner Complotto24",
            filetypes=[
                ("Immagini", "*.png *.jpg *.jpeg *.webp"),
                ("PNG", "*.png"),
                ("JPEG", "*.jpg *.jpeg"),
                ("Tutti i file", "*.*"),
            ],
        )
        if not path:
            return
        saved = _save_custom_banner(path)
        self._banner_cache_key = None
        self._banner_path_var.set(str(saved))
        if getattr(self, "_banner_canvas", None) is not None:
            _render_banner(self, self._banner_canvas, self._banner_canvas.winfo_width())
        self.logger.log(f"Banner GUI impostato: {saved}")
        core.messagebox.showinfo(core.APP_NAME, "Banner Complotto24 impostato.\n\nVerrà riutilizzato automaticamente ai prossimi avvii.")
    except Exception as exc:
        core.messagebox.showwarning(core.APP_NAME, f"Impossibile impostare il banner:\n\n{exc}")


def _reset_banner(self) -> None:
    try:
        CUSTOM_BANNER.unlink(missing_ok=True)
        self._banner_cache_key = None
        self._banner_path_var.set("Fallback grafico incorporato")
        if getattr(self, "_banner_canvas", None) is not None:
            _render_banner(self, self._banner_canvas, self._banner_canvas.winfo_width())
        self.logger.log("Banner GUI personalizzato rimosso")
    except Exception as exc:
        core.messagebox.showwarning(core.APP_NAME, f"Impossibile ripristinare il banner:\n\n{exc}")


_previous_build = core.BatchFactoryGUI._build


def _build_v207(self):
    _previous_build(self)
    self.root.title(core.APP_NAME)
    tk = core.tk
    ttk = core.ttk

    # Keep model name available before the very first planner progress message.
    try:
        v206._ACTIVE_MODEL = self.cfg.ollama_model.strip()
    except Exception:
        pass

    # Locate and rebind the original banner Canvas. Older wrappers used a lexical
    # callback, so rebinding here guarantees v2.0.7/custom artwork actually renders.
    self._banner_canvas = None
    for widget in v206.v205._walk_widgets(self.root):
        try:
            if isinstance(widget, tk.Canvas) and self._banner_canvas is None:
                self._banner_canvas = widget
            text = str(widget.cget("text"))
            if text in ("v2.0.4  •  LOCAL", "v2.0.5  •  LOCAL", "v2.0.6  •  LOCAL"):
                widget.configure(text="v2.0.7  •  LOCAL")
        except Exception:
            continue

    if self._banner_canvas is not None:
        self._banner_canvas.configure(height=180)
        self._banner_canvas.unbind("<Configure>")
        self._banner_canvas.bind("<Configure>", lambda e: _render_banner(self, self._banner_canvas, e.width))
        self.root.after(40, lambda: _render_banner(self, self._banner_canvas, self._banner_canvas.winfo_width()))

    # Branding controls live in the existing configuration panel.
    try:
        config = self.model_combo.master
        current = _find_banner()
        self._banner_path_var = tk.StringVar(value=str(current) if current else "Fallback grafico incorporato")
        ttk.Label(config, text="Banner GUI", style="Panel.TLabel").grid(row=9, column=0, sticky="w", padx=(0, 10), pady=(7, 4))
        ttk.Label(config, textvariable=self._banner_path_var, style="Muted.TLabel").grid(row=9, column=1, columnspan=2, sticky="ew", pady=(7, 4))
        bframe = ttk.Frame(config, style="Panel.TFrame")
        bframe.grid(row=9, column=3, sticky="e", padx=(7, 0), pady=(7, 4))
        ttk.Button(bframe, text="Imposta…", style="Secondary.TButton", command=lambda: _choose_banner(self)).pack(side="left")
        ttk.Button(bframe, text="Reset", style="Secondary.TButton", command=lambda: _reset_banner(self)).pack(side="left", padx=(5, 0))
    except Exception as exc:
        try:
            self.logger.log(f"Controlli branding non disponibili: {exc}")
        except Exception:
            pass

    try:
        self.model_hint.set("Modello Ollama selezionabile • think/JSON adattati automaticamente per famiglia e capability")
    except Exception:
        pass


core.BatchFactoryGUI._build = _build_v207


# Make the first planning progress message model-aware even before chat() is entered.
_original_set_progress = core.BatchEngine._set_progress


def _set_progress_v207(self, pct, msg):
    model = (self.cfg.ollama_model or "LLM Ollama").strip()
    msg = str(msg).replace("con Qwen locale", f"con {model}")
    return _original_set_progress(self, pct, msg)


core.BatchEngine._set_progress = _set_progress_v207


# Keep global model-aware logger state synchronized whenever the engine is created.
_original_engine_init = core.BatchEngine.__init__


def _engine_init_v207(self, cfg, logger, progress=None):
    _original_engine_init(self, cfg, logger, progress)
    try:
        v206._ACTIVE_MODEL = cfg.ollama_model.strip()
    except Exception:
        pass


core.BatchEngine.__init__ = _engine_init_v207


def self_test() -> None:
    # Run prior offline tests with the historical version value they expect.
    current = core.APP_VERSION
    try:
        core.APP_VERSION = "2.0.5"
        v206.v205._self_test_v205()
    finally:
        core.APP_VERSION = current

    class Dummy:
        def model_info(self, model):
            if "gpt-oss" in model:
                return {"capabilities": ["completion", "thinking"], "details": {"family": "gptoss"}}
            if "qwen3" in model:
                return {"capabilities": ["completion", "thinking"], "details": {"family": "qwen3"}}
            return {"capabilities": ["completion"], "details": {"family": "llama"}}

    d = Dummy()
    assert v206._compat_profile(d, "gpt-oss:20b")["think"] == "low"
    assert v206._compat_profile(d, "qwen3:8b")["think"] is False
    assert v206._compat_profile(d, "llama3.2:3b")["think"] is None

    import tempfile
    with tempfile.TemporaryDirectory() as td:
        src = Path(td) / "banner.jpg"
        Image.new("RGB", (1500, 1000), (10, 10, 10)).save(src, "JPEG")
        old = globals()["CUSTOM_BANNER"]
        try:
            globals()["CUSTOM_BANNER"] = Path(td) / "saved.png"
            _save_custom_banner(src)
            assert globals()["CUSTOM_BANNER"].exists()
            with Image.open(globals()["CUSTOM_BANNER"]) as im:
                assert im.width == 1500 and im.height == 1000
        finally:
            globals()["CUSTOM_BANNER"] = old

    assert core.APP_VERSION == "2.0.7"
    print("V2.0.7 ADAPTIVE LLM + CUSTOM BANNER SELF-TEST PASS")


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
