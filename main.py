import json
import os
import threading
import time
import uuid
from datetime import datetime, timedelta
from pathlib import Path

from fastapi import BackgroundTasks, FastAPI, File, HTTPException, UploadFile
from fastapi.responses import FileResponse, HTMLResponse
from pydantic import BaseModel, Field

APP_HOST = "0.0.0.0"
APP_PORT = 8000
STORAGE_DIR = Path("storage")
METADATA_FILE = Path("file_metadata.json")
DEFAULT_EXPIRE_DAYS = 7
CLEANUP_INTERVAL_SECONDS = 10 * 60

app = FastAPI(title="Primex Secure Transfer")
_metadata_lock = threading.Lock()


class UploadResponse(BaseModel):
    file_id: str = Field(..., description="ID to download the file")
    download_url: str = Field(..., description="Direct download URL")
    view_url: str = Field(..., description="Landing page for the file")
    expires_at: datetime


def _load_metadata() -> dict:
    if not METADATA_FILE.exists():
        return {}
    with METADATA_FILE.open("r", encoding="utf-8") as f:
        return json.load(f)


def _save_metadata(data: dict) -> None:
    with METADATA_FILE.open("w", encoding="utf-8") as f:
        json.dump(data, f, indent=2, default=str)


def _cleanup_expired_files() -> None:
    while True:
        time.sleep(CLEANUP_INTERVAL_SECONDS)
        with _metadata_lock:
            metadata = _load_metadata()
            now = datetime.utcnow()
            updated = {}
            for file_id, info in metadata.items():
                expires_at = datetime.fromisoformat(info["expires_at"])
                file_path = Path(info["path"])
                if expires_at <= now or not file_path.exists():
                    try:
                        file_path.unlink(missing_ok=True)
                    except OSError:
                        pass
                    continue
                updated[file_id] = info
            _save_metadata(updated)


@app.on_event("startup")
def startup_event() -> None:
    STORAGE_DIR.mkdir(parents=True, exist_ok=True)
    if not METADATA_FILE.exists():
        _save_metadata({})
    threading.Thread(target=_cleanup_expired_files, daemon=True).start()


@app.post("/upload", response_model=UploadResponse)
async def upload_file(background_tasks: BackgroundTasks, file: UploadFile = File(...), expire_days: int = DEFAULT_EXPIRE_DAYS) -> UploadResponse:
    if expire_days <= 0:
        raise HTTPException(status_code=400, detail="expire_days must be positive")

    file_id = uuid.uuid4().hex
    file_name = f"{file_id}_{file.filename}"
    file_path = STORAGE_DIR / file_name

    # Stream the upload to disk to avoid buffering very large files in memory.
    with file_path.open("wb") as dest:
        while chunk := await file.read(1024 * 1024):
            dest.write(chunk)

    expires_at = datetime.utcnow() + timedelta(days=expire_days)
    with _metadata_lock:
        metadata = _load_metadata()
        metadata[file_id] = {"path": str(file_path), "expires_at": expires_at.isoformat()}
        _save_metadata(metadata)

    download_url = f"/download/{file_id}"
    view_url = f"/file/{file_id}"
    # Schedule a cleanup in case the file is already expired by the time the request completes.
    background_tasks.add_task(_remove_if_expired, file_id)

    return UploadResponse(file_id=file_id, download_url=download_url, view_url=view_url, expires_at=expires_at)


def _remove_if_expired(file_id: str) -> None:
    with _metadata_lock:
        metadata = _load_metadata()
        info = metadata.get(file_id)
        if not info:
            return
        expires_at = datetime.fromisoformat(info["expires_at"])
        if expires_at <= datetime.utcnow():
            try:
                Path(info["path"]).unlink(missing_ok=True)
            except OSError:
                pass
            metadata.pop(file_id, None)
            _save_metadata(metadata)


@app.get("/download/{file_id}")
def download_file(file_id: str):
    with _metadata_lock:
        metadata = _load_metadata()
        info = metadata.get(file_id)
        if not info:
            raise HTTPException(status_code=404, detail="File not found")

        expires_at = datetime.fromisoformat(info["expires_at"])
        if expires_at <= datetime.utcnow():
            Path(info["path"]).unlink(missing_ok=True)
            metadata.pop(file_id, None)
            _save_metadata(metadata)
            raise HTTPException(status_code=410, detail="File expired")

        file_path = Path(info["path"])
        if not file_path.exists():
            metadata.pop(file_id, None)
            _save_metadata(metadata)
            raise HTTPException(status_code=404, detail="File missing on disk")

    return FileResponse(path=file_path, filename=file_path.name.split("_", 1)[-1])


@app.get("/file/{file_id}", response_class=HTMLResponse)
def file_page(file_id: str):
    with _metadata_lock:
        metadata = _load_metadata()
        info = metadata.get(file_id)
        if not info:
            raise HTTPException(status_code=404, detail="File not found")

        expires_at = datetime.fromisoformat(info["expires_at"])
        if expires_at <= datetime.utcnow():
            Path(info["path"]).unlink(missing_ok=True)
            metadata.pop(file_id, None)
            _save_metadata(metadata)
            raise HTTPException(status_code=410, detail="File expired")

        file_path = Path(info["path"])
        if not file_path.exists():
            metadata.pop(file_id, None)
            _save_metadata(metadata)
            raise HTTPException(status_code=404, detail="File missing on disk")

    filename = file_path.name.split("_", 1)[-1]
    download_link = f"/download/{file_id}"
    expires_text = expires_at.strftime("%Y-%m-%d %H:%M UTC")
    return f"""
    <html>
      <head>
        <title>Download {filename}</title>
        <style>
          body {{ font-family: Arial, sans-serif; max-width: 600px; margin: 60px auto; padding: 0 20px; text-align: center; }}
          h1 {{ margin-bottom: 0.5rem; }}
          p {{ color: #444; }}
          a.button {{
            display: inline-block;
            margin-top: 20px;
            padding: 10px 18px;
            background: #2563eb;
            color: #fff;
            border-radius: 6px;
            text-decoration: none;
            font-weight: 600;
          }}
          a.button:hover {{ background: #1d4ed8; }}
        </style>
      </head>
      <body>
        <h1>{filename}</h1>
        <p>Expires at: {expires_text}</p>
        <a class="button" href="{download_link}">Download</a>
      </body>
    </html>
    """


@app.get("/", response_class=HTMLResponse)
def landing_page() -> str:
    return """
    <html>
      <head>
        <title>Primex Secure Transfer</title>
        <style>
          body { font-family: Arial, sans-serif; max-width: 700px; margin: 40px auto; padding: 0 20px; }
          h1 { margin-bottom: 0.3rem; }
          form { border: 1px solid #ddd; padding: 16px; border-radius: 8px; }
          label { display: block; margin: 8px 0 4px; }
          input[type="file"] { margin-bottom: 12px; }
          button { padding: 8px 16px; border: none; background: #2563eb; color: #fff; border-radius: 4px; cursor: pointer; }
          button:hover { background: #1d4ed8; }
          code { background: #f5f5f5; padding: 2px 4px; border-radius: 4px; }
          .tip { color: #555; font-size: 0.95rem; margin-top: 8px; }
        </style>
      </head>
      <body>
        <h1>Primex Secure Transfer</h1>
        <p>Upload a file and get a download link. Default expiry is 7 days.</p>
        <form id="upload-form">
          <label>File</label>
          <input type="file" name="file" required />
          <label>Expire days (optional, default 7)</label>
          <input type="number" name="expire_days" min="1" placeholder="7" />
          <br /><br />
          <button type="submit">Upload</button>
        </form>
        <div id="result" class="tip"></div>
        <script>
          const form = document.getElementById('upload-form');
          form.addEventListener('submit', async (e) => {
            e.preventDefault();
            const fileInput = form.querySelector('input[type="file"]');
            if (!fileInput.files.length) return;
            const data = new FormData(form);
            const res = await fetch('/upload', { method: 'POST', body: data });
            const out = document.getElementById('result');
            if (!res.ok) {
              const err = await res.json();
              out.textContent = 'Error: ' + (err.detail || res.statusText);
              return;
            }
            const json = await res.json();
            const viewLink = `${window.location.origin}${json.view_url}`;
            out.innerHTML = `File ID: <code>${json.file_id}</code><br/>Link: <a href="${viewLink}">${viewLink}</a><br/>Expires at: ${json.expires_at}`;
          });
        </script>
        <p class="tip">Direct API: POST <code>/upload</code> (form key <code>file</code>), GET <code>/file/&lt;file_id&gt;</code> for a landing page, GET <code>/download/&lt;file_id&gt;</code> to download.</p>
      </body>
    </html>
    """
