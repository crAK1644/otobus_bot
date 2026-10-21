# Sourced by backup.sh and restore-drill.sh (needs $root): load the root-only settings file.
env_file="${BACKUP_ENV_FILE:-$root/.env.backup}"
[ -f "$env_file" ] || { echo "$env_file not found" >&2; exit 1; }
# Sourced as root: group/other read leaks secrets, group/other write is code execution as root.
[ "$(ls -ld "$env_file" | cut -c5-10)" = "------" ] || { echo "$env_file must be mode 600" >&2; exit 1; }
set -a
. "$env_file"
set +a
need() { [ -n "${!1:-}" ] || { echo "$1 is not set in $env_file" >&2; return 1; }; }
