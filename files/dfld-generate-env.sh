#!/bin/bash
#
# dfld-generate-env.sh
# Generate environment file from YAML configuration
#
# Usage: dfld-generate-env.sh <source_yml> <target_env> <owner:group>
#
# Exit codes:
#   0 - File was regenerated (reload required)
#   1 - File is up to date (no reload needed)
#   2 - Error occurred

set -euo pipefail

SOURCE="${1:-}"
TARGET="${2:-}"
OWNERSHIP="${3:-}"

# Validate arguments
if [ -z "$SOURCE" ] || [ -z "$TARGET" ] || [ -z "$OWNERSHIP" ]; then
    echo "ERROR: Missing required arguments" >&2
    echo "Usage: $0 <source_yml> <target_env> <owner:group>" >&2
    exit 2
fi

if [ ! -f "$SOURCE" ]; then
    echo "ERROR: Source file '$SOURCE' does not exist" >&2
    exit 2
fi

# Check if regeneration is needed
if [ ! -f "$TARGET" ] || [ "$SOURCE" -nt "$TARGET" ]; then
    echo "Regenerating $TARGET from $SOURCE..."
    
    # Create directory if it doesn't exist
    TARGET_DIR=$(dirname "$TARGET")
    mkdir -p "$TARGET_DIR"
    
    # Generate the environment file
    if ! yq -r '. | to_entries | .[] | "\(.key | ascii_upcase)=\(.value)"' "$SOURCE" > "$TARGET"; then
        echo "ERROR: Failed to generate environment file" >&2
        exit 2
    fi
    
    # Set ownership and permissions
    if ! chown "$OWNERSHIP" "$TARGET"; then
        echo "ERROR: Failed to set ownership on $TARGET" >&2
        exit 2
    fi
    
    if ! chmod 644 "$TARGET"; then
        echo "ERROR: Failed to set permissions on $TARGET" >&2
        exit 2
    fi
    
    echo "Successfully generated $TARGET"
    exit 0  # File was regenerated - reload required
else
    echo "$TARGET is up to date (not newer than $SOURCE)"
    exit 1  # File is current - no reload needed
fi
