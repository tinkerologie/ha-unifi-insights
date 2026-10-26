#!/bin/bash
# Select one interpreter for every canonical Python validation command.
select_project_python() {
    local project_root="$1"
    local candidate
    if [[ -n "${VIRTUAL_ENV:-}" ]]; then
        candidate="$VIRTUAL_ENV/bin/python"
        if [[ ! -x "$candidate" ]]; then
            echo "Active virtual environment has no executable Python: $VIRTUAL_ENV" >&2
            return 1
        fi
        PROJECT_PYTHON="$candidate"
    else
        PROJECT_PYTHON=""
        for candidate in \
            "$project_root/.local/ha-venv/bin/python" \
            "$project_root/.venv/bin/python" \
            "$HOME/.local/ha-venv/bin/python"; do
            if [[ -x "$candidate" ]]; then
                PROJECT_PYTHON="$candidate"
                break
            fi
        done
        if [[ -z "$PROJECT_PYTHON" ]]; then
            echo "Project environment unavailable. Run ./script/setup/bootstrap." >&2
            return 1
        fi
    fi
    "$PROJECT_PYTHON" -c 'import sys; assert sys.version_info >= (3, 14)' || return 1
    VIRTUAL_ENV="$(dirname "$(dirname "$PROJECT_PYTHON")")"
    export PROJECT_PYTHON VIRTUAL_ENV
    export PATH="$VIRTUAL_ENV/bin:$PATH"
}
