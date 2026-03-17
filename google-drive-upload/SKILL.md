---
name: google-drive-upload
description: Self-contained Google Drive upload skill. Upload local files or entire local folders to Google Drive without browser automation, using only the files shipped in this skill folder plus a Drive API bearer token.
---

# Google Drive Upload

## Overview

Upload local files and folders to Google Drive without browser automation and without relying on any other installed skill.
This skill is self-contained: all execution logic lives in `scripts/upload_to_drive.py`, which uses only the Python standard library.
The only external prerequisite for real uploads is a valid Google Drive API bearer token supplied by the user.
Do not assume the presence of Atlas, MCP connectors, or any companion skill when following this workflow on another computer.

## Workflow

1. Resolve the upload inputs:
- Source files or folders to upload
- Destination Drive folder ID, Drive folder URL, or `root`
- Whether duplicate names should be duplicated or skipped
- Whether full folder structure should be preserved

2. Check local access first:
- If the requested files are already inside the writable workspace, use them directly.
- If the files are elsewhere on disk, request directory access first.
- If a source path does not exist, stop and ask for the correct path.
- Treat a folder as a recursive upload by default.

3. Resolve the Drive destination:
- If the user gives a Drive folder ID, use it directly.
- If the user gives a Drive folder URL, pass it through `--parent-url` and let the script extract the folder ID.
- If the user gives no destination, default to `root`.

4. Run the local upload script:
- Script path: `scripts/upload_to_drive.py`
- Real uploads require a Drive API bearer token supplied by one of these inputs:
  - `--access-token`
  - `--token-file`
  - `GOOGLE_DRIVE_ACCESS_TOKEN`
- Treat bearer tokens as transient secrets. Pass them only at execution time and do not write them into the skill files, examples, or checked-in configuration.
- Use `--parent-id` to target the destination folder by ID.
- Use `--parent-url` to target the destination folder by Drive URL.
- Use `--duplicate-behavior duplicate` unless the user asked to skip existing names.
- Pass files directly as positional arguments.
- Pass folders directly as positional arguments and let the script upload them recursively.
- The script preserves relative structure by creating the source folder in Drive and then recreating nested folders below it.

5. Validate when useful:
- For changes to the skill or for safe verification, run the script with `--dry-run` first.
- If a real token is available and the user asked for execution, run the real upload after the dry run succeeds.
- Report which files and folders were created or skipped.

## Decision Rules

- Use only this skill's own files to complete the upload workflow.
- Do not assume Atlas, MCP connectors, or other skills are installed.
- If no Drive token is available, stop and say that browserless upload is not currently possible with the available credentials.
- Preserve the full recursive folder structure unless the user explicitly asks to flatten it.
- Hidden files are treated like any other file unless the user asks to exclude them.

## Commands

Dry run:

```bash
python3 scripts/upload_to_drive.py --dry-run --parent-id root '/path/to/folder'
```

Real upload:

```bash
python3 scripts/upload_to_drive.py --access-token 'ya29...' --parent-id root '/path/to/folder'
```

Real upload with token file:

```bash
python3 scripts/upload_to_drive.py --token-file '/path/to/token.txt' --parent-url 'https://drive.google.com/drive/folders/ABC123' '/path/to/folder'
```

Skip duplicates:

```bash
python3 scripts/upload_to_drive.py --token-file '/path/to/token.txt' --parent-id root --duplicate-behavior skip '/path/to/file' '/path/to/folder'
```

## Clarifications To Ask

Ask only when required:
- Which local files or folders should be uploaded?
- Which Drive folder should receive them?
- Should duplicates be skipped or duplicated?

Do not ask whether Atlas, a connector, or another skill should be used.

## Constraints

- This skill is intentionally standalone and should remain usable when copied to another computer.
- Real uploads need a valid Drive API bearer token.
- Do not persist user tokens into the skill folder.
- Without that token, this skill can only dry-run and validate the planned recursive upload operations.
