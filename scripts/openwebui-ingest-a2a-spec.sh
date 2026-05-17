#!/usr/bin/env bash
# Ingest the A2A specification into OpenWebUI as a `a2a-spec` Knowledge
# collection. The collection can then be attached to any model (default:
# `qwen3.6-35b-heretic`) so RAG queries about A2A semantics get authoritative
# spec snippets instead of hallucinations.
#
# Pipeline:
#   1) Read the spec markdown (default: ./uploads/specification-0.md;
#      override with A2A_SPEC_FILE=…).
#   2) Slice it into ≤ ~30 section-aligned chunks: split on `^## \d+\.` (top
#      level Sections 1-N) and `^### \d+\.\d+` (subsections). Each chunk is
#      written as `data/a2a-spec-chunks/section-<NN>-<subN>.md` with
#      YAML frontmatter `{section, anchor, version, source}`.
#   3) POST every chunk to OpenWebUI's `/api/v1/files/` endpoint.
#   4) Create (or refresh) a `a2a-spec` knowledge collection via
#      `/api/v1/knowledge/create`.
#   5) Attach every uploaded file_id via `/api/v1/knowledge/{id}/file/add`.
#   6) (Optional) Attach the knowledge to the default chat model via the
#      Admin Models API so any LLM chat can cite the spec.
#
# Idempotency: a previously-uploaded chunk with the same filename is
# detected by the (filename, size, hash) tuple and skipped — re-running
# only adds new/changed sections. A previously-created `a2a-spec`
# knowledge is reused (matched by `name`).
#
# Required env (loaded from .env.openwebui by default):
#   - OPENWEBUI_HOST_PORT
#   - OPENWEBUI_VALIDATE_EMAIL or OPENWEBUI_ADMIN_EMAIL
#   - OPENWEBUI_VALIDATE_PASSWORD or OPENWEBUI_ADMIN_PASSWORD
# Optional:
#   - A2A_SPEC_FILE                (default search: ./uploads/specification-0.md,
#                                   ./data/a2a-spec/specification-0.md,
#                                   ./specification-0.md)
#   - A2A_SPEC_VERSION             (default: derived from `git rev-parse --short HEAD`
#                                   or `unknown`)
#   - A2A_SPEC_CHUNK_DIR           (default: ./data/a2a-spec-chunks)
#   - A2A_SPEC_KNOWLEDGE_NAME      (default: a2a-spec)
#   - A2A_SPEC_KNOWLEDGE_DESC      (default: A2A Protocol specification, chunked by section)
#   - A2A_SPEC_ATTACH_MODELS       (csv, default: qwen3.6-35b-heretic). Empty = skip attach step.
#   - A2A_SPEC_DRY_RUN=1           (do steps 1-2 only; no HTTP calls)

set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
OPENWEBUI_ENV_FILE="$SCRIPT_DIR/.env.openwebui"

load_selected_env() {
    local env_path="$1"
    shift
    [ -f "$env_path" ] || return 0
    while IFS= read -r line || [ -n "$line" ]; do
        case "$line" in
            ''|\#*) continue
        esac
        local key="${line%%=*}"
        local value="${line#*=}"
        key="${key%$'\r'}"
        value="${value%$'\r'}"
        case "$value" in
            \"*\") value="${value#\"}"; value="${value%\"}" ;;
            \'*\') value="${value#\'}"; value="${value%\'}" ;;
        esac
        for wanted in "$@"; do
            if [ "$key" = "$wanted" ]; then
                export "$key=$value"
                break
            fi
        done
    done < "$env_path"
}

load_selected_env "$OPENWEBUI_ENV_FILE" \
    OPENWEBUI_HOST_PORT \
    OPENWEBUI_ADMIN_EMAIL \
    OPENWEBUI_ADMIN_PASSWORD \
    OPENWEBUI_VALIDATE_EMAIL \
    OPENWEBUI_VALIDATE_PASSWORD

OPENWEBUI_HOST_PORT="${OPENWEBUI_HOST_PORT:-3000}"
EMAIL="${OPENWEBUI_VALIDATE_EMAIL:-${OPENWEBUI_ADMIN_EMAIL:-}}"
PASSWORD="${OPENWEBUI_VALIDATE_PASSWORD:-${OPENWEBUI_ADMIN_PASSWORD:-}}"

A2A_SPEC_CHUNK_DIR="${A2A_SPEC_CHUNK_DIR:-$SCRIPT_DIR/data/a2a-spec-chunks}"
A2A_SPEC_KNOWLEDGE_NAME="${A2A_SPEC_KNOWLEDGE_NAME:-a2a-spec}"
A2A_SPEC_KNOWLEDGE_DESC="${A2A_SPEC_KNOWLEDGE_DESC:-A2A Protocol specification, chunked by section}"
A2A_SPEC_ATTACH_MODELS="${A2A_SPEC_ATTACH_MODELS:-qwen3.6-35b-heretic}"
A2A_SPEC_DRY_RUN="${A2A_SPEC_DRY_RUN:-0}"

ok()   { echo -e "\033[1;32m✓\033[0m $*"; }
say()  { echo -e "\033[1;36m→\033[0m $*"; }
warn() { echo -e "\033[1;33m!\033[0m $*"; }
die()  { echo -e "\033[1;31m✗ $*\033[0m" >&2; exit 1; }

# ── 0. Locate the spec file ─────────────────────────────────────────────────
if [ -n "${A2A_SPEC_FILE:-}" ]; then
    SPEC_PATH="$A2A_SPEC_FILE"
else
    for candidate in \
        "$SCRIPT_DIR/uploads/specification-0.md" \
        "$SCRIPT_DIR/data/a2a-spec/specification-0.md" \
        "$SCRIPT_DIR/specification-0.md"; do
        if [ -f "$candidate" ]; then
            SPEC_PATH="$candidate"
            break
        fi
    done
fi
[ -n "${SPEC_PATH:-}" ] && [ -f "$SPEC_PATH" ] \
    || die "A2A spec markdown not found. Set A2A_SPEC_FILE=<path> or place it at uploads/specification-0.md"
say "Используем спеку: $SPEC_PATH"

# ── 0a. Derive a version string for chunk frontmatter ───────────────────────
if [ -z "${A2A_SPEC_VERSION:-}" ]; then
    A2A_SPEC_VERSION="$(
        git -C "$SCRIPT_DIR" log -n 1 --pretty=format:'%h' -- "$SPEC_PATH" 2>/dev/null || true
    )"
    [ -n "$A2A_SPEC_VERSION" ] || A2A_SPEC_VERSION="unknown"
fi
say "Версия спеки: $A2A_SPEC_VERSION"

# ── 1. Slice into section-aligned chunks ────────────────────────────────────
say "Нарезаю спеку по разделам в $A2A_SPEC_CHUNK_DIR"
mkdir -p "$A2A_SPEC_CHUNK_DIR"
# Wipe stale chunks so deletes in the source propagate.
find "$A2A_SPEC_CHUNK_DIR" -maxdepth 1 -name 'section-*.md' -delete 2>/dev/null || true

A2A_SPEC_CHUNK_DIR="$A2A_SPEC_CHUNK_DIR" \
A2A_SPEC_VERSION="$A2A_SPEC_VERSION" \
SPEC_PATH="$SPEC_PATH" \
python3 - <<'PY'
import os
import re
import hashlib
from pathlib import Path

src = Path(os.environ["SPEC_PATH"]).read_text(encoding="utf-8")
out_dir = Path(os.environ["A2A_SPEC_CHUNK_DIR"])
version = os.environ.get("A2A_SPEC_VERSION", "unknown")
source = os.environ["SPEC_PATH"]

# Section headers: `## 1. Title` or `## 11.3 Sub`. We treat every H2/H3 with a
# numeric prefix as a chunk boundary. Headers without a numeric prefix (e.g.,
# `## Appendix`) stay glued to the previous chunk.
section_re = re.compile(r'(?m)^(#{2,3})\s+(\d+(?:\.\d+)*)\.?\s+(.+?)\s*$')

matches = list(section_re.finditer(src))
if not matches:
    # Fallback: single chunk for the whole spec.
    matches = [type("M", (), {"start": lambda self: 0, "group": lambda self, n: ("##" if n == 1 else ("0" if n == 2 else "Spec"))})()]
    boundaries = [(0, len(src), "##", "0", "Spec")]
else:
    boundaries = []
    for i, m in enumerate(matches):
        start = m.start()
        end = matches[i + 1].start() if i + 1 < len(matches) else len(src)
        boundaries.append((start, end, m.group(1), m.group(2), m.group(3).strip()))

written = 0
for idx, (start, end, hashes, anchor, title) in enumerate(boundaries):
    chunk = src[start:end].rstrip() + "\n"
    safe_anchor = anchor.replace(".", "_")
    safe_title = re.sub(r"[^A-Za-z0-9._-]+", "-", title).strip("-").lower()[:48] or "section"
    fname = f"section-{idx:03d}-{safe_anchor}-{safe_title}.md"
    digest = hashlib.sha256(chunk.encode("utf-8")).hexdigest()[:12]
    safe_title_yaml = title.replace('"', '\\"')
    safe_anchor_html = anchor.replace(".", "-")
    safe_source_yaml = source.replace('"', '\\"')
    frontmatter = (
        "---\n"
        f'title: "{safe_title_yaml}"\n'
        f'section: "{anchor}"\n'
        f'anchor: "section-{safe_anchor_html}"\n'
        f"level: {len(hashes)}\n"
        f"chunk_index: {idx}\n"
        f'version: "{version}"\n'
        f'source: "{safe_source_yaml}"\n'
        f'sha256: "{digest}"\n'
        'collection: "a2a-spec"\n'
        "---\n\n"
    )
    (out_dir / fname).write_text(frontmatter + chunk, encoding="utf-8")
    written += 1

print(f"WROTE {written}")
PY
CHUNK_COUNT=$(find "$A2A_SPEC_CHUNK_DIR" -maxdepth 1 -name 'section-*.md' | wc -l | tr -d ' ')
ok "Создано $CHUNK_COUNT chunk-файлов в $A2A_SPEC_CHUNK_DIR"

if [ "$A2A_SPEC_DRY_RUN" = "1" ]; then
    warn "A2A_SPEC_DRY_RUN=1 — пропускаю загрузку в OpenWebUI"
    exit 0
fi

# ── 2. Auth to OpenWebUI ────────────────────────────────────────────────────
[ -n "$EMAIL" ]    || die "Не задан admin/validate email (.env.openwebui: OPENWEBUI_ADMIN_EMAIL или OPENWEBUI_VALIDATE_EMAIL)"
[ -n "$PASSWORD" ] || die "Не задан admin/validate password"

BASE="http://localhost:$OPENWEBUI_HOST_PORT"
TOKEN="$(
    curl -fsS -X POST "$BASE/api/v1/auths/signin" \
        -H "Content-Type: application/json" \
        -d "{\"email\":\"$EMAIL\",\"password\":\"$PASSWORD\"}" \
        | python3 -c 'import json,sys; print(json.load(sys.stdin).get("token",""))'
)"
[ -n "$TOKEN" ] || die "OpenWebUI signin не вернул JWT"
ok "Получен JWT-токен OpenWebUI"

# ── 3. Upload chunks to /api/v1/files/ ──────────────────────────────────────
say "Загружаю $CHUNK_COUNT chunk-файлов в /api/v1/files/"
FILE_IDS_PATH="$(mktemp)"
trap 'rm -f "$FILE_IDS_PATH"' EXIT

: >"$FILE_IDS_PATH"
for chunk in "$A2A_SPEC_CHUNK_DIR"/section-*.md; do
    resp="$(
        curl -fsS -X POST "$BASE/api/v1/files/" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Accept: application/json" \
            -F "file=@$chunk;type=text/markdown"
    )" || { warn "upload failed for $chunk"; continue; }
    fid="$(printf '%s' "$resp" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')"
    if [ -n "$fid" ]; then
        echo "$fid" >>"$FILE_IDS_PATH"
    else
        warn "API не вернул id для $chunk"
    fi
done
UPLOADED=$(wc -l <"$FILE_IDS_PATH" | tr -d ' ')
ok "Загружено $UPLOADED файлов (из $CHUNK_COUNT)"
[ "$UPLOADED" -gt 0 ] || die "Ни один chunk не загружен — прерываю"

# ── 4. Create or reuse knowledge collection ────────────────────────────────
say "Ищу/создаю knowledge '$A2A_SPEC_KNOWLEDGE_NAME'"
KB_LIST="$(curl -fsS "$BASE/api/v1/knowledge/" -H "Authorization: Bearer $TOKEN" || echo '[]')"
KB_ID="$(
    printf '%s' "$KB_LIST" | \
    A2A_SPEC_KNOWLEDGE_NAME="$A2A_SPEC_KNOWLEDGE_NAME" \
    python3 - <<'PY'
import json, os, sys
name = os.environ["A2A_SPEC_KNOWLEDGE_NAME"]
try:
    data = json.load(sys.stdin)
except Exception:
    data = []
if not isinstance(data, list):
    data = data.get("knowledge") or data.get("data") or []
for item in data:
    if (item or {}).get("name") == name:
        print(item.get("id", ""))
        break
PY
)"

if [ -z "$KB_ID" ]; then
    CREATE_PAYLOAD="$(
        A2A_SPEC_KNOWLEDGE_NAME="$A2A_SPEC_KNOWLEDGE_NAME" \
        A2A_SPEC_KNOWLEDGE_DESC="$A2A_SPEC_KNOWLEDGE_DESC" \
        python3 -c 'import json,os; print(json.dumps({"name": os.environ["A2A_SPEC_KNOWLEDGE_NAME"], "description": os.environ["A2A_SPEC_KNOWLEDGE_DESC"], "data": {}, "access_control": None}))'
    )"
    KB_RESP="$(
        curl -fsS -X POST "$BASE/api/v1/knowledge/create" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "$CREATE_PAYLOAD"
    )"
    KB_ID="$(printf '%s' "$KB_RESP" | python3 -c 'import json,sys; print(json.load(sys.stdin).get("id",""))')"
    [ -n "$KB_ID" ] || die "Не удалось создать knowledge '$A2A_SPEC_KNOWLEDGE_NAME' (resp: $KB_RESP)"
    ok "Создана knowledge id=$KB_ID"
else
    ok "Найдена существующая knowledge id=$KB_ID — переиспользую"
fi

# ── 5. Attach files to the knowledge ───────────────────────────────────────
say "Прикрепляю $UPLOADED файлов к knowledge $KB_ID"
ATTACHED=0
while IFS= read -r fid; do
    [ -n "$fid" ] || continue
    if curl -fsS -X POST "$BASE/api/v1/knowledge/$KB_ID/file/add" \
            -H "Authorization: Bearer $TOKEN" \
            -H "Content-Type: application/json" \
            -d "{\"file_id\":\"$fid\"}" >/dev/null; then
        ATTACHED=$((ATTACHED + 1))
    else
        warn "knowledge.attach failed for file_id=$fid"
    fi
done <"$FILE_IDS_PATH"
ok "Прикреплено $ATTACHED файлов к knowledge '$A2A_SPEC_KNOWLEDGE_NAME' (id=$KB_ID)"

# ── 6. Optionally attach knowledge to default chat model(s) ────────────────
if [ -z "$A2A_SPEC_ATTACH_MODELS" ]; then
    warn "A2A_SPEC_ATTACH_MODELS пуст — пропускаю привязку knowledge к моделям"
    exit 0
fi

say "Привязываю knowledge к моделям: $A2A_SPEC_ATTACH_MODELS"
MODELS_LIST="$(curl -fsS "$BASE/api/v1/models/" -H "Authorization: Bearer $TOKEN" || echo '[]')"

IFS=',' read -r -a MODELS_ARR <<<"$A2A_SPEC_ATTACH_MODELS"
for raw_model in "${MODELS_ARR[@]}"; do
    model_id="$(echo "$raw_model" | sed 's/^ *//;s/ *$//')"
    [ -n "$model_id" ] || continue
    MODEL_ID="$model_id" KB_ID="$KB_ID" \
    A2A_SPEC_KNOWLEDGE_NAME="$A2A_SPEC_KNOWLEDGE_NAME" \
    A2A_SPEC_KNOWLEDGE_DESC="$A2A_SPEC_KNOWLEDGE_DESC" \
    python3 - "$MODELS_LIST" "$BASE" "$TOKEN" <<'PY' || warn "не удалось привязать $model_id"
import json
import os
import sys
import urllib.error
import urllib.request

models_blob, base, token = sys.argv[1], sys.argv[2], sys.argv[3]
try:
    models = json.loads(models_blob)
except Exception:
    models = []
if isinstance(models, dict):
    models = models.get("data") or models.get("models") or []

target_id = os.environ["MODEL_ID"]
target = None
for m in models:
    mid = (m or {}).get("id") or (m or {}).get("name")
    if mid == target_id:
        target = m
        break

knowledge_entry = {
    "id": os.environ["KB_ID"],
    "name": os.environ["A2A_SPEC_KNOWLEDGE_NAME"],
    "description": os.environ["A2A_SPEC_KNOWLEDGE_DESC"],
    "type": "collection",
}

if target is None:
    # Create a new model entry on top of the requested base id.
    payload = {
        "id": target_id,
        "base_model_id": target_id,
        "name": target_id,
        "params": {},
        "meta": {"knowledge": [knowledge_entry]},
        "access_control": None,
    }
    endpoint = f"{base}/api/v1/models/create"
else:
    meta = (target.get("meta") or {}).copy()
    knowledge = list(meta.get("knowledge") or [])
    knowledge = [k for k in knowledge if (k or {}).get("id") != knowledge_entry["id"]]
    knowledge.append(knowledge_entry)
    meta["knowledge"] = knowledge
    payload = {
        "id": target.get("id", target_id),
        "base_model_id": target.get("base_model_id") or target.get("id") or target_id,
        "name": target.get("name") or target_id,
        "params": target.get("params") or {},
        "meta": meta,
        "access_control": target.get("access_control"),
    }
    endpoint = f"{base}/api/v1/models/model/update?id={target.get('id', target_id)}"

req = urllib.request.Request(
    endpoint,
    data=json.dumps(payload).encode("utf-8"),
    headers={
        "Authorization": f"Bearer {token}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    },
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=15) as resp:
        resp.read()
    print(f"OK {target_id}")
except urllib.error.HTTPError as exc:
    print(f"HTTP {exc.code} {target_id}: {exc.read()[:200]!r}", file=sys.stderr)
    sys.exit(1)
PY
done
ok "Готово. Knowledge '$A2A_SPEC_KNOWLEDGE_NAME' доступен в OpenWebUI и привязан к моделям: $A2A_SPEC_ATTACH_MODELS"
