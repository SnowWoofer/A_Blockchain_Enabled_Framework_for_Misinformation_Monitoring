from __future__ import annotations
import json
import os
import subprocess
import urllib.error
import urllib.request
from typing import Any, Dict, List, Optional, Sequence


class FabricBridge:

    def __init__(
        self,
        channel: str = "mychannel",
        chaincode: str = "misinformation",
        peer_bin: str = "peer",
        org: str = "org1",
        test_network: str = os.path.join(
            os.path.dirname(os.path.abspath(__file__)),
            *([".."] * 5),
            "blockchain",
            "fabric-samples",
            "test-network",
        ),
        endorsers: Optional[Sequence[str]] = None,
    ) -> None:
        self.channel = channel
        self.chaincode = chaincode
        self.peer_bin = peer_bin
        self.org = org
        self.test_network = test_network
        self.endorsers = endorsers or ["org1", "org2"]
        self._env = self._build_env()

    def _build_env(self) -> Dict[str, str]:
        env = os.environ.copy()
        tn = self.test_network
        org_dir = f"org{self.org[3:]}.example.com"
        ports = {"org1": "7051", "org2": "9051", "org3": "11051"}
        msps = {"org1": "Org1MSP", "org2": "Org2MSP", "org3": "Org3MSP"}
        peer_port = ports.get(self.org, "7051")
        env.update(
            {
                "CORE_PEER_TLS_ENABLED": "true",
                "CORE_PEER_LOCALMSPID": msps.get(self.org, "Org1MSP"),
                "CORE_PEER_ADDRESS": f"localhost:{peer_port}",
                "CORE_PEER_TLS_ROOTCERT_FILE": (
                    f"{tn}/organizations/peerOrganizations/{org_dir}/peers/"
                    f"peer0.{org_dir}/tls/ca.crt"
                ),
                "CORE_PEER_MSPCONFIGPATH": (
                    f"{tn}/organizations/peerOrganizations/{org_dir}/users/"
                    f"Admin@{org_dir}/msp"
                ),
                "FABRIC_CFG_PATH": f"{tn}/../config",
            }
        )
        return env

    def _orderer_tls_ca(self) -> str:
        return (
            f"{self.test_network}/organizations/ordererOrganizations/example.com/"
            "orderers/orderer.example.com/msp/tlscacerts/tlsca.example.com-cert.pem"
        )

    def _run(self, args: List[str]) -> str:
        cmd = [self.peer_bin] + args
        result = subprocess.run(
            cmd, capture_output=True, text=True, env=self._env, check=False
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"peer command failed ({result.returncode}): {result.stderr.strip()}"
            )
        return result.stdout.strip()

    def invoke(self, function: str, args: List[str]) -> str:
        payload = json.dumps({"function": function, "Args": args})
        cmd = [
            "chaincode", "invoke",
            "-o", "localhost:7050",
            "--ordererTLSHostnameOverride", "orderer.example.com",
            "--tls", "--cafile", self._orderer_tls_ca(),
            "-C", self.channel, "-n", self.chaincode,
        ]
        for e in self.endorsers:
            cmd += ["--peerAddresses", f"localhost:{self._peer_port(e)}",
                    "--tlsRootCertFiles", self._peer_tls_ca(e)]
        cmd += ["--waitForEvent", "-c", payload]
        return self._run(cmd)
    @staticmethod
    def _peer_port(org: str) -> str:
        return {"org1": "7051", "org2": "9051", "org3": "11051"}.get(org, "7051")

    def _peer_tls_ca(self, org: str) -> str:
        mspid = {"org1": "org1", "org2": "org2", "org3": "org3"}.get(org, "org1")
        return (
            f"{self.test_network}/organizations/peerOrganizations/{mspid}.example.com/"
            f"peers/peer0.{mspid}.example.com/tls/ca.crt"
        )

    def query(self, function: str, args: List[str]) -> Any:
        payload = json.dumps({"function": function, "Args": args})
        out = self._run(
            ["chaincode", "query", "-C", self.channel, "-n", self.chaincode, "-c", payload]
        )
        try:
            return json.loads(out)
        except json.JSONDecodeError:
            return out

    def register_org(self) -> str:
        return self.invoke("RegisterOrg", [])

    def list_orgs(self) -> Any:
        return self.query("ListRegisteredOrgs", [])

    def request_admission(self, org_name: str, org_type: str) -> str:
        return self.invoke("RequestOrgAdmission", [org_name, org_type])

    def vote_on_admission(self, candidate_msp: str, verdict: str) -> str:
        return self.invoke("VoteOnOrgAdmission", [candidate_msp, verdict])

    def finalize_admission(self, candidate_msp: str) -> str:
        return self.invoke("FinalizeOrgAdmission", [candidate_msp])

    def query_admission(self, candidate_msp: str) -> Any:
        return self.query("QueryOrgAdmission", [candidate_msp])

    def submit_report(
        self, report_id: str, content_hash: str, label: str,
        confidence: float, model_version: str, timestamp: str,
    ) -> str:
        return self.invoke(
            "Submit",
            [report_id, content_hash, label,
             f"{confidence:.6f}", model_version, timestamp],
        )

    def submit_fact_check(self, report_id: str, outcome: str) -> str:
        return self.invoke("SubmitFactCheck", [report_id, outcome])

    def finalize_report(self, report_id: str) -> str:
        return self.invoke("FinalizeReport", [report_id])

    def expire_report(self, report_id: str) -> str:
        return self.invoke("ExpireReport", [report_id])

    def query_report(self, report_id: str) -> Any:
        return self.query("QueryReport", [report_id])

    def query_all(self) -> Any:
        return self.query("QueryAllReports", [])

    def history(self, report_id: str) -> Any:
        return self.query("QueryReportHistory", [report_id])


class FabricGatewayBridge:

    def __init__(
        self,
        gateway_url: str = "http://localhost:9100",
        channel: str = "mychannel",
        chaincode: str = "misinformation",
        org: str = "org1",
        endorsers: Optional[Sequence[str]] = None,
        timeout_invoke: float = 60.0,
        timeout_query: float = 30.0,
    ) -> None:
        self.gateway_url = gateway_url.rstrip("/")
        self.channel = channel
        self.chaincode = chaincode
        self.org = org
        self.endorsers = endorsers or ["org1", "org2"]
        self.timeout_invoke = timeout_invoke
        self.timeout_query = timeout_query

    @staticmethod
    def healthy(gateway_url: str = "http://localhost:9100", timeout: float = 2.0) -> bool:
        try:
            with urllib.request.urlopen(
                f"{gateway_url.rstrip('/')}/health", timeout=timeout
            ) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError):
            return False

    def _post(self, path: str, body: Dict[str, Any], timeout: float) -> Any:
        data = json.dumps(body).encode("utf-8")
        req = urllib.request.Request(
            f"{self.gateway_url}{path}",
            data=data,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                raw = resp.read().decode("utf-8")
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="replace").strip()
            raise RuntimeError(f"gateway service error ({exc.code}): {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(
                f"cannot reach Fabric Gateway SDK service at {self.gateway_url} "
                f"(start it with blockchain/scripts/start-gateway-service.sh): {exc.reason}"
            ) from exc
        if not raw:
            return None
        try:
            return json.loads(raw)
        except json.JSONDecodeError:
            return raw

    def invoke(self, function: str, args: List[str]) -> str:
        result = self._post(
            "/invoke",
            {
                "org": self.org,
                "channel": self.channel,
                "chaincode": self.chaincode,
                "function": function,
                "args": args,
            },
            self.timeout_invoke,
        )
        if isinstance(result, dict):
            if not result.get("ok") or result.get("status") != "SUCCESS":
                raise RuntimeError(f"invoke {function} failed: {result}")
            return str(result.get("tx_id", ""))
        return str(result)

    def query(self, function: str, args: List[str]) -> Any:
        result = self._post(
            "/query",
            {
                "org": self.org,
                "channel": self.channel,
                "chaincode": self.chaincode,
                "function": function,
                "args": args,
            },
            self.timeout_query,
        )
        if isinstance(result, dict) and "ok" in result and "result" in result:
            return result["result"]
        return result

    def register_org(self) -> str:
        return self.invoke("RegisterOrg", [])

    def list_orgs(self) -> Any:
        return self.query("ListRegisteredOrgs", [])

    def request_admission(self, org_name: str, org_type: str) -> str:
        return self.invoke("RequestOrgAdmission", [org_name, org_type])

    def vote_on_admission(self, candidate_msp: str, verdict: str) -> str:
        return self.invoke("VoteOnOrgAdmission", [candidate_msp, verdict])

    def finalize_admission(self, candidate_msp: str) -> str:
        return self.invoke("FinalizeOrgAdmission", [candidate_msp])

    def query_admission(self, candidate_msp: str) -> Any:
        return self.query("QueryOrgAdmission", [candidate_msp])

    def submit_report(
        self, report_id: str, content_hash: str, label: str,
        confidence: float, model_version: str, timestamp: str,
    ) -> str:
        return self.invoke(
            "Submit",
            [report_id, content_hash, label,
             f"{confidence:.6f}", model_version, timestamp],
        )

    def submit_fact_check(self, report_id: str, outcome: str) -> str:
        return self.invoke("SubmitFactCheck", [report_id, outcome])

    def finalize_report(self, report_id: str) -> str:
        return self.invoke("FinalizeReport", [report_id])

    def expire_report(self, report_id: str) -> str:
        return self.invoke("ExpireReport", [report_id])

    def query_report(self, report_id: str) -> Any:
        return self.query("QueryReport", [report_id])

    def query_all(self) -> Any:
        return self.query("QueryAllReports", [])

    def history(self, report_id: str) -> Any:
        return self.query("QueryReportHistory", [report_id])


def _cli_main(argv: Optional[Sequence[str]] = None) -> int:
    import argparse
    import sys
    parser = argparse.ArgumentParser(
        prog="python -m blockchain",
        description="Interact with the misinformation chaincode via the peer CLI.",
    )
    sub = parser.add_subparsers(dest="command", required=True)
    reg = sub.add_parser("register-org", help="self-register org(s) during the genesis bootstrap")
    reg.add_argument("orgs", nargs="+", help="org1/org2/org3 ...")
    reg.add_argument("--endorsers", default="org1,org2",
                     help="comma-separated endorsing peers (default org1,org2)")
    sub.add_parser("register-all", help="register org1, org2, org3")
    ls = sub.add_parser("list-orgs", help="list registered stakeholder orgs")
    ls.add_argument("--org", default="org1")
    args = parser.parse_args(argv)
    if args.command == "register-org":
        endorsers = [e.strip() for e in args.endorsers.split(",") if e.strip()]
        for org in args.orgs:
            try:
                out = FabricBridge(org=org, endorsers=endorsers).register_org()
                print(f"registered {org}: {out}")
            except RuntimeError as exc:
                print(f"register {org} failed: {exc}", file=sys.stderr)
                return 1
        return 0
    if args.command == "register-all":
        for org in ("org1", "org2", "org3"):
            try:
                out = FabricBridge(org=org, endorsers=["org1", "org2"]).register_org()
                print(f"registered {org}: {out}")
            except RuntimeError as exc:
                print(f"register {org} failed: {exc}", file=sys.stderr)
                return 1
        return 0
    if args.command == "list-orgs":
        try:
            print(json.dumps(FabricBridge(org=args.org).list_orgs(), indent=2))
        except RuntimeError as exc:
            print(f"list-orgs failed: {exc}", file=sys.stderr)
            return 1
        return 0
    parser.print_help()
    return 2

if __name__ == "__main__":
    import sys as _sys
    _sys.exit(_cli_main())
