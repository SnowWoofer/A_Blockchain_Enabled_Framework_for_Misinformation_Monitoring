// Misinformation monitoring chaincode.
//
// Consortium review workflow: multiple fact-checking organisations join, submit
// reports, and every stakeholder organisation votes on each report's legitimacy.
// Once >= 2/3 of registered orgs have voted, the report is finalised (or expires)
// and becomes an immutable ledger record.
//
// Statuses: PENDING -> FINAL (accepted) | REJECTED | EXPIRED.
//
// v2 additions:
//   - Reports are keyed by a caller-supplied report_id (not language+row_id).
//   - Each PENDING report carries off_chain_uri (full report lives off-chain, raw
//     text never touches the ledger) and a voting_deadline = tx-time + 72h.
//   - Org membership is two-tier: Tier-1 channel membership (Fabric) plus an
//     on-chain admission vote. RegisterOrg only works during a small genesis
//     bootstrap window; after that new orgs apply via RequestOrgAdmission and are
//     voted in by existing registered orgs (reuses the 2/3 quorum).
//
// Deliberately simple, modular, platform-agnostic audit layer (per proposal).
// See DATA_MODEL.md.
package main

import (
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

const (
	statusPending  = "PENDING"
	statusFinal    = "FINAL"
	statusRejected = "REJECTED"
	statusExpired  = "EXPIRED"

	// admissionPending/Admitted/Rejected track org admission requests.
	admissionPending  = "PENDING"
	admissionAdmitted = "ADMITTED"
	admissionRejected = "REJECTED"

	// foundingOrgLimit is the genesis bootstrap window: while fewer than this many
	// orgs are registered, self-service RegisterOrg is allowed. Once the limit is
	// reached all new orgs must go through the admission-vote workflow.
	foundingOrgLimit = 3

	// votingWindow is how long a PENDING report stays open for votes.
	votingWindow = 72 * time.Hour
)

// Vote is one organisation's verdict on a report (or an org admission request).
type Vote struct {
	VoterMSP string `json:"voter_msp"`
	Verdict  string `json:"verdict"` // "accept" | "reject"
	TxID     string `json:"txid"`
}

// ReportRecord is the on-chain audit record for one report.
// Raw text is never stored; only an integrity hash plus an off-chain URI.
type ReportRecord struct {
	ReportID       string  `json:"report_id"`
	Language       string  `json:"language"`
	ContentHash    string  `json:"content_hash"`
	ProposedLabel  string  `json:"proposed_label"` // "0" reliable | "1" misinformation (claim)
	Confidence     float64 `json:"confidence"`
	ModelVersion   string  `json:"model_version"`
	Timestamp      string  `json:"timestamp"`
	SubmittedBy    string  `json:"submitted_by"`
	OffChainURI    string  `json:"off_chain_uri"`
	VotingDeadline string  `json:"voting_deadline"` // RFC3339, set at submission
	Status         string  `json:"status"`          // PENDING | FINAL | REJECTED | EXPIRED
	Votes          []Vote  `json:"votes"`
	FinalizedBy    string  `json:"finalized_by,omitempty" metadata:",optional"`
	FinalizedAt    string  `json:"finalized_at,omitempty" metadata:",optional"`
}

// RegisteredOrg is a stakeholder organisation enrolled on-chain.
type RegisteredOrg struct {
	MSPID        string `json:"mspid"`
	RegisteredAt string `json:"registered_at"`
}

// OrgAdmissionRequest is a candidate organisation's request for Tier-2
// stakeholder status, voted on by existing registered orgs.
type OrgAdmissionRequest struct {
	CandidateMSP string `json:"candidate_msp"`
	OrgName      string `json:"org_name"`
	OrgType      string `json:"org_type"` // "fact-checker" | "media-monitor" | "ngo"
	RequestedAt  string `json:"requested_at"`
	Votes        []Vote `json:"votes"`
	Status       string `json:"status"` // PENDING | ADMITTED | REJECTED
	FinalizedBy  string `json:"finalized_by,omitempty" metadata:",optional"`
	FinalizedAt  string `json:"finalized_at,omitempty" metadata:",optional"`
}

// MisinformationContract provides the chaincode functions.
type MisinformationContract struct {
	contractapi.Contract
}

// newReportKey returns the composite ledger key for a report.
func newReportKey(ctx contractapi.TransactionContextInterface, reportID string) (string, error) {
	return ctx.GetStub().CreateCompositeKey("pred", []string{reportID})
}

// newOrgKey returns the composite ledger key for an enrolled organisation.
func newOrgKey(ctx contractapi.TransactionContextInterface, mspid string) (string, error) {
	return ctx.GetStub().CreateCompositeKey("org", []string{mspid})
}

// newVoteKey returns the composite ledger key for one org's vote on a report.
func newVoteKey(ctx contractapi.TransactionContextInterface, reportID, mspid string) (string, error) {
	return ctx.GetStub().CreateCompositeKey("vote", []string{reportID, mspid})
}

// newAdmissionKey returns the composite ledger key for an org admission request.
func newAdmissionKey(ctx contractapi.TransactionContextInterface, mspid string) (string, error) {
	return ctx.GetStub().CreateCompositeKey("admission", []string{mspid})
}

// validateReportInput normalises and checks a report's core fields.
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

// getRegisteredOrgs returns all enrolled stakeholder organisations.
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

// isRegisteredOrg reports whether the caller's MSP is an enrolled stakeholder org.
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

// quorumFor returns the 2/3-of-registered-orgs threshold, rounding up.
func quorumFor(registeredCount int) int {
	if registeredCount < 1 {
		return 0
	}
	return (2*registeredCount + 2) / 3
}

// RegisterOrg enrolls the calling organisation as a stakeholder during the
// genesis bootstrap window only. Idempotent (re-registering is a no-op).
// Once foundingOrgLimit orgs are registered, new orgs must use
// RequestOrgAdmission. Returns the registered MSPID.
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
	if len(orgs) >= foundingOrgLimit {
		return "", fmt.Errorf(
			"genesis bootstrap closed (%d founding orgs already set); call RequestOrgAdmission instead",
			foundingOrgLimit,
		)
	}

	org := RegisteredOrg{
		MSPID:        mspid,
		RegisteredAt: time.Now().UTC().Format(time.RFC3339),
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

// ListRegisteredOrgs returns all enrolled stakeholder organisations.
func (c *MisinformationContract) ListRegisteredOrgs(
	ctx contractapi.TransactionContextInterface,
) ([]*RegisteredOrg, error) {
	return c.getRegisteredOrgs(ctx)
}

// RequestOrgAdmission creates a PENDING Tier-2 admission request for the calling
// org. Requires the caller to be a channel member (Tier-1) but not yet registered.
func (c *MisinformationContract) RequestOrgAdmission(
	ctx contractapi.TransactionContextInterface,
	orgName, orgType string,
) (string, error) {
	mspid, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return "", fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, mspid); err != nil {
		return "", err
	} else if ok {
		return "", fmt.Errorf("org %s is already a registered stakeholder", mspid)
	}
	if strings.TrimSpace(orgName) == "" {
		return "", fmt.Errorf("org_name must not be empty")
	}

	key, err := newAdmissionKey(ctx, mspid)
	if err != nil {
		return "", fmt.Errorf("failed to build admission key: %v", err)
	}
	if exists, _ := ctx.GetStub().GetState(key); exists != nil {
		return "", fmt.Errorf("admission request for %s already exists", mspid)
	}

	req := OrgAdmissionRequest{
		CandidateMSP: mspid,
		OrgName:      orgName,
		OrgType:      orgType,
		RequestedAt:  time.Now().UTC().Format(time.RFC3339),
		Votes:        []Vote{},
		Status:       admissionPending,
	}
	reqBytes, err := json.Marshal(req)
	if err != nil {
		return "", fmt.Errorf("failed to marshal admission: %v", err)
	}
	if err := ctx.GetStub().PutState(key, reqBytes); err != nil {
		return "", fmt.Errorf("failed to write admission: %v", err)
	}
	return mspid, nil
}

// VoteOnOrgAdmission records one registered org's verdict on a PENDING admission
// request. Each registered org votes once per candidate.
func (c *MisinformationContract) VoteOnOrgAdmission(
	ctx contractapi.TransactionContextInterface,
	candidateMSP, verdict string,
) error {
	if verdict != "accept" && verdict != "reject" {
		return fmt.Errorf("verdict must be \"accept\" or \"reject\", got %q", verdict)
	}

	voterMSP, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, voterMSP); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("org %s is not a registered stakeholder; call RegisterOrg first", voterMSP)
	}

	key, err := newAdmissionKey(ctx, candidateMSP)
	if err != nil {
		return fmt.Errorf("failed to build admission key: %v", err)
	}
	reqBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return fmt.Errorf("failed to read admission: %v", err)
	}
	if reqBytes == nil {
		return fmt.Errorf("no admission request for %s", candidateMSP)
	}
	var req OrgAdmissionRequest
	if err := json.Unmarshal(reqBytes, &req); err != nil {
		return fmt.Errorf("failed to unmarshal admission: %v", err)
	}
	if req.Status != admissionPending {
		return fmt.Errorf("admission for %s is already %s", candidateMSP, req.Status)
	}
	if candidateMSP == voterMSP {
		return fmt.Errorf("a candidate cannot vote on its own admission")
	}
	for _, v := range req.Votes {
		if v.VoterMSP == voterMSP {
			return fmt.Errorf("org %s already voted on %s's admission", voterMSP, candidateMSP)
		}
	}

	txID := ctx.GetStub().GetTxID()
	req.Votes = append(req.Votes, Vote{VoterMSP: voterMSP, Verdict: verdict, TxID: txID})
	reqBytes, err = json.Marshal(req)
	if err != nil {
		return fmt.Errorf("failed to marshal admission: %v", err)
	}
	if err := ctx.GetStub().PutState(key, reqBytes); err != nil {
		return fmt.Errorf("failed to update admission: %v", err)
	}
	return nil
}

// FinalizeOrgAdmission locks a PENDING admission once >= 2/3 of currently
// registered orgs have voted, admitting (or rejecting) the candidate.
func (c *MisinformationContract) FinalizeOrgAdmission(
	ctx contractapi.TransactionContextInterface,
	candidateMSP string,
) (string, error) {
	finalizerMSP, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return "", fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, finalizerMSP); err != nil {
		return "", err
	} else if !ok {
		return "", fmt.Errorf("org %s is not a registered stakeholder; call RegisterOrg first", finalizerMSP)
	}

	key, err := newAdmissionKey(ctx, candidateMSP)
	if err != nil {
		return "", fmt.Errorf("failed to build admission key: %v", err)
	}
	reqBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return "", fmt.Errorf("failed to read admission: %v", err)
	}
	if reqBytes == nil {
		return "", fmt.Errorf("no admission request for %s", candidateMSP)
	}
	var req OrgAdmissionRequest
	if err := json.Unmarshal(reqBytes, &req); err != nil {
		return "", fmt.Errorf("failed to unmarshal admission: %v", err)
	}
	if req.Status != admissionPending {
		return "", fmt.Errorf("admission for %s is already %s", candidateMSP, req.Status)
	}

	orgs, err := c.getRegisteredOrgs(ctx)
	if err != nil {
		return "", err
	}
	quorum := quorumFor(len(orgs))
	if quorum < 1 {
		return "", fmt.Errorf("no registered stakeholder orgs; cannot finalise admission")
	}
	if len(req.Votes) < quorum {
		return "", fmt.Errorf(
			"only %d of %d registered orgs have voted; %d votes required for 2/3 quorum",
			len(req.Votes), len(orgs), quorum,
		)
	}

	accept := 0
	for _, v := range req.Votes {
		if v.Verdict == "accept" {
			accept++
		}
	}
	req.FinalizedBy = finalizerMSP
	req.FinalizedAt = time.Now().UTC().Format(time.RFC3339)
	if accept >= quorum {
		req.Status = admissionAdmitted
		org := RegisteredOrg{MSPID: candidateMSP, RegisteredAt: time.Now().UTC().Format(time.RFC3339)}
		orgByte, err := json.Marshal(org)
		if err != nil {
			return "", fmt.Errorf("failed to marshal org: %v", err)
		}
		orgKey, err := newOrgKey(ctx, candidateMSP)
		if err != nil {
			return "", err
		}
		if err := ctx.GetStub().PutState(orgKey, orgByte); err != nil {
			return "", fmt.Errorf("failed to admit org: %v", err)
		}
	} else {
		req.Status = admissionRejected
	}

	reqBytes, err = json.Marshal(req)
	if err != nil {
		return "", fmt.Errorf("failed to marshal admission: %v", err)
	}
	if err := ctx.GetStub().PutState(key, reqBytes); err != nil {
		return "", fmt.Errorf("failed to write final admission: %v", err)
	}
	return candidateMSP, nil
}

// SubmitReport creates a PENDING report awaiting stakeholder votes.
// Only a registered organisation may submit. Raw text is not sent — only
// content_hash plus an off_chain_uri pointing to the full report.
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

	// Voting deadline is anchored to the ordering-service-assigned transaction
	// timestamp so every peer computes an identical deadline (determinism).
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

// CastVote records one organisation's verdict on a PENDING report.
// Each org votes once. Verdict is "accept" or "reject".
func (c *MisinformationContract) CastVote(
	ctx contractapi.TransactionContextInterface,
	reportID, verdict string,
) error {
	if verdict != "accept" && verdict != "reject" {
		return fmt.Errorf("verdict must be \"accept\" or \"reject\", got %q", verdict)
	}

	voterMSP, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, voterMSP); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("org %s is not a registered stakeholder; call RegisterOrg first", voterMSP)
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
		return fmt.Errorf("report %s is %s; votes are closed", reportID, record.Status)
	}

	voteKey, err := newVoteKey(ctx, reportID, voterMSP)
	if err != nil {
		return fmt.Errorf("failed to build vote key: %v", err)
	}
	if exists, _ := ctx.GetStub().GetState(voteKey); exists != nil {
		return fmt.Errorf("org %s already voted on %s", voterMSP, reportID)
	}

	txID := ctx.GetStub().GetTxID()
	vote := Vote{VoterMSP: voterMSP, Verdict: verdict, TxID: txID}
	voteBytes, err := json.Marshal(vote)
	if err != nil {
		return fmt.Errorf("failed to marshal vote: %v", err)
	}
	if err := ctx.GetStub().PutState(voteKey, voteBytes); err != nil {
		return fmt.Errorf("failed to write vote: %v", err)
	}

	record.Votes = append(record.Votes, vote)
	recordBytes, err = json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}
	if err := ctx.GetStub().PutState(key, recordBytes); err != nil {
		return fmt.Errorf("failed to update record: %v", err)
	}
	return nil
}

// FinalizeReport locks a PENDING report into FINAL or REJECTED once >= 2/3 of
// registered orgs have voted. If called after the voting deadline without
// quorum, the report is auto-marked EXPIRED. After this the record is immutable.
func (c *MisinformationContract) FinalizeReport(
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
		return fmt.Errorf("report %s is already %s", reportID, record.Status)
	}

	// Auto-expire if the voting window has closed with no decision (v2 §3.2).
	if expired, err := isPastDeadline(record.VotingDeadline); err != nil {
		return err
	} else if expired {
		record.Status = statusExpired
		record.FinalizedBy = finalizerMSP
		record.FinalizedAt = time.Now().UTC().Format(time.RFC3339)
		recordBytes, err = json.Marshal(record)
		if err != nil {
			return fmt.Errorf("failed to marshal record: %v", err)
		}
		return ctx.GetStub().PutState(key, recordBytes)
	}

	orgs, err := c.getRegisteredOrgs(ctx)
	if err != nil {
		return err
	}
	quorum := quorumFor(len(orgs))
	if quorum < 1 {
		return fmt.Errorf("no registered stakeholder orgs; cannot finalise")
	}
	if len(record.Votes) < quorum {
		return fmt.Errorf("only %d of %d registered orgs have voted; %d votes required for 2/3 quorum",
			len(record.Votes), len(orgs), quorum)
	}

	accept := 0
	for _, v := range record.Votes {
		if v.Verdict == "accept" {
			accept++
		}
	}
	record.FinalizedBy = finalizerMSP
	record.FinalizedAt = time.Now().UTC().Format(time.RFC3339)
	if accept >= quorum {
		record.Status = statusFinal
	} else {
		record.Status = statusRejected
	}

	recordBytes, err = json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}
	if err := ctx.GetStub().PutState(key, recordBytes); err != nil {
		return fmt.Errorf("failed to write final record: %v", err)
	}
	return nil
}

// ExpireReport marks a PENDING report EXPIRED once its voting deadline passes.
// Rejected (error) if called before the deadline or on a non-PENDING report.
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
	record.FinalizedAt = time.Now().UTC().Format(time.RFC3339)
	recordBytes, err = json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}
	if err := ctx.GetStub().PutState(key, recordBytes); err != nil {
		return fmt.Errorf("failed to write expired record: %v", err)
	}
	return nil
}

// isPastDeadline reports whether an RFC3339 deadline is before now.
func isPastDeadline(rfc3339 string) (bool, error) {
	deadline, err := time.Parse(time.RFC3339, rfc3339)
	if err != nil {
		return false, fmt.Errorf("invalid voting_deadline %q: %v", rfc3339, err)
	}
	return time.Now().UTC().After(deadline), nil
}

// QueryReport returns the record for a report_id.
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

// QueryAllReports returns every report on the ledger.
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

// GetReportCount returns the number of reports on the ledger.
func (c *MisinformationContract) GetReportCount(
	ctx contractapi.TransactionContextInterface,
) (int, error) {
	resultsIter, err := ctx.GetStub().GetStateByPartialCompositeKey("pred", nil)
	if err != nil {
		return 0, fmt.Errorf("failed to open composite key query: %v", err)
	}
	defer resultsIter.Close()
	count := 0
	for resultsIter.HasNext() {
		if _, err := resultsIter.Next(); err != nil {
			return 0, fmt.Errorf("failed to advance results iterator: %v", err)
		}
		count++
	}
	return count, nil
}

// QueryVotes returns all votes cast on a report.
func (c *MisinformationContract) QueryVotes(
	ctx contractapi.TransactionContextInterface,
	reportID string,
) ([]*Vote, error) {
	iter, err := ctx.GetStub().GetStateByPartialCompositeKey("vote", []string{reportID})
	if err != nil {
		return nil, fmt.Errorf("failed to open vote query: %v", err)
	}
	defer iter.Close()
	var votes []*Vote
	for iter.HasNext() {
		kv, err := iter.Next()
		if err != nil {
			return nil, fmt.Errorf("failed to advance vote iterator: %v", err)
		}
		var vote Vote
		if err := json.Unmarshal(kv.Value, &vote); err != nil {
			return nil, fmt.Errorf("failed to unmarshal vote %q: %v", kv.Key, err)
		}
		votes = append(votes, &vote)
	}
	sort.Slice(votes, func(i, j int) bool { return votes[i].VoterMSP < votes[j].VoterMSP })
	return votes, nil
}

// QueryOrgAdmission returns the admission request for a candidate MSP.
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
		return nil, fmt.Errorf("failed to read admission: %v", err)
	}
	if reqBytes == nil {
		return nil, fmt.Errorf("no admission request for %s", candidateMSP)
	}
	var req OrgAdmissionRequest
	if err := json.Unmarshal(reqBytes, &req); err != nil {
		return nil, fmt.Errorf("failed to unmarshal admission: %v", err)
	}
	return &req, nil
}

// QueryReportHistory returns the full ledger history (prior values) of a key,
// proving the tamper-evident audit trail for a report_id.
func (c *MisinformationContract) QueryReportHistory(
	ctx contractapi.TransactionContextInterface,
	reportID string,
) ([]string, error) {
	key, err := newReportKey(ctx, reportID)
	if err != nil {
		return nil, fmt.Errorf("failed to build key: %v", err)
	}
	historyIter, err := ctx.GetStub().GetHistoryForKey(key)
	if err != nil {
		return nil, fmt.Errorf("failed to open history query: %v", err)
	}
	defer historyIter.Close()

	var entries []string
	for historyIter.HasNext() {
		entry, err := historyIter.Next()
		if err != nil {
			return nil, fmt.Errorf("failed to advance history iterator: %v", err)
		}
		entries = append(entries, fmt.Sprintf(
			"tx=%s value=%s deleted=%v",
			entry.TxId, string(entry.Value), entry.IsDelete,
		))
	}
	return entries, nil
}

// ComputeContentHash is a convenience helper mirroring the Python bridge's
// sha256 hexdigest so report hashes can be verified on-chain.
func (c *MisinformationContract) ComputeContentHash(
	ctx contractapi.TransactionContextInterface,
	text string,
) (string, error) {
	if text == "" {
		return "", fmt.Errorf("text must not be empty")
	}
	sum := sha256.Sum256([]byte(text))
	return hex.EncodeToString(sum[:]), nil
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
