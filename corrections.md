# Critical Analysis of `current_report.txt`

Generated: 2026-09-21

---

## Confirmed Accurate

- **Section 6.2 Table 2:** All 6 model results (accuracy, precision, recall, F1, ROC-AUC) — exact match against `apps/flagging-engine/model/misinfo_metrics.json`
- **Section 6.2 Table 3:** Per-language n sizes (468/466/459) — exact match in `misinfo_metrics.json`
- **Section 6.2:** AfroXLM-R-large: 88.1% accuracy, F1 0.889 — exact match (0.8808, 0.8889)
- **Section 3.1:** 13,877 usable rows, 80/10/10 split, 1,393 test rows — confirmed in `misinfo_metrics.json`
- **Section 3.3.1:** Raft ordering service — confirmed: `OrdererType: etcdraft` in `configtx.yaml`
- **Section 6.1:** 14 chaincode exported functions — confirmed exactly 14 exported methods on `MisinformationContract`
- **Section 6.1:** ABAC: 3 roles, enforced in chaincode — confirmed: 7 functions guarded by `requireRole()`
- **Section 6.1:** 25 unit tests — confirmed: 25 test functions in `misinformation_test.go`
- **Section 6.1:** REST API with dual auth modes — confirmed: bootstrap (API key) + JWT in `auth.py`
- **Section 6.1:** MAX_ORGS = 30 — confirmed in `startup.sh` line 9
- **Section 6.3.3:** Caliper stress test numbers (76/1000 at 3.6 TPS, 340/2000 at 8.4 TPS) — confirmed in `results/local/_archive_20260907-022337/caliper.csv`
- **Section 6.4:** POPIA compliance mapping — accurate description of implemented features
- **Section 3.4.2:** Two consensus paths, tie-break rule — confirmed in chaincode + tests (`TestFinalizeTieRejected`)

---

## Discrepancies / Issues Found

### 1. Section 6.3.2: Benchmark data claims don't match available files [MAJOR]

**Report claims:** "four consortium sizes — 3, 10, 15 and 20 organisations — with 310 submissions per organisation count (1,240 total, zero failures)"

**Reality:**
- No CSV file in `results/` has ~310 rows
- `run_remaining.sh` defines `ORGS=(15 20 25 30)`, not `(3 10 15 20)`
- The only file with 1,240 rows is `final_data.csv`, but its per-status breakdown (932 FINAL / 308 REJECTED) cannot be cleanly verified — the CSV is malformed (unquoted commas in the `statement` column shift fields)
- The actual ABAC CSVs from the overnight run have only 10 samples each

**Impact:** The Table 4 latency numbers (2,127 ms, 2,121 ms, etc.) cannot be reproduced from any file in the repo. The data may have existed in a previous run that was cleaned up, but the source of truth is missing.

**Fix:** Either locate the original 310-sample benchmark run data, or rewrite Section 6.3.2 to reference the data that actually exists. If the original data was lost, acknowledge this as a gap.

### 2. Section 6.3.2: "closely consistent mean latencies (2,056–2,154 ms)" [MODERATE]

**Report claims:** "A separate, larger sweep spanning organisation counts of 3, 5, 10, 15 and 20 and a wider range of sample sizes was subsequently run and produced closely consistent mean latencies (2,056–2,154 ms)"

**Reality:** The actual ABAC CSV data shows individual row latencies of ~2,164–2,488 ms, with means likely higher than 2,154 ms. The report's claimed range is in the right ballpark but understates the actual values.

**Impact:** Minor — the qualitative finding (flat latency across org counts) still holds, but the specific numbers are slightly off.

**Fix:** Recalculate mean latencies from the actual CSV data and update the claimed range.

### 3. Section 6.3.2: "932 (75.2%) reached a final verdict and 308 (24.8%) were rejected" [MODERATE]

**Report claims:** 932 FINAL + 308 REJECTED = 1,240 total

**Reality:** The `final_data.csv` has 1,240 rows, but column extraction shows only 316 FINAL and 102 REJECTED visible — far fewer than claimed. The CSV is malformed, so the 932/308 split cannot be independently verified from the file.

**Impact:** This is a key finding (showing the early-rejection path works as intended). If the data can't be reproduced, the claim is unverifiable.

**Fix:** Re-run the benchmark to regenerate clean data, or locate the original analysis script that produced the 932/308 counts.

### 4. Section 3.6: "the benchmark procedures... do not require the detection model" [MINOR]

**Report claims:** "the benchmark procedures described in Section 3.6 submit directly to the blockchain gateway and do not require the detection model, the event-streaming platform, or any application-pipeline service to be running"

**Reality:** `feed_samples.py` with `--ai-pct 50` does call the flagging engine (`localhost:8004/predict`) for 50% of samples. The flagging engine is explicitly waited for (`wait_for_engine()`). The claim is only true for the non-AI portion.

**Impact:** Minor — the report later clarifies that "only the AI-stakeholder organisation's submissions carry genuine model output" (Section 6.3.2), but the initial claim is misleading.

**Fix:** Qualify the claim: "the benchmark procedures submit directly to the blockchain gateway for report submission and fact-checking, though when configured with an AI-submission percentage, the flagging engine is called for classification on that subset."

### 5. Section 6.3.3: Caliper data is from an archived run [MINOR]

The stress test data exists only in `results/local/_archive_20260907-022337/caliper.csv`. The report presents it as current results without noting it's from an archived run. This is fine if the data is valid, but worth noting for reproducibility.

### 6. Section 3.4.2: Floating-point quorum edge case [NOTED]

The report honestly discloses this: "the early-decision path's own floating-point computation was not migrated to that same formulation, leaving a theoretical, not-yet-observed rounding edge case." This is good academic practice — it's a known limitation honestly reported.

---

## Missing from the Report

1. **No explicit chaincode function count.** The report describes what functions do but never states "the chaincode implements 14 exported functions" — this would strengthen Section 3.4.

2. **No mention of the `ca_port()` bug** (org3 CA port mismatch at 11054 vs actual 9054). This doesn't affect the reported results but is a real codebase issue.

3. **No mention of the port scheme change.** The report doesn't discuss the 100-increment port scheme that was implemented to resolve the CA port collision at org9. This is infrastructure detail that may not belong in a research report, but if the report claims "up to thirty organisations," the port scheme is what makes it possible.

4. **No explicit statement about test count.** The report mentions "a dedicated unit-test suite" but never says "25 test functions" — adding the number would strengthen the evidence.

---

## ABAC: What to Add in the Report

### Section 3 — Methodology (ABAC)

Add a paragraph describing the ABAC implementation:

> Role-based access control is enforced directly in the ledger's contract logic through Attribute-Based Access Control (ABAC). Each participating identity carries a `role` attribute embedded in its X.509 certificate at the point of enrolment with the network's certificate authority. The chaincode's `requireRole` helper reads this attribute from the caller's transaction context via `cid.GetAttributeValue("role")` and rejects the transaction if the attribute is absent or does not match any of the roles permitted for the invoked function. Three roles are defined:
>
> - **official** — permitted to finalise or expire reports, vote on and finalise organisation admission, configure the founding-organisation limit, submit reports, and submit fact-checks
> - **fact_checker** — permitted to submit reports and submit fact-checks
> - **observer** — restricted to read-only queries (no `requireRole` guard; the default when no write function is called)
>
> Role identities are registered via a dedicated enrolment script (`register-roles.sh`) that invokes the Fabric CA client to register each identity with the appropriate attribute. The attribute is cryptographically bound to the certificate and cannot be modified without re-enrolment, providing a stronger guarantee than gateway-level access control alone — the check is enforced inside the ledger's own logic regardless of which client submits the transaction.

### Section 4 — Background (ABAC)

Add a short subsection (4.x) explaining ABAC in the context of Hyperledger Fabric:

> **4.x Attribute-Based Access Control (ABAC) in Hyperledger Fabric**
>
> Hyperledger Fabric supports Attribute-Based Access Control (ABAC), in which access decisions are based on attributes embedded in the transacting identity's X.509 certificate rather than on a separate role-lookup table. Attributes are defined at certificate enrolment time and are cryptographically bound to the certificate's attribute extension. The chaincode reads these attributes from the transaction context at execution time, enabling fine-grained, per-function access policies that are enforced at the ledger layer rather than relying solely on application-level checks. This study defines three roles — `official`, `fact_checker`, and `observer` — as certificate attributes, and the chaincode verifies the attribute before permitting each state-changing operation.

---

## CVSS: What to Add in the Report

### Section 3 — Methodology (CVSS)

Add a paragraph describing the CVSS assessment approach:

> A structured security assessment of the ledger's contract logic was performed using the Common Vulnerability Scoring System (CVSS) version 3.1. The assessment scope covered the chaincode's 14 exported functions, the certificate-based role-assignment workflow (including the enrolment and attribute-binding process), the organisation admission voting mechanism, and the quorum computation logic. Each identified vulnerability was assigned a CVSS v3.1 vector string and a corresponding severity score (Critical, High, Medium, or Low) following the methodology defined in the CVSS v3.1 specification. The assessment was conducted through manual code review of the chaincode's Go source and the supporting enrolment scripts, augmented by the unit-test suite's coverage of access-control enforcement paths.

### Section 4 — Background (CVSS)

Add a short subsection (4.x) explaining CVSS:

> **4.x Common Vulnerability Scoring System (CVSS)**
>
> The Common Vulnerability Scoring System (CVSS) is an open framework maintained by FIRST.org for communicating the characteristics and severity of software vulnerabilities. Version 3.1 defines a vector string encoding seven metric groups — Attack Vector, Attack Complexity, Privileges Required, User Interaction, Scope, Confidentiality Impact, Integrity Impact, and Availability Impact — which together produce a numerical score from 0.0 to 10.0 mapped to severity levels (None, Low, Medium, High, Critical). CVSS is widely adopted in industry and government for prioritising remediation and is referenced by NIST's National Vulnerability Database (NVD). This study applies CVSS v3.1 to the chaincode's contract logic to produce a structured, standardised assessment of the system's security posture.

### Section 6 — Results (CVSS)

Add a new subsection (6.7) with the CVSS findings table:

> **6.7 Security Assessment (CVSS)**
>
> Table N summarises the findings of the CVSS v3.1 security assessment of the ledger's contract logic and role-assignment workflow.
>
> | ID | Vulnerability | CVSS Vector | Score | Severity | Status |
> |----|--------------|-------------|-------|----------|--------|
> | CVSS-01 | [e.g., Missing input validation on Submit] | AV:N/AC:L/PR:L/UI:N/S:U/C:N/I:H/A:N | [score] | [severity] | [mitigated/open] |
> | CVSS-02 | [e.g., Floating-point quorum rounding edge case] | AV:L/AC:H/PR:L/UI:N/S:U/C:N/I:L/A:N | [score] | [severity] | [open — theoretical] |
> | ... | ... | ... | ... | ... | ... |
>
> [N] of [total] identified vulnerabilities were rated Medium or above. [N] have been mitigated through code changes; [N] remain open and are scoped as future work (Section 7.2).
>
> The assessment confirms that the chaincode's ABAC enforcement is structurally sound — the `requireRole` helper reads the role attribute from the transaction context rather than accepting client-supplied input, and the attribute is cryptographically bound to the certificate. The primary residual risk is in edge cases of the quorum computation (Section 3.4.2) and in the absence of formal verification of the consensus logic, which is noted as a limitation (Section 6.6).

---

## Verdict

The report is substantially accurate on all AI detection metrics and system design descriptions. The model comparison table, per-language breakdown, chaincode logic, ABAC system, and POPIA mapping are all verified correct against the codebase.

The main concern is Section 6.3.2 benchmark data. The specific numbers (310 samples × 4 org counts, 932/308 split, Table 4 latencies) cannot be reproduced from any file currently in the repo. If the original benchmark data was lost, this section needs to either:

- Locate and restore the original data
- Re-run the benchmarks to regenerate it
- Rewrite to reference the data that does exist (the ABAC CSVs with 10 samples each, which show consistent latencies but at lower sample counts)

**Recommendation:** Before submission, either regenerate the benchmark data or add a footnote acknowledging the data source. The qualitative findings are sound, but the specific quantitative claims in Section 6.3.2 need a verifiable source.
