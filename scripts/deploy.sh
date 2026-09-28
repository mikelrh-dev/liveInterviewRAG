#!/usr/bin/env bash
# Deploy pipeline for InterviewTTS candidate content (design §7).
#
# validate -> compile -> rsync to VPS -> restart service.
# Abort on first failure via set -euo pipefail; the pre-rsync server-side
# mv keeps exactly one rollback copy at candidate.prev/.
#
# Usage:
#   scripts/deploy.sh            deploy (the default)
#   scripts/deploy.sh rollback   restore candidate.prev/ over the live tree
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

SSH_TARGET="$VPS_USER@$VPS_HOST"
ssh_cmd() { ssh -p "$SSH_PORT" "$SSH_TARGET" "$@"; }

# Restore the previous deploy over the live one.
#
# THE ORDER OF THE CHECKS IS THE WHOLE POINT. The procedure this replaces was
#
#     mv candidate candidate.broken && mv candidate.prev candidate
#
# run as one remote command, with no check that candidate.prev existed. If it
# did not, the FIRST mv still succeeds -- it only needs candidate/ -- and the
# second then fails. The result is a live tree that has been renamed out of the
# way, a candidate/ that no longer exists, and a rollback that has destroyed the
# content it was invoked to restore, while reporting failure. The window
# between the two mvs is tiny and the guard is not a race to be lucky about: a
# deploy that aborted before rsync, or one whose rotation was interrupted,
# leaves no candidate.prev/ at all, and that is the case a reader reaches for
# this command in precisely when they can least afford to lose the live tree.
#
# So: a read-only pre-check, as its own round trip, before anything moves.
rollback() {
    local live="$REMOTE_DIR/candidate"
    local backup="$REMOTE_DIR/candidate.prev"

    if ! ssh_cmd "test -d '$live' && test -d '$backup'"; then
        echo "ABORT: rollback needs BOTH of these to exist:" >&2
        echo "         $live" >&2
        echo "         $backup" >&2
        echo "       Nothing was moved. Restore from a known-good source instead." >&2
        return 1
    fi

    # The swap itself still restores on failure rather than leaving the tree
    # half-moved. The pre-check removes the documented failure; this covers the
    # ones it cannot -- a full disk, a permission change, a concurrent deploy.
    # Either way the live content ends up at candidate/ again, and the operator
    # sees which of the two happened.
    if ! ssh_cmd "set -e
        rm -rf '$REMOTE_DIR/candidate.broken'
        mv '$live' '$REMOTE_DIR/candidate.broken'
        if ! mv '$backup' '$live'; then
            echo 'ROLLBACK FAILED mid-swap: putting the live tree back' >&2
            mv '$REMOTE_DIR/candidate.broken' '$live'
            exit 1
        fi"; then
        return 1
    fi

    # Restarted separately from the swap, and reported separately. A failed
    # restart after a successful swap is a different problem with a different
    # fix, and the operator is standing right there.
    if ! ssh_cmd "sudo systemctl restart interviewtts.service"; then
        echo "WARN: content rolled back but the service did not restart." >&2
        echo "      Start it by hand: sudo systemctl restart interviewtts.service" >&2
        return 1
    fi

    echo "DEPLOY ROLLED BACK — candidate.prev/ is live again."
}

deploy() {
    echo "==> [1/5] Validating wiki/ ..."
    "$PYTHON" "$REPO_ROOT/scripts/wiki/validate.py" --wiki "$REPO_ROOT/wiki"

    echo "==> [2/5] Compiling wiki/ -> candidate/ ..."
    "$PYTHON" "$REPO_ROOT/scripts/wiki/compile.py" \
        --wiki "$REPO_ROOT/wiki" --out "$REPO_ROOT/candidate"

    if [ ! -d "$REPO_ROOT/candidate" ]; then
        echo "ABORT: compile produced no candidate/ directory" >&2
        exit 1
    fi

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
    echo "Roll back with:"
    echo "  VPS_HOST=$VPS_HOST VPS_USER=$VPS_USER ./scripts/deploy.sh rollback"
}

main() {
    case "${1:-deploy}" in
        deploy) deploy ;;
        rollback) rollback ;;
        *)
            echo "usage: $0 [deploy|rollback]" >&2
            return 2
            ;;
    esac
}

# Sourced, not executed. The `rollback` function above is shell-level logic over
# a remote tree, and the only honest way to test it is to run it — a test that
# only greps this file for the word "test -d" proves that the word is here, not
# that a missing backup leaves the live tree intact. tests/test_deploy_rollback.py
# sources this file, points REMOTE_DIR at a tmp_path and replaces ssh_cmd with a
# local shell, which is what the guard is for.
if [ "${BASH_SOURCE[0]}" = "$0" ]; then
    main "$@"
fi
