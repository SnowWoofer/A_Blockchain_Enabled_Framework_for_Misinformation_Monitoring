"""Python -> chaincode bridge (Track C, C1).

Submits AI pipeline output (report_id, language, label, confidence, ...) to the
Hyperledger Fabric ledger via the peer CLI (CLI shell-out — the PoC option
documented in the plan; no Fabric SDK dependency needed, works in Colab/Kaggle
when the Fabric binaries are bundled).

v2 contract changes:
  - Reports are keyed by a caller-supplied report_id.
  - SubmitReport takes report_id + off_chain_uri (raw text never sent).
  - Suspended reports have a voting_deadline and can expire (EXPIRED).
  - Org membership is two-tier: genesis RegisterOrg, then admission voting.

Contract: `blockchain/chaincode/misinformation/go/misinformation.go`
Data model: `blockchain/chaincode/misinformation/DATA_MODEL.md`
"""
from __future__ import annotations

import hashlib
import json
import os
import subprocess
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Sequence


@dataclass
class Prediction:
    """One model prediction ready to be anchored on-chain (raw text is NOT sent)."""

    report_id: str
    language: str  # nso | zul | eng
    label: str  # "0" reliable / "1" misinformation
    confidence: float  # [0,1]
    model_version: str
    timestamp: str  # RFC3339 UTC
    content_hash: str = field(default="")

    def __post_init__(self) -> None:
        if not self.content_hash:
            self.content_hash = self.hash_content("")
        if not (0.0 <= self.confidence <= 1.0):
            raise ValueError(f"confidence must be in [0,1], got {self.confidence}")
        if self.label not in ("0", "1"):
            raise ValueError(f"label must be '0' or '1', got {self.label!r}")
        if self.language not in ("nso", "zul", "eng"):
            raise ValueError(f"language must be nso/zul/eng, got {self.language!r}")

    @staticmethod
    def hash_content(text: str) -> str:
        """sha256 hex digest of the raw text — matches ComputeContentHash on-chain."""
        return hashlib.sha256(text.encode("utf-8")).hexdigest()


class FabricBridge:
    """Thin wrapper around the `peer` CLI for the misinformation chaincode."""

    def __init__(
        self,
        channel: str = "mychannel",
        chaincode: str = "misinformation",
        peer_bin: str = "peer",
        org: str = "org1",
        test_network: str = os.path.expanduser("~/fabric-samples/test-network"),
        endorsers: Optional[Sequence[str]] = None,
    ) -> None:
        """Bridge to the misinformation chaincode.

        `org` selects the signing identity (org1/org2/org3). `endorsers` selects
        which orgs' peers must endorse an invoke — with a 2/3 policy you need at
        least two of them. Defaults to org1 + org2.
        """
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

    # ---- Org membership (Tier-2 stakeholder status) --------------------------

    def register_org(self) -> str:
        """Enroll the calling org during the genesis bootstrap window."""
        return self.invoke("RegisterOrg", [])

    def list_orgs(self) -> Any:
        return self.query("ListRegisteredOrgs", [])

    def request_admission(self, org_name: str, org_type: str) -> str:
        """Request Tier-2 stakeholder status (PENDING admission request)."""
        return self.invoke("RequestOrgAdmission", [org_name, org_type])

    def vote_on_admission(self, candidate_msp: str, verdict: str) -> str:
        """Vote 'accept'/'reject' on an org's admission request."""
        return self.invoke("VoteOnOrgAdmission", [candidate_msp, verdict])

    def finalize_admission(self, candidate_msp: str) -> str:
        """Finalize an admission once 2/3 of registered orgs have voted."""
        return self.invoke("FinalizeOrgAdmission", [candidate_msp])

    def query_admission(self, candidate_msp: str) -> Any:
        return self.query("QueryOrgAdmission", [candidate_msp])

    # ---- Reports -------------------------------------------------------------

    def submit_report(
        self, report_id: str, language: str, content_hash: str, label: str,
        confidence: float, model_version: str, timestamp: str, off_chain_uri: str,
    ) -> str:
        """Submit a report for stakeholder review (PENDING)."""
        return self.invoke(
            "SubmitReport",
            [report_id, language, content_hash, label,
             f"{confidence:.6f}", model_version, timestamp, off_chain_uri],
        )

    def cast_vote(self, report_id: str, verdict: str) -> str:
        """Vote 'accept' or 'reject' on a PENDING report."""
        return self.invoke("CastVote", [report_id, verdict])

    def finalize_report(self, report_id: str) -> str:
        """Finalize a report once >= 2/3 of registered orgs have voted."""
        return self.invoke("FinalizeReport", [report_id])

    def expire_report(self, report_id: str) -> str:
        """Mark a PENDING report EXPIRED once its voting deadline passes."""
        return self.invoke("ExpireReport", [report_id])

    def query_report(self, report_id: str) -> Any:
        return self.query("QueryReport", [report_id])

    def query_all(self) -> Any:
        return self.query("QueryAllReports", [])

    def count(self) -> Any:
        return self.query("GetReportCount", [])

    def votes(self, report_id: str) -> Any:
        return self.query("QueryVotes", [report_id])

    def history(self, report_id: str) -> Any:
        return self.query("QueryReportHistory", [report_id])


def submit_pipeline_output(
    rows: List[Dict[str, Any]], *, language: str, model_version: str,
    bridge: FabricBridge, base_uri: str = "https://server.example/api/reports",
) -> List[str]:
    """Batch-submit pipeline output rows as PENDING reports.

    Rows provide [{report_id, text, label, confidence}]. `content_hash` is the
    sha256 of the raw text (for a stronger whole-object hash, build the report
    with `report.build` and pass that hash instead).
    """
    txids: List[str] = []
    for row in rows:
        report_id = str(row["report_id"])
        channel_hash = Prediction.hash_content(row["text"])
        txids.append(bridge.submit_report(
            report_id, language, channel_hash, str(row["label"]),
            float(row["confidence"]), model_version,
            row.get("timestamp", _rfc3339_now()),
            f"{base_uri}/{report_id}",
        ))
    return txids


def _rfc3339_now() -> str:
    import datetime as _dt

    return _dt.datetime.now(_dt.timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")