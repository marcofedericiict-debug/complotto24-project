from __future__ import annotations

import base64
import difflib
import hashlib
import html
import io
import json
import os
import queue
import random
import re
import shutil
import socket
import subprocess
import sys
import threading
import time
import traceback
import uuid
import webbrowser
import zipfile
from dataclasses import dataclass, asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse, urlencode
import xml.etree.ElementTree as ET

import requests
from bs4 import BeautifulSoup
from PIL import Image, ImageOps, ImageStat

try:
    import tkinter as tk
    from tkinter import ttk, filedialog, messagebox
except Exception:
    tk = None

APP_NAME = "Complotto24 Batch Factory LOCAL"
APP_VERSION = "2.0.0"
DEFAULT_OLLAMA = "http://127.0.0.1:11434"
DEFAULT_COMFY = "http://127.0.0.1:8188"
DEFAULT_MODEL = "qwen3.8:27b"
DEFAULT_IMAGE_WIDTH = 1152
DEFAULT_IMAGE_HEIGHT = 640
FALLBACK_IMAGE_WIDTH = 1024
FALLBACK_IMAGE_HEIGHT = 576
FINAL_IMAGE_SIZE = (1600, 900)
HTTP_TIMEOUT = 25
OLLAMA_TIMEOUT = 420
COMFY_IMAGE_TIMEOUT = 720
MAX_IMAGE_RETRIES = 3
MAX_TEXT_RETRIES = 3
ANTI_SPOILER = [
    r"\bfake\b", r"\binventat[aoei]\b", r"\bfittizi[aoei]\b", r"\bsimulazion[ei]\b",
    r"\bscenario inventato\b", r"\bnon reale\b", r"\bplausibile ma inventat[ao]\b",
    r"\bcreat[ao] per complotto24\b", r"\bcomplotto24\b", r"\bnon verificabile\b",
]
SAFE_FAKE_BANS = [
    "morto", "morta", "decesso", "arrestato", "arrestata", "omicidio", "stupro", "terrorismo",
    "tumore", "cancro", "cura miracolosa", "vaccino pericoloso", "bonus da", "rimborso automatico",
    "conto corrente bloccato", "banca fallita", "pensione sospesa", "farmaco ritirato",
]
EDITORS = ["Luca Ferri", "Giulia Moretti", "Sara Donati", "Marta Rinaldi", "Andrea Bellini", "Elena Marchetti", "Riccardo Neri", "Chiara Galli", "Paolo Vitale", "Federica Romano"]

APP_DIR = Path(os.environ.get("LOCALAPPDATA", Path.home())) / "Complotto24BatchFactory"
CONFIG_PATH = APP_DIR / "config.json"
LOG_DIR = APP_DIR / "logs"
STATE_DIR = APP_DIR / "state"
HISTORY_PATH = APP_DIR / "history.json"


def default_output_dir() -> Path:
    desktop = Path(os.environ.get("USERPROFILE", str(Path.home()))) / "Desktop"
    return desktop / "Complotto24" / "Builds"


def ensure_dirs() -> None:
    for p in (APP_DIR, LOG_DIR, STATE_DIR, default_output_dir()):
        p.mkdir(parents=True, exist_ok=True)


def now_stamp() -> str:
    return datetime.now().strftime("%Y%m%d-%H%M%S")


def sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def slugify(text: str) -> str:
    import unicodedata
    text = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode("ascii")
    text = text.lower()
    text = re.sub(r"[^a-z0-9]+", "-", text).strip("-")
    return text[:180] or f"articolo-{uuid.uuid4().hex[:8]}"


def strip_html(text: str) -> str:
    return BeautifulSoup(text or "", "html.parser").get_text(" ", strip=True)


def normalize_for_similarity(text: str) -> str:
    text = strip_html(text).lower()
    text = re.sub(r"\b(20\d{2}|oggi|domani|ieri|italia|italiano|italiana)\b", " ", text)
    text = re.sub(r"[^a-zàèéìòù0-9 ]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def similarity(a: str, b: str) -> float:
    return difflib.SequenceMatcher(None, normalize_for_similarity(a), normalize_for_similarity(b)).ratio()


def json_extract(raw: str) -> Any:
    raw = (raw or "").strip()
    try:
        return json.loads(raw)
    except Exception:
        pass
    fence = re.search(r"```(?:json)?\s*(.*?)\s*```", raw, re.S | re.I)
    if fence:
        try:
            return json.loads(fence.group(1))
        except Exception:
            pass
    starts = [i for i, c in enumerate(raw) if c in "[{"]
    for start in starts:
        opening = raw[start]
        closing = "]" if opening == "[" else "}"
        depth = 0
        in_str = False
        esc = False
        for i in range(start, len(raw)):
            c = raw[i]
            if in_str:
                if esc:
                    esc = False
                elif c == "\\":
                    esc = True
                elif c == '"':
                    in_str = False
                continue
            if c == '"':
                in_str = True
            elif c == opening:
                depth += 1
            elif c == closing:
                depth -= 1
                if depth == 0:
                    try:
                        return json.loads(raw[start:i+1])
                    except Exception:
                        break
    raise ValueError("Il modello non ha restituito JSON valido")


class AppLogger:
    def __init__(self, callback: Optional[Callable[[str], None]] = None):
        ensure_dirs()
        self.path = LOG_DIR / f"batch-factory-{now_stamp()}.log"
        self.callback = callback
        self._lock = threading.Lock()

    def log(self, msg: str) -> None:
        line = f"[{datetime.now().strftime('%H:%M:%S')}] {msg}"
        with self._lock:
            self.path.open("a", encoding="utf-8").write(line + "\n")
        if self.callback:
            try:
                self.callback(line)
            except Exception:
                pass

    def exception(self, prefix: str, exc: BaseException) -> None:
        self.log(f"{prefix}: {exc}")
        with self._lock:
            with self.path.open("a", encoding="utf-8") as f:
                f.write(traceback.format_exc() + "\n")


@dataclass
class Config:
    ollama_url: str = DEFAULT_OLLAMA
    ollama_model: str = DEFAULT_MODEL
    vision_model: str = ""
    comfy_url: str = DEFAULT_COMFY
    comfy_dir: str = ""
    checkpoint: str = ""
    output_dir: str = str(default_output_dir())
    image_steps: int = 24
    image_cfg: float = 5.5
    require_vision_qa: bool = False
    auto_start_services: bool = True

    @classmethod
    def load(cls) -> "Config":
        ensure_dirs()
        if not CONFIG_PATH.exists():
            return cls()
        try:
            obj = json.loads(CONFIG_PATH.read_text(encoding="utf-8"))
            allowed = {k: v for k, v in obj.items() if k in cls.__annotations__}
            return cls(**allowed)
        except Exception:
            return cls()

    def save(self) -> None:
        ensure_dirs()
        CONFIG_PATH.write_text(json.dumps(asdict(self), ensure_ascii=False, indent=2), encoding="utf-8")


class HTTPClient:
    def __init__(self, logger: AppLogger):
        self.s = requests.Session()
        self.s.headers.update({
            "User-Agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) Complotto24BatchFactory/2.0",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.7",
        })
        self.logger = logger

    def request(self, method: str, url: str, *, retries: int = 3, timeout: int = HTTP_TIMEOUT, **kwargs) -> requests.Response:
        last = None
        for attempt in range(1, retries + 1):
            try:
                r = self.s.request(method, url, timeout=timeout, **kwargs)
                if r.status_code >= 500 or r.status_code in (408, 425, 429):
                    raise requests.HTTPError(f"HTTP {r.status_code}: {r.text[:500]}", response=r)
                r.raise_for_status()
                return r
            except Exception as e:
                last = e
                if attempt < retries:
                    wait = min(8, 1.5 ** attempt)
                    self.logger.log(f"HTTP retry {attempt}/{retries} per {url}: {e}; attendo {wait:.1f}s")
                    time.sleep(wait)
        raise RuntimeError(f"Connessione fallita verso {url}: {last}")


class OllamaClient:
    def __init__(self, cfg: Config, http: HTTPClient, logger: AppLogger):
        self.cfg, self.http, self.logger = cfg, http, logger

    @property
    def base(self) -> str:
        return self.cfg.ollama_url.rstrip("/")

    def tags(self) -> list[dict[str, Any]]:
        return self.http.request("GET", self.base + "/api/tags", timeout=8, retries=2).json().get("models", [])

    def is_up(self) -> bool:
        try:
            self.tags(); return True
        except Exception:
            return False

    def try_start(self) -> bool:
        if self.is_up():
            return True
        if not self.cfg.auto_start_services:
            return False
        exe = shutil.which("ollama") or shutil.which("ollama.exe")
        if not exe:
            candidates = [Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Ollama" / "ollama.exe"]
            exe = str(next((p for p in candidates if p.exists()), ""))
        if not exe:
            return False
        try:
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            subprocess.Popen([exe, "serve"], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
            for _ in range(20):
                time.sleep(0.5)
                if self.is_up():
                    self.logger.log("Ollama avviato automaticamente")
                    return True
        except Exception as e:
            self.logger.log(f"Avvio automatico Ollama fallito: {e}")
        return False

    def resolve_model(self) -> str:
        tags = self.tags()
        names = [m.get("name", "") for m in tags]
        wanted = self.cfg.ollama_model.strip()
        if wanted in names:
            return wanted
        for n in names:
            if n.split(":")[0].lower() == wanted.split(":")[0].lower() and (wanted.split(":")[-1].lower() in n.lower()):
                self.logger.log(f"Modello richiesto {wanted} trovato come {n}")
                self.cfg.ollama_model = n
                return n
        if names:
            raise RuntimeError(f"Modello Ollama '{wanted}' non trovato. Installati: {', '.join(names[:12])}")
        raise RuntimeError("Ollama è attivo ma non risultano modelli installati")

    def detect_vision_model(self) -> str:
        if self.cfg.vision_model:
            try:
                if any(m.get("name") == self.cfg.vision_model for m in self.tags()):
                    return self.cfg.vision_model
            except Exception:
                pass
        tags = self.tags()
        preferred = []
        for m in tags:
            name = m.get("name", "")
            low = name.lower()
            if any(k in low for k in ("qwen3-vl", "qwen2.5vl", "llava", "gemma3", "minicpm-v", "vision")):
                preferred.append(name)
        if preferred:
            self.cfg.vision_model = preferred[0]
            return preferred[0]
        for m in tags[:8]:
            n = m.get("name", "")
            try:
                data = self.http.request("POST", self.base + "/api/show", json={"model": n}, timeout=15, retries=1).json()
                caps = data.get("capabilities", []) or []
                if "vision" in caps:
                    self.cfg.vision_model = n
                    return n
            except Exception:
                continue
        return ""

    def chat(self, system: str, user: str, *, model: Optional[str] = None, json_mode: bool = False, temperature: float = 0.5, images: Optional[list[str]] = None) -> str:
        model = model or self.resolve_model()
        messages = [{"role": "system", "content": system}, {"role": "user", "content": user}]
        if images:
            messages[-1]["images"] = images
        payload: dict[str, Any] = {"model": model, "messages": messages, "stream": False, "options": {"temperature": temperature, "num_ctx": 32768}}
        if json_mode:
            payload["format"] = "json"
        last = None
        for attempt in range(1, MAX_TEXT_RETRIES + 1):
            try:
                r = self.http.request("POST", self.base + "/api/chat", json=payload, timeout=OLLAMA_TIMEOUT, retries=1)
                data = r.json()
                content = (data.get("message") or {}).get("content", "")
                if not content.strip():
                    raise RuntimeError("Ollama ha restituito una risposta vuota")
                return content
            except Exception as e:
                last = e
                txt = str(e).lower()
                if any(k in txt for k in ("out of memory", "cuda", "oom")):
                    raise RuntimeError("Ollama ha esaurito la memoria GPU/RAM. Chiudi altre applicazioni o riduci il modello.") from e
                if attempt < MAX_TEXT_RETRIES:
                    self.logger.log(f"Ollama retry {attempt}/{MAX_TEXT_RETRIES}: {e}")
                    time.sleep(2 * attempt)
        raise RuntimeError(f"Ollama non ha completato la richiesta: {last}")

    def chat_json(self, system: str, user: str, *, temperature: float = 0.4) -> Any:
        last = None
        for attempt in range(1, MAX_TEXT_RETRIES + 1):
            try:
                raw = self.chat(system, user, json_mode=True, temperature=temperature)
                return json_extract(raw)
            except Exception as e:
                last = e
                if attempt < MAX_TEXT_RETRIES:
                    user = user + "\n\nATTENZIONE: la risposta precedente non era JSON valido. Restituisci SOLO JSON valido, senza markdown."
        raise RuntimeError(f"Impossibile ottenere JSON valido da Ollama: {last}")


class ComfyClient:
    def __init__(self, cfg: Config, http: HTTPClient, logger: AppLogger):
        self.cfg, self.http, self.logger = cfg, http, logger

    @property
    def base(self) -> str:
        return self.cfg.comfy_url.rstrip("/")

    def is_up(self) -> bool:
        for path in ("/system_stats", "/object_info"):
            try:
                self.http.request("GET", self.base + path, timeout=5, retries=1); return True
            except Exception: continue
        return False

    def _discover_dirs(self) -> list[Path]:
        home = Path.home()
        roots = [Path(self.cfg.comfy_dir) if self.cfg.comfy_dir else None, home / "ComfyUI_windows_portable", home / "ComfyUI", home / "Documents" / "ComfyUI", Path("C:/ComfyUI_windows_portable"), Path("C:/ComfyUI"), Path("C:/AI/ComfyUI"), Path("D:/ComfyUI_windows_portable"), Path("D:/ComfyUI"), Path("D:/AI/ComfyUI")]
        return [p for p in roots if p and p.exists()]

    def try_start(self) -> bool:
        if self.is_up(): return True
        if not self.cfg.auto_start_services: return False
        flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
        for root in self._discover_dirs():
            try:
                bat = root / "run_nvidia_gpu.bat"
                if bat.exists(): subprocess.Popen(["cmd", "/c", str(bat)], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
                elif (root / "python_embeded" / "python.exe").exists() and (root / "ComfyUI" / "main.py").exists(): subprocess.Popen([str(root / "python_embeded" / "python.exe"), "-s", str(root / "ComfyUI" / "main.py"), "--windows-standalone-build"], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
                elif (root / "main.py").exists():
                    py = shutil.which("python") or shutil.which("python.exe")
                    if not py: continue
                    subprocess.Popen([py, str(root / "main.py")], cwd=root, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, creationflags=flags)
                else: continue
                self.cfg.comfy_dir = str(root)
                self.logger.log(f"Tentativo avvio ComfyUI da {root}")
                for _ in range(120):
                    time.sleep(1)
                    if self.is_up(): self.logger.log("ComfyUI avviato automaticamente"); return True
            except Exception as e: self.logger.log(f"Avvio ComfyUI da {root} fallito: {e}")
        return False

    def checkpoints(self) -> list[str]:
        data = self.http.request("GET", self.base + "/object_info/CheckpointLoaderSimple", timeout=12, retries=2).json()
        try: return list(data["CheckpointLoaderSimple"]["input"]["required"]["ckpt_name"][0])
        except Exception: return []

    def resolve_checkpoint(self) -> str:
        cps = self.checkpoints()
        if not cps: raise RuntimeError("ComfyUI è attivo ma non trova checkpoint. Installa/seleziona un modello SDXL/SD1.5 compatibile.")
        if self.cfg.checkpoint in cps: return self.cfg.checkpoint
        scoring=[]
        for cp in cps:
            low=cp.lower(); score=0
            if "xl" in low or "sdxl" in low: score+=10
            if any(k in low for k in ("real","photo","juggernaut","dreamshaper")): score+=6
            if any(k in low for k in ("flux","unet","turbo")): score-=8
            scoring.append((score,cp))
        scoring.sort(reverse=True); chosen=scoring[0][1]; self.cfg.checkpoint=chosen; self.logger.log(f"Checkpoint selezionato automaticamente: {chosen}"); return chosen

    def workflow(self, prompt: str, negative: str, filename_prefix: str, *, width: int, height: int, seed: int, checkpoint: str) -> dict[str, Any]:
        return {
            "1":{"class_type":"CheckpointLoaderSimple","inputs":{"ckpt_name":checkpoint}},
            "2":{"class_type":"CLIPTextEncode","inputs":{"text":prompt,"clip":["1",1]}},
            "3":{"class_type":"CLIPTextEncode","inputs":{"text":negative,"clip":["1",1]}},
            "4":{"class_type":"EmptyLatentImage","inputs":{"width":width,"height":height,"batch_size":1}},
            "5":{"class_type":"KSampler","inputs":{"seed":seed,"steps":int(self.cfg.image_steps),"cfg":float(self.cfg.image_cfg),"sampler_name":"dpmpp_2m","scheduler":"karras","denoise":1.0,"model":["1",0],"positive":["2",0],"negative":["3",0],"latent_image":["4",0]}},
            "6":{"class_type":"VAEDecode","inputs":{"samples":["5",0],"vae":["1",2]}},
            "7":{"class_type":"SaveImage","inputs":{"filename_prefix":filename_prefix,"images":["6",0]}},
        }

    def generate(self, prompt: str, slug: str, target_dir: Path, attempt: int = 1) -> Path:
        checkpoint=self.resolve_checkpoint(); width,height=(DEFAULT_IMAGE_WIDTH,DEFAULT_IMAGE_HEIGHT) if attempt==1 else (FALLBACK_IMAGE_WIDTH,FALLBACK_IMAGE_HEIGHT); seed=random.randint(1,2**63-1)
        negative="visible text, letters, words, caption, watermark, logo, brand, collage, grid, split screen, multiple panels, infographic, dashboard, spreadsheet, website, UI, screenshot, poster, article card, frame, border, low resolution, deformed anatomy, duplicate subjects"
        pure="Photorealistic editorial photograph. One single continuous physical scene only. "+prompt.strip()+" Natural photographic perspective, realistic materials and proportions, documentary photography, no visible text. Landscape composition."
        wf=self.workflow(pure,negative,f"c24bf/{slug}",width=width,height=height,seed=seed,checkpoint=checkpoint)
        try: resp=self.http.request("POST",self.base+"/prompt",json={"prompt":wf,"client_id":uuid.uuid4().hex},timeout=20,retries=1).json()
        except Exception as e:
            if any(k in str(e).lower() for k in ("out of memory","cuda","oom")): raise RuntimeError("ComfyUI ha esaurito la VRAM. Il programma riproverà a risoluzione ridotta.") from e
            raise
        pid=resp.get("prompt_id")
        if not pid: raise RuntimeError(f"ComfyUI ha rifiutato il workflow: {resp.get('error') or resp}")
        deadline=time.time()+COMFY_IMAGE_TIMEOUT; hist=None
        while time.time()<deadline:
            time.sleep(1.5)
            try: h=self.http.request("GET",self.base+f"/history/{pid}",timeout=12,retries=1).json()
            except Exception: continue
            if pid in h:
                hist=h[pid]; status=hist.get("status",{})
                if status.get("completed") is True or hist.get("outputs"): break
                msgs=status.get("messages") or []
                if any("error" in str(m).lower() for m in msgs): raise RuntimeError(f"ComfyUI errore job: {msgs[-1] if msgs else status}")
        if not hist: raise TimeoutError(f"ComfyUI non ha completato l'immagine entro {COMFY_IMAGE_TIMEOUT}s")
        outputs=hist.get("outputs",{}); imgs=[]
        for node in outputs.values(): imgs.extend(node.get("images",[]) or [])
        if not imgs:
            status_text=json.dumps(hist.get("status",{}),ensure_ascii=False)[:1500]
            if any(k in status_text.lower() for k in ("out of memory","cuda","oom")): raise RuntimeError("ComfyUI OOM/CUDA durante la generazione")
            raise RuntimeError("ComfyUI ha completato il job ma non ha prodotto immagini")
        info=imgs[0]; params=urlencode({"filename":info.get("filename",""),"subfolder":info.get("subfolder",""),"type":info.get("type","output")}); raw=self.http.request("GET",self.base+"/view?"+params,timeout=60,retries=2).content
        target_dir.mkdir(parents=True,exist_ok=True); raw_path=target_dir/f"{slug}.source.png"; raw_path.write_bytes(raw)
        try:
            with Image.open(raw_path) as im: im.verify()
        except Exception as e: raw_path.unlink(missing_ok=True); raise RuntimeError("L'immagine restituita da ComfyUI è corrotta") from e
        return raw_path


@dataclass
class Candidate:
    id:int; title:str; url:str; source:str; published:str; description:str


class TrendCollector:
    def __init__(self,http:HTTPClient,logger:AppLogger): self.http,self.logger=http,logger
    def _parse_rss(self,xml:str,source_label:str,start_id:int)->list[Candidate]:
        out=[]; root=ET.fromstring(xml); ns={"ht":"https://trends.google.com/trending/rss"}
        for item in root.findall(".//item"):
            title=(item.findtext("title") or "").strip(); link=(item.findtext("link") or "").strip(); pub=(item.findtext("pubDate") or "").strip(); desc=strip_html(item.findtext("description") or "")
            news_urls=[n.text.strip() for n in item.findall("ht:news_item/ht:news_item_url",ns) if n.text]; news_titles=[n.text.strip() for n in item.findall("ht:news_item/ht:news_item_title",ns) if n.text]
            if news_urls:
                for j,nu in enumerate(news_urls[:3]): out.append(Candidate(start_id+len(out),news_titles[j] if j<len(news_titles) else title,nu,source_label,pub,desc))
            elif title and link: out.append(Candidate(start_id+len(out),title,link,source_label,pub,desc))
        return out
    def collect(self)->list[Candidate]:
        urls=[("Google Trends IT","https://trends.google.com/trending/rss?geo=IT"),("Google News IT","https://news.google.com/rss?hl=it&gl=IT&ceid=IT:it"),("Google News Tecnologia","https://news.google.com/rss/headlines/section/topic/TECHNOLOGY?hl=it&gl=IT&ceid=IT:it"),("Google News Scienza","https://news.google.com/rss/headlines/section/topic/SCIENCE?hl=it&gl=IT&ceid=IT:it"),("Google News Sport","https://news.google.com/rss/headlines/section/topic/SPORTS?hl=it&gl=IT&ceid=IT:it")]
        allc=[]; errors=[]
        for label,url in urls:
            try:
                r=self.http.request("GET",url,timeout=20,retries=2); got=self._parse_rss(r.text,label,len(allc)+1); allc.extend(got); self.logger.log(f"Trend source {label}: {len(got)} elementi")
            except Exception as e: errors.append(f"{label}: {e}"); self.logger.log(f"Trend source fallita {label}: {e}")
        uniq=[]; seen=set()
        for c in allc:
            k=normalize_for_similarity(c.title)
            if not k or k in seen or len(k)<8: continue
            seen.add(k); uniq.append(c)
        if len(uniq)<20: raise RuntimeError("Non sono riuscito a raccogliere abbastanza trend/notizie recenti. "+" | ".join(errors[:3]))
        random.shuffle(uniq)
        for i,c in enumerate(uniq,1): c.id=i
        return uniq[:120]
    def fetch_source_text(self,url:str)->str:
        try:
            r=self.http.request("GET",url,timeout=18,retries=2,allow_redirects=True); ct=r.headers.get("content-type","")
            if "html" not in ct and "text" not in ct: return ""
            soup=BeautifulSoup(r.text,"html.parser")
            for tag in soup(["script","style","nav","header","footer","aside","form"]): tag.decompose()
            text=" ".join(p.get_text(" ",strip=True) for p in soup.find_all(["p","h1","h2"])); return re.sub(r"\s+"," ",text)[:9000]
        except Exception as e: self.logger.log(f"Lettura fonte fallita {url}: {e}"); return ""


class SiteHistory:
    def __init__(self,http:HTTPClient,logger:AppLogger): self.http,self.logger=http,logger
    def load_local(self):
        if not HISTORY_PATH.exists(): return []
        try:return json.loads(HISTORY_PATH.read_text(encoding="utf-8"))
        except Exception:return []
    def save_posts(self,posts,batch_id):
        hist=self.load_local(); stamp=datetime.now(timezone.utc).isoformat(); hist.extend({"title":p["title"],"slug":p["slug"],"batch_id":batch_id,"at":stamp} for p in posts); HISTORY_PATH.write_text(json.dumps(hist[-500:],ensure_ascii=False,indent=2),encoding="utf-8")
    def site_slugs(self):
        try:
            xml=self.http.request("GET","https://complotto24.it/sitemap.xml",timeout=15,retries=2).text; root=ET.fromstring(xml); locs=[e.text.strip() for e in root.iter() if e.tag.endswith("loc") and e.text]; out=[]
            for loc in locs:
                path=urlparse(loc).path.strip("/")
                if path: out.append(path.split("/")[-1].replace("-"," "))
            return out[:1000]
        except Exception as e: self.logger.log(f"Sitemap sito non disponibile per dedupe: {e}"); return []
    def corpus(self):
        local=[x.get("title","") for x in self.load_local()]+[x.get("slug","").replace("-"," ") for x in self.load_local()]; return [x for x in local+self.site_slugs() if x]


class BatchEngine:
    def __init__(self,cfg:Config,logger:AppLogger,progress=None):
        self.cfg,self.logger,self.progress=cfg,logger,progress or (lambda n,m:None); self.http=HTTPClient(logger); self.ollama=OllamaClient(cfg,self.http,logger); self.comfy=ComfyClient(cfg,self.http,logger); self.trends=TrendCollector(self.http,logger); self.history=SiteHistory(self.http,logger); self.cancel_event=threading.Event(); self.vision_model=""
    def check_cancel(self):
        if self.cancel_event.is_set(): raise InterruptedError("Operazione annullata dall'utente")
    def _set_progress(self,pct,msg): self.logger.log(msg); self.progress(pct,msg)
    def precheck(self):
        ensure_dirs(); result={}; self._set_progress(1,"Precheck: cartella output"); out=Path(self.cfg.output_dir)
        try:
            out.mkdir(parents=True,exist_ok=True); probe=out/f".write-test-{uuid.uuid4().hex}"; probe.write_text("ok",encoding="ascii"); probe.unlink(); free=shutil.disk_usage(out).free/(1024**3)
            if free<1.5: raise RuntimeError(f"Spazio disco insufficiente: {free:.1f} GB liberi")
            result["output"]=f"OK ({free:.1f} GB liberi)"
        except Exception as e: result["output"]=f"FAIL: {e}"
        self._set_progress(3,"Precheck: connessione Internet")
        try:self.http.request("GET","https://trends.google.com/trending/rss?geo=IT",timeout=10,retries=1); result["internet"]="OK"
        except Exception:
            try:self.http.request("GET","https://news.google.com/rss?hl=it&gl=IT&ceid=IT:it",timeout=10,retries=1); result["internet"]="OK (fallback Google News)"
            except Exception as e2:result["internet"]=f"FAIL: {e2}"
        self._set_progress(5,"Precheck: Ollama")
        if not self.ollama.try_start(): result["ollama"]="FAIL: Ollama non raggiungibile"
        else:
            try:
                model=self.ollama.resolve_model(); result["ollama"]=f"OK ({model})"; self.vision_model=self.ollama.detect_vision_model(); result["vision"]=f"OK ({self.vision_model})" if self.vision_model else ("FAIL richiesto" if self.cfg.require_vision_qa else "opzionale: non trovato")
            except Exception as e:result["ollama"]=f"FAIL: {e}"
        self._set_progress(8,"Precheck: ComfyUI")
        if not self.comfy.try_start():result["comfyui"]="FAIL: ComfyUI non raggiungibile. Se è installato, indicane la cartella nelle impostazioni."
        else:
            try:cp=self.comfy.resolve_checkpoint(); result["comfyui"]=f"OK ({cp})"
            except Exception as e:result["comfyui"]=f"FAIL: {e}"
        self._set_progress(10,"Precheck completato"); failures=[k for k,v in result.items() if str(v).startswith("FAIL")]
        if failures: raise RuntimeError("Precheck non superato:\n"+"\n".join(f"- {k}: {result[k]}" for k in failures))
        self.cfg.save(); return result
    def _candidate_packet(self,candidates,limit=70):return "\n".join(f"[{c.id}] {c.title} | {c.source} | {c.published} | {c.url}" for c in candidates[:limit])
    def select_plans(self,candidates,corpus):
        system="Sei il caporedattore di un progetto italiano di media literacy. Seleziona temi recenti con potenziale di ricerca, ma evita danni, diffamazione e falsi allarmi. Restituisci SOLO JSON valido."
        avoid="\n".join(f"- {x[:180]}" for x in random.sample(corpus,min(len(corpus),80))) if corpus else "(nessuno)"
        user=f'''Scegli ESATTAMENTE 10 idee editoriali diverse dal pool recente qui sotto.
Non usare quote fisse per categorie o status: la combinazione deve risultare naturale e variata.
Gli status ammessi sono solo: real, fake, unverifiable.
REGOLE:
- Per status=real devi indicare 1-3 source_ids PRESI DAL POOL e coerenti con il fatto centrale.
- Per fake: costruisci una notizia plausibile ma INNOCUA; niente falsa morte, reato, malattia, emergenza, bonus/pagamento, banca o danno reputazionale riferito a persone/enti reali.
- Per unverifiable: usa solo un tema che possa essere raccontato con prudenza senza generare conseguenze concrete.
- Titoli SEO naturali e cliccabili, non trash.
- Evita storie troppo simili all'elenco storico.
- image_scene DEVE descrivere una SOLA scena fisica fotografabile, senza parole come articolo, notizia, SEO, reale, fake, dashboard, tabella o collage.
ELENCO STORICO DA EVITARE:\n{avoid}\nPOOL RECENTE:\n{self._candidate_packet(candidates)}
JSON richiesto: {{"plans":[{{"title":"...","status":"real|fake|unverifiable","source_ids":[1,2],"category":"...","category_slug":"...","image_scene":"...","angle":"..."}}]}}'''
        data=self.ollama.chat_json(system,user,temperature=0.7); plans=data.get("plans") if isinstance(data,dict) else None
        if not isinstance(plans,list) or len(plans)!=10:raise ValueError("Pianificazione: attesi 10 elementi")
        ids={c.id for c in candidates}; seen_titles=[]
        for i,p in enumerate(plans):
            if p.get("status") not in ("real","fake","unverifiable"):raise ValueError(f"Status non valido al piano {i+1}")
            p["source_ids"]=[int(x) for x in p.get("source_ids",[]) if str(x).isdigit() and int(x) in ids][:3]
            if p["status"]=="real" and not p["source_ids"]:raise ValueError(f"Piano reale {i+1} senza fonte dal pool")
            title=str(p.get("title","")).strip()
            if len(title)<20:raise ValueError(f"Titolo troppo corto al piano {i+1}")
            if any(similarity(title,old)>0.76 for old in corpus):raise ValueError(f"Titolo troppo simile a contenuto esistente: {title}")
            if any(similarity(title,t)>0.72 for t in seen_titles):raise ValueError(f"Due idee del batch sono troppo simili: {title}")
            if p["status"]=="fake" and any(b in title.lower() for b in SAFE_FAKE_BANS):raise ValueError(f"Fake potenzialmente dannosa scartata: {title}")
            scene=str(p.get("image_scene","")).strip()
            if len(scene)<20 or any(w in scene.lower() for w in ("dashboard","tabella","collage","articolo","seo")):raise ValueError(f"Scena immagine non isolata al piano {i+1}")
            seen_titles.append(title)
        return plans
    def _sources_for_plan(self,p,candidates):
        by_id={c.id:c for c in candidates}; out=[]
        for sid in p.get("source_ids",[]):
            c=by_id.get(int(sid))
            if not c:continue
            body=self.trends.fetch_source_text(c.url) if p.get("status")=="real" else ""; out.append({"id":c.id,"title":c.title,"url":c.url,"source":c.source,"published":c.published,"description":c.description[:1200],"body":body[:7000]})
        return out
    def generate_article(self,p,candidates,used_slugs):
        sources=self._sources_for_plan(p,candidates); safe_source=json.dumps(sources,ensure_ascii=False); system="Sei un giornalista italiano. Scrivi contenuti chiari, plausibili, SEO-friendly e adatti a un progetto di media literacy. Devi rispettare rigidamente la separazione tra testo pre-quiz e reveal. Restituisci SOLO JSON."
        user=f'''Crea UN articolo completo a partire da questo piano:\n{json.dumps(p,ensure_ascii=False)}\nFONTI DISPONIBILI (unica base fattuale se status=real):\n{safe_source}
REGOLE PRE-QUIZ: non usare mai fake, inventata/o, fittizia/o, simulazione, scenario inventato, non reale, non verificabile, Complotto24. Non suggerire che sia un test. 4-6 paragrafi HTML, tono giornalistico naturale, circa 450-750 parole. Non inventare dettagli nelle notizie reali non supportati dalle fonti. Per fake: storia innocua senza perdite economiche, allarmi sanitari, accuse, danni reputazionali o rischi concreti. image_prompt: pura scena fisica, una sola fotografia, nessun testo visibile.
Restituisci: {{"title":"...","slug":"...","excerpt":"...","content":"<p>...</p>...","reveal_explanation":"...","image_alt":"...","image_prompt":"...","categories":[["Nome","slug"]]}}'''
        for attempt in range(1,MAX_TEXT_RETRIES+1):
            obj=self.ollama.chat_json(system,user,temperature=0.55)
            if not isinstance(obj,dict):continue
            obj["title"]=str(obj.get("title") or p["title"]).strip(); slug=slugify(str(obj.get("slug") or obj["title"]));
            if slug in used_slugs:slug+="-"+uuid.uuid4().hex[:6]
            obj["slug"]=slug; obj["status"]=p["status"]; obj["editor"]=random.choice(EDITORS); obj["source_urls"]=[s["url"] for s in sources]; obj["image"]=slug+".jpg"
            if self.validate_article(obj):
                used_slugs.add(slug); obj["reveal"]=self.build_reveal(obj); obj.pop("source_urls",None); obj.pop("reveal_explanation",None); return obj
            user+="\nLa versione precedente ha fallito il QA. Rigenerala integralmente."
        raise RuntimeError(f"Articolo non valido dopo {MAX_TEXT_RETRIES} tentativi: {p['title']}")
    def validate_article(self,a):
        required=("title","slug","excerpt","content","image_alt","image_prompt","categories","status")
        if any(not a.get(k) for k in required):return False
        pre=" ".join(str(a.get(k,"")) for k in ("title","excerpt","content"))
        for pat in ANTI_SPOILER:
            if re.search(pat,pre,re.I):self.logger.log(f"Anti-spoiler FAIL '{pat}' su {a.get('title')}"); return False
        if len(strip_html(a["content"]).split())<250:self.logger.log(f"Articolo troppo corto: {a.get('title')}");return False
        if a["status"]=="real" and not a.get("source_urls"):return False
        if a["status"]=="fake" and any(b in strip_html(pre).lower() for b in SAFE_FAKE_BANS):self.logger.log(f"Safety fake FAIL: {a.get('title')}");return False
        cats=a.get("categories");
        if not isinstance(cats,list) or not cats:return False
        norm=[]
        for c in cats[:2]:
            if isinstance(c,list) and len(c)>=2:norm.append([str(c[0])[:60],slugify(str(c[1]))[:60]])
        if not norm:return False
        a["categories"]=norm; return True
    def build_reveal(self,a):
        explanation=html.escape(str(a.get("reveal_explanation","")).strip()); status=a["status"]
        if status=="real":
            links=[]
            for u in a.get("source_urls",[])[:3]:
                domain=urlparse(u).netloc.replace("www.","") or "fonte"; links.append(f'<a href="{html.escape(u,quote=True)}" target="_blank" rel="noopener noreferrer">{html.escape(domain)}</a>')
            return f"<p><strong>Esito: Reale.</strong> {explanation} Fonti: {'; '.join(links)}.</p>"
        if status=="fake":return f"<p><strong>Esito: Inventata.</strong> {explanation} Questo contenuto è una simulazione editoriale innocua usata per esercitare la verifica delle fonti.</p>"
        return f"<p><strong>Esito: Non verificabile.</strong> {explanation} Al momento non sono disponibili riscontri sufficienti per considerare la ricostruzione confermata.</p>"
    def _ahash(self,path):
        with Image.open(path) as im:
            im=im.convert("L").resize((16,16),Image.Resampling.LANCZOS);pix=list(im.getdata());avg=sum(pix)/len(pix);return ''.join('1' if p>avg else '0' for p in pix)
    @staticmethod
    def _hash_distance(a,b):return sum(x!=y for x,y in zip(a,b))
    def semantic_image_qa(self,path,expected_scene):
        if not self.vision_model:return True,"Vision QA non disponibile: usato QA tecnico + isolamento ComfyUI"
        b64=base64.b64encode(path.read_bytes()).decode("ascii"); system="Valuta una singola immagine editoriale. Restituisci SOLO JSON valido."; user=f'''Scena attesa: {expected_scene}\nControlla: una sola scena continua, non collage/griglia/split-screen/dashboard/UI, niente testo leggibile rilevante, coerenza. JSON: {{"pass":true|false,"reason":"..."}}'''
        try:
            raw=self.ollama.chat(system,user,model=self.vision_model,json_mode=True,temperature=0.1,images=[b64]);obj=json_extract(raw);return bool(obj.get("pass")),str(obj.get("reason",""))
        except Exception as e:
            if self.cfg.require_vision_qa:return False,f"Vision QA fallito: {e}"
            self.logger.log(f"Vision QA saltato per errore: {e}");return True,f"Vision QA non disponibile: {e}"
    def optimize_image(self,raw_path,out_path):
        out_path.parent.mkdir(parents=True,exist_ok=True)
        try:
            with Image.open(raw_path) as im:
                im=im.convert("RGB");stat=ImageStat.Stat(im.resize((128,128)))
                if sum(stat.var)<50:raise RuntimeError("Immagine quasi uniforme/anomala")
                im=ImageOps.fit(im,FINAL_IMAGE_SIZE,method=Image.Resampling.LANCZOS,centering=(0.5,0.5));quality=84
                while True:
                    im.save(out_path,"JPEG",quality=quality,optimize=True,progressive=True);kb=out_path.stat().st_size/1024
                    if kb<=450 or quality<=70:break
                    quality-=2
            with Image.open(out_path) as check:fmt,size=check.format,check.size;check.verify()
            if fmt!="JPEG" or size!=FINAL_IMAGE_SIZE:raise RuntimeError("Post-processing JPEG non conforme")
            return {"format":fmt,"size":list(size),"kb":round(kb,1),"quality":quality}
        except Exception:out_path.unlink(missing_ok=True);raise
    def generate_images(self,posts,work_images):
        hashes=[];stats=[]
        for idx,a in enumerate(posts,1):
            self.check_cancel();final_path=work_images/a["image"]
            if final_path.exists():
                try:
                    with Image.open(final_path) as im:
                        if im.format=="JPEG" and im.size==FINAL_IMAGE_SIZE:
                            h=self._ahash(final_path)
                            if all(self._hash_distance(h,old)>10 for old in hashes):hashes.append(h);stats.append({"file":a["image"],"resumed":True,"kb":round(final_path.stat().st_size/1024,1)});self._set_progress(55+idx*3,f"Immagine {idx}/10 recuperata dal resume");continue
                except Exception:final_path.unlink(missing_ok=True)
            last=None
            for attempt in range(1,MAX_IMAGE_RETRIES+1):
                self.check_cancel();self._set_progress(55+idx*3,f"Immagine {idx}/10 — tentativo {attempt}/{MAX_IMAGE_RETRIES}");raw=None
                try:
                    raw=self.comfy.generate(a["image_prompt"],a["slug"],work_images,attempt=attempt);ok,reason=self.semantic_image_qa(raw,a["image_prompt"]);self.logger.log(f"Vision QA immagine {idx}: {'PASS' if ok else 'FAIL'} — {reason}")
                    if not ok:raw.unlink(missing_ok=True);raise RuntimeError(f"QA immagine non superato: {reason}")
                    info=self.optimize_image(raw,final_path);h=self._ahash(final_path)
                    if any(self._hash_distance(h,old)<=10 for old in hashes):final_path.unlink(missing_ok=True);raise RuntimeError("Immagine troppo simile a una già generata nel batch")
                    hashes.append(h);raw.unlink(missing_ok=True);info["file"]=a["image"];info["attempt"]=attempt;stats.append(info);break
                except Exception as e:
                    last=e
                    if raw:raw.unlink(missing_ok=True)
                    final_path.unlink(missing_ok=True);txt=str(e).lower()
                    if attempt<MAX_IMAGE_RETRIES:
                        self.logger.log(f"Retry immagine {idx}: {e}")
                        if any(k in txt for k in ("out of memory","cuda","oom")):self.cfg.image_steps=max(16,int(self.cfg.image_steps)-4)
                        time.sleep(2)
            if not final_path.exists():raise RuntimeError(f"Immagine {idx}/10 fallita dopo {MAX_IMAGE_RETRIES} tentativi: {last}")
        return stats
    def _save_state(self,state_path,payload):state_path.write_text(json.dumps(payload,ensure_ascii=False,indent=2),encoding="utf-8")
    def _load_latest_resume(self):
        states=sorted(STATE_DIR.glob("incomplete-*.json"),key=lambda p:p.stat().st_mtime,reverse=True);return states[0] if states else None
    def generate_batch(self,allow_resume=True):
        self.precheck();self.check_cancel();output_root=Path(self.cfg.output_dir);output_root.mkdir(parents=True,exist_ok=True);state_path=self._load_latest_resume() if allow_resume else None;state={}
        if state_path:
            try:
                state=json.loads(state_path.read_text(encoding="utf-8"))
                if not Path(state.get("work_dir","")).exists():state={};state_path=None
            except Exception:state={};state_path=None
        if not state:
            batch_token=datetime.now().strftime("%Y%m%d")+"-"+uuid.uuid4().hex[:8];work=STATE_DIR/("work-"+batch_token);work.mkdir(parents=True,exist_ok=True);state_path=STATE_DIR/f"incomplete-{batch_token}.json";state={"version":APP_VERSION,"batch_token":batch_token,"work_dir":str(work),"stage":"start"};self._save_state(state_path,state)
        else:work=Path(state["work_dir"]);self.logger.log(f"Ripresa batch incompleto: {state.get('batch_token')}")
        work_images=work/"images";work_images.mkdir(exist_ok=True)
        try:
            candidates=[Candidate(**x) for x in state.get("candidates",[])]
            if not candidates:self._set_progress(12,"Raccolta trend/notizie recenti");candidates=self.trends.collect();state["candidates"]=[asdict(x) for x in candidates];state["stage"]="trends";self._save_state(state_path,state)
            corpus=self.history.corpus();plans=state.get("plans") or []
            if not plans:
                self._set_progress(20,"Selezione dei 10 temi con Qwen locale");last=None
                for attempt in range(1,MAX_TEXT_RETRIES+1):
                    try:plans=self.select_plans(candidates,corpus);break
                    except Exception as e:last=e;self.logger.log(f"Retry pianificazione {attempt}: {e}")
                if not plans:raise RuntimeError(f"Pianificazione fallita: {last}")
                state["plans"]=plans;state["stage"]="plans";self._save_state(state_path,state)
            posts=state.get("posts") or [];used_slugs={p["slug"] for p in posts if p.get("slug")}
            if len(posts)<10:
                for i in range(len(posts),10):self.check_cancel();self._set_progress(28+i*2,f"Articolo {i+1}/10 — generazione locale");a=self.generate_article(plans[i],candidates,used_slugs);posts.append(a);state["posts"]=posts;state["stage"]=f"article-{i+1}";self._save_state(state_path,state)
            self._set_progress(52,"QA editoriale completo")
            if len(posts)!=10 or len({p["slug"] for p in posts})!=10:raise RuntimeError("QA articoli: conteggio o slug univoci non conformi")
            for a in posts:
                pre=" ".join(str(a.get(k,"")) for k in ("title","excerpt","content"))
                if any(re.search(pat,pre,re.I) for pat in ANTI_SPOILER):raise RuntimeError(f"QA anti-spoiler fallito: {a['title']}")
            self._set_progress(55,"Generazione immagini isolate con ComfyUI");image_stats=self.generate_images(posts,work_images);state["image_stats"]=image_stats;state["stage"]="images";self._save_state(state_path,state)
            batch_id=f"complotto24-batch-{datetime.now().strftime('%Y%m%d')}-{uuid.uuid4().hex[:10]}";batch={"batch_id":batch_id,"posts":posts};json_path=work/"c24-batch.json";json_path.write_text(json.dumps(batch,ensure_ascii=False,indent=2),encoding="utf-8")
            self._set_progress(90,"Preflight finale e creazione ZIP")
            if len(list(work_images.glob("*.jpg")))!=10:raise RuntimeError("Preflight: non ci sono esattamente 10 JPEG finali")
            for p in posts:
                ip=work_images/p["image"]
                if not ip.exists():raise RuntimeError(f"Preflight: immagine mancante {p['image']}")
                with Image.open(ip) as im:
                    if im.format!="JPEG" or im.size!=FINAL_IMAGE_SIZE:raise RuntimeError(f"Preflight JPEG fallito: {p['image']}")
            zip_path=output_root/f"{batch_id}.zip"
            with zipfile.ZipFile(zip_path,"w",zipfile.ZIP_DEFLATED,compresslevel=9) as z:
                z.write(json_path,"c24-batch.json")
                for p in posts:z.write(work_images/p["image"],f"images/{p['image']}")
            with zipfile.ZipFile(zip_path,"r") as z:
                bad=z.testzip();names=z.namelist()
                if bad or len(names)!=11 or names.count("c24-batch.json")!=1:raise RuntimeError(f"ZIP integrity FAIL: {bad or names}")
            sha=sha256_file(zip_path);qa={"app_version":APP_VERSION,"batch_id":batch_id,"articles":10,"images":10,"anti_spoiler":"PASS","jpeg_real":"PASS","dimensions":"1600x900","zip_integrity":"PASS","zip_mb":round(zip_path.stat().st_size/1024/1024,2),"sha256":sha,"ollama_model":self.cfg.ollama_model,"vision_model":self.vision_model or None,"checkpoint":self.cfg.checkpoint,"image_stats":image_stats};qa_path=output_root/f"{batch_id}-QA.json";qa_path.write_text(json.dumps(qa,ensure_ascii=False,indent=2),encoding="utf-8");self.history.save_posts(posts,batch_id);state_path.unlink(missing_ok=True);shutil.rmtree(work,ignore_errors=True);self._set_progress(100,f"Batch completato: {zip_path.name}");return zip_path,qa
        except Exception:state["stage"]=state.get("stage","error");self._save_state(state_path,state);raise


class BatchFactoryGUI:
    def __init__(self):
        if tk is None:raise RuntimeError("Tkinter non disponibile")
        ensure_dirs();self.cfg=Config.load();self.root=tk.Tk();self.root.title(APP_NAME);self.root.geometry("860x700");self.root.minsize(760,620);self.q=queue.Queue();self.worker=None;self.engine=None;self.logger=AppLogger(lambda line:self.q.put(("log",line)));self._build();self.root.after(100,self._poll)
    def _build(self):
        main=ttk.Frame(self.root,padding=12);main.pack(fill="both",expand=True);ttk.Label(main,text="Complotto24 Batch Factory — LOCAL",font=("Segoe UI",18,"bold")).pack(anchor="w");ttk.Label(main,text="Ollama locale + ComfyUI locale · nessuna API key cloud",font=("Segoe UI",10)).pack(anchor="w",pady=(0,10));form=ttk.LabelFrame(main,text="Configurazione",padding=10);form.pack(fill="x")
        self.vars={"ollama_url":tk.StringVar(value=self.cfg.ollama_url),"ollama_model":tk.StringVar(value=self.cfg.ollama_model),"comfy_url":tk.StringVar(value=self.cfg.comfy_url),"comfy_dir":tk.StringVar(value=self.cfg.comfy_dir),"checkpoint":tk.StringVar(value=self.cfg.checkpoint),"output_dir":tk.StringVar(value=self.cfg.output_dir)}
        for r,(lab,key) in enumerate([("Ollama URL","ollama_url"),("Modello testo","ollama_model"),("ComfyUI URL","comfy_url")]):ttk.Label(form,text=lab).grid(row=r,column=0,sticky="w",padx=(0,8),pady=3);ttk.Entry(form,textvariable=self.vars[key]).grid(row=r,column=1,columnspan=2,sticky="ew",pady=3)
        ttk.Label(form,text="Cartella ComfyUI").grid(row=3,column=0,sticky="w",pady=3);ttk.Entry(form,textvariable=self.vars["comfy_dir"]).grid(row=3,column=1,sticky="ew",pady=3);ttk.Button(form,text="Sfoglia",command=self._browse_comfy).grid(row=3,column=2,padx=(6,0));ttk.Label(form,text="Checkpoint").grid(row=4,column=0,sticky="w",pady=3);self.cp_combo=ttk.Combobox(form,textvariable=self.vars["checkpoint"]);self.cp_combo.grid(row=4,column=1,columnspan=2,sticky="ew",pady=3);ttk.Label(form,text="Cartella output").grid(row=5,column=0,sticky="w",pady=3);ttk.Entry(form,textvariable=self.vars["output_dir"]).grid(row=5,column=1,sticky="ew",pady=3);ttk.Button(form,text="Sfoglia",command=self._browse_output).grid(row=5,column=2,padx=(6,0));form.columnconfigure(1,weight=1)
        opts=ttk.Frame(form);opts.grid(row=6,column=0,columnspan=3,sticky="w",pady=(8,0));self.auto_var=tk.BooleanVar(value=self.cfg.auto_start_services);self.vision_var=tk.BooleanVar(value=self.cfg.require_vision_qa);ttk.Checkbutton(opts,text="Avvia automaticamente Ollama/ComfyUI se possibile",variable=self.auto_var).pack(side="left");ttk.Checkbutton(opts,text="Richiedi QA vision locale",variable=self.vision_var).pack(side="left",padx=15)
        actions=ttk.Frame(main);actions.pack(fill="x",pady=10);self.pre_btn=ttk.Button(actions,text="PRECHECK",command=self.precheck);self.pre_btn.pack(side="left");self.gen_btn=ttk.Button(actions,text="GENERA BATCH",command=self.generate);self.gen_btn.pack(side="left",padx=8);self.cancel_btn=ttk.Button(actions,text="ANNULLA",command=self.cancel,state="disabled");self.cancel_btn.pack(side="left");ttk.Button(actions,text="Apri output",command=self.open_output).pack(side="right");ttk.Button(actions,text="Apri log",command=self.open_log).pack(side="right",padx=8)
        self.status=tk.StringVar(value="Pronto. Esegui PRECHECK prima del primo batch.");ttk.Label(main,textvariable=self.status,font=("Segoe UI",10,"bold")).pack(anchor="w");self.progress=ttk.Progressbar(main,maximum=100);self.progress.pack(fill="x",pady=(4,8));logframe=ttk.LabelFrame(main,text="Log",padding=6);logframe.pack(fill="both",expand=True);self.logtxt=tk.Text(logframe,height=18,wrap="word",font=("Consolas",9));self.logtxt.pack(fill="both",expand=True);self.logtxt.configure(state="disabled")
    def _sync_cfg(self):
        for k in ("ollama_url","ollama_model","comfy_url","comfy_dir","checkpoint","output_dir"):setattr(self.cfg,k,self.vars[k].get().strip())
        self.cfg.auto_start_services=bool(self.auto_var.get());self.cfg.require_vision_qa=bool(self.vision_var.get());self.cfg.save()
    def _browse_comfy(self):
        d=filedialog.askdirectory(title="Seleziona cartella ComfyUI")
        if d:self.vars["comfy_dir"].set(d)
    def _browse_output(self):
        d=filedialog.askdirectory(title="Seleziona cartella output")
        if d:self.vars["output_dir"].set(d)
    def open_output(self):self._sync_cfg();Path(self.cfg.output_dir).mkdir(parents=True,exist_ok=True);os.startfile(self.cfg.output_dir) if os.name=="nt" else webbrowser.open(Path(self.cfg.output_dir).as_uri())
    def open_log(self):os.startfile(str(self.logger.path)) if os.name=="nt" else webbrowser.open(self.logger.path.as_uri())
    def _append(self,line):self.logtxt.configure(state="normal");self.logtxt.insert("end",line+"\n");self.logtxt.see("end");self.logtxt.configure(state="disabled")
    def _busy(self,on):self.pre_btn.configure(state="disabled" if on else "normal");self.gen_btn.configure(state="disabled" if on else "normal");self.cancel_btn.configure(state="normal" if on else "disabled")
    def _progress_cb(self,pct,msg):self.q.put(("progress",pct,msg))
    def _run(self,fn,kind):
        if self.worker and self.worker.is_alive():return
        self._sync_cfg();self._busy(True);self.progress["value"]=0
        def work():
            try:self.engine=BatchEngine(self.cfg,self.logger,self._progress_cb);result=fn(self.engine);self.q.put(("done",kind,result))
            except InterruptedError as e:self.q.put(("error","Operazione annullata",str(e)))
            except Exception as e:self.logger.exception("Errore",e);self.q.put(("error","Generazione interrotta",str(e)))
        self.worker=threading.Thread(target=work,daemon=True);self.worker.start()
    def precheck(self):self._run(lambda e:e.precheck(),"precheck")
    def generate(self):self._run(lambda e:e.generate_batch(allow_resume=True),"batch")
    def cancel(self):
        if self.engine:self.engine.cancel_event.set();self.status.set("Annullamento richiesto…")
    def _poll(self):
        try:
            while True:
                item=self.q.get_nowait();typ=item[0]
                if typ=="log":self._append(item[1])
                elif typ=="progress":self.progress["value"]=item[1];self.status.set(item[2])
                elif typ=="done":
                    self._busy(False)
                    if item[1]=="precheck":
                        res=item[2];self.status.set("Precheck PASS")
                        try:cps=self.engine.comfy.checkpoints() if self.engine else [];self.cp_combo["values"]=cps;self.vars["checkpoint"].set(self.cfg.checkpoint) if self.cfg.checkpoint else None
                        except Exception:pass
                        messagebox.showinfo(APP_NAME,"PRECHECK SUPERATO\n\n"+"\n".join(f"{k}: {v}" for k,v in res.items()))
                    else:
                        zip_path,qa=item[2];self.status.set("Batch completato");messagebox.showinfo(APP_NAME,f"Batch completato.\n\n{zip_path}\n\nDimensione: {qa['zip_mb']} MB\nSHA-256: {qa['sha256']}")
                        try:os.startfile(str(zip_path.parent))
                        except Exception:pass
                elif typ=="error":self._busy(False);self.status.set(item[1]);messagebox.showerror(APP_NAME,f"{item[1]}:\n\n{item[2]}\n\nLog:\n{self.logger.path}")
        except queue.Empty:pass
        self.root.after(120,self._poll)
    def run(self):self.root.mainloop()


def self_test():
    assert slugify("Caffè & Pensioni 2026!")=="caffe-pensioni-2026";assert json_extract('{"a":1}')["a"]==1;assert json_extract('```json\n{"b":2}\n```')["b"]==2;assert similarity("Pensione agosto 2026 cedolino","Cedolino pensione agosto")>0.45
    tmp=APP_DIR/"selftest";tmp.mkdir(parents=True,exist_ok=True);src=tmp/"x.png";dst=tmp/"x.jpg";im=Image.new("RGB",(512,288));px=im.load()
    for y in range(288):
        for x in range(512):px[x,y]=((x*3)%255,(y*5)%255,((x+y)*2)%255)
    im.save(src);logger=AppLogger();cfg=Config();eng=BatchEngine(cfg,logger);info=eng.optimize_image(src,dst);assert info["format"]=="JPEG" and info["size"]==[1600,900];shutil.rmtree(tmp,ignore_errors=True);print("SELF-TEST PASS")


def main():
    ensure_dirs()
    if "--self-test" in sys.argv:self_test();return
    if tk is None:raise SystemExit("Tkinter non disponibile")
    BatchFactoryGUI().run()

if __name__=="__main__":main()
