#!/usr/bin/env python3
# Bring the whole Misinformation Monitor system up, in one command.

from __future__ import annotations

import argparse
import json
import os
import shutil
import ssl
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

import down

ROOT = Path(__file__).resolve().parent
FABRIC_DIR = ROOT / "blockchain/fabric-samples"
MODEL = ROOT / "apps/flagging-engine/model/model.safetensors"
FABRIC_VERSION, CA_VERSION = "2.5.9", "1.5.9"
BOOTSTRAP_URL = os.environ.get(
    "FABRIC_BOOTSTRAP_URL", "https://raw.githubusercontent.com/hyperledger/fabric/main/scripts/bootstrap.sh")
APP_SERVICES = ["kafka", "claim-ingest-worker", "flagging-engine", "submission-worker",
                "fact-checking-service", "web", "prometheus", "grafana"]


class Log:
    def __init__(self, dry_run: bool):
        self.file = None
        if not dry_run:
            (ROOT / "logs").mkdir(exist_ok=True)
            self.path = ROOT / "logs" / time.strftime("up-%Y%m%d-%H%M%S.log")
            self.file = open(self.path, "w", buffering=1)

    def write(self, text: str) -> None:
        print(text, end="", flush=True)
        if self.file:
            self.file.write(text)


LOG: Log
STEP = [0, 0]


def say(msg: str = "") -> None:
    LOG.write(msg + "\n")


def step(title: str) -> None:
    STEP[0] += 1
    say(f"\n=== [{STEP[0]}/{STEP[1]}] {title} " + "=" * max(4, 60 - len(title)))


def fail(msg: str) -> None:
    say(f"\nERROR: {msg}")
    say("Remove anything that was partly started with: python3 down.py")
    sys.exit(1)


def run(cmd: list[str], cwd: Path = ROOT, env: dict | None = None, check: bool = True, shell: bool = False) -> int:
    """Run a command, streaming its output to the console and the log."""
    shown = cmd if isinstance(cmd, str) else " ".join(cmd)
    say(f"$ {shown}")
    proc = subprocess.Popen(cmd, cwd=cwd, env=env, shell=shell, text=True, bufsize=1,
                            stdout=subprocess.PIPE, stderr=subprocess.STDOUT)
    assert proc.stdout is not None
    for line in proc.stdout:
        LOG.write("  " + line)
    code = proc.wait()
    if check and code != 0:
        fail(f"command failed (exit {code}): {shown}")
    return code


def base_env() -> dict:
    env = dict(os.environ)
    for line in (ROOT / ".env").read_text().splitlines() if (ROOT / ".env").exists() else []:
        if "=" in line and not line.lstrip().startswith("#"):
            k, v = line.split("=", 1)
            env.setdefault(k.strip(), v.strip())
    return env


def mem_available_gb() -> float | None:
    try:
        for line in Path("/proc/meminfo").read_text().splitlines():
            if line.startswith("MemAvailable:"):
                return int(line.split()[1]) / 1024 / 1024
    except OSError:
        pass
    return None


def fabric_provisioned() -> bool:
    return all((FABRIC_DIR / p).exists() for p in ("bin/peer", "config/core.yaml", "test-network/network.sh"))


def preflight(args) -> list[str]:
    # Hard requirements fail; soft ones become warnings.
    warnings: list[str] = []
    if sys.version_info < (3, 10):
        fail("Python 3.10+ is required")
    needed = ["docker", "openssl", "curl"] + ([] if args.demo else ["jq", "go", "git"])
    missing = [c for c in needed if not shutil.which(c)]
    if missing:
        fail(f"missing tools: {', '.join(missing)} (see README > Prerequisites)")
    if subprocess.run(["docker", "info"], capture_output=True).returncode != 0:
        fail("cannot talk to Docker: start the daemon and make sure your user is in the docker group")
    if subprocess.run(["docker", "compose", "version"], capture_output=True).returncode != 0:
        fail("Docker Compose v2 is required (`docker compose`, not the old `docker-compose`)")
    say("   docker, compose v2, " + ", ".join(needed[1:]) + ": ok")

    if not args.demo and not args.no_ai and (not MODEL.exists() or MODEL.stat().st_size < 100_000_000):
        warnings.append(f"model weights not found at {MODEL.relative_to(ROOT)}: starting without the "
                        "flagging engine (claims will not be screened or submitted automatically)")
        args.no_ai = True
    if not args.demo and not fabric_provisioned():
        say(f"   Fabric binaries not provisioned yet: they will be downloaded ({FABRIC_VERSION} / CA {CA_VERSION})")
    for w in warnings:
        say(f"   WARNING: {w}")
    return warnings


def check_memory(args, note: str = "") -> list[str]:
    need_gb = 1.5 if args.demo else (3.5 if args.no_ai else 6.5)
    avail = mem_available_gb()
    if avail is None:
        return []
    say(f"   memory available: {avail:.1f} GB{note} (this mode needs about {need_gb} GB)")
    if avail >= need_gb:
        return []
    warning = (f"only {avail:.1f} GB of memory is free; services may be killed or slow. "
               "Close other programs or use --no-ai / --demo.")
    say(f"   WARNING: {warning}")
    return [warning]


def provision_fabric() -> None:
    """README step 2, automated: Fabric binaries, config and test-network."""
    if fabric_provisioned():
        say("   already provisioned")
        return
    run(f"curl -sSL {BOOTSTRAP_URL} | bash -s -- {FABRIC_VERSION} {CA_VERSION}", cwd=FABRIC_DIR, shell=True)
    nested = FABRIC_DIR / "fabric-samples"
    for name in ("test-network", "bin", "config"):
        if (nested / name).exists() and not (FABRIC_DIR / name).exists():
            shutil.move(str(nested / name), str(FABRIC_DIR / name))
    if nested.exists():
        shutil.rmtree(nested)
    if not fabric_provisioned():
        fail("Fabric provisioning did not produce bin/, config/ and test-network/ in blockchain/fabric-samples")


# health checks

_INSECURE = ssl.create_default_context()
_INSECURE.check_hostname = False
_INSECURE.verify_mode = ssl.CERT_NONE  # localhost probes against the self-signed edge certificate only


def http(url: str, method: str = "GET", body: dict | None = None, timeout: float = 5.0):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, method=method,
                                 headers={"Content-Type": "application/json"} if data else {})
    try:
        with urllib.request.urlopen(req, timeout=timeout, context=_INSECURE if url.startswith("https") else None) as r:
            raw = r.read()
            status = r.status
    except urllib.error.HTTPError as exc:
        raw, status = exc.read(), exc.code
    except (urllib.error.URLError, OSError, TimeoutError):
        return 0, None
    try:
        return status, json.loads(raw) if raw else None
    except json.JSONDecodeError:
        return status, raw.decode(errors="replace")


def wait_for(name: str, url: str, timeout_s: int, ok=lambda s, b: s == 200) -> bool:
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        status, body = http(url)
        if ok(status, body):
            return True
        time.sleep(3)
    return False


def health_checks(args, env: dict) -> list[tuple[str, str, bool, bool]]:
    # (name, url, ok, required)
    web = f"https://127.0.0.1:{env.get('WEB_HTTPS_PORT', '8443')}"
    status_ok = lambda s, b: s == 200 and isinstance(b, dict) and b.get("status") == "ok"  # noqa: E731
    checks = [
        ("blockchain gateway", "http://127.0.0.1:8000/healthz", 120, lambda s, b: s == 200, True),
        ("ledger reachable", "http://127.0.0.1:8000/api/public/info", 120,
         lambda s, b: s == 200 and isinstance(b, dict) and b.get("registered_orgs", 0) >= args.orgs,
         True),
        ("fact-checking service", "http://127.0.0.1:8002/health", 120, lambda s, b: s == 200, True),
        ("web client (HTTPS edge)", f"{web}/", 120, lambda s, b: s == 200, True),
        ("API through the edge", f"{web}/api/public/stats", 60, lambda s, b: s == 200, True),
    ]
    if not args.demo:
        checks += [
            ("claim ingest", "http://127.0.0.1:8003/health", 180, status_ok, True),
            ("submission worker", "http://127.0.0.1:8001/health", 180, status_ok, True),
            ("prometheus", "http://127.0.0.1:9090/-/ready", 90, lambda s, b: s == 200, False),
            ("grafana", "http://127.0.0.1:3000/api/health", 120, lambda s, b: s == 200, False),
        ]
        if not args.no_ai:
            checks.append(("flagging engine (model load)", "http://127.0.0.1:8004/health", 600, status_ok, False))
        if args.explorer:
            checks.append(("explorer", "http://127.0.0.1:8080/", 180, lambda s, b: s == 200, False))
    results = []
    for name, url, timeout_s, ok, required in checks:
        LOG.write(f"   {name:<30} ")
        good = wait_for(name, url, timeout_s, ok)
        LOG.write(("ok" if good else ("FAILED" if required else "not ready (optional)")) + "\n")
        results.append((name, url, good, required))
    return results


def smoke_test(env: dict) -> bool:
    # One tip through edge -> ingest -> Kafka -> AI -> submission -> gateway -> IPFS -> Fabric.
    web = f"https://127.0.0.1:{env.get('WEB_HTTPS_PORT', '8443')}"
    status, body = http(f"{web}/tips", "POST", {
        "claim": f"Smoke test from up.py at {time.strftime('%Y-%m-%d %H:%M:%S')}: checking the end-to-end pipeline."})
    if status != 200 or not isinstance(body, dict):
        say(f"   tip submission failed: HTTP {status} {body}")
        return False
    ref = body["reference"]
    say(f"   tip accepted, reference {ref}; waiting for it to reach the ledger")
    deadline = time.monotonic() + 300
    while time.monotonic() < deadline:
        status, body = http(f"{web}/api/public/tips/{ref}")
        if status == 200 and isinstance(body, dict) and body.get("state") in ("under_review", "closed"):
            say(f"   on the ledger and under review after AI screening: {ref}")
            return True
        time.sleep(5)
    say("   the tip did not reach the ledger within 5 minutes (see docker logs flagging-engine / submission-worker)")
    return False


def seed_claims(count: int, env: dict, engine_ready: bool) -> bool:
    # A fixed sample of data/test.json through the real pipeline, so the review queue starts filled.
    cmd = [sys.executable, "-u", str(ROOT / "blockchain/scripts/seed-claims.py"), "--count", str(count), "--force"]
    if not engine_ready:
        say("   the flagging engine is still loading: queuing the claims without waiting for them")
        cmd += ["--wait", "0"]
    return run(cmd, env=env, check=False) == 0


# container addresses

# What a published port is for, so the table says how to open it.
PORT_HINTS = {
    ("web-client", 8443): ("https", "/", "web client"),
    ("web-client", 8080): ("http", "/", "redirects to HTTPS"),
    ("blockchain-gateway", 8000): ("http", "/api/public/info", "gateway API"),
    ("fact-checking-service", 8000): ("http", "/health", "fact-checking API"),
    ("claim-ingest-worker", 8000): ("http", "/health", "ingest API"),
    ("submission-worker", 8000): ("http", "/metrics", "metrics"),
    ("flagging-engine", 8000): ("http", "/health", "AI model API"),
    ("fabric-gateway", 9100): ("http", "/health", "Fabric SDK sidecar"),
    ("ipfs-gateway", 9101): ("http", "/health", "IPFS bridge"),
    ("ipfs-node", 8080): ("http", "/ipfs/<CID>", "IPFS HTTP gateway"),
    ("ipfs-node", 5001): ("http", "/api/v0", "kubo RPC (admin)"),
    ("grafana", 3000): ("http", "/", "Grafana"),
    ("prometheus", 9090): ("http", "/", "Prometheus"),
    ("explorer", 8080): ("http", "/", "Hyperledger Explorer"),
}
# Display order: what people open first, the ledger last.
ORDER = ["web-client", "blockchain-gateway", "fact-checking-service", "claim-ingest-worker", "kafka",
         "flagging-engine", "submission-worker", "fabric-gateway", "ipfs-gateway", "ipfs-node", "ipfs-node-1",
         "ipfs-node-2", "prometheus", "grafana", "explorer", "explorerdb"]


def lan_ip() -> str | None:
    # This machine's address on its local network
    import socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
    try:
        sock.connect(("10.255.255.255", 1))
        ip = sock.getsockname()[0]
    except OSError:
        return None
    finally:
        sock.close()
    return None if ip.startswith("127.") else ip


def system_containers() -> list[dict]:
    names = subprocess.run(["docker", "ps", "--format", "{{.Names}}"], capture_output=True, text=True).stdout.split()
    ours = [n for n in names if n in down.KNOWN_CONTAINERS or down.FABRIC_NAME.match(n)]
    if not ours:
        return []
    res = subprocess.run(["docker", "inspect", *ours], capture_output=True, text=True)
    try:
        data = json.loads(res.stdout or "[]")
    except json.JSONDecodeError:
        return []
    rank = {n: i for i, n in enumerate(ORDER)}
    return sorted(data, key=lambda c: (rank.get(c["Name"].lstrip("/"), len(ORDER)), c["Name"]))


def describe(c: dict, lan: str | None) -> tuple[str, list[str], list[str]]:
    # (display name, ["IP (network)"], ["how to reach it"])
    name = c["Name"].lstrip("/")
    display = name
    if name.startswith("dev-peer"):  # chaincode container, e.g. dev-peer0.org1.example.com-misinformation_2.4-<hash>
        display = name.split(".example.com")[0] + " chaincode"
    nets = c.get("NetworkSettings", {}).get("Networks") or {}
    short = lambda n: n if len(n) <= 24 else n[:21] + "..."  # noqa: E731  compose default nets are long
    ips = [f"{v['IPAddress']} ({short(net)})" for net, v in sorted(nets.items()) if v.get("IPAddress")] or ["-"]

    access: list[str] = []
    published = set()
    for spec, bindings in sorted((c.get("NetworkSettings", {}).get("Ports") or {}).items()):
        port, proto = int(spec.split("/")[0]), spec.split("/")[1]
        hosts = {(b["HostIp"], b["HostPort"]) for b in bindings or [] if ":" not in b["HostIp"]}  # skip IPv6 dupes
        for host_ip, host_port in sorted(hosts):
            published.add(port)
            public = host_ip in ("0.0.0.0", "")
            shown_host = "localhost" if public or host_ip.startswith("127.") else host_ip
            hint = PORT_HINTS.get((name if not name.startswith("ipfs-node") else "ipfs-node", port))
            scope = "public" if public else "this host only"
            if hint and proto == "tcp":
                scheme, path, label = hint
                access.append(f"{scheme}://{shown_host}:{host_port}{path}  {label} [{scope}]")
                if public and lan:
                    access.append(f"{scheme}://{lan}:{host_port}{path}  from other machines")
            else:
                access.append(f"{shown_host}:{host_port} -> {port}/{proto} [{scope}]")
    internal = sorted(int(p.split("/")[0]) for p in (c.get("Config", {}).get("ExposedPorts") or {})
                      if int(p.split("/")[0]) not in published)
    if internal and not name.startswith("dev-peer"):
        access.append("containers: " + ", ".join(f"{name}:{p}" for p in internal))
    return display, ips, access or ["-"]


def print_addresses() -> None:
    containers = system_containers()
    if not containers:
        say("   No containers of the system are running.")
        return
    lan = lan_ip()
    rows = [describe(c, lan) for c in containers]
    w_name = max(len("CONTAINER"), *(len(r[0]) for r in rows))
    w_ip = max(len("IP ADDRESS (DOCKER NETWORK)"), *(len(ip) for r in rows for ip in r[1]))
    say(f"   {'CONTAINER':<{w_name}}  {'IP ADDRESS (DOCKER NETWORK)':<{w_ip}}  HOW TO REACH IT")
    say(f"   {'-' * w_name}  {'-' * w_ip}  {'-' * 15}")
    for display, ips, access in rows:
        for i in range(max(len(ips), len(access))):
            say(f"   {display if i == 0 else '':<{w_name}}  {ips[i] if i < len(ips) else '':<{w_ip}}  "
                f"{access[i] if i < len(access) else ''}")
    say("")
    say("   Container IPs work only from this machine and from other containers, and change when a")
    say("   container is recreated: prefer the addresses on the right. Containers on the same Docker")
    say("   network also reach each other by name (e.g. http://blockchain-gateway:8000).")
    if lan:
        say(f"   This machine's LAN address is {lan}; from other machines only [public] ports answer.")

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0],
                                 formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    ap.add_argument("--orgs", type=int, default=3, help="consortium size, 3..30 (default 3)")
    mode = ap.add_mutually_exclusive_group()
    mode.add_argument("--benchmark", action="store_true", help="well-known API keys + validation load")
    mode.add_argument("--demo", action="store_true", help="mock ledger, no Fabric (gateway, fact-checking, web)")
    ap.add_argument("--no-ai", action="store_true", help="skip the flagging engine")
    ap.add_argument("--explorer", action="store_true", help="also start Hyperledger Explorer")
    ap.add_argument("--smoke", action="store_true", help="send one tip through the whole pipeline at the end")
    ap.add_argument("--seed-claims", type=int, metavar="N",
                    help="claims from data/test.json to fill the review queue with (default 10, "
                         "0 with --benchmark; 0: none)")
    ap.add_argument("--dry-run", action="store_true", help="pre-flight checks and plan only")
    ap.add_argument("--status", action="store_true",
                    help="only list the running containers with their IP addresses and how to reach them")
    args = ap.parse_args()
    if not 3 <= args.orgs <= 30:
        ap.error("--orgs must be between 3 and 30")
    if args.seed_claims is None:
        args.seed_claims = 0 if args.benchmark or args.demo else 10  # demo.sh seeds its own mock claims
    if args.seed_claims < 0:
        ap.error("--seed-claims must be 0 or more")

    global LOG
    LOG = Log(args.dry_run or args.status)
    if args.status:
        say("Misinformation Monitor: running containers")
        print_addresses()
        return 0
    mode_name = "demo (mock ledger)" if args.demo else ("benchmark" if args.benchmark else "secure prototype")
    say(f"Misinformation Monitor: bringing the system up, mode={mode_name}, orgs={args.orgs}"
        + ("" if args.demo else f", ai={'off' if args.no_ai else 'on'}"))
    STEP[1] = 5 if args.demo else 8 + int(args.explorer) + int(args.smoke) + int(args.seed_claims > 0)

    step("Pre-flight checks")
    warnings = preflight(args)
    if args.dry_run:
        check_memory(args, " before removing the previous deployment")
        say("\nDry run. Plan:")
        say("  clean slate (down.py) -> " + ("demo.sh up" if args.demo else
            "provision Fabric if needed -> secrets + networks -> startup.sh -> docker compose up -> health checks"))
        down.teardown(dry_run=True)
        return 0

    step("Clean slate (removing anything left from a previous run)")
    if down.teardown(quiet=True) != 0:
        fail("could not remove the previous deployment; run python3 down.py to see why")
    say("   previous deployment removed")
    warnings += check_memory(args)

    if args.demo:
        step("Starting the demo stack (gateway on the mock ledger, fact-checking service, web client)")
        run([str(ROOT / "demo.sh"), "up", "--orgs", str(args.orgs)])
        env = base_env()
    else:
        step("Fabric binaries")
        provision_fabric()

        step("Secrets and Docker networks")
        run([str(ROOT / "blockchain/scripts/gen-secrets.sh")])
        run([str(ROOT / "blockchain/scripts/ensure-networks.sh")])
        env = base_env()

        step(f"Blockchain layer: {args.orgs}-org Fabric network, chaincode, IPFS, gateways, accounts")
        startup = [str(ROOT / "startup.sh"), "--orgs", str(args.orgs), "--skip-caliper"]
        startup += ["--test-samples", "10"] if args.benchmark else ["--secure"]
        run(startup, env=env)

        step("Application layer: Kafka, AI pipeline, fact-checking service, web client, monitoring")
        services = [s for s in APP_SERVICES if not (args.no_ai and s == "flagging-engine")]
        run(["docker", "compose", "up", "-d", "--build", *services], env=env)

        if args.explorer:
            step("Hyperledger Explorer")
            run(["docker", "compose", "-f", "blockchain/explorer/docker-compose.yaml", "up", "-d", "--build"], env=env)

    step("Health checks")
    results = health_checks(args, env)
    seed_ok = None
    if args.seed_claims and not args.demo:
        step(f"Review queue: {args.seed_claims} claims from data/test.json through the AI pipeline")
        if args.no_ai:
            say("   skipped: the claims are screened by the flagging engine")
        else:
            engine_ready = any(good for name, _, good, _ in results if name.startswith("flagging engine"))
            seed_ok = seed_claims(args.seed_claims, env, engine_ready)
    smoke_ok = None
    if args.smoke and not args.demo:
        step("End-to-end smoke test")
        smoke_ok = smoke_test(env) if not args.no_ai else None
        if args.no_ai:
            say("   skipped: needs the flagging engine")

    step("Summary")
    failed = [name for name, _, good, required in results if required and not good]
    optional_down = [name for name, _, good, required in results if not required and not good]
    web = f"https://localhost:{env.get('WEB_HTTPS_PORT', '8443')}"
    say(f"   Web client        {web}   (self-signed certificate: accept the browser warning)")
    say("   Sign-in accounts  secrets/initial-credentials.txt   (one-time passwords; change at first sign-in)")
    say("                     checker.orgN = fact-checker, official.orgN = official console")
    if seed_ok:
        say(f"   Review queue      {args.seed_claims} claims from data/test.json, submitted by the AI's org (org1),")
        say("                     in every org's queue, org1 included")
    if not args.demo:
        say("   Gateway API       http://localhost:8000   (loopback only)")
        say("   Grafana           http://localhost:3000   (admin / GRAFANA_ADMIN_PASSWORD in .env)")
        if args.explorer:
            say("   Explorer          http://localhost:8080   (exploreradmin / exploreradminpw)")
        if args.benchmark:
            say("   Benchmarks        python3 benchmarks/feed_samples.py --samples 10   (uses key-orgN)")
    say(f"   Log               {LOG.path.relative_to(ROOT)}")
    say("   Shut down         python3 down.py")
    for w in warnings:
        say(f"   WARNING: {w}")
    if optional_down:
        say(f"   Still starting or unavailable (optional): {', '.join(optional_down)}")
    if seed_ok is False:
        say("   Review queue NOT fully seeded: see the review-queue step above "
            "(re-run: python3 blockchain/scripts/seed-claims.py)")
    if smoke_ok is False:
        say("   Smoke test FAILED: the pipeline did not deliver the test tip to the ledger")
    say("\n   Containers, their IP addresses and how to reach them (again any time: python3 up.py --status)\n")
    print_addresses()
    if failed:
        say(f"\nFAILED: {', '.join(failed)} did not become healthy. Check `docker ps` and `docker logs <name>`.")
        return 1
    say("\nSystem is up.")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except KeyboardInterrupt:
        print("\nInterrupted. Partially started services can be removed with: python3 down.py", file=sys.stderr)
        sys.exit(130)
    except BrokenPipeError:  # output piped into head/less, which closed early
        os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        sys.exit(0)
