package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"math"
	"os"
	"strings"
	"time"

	"github.com/hyperledger/fabric-chaincode-go/v2/pkg/cid"
	"github.com/hyperledger/fabric-chaincode-go/v2/shim"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

const (
	statusPending           = "PENDING"
	statusUnderReview       = "UNDER_REVIEW"
	statusFinal             = "FINAL"
	statusRejected          = "REJECTED"
	statusExpired           = "EXPIRED"
	statusDisputed          = "DISPUTED"
	statusSuperseded        = "SUPERSEDED"
	admissionPending        = "PENDING"
	admissionAdmitted       = "ADMITTED"
	admissionRejected       = "REJECTED"
	defaultFoundingOrgLimit = 3
	factCheckWindow         = 72 * time.Hour

	// Fact-check outcome domain
	outcomeNonMisinformation = "0"
	outcomeMisinformation    = "1"
)

func isValidOutcome(v string) bool {
	return v == outcomeNonMisinformation || v == outcomeMisinformation
}

// requireRole checks that the transaction submitter carries one of the
// allowed ABAC roles in its X509 certificate attribute extension.
func requireRole(ctx contractapi.TransactionContextInterface, allowedRoles ...string) error {
	if _, err := transactionTime(ctx); err != nil {
		return err
	}
	role, found, err := cid.GetAttributeValue(ctx.GetStub(), "role")
	if err != nil {
		return fmt.Errorf("failed to read role attribute: %v", err)
	}
	if !found {
		return fmt.Errorf("no role attribute found on identity")
	}
	for _, r := range allowedRoles {
		if role == r {
			return nil
		}
	}
	return fmt.Errorf("identity has role %q; required one of %v", role, allowedRoles)
}

// FactCheck deliberately carries only what the on-chain consensus tally needs.
type FactCheck struct {
	CheckerMSP   string `json:"checker_msp"`
	Outcome      string `json:"outcome"`
	TxID         string `json:"txid"`
	EvidenceCID  string `json:"evidence_cid"`
	EvidenceHash string `json:"evidence_hash"`
	Timestamp    string `json:"timestamp"`
}

type ReportRecord struct {
	SchemaVersion     int         `json:"schema_version"`
	ReportID          string      `json:"id"`
	ContentHash       string      `json:"content_hash"`
	ProposedLabel     string      `json:"inference_label"`
	Confidence        float64     `json:"confidence"`
	ModelVersion      string      `json:"model_version"`
	Timestamp         string      `json:"timestamp"`
	SubmittedBy       string      `json:"submitted_by"`
	FactCheckDeadline string      `json:"fact_check_deadline"`
	Status            string      `json:"status"`
	FactChecks        []FactCheck `json:"fact_checks"`
	EligibleReviewers []string    `json:"eligible_reviewers"`
	RequiredVotes     int         `json:"required_votes"`
	ClaimCID          string      `json:"claim_cid"`
	Supersedes        string      `json:"supersedes,omitempty" metadata:",optional"`
	SupersededBy      string      `json:"superseded_by,omitempty" metadata:",optional"`
	FinalLabel        string      `json:"final_label,omitempty" metadata:",optional"`
	FinalizedBy       string      `json:"finalized_by,omitempty" metadata:",optional"`
	FinalizedAt       string      `json:"finalized_at,omitempty" metadata:",optional"`
}

type RegisteredOrg struct {
	MSPID        string `json:"mspid"`
	RegisteredAt string `json:"registered_at"`
}

// Vote is for genuine governance decisions
type Vote struct {
	VoterMSP string `json:"voter_msp"`
	Verdict  string `json:"verdict"`
	TxID     string `json:"txid"`
}

type OrgAdmissionRequest struct {
	CandidateMSP string `json:"candidate_msp"`
	OrgName      string `json:"org_name"`
	OrgType      string `json:"org_type"`
	RequestedAt  string `json:"requested_at"`
	Votes        []Vote `json:"votes"`
	Status       string `json:"status"`
	FinalizedBy  string `json:"finalized_by,omitempty" metadata:",optional"`
	FinalizedAt  string `json:"finalized_at,omitempty" metadata:",optional"`
}

type MisinformationContract struct {
	contractapi.Contract
}

func newReportKey(ctx contractapi.TransactionContextInterface, reportID string) (string, error) {
	return ctx.GetStub().CreateCompositeKey("pred", []string{reportID})
}

func newOrgKey(ctx contractapi.TransactionContextInterface, mspid string) (string, error) {
	return ctx.GetStub().CreateCompositeKey("org", []string{mspid})
}

func newAdmissionKey(ctx contractapi.TransactionContextInterface, mspid string) (string, error) {
	return ctx.GetStub().CreateCompositeKey("admission", []string{mspid})
}

func newConfigKey(ctx contractapi.TransactionContextInterface, name string) (string, error) {
	return ctx.GetStub().CreateCompositeKey("cfg", []string{name})
}

func (c *MisinformationContract) foundingOrgLimit(ctx contractapi.TransactionContextInterface) (int, error) {
	key, err := newConfigKey(ctx, "foundingOrgLimit")
	if err != nil {
		return 0, fmt.Errorf("failed to build config key: %v", err)
	}
	raw, err := ctx.GetStub().GetState(key)
	if err != nil {
		return 0, fmt.Errorf("failed to read foundingOrgLimit: %v", err)
	}
	if raw == nil {
		return defaultFoundingOrgLimit, nil
	}
	var val int
	if err := json.Unmarshal(raw, &val); err != nil {
		return 0, fmt.Errorf("failed to parse foundingOrgLimit: %v", err)
	}
	return val, nil
}

func (c *MisinformationContract) SetFoundingOrgLimit(
	ctx contractapi.TransactionContextInterface, limit int,
) (int, error) {
	if err := requireRole(ctx, "official"); err != nil {
		return 0, err
	}
	if limit < 1 {
		return 0, fmt.Errorf("founding org limit must be >= 1, got %d", limit)
	}
	orgs, err := c.getRegisteredOrgs(ctx)
	if err != nil {
		return 0, err
	}
	if len(orgs) != 0 {
		return 0, fmt.Errorf("founding configuration is locked after registration starts")
	}
	key, err := newConfigKey(ctx, "foundingOrgLimit")
	if err != nil {
		return 0, fmt.Errorf("failed to build config key: %v", err)
	}
	bytes, err := json.Marshal(limit)
	if err != nil {
		return 0, fmt.Errorf("failed to marshal limit: %v", err)
	}
	if err := ctx.GetStub().PutState(key, bytes); err != nil {
		return 0, fmt.Errorf("failed to write foundingOrgLimit: %v", err)
	}
	return limit, nil
}

func validateReportInput(reportID, contentHash, label, modelVersion, timestamp string, confidence float64) error {
	if strings.TrimSpace(reportID) == "" {
		return fmt.Errorf("report_id must not be empty")
	}
	if label != "0" && label != "1" {
		return fmt.Errorf("label must be \"0\" or \"1\", got %q", label)
	}
	if math.IsNaN(confidence) || math.IsInf(confidence, 0) || confidence < 0 || confidence > 1 {
		return fmt.Errorf("confidence must be in [0,1], got %f", confidence)
	}
	if strings.TrimSpace(modelVersion) == "" {
		return fmt.Errorf("model_version must not be empty")
	}
	if len(contentHash) != 64 {
		return fmt.Errorf("content_hash must be a 64-char sha256 hex digest, got %d chars", len(contentHash))
	}
	if _, err := hex.DecodeString(contentHash); err != nil {
		return fmt.Errorf("content_hash is not valid hex: %v", err)
	}
	if _, err := time.Parse(time.RFC3339, timestamp); err != nil {
		return fmt.Errorf("timestamp must be RFC3339 UTC, got %q: %v", timestamp, err)
	}
	return nil
}

func (c *MisinformationContract) getRegisteredOrgs(
	ctx contractapi.TransactionContextInterface,
) ([]*RegisteredOrg, error) {
	iter, err := ctx.GetStub().GetStateByPartialCompositeKey("org", nil)
	if err != nil {
		return nil, fmt.Errorf("failed to open org query: %v", err)
	}
	defer iter.Close()
	var orgs []*RegisteredOrg
	for iter.HasNext() {
		kv, err := iter.Next()
		if err != nil {
			return nil, fmt.Errorf("failed to advance org iterator: %v", err)
		}
		var org RegisteredOrg
		if err := json.Unmarshal(kv.Value, &org); err != nil {
			return nil, fmt.Errorf("failed to unmarshal org %q: %v", kv.Key, err)
		}
		orgs = append(orgs, &org)
	}
	return orgs, nil
}

func (c *MisinformationContract) isRegisteredOrg(
	ctx contractapi.TransactionContextInterface, mspid string,
) (bool, error) {
	key, err := newOrgKey(ctx, mspid)
	if err != nil {
		return false, err
	}
	state, err := ctx.GetStub().GetState(key)
	if err != nil {
		return false, fmt.Errorf("failed to read org state: %v", err)
	}
	return state != nil, nil
}

func quorumFor(registeredCount int) int {
	if registeredCount < 1 {
		return 0
	}
	return (2*registeredCount + 2) / 3
}

func deterministicTimestamp(ctx contractapi.TransactionContextInterface) string {
	t, _ := transactionTime(ctx) // every mutator validates this before writing
	return t.Format(time.RFC3339Nano)
}

func (c *MisinformationContract) RegisterOrg(
	ctx contractapi.TransactionContextInterface,
) (string, error) {
	if err := requireRole(ctx, "official"); err != nil {
		return "", err
	}
	mspid, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return "", fmt.Errorf("failed to read caller MSP: %v", err)
	}
	key, err := newOrgKey(ctx, mspid)
	if err != nil {
		return "", fmt.Errorf("failed to build org key: %v", err)
	}
	exists, err := ctx.GetStub().GetState(key)
	if err != nil {
		return "", fmt.Errorf("failed to read org state: %v", err)
	}
	if exists != nil {
		return mspid, nil
	}
	orgs, err := c.getRegisteredOrgs(ctx)
	if err != nil {
		return "", err
	}
	limit, err := c.foundingOrgLimit(ctx)
	if err != nil {
		return "", err
	}
	if len(orgs) >= limit {
		return "", fmt.Errorf(
			"genesis bootstrap closed (%d founding orgs already set); call RequestOrgAdmission instead",
			limit,
		)
	}
	org := RegisteredOrg{
		MSPID:        mspid,
		RegisteredAt: deterministicTimestamp(ctx),
	}
	orgBytes, err := json.Marshal(org)
	if err != nil {
		return "", fmt.Errorf("failed to marshal org: %v", err)
	}
	if err := ctx.GetStub().PutState(key, orgBytes); err != nil {
		return "", fmt.Errorf("failed to write org: %v", err)
	}
	return mspid, nil
}

func (c *MisinformationContract) ListRegisteredOrgs(
	ctx contractapi.TransactionContextInterface,
) ([]*RegisteredOrg, error) {
	return c.getRegisteredOrgs(ctx)
}

func (c *MisinformationContract) RequestOrgAdmission(
	ctx contractapi.TransactionContextInterface,
	orgName, orgType string,
) (string, error) {
	if err := requireRole(ctx, "official"); err != nil {
		return "", err
	}
	candidateMSP, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return "", fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, candidateMSP); err != nil {
		return "", err
	} else if ok {
		return "", fmt.Errorf("org %s is already a registered stakeholder", candidateMSP)
	}
	if strings.TrimSpace(orgName) == "" {
		return "", fmt.Errorf("org_name must not be empty")
	}
	key, err := newAdmissionKey(ctx, candidateMSP)
	if err != nil {
		return "", fmt.Errorf("failed to build admission key: %v", err)
	}
	if existing, err := ctx.GetStub().GetState(key); err != nil {
		return "", fmt.Errorf("failed to read admission state: %v", err)
	} else if existing != nil {
		var prev OrgAdmissionRequest
		if err := json.Unmarshal(existing, &prev); err != nil {
			return "", fmt.Errorf("failed to unmarshal admission request: %v", err)
		}
		if prev.Status == admissionPending {
			return "", fmt.Errorf("org %s already has a pending admission request", candidateMSP)
		}
	}
	req := OrgAdmissionRequest{
		CandidateMSP: candidateMSP,
		OrgName:      orgName,
		OrgType:      orgType,
		RequestedAt:  deterministicTimestamp(ctx),
		Votes:        []Vote{},
		Status:       admissionPending,
	}
	reqBytes, err := json.Marshal(req)
	if err != nil {
		return "", fmt.Errorf("failed to marshal admission request: %v", err)
	}
	if err := ctx.GetStub().PutState(key, reqBytes); err != nil {
		return "", fmt.Errorf("failed to write admission request: %v", err)
	}
	return candidateMSP, nil
}

func (c *MisinformationContract) VoteOnOrgAdmission(
	ctx contractapi.TransactionContextInterface,
	candidateMSP, verdict string,
) error {
	voterMSP, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, voterMSP); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("org %s is not a registered stakeholder; call RegisterOrg first", voterMSP)
	}
	if err := requireRole(ctx, "official"); err != nil {
		return err
	}
	if verdict != "0" && verdict != "1" {
		return fmt.Errorf("verdict must be \"0\" or \"1\", got %q", verdict)
	}
	if voterMSP == candidateMSP {
		return fmt.Errorf("org %s cannot vote on its own admission request", voterMSP)
	}
	key, err := newAdmissionKey(ctx, candidateMSP)
	if err != nil {
		return fmt.Errorf("failed to build admission key: %v", err)
	}
	reqBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return fmt.Errorf("failed to read admission state: %v", err)
	}
	if reqBytes == nil {
		return fmt.Errorf("no admission request found for %s", candidateMSP)
	}
	var req OrgAdmissionRequest
	if err := json.Unmarshal(reqBytes, &req); err != nil {
		return fmt.Errorf("failed to unmarshal admission request: %v", err)
	}
	if req.Status != admissionPending {
		return fmt.Errorf("admission request for %s is %s; only PENDING requests accept votes", candidateMSP, req.Status)
	}
	for _, v := range req.Votes {
		if v.VoterMSP == voterMSP {
			return fmt.Errorf("org %s has already voted on %s's admission", voterMSP, candidateMSP)
		}
	}
	req.Votes = append(req.Votes, Vote{
		VoterMSP: voterMSP,
		Verdict:  verdict,
		TxID:     ctx.GetStub().GetTxID(),
	})
	reqBytes, err = json.Marshal(req)
	if err != nil {
		return fmt.Errorf("failed to marshal admission request: %v", err)
	}
	if err := ctx.GetStub().PutState(key, reqBytes); err != nil {
		return fmt.Errorf("failed to write admission request: %v", err)
	}
	return nil
}

// FinalizeOrgAdmission admits the candidate once at least quorumFor(current registered org count) of its votes are "1" (admit)
func (c *MisinformationContract) FinalizeOrgAdmission(
	ctx contractapi.TransactionContextInterface,
	candidateMSP string,
) error {
	finalizerMSP, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, finalizerMSP); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("org %s is not a registered stakeholder; call RegisterOrg first", finalizerMSP)
	}
	if err := requireRole(ctx, "official"); err != nil {
		return err
	}
	key, err := newAdmissionKey(ctx, candidateMSP)
	if err != nil {
		return fmt.Errorf("failed to build admission key: %v", err)
	}
	reqBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return fmt.Errorf("failed to read admission state: %v", err)
	}
	if reqBytes == nil {
		return fmt.Errorf("no admission request found for %s", candidateMSP)
	}
	var req OrgAdmissionRequest
	if err := json.Unmarshal(reqBytes, &req); err != nil {
		return fmt.Errorf("failed to unmarshal admission request: %v", err)
	}
	if req.Status != admissionPending {
		return fmt.Errorf("admission request for %s is %s; only PENDING requests can be finalised", candidateMSP, req.Status)
	}
	orgs, err := c.getRegisteredOrgs(ctx)
	if err != nil {
		return err
	}
	needed := quorumFor(len(orgs))
	admitVotes := 0
	for _, v := range req.Votes {
		if v.Verdict == "1" {
			admitVotes++
		}
	}
	if admitVotes < needed {
		return fmt.Errorf(
			"admission request for %s has %d admit vote(s); at least %d (of %d registered orgs) are required to finalise",
			candidateMSP, admitVotes, needed, len(orgs),
		)
	}
	req.Status = admissionAdmitted
	req.FinalizedBy = finalizerMSP
	req.FinalizedAt = deterministicTimestamp(ctx)
	reqBytes, err = json.Marshal(req)
	if err != nil {
		return fmt.Errorf("failed to marshal admission request: %v", err)
	}
	if err := ctx.GetStub().PutState(key, reqBytes); err != nil {
		return fmt.Errorf("failed to write finalised admission request: %v", err)
	}
	orgKey, err := newOrgKey(ctx, candidateMSP)
	if err != nil {
		return fmt.Errorf("failed to build org key: %v", err)
	}
	org := RegisteredOrg{MSPID: candidateMSP, RegisteredAt: deterministicTimestamp(ctx)}
	orgBytes, err := json.Marshal(org)
	if err != nil {
		return fmt.Errorf("failed to marshal org: %v", err)
	}
	if err := ctx.GetStub().PutState(orgKey, orgBytes); err != nil {
		return fmt.Errorf("failed to write newly admitted org: %v", err)
	}
	return nil
}

func (c *MisinformationContract) QueryOrgAdmission(
	ctx contractapi.TransactionContextInterface,
	candidateMSP string,
) (*OrgAdmissionRequest, error) {
	key, err := newAdmissionKey(ctx, candidateMSP)
	if err != nil {
		return nil, fmt.Errorf("failed to build admission key: %v", err)
	}
	reqBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return nil, fmt.Errorf("failed to read admission state: %v", err)
	}
	if reqBytes == nil {
		return nil, fmt.Errorf("no admission request found for %s", candidateMSP)
	}
	var req OrgAdmissionRequest
	if err := json.Unmarshal(reqBytes, &req); err != nil {
		return nil, fmt.Errorf("failed to unmarshal admission request: %v", err)
	}
	return &req, nil
}

func (c *MisinformationContract) QueryReport(
	ctx contractapi.TransactionContextInterface,
	reportID string,
) (*ReportRecord, error) {
	key, err := newReportKey(ctx, reportID)
	if err != nil {
		return nil, fmt.Errorf("failed to build key: %v", err)
	}
	recordBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return nil, fmt.Errorf("failed to read state: %v", err)
	}
	if recordBytes == nil {
		return nil, fmt.Errorf("no report found for %s", reportID)
	}
	var record ReportRecord
	if err := json.Unmarshal(recordBytes, &record); err != nil {
		return nil, fmt.Errorf("failed to unmarshal record: %v", err)
	}
	return &record, nil
}

type ReportHistoryEntry struct {
	TxID      string        `json:"tx_id"`
	Timestamp string        `json:"timestamp"`
	IsDelete  bool          `json:"is_delete"`
	Record    *ReportRecord `json:"record,omitempty" metadata:",optional"`
}

// QueryReportHistory returns every transaction that ever wrote this report's ledger key (submission, each fact-check, finalize/expire)
func (c *MisinformationContract) QueryReportHistory(
	ctx contractapi.TransactionContextInterface,
	reportID string,
) ([]*ReportHistoryEntry, error) {
	key, err := newReportKey(ctx, reportID)
	if err != nil {
		return nil, fmt.Errorf("failed to build key: %v", err)
	}
	iter, err := ctx.GetStub().GetHistoryForKey(key)
	if err != nil {
		return nil, fmt.Errorf("failed to read history for %s: %v", reportID, err)
	}
	defer iter.Close()
	var entries []*ReportHistoryEntry
	for iter.HasNext() {
		mod, err := iter.Next()
		if err != nil {
			return nil, fmt.Errorf("failed to advance history iterator: %v", err)
		}
		entry := &ReportHistoryEntry{
			TxID:     mod.TxId,
			IsDelete: mod.IsDelete,
		}
		if mod.Timestamp != nil {
			entry.Timestamp = mod.Timestamp.AsTime().UTC().Format(time.RFC3339)
		}
		if !mod.IsDelete && len(mod.Value) > 0 {
			var record ReportRecord
			if err := json.Unmarshal(mod.Value, &record); err == nil {
				entry.Record = &record
			}
		}
		entries = append(entries, entry)
	}
	if entries == nil {
		return nil, fmt.Errorf("no report found for %s", reportID)
	}
	return entries, nil
}

func (c *MisinformationContract) QueryAllReports(
	ctx contractapi.TransactionContextInterface,
) ([]*ReportRecord, error) {
	resultsIter, err := ctx.GetStub().GetStateByPartialCompositeKey("pred", nil)
	if err != nil {
		return nil, fmt.Errorf("failed to open composite key query: %v", err)
	}
	defer resultsIter.Close()
	var records []*ReportRecord
	for resultsIter.HasNext() {
		kv, err := resultsIter.Next()
		if err != nil {
			return nil, fmt.Errorf("failed to advance results iterator: %v", err)
		}
		var record ReportRecord
		if err := json.Unmarshal(kv.Value, &record); err != nil {
			return nil, fmt.Errorf("failed to unmarshal record %q: %v", kv.Key, err)
		}
		records = append(records, &record)
	}
	return records, nil
}

func main() {
	chaincode, err := newMisinformationChaincode()
	if err != nil {
		fmt.Printf("Error creating misinformation chaincode: %v\n", err)
		panic(err)
	}
	var startErr error
	if address := os.Getenv("CHAINCODE_SERVER_ADDRESS"); address != "" {
		cert, err := os.ReadFile(os.Getenv("CHAINCODE_TLS_CERT"))
		if err != nil {
			panic(fmt.Errorf("chaincode TLS certificate: %w", err))
		}
		key, err := os.ReadFile(os.Getenv("CHAINCODE_TLS_KEY"))
		if err != nil {
			panic(fmt.Errorf("chaincode TLS key: %w", err))
		}
		server := &shim.ChaincodeServer{CCID: os.Getenv("CHAINCODE_ID"), Address: address, CC: chaincode, TLSProps: shim.TLSProperties{Cert: cert, Key: key}}
		startErr = server.Start()
	} else {
		startErr = chaincode.Start()
	}
	if err := startErr; err != nil {
		fmt.Printf("Error starting misinformation chaincode: %v\n", err)
		panic(err)
	}
}
