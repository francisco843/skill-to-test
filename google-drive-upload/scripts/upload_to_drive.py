#!/usr/bin/env python3

import argparse
import json
import mimetypes
import os
import re
import sys
import uuid
from pathlib import Path
from typing import Dict, Iterable, Optional
from urllib import error, parse, request


DRIVE_API_BASE = "https://www.googleapis.com/drive/v3/files"
DRIVE_UPLOAD_URL = "https://www.googleapis.com/upload/drive/v3/files"
FOLDER_MIME = "application/vnd.google-apps.folder"
FOLDER_URL_RE = re.compile(r"/folders/([a-zA-Z0-9_-]+)")


class DriveClient:
    def __init__(self, access_token: str, dry_run: bool = False) -> None:
        self.access_token = access_token
        self.dry_run = dry_run

    def _headers(self, extra: Optional[Dict[str, str]] = None) -> Dict[str, str]:
        headers = {"Authorization": f"Bearer {self.access_token}"}
        if extra:
            headers.update(extra)
        return headers

    def _json_request(
        self,
        url: str,
        method: str = "GET",
        body: Optional[dict] = None,
        headers: Optional[Dict[str, str]] = None,
    ) -> dict:
        payload = None if body is None else json.dumps(body).encode("utf-8")
        merged_headers = self._headers(headers)
        if payload is not None:
            merged_headers["Content-Type"] = "application/json"
        req = request.Request(url, data=payload, headers=merged_headers, method=method)
        try:
            with request.urlopen(req) as resp:
                data = resp.read()
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Drive API error {exc.code}: {details}") from exc
        return json.loads(data.decode("utf-8")) if data else {}

    def find_child(self, parent_id: str, name: str, mime_type: Optional[str] = None) -> Optional[dict]:
        name_literal = name.replace("\\", "\\\\").replace("'", "\\'")
        clauses = [
            f"'{parent_id}' in parents",
            f"name = '{name_literal}'",
            "trashed = false",
        ]
        if mime_type:
            clauses.append(f"mimeType = '{mime_type}'")
        query = " and ".join(clauses)
        params = parse.urlencode({"q": query, "fields": "files(id,name,mimeType)", "pageSize": 1})
        url = f"{DRIVE_API_BASE}?{params}"
        data = self._json_request(url)
        files = data.get("files", [])
        return files[0] if files else None

    def create_folder(self, name: str, parent_id: str) -> str:
        if self.dry_run:
            folder_id = f"dryrun-folder-{uuid.uuid4().hex[:8]}"
            print(f"CREATE FOLDER {name!r} under {parent_id} -> {folder_id}")
            return folder_id

        body = {"name": name, "mimeType": FOLDER_MIME, "parents": [parent_id]}
        data = self._json_request(
            f"{DRIVE_API_BASE}?fields=id,name,webViewLink",
            method="POST",
            body=body,
        )
        folder_id = data["id"]
        print(f"CREATED FOLDER {name!r} -> {folder_id}")
        return folder_id

    def upload_file(self, path: Path, parent_id: str) -> str:
        mime_type = mimetypes.guess_type(path.name)[0] or "application/octet-stream"
        metadata = json.dumps({"name": path.name, "parents": [parent_id]}).encode("utf-8")
        file_bytes = path.read_bytes()
        boundary = f"===============codex-{uuid.uuid4().hex}"
        body = b"".join(
            [
                f"--{boundary}\r\n".encode("utf-8"),
                b"Content-Type: application/json; charset=UTF-8\r\n\r\n",
                metadata,
                b"\r\n",
                f"--{boundary}\r\n".encode("utf-8"),
                f"Content-Type: {mime_type}\r\n\r\n".encode("utf-8"),
                file_bytes,
                b"\r\n",
                f"--{boundary}--\r\n".encode("utf-8"),
            ]
        )

        if self.dry_run:
            file_id = f"dryrun-file-{uuid.uuid4().hex[:8]}"
            print(f"UPLOAD FILE {str(path)!r} to {parent_id} -> {file_id}")
            return file_id

        headers = {"Content-Type": f"multipart/related; boundary={boundary}"}
        req = request.Request(
            f"{DRIVE_UPLOAD_URL}?uploadType=multipart&fields=id,name,webViewLink",
            data=body,
            headers=self._headers(headers),
            method="POST",
        )
        try:
            with request.urlopen(req) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except error.HTTPError as exc:
            details = exc.read().decode("utf-8", errors="replace")
            raise RuntimeError(f"Drive upload error {exc.code}: {details}") from exc
        file_id = data["id"]
        print(f"UPLOADED FILE {str(path)!r} -> {file_id}")
        return file_id


def iter_sources(paths: Iterable[Path]) -> Iterable[Path]:
    for path in paths:
        if not path.exists():
            raise FileNotFoundError(f"Source path not found: {path}")
        yield path


def ensure_folder(
    client: DriveClient,
    name: str,
    parent_id: str,
    folder_cache: Dict[tuple[str, str], str],
    duplicate_behavior: str,
) -> str:
    cache_key = (parent_id, name)
    if cache_key in folder_cache:
        return folder_cache[cache_key]

    existing = None
    if duplicate_behavior in {"skip", "reuse"} and not client.dry_run:
        existing = client.find_child(parent_id, name, FOLDER_MIME)
    if existing:
        folder_cache[cache_key] = existing["id"]
        print(f"REUSE FOLDER {name!r} under {parent_id} -> {existing['id']}")
        return existing["id"]

    folder_id = client.create_folder(name, parent_id)
    folder_cache[cache_key] = folder_id
    return folder_id


def upload_tree(
    client: DriveClient,
    source: Path,
    parent_id: str,
    folder_cache: Dict[tuple[str, str], str],
    duplicate_behavior: str,
) -> None:
    target_root = ensure_folder(client, source.name, parent_id, folder_cache, duplicate_behavior)
    for child in sorted(source.iterdir(), key=lambda p: (p.is_file(), p.name.lower())):
        if child.is_dir():
            upload_tree(client, child, target_root, folder_cache, duplicate_behavior)
            continue
        if duplicate_behavior == "skip" and not client.dry_run:
            existing = client.find_child(target_root, child.name)
            if existing:
                print(f"SKIP FILE {str(child)!r}; already exists as {existing['id']}")
                continue
        client.upload_file(child, target_root)


def upload_sources(
    client: DriveClient,
    sources: Iterable[Path],
    parent_id: str,
    duplicate_behavior: str,
) -> None:
    folder_cache: Dict[tuple[str, str], str] = {}
    for source in iter_sources(sources):
        if source.is_dir():
            upload_tree(client, source, parent_id, folder_cache, duplicate_behavior)
        else:
            if duplicate_behavior == "skip" and not client.dry_run:
                existing = client.find_child(parent_id, source.name)
                if existing:
                    print(f"SKIP FILE {str(source)!r}; already exists as {existing['id']}")
                    continue
            client.upload_file(source, parent_id)


def parse_args(argv: list[str]) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Upload local files and folders to Google Drive.")
    parser.add_argument("sources", nargs="+", help="Local files or folders to upload.")
    parser.add_argument("--parent-id", default="root", help="Drive parent folder ID. Defaults to root.")
    parser.add_argument("--parent-url", help="Drive folder URL. If provided, overrides --parent-id.")
    parser.add_argument("--access-token", help="Drive API bearer token. Overrides GOOGLE_DRIVE_ACCESS_TOKEN.")
    parser.add_argument("--token-file", help="Path to a file containing the Drive API bearer token.")
    parser.add_argument(
        "--duplicate-behavior",
        choices=["duplicate", "skip", "reuse"],
        default="duplicate",
        help="How to handle name collisions. 'reuse' only applies to folders.",
    )
    parser.add_argument("--dry-run", action="store_true", help="Print the planned Drive operations without network calls.")
    return parser.parse_args(argv)


def resolve_parent_id(args: argparse.Namespace) -> str:
    if not args.parent_url:
        return args.parent_id
    match = FOLDER_URL_RE.search(args.parent_url)
    if not match:
        raise ValueError(f"Could not extract a Drive folder ID from URL: {args.parent_url}")
    return match.group(1)


def resolve_access_token(args: argparse.Namespace) -> str:
    if args.access_token:
        return args.access_token.strip()
    if args.token_file:
        return Path(args.token_file).expanduser().read_text(encoding="utf-8").strip()
    return (os.environ.get("GOOGLE_DRIVE_ACCESS_TOKEN") or "").strip()


def main(argv: list[str]) -> int:
    args = parse_args(argv)
    access_token = "" if args.dry_run else resolve_access_token(args)
    if not args.dry_run and not access_token:
        print(
            "A Drive API bearer token is required for non-dry-run uploads. "
            "Provide --access-token, --token-file, or GOOGLE_DRIVE_ACCESS_TOKEN.",
            file=sys.stderr,
        )
        return 2

    sources = [Path(item).expanduser().resolve() for item in args.sources]
    try:
        parent_id = resolve_parent_id(args)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 2
    client = DriveClient(access_token=access_token, dry_run=args.dry_run)
    try:
        upload_sources(client, sources, parent_id, args.duplicate_behavior)
    except Exception as exc:
        print(str(exc), file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
