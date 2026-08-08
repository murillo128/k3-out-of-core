#!/usr/bin/env bash
set -u

readonly REPO="murillo128/k3-out-of-core"
readonly EVENT_TYPE="codex-turn-complete"
readonly MAX_SUMMARY_CHARS=320

usage() {
    cat <<'EOF'
Usage: scripts/codex-turn-notify.sh --issue <number> --outcome <outcome> --summary <text> [--dry-run]

Outcomes: complete, needs-input, blocked, checkpoint, progress
EOF
}

issue=""
outcome=""
summary=""
dry_run=0

while (( $# > 0 )); do
    case "$1" in
        --issue)
            (( $# >= 2 )) || { usage >&2; exit 2; }
            issue="$2"
            shift 2
            ;;
        --outcome)
            (( $# >= 2 )) || { usage >&2; exit 2; }
            outcome="$2"
            shift 2
            ;;
        --summary)
            (( $# >= 2 )) || { usage >&2; exit 2; }
            summary="$2"
            shift 2
            ;;
        --dry-run)
            dry_run=1
            shift
            ;;
        -h|--help)
            usage
            exit 0
            ;;
        *)
            echo "codex-turn-notify: unknown argument: $1" >&2
            usage >&2
            exit 2
            ;;
    esac
done

if [[ ! "$issue" =~ ^[1-9][0-9]*$ ]]; then
    echo "codex-turn-notify: --issue must be a positive integer" >&2
    exit 2
fi

case "$outcome" in
    complete|needs-input|blocked|checkpoint|progress) ;;
    *)
        echo "codex-turn-notify: invalid --outcome: $outcome" >&2
        exit 2
        ;;
esac

summary="${summary//$'\r'/ }"
summary="${summary//$'\n'/ }"
summary="${summary:0:MAX_SUMMARY_CHARS}"
if [[ -z "${summary//[[:space:]]/}" ]]; then
    echo "codex-turn-notify: --summary must contain non-whitespace text" >&2
    exit 2
fi

if (( dry_run )); then
    printf 'codex-turn-notify dry-run: issue=%s outcome=%s summary=%q\n' "$issue" "$outcome" "$summary"
    exit 0
fi

# Notification is deliberately best-effort. The executor's technical result and
# workflow state must never depend on this out-of-band convenience path.
if ! command -v gh >/dev/null 2>&1; then
    echo "codex-turn-notify: gh is unavailable; notification skipped" >&2
    exit 0
fi

if ! gh auth status --hostname github.com >/dev/null 2>&1; then
    echo "codex-turn-notify: gh is not authenticated; notification skipped" >&2
    exit 0
fi

if ! gh api \
    --hostname github.com \
    --method POST \
    "repos/${REPO}/dispatches" \
    --header "Accept: application/vnd.github+json" \
    --raw-field "event_type=${EVENT_TYPE}" \
    --raw-field "client_payload[issue]=${issue}" \
    --raw-field "client_payload[outcome]=${outcome}" \
    --raw-field "client_payload[summary]=${summary}" \
    --silent; then
    echo "codex-turn-notify: repository dispatch failed; notification skipped" >&2
fi

exit 0
