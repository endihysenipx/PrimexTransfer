# Mini SwissTransfer-style uploader

A tiny FastAPI service for uploading a file, getting a shareable link, and downloading until expiry. Files are stored locally in `storage/` and metadata is tracked in `file_metadata.json`.

## Setup

```bash
python -m venv .venv
.venv\Scripts\activate  # on Windows
pip install -r requirements.txt
```

## Run

```bash
uvicorn main:app --host 0.0.0.0 --port 8000
```

## Use

- Upload: `POST /upload` with `multipart/form-data` key `file` (optional `expire_days`, default 7).
- Download: `GET /download/{file_id}` returns the file if not expired.

Files auto-clean every 10 minutes; each upload also checks its own expiry. Delete `storage/` or `file_metadata.json` to wipe the store.
