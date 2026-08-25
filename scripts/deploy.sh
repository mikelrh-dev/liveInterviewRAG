#!/usr/bin/env bash
# Deploy pipeline for InterviewTTS candidate content (design §7).
#
# validate -> compile -> rsync to VPS -> restart service.
# Abort on first failure via set -euo pipefail; the pre-rsync server-side
# mv keeps exactly one rollback copy at candidate.prev/.
#
# Targets bash/systemd; run ON the VPS checkout or via SSH from WSL/Git-Bash.
# Requires SSH public-key auth and a sudo-capable user. No secrets here.

set -euo pipefail

VPS_HOST="${VPS_HOST:-your-vps-hostname}"
VPS_USER="${VPS_USER:-deploy}"
SSH_PORT="${SSH_PORT:-22}"
REMOTE_DIR="${REMOTE_DIR:-/opt/interviewtts}"

REPO_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON="${PYTHON:-python3}"

echo "==> [1/5] Validating wiki/ ..."
"$PYTHON" "$REPO_ROOT/scripts/wiki/validate.py" --wiki "$REPO_ROOT/wiki"

echo "==> [2/5] Compiling wiki/ -> candidate/ ..."
"$PYTHON" "$REPO_ROOT/scripts/wiki/compile.py" \
    --wiki "$REPO_ROOT/wiki" --out "$REPO_ROOT/candidate"

if [ ! -d "$REPO_ROOT/candidate" ]; then
    echo "ABORT: compile produced no candidate/ directory" >&2
    exit 1
fi

SSH_TARGET="$VPS_USER@$VPS_HOST"
ssh_cmd() { ssh -p "$SSH_PORT" "$SSH_TARGET" "$@"; }

echo "==> [3/5] Rotating stale backup; retaining live candidate/ as candidate.prev/ ..."
# Rotation happens BEFORE the mv: a candidate.prev left by the last SUCCESSFUL
# deploy is stale (service proven healthy), so drop it to make room. The mv
# then preserves the currently-live tree as the single rollback copy.
ssh_cmd "rm -rf $REMOTE_DIR/candidate.prev/"
if ssh_cmd "test -d $REMOTE_DIR/candidate/"; then
    ssh_cmd "mv $REMOTE_DIR/candidate/ $REMOTE_DIR/candidate.prev/"
fi

echo "==> [4/5] Rsyncing candidate/ to VPS ..."
rsync -az --delete -e "ssh -p $SSH_PORT" \
    "$REPO_ROOT/candidate/" "$SSH_TARGET:$REMOTE_DIR/candidate/"

echo "==> Replaced content:"
ssh_cmd "ls -la $REMOTE_DIR/candidate/ && echo 'docs files:' \$(ls $REMOTE_DIR/candidate/docs/ | wc -l)"

echo "==> [5/5] Restarting interviewtts.service ..."
ssh_cmd "sudo systemctl restart interviewtts.service"
ssh_cmd "systemctl is-active interviewtts.service"

echo ""
echo "DEPLOY OK — interviewtts.service restarted with fresh candidate/."
echo "Rollback if needed:"
echo "  ssh -p $SSH_PORT $SSH_TARGET 'mv $REMOTE_DIR/candidate $REMOTE_DIR/candidate.broken && mv $REMOTE_DIR/candidate.prev $REMOTE_DIR/candidate && sudo systemctl restart interviewtts.service'"
