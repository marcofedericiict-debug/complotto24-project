from __future__ import annotations

import json
import os
import random
from dataclasses import dataclass, asdict, fields
from pathlib import Path
from typing import Any

import c24_batch_factory_local_v210 as v210

core = v210.core
core.APP_VERSION = "2.0.11"
core.APP_NAME = "Complotto24 Batch Factory LOCAL v2.0.11"

IMAGE_PROFILES = ("Auto", "Editorial", "Portrait", "Technology", "Landscape", "No People")
QUALITY_PROFILES = ("Fast", "Balanced", "High", "Custom")
FACE_MODES = ("Auto", "Disattivato", "Sempre")
RESOLUTIONS = ("1024x576", "1152x648", "1280x720")
NEGATIVE_PRESETS = ("Photorealistic", "Portrait", "Clean", "Minimal")

DEFAULT_NEGATIVE = (
    "visible text, letters, words, caption, watermark, logo, brand, collage, grid, split screen, "
    "multiple panels, infographic, dashboard, spreadsheet, website, UI, screenshot, poster, article card, "
    "frame, border, low resolution, deformed anatomy, duplicate subjects"
)
NEGATIVE_PRESET_TEXT = {
    "Photorealistic": DEFAULT_NEGATIVE + ", cartoon, illustration, painting, CGI, plastic skin, waxy skin, deformed face, distorted face, asymmetrical eyes, malformed hands, extra fingers, extra limbs, blurry face",
    "Portrait": DEFAULT_NEGATIVE + ", cartoon, illustration, painting, CGI, plastic skin, waxy skin, deformed face, distorted face, asymmetrical eyes, crossed eyes, malformed ears, malformed hands, extra fingers, extra limbs, blurry face, oversmoothed skin, doll-like face",
    "Clean": DEFAULT_NEGATIVE + ", deformed face, malformed hands, extra fingers, blurry, noisy, oversaturated",
    "Minimal": DEFAULT_NEGATIVE,
}
PROFILE_PREFIX = {
    "Editorial": "Photorealistic editorial press photograph. One single continuous physical scene only. Natural human proportions, realistic materials, documentary photography, plausible real-world lighting. ",
    "Portrait": "Photorealistic editorial portrait photograph. One single continuous physical scene only. Natural facial anatomy, realistic skin texture, realistic eyes, natural human proportions, professional press photography, shallow depth of field, plausible real-world lighting. ",
    "Technology": "Photorealistic technology editorial photograph. One single continuous physical scene only. Realistic devices and materials, contemporary professional environment, documentary press photography. ",
    "Landscape": "Photorealistic wide editorial photograph. One single continuous physical scene only. Natural perspective, realistic architecture and environment, documentary photography, landscape composition. ",
    "No People": "Photorealistic editorial photograph with no people and no human figures. One single continuous physical scene only. Realistic materials, natural perspective, documentary photography. ",
}
PERSON_HINTS = (
    "person", "people", "man ", "woman", "male ", "female ", "executive", "ceo", "president", "minister", "politician", "actor", "actress", "singer", "athlete", "player", "portrait", "face", "journalist",
    "uomo", "donna", "persona", "persone", "dirigente", "presidente", "ministro", "politico", "attore", "attrice", "cantante", "atleta", "giocatore", "volto", "ritratto",
)
TECH_HINTS = ("computer", "server", "datacenter", "data center", "smartphone", "phone", "robot", "ai ", "artificial intelligence", "chip", "semiconductor", "laboratory", "console", "software", "cyber", "device", "technology", "tecnologia")
LANDSCAPE_HINTS = ("city", "street", "building", "landscape", "mountain", "beach", "airport", "station", "train", "bridge", "città", "strada", "edificio", "paesaggio", "montagna", "spiaggia", "aeroporto", "stazione", "treno", "ponte")

@dataclass
class ModularConfig(core.Config):
    image_profile: str = "Auto"
    image_quality: str = "Balanced"
    image_resolution: str = "1152x648"
    face_refinement: str = "Auto"
    negative_preset: str = "Photorealistic"
    image_sampler: str = "dpmpp_2m"
    image_scheduler: str = "karras"
    custom_steps: int = 28
    custom_cfg: float = 5.5
    face_steps: int = 20
    face_cfg: float = 5.0
    face_denoise: float = 0.32
    face_guide_size: int = 512
    face_max_size: int = 1024
    face_threshold: float = 0.35
    face_crop_factor: float = 3.0
    detector_model: str = "bbox/face_yolov8m.pt"

    @classmethod
    def load(cls) -> "ModularConfig":
        core.ensure_dirs()
        if not core.CONFIG_PATH.exists():
            return cls()
        try:
            obj = json.loads(core.CONFIG_PATH.read_text(encoding="utf-8"))
            allowed = {f.name for f in fields(cls)}
            return cls(**{k: v for k, v in obj.items() if k in allowed})
        except Exception:
            return cls()

    def save(self) -> None:
        core.ensure_dirs()
        core.CONFIG_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")

core.Config = ModularConfig


def _resolution_tuple(value: str) -> tuple[int, int]:
    try:
        w, h = str(value).lower().split("x", 1)
        w, h = int(w), int(h)
        if w >= 512 and h >= 288:
            return w, h
    except Exception:
        pass
    return (1152, 648)


def _quality_params(cfg: ModularConfig) -> dict[str, Any]:
    if cfg.image_quality == "Fast":
        return {"steps": 20, "cfg": 5.0, "face_steps": 14, "face_denoise": 0.28}
    if cfg.image_quality == "High":
        return {"steps": 34, "cfg": 5.5, "face_steps": 24, "face_denoise": 0.34}
    if cfg.image_quality == "Custom":
        return {"steps": max(1, int(cfg.custom_steps)), "cfg": float(cfg.custom_cfg), "face_steps": max(1, int(cfg.face_steps)), "face_denoise": min(1.0, max(0.01, float(cfg.face_denoise)))}
    return {"steps": 28, "cfg": 5.5, "face_steps": 20, "face_denoise": 0.32}


def _auto_profile(prompt: str) -> str:
    low = " " + (prompt or "").lower() + " "
    if any(k in low for k in PERSON_HINTS): return "Portrait"
    if any(k in low for k in TECH_HINTS): return "Technology"
    if any(k in low for k in LANDSCAPE_HINTS): return "Landscape"
    return "Editorial"


def _effective_profile(cfg: ModularConfig, prompt: str) -> str:
    return _auto_profile(prompt) if cfg.image_profile == "Auto" else cfg.image_profile


def _should_face_refine(cfg: ModularConfig, prompt: str, profile: str) -> bool:
    if cfg.face_refinement == "Disattivato": return False
    if cfg.face_refinement == "Sempre": return True
    low = " " + (prompt or "").lower() + " "
    return profile == "Portrait" or any(k in low for k in PERSON_HINTS)


def _comfy_object_info(self) -> dict[str, Any]:
    return self.http.request("GET", self.base + "/object_info", timeout=20, retries=2).json()


def _comfy_capabilities(self) -> dict[str, Any]:
    data = self.object_info()
    req = (((data.get("KSampler") or {}).get("input") or {}).get("required") or {})
    try: samplers = list(req.get("sampler_name", [[], {}])[0])
    except Exception: samplers = []
    try: schedulers = list(req.get("scheduler", [[], {}])[0])
    except Exception: schedulers = []
    try: detector_models = list(((((data.get("UltralyticsDetectorProvider") or {}).get("input") or {}).get("required") or {}).get("model_name") or [[], {}])[0])
    except Exception: detector_models = []
    return {
        "face_detailer": "FaceDetailer" in data,
        "ultralytics": "UltralyticsDetectorProvider" in data,
        "detector_models": detector_models,
        "samplers": samplers,
        "schedulers": schedulers,
        "checkpoint_loader": "CheckpointLoaderSimple" in data,
        "ksampler": "KSampler" in data,
    }

core.ComfyClient.object_info = _comfy_object_info
core.ComfyClient.capabilities = _comfy_capabilities


def _resolve_detector(self, caps: dict[str, Any]) -> str:
    models = caps.get("detector_models") or []
    wanted = str(getattr(self.cfg, "detector_model", "") or "").strip()
    if wanted and wanted in models: return wanted
    face_models = [m for m in models if "face" in str(m).lower() and str(m).lower().startswith("bbox/")]
    if face_models:
        self.cfg.detector_model = face_models[0]; return face_models[0]
    bbox = [m for m in models if str(m).lower().startswith("bbox/")]
    if bbox:
        self.cfg.detector_model = bbox[0]; return bbox[0]
    return ""

core.ComfyClient.resolve_detector = _resolve_detector


def _build_modular_workflow(self, prompt: str, negative: str, filename_prefix: str, *, width: int, height: int, seed: int, checkpoint: str, profile: str, use_face: bool, caps: dict[str, Any]) -> dict[str, Any]:
    cfg = self.cfg
    qp = _quality_params(cfg)
    samplers = caps.get("samplers") or []
    schedulers = caps.get("schedulers") or []
    sampler = cfg.image_sampler if not samplers or cfg.image_sampler in samplers else ("dpmpp_2m" if "dpmpp_2m" in samplers else samplers[0])
    scheduler = cfg.image_scheduler if not schedulers or cfg.image_scheduler in schedulers else ("karras" if "karras" in schedulers else schedulers[0])
    wf: dict[str, Any] = {
        "1": {"class_type": "CheckpointLoaderSimple", "inputs": {"ckpt_name": checkpoint}},
        "2": {"class_type": "CLIPTextEncode", "inputs": {"text": prompt, "clip": ["1", 1]}},
        "3": {"class_type": "CLIPTextEncode", "inputs": {"text": negative, "clip": ["1", 1]}},
        "4": {"class_type": "EmptyLatentImage", "inputs": {"width": width, "height": height, "batch_size": 1}},
        "5": {"class_type": "KSampler", "inputs": {"seed": seed, "steps": int(qp["steps"]), "cfg": float(qp["cfg"]), "sampler_name": sampler, "scheduler": scheduler, "denoise": 1.0, "model": ["1", 0], "positive": ["2", 0], "negative": ["3", 0], "latent_image": ["4", 0]}},
        "6": {"class_type": "VAEDecode", "inputs": {"samples": ["5", 0], "vae": ["1", 2]}},
    }
    save_input = ["6", 0]
    if use_face:
        detector = self.resolve_detector(caps)
        if detector:
            wf["7"] = {"class_type": "UltralyticsDetectorProvider", "inputs": {"model_name": detector}}
            wf["8"] = {"class_type": "FaceDetailer", "inputs": {
                "image": ["6", 0], "model": ["1", 0], "clip": ["1", 1], "vae": ["1", 2],
                "guide_size": float(cfg.face_guide_size), "guide_size_for": True, "max_size": float(cfg.face_max_size),
                "seed": seed, "steps": int(qp["face_steps"]), "cfg": float(cfg.face_cfg), "sampler_name": sampler, "scheduler": scheduler,
                "positive": ["2", 0], "negative": ["3", 0], "denoise": float(qp["face_denoise"]), "feather": 5,
                "noise_mask": True, "force_inpaint": True, "bbox_threshold": float(cfg.face_threshold), "bbox_dilation": 10,
                "bbox_crop_factor": float(cfg.face_crop_factor), "sam_detection_hint": "center-1", "sam_dilation": 0, "sam_threshold": 0.93,
                "sam_bbox_expansion": 0, "sam_mask_hint_threshold": 0.7, "sam_mask_hint_use_negative": "False", "drop_size": 10,
                "bbox_detector": ["7", 0], "wildcard": "", "cycle": 1,
            }}
            save_input = ["8", 0]
    save_id = "9" if use_face and "8" in wf else "7"
    wf[save_id] = {"class_type": "SaveImage", "inputs": {"filename_prefix": filename_prefix, "images": save_input}}
    return wf

core.ComfyClient.modular_workflow = _build_modular_workflow


def _generate_v211(self, prompt: str, slug: str, target_dir: Path, attempt: int = 1) -> Path:
    cfg: ModularConfig = self.cfg
    checkpoint = self.resolve_checkpoint()
    caps = self.capabilities()
    profile = _effective_profile(cfg, prompt)
    use_face = _should_face_refine(cfg, prompt, profile)
    if use_face and not (caps.get("face_detailer") and caps.get("ultralytics")):
        if cfg.face_refinement == "Sempre":
            raise RuntimeError("Face refinement richiesto ma FaceDetailer/UltralyticsDetectorProvider non sono disponibili in ComfyUI")
        self.logger.log("Face refinement Auto non disponibile: proseguo con workflow base"); use_face = False
    if use_face and not self.resolve_detector(caps):
        if cfg.face_refinement == "Sempre": raise RuntimeError("Face refinement richiesto ma non trovo un detector bbox volto in ComfyUI")
        self.logger.log("Face refinement Auto: detector volto non trovato; proseguo senza detailer"); use_face = False
    width, height = _resolution_tuple(cfg.image_resolution)
    if attempt > 1: width, height = (1024, 576)
    seed = random.randint(1, 2**63 - 1)
    preset = cfg.negative_preset if cfg.negative_preset in NEGATIVE_PRESET_TEXT else "Photorealistic"
    negative = NEGATIVE_PRESET_TEXT[preset]
    prefix = PROFILE_PREFIX.get(profile, PROFILE_PREFIX["Editorial"])
    pure = prefix + prompt.strip() + " Natural photographic perspective, realistic materials and proportions, no visible text. " + ("Landscape 16:9 composition." if profile != "Portrait" else "Editorial 16:9 composition, subject naturally framed.")
    wf = self.modular_workflow(pure, negative, f"c24bf/{slug}", width=width, height=height, seed=seed, checkpoint=checkpoint, profile=profile, use_face=use_face, caps=caps)
    qp = _quality_params(cfg)
    self.logger.log(f"ComfyUI profilo={profile} | quality={cfg.image_quality} | {width}x{height} | sampler={cfg.image_sampler}/{cfg.image_scheduler} | steps={qp['steps']} cfg={qp['cfg']} | FaceDetailer={'ON' if use_face else 'OFF'}")
    try:
        resp = self.http.request("POST", self.base + "/prompt", json={"prompt": wf, "client_id": core.uuid.uuid4().hex}, timeout=20, retries=1).json()
    except Exception as e:
        if any(k in str(e).lower() for k in ("out of memory", "cuda", "oom")):
            raise RuntimeError("ComfyUI ha esaurito la VRAM. Il programma riproverà a risoluzione ridotta.") from e
        raise
    pid = resp.get("prompt_id")
    if not pid: raise RuntimeError(f"ComfyUI ha rifiutato il workflow modulare: {resp.get('error') or resp}")
    deadline = core.time.time() + core.COMFY_IMAGE_TIMEOUT; hist = None
    while core.time.time() < deadline:
        core.time.sleep(1.5)
        try: h = self.http.request("GET", self.base + f"/history/{pid}", timeout=12, retries=1).json()
        except Exception: continue
        if pid in h:
            hist = h[pid]; status = hist.get("status", {})
            if status.get("completed") is True or hist.get("outputs"): break
            msgs = status.get("messages") or []
            if any("error" in str(m).lower() for m in msgs): raise RuntimeError(f"ComfyUI errore job: {msgs[-1] if msgs else status}")
    if not hist: raise TimeoutError(f"ComfyUI non ha completato l'immagine entro {core.COMFY_IMAGE_TIMEOUT}s")
    outputs = hist.get("outputs", {}); imgs = []
    for node in outputs.values(): imgs.extend(node.get("images", []) or [])
    if not imgs:
        status_text = json.dumps(hist.get("status", {}), ensure_ascii=False)[:1500]
        if any(k in status_text.lower() for k in ("out of memory", "cuda", "oom")): raise RuntimeError("ComfyUI OOM/CUDA durante la generazione")
        raise RuntimeError("ComfyUI ha completato il job ma non ha prodotto immagini")
    info = imgs[0]
    params = core.urlencode({"filename": info.get("filename", ""), "subfolder": info.get("subfolder", ""), "type": info.get("type", "output")})
    raw = self.http.request("GET", self.base + "/view?" + params, timeout=60, retries=2).content
    target_dir.mkdir(parents=True, exist_ok=True); raw_path = target_dir / f"{slug}.source.png"; raw_path.write_bytes(raw)
    try:
        with core.Image.open(raw_path) as im: im.verify()
    except Exception as e:
        raw_path.unlink(missing_ok=True); raise RuntimeError("L'immagine restituita da ComfyUI è corrotta") from e
    return raw_path

core.ComfyClient.generate = _generate_v211

_original_precheck = core.BatchEngine.precheck

def _precheck_v211(self):
    result = _original_precheck(self)
    caps = self.comfy.capabilities(); detector = self.comfy.resolve_detector(caps)
    result["image_engine"] = f"OK modular | FaceDetailer={'SI' if caps.get('face_detailer') else 'NO'} | Ultralytics={'SI' if caps.get('ultralytics') else 'NO'} | detector={detector or 'n/d'}"
    self.logger.log(f"ComfyUI capabilities: FaceDetailer={caps.get('face_detailer')} | Ultralytics={caps.get('ultralytics')} | detector={detector or 'n/d'} | samplers={len(caps.get('samplers') or [])} | schedulers={len(caps.get('schedulers') or [])}")
    if self.cfg.face_refinement == "Sempre" and (not caps.get("face_detailer") or not caps.get("ultralytics") or not detector):
        raise RuntimeError("Precheck: Face refinement=Sempre ma FaceDetailer, UltralyticsDetectorProvider o il detector volto non sono disponibili.")
    return result

core.BatchEngine.precheck = _precheck_v211

_previous_build = core.BatchFactoryGUI._build

def _build_v211(self):
    _previous_build(self)
    self.root.title(core.APP_NAME); self.root.geometry("1180x940"); self.root.minsize(1000, 760)
    tk, ttk = core.tk, core.ttk
    try: config = self.model_combo.master
    except Exception: return
    self.image_vars = {
        "image_profile": tk.StringVar(value=self.cfg.image_profile), "image_quality": tk.StringVar(value=self.cfg.image_quality),
        "image_resolution": tk.StringVar(value=self.cfg.image_resolution), "face_refinement": tk.StringVar(value=self.cfg.face_refinement),
        "negative_preset": tk.StringVar(value=self.cfg.negative_preset), "image_sampler": tk.StringVar(value=self.cfg.image_sampler),
        "image_scheduler": tk.StringVar(value=self.cfg.image_scheduler), "custom_steps": tk.StringVar(value=str(self.cfg.custom_steps)),
        "custom_cfg": tk.StringVar(value=str(self.cfg.custom_cfg)), "face_steps": tk.StringVar(value=str(self.cfg.face_steps)),
        "face_cfg": tk.StringVar(value=str(self.cfg.face_cfg)), "face_denoise": tk.StringVar(value=str(self.cfg.face_denoise)),
        "face_threshold": tk.StringVar(value=str(self.cfg.face_threshold)), "detector_model": tk.StringVar(value=self.cfg.detector_model),
    }
    row0 = 10
    ttk.Separator(config, orient="horizontal").grid(row=row0, column=0, columnspan=4, sticky="ew", pady=(12, 8))
    ttk.Label(config, text="MOTORE IMMAGINI MODULARE", style="Panel.TLabel").grid(row=row0+1, column=0, columnspan=4, sticky="w", pady=(0,7))
    def combo(row, label, key, values):
        ttk.Label(config, text=label, style="Panel.TLabel").grid(row=row, column=0, sticky="w", padx=(0,10), pady=3)
        c = ttk.Combobox(config, textvariable=self.image_vars[key], values=values, state="readonly"); c.grid(row=row, column=1, sticky="ew", pady=3); return c
    self.image_profile_combo = combo(row0+2, "Profilo scena", "image_profile", IMAGE_PROFILES)
    self.quality_combo = combo(row0+3, "Qualità", "image_quality", QUALITY_PROFILES)
    self.resolution_combo = combo(row0+4, "Risoluzione", "image_resolution", RESOLUTIONS)
    self.face_combo = combo(row0+5, "Face refinement", "face_refinement", FACE_MODES)
    self.negative_combo = combo(row0+6, "Negative preset", "negative_preset", NEGATIVE_PRESETS)
    self.sampler_combo = combo(row0+7, "Sampler", "image_sampler", (self.cfg.image_sampler, "dpmpp_2m", "euler", "dpmpp_2m_sde"))
    self.scheduler_combo = combo(row0+8, "Scheduler", "image_scheduler", (self.cfg.image_scheduler, "karras", "normal", "simple"))
    self.detector_combo = combo(row0+9, "Detector volto", "detector_model", (self.cfg.detector_model,))
    adv = ttk.Frame(config, style="Panel.TFrame"); adv.grid(row=row0+2, column=2, rowspan=8, columnspan=2, sticky="nsew", padx=(18,0))
    ttk.Label(adv, text="PARAMETRI CUSTOM / FACE", style="Panel.TLabel").grid(row=0, column=0, columnspan=4, sticky="w", pady=(0,7))
    for i, (lab, key) in enumerate((("Steps custom","custom_steps"),("CFG custom","custom_cfg"),("Face steps","face_steps"),("Face CFG","face_cfg"),("Face denoise","face_denoise"),("Face threshold","face_threshold")), 1):
        ttk.Label(adv, text=lab, style="Muted.TLabel").grid(row=i, column=0, sticky="w", pady=3)
        ttk.Entry(adv, textvariable=self.image_vars[key], width=10).grid(row=i, column=1, sticky="w", padx=(8,18), pady=3)
    self.comfy_caps_var = tk.StringVar(value="Capacità ComfyUI: esegui PRECHECK / Aggiorna")
    ttk.Label(adv, textvariable=self.comfy_caps_var, style="Muted.TLabel", wraplength=420).grid(row=7, column=0, columnspan=4, sticky="w", pady=(9,4))
    ttk.Button(adv, text="AGGIORNA COMFYUI", style="Secondary.TButton", command=lambda: _refresh_comfy_ui(self, True)).grid(row=8, column=0, columnspan=2, sticky="w", pady=(4,0))
    try:
        walker = v210.v209.v208.v207.v206.v205._walk_widgets
        for widget in walker(self.root):
            try:
                if str(widget.cget("text")) == "v2.0.10  •  LOCAL": widget.configure(text="v2.0.11  •  MODULAR")
            except Exception: pass
    except Exception: pass
    self.root.after(250, lambda: _refresh_comfy_ui(self, False))

core.BatchFactoryGUI._build = _build_v211

_previous_sync = core.BatchFactoryGUI._sync_cfg

def _sync_cfg_v211(self):
    _previous_sync(self)
    if not hasattr(self, "image_vars"): return
    iv = self.image_vars
    for key in ("image_profile","image_quality","image_resolution","face_refinement","negative_preset","image_sampler","image_scheduler","detector_model"):
        setattr(self.cfg, key, iv[key].get())
    try:
        self.cfg.custom_steps = max(1, int(float(iv["custom_steps"].get()))); self.cfg.custom_cfg = float(iv["custom_cfg"].get())
        self.cfg.face_steps = max(1, int(float(iv["face_steps"].get()))); self.cfg.face_cfg = float(iv["face_cfg"].get())
        self.cfg.face_denoise = min(1.0, max(0.01, float(iv["face_denoise"].get()))); self.cfg.face_threshold = min(1.0, max(0.01, float(iv["face_threshold"].get())))
    except Exception as exc: raise ValueError(f"Parametri immagine non validi: {exc}") from exc
    self.cfg.save()

core.BatchFactoryGUI._sync_cfg = _sync_cfg_v211


def _refresh_comfy_ui(self, show_popup: bool = False):
    try:
        if hasattr(self, "vars"):
            self.cfg.comfy_url = self.vars["comfy_url"].get().strip(); self.cfg.checkpoint = self.vars["checkpoint"].get().strip()
        client = core.ComfyClient(self.cfg, core.HTTPClient(self.logger), self.logger)
        if not client.is_up(): self.comfy_caps_var.set("Capacità ComfyUI: server non raggiungibile"); return
        caps = client.capabilities(); detector = client.resolve_detector(caps)
        samplers = caps.get("samplers") or [self.cfg.image_sampler]; schedulers = caps.get("schedulers") or [self.cfg.image_scheduler]; detectors = caps.get("detector_models") or ([detector] if detector else [])
        self.sampler_combo["values"] = samplers; self.scheduler_combo["values"] = schedulers; self.detector_combo["values"] = detectors
        if self.image_vars["image_sampler"].get() not in samplers and samplers: self.image_vars["image_sampler"].set("dpmpp_2m" if "dpmpp_2m" in samplers else samplers[0])
        if self.image_vars["image_scheduler"].get() not in schedulers and schedulers: self.image_vars["image_scheduler"].set("karras" if "karras" in schedulers else schedulers[0])
        if detector: self.image_vars["detector_model"].set(detector)
        msg = f"FaceDetailer={'SI' if caps.get('face_detailer') else 'NO'} • Ultralytics={'SI' if caps.get('ultralytics') else 'NO'} • detector={detector or 'n/d'} • sampler={len(samplers)} • scheduler={len(schedulers)}"
        self.comfy_caps_var.set("Capacità ComfyUI: " + msg); self.logger.log("Refresh ComfyUI GUI: " + msg)
        if show_popup: core.messagebox.showinfo(core.APP_NAME, "ComfyUI aggiornato.\n\n" + msg)
    except Exception as exc:
        self.comfy_caps_var.set(f"Capacità ComfyUI: errore - {exc}"); self.logger.log(f"Refresh ComfyUI fallito: {exc}")
        if show_popup: core.messagebox.showwarning(core.APP_NAME, f"Impossibile aggiornare ComfyUI:\n\n{exc}")


def self_test() -> None:
    current = core.APP_VERSION
    try:
        core.APP_VERSION = "2.0.10"; v210.self_test()
    finally: core.APP_VERSION = current
    cfg = ModularConfig()
    assert _resolution_tuple("1152x648") == (1152, 648)
    assert _quality_params(cfg)["steps"] == 28
    assert _auto_profile("male technology executive during a conference") == "Portrait"
    assert _auto_profile("empty modern data center with server racks") == "Technology"
    assert _should_face_refine(cfg, "male executive portrait", "Portrait") is True
    cfg.face_refinement = "Disattivato"; assert _should_face_refine(cfg, "male executive portrait", "Portrait") is False
    class DummyComfy:
        cfg = ModularConfig()
        def resolve_detector(self, caps): return "bbox/face_yolov8m.pt"
    caps = {"samplers":["dpmpp_2m"], "schedulers":["karras"], "detector_models":["bbox/face_yolov8m.pt"]}
    wf = _build_modular_workflow(DummyComfy(), "portrait", "bad anatomy", "test", width=1152, height=648, seed=42, checkpoint="x.safetensors", profile="Portrait", use_face=True, caps=caps)
    assert wf["8"]["class_type"] == "FaceDetailer" and wf["7"]["class_type"] == "UltralyticsDetectorProvider" and wf["9"]["inputs"]["images"] == ["8",0]
    assert core.APP_VERSION == "2.0.11"
    print("V2.0.11 MODULAR COMFYUI SELF-TEST PASS")


def main() -> None:
    if "--self-test" in os.sys.argv: self_test(); return
    core.ensure_dirs()
    if core.tk is None: raise SystemExit("Tkinter non disponibile")
    core.BatchFactoryGUI().run()

if __name__ == "__main__": main()
