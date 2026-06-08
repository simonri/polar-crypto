"""Start Docker infrastructure (PostgreSQL, Redis, Tinybird)."""

from shared import (
    SERVER_DIR,
    Context,
    console,
    run_command,
    step_spinner,
    step_status,
)

NAME = "Starting infrastructure"


def get_running_services() -> set[str]:
    """Get names of currently-running docker compose services."""
    result = run_command(
        ["docker", "compose", "ps", "--services", "--filter", "status=running"],
        cwd=SERVER_DIR,
        capture=True,
    )
    if result and result.returncode == 0 and result.stdout.strip():
        return set(result.stdout.strip().split("\n"))
    return set()


def run(ctx: Context) -> bool:
    """Start Docker containers."""
    required = {"db", "redis"}
    if not ctx.skip_tinybird:
        required.add("tinybird")

    running = get_running_services()
    all_running = required.issubset(running)

    if all_running and not ctx.clean:
        step_status(True, "Docker containers", "already running")
        return True

    compose_cmd = ["docker", "compose"]
    # Include tinybird profile by default; exclude it when skip_tinybird is set
    if not ctx.skip_tinybird:
        compose_cmd.extend(["--profile", "tinybird"])
    compose_cmd.extend(["up", "-d"])

    service_name = "PostgreSQL, Redis"
    if not ctx.skip_tinybird:
        service_name += ", Tinybird"

    with step_spinner(f"Starting {service_name}..."):
        result = run_command(
            compose_cmd,
            cwd=SERVER_DIR,
            capture=True,
        )

    if result and result.returncode == 0:
        new_running = get_running_services()
        services = sorted(new_running & required)
        step_status(True, "Docker containers", f"started ({', '.join(services)})" if services else "started")
        return True
    else:
        step_status(False, "Docker containers", "failed to start")
        if result and result.stderr:
            console.print(f"[dim]{result.stderr}[/dim]")
        if result and result.stdout:
            console.print(f"[dim]{result.stdout}[/dim]")
        return False
