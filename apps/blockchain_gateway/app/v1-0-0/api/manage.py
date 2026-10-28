"""Operator CLI for the gateway's user accounts and audit log.

Over the API only a signed-in official can create users, so the first
official of each org is bootstrapped here. Run it where the gateway's
databases live, usually inside the container:

    docker exec -it blockchain-gateway python api/manage.py seed --orgs org1,org2,org3
    docker exec -it blockchain-gateway python api/manage.py create-user --username alice --org org2 --role fact_checker
    docker exec -it blockchain-gateway python api/manage.py reset --username alice
    docker exec    blockchain-gateway python api/manage.py list-users
    docker exec    blockchain-gateway python api/manage.py verify-audit

New accounts get a one-time password and must choose their own at first
sign-in."""
from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import audit  # noqa: E402
from auth import (  # noqa: E402
    USER_ROLES, USERNAME_RE, UserStore, generate_temp_password, password_problems,
)

USER_DB = os.environ.get("USER_DB") or str(Path(__file__).resolve().parent / "users.db")
AUDIT_DB = os.environ.get("AUDIT_DB") or str(Path(USER_DB).resolve().parent / "audit.db")


def _store() -> UserStore:
    return UserStore(USER_DB)


def _log(action: str, target: str, org: str, **details) -> None:
    audit.init_audit(AUDIT_DB)
    audit.record(action=action, actor="cli", org=org, role="operator", target=target, details=details)


def cmd_create(args: argparse.Namespace) -> int:
    username = args.username.strip().lower()
    if not USERNAME_RE.fullmatch(username):
        print("username: 3-64 chars of a-z, 0-9, '.', '_' or '-'", file=sys.stderr)
        return 2
    if args.password_stdin:
        password = sys.stdin.readline().rstrip("\n")
        problems = password_problems(password, username)
        if problems:
            print("password " + "; ".join(problems), file=sys.stderr)
            return 2
        must_change = False
    else:
        password, must_change = generate_temp_password(), True
    if not _store().create_user(username, args.org, args.role, password, display_name=args.display_name,
                                created_by="cli", must_change=must_change):
        print(f"user '{username}' already exists", file=sys.stderr)
        return 1
    _log("user_created", username, args.org, role=args.role)
    print(f"created {username} ({args.role} @ {args.org})")
    if must_change:
        print(f"one-time password: {password}")
    return 0


def cmd_reset(args: argparse.Namespace) -> int:
    store = _store()
    user = store.get_user(args.username)
    if not user:
        print(f"no user '{args.username}'", file=sys.stderr)
        return 1
    temp = generate_temp_password()
    store.set_password(args.username, temp, must_change=True)
    store.update_user(args.username, totp_secret="", totp_pending="", totp_last_step=-1, active=1)
    store.revoke_user_sessions(args.username)
    _log("user_reset", args.username, user["org"])
    print(f"one-time password for {args.username}: {temp}")
    return 0


def cmd_list(args: argparse.Namespace) -> int:
    for u in _store().list_users(args.org):
        flags = [f for f, on in (("disabled", not u["active"]), ("must-change", u["must_change_password"]),
                                 ("mfa", bool(u["totp_secret"]))) if on]
        print(f"{u['org']:6} {u['role']:13} {u['username']:28} {' '.join(flags)}")
    return 0


def cmd_seed(args: argparse.Namespace) -> int:
    # One official and one fact-checker per org, skipping accounts that already exist. Credentials go to stdout and, with --out, to a 0600 file.
    store = _store()
    lines = []
    for org in [o.strip() for o in args.orgs.split(",") if o.strip()]:
        for role, prefix in (("official", "official"), ("fact_checker", "checker")):
            username = f"{prefix}.{org}"
            if store.get_user(username):
                continue
            temp = generate_temp_password()
            store.create_user(username, org, role, temp, created_by="seed", must_change=True)
            _log("user_created", username, org, role=role, via="seed")
            lines.append(f"{org:6} {role:13} {username:20} {temp}")
    if not lines:
        print("all seed accounts already exist; nothing to do")
        return 0
    text = "# org   role          username             one-time password (change at first sign-in)\n" + "\n".join(lines) + "\n"
    print(text, end="")
    if args.out:
        fd = os.open(args.out, os.O_WRONLY | os.O_CREAT | os.O_APPEND, 0o600)
        with os.fdopen(fd, "a") as fh:
            fh.write(text)
        print(f"(also appended to {args.out})")
    return 0


def cmd_verify_audit(_: argparse.Namespace) -> int:
    result = audit.init_audit(AUDIT_DB).verify()
    print(result)
    return 0 if result["intact"] else 1


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(prog="manage.py", description=__doc__.split("\n\n")[0])
    sub = ap.add_subparsers(dest="cmd", required=True)
    c = sub.add_parser("create-user", help="create one account")
    c.add_argument("--username", required=True)
    c.add_argument("--org", required=True)
    c.add_argument("--role", required=True, choices=USER_ROLES)
    c.add_argument("--display-name", default="")
    c.add_argument("--password-stdin", action="store_true",
                   help="read the password from stdin instead of issuing a one-time password")
    c.set_defaults(fn=cmd_create)
    r = sub.add_parser("reset", help="issue a one-time password, clear 2FA, end sessions, re-enable")
    r.add_argument("--username", required=True)
    r.set_defaults(fn=cmd_reset)
    ls = sub.add_parser("list-users")
    ls.add_argument("--org", default=None)
    ls.set_defaults(fn=cmd_list)
    s = sub.add_parser("seed", help="official.<org> + checker.<org> for each org")
    s.add_argument("--orgs", required=True, help="comma-separated, e.g. org1,org2,org3")
    s.add_argument("--out", default=None, help="append credentials to this file (created 0600)")
    s.set_defaults(fn=cmd_seed)
    v = sub.add_parser("verify-audit", help="check the audit log's hash chain")
    v.set_defaults(fn=cmd_verify_audit)
    args = ap.parse_args(argv)
    return args.fn(args)


if __name__ == "__main__":
    sys.exit(main())
