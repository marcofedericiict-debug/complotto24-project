from __future__ import annotations

import os
import queue
import threading
import webbrowser
from pathlib import Path

from PIL import Image, ImageDraw, ImageTk

import c24_batch_factory_local_v203 as v203

core = v203.core
core.APP_VERSION = "2.0.4"
core.APP_NAME = "Complotto24 Batch Factory LOCAL v2.0.4"


# ---------- Ollama model discovery / compatibility ----------

def _model_names(self) -> list[str]:
    tags = self.tags()
    names = []
    for m in tags:
        n = str(m.get("name", "")).strip()
        if n and n not in names:
            names.append(n)
    return sorted(names, key=str.lower)


def _model_info(self, name: str) -> dict:
    try:
        return self.http.request(
            "POST", self.base + "/api/show", json={"model": name}, timeout=15, retries=1
        ).json()
    except Exception:
        return {}


def _resolve_model_v204(self) -> str:
    names = self.model_names()
    wanted = self.cfg.ollama_model.strip()
    if not wanted:
        if not names:
            raise RuntimeError("Ollama e attivo ma non risultano modelli installati")
        wanted = names[0]
        self.cfg.ollama_model = wanted
    if wanted not in names:
        # Friendly near-match when tags changed, e.g. model -> model:latest.
        base = wanted.split(":")[0].lower()
        matches = [n for n in names if n.split(":")[0].lower() == base]
        if len(matches) == 1:
            wanted = matches[0]
            self.cfg.ollama_model = wanted
            self.logger.log(f"Modello richiesto trovato come {wanted}")
        else:
            raise RuntimeError(
                f"Modello Ollama '{self.cfg.ollama_model}' non trovato. Disponibili: {', '.join(names[:20])}"
            )

    info = self.model_info(wanted)
    caps = [str(x).lower() for x in (info.get("capabilities") or [])]
    if caps and "embedding" in caps and not any(x in caps for x in ("completion", "chat", "thinking", "tools")):
        raise RuntimeError(f"Il modello '{wanted}' sembra essere embedding-only e non puo generare articoli")
    return wanted


core.OllamaClient.model_names = _model_names
core.OllamaClient.model_info = _model_info
core.OllamaClient.resolve_model = _resolve_model_v204


# ---------- C24 branded Tkinter UI ----------

BG = "#08111f"
PANEL = "#101b2d"
PANEL_2 = "#15233a"
TEXT = "#f7f8fa"
MUTED = "#a9b4c6"
ACCENT = "#e53b45"
ACCENT_HOVER = "#c92f39"
SUCCESS = "#31c48d"
BORDER = "#263751"
ENTRY = "#0c1727"


def _make_app_icon(size=64):
    im = Image.new("RGBA", (size, size), (8, 17, 31, 255))
    d = ImageDraw.Draw(im)
    pad = max(3, size // 12)
    d.rounded_rectangle((pad, pad, size-pad, size-pad), radius=size//5, fill=(229, 59, 69, 255))
    # Stylized C24 mark, intentionally simple and readable at small sizes.
    d.arc((size*.20, size*.20, size*.62, size*.70), 55, 305, fill="white", width=max(2, size//11))
    d.line((size*.52, size*.37, size*.78, size*.37), fill="white", width=max(2, size//13))
    d.line((size*.78, size*.37, size*.56, size*.67), fill="white", width=max(2, size//13))
    d.line((size*.55, size*.67, size*.80, size*.67), fill="white", width=max(2, size//13))
    return im


def _style_button(style, name, bg, fg=TEXT):
    style.configure(name, background=bg, foreground=fg, padding=(14, 9), font=("Segoe UI Semibold", 10), borderwidth=0)
    style.map(name, background=[("active", ACCENT_HOVER if bg == ACCENT else PANEL_2), ("disabled", PANEL_2)], foreground=[("disabled", MUTED)])


def _build_branded(self):
    tk = core.tk
    ttk = core.ttk

    self.root.configure(bg=BG)
    self.root.geometry("980x790")
    self.root.minsize(860, 690)

    style = ttk.Style(self.root)
    try:
        style.theme_use("clam")
    except Exception:
        pass
    style.configure("C24.TFrame", background=BG)
    style.configure("Panel.TFrame", background=PANEL)
    style.configure("Panel2.TFrame", background=PANEL_2)
    style.configure("C24.TLabel", background=BG, foreground=TEXT, font=("Segoe UI", 10))
    style.configure("Panel.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI", 10))
    style.configure("Muted.TLabel", background=PANEL, foreground=MUTED, font=("Segoe UI", 9))
    style.configure("Section.TLabel", background=PANEL, foreground=TEXT, font=("Segoe UI Semibold", 11))
    style.configure("Status.TLabel", background=BG, foreground=TEXT, font=("Segoe UI Semibold", 10))
    style.configure("C24.TEntry", fieldbackground=ENTRY, foreground=TEXT, insertcolor=TEXT, bordercolor=BORDER, lightcolor=BORDER, darkcolor=BORDER, padding=7)
    style.configure("C24.TCombobox", fieldbackground=ENTRY, background=ENTRY, foreground=TEXT, arrowcolor=TEXT, bordercolor=BORDER, padding=6)
    style.map("C24.TCombobox", fieldbackground=[("readonly", ENTRY)], foreground=[("readonly", TEXT)], selectbackground=[("readonly", ENTRY)], selectforeground=[("readonly", TEXT)])
    style.configure("C24.Horizontal.TProgressbar", troughcolor=PANEL, background=ACCENT, bordercolor=PANEL, lightcolor=ACCENT, darkcolor=ACCENT, thickness=9)
    style.configure("C24.TCheckbutton", background=PANEL, foreground=TEXT, font=("Segoe UI", 9))
    style.map("C24.TCheckbutton", background=[("active", PANEL)], foreground=[("active", TEXT)])
    _style_button(style, "Primary.TButton", ACCENT)
    _style_button(style, "Secondary.TButton", PANEL_2)
    _style_button(style, "Danger.TButton", "#6d2730")

    # Window/taskbar icon generated locally; no internet dependency.
    try:
        self._c24_icon_pil = _make_app_icon(64)
        self._c24_icon = ImageTk.PhotoImage(self._c24_icon_pil)
        self.root.iconphoto(True, self._c24_icon)
    except Exception:
        self._c24_icon = None

    main = ttk.Frame(self.root, style="C24.TFrame", padding=(16, 14))
    main.pack(fill="both", expand=True)

    # Banner/header.
    banner = tk.Canvas(main, height=112, bg=PANEL, bd=0, highlightthickness=1, highlightbackground=BORDER)
    banner.pack(fill="x", pady=(0, 12))
    banner.bind("<Configure>", lambda e: _paint_banner(self, banner, e.width))

    body = ttk.Frame(main, style="C24.TFrame")
    body.pack(fill="both", expand=True)

    config = ttk.Frame(body, style="Panel.TFrame", padding=14)
    config.pack(fill="x")
    ttk.Label(config, text="CONFIGURAZIONE LOCALE", style="Section.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0, 9))

    self.vars = {
        "ollama_url": tk.StringVar(value=self.cfg.ollama_url),
        "ollama_model": tk.StringVar(value=self.cfg.ollama_model),
        "comfy_url": tk.StringVar(value=self.cfg.comfy_url),
        "comfy_dir": tk.StringVar(value=self.cfg.comfy_dir),
        "checkpoint": tk.StringVar(value=self.cfg.checkpoint),
        "output_dir": tk.StringVar(value=self.cfg.output_dir),
    }

    def lab(row, text):
        ttk.Label(config, text=text, style="Panel.TLabel").grid(row=row, column=0, sticky="w", padx=(0, 10), pady=5)

    lab(1, "Ollama URL")
    ttk.Entry(config, textvariable=self.vars["ollama_url"], style="C24.TEntry").grid(row=1, column=1, columnspan=3, sticky="ew", pady=5)

    lab(2, "Modello LLM")
    self.model_combo = ttk.Combobox(config, textvariable=self.vars["ollama_model"], style="C24.TCombobox", state="normal")
    self.model_combo.grid(row=2, column=1, columnspan=2, sticky="ew", pady=5)
    ttk.Button(config, text="Aggiorna modelli", style="Secondary.TButton", command=lambda: self._refresh_ollama_models(False)).grid(row=2, column=3, padx=(7, 0), pady=5)

    self.model_hint = tk.StringVar(value="Seleziona qualsiasi modello generativo installato in Ollama")
    ttk.Label(config, textvariable=self.model_hint, style="Muted.TLabel").grid(row=3, column=1, columnspan=3, sticky="w", pady=(0, 4))

    lab(4, "ComfyUI URL")
    ttk.Entry(config, textvariable=self.vars["comfy_url"], style="C24.TEntry").grid(row=4, column=1, columnspan=3, sticky="ew", pady=5)

    lab(5, "Cartella ComfyUI")
    ttk.Entry(config, textvariable=self.vars["comfy_dir"], style="C24.TEntry").grid(row=5, column=1, columnspan=2, sticky="ew", pady=5)
    ttk.Button(config, text="Sfoglia", style="Secondary.TButton", command=self._browse_comfy).grid(row=5, column=3, padx=(7, 0), pady=5)

    lab(6, "Checkpoint SDXL")
    self.cp_combo = ttk.Combobox(config, textvariable=self.vars["checkpoint"], style="C24.TCombobox", state="normal")
    self.cp_combo.grid(row=6, column=1, columnspan=3, sticky="ew", pady=5)

    lab(7, "Cartella output")
    ttk.Entry(config, textvariable=self.vars["output_dir"], style="C24.TEntry").grid(row=7, column=1, columnspan=2, sticky="ew", pady=5)
    ttk.Button(config, text="Sfoglia", style="Secondary.TButton", command=self._browse_output).grid(row=7, column=3, padx=(7, 0), pady=5)

    opts = ttk.Frame(config, style="Panel.TFrame")
    opts.grid(row=8, column=0, columnspan=4, sticky="w", pady=(9, 0))
    self.auto_var = tk.BooleanVar(value=self.cfg.auto_start_services)
    self.vision_var = tk.BooleanVar(value=self.cfg.require_vision_qa)
    ttk.Checkbutton(opts, text="Avvia automaticamente i servizi quando possibile", variable=self.auto_var, style="C24.TCheckbutton").pack(side="left")
    ttk.Checkbutton(opts, text="Richiedi QA vision locale", variable=self.vision_var, style="C24.TCheckbutton").pack(side="left", padx=18)

    config.columnconfigure(1, weight=1)
    config.columnconfigure(2, weight=1)

    actions = ttk.Frame(body, style="C24.TFrame")
    actions.pack(fill="x", pady=12)
    self.pre_btn = ttk.Button(actions, text="PRECHECK", style="Secondary.TButton", command=self.precheck)
    self.pre_btn.pack(side="left")
    self.gen_btn = ttk.Button(actions, text="GENERA BATCH", style="Primary.TButton", command=self.generate)
    self.gen_btn.pack(side="left", padx=8)
    self.cancel_btn = ttk.Button(actions, text="ANNULLA", style="Danger.TButton", command=self.cancel, state="disabled")
    self.cancel_btn.pack(side="left")
    ttk.Button(actions, text="Apri output", style="Secondary.TButton", command=self.open_output).pack(side="right")
    ttk.Button(actions, text="Apri log", style="Secondary.TButton", command=self.open_log).pack(side="right", padx=8)

    statusbar = ttk.Frame(body, style="C24.TFrame")
    statusbar.pack(fill="x", pady=(0, 6))
    self.status = tk.StringVar(value="Pronto. Esegui PRECHECK prima del primo batch.")
    ttk.Label(statusbar, textvariable=self.status, style="Status.TLabel").pack(side="left")
    ttk.Label(statusbar, text="v2.0.4  •  LOCAL", style="C24.TLabel").pack(side="right")

    self.progress = ttk.Progressbar(body, maximum=100, style="C24.Horizontal.TProgressbar")
    self.progress.pack(fill="x", pady=(0, 10))

    logframe = ttk.Frame(body, style="Panel.TFrame", padding=8)
    logframe.pack(fill="both", expand=True)
    ttk.Label(logframe, text="LOG OPERATIVO", style="Section.TLabel").pack(anchor="w", pady=(0, 5))
    self.logtxt = tk.Text(
        logframe, height=17, wrap="word", font=("Cascadia Mono", 9),
        bg=ENTRY, fg="#dce6f5", insertbackground=TEXT, selectbackground="#334b70",
        relief="flat", padx=9, pady=8, bd=0,
    )
    self.logtxt.pack(fill="both", expand=True)
    self.logtxt.configure(state="disabled")

    # Load installed models shortly after the GUI appears. Silent if Ollama is still starting.
    self.root.after(350, lambda: self._refresh_ollama_models(True))


def _paint_banner(self, canvas, width):
    canvas.delete("all")
    h = 112
    canvas.create_rectangle(0, 0, width, h, fill=PANEL, outline="")
    canvas.create_rectangle(0, 0, 8, h, fill=ACCENT, outline="")
    # C24 shield/mark.
    canvas.create_oval(27, 23, 91, 87, fill=ACCENT, outline="")
    canvas.create_text(59, 55, text="C24", fill="white", font=("Segoe UI Black", 19, "bold"))
    canvas.create_text(111, 34, anchor="w", text="COMPLOTTO24", fill=TEXT, font=("Segoe UI Black", 23, "bold"))
    canvas.create_text(112, 65, anchor="w", text="BATCH FACTORY  •  LOCAL AI EDITION", fill=MUTED, font=("Segoe UI Semibold", 10))
    canvas.create_text(112, 88, anchor="w", text="Trend → Qwen / GPT-OSS / altri Ollama → QA editoriale → ComfyUI → ZIP WordPress", fill="#7f91aa", font=("Segoe UI", 9))
    # Right-side pills.
    if width > 720:
        canvas.create_rectangle(width-184, 28, width-112, 54, fill="#193d35", outline="")
        canvas.create_text(width-148, 41, text="LOCAL", fill=SUCCESS, font=("Segoe UI Semibold", 9))
        canvas.create_rectangle(width-102, 28, width-25, 54, fill="#40252c", outline="")
        canvas.create_text(width-63, 41, text="v2.0.4", fill="#ff9da4", font=("Segoe UI Semibold", 9))


def _refresh_ollama_models(self, silent=False):
    self._sync_cfg()
    try:
        client = core.OllamaClient(self.cfg, core.HTTPClient(self.logger), self.logger)
        if not client.is_up():
            if not client.try_start():
                raise RuntimeError("Ollama non raggiungibile")
        names = client.model_names()
        if not names:
            raise RuntimeError("Nessun modello Ollama installato")
        self.model_combo["values"] = names
        current = self.vars["ollama_model"].get().strip()
        if current not in names:
            # Prefer Qwen/GPT-OSS style generative models, otherwise first available.
            preferred = next((n for n in names if any(k in n.lower() for k in ("qwen", "gpt-oss", "llama", "gemma", "mistral"))), names[0])
            self.vars["ollama_model"].set(preferred)
            current = preferred
        info = client.model_info(current)
        details = info.get("details") or {}
        family = details.get("family") or details.get("families") or ""
        params = details.get("parameter_size") or ""
        quant = details.get("quantization_level") or ""
        meta = " • ".join(str(x) for x in (family, params, quant) if x)
        self.model_hint.set(f"{len(names)} modelli Ollama disponibili" + (f"  •  {meta}" if meta else ""))
        self.cfg.ollama_model = current
        self.cfg.save()
        if not silent:
            self.status.set(f"Modelli Ollama aggiornati: {len(names)} disponibili")
    except Exception as exc:
        self.model_hint.set(f"Impossibile leggere i modelli: {exc}")
        if not silent:
            try:
                core.messagebox.showwarning(core.APP_NAME, f"Impossibile aggiornare i modelli Ollama:\n\n{exc}")
            except Exception:
                pass


def _on_model_selected(self, _event=None):
    self._sync_cfg()
    self.model_hint.set(f"Modello selezionato: {self.cfg.ollama_model}")


# Patch GUI methods before core.main creates the window.
core.BatchFactoryGUI._build = _build_branded
core.BatchFactoryGUI._refresh_ollama_models = _refresh_ollama_models
core.BatchFactoryGUI._paint_banner = _paint_banner
core.BatchFactoryGUI._on_model_selected = _on_model_selected


# Wrap _build once more to bind the combobox selection after creation.
_actual_build = core.BatchFactoryGUI._build

def _build_and_bind(self):
    _actual_build(self)
    try:
        self.model_combo.bind("<<ComboboxSelected>>", self._on_model_selected)
    except Exception:
        pass

core.BatchFactoryGUI._build = _build_and_bind


def _self_test_v204():
    v203._self_test_v203()
    im = _make_app_icon(64)
    assert im.size == (64, 64) and im.mode == "RGBA"
    print("V2.0.4 MODEL/UI SELF-TEST PASS")


def main():
    if "--self-test" in os.sys.argv:
        _self_test_v204()
        return
    core.main()


if __name__ == "__main__":
    main()
