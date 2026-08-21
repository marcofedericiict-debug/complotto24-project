# Complotto24 Batch Factory LOCAL v2

Windows desktop app for generating Complotto24 incremental import batches locally.

## Runtime
- Ollama on `http://127.0.0.1:11434`
- Default text model: `qwen3.8:27b`
- ComfyUI on `http://127.0.0.1:8188`
- NVIDIA GPU recommended

## Workflow
1. Precheck internet, disk, Ollama/model, ComfyUI/checkpoint.
2. Read fresh Italian trends/news through RSS with fallbacks.
3. Deduplicate against local history and `https://complotto24.it/sitemap.xml`.
4. Generate 10 plans/articles locally through Ollama with anti-spoiler and safe-simulation gates.
5. Generate each image as an independent ComfyUI job.
6. Optional local vision QA if an Ollama vision-capable model is detected.
7. Convert to actual progressive JPEG 1600x900, target <=450 KB.
8. Package only `c24-batch.json` + `images/`, test ZIP integrity, emit SHA-256/QA.

## Fault handling
- Retry/backoff on transient HTTP failures.
- Automatic Ollama/ComfyUI startup when discoverable.
- Clear diagnostics for missing Ollama model/checkpoint.
- ComfyUI OOM retry at lower resolution/steps.
- Malformed Ollama JSON retry.
- Anti-spoiler and unsafe fake-story regeneration.
- Duplicate slug/title/image checks.
- Corrupt image/JPEG validation.
- Disk/write permissions and ZIP integrity preflight.
- Persistent incomplete state allows restart/resume after failure.
- Detailed logs under `%LOCALAPPDATA%\Complotto24BatchFactory\logs`.

## Windows build verification
The GitHub Actions Windows pipeline performs Python syntax validation, the internal self-test, PyInstaller one-file GUI compilation, PE header validation, a second self-test executed from the compiled EXE, SHA-256 generation, and artifact publication. The build artifact is published only when every validation step passes.

## Output
Default: `%USERPROFILE%\Desktop\Complotto24\Builds`

The generated import ZIP contains exactly:
```
c24-batch.json
images/*.jpg
```
