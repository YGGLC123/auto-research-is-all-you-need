#!/usr/bin/env bash
# Run a hook script with the first working Python 3, so the same hooks.json works
# on macOS, Linux and Windows (Git Bash).
#
# Candidates are probed before use: on Windows `python3` is often the Microsoft
# Store stub, which exits without running anything, so it must fall through to a
# real interpreter (`python`, or the `py -3` launcher).
#
#   bash "${CLAUDE_PLUGIN_ROOT}/hooks/run-python.sh" "${CLAUDE_PLUGIN_ROOT}/scripts/<hook>.py" [args...]

for cmd in "python3" "python" "py -3"; do
    # Word splitting is intended: `py -3` is two words.
    # shellcheck disable=SC2086
    if [ "$($cmd -c 'import sys; print(sys.version_info[0])' 2>/dev/null)" = "3" ]; then
        # shellcheck disable=SC2086
        exec $cmd "$@"
    fi
done

echo "auto-research: no working Python 3 found (tried python3, python, py -3)." >&2
exit 1
