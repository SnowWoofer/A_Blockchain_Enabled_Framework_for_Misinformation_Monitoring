package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
	"strings"
	"time"
)

const (
	statusPending           = "PENDING"
	statusFinal             = "FINAL"
	statusRejected          = "REJECTED"
	statusExpired           = "EXPIRED"
	admissionPending        = "PENDING"
	admissionAdmitted       = "ADMITTED"
	admissionRejected       = "REJECTED"
	defaultFoundingOrgLimit = 3
	votingWindow            = 72 * time.Hour
)

type Vote struct {
	VoterMSP string `json:"voter_msp"`
	Verdict  string `json:"verdict"`
	TxID     string `json:"txid"`
}

type ReportRecord struct {
	ReportID       string  `json:"report_id"`
	Language       string  `json:"language"`
	ContentHash    string  `json:"content_hash"`
	ProposedLabel  string  `json:"proposed_label"`
	Confidence     float64 `json:"confidence"`
	ModelVersion   string  `json:"model_version"`
	Timestamp      string  `json:"timestamp"`
	SubmittedBy    string  `json:"submitted_by"`
	OffChainURI    string  `json:"off_chain_uri"`
	VotingDeadline string  `json:"voting_deadline"`
	Status         string  `json:"status"`
	Votes          []Vote  `json:"votes"`
	FinalizedBy    string  `json:"finalized_by,omitempty" metadata:",optional"`
	FinalizedAt    string  `json:"finalized_at,omitempty" metadata:",optional"`
}

type RegisteredOrg struct {
	MSPID        string `json:"mspid"`
	RegisteredAt string `json:"registered_at"`
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

func newVoteKey(ctx contractapi.TransactionContextInterface, reportID, mspid string) (string, error) {
	return ctx.GetStub().CreateCompositeKey("vote", []string{reportID, mspid})
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
	if limit < 1 {
		return 0, fmt.Errorf("founding org limit must be >= 1, got %d", limit)
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

func validateReportInput(reportID, language, contentHash, label, modelVersion, timestamp string, confidence float64) error {
	if strings.TrimSpace(reportID) == "" {
		return fmt.Errorf("report_id must not be empty")
	}
	if language != "nso" && language != "zul" && language != "eng" {
		return fmt.Errorf("language must be one of nso/zul/eng, got %q", language)
	}
	if label != "0" && label != "1" {
		return fmt.Errorf("label must be \"0\" or \"1\", got %q", label)
	}
	if confidence < 0 || confidence > 1 {
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
	if txTS, err := ctx.GetStub().GetTxTimestamp(); err == nil && txTS != nil {
		return txTS.AsTime().UTC().Format(time.RFC3339)
	}
	return time.Now().UTC().Format(time.RFC3339)
}

func (c *MisinformationContract) RegisterOrg(
	ctx contractapi.TransactionContextInterface,
) (string, error) {
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

func (c *MisinformationContract) SubmitReport(
	ctx contractapi.TransactionContextInterface,
	reportID, language, contentHash, label string,
	confidence float64,
	modelVersion, timestamp, offChainURI string,
) error {
	if err := validateReportInput(reportID, language, contentHash, label, modelVersion, timestamp, confidence); err != nil {
		return err
	}
	if strings.TrimSpace(offChainURI) == "" {
		return fmt.Errorf("off_chain_uri must not be empty")
	}
	submittedBy, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, submittedBy); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("org %s is not a registered stakeholder; call RegisterOrg first", submittedBy)
	}
	key, err := newReportKey(ctx, reportID)
	if err != nil {
		return fmt.Errorf("failed to build key: %v", err)
	}
	if exists, _ := ctx.GetStub().GetState(key); exists != nil {
		return fmt.Errorf("report %s already exists (immutable once finalised)", reportID)
	}
	deadline := time.Now().UTC().Add(votingWindow).Format(time.RFC3339)
	if txTS, err := ctx.GetStub().GetTxTimestamp(); err == nil && txTS != nil {
		deadline = txTS.AsTime().Add(votingWindow).UTC().Format(time.RFC3339)
	}
	record := ReportRecord{
		ReportID:       reportID,
		Language:       language,
		ContentHash:    contentHash,
		ProposedLabel:  label,
		Confidence:     confidence,
		ModelVersion:   modelVersion,
		Timestamp:      timestamp,
		SubmittedBy:    submittedBy,
		OffChainURI:    offChainURI,
		VotingDeadline: deadline,
		Status:         statusPending,
		Votes:          []Vote{},
	}
	recordBytes, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}
	if err := ctx.GetStub().PutState(key, recordBytes); err != nil {
		return fmt.Errorf("failed to write record: %v", err)
	}
	return nil
}

func (c *MisinformationContract) ExpireReport(
	ctx contractapi.TransactionContextInterface,
	reportID string,
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
	key, err := newReportKey(ctx, reportID)
	if err != nil {
		return fmt.Errorf("failed to build key: %v", err)
	}
	recordBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return fmt.Errorf("failed to read state: %v", err)
	}
	if recordBytes == nil {
		return fmt.Errorf("no report found for %s", reportID)
	}
	var record ReportRecord
	if err := json.Unmarshal(recordBytes, &record); err != nil {
		return fmt.Errorf("failed to unmarshal record: %v", err)
	}
	if record.Status != statusPending {
		return fmt.Errorf("report %s is %s; only PENDING reports can expire", reportID, record.Status)
	}
	expired, err := isPastDeadline(record.VotingDeadline)
	if err != nil {
		return err
	}
	if !expired {
		return fmt.Errorf("report %s is still within its voting window (deadline %s)", reportID, record.VotingDeadline)
	}
	record.Status = statusExpired
	record.FinalizedBy = finalizerMSP
	record.FinalizedAt = deterministicTimestamp(ctx)
	recordBytes, err = json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}
	if err := ctx.GetStub().PutState(key, recordBytes); err != nil {
		return fmt.Errorf("failed to write expired record: %v", err)
	}
	return nil
}

func isPastDeadline(rfc3339 string) (bool, error) {
	deadline, err := time.Parse(time.RFC3339, rfc3339)
	if err != nil {
		return false, fmt.Errorf("invalid voting_deadline %q: %v", rfc3339, err)
	}
	return time.Now().UTC().After(deadline), nil
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
	chaincode, err := contractapi.NewChaincode(&MisinformationContract{})
	if err != nil {
		fmt.Printf("Error creating misinformation chaincode: %v\n", err)
		panic(err)
	}
	if err := chaincode.Start(); err != nil {
		fmt.Printf("Error starting misinformation chaincode: %v\n", err)
		panic(err)
	}
}
