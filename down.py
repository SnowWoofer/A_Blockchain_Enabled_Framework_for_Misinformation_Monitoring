#!/usr/bin/env python3
"""Shut the whole Misinformation Monitor system down and clean up, in one command.

    python3 down.py              stop and remove every container, network, volume,
                                 chaincode image and runtime file the system created
    python3 down.py --purge      also delete the generated secrets (.env, secrets/,
                                 the IPFS swarm key) and the images built here
    python3 down.py --dry-run    show what would be removed, change nothing

Never touched: research results (results/), datasets (data/), the model
weights, the Fabric binaries in blockchain/fabric-samples/, and source code.

Container names are fixed in the compose files, so this also removes
containers of the same name started from another clone of the repository.
Stdlib only; needs Docker with Compose v2.
"""
# Shut the whole Misinformation Monitor system down and clean up, in one command.

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent
GATEWAY_DATA = ROOT / "apps/blockchain_gateway/app/v1-0-0/api"

# Compose files, in teardown order: users of the ledger first, the ledger last.
COMPOSE_FILES = [
    "docker-compose.yml",                       # Kafka, AI pipeline, fact-checking, web client, monitoring
    "blockchain/explorer/docker-compose.yaml",
    "benchmarks/caliper/docker-compose.yaml",
    "apps/fact-checking-service/docker-compose.yaml",  # as started on their own by demo.sh
    "apps/web-client/docker-compose.yaml",
    "apps/blockchain_gateway/docker-compose.yaml",
    "apps/fabric_gateway/docker-compose.yaml",
    "apps/ipfs_gateway/docker-compose.yaml",     # includes the three IPFS nodes
]
KNOWN_CONTAINERS = [
    "web-client", "fact-checking-service", "claim-ingest-worker", "flagging-engine", "submission-worker",
    "kafka", "prometheus", "grafana", "blockchain-gateway", "fabric-gateway", "ipfs-gateway",
    "ipfs-node", "ipfs-node-1", "ipfs-node-2", "explorer", "explorerdb", "misinfo-caliper",
]
NETWORKS = ["misinfo_net", "kafka_net", "ipfs_net", "fabric_test"]
# Fabric test-network containers (any consortium size) and their chaincode containers.
FABRIC_NAME = re.compile(r"^(peer\d+\.org\d+\.example\.com|orderer\d*\.example\.com|ca_org\d+|ca_orderer|"
                         r"couchdb\d+|dev-peer.*)$")
EXTERNAL_VOLUMES = ["ipfs-data"]
BUILT_IMAGES = ["misinfo-blockchain-gateway", "misinfo-gateway", "misinfo-ipfs-gateway", "misinfo-web",
                "misinfo-caliper"]
RUNTIME_FILES = [
    *(GATEWAY_DATA / f"{db}{suffix}" for db in ("offchain.db", "users.db", "audit.db")
      for suffix in ("", "-journal", "-wal", "-shm")),
    GATEWAY_DATA / "mock-ledger.json",
    GATEWAY_DATA / "mock-ledger.json.tmp",
    ROOT / "secrets/initial-credentials.txt",
]
PURGE_PATHS = [ROOT / ".env", ROOT / "secrets", ROOT / "blockchain/scripts/swarm.key",
               ROOT / "apps/web-client/dist"]
# Placeholders so `compose down` can parse files whose ${VAR:?} secrets are unset (e.g. after a purge). Only used when the real value is missing.
COMPOSE_PLACEHOLDERS = {
    k: "unset" for k in ("JWT_SECRET", "INTERNAL_API_TOKEN", "PSEUDONYM_KEY", "PIPELINE_RAW_KEY",
                         "PIPELINE_FLAGGED_KEY", "INGEST_API_KEY", "SUBMITTER_API_KEY", "GRAFANA_ADMIN_PASSWORD")
}
COMPOSE_PLACEHOLDERS["ORGS_LIST"] = "org1"


class Runner:
    def __init__(self, dry_run: bool = False, quiet: bool = False):
        self.dry_run = dry_run
        self.quiet = quiet
        self.warnings: list[str] = []

    def say(self, msg: str) -> None:
        if not self.quiet:
            print(msg, flush=True)

    def query(self, *cmd: str) -> list[str]:
        """Read-only docker query; runs even in dry-run mode."""
        res = subprocess.run(cmd, capture_output=True, text=True)
        return [line for line in res.stdout.splitlines() if line.strip()] if res.returncode == 0 else []

    def act(self, *cmd: str, cwd: Path = ROOT, env: dict | None = None, show: bool = False) -> bool:
        if self.dry_run:
            print("   would run:", " ".join(cmd), flush=True)
            return True
        stream = show and not self.quiet
        res = subprocess.run(cmd, cwd=cwd, env=env, text=True,
                             stdout=None if stream else subprocess.PIPE,
                             stderr=None if stream else subprocess.STDOUT)
        if res.returncode != 0:
            tail = (res.stdout or "").strip().splitlines()[-1:] if res.stdout else []
            self.warnings.append(f"{' '.join(cmd[:4])}... exited {res.returncode}{': ' + tail[0] if tail else ''}")
            return False
        return True

    def remove_path(self, path: Path) -> bool:
        if not path.exists() and not path.is_symlink():
            return False
        if self.dry_run:
            print(f"   would delete {path.relative_to(ROOT)}", flush=True)
            return True
        if path.is_dir() and not path.is_symlink():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True


def compose_env() -> dict:
    env = dict(os.environ)
    env_file = ROOT / ".env"
    if env_file.exists():
        for line in env_file.read_text().splitlines():
            if "=" in line and not line.lstrip().startswith("#"):
                key, value = line.split("=", 1)
                env.setdefault(key.strip(), value.strip())
    for key, value in COMPOSE_PLACEHOLDERS.items():
        env.setdefault(key, value)
    return env


def docker_available() -> bool:
    return subprocess.run(["docker", "info"], capture_output=True).returncode == 0


def teardown(purge: bool = False, dry_run: bool = False, quiet: bool = False) -> int:
    r = Runner(dry_run=dry_run, quiet=quiet)
    if not docker_available():
        print("ERROR: cannot talk to Docker (is the daemon running and are you in the docker group?)",
              file=sys.stderr)
        return 1
    env = compose_env()

    r.say(">> 1/6  Stopping compose stacks (containers, their networks and volumes)")
    for rel in COMPOSE_FILES:
        if not (ROOT / rel).exists():
            continue
        cmd = ["docker", "compose", "-f", rel, "down", "--volumes", "--remove-orphans", "--timeout", "15"]
        if purge and rel == "docker-compose.yml":
            cmd += ["--rmi", "local"]  # images built for the app layer (no custom tag)
        r.say(f"   {rel}")
        r.act(*cmd, env=env)

    r.say(">> 2/6  Tearing down the Fabric network (peers, orderer, CAs, crypto material)")
    test_network = ROOT / "blockchain/fabric-samples/test-network/network.sh"
    if test_network.exists():
        r.act(str(ROOT / "blockchain/scripts/deploy.sh"), "down", env=env, show=not quiet)
    else:
        r.say("   blockchain/fabric-samples/test-network not provisioned; skipping network.sh down")

    r.say(">> 3/6  Removing leftover containers")
    leftovers = set()
    for net in NETWORKS:
        leftovers.update(r.query("docker", "ps", "-aq", "--filter", f"network={net}"))
    existing_names = set(r.query("docker", "ps", "-a", "--format", "{{.Names}}"))
    fabric_name = FABRIC_NAME
    named = sorted(n for n in existing_names if n in KNOWN_CONTAINERS or fabric_name.match(n))
    targets = sorted(leftovers | set(named))
    if targets:
        shown = [n if len(n) <= 40 else n[:37] + "..." for n in named[:14]]
        r.say(f"   {len(targets)} container(s); named: " + ", ".join(shown) + (" ..." if len(named) > 14 else ""))
        unnamed = len(targets) - len(named)
        if unnamed > 0:
            r.say(f"   plus {unnamed} unnamed leftover(s) on the system's networks (e.g. stale chaincode build containers)")
        if dry_run:
            r.say(f"   would run: docker rm -f <{len(targets)} containers>")
        else:
            for i in range(0, len(targets), 100):
                r.act("docker", "rm", "-f", *targets[i:i + 100])
    else:
        r.say("   none")

    r.say(">> 4/6  Removing chaincode images, volumes and networks")
    chaincode_images = r.query("docker", "images", "-q", "--filter", "reference=dev-peer*")
    if chaincode_images:
        r.say(f"   {len(set(chaincode_images))} chaincode image(s)")
        r.act("docker", "rmi", "-f", *sorted(set(chaincode_images)))
    all_volumes = r.query("docker", "volume", "ls", "-q")
    fabric_volume = re.compile(r"(orderer\d*|peer\d+\.org\d+)\.example\.com$")
    volumes = [v for v in all_volumes if v in EXTERNAL_VOLUMES or fabric_volume.search(v)]
    if volumes:
        r.say("   volumes: " + ", ".join(volumes))
        r.act("docker", "volume", "rm", "-f", *volumes)
    existing_nets = set(r.query("docker", "network", "ls", "--format", "{{.Name}}"))
    nets = [n for n in NETWORKS if n in existing_nets]
    if nets:
        r.say("   networks: " + ", ".join(nets))
        r.act("docker", "network", "rm", *nets)

    r.say(">> 5/6  Deleting runtime state (gateway databases, demo ledger, one-time passwords)")
    removed = [p for p in RUNTIME_FILES if r.remove_path(p)]
    r.say(f"   {len(removed)} file(s)" if removed else "   none")

    if purge:
        r.say(">> 6/6  Purging secrets and built images")
        for p in PURGE_PATHS:
            if r.remove_path(p):
                r.say(f"   deleted {p.relative_to(ROOT)}")
        present = set(r.query("docker", "images", "--format", "{{.Repository}}"))
        images = [f"{i}:latest" for i in BUILT_IMAGES if i in present]
        if images:
            r.act("docker", "rmi", "-f", *images)
            r.say("   images: " + ", ".join(images))
    else:
        r.say(">> 6/6  Keeping secrets (.env, secrets/) and built images; use --purge to remove them")

    if dry_run:
        r.say("\nDry run: nothing was changed.")
        return 0
    still = [n for n in r.query("docker", "ps", "-a", "--format", "{{.Names}}")
             if n in KNOWN_CONTAINERS or fabric_name.match(n)]
    if r.warnings:
        r.say("\nWarnings (usually harmless: something was already gone):")
        for w in r.warnings:
            r.say(f"   - {w}")
    if still:
        print(f"\nERROR: still present: {', '.join(still)}", file=sys.stderr)
        return 1
    r.say("\nDone. Nothing of the system is running. Bring it back with: python3 up.py")
    return 0


def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--purge", action="store_true",
                    help="also delete .env, secrets/, the IPFS swarm key and images built here")
    ap.add_argument("--dry-run", action="store_true", help="show what would be removed without changing anything")
    args = ap.parse_args()
    return teardown(purge=args.purge, dry_run=args.dry_run)


if __name__ == "__main__":
    sys.exit(main())
