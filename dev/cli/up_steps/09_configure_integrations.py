"""Configure integrations (GitHub only — Stripe removed)."""

from shared import (
    SECRETS_FILE,
    Context,
    step_status,
)

NAME = "Configuring integrations"


def is_github_configured() -> bool:
    """Check if GitHub App is configured."""
    if SECRETS_FILE.exists():
        content = SECRETS_FILE.read_text()
        if "POLAR_GITHUB_CLIENT_ID=" in content:
            for line in content.split("\n"):
                if line.startswith("POLAR_GITHUB_CLIENT_ID="):
                    value = line.split("=", 1)[1].strip().strip("\"'")
                    return bool(value)
    return False


def run(ctx: Context) -> bool:
    """Stripe removed — only GitHub integration remains (configured separately)."""
    step_status(True, NAME, "GitHub configured via separate step; Stripe removed")
    return True
