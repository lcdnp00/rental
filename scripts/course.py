#!/usr/bin/env python3
"""Portable commands used by students and by the course documentation."""

from __future__ import annotations

import argparse
import json
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
from urllib.error import URLError
from urllib.request import Request, urlopen


ROOT = Path(__file__).resolve().parents[1]
CAR_SERVICE = ROOT / "apps" / "car-rental-service"
PRICING_SERVICE = ROOT / "apps" / "pricing-service"


def run(command: list[str], *, cwd: Path = ROOT) -> None:
    print(f"+ {' '.join(command)}", flush=True)
    subprocess.run(command, cwd=cwd, check=True)


def gradle_command() -> list[str]:
    if os.name == "nt":
        return ["cmd", "/c", "gradlew.bat"]
    return ["./gradlew"]


def test_java() -> None:
    run([*gradle_command(), "clean", "test"], cwd=CAR_SERVICE)


def test_python() -> None:
    run([sys.executable, "-m", "pytest"], cwd=PRICING_SERVICE)


def test() -> None:
    test_java()
    test_python()


def lint_java() -> None:
    run([*gradle_command(), "checkstyleMain", "checkstyleTest"], cwd=CAR_SERVICE)


def lint_python() -> None:
    run([sys.executable, "-m", "ruff", "check", "app", "tests"], cwd=PRICING_SERVICE)
    run(
        [sys.executable, "-m", "ruff", "format", "--check", "app", "tests"],
        cwd=PRICING_SERVICE,
    )


def lint() -> None:
    lint_java()
    lint_python()

FULL_SHA = re.compile(r"^[0-9a-f]{40}$")
IMAGE_DIGEST = re.compile(r"@sha256:[0-9a-f]{64}$")
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*([^\s#]+)")
FROM_LINE = re.compile(r"^\s*FROM\s+(?:--\S+\s+)*(\S+)(?:\s+AS\s+(\S+))?", re.IGNORECASE)


def workflow_violations() -> list[str]:
    violations = []
    for workflow in sorted((ROOT / ".github" / "workflows").glob("*.y*ml")):
        for number, line in enumerate(workflow.read_text().splitlines(), start=1):
            match = USES_LINE.match(line)
            if not match:
                continue
            reference = match.group(1)
            if reference.startswith("./"):
                continue  # action locale au dépôt : rien à épingler
            _, _, version = reference.partition("@")
            if not FULL_SHA.fullmatch(version):
                where = workflow.relative_to(ROOT)
                violations.append(f"{where}:{number} action non épinglée par SHA: {reference}")
    return violations


def dockerfile_violations() -> list[str]:
    violations = []
    for dockerfile in sorted((ROOT / "apps").rglob("Dockerfile*")):
        stages: set[str] = set()
        for number, line in enumerate(dockerfile.read_text().splitlines(), start=1):
            match = FROM_LINE.match(line)
            if not match:
                continue
            image, alias = match.groups()
            internal = image.lower() == "scratch" or image in stages
            if alias:
                stages.add(alias)
            if not internal and not IMAGE_DIGEST.search(image):
                where = dockerfile.relative_to(ROOT)
                violations.append(f"{where}:{number} image de base sans digest sha256: {image}")
    return violations


def supply_chain_policy() -> None:
    violations = [*workflow_violations(), *dockerfile_violations()]
    for violation in violations:
        print(f"VIOLATION {violation}", file=sys.stderr)
    if violations:
        raise RuntimeError(f"{len(violations)} référence(s) mutable(s) détectée(s)")
    print("OK supply-chain policy")


def verify() -> None:
    test()
    lint()
    supply_chain_policy()


def compose_up() -> None:
    require_command("docker")
    run(["docker", "compose", "up", "--build", "--detach"])


def compose_down() -> None:
    require_command("docker")
    run(["docker", "compose", "down"])


def wait_for(url: str, *, timeout_seconds: int = 45) -> None:
    deadline = time.monotonic() + timeout_seconds
    last_error: Exception | None = None

    while time.monotonic() < deadline:
        try:
            with urlopen(url, timeout=3) as response:
                if 200 <= response.status < 300:
                    print(f"OK {url}")
                    return
        except (URLError, TimeoutError) as error:
            last_error = error
        time.sleep(1)

    raise RuntimeError(f"Service unavailable at {url}: {last_error}")


def smoke() -> None:
    wait_for("http://localhost:8080/health")
    wait_for("http://localhost:8080/cars")
    wait_for("http://localhost:8000/health")
    request = Request(
        "http://localhost:8000/quotes",
        data=json.dumps({"category": "standard", "days": 2}).encode(),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urlopen(request, timeout=3) as response:
        quote = json.load(response)
    if quote.get("total") != 120.0 or quote.get("currency") != "EUR":
        raise RuntimeError(f"Unexpected pricing response: {quote}")
    print("OK pricing quote")


def require_command(command: str) -> None:
    if shutil.which(command) is None:
        raise RuntimeError(f"Required command not found: {command}")


def prerequisites() -> None:
    checks = {
        "Python": sys.executable,
        "Java": shutil.which("java"),
        "Docker": shutil.which("docker"),
    }
    missing = False

    for name, location in checks.items():
        if location:
            print(f"OK {name}: {location}")
        else:
            print(f"MISSING {name}")
            missing = True

    if missing:
        raise RuntimeError("One or more prerequisites are missing")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "command",
        choices=[
            "prerequisites",
            "test",
            "test-java",
            "test-python",
            "lint",
            "lint-java",
            "lint-python",
            "verify",
            "supply-chain-policy",
            "compose-up",
            "compose-down",
            "smoke",
        ],
    )
    args = parser.parse_args()

    commands = {
        "prerequisites": prerequisites,
        "test": test,
        "test-java": test_java,
        "test-python": test_python,
        "lint": lint,
        "lint-java": lint_java,
        "lint-python": lint_python,
        "verify": verify,
        "supply-chain-policy": supply_chain_policy,
        "compose-up": compose_up,
        "compose-down": compose_down,
        "smoke": smoke,
    }

    try:
        commands[args.command]()
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        print(f"ERROR: {error}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
