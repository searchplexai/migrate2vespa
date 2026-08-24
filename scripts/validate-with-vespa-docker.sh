#!/usr/bin/env bash
set -euo pipefail

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
VESPA_IMAGE="${VESPA_IMAGE:-vespaengine/vespa:8.618.24}"
HTTP_PORT="${VESPA_HTTP_PORT:-18080}"
CONFIG_PORT="${VESPA_CONFIG_PORT:-29071}"
CONTAINER_NAME="migrate2vespa-smoke-$$"
WORK_ROOT="$(mktemp -d "${TMPDIR:-/tmp}/migrate2vespa-smoke.XXXXXX")"

cleanup() {
    docker rm -f "$CONTAINER_NAME" >/dev/null 2>&1 || true
    rm -rf "$WORK_ROOT"
}
trap cleanup EXIT

for command in docker vespa python3 curl; do
    if ! command -v "$command" >/dev/null 2>&1; then
        echo "Missing required command: $command" >&2
        exit 2
    fi
done

cp -R "$REPO_ROOT/fixtures/quickstart" "$WORK_ROOT/quickstart"
rm -rf "$WORK_ROOT/quickstart/out"
PYTHONPATH="$REPO_ROOT/src" python3 -m migrate2vespa.cli "$WORK_ROOT/quickstart" >/dev/null

APP_DIR="$WORK_ROOT/quickstart/out/vespa-app"
FEED_FILE="$APP_DIR/feed/documents.jsonl"
CONFIG_URL="http://localhost:$CONFIG_PORT"
HTTP_URL="http://localhost:$HTTP_PORT"

docker run --rm --detach \
    --name "$CONTAINER_NAME" \
    --hostname vespa-container \
    --privileged \
    --publish "$HTTP_PORT:8080" \
    --publish "$CONFIG_PORT:19071" \
    "$VESPA_IMAGE" >/dev/null

echo "Waiting for Vespa configuration service..."
for _ in $(seq 1 180); do
    if curl --fail --silent "$CONFIG_URL/ApplicationStatus" >/dev/null 2>&1; then
        break
    fi
    sleep 1
done
curl --fail --silent "$CONFIG_URL/ApplicationStatus" >/dev/null

echo "Deploying generated application..."
vespa deploy --target "$CONFIG_URL" --wait 600 "$APP_DIR" >/dev/null

echo "Feeding generated documents..."
vespa feed --target "$HTTP_URL" --wait 300 "$FEED_FILE" >/dev/null

assert_source_id() {
    local query_output="$1"
    local expected="$2"
    python3 - "$query_output" "$expected" <<'PY'
import json
import sys

response = json.load(open(sys.argv[1], encoding="utf-8"))
children = response.get("root", {}).get("children", [])
actual = children[0].get("fields", {}).get("migrate2vespa_source_id") if children else None
if str(actual) != sys.argv[2]:
    raise SystemExit(f"Expected source ID {sys.argv[2]}, found {actual!r}")
PY
}

assert_has_hits() {
    local query_output="$1"
    python3 - "$query_output" <<'PY'
import json
import sys

response = json.load(open(sys.argv[1], encoding="utf-8"))
if not response.get("root", {}).get("children", []):
    raise SystemExit("Expected at least one Vespa hit")
PY
}

echo "Running retrieval smoke checks..."
vespa query --target "$HTTP_URL" --format plain \
    'yql=select * from quickstart where true' hits=1 >"$WORK_ROOT/select.json"
assert_has_hits "$WORK_ROOT/select.json"

vespa query --target "$HTTP_URL" --format plain \
    'yql=select * from quickstart where sku contains "PRODUCT-0001"' \
    ranking=unranked hits=1 >"$WORK_ROOT/exact.json"
assert_source_id "$WORK_ROOT/exact.json" "1"

vespa query --target "$HTTP_URL" --format plain \
    'yql=select * from quickstart where range(price, 21.32, 21.32)' \
    ranking=unranked hits=1 >"$WORK_ROOT/range.json"
assert_has_hits "$WORK_ROOT/range.json"

vespa query --target "$HTTP_URL" --format plain \
    'yql=select * from quickstart where title contains "northstar"' \
    ranking=default hits=1 >"$WORK_ROOT/text.json"
assert_has_hits "$WORK_ROOT/text.json"

VECTOR="$(python3 - "$FEED_FILE" <<'PY'
import json
import sys

first = json.loads(open(sys.argv[1], encoding="utf-8").readline())
print(json.dumps(first["fields"]["embedding"]["values"], separators=(",", ":")))
PY
)"
vespa query --target "$HTTP_URL" --format plain \
    'yql=select * from quickstart where {targetHits:5}nearestNeighbor(embedding, embedding_query)' \
    ranking=embedding_ann hits=1 "input.query(embedding_query)=$VECTOR" \
    >"$WORK_ROOT/ann.json"
assert_source_id "$WORK_ROOT/ann.json" "1"

echo "Vespa Docker smoke validation passed."
