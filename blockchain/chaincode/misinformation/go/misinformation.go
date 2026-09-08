package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
	"sort"
	"strings"
	"time"
)

const (
	statusPending           = "PENDING"
	statusUnderReview       = "UNDER_REVIEW"
	statusFinal             = "FINAL"
	statusReopened          = "REOPENED"
	statusRejected          = "REJECTED"
	statusExpired           = "EXPIRED"
	admissionPending        = "PENDING"
	admissionAdmitted       = "ADMITTED"
	admissionRejected       = "REJECTED"
	defaultFoundingOrgLimit = 3
	factCheckWindow         = 72 * time.Hour

	// Panel sizes are the minimum number of fact-checks a round must gather
	// before it can close. They are deliberately odd and grow by two: with a
	// binary outcome an odd panel cannot split evenly, so a tie is
	// structurally impossible at every round and no tie-breaking logic
	// exists anywhere in this contract.
	initialPanel = 3
	panelStep    = 2
	// A claim may be reopened at most this many times. Escalation alone is
	// not a bound — at 100 orgs a panel growing by two would allow 32 rounds,
	// which is churn, not due process. Three reopens is the policy limit;
	// the panel ceiling below is the physical one.
	maxReopenRounds = 3

	// Fact-check outcome domain — the same binary label domain the model
	// predicts into (ReportRecord.InferenceLabel), so a finalised
	// FinalLabel can be compared directly against what the model proposed.
	// Deliberately small: the finalisation rule
	// needs independent fact-checkers to land on the exact same value for
	// consensus to mean anything, and a large taxonomy makes that
	// vanishingly unlikely even when checkers substantively agree.
	outcomeNonMisinformation = "0"
	outcomeMisinformation    = "1"
)

func isValidOutcome(v string) bool {
	return v == outcomeNonMisinformation || v == outcomeMisinformation
}

// FactCheck deliberately carries only what the on-chain consensus tally
// needs. This is not a vote — a fact-checker isn't expressing a preference,
// they're independently verifying a claim and reporting what they found;
// "vote"/election framing belongs to genuine governance decisions like org
// admission (see Vote/OrgAdmissionRequest below), not to fact-finding. The
// fact-checker's actual reasoning/evidence live off-chain, in the versioned
// IPFS document — only its CID is anchored here (ReportRecord.RootCID),
// never the content itself.
type FactCheck struct {
	CheckerMSP string `json:"checker_msp"`
	Outcome    string `json:"outcome"`
	TxID       string `json:"tx_id"`
	// Round this check belongs to. Tallies are per-round: reopening starts a
	// fresh panel, and every org — including those who checked before — may
	// check again, because new evidence is exactly what a reopen is for.
	Round int `json:"round"`
}

// Reopen records one challenge to a finalised verdict. Kept as a list rather
// than a single field so the full challenge history is readable from the
// record itself: "which orgs keep reopening claims that are then reaffirmed?"
// is the question a consortium would ask when it suspects bad faith, and an
// audit trail that has to be reconstructed by diffing block history is a
// weaker guarantee than one that can simply be read.
type Reopen struct {
	OrgMSP string `json:"org_msp"`
	Round  int    `json:"round"`
	At     string `json:"at"`
	TxID   string `json:"tx_id"`
}

type ReportRecord struct {
	RootCID string `json:"root_cid"`
	// CurrentCID is the head of this claim's off-chain version chain. It
	// equals RootCID at submission and advances on every re-publish, so the
	// ledger — not the gateway's local index — is the authority on which
	// document version the consortium actually endorsed. Walk backwards from
	// it via each document's own previous_cid to recover the full history.
	CurrentCID    string  `json:"current_cid"`
	InferenceHash string  `json:"inference_hash"`
	ProposedLabel string  `json:"inference_label"`
	Confidence    float64 `json:"confidence"`
	ModelVersion  string  `json:"model_version"`
	Timestamp     string  `json:"timestamp"`
	SubmittedBy   string  `json:"submitted_by"`
	// TxID is the transaction that created this record. Written once by
	// Submit and never touched again, so it is immutable like the rest of
	// the claim's identity. "What wrote this record last" is deliberately
	// not stored — GetHistoryForKey already states it, and duplicating that
	// in mutable state only invites the two to disagree. Each FactCheck
	// carries its own TxID because history merely *implies* which
	// transaction added which entry (by diffing array lengths); this one
	// records what history states outright.
	TxID              string `json:"tx_id"`
	FactCheckDeadline string `json:"fact_check_deadline"`
	// Round 0 is the original review; each reopen increments it. RequiredPanel
	// is how many checks this round must gather before it can close, growing
	// by panelStep each time: 3, 5, 7 … capped at the governance quorum.
	Round         int         `json:"round"`
	RequiredPanel int         `json:"required_panel"`
	Reopens       []Reopen    `json:"reopens,omitempty" metadata:",optional"`
	Status        string      `json:"status"`
	FactChecks    []FactCheck `json:"fact_checks"`
	FinalLabel    string      `json:"final_label,omitempty" metadata:",optional"`
	FinalizedBy   string      `json:"finalized_by,omitempty" metadata:",optional"`
	FinalizedAt   string      `json:"finalized_at,omitempty" metadata:",optional"`
}

type RegisteredOrg struct {
	MSPID        string `json:"mspid"`
	RegisteredAt string `json:"registered_at"`
}

// Vote is for genuine governance decisions — admitting a new org to the
// consortium is a preference/majority decision among existing members, not
// a fact to be checked, so "vote" is the right word here (unlike FactCheck
// above).
type Vote struct {
	VoterMSP string `json:"voter_msp"`
	Verdict  string `json:"verdict"`
	TxID     string `json:"tx_id"`
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

func newReportKey(ctx contractapi.TransactionContextInterface, rootCID string) (string, error) {
	return ctx.GetStub().CreateCompositeKey("pred", []string{rootCID})
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

func validateReportInput(rootCID, inferenceHash, label, modelVersion, timestamp string, confidence float64) error {
	if strings.TrimSpace(rootCID) == "" {
		return fmt.Errorf("root_cid must not be empty")
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
	if len(inferenceHash) != 64 {
		return fmt.Errorf("inference_hash must be a 64-char sha256 hex digest, got %d chars", len(inferenceHash))
	}
	if _, err := hex.DecodeString(inferenceHash); err != nil {
		return fmt.Errorf("inference_hash is not valid hex: %v", err)
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

func (c *MisinformationContract) RequestOrgAdmission(
	ctx contractapi.TransactionContextInterface,
	orgName, orgType string,
) (string, error) {
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

// FinalizeOrgAdmission admits the candidate once at least quorumFor(current
// registered org count) of its votes are "1" (admit) — a supermajority that
// scales with consortium size, deliberately a higher and different bar than
// the fixed 2-check/tiebreak rule applyConsensus uses for fact-checking a
// single claim: admitting a new permanent member is a governance decision,
// not a per-claim verdict.
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

func (c *MisinformationContract) Submit(
	ctx contractapi.TransactionContextInterface,
	rootCID, inferenceHash, label string,
	confidence float64,
	modelVersion, timestamp string,
) error {
	if err := validateReportInput(rootCID, inferenceHash, label, modelVersion, timestamp, confidence); err != nil {
		return err
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
	key, err := newReportKey(ctx, rootCID)
	if err != nil {
		return fmt.Errorf("failed to build key: %v", err)
	}
	if exists, _ := ctx.GetStub().GetState(key); exists != nil {
		return fmt.Errorf("report %s already exists (immutable once finalised)", rootCID)
	}
	deadline := time.Now().UTC().Add(factCheckWindow).Format(time.RFC3339)
	if txTS, err := ctx.GetStub().GetTxTimestamp(); err == nil && txTS != nil {
		deadline = txTS.AsTime().Add(factCheckWindow).UTC().Format(time.RFC3339)
	}
	record := ReportRecord{
		RootCID:           rootCID,
		CurrentCID:        rootCID,
		InferenceHash:     inferenceHash,
		ProposedLabel:     label,
		Confidence:        confidence,
		ModelVersion:      modelVersion,
		Timestamp:         timestamp,
		SubmittedBy:       submittedBy,
		TxID:              ctx.GetStub().GetTxID(),
		FactCheckDeadline: deadline,
		Round:             0,
		RequiredPanel:     initialPanel,
		Status:            statusPending,
		FactChecks:        []FactCheck{},
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

// SubmitFactCheck records a fact-checker's outcome on a claim — only what the
// on-chain consensus tally needs. The reasoning/support behind the outcome
// live off-chain, in a re-published IPFS document whose CID is anchored here
// as CurrentCID, so the version carrying this fact-check is itself
// tamper-evident rather than trusted from the gateway's local index.
func (c *MisinformationContract) SubmitFactCheck(
	ctx contractapi.TransactionContextInterface,
	rootCID, outcome, newCID string,
) error {
	if strings.TrimSpace(newCID) == "" {
		return fmt.Errorf("newCID must not be empty")
	}
	checkerMSP, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, checkerMSP); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("org %s is not a registered stakeholder; call RegisterOrg first", checkerMSP)
	}
	if !isValidOutcome(outcome) {
		return fmt.Errorf("outcome must be \"0\" or \"1\", got %q", outcome)
	}
	key, err := newReportKey(ctx, rootCID)
	if err != nil {
		return fmt.Errorf("failed to build key: %v", err)
	}
	recordBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return fmt.Errorf("failed to read state: %v", err)
	}
	if recordBytes == nil {
		return fmt.Errorf("no report found for %s", rootCID)
	}
	var record ReportRecord
	if err := json.Unmarshal(recordBytes, &record); err != nil {
		return fmt.Errorf("failed to unmarshal record: %v", err)
	}
	if record.Status != statusPending && record.Status != statusUnderReview && record.Status != statusReopened {
		return fmt.Errorf("report %s is %s; only PENDING/UNDER_REVIEW/REOPENED reports accept fact-checks", rootCID, record.Status)
	}
	// One check per org per round, not per report: a reopen exists precisely
	// so the question can be examined again, and the orgs that examined it
	// before are the ones most likely to hold new evidence.
	for _, v := range record.FactChecks {
		if v.CheckerMSP == checkerMSP && v.Round == record.Round {
			return fmt.Errorf("org %s has already fact-checked report %s in round %d", checkerMSP, rootCID, record.Round)
		}
	}
	record.FactChecks = append(record.FactChecks, FactCheck{
		CheckerMSP: checkerMSP,
		Outcome:    outcome,
		TxID:       ctx.GetStub().GetTxID(),
		Round:      record.Round,
	})
	record.Status = statusUnderReview
	record.CurrentCID = newCID
	applyConsensus(ctx, &record, checkerMSP)
	recordBytes, err = json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}
	if err := ctx.GetStub().PutState(key, recordBytes); err != nil {
		return fmt.Errorf("failed to write record: %v", err)
	}
	return nil
}

// tallyRound counts the fact-checks belonging to one round, per outcome. The
// outcome domain is binary, so at most two keys ever exist and map iteration
// order cannot affect which outcome wins.
func tallyRound(checks []FactCheck, round int) (map[string]int, int) {
	tally, n := make(map[string]int), 0
	for _, v := range checks {
		if v.Round == round {
			tally[v.Outcome]++
			n++
		}
	}
	return tally, n
}

// supermajorityOf returns how many agreeing checks a panel of n needs: a
// two-thirds supermajority, rounded up. One ratio governs every round — the
// escalation on reopen comes from the panel growing, not from moving the bar,
// so there is a single number to state and defend.
func supermajorityOf(n int) int {
	return (2*n + 2) / 3
}

// applyConsensus closes the report in place once the current round has
// gathered its required panel and one outcome holds a two-thirds
// supermajority of it. Panels are odd, so no round can deadlock on a tie.
//
// This runs inside SubmitFactCheck rather than being its own transaction. The
// decision is a pure function of fact-checks already on the ledger — no caller
// supplies anything to it — so there is nothing for anyone to decide, and a
// settled report should not sit open waiting for someone to remember to close
// it. FinalizedBy is therefore the org whose fact-check closed it, which is a
// truer fact than whoever happened to run a finalise command.
func applyConsensus(ctx contractapi.TransactionContextInterface, record *ReportRecord, closerMSP string) {
	panel := record.RequiredPanel
	if panel < initialPanel {
		panel = initialPanel
	}
	tally, n := tallyRound(record.FactChecks, record.Round)
	if n < panel {
		return
	}
	needed := supermajorityOf(n)
	winner, winnerCount := "", 0
	for outcome, count := range tally {
		if count > winnerCount {
			winner, winnerCount = outcome, count
		}
	}
	if winnerCount < needed {
		return
	}
	record.FinalLabel = winner
	record.Status = statusFinal
	record.FinalizedBy = closerMSP
	record.FinalizedAt = deterministicTimestamp(ctx)
}

// ReopenReport puts a finalised claim back under review. Any registered org
// may reopen — a low bar to close is only defensible if verdicts are
// correctable, so no organisation needs permission to raise a question.
//
// Three properties keep that from being an attack surface. The standing
// FinalLabel is deliberately NOT cleared: it remains the consortium's answer
// throughout the new round, so reopening cannot be used to make an
// inconvenient verdict disappear while the round runs. The required panel
// grows by panelStep, so overturning a verdict is strictly harder than
// establishing one was. And the round carries its own deadline: a challenge
// that cannot gather its panel expires and the standing verdict is
// reaffirmed, which makes a frivolous reopen strengthen what it attacked
// rather than leave it in limbo.
//
// Escalation is capped at the governance quorum. A claim confirmed by a panel
// that large has met the same bar as admitting a permanent member, and is
// terminal — that ceiling is what stops reopening from cycling forever.
func (c *MisinformationContract) ReopenReport(
	ctx contractapi.TransactionContextInterface,
	rootCID string,
) error {
	callerMSP, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, callerMSP); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("org %s is not a registered stakeholder; call RegisterOrg first", callerMSP)
	}
	key, err := newReportKey(ctx, rootCID)
	if err != nil {
		return fmt.Errorf("failed to build key: %v", err)
	}
	recordBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return fmt.Errorf("failed to read state: %v", err)
	}
	if recordBytes == nil {
		return fmt.Errorf("no report found for %s", rootCID)
	}
	var record ReportRecord
	if err := json.Unmarshal(recordBytes, &record); err != nil {
		return fmt.Errorf("failed to unmarshal record: %v", err)
	}
	if record.Status != statusFinal {
		return fmt.Errorf("report %s is %s; only FINAL reports can be reopened", rootCID, record.Status)
	}
	// One reopen per org per report. The round cap is a budget for the
	// report, so without this a single actor could spend all of it alone and
	// leave a later, well-founded challenge with no recourse. Exhausting it
	// now takes three separate organisations, each permanently named below.
	for _, r := range record.Reopens {
		if r.OrgMSP == callerMSP {
			return fmt.Errorf("org %s has already reopened report %s (in round %d)", callerMSP, rootCID, r.Round)
		}
	}
	if record.Round >= maxReopenRounds {
		return fmt.Errorf(
			"report %s is settled: it has already been reopened %d times, the limit",
			rootCID, record.Round)
	}
	orgs, err := c.getRegisteredOrgs(ctx)
	if err != nil {
		return err
	}
	// The panel cannot exceed the consortium — there would be nobody left to
	// seat on it. This is a physical limit, not a policy one, and it is what
	// makes escalating reopen unavailable to consortiums smaller than
	// initialPanel + panelStep.
	nextPanel := record.RequiredPanel + panelStep
	if nextPanel > len(orgs) {
		return fmt.Errorf(
			"report %s cannot be reopened: the next round needs a panel of %d but only %d orgs are registered",
			rootCID, nextPanel, len(orgs))
	}
	record.Round++
	record.RequiredPanel = nextPanel
	record.Status = statusReopened
	record.Reopens = append(record.Reopens, Reopen{
		OrgMSP: callerMSP,
		Round:  record.Round,
		At:     deterministicTimestamp(ctx),
		TxID:   ctx.GetStub().GetTxID(),
	})
	// A fresh window for the new round. FinalLabel/FinalizedBy stay as they
	// are: the previous verdict stands until this round overturns it.
	deadline := time.Now().UTC().Add(factCheckWindow).Format(time.RFC3339)
	if txTS, err := ctx.GetStub().GetTxTimestamp(); err == nil && txTS != nil {
		deadline = txTS.AsTime().Add(factCheckWindow).UTC().Format(time.RFC3339)
	}
	record.FactCheckDeadline = deadline
	updated, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}
	if err := ctx.GetStub().PutState(key, updated); err != nil {
		return fmt.Errorf("failed to write reopened record: %v", err)
	}
	return nil
}

// SetCurrentCID advances a claim's off-chain version pointer. Most
// re-publishes ride along with the invoke that caused them (SubmitFactCheck
// carries its own newCID), but finalisation is inherently two-phase: the new
// document records the FinalLabel that applyConsensus itself computes, so its
// CID cannot exist until after that transaction commits. This closes that
// window rather than leaving the pointer one version stale.
func (c *MisinformationContract) SetCurrentCID(
	ctx contractapi.TransactionContextInterface,
	rootCID, newCID string,
) error {
	if strings.TrimSpace(newCID) == "" {
		return fmt.Errorf("newCID must not be empty")
	}
	callerMSP, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, callerMSP); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("org %s is not a registered stakeholder; call RegisterOrg first", callerMSP)
	}
	key, err := newReportKey(ctx, rootCID)
	if err != nil {
		return fmt.Errorf("failed to build key: %v", err)
	}
	recordBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return fmt.Errorf("failed to read state: %v", err)
	}
	if recordBytes == nil {
		return fmt.Errorf("no report found for %s", rootCID)
	}
	var record ReportRecord
	if err := json.Unmarshal(recordBytes, &record); err != nil {
		return fmt.Errorf("failed to unmarshal record: %v", err)
	}
	record.CurrentCID = newCID
	updated, err := json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}
	if err := ctx.GetStub().PutState(key, updated); err != nil {
		return fmt.Errorf("failed to write record: %v", err)
	}
	return nil
}

func (c *MisinformationContract) ExpireReport(
	ctx contractapi.TransactionContextInterface,
	rootCID string,
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
	key, err := newReportKey(ctx, rootCID)
	if err != nil {
		return fmt.Errorf("failed to build key: %v", err)
	}
	recordBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return fmt.Errorf("failed to read state: %v", err)
	}
	if recordBytes == nil {
		return fmt.Errorf("no report found for %s", rootCID)
	}
	var record ReportRecord
	if err := json.Unmarshal(recordBytes, &record); err != nil {
		return fmt.Errorf("failed to unmarshal record: %v", err)
	}
	if record.Status != statusPending && record.Status != statusUnderReview && record.Status != statusReopened {
		return fmt.Errorf("report %s is %s; only PENDING/UNDER_REVIEW/REOPENED reports can expire", rootCID, record.Status)
	}
	expired, err := isPastDeadline(record.FactCheckDeadline)
	if err != nil {
		return err
	}
	if !expired {
		return fmt.Errorf("report %s is still within its fact-check window (deadline %s)", rootCID, record.FactCheckDeadline)
	}
	if record.Status == statusReopened {
		// A challenge that could not gather its panel fails, and the verdict
		// it questioned is reaffirmed rather than the claim being discarded.
		// This is what stops a frivolous reopen from parking a claim in limbo
		// forever: the standing answer comes back, stronger for having held.
		record.Status = statusFinal
		record.FinalizedBy = finalizerMSP
		record.FinalizedAt = deterministicTimestamp(ctx)
		updated, err := json.Marshal(record)
		if err != nil {
			return fmt.Errorf("failed to marshal record: %v", err)
		}
		if err := ctx.GetStub().PutState(key, updated); err != nil {
			return fmt.Errorf("failed to write reaffirmed record: %v", err)
		}
		return nil
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
		return false, fmt.Errorf("invalid fact_check_deadline %q: %v", rfc3339, err)
	}
	return time.Now().UTC().After(deadline), nil
}

func (c *MisinformationContract) QueryReport(
	ctx contractapi.TransactionContextInterface,
	rootCID string,
) (*ReportRecord, error) {
	key, err := newReportKey(ctx, rootCID)
	if err != nil {
		return nil, fmt.Errorf("failed to build key: %v", err)
	}
	recordBytes, err := ctx.GetStub().GetState(key)
	if err != nil {
		return nil, fmt.Errorf("failed to read state: %v", err)
	}
	if recordBytes == nil {
		return nil, fmt.Errorf("no report found for %s", rootCID)
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

// QueryReportHistory returns every transaction that ever wrote this report's
// ledger key (submission, each fact-check, finalize/expire), oldest first, using
// Fabric's own block history rather than any bespoke version-tracking —
// the blockchain already is that history, this just exposes it.
func (c *MisinformationContract) QueryReportHistory(
	ctx contractapi.TransactionContextInterface,
	rootCID string,
) ([]*ReportHistoryEntry, error) {
	key, err := newReportKey(ctx, rootCID)
	if err != nil {
		return nil, fmt.Errorf("failed to build key: %v", err)
	}
	iter, err := ctx.GetStub().GetHistoryForKey(key)
	if err != nil {
		return nil, fmt.Errorf("failed to read history for %s: %v", rootCID, err)
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
		return nil, fmt.Errorf("no report found for %s", rootCID)
	}
	// Sort explicitly rather than trusting the iterator. GetHistoryForKey was
	// observed returning newest-first on this deployment while the contract
	// documents oldest-first, and callers reasonably read entries[0] as the
	// submission. TxID breaks ties so the result is deterministic across
	// peers even if two writes share a block timestamp.
	sort.SliceStable(entries, func(i, j int) bool {
		if entries[i].Timestamp != entries[j].Timestamp {
			return entries[i].Timestamp < entries[j].Timestamp
		}
		return entries[i].TxID < entries[j].TxID
	})
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
