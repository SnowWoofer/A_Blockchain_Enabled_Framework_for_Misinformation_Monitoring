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
	statusUnderReview       = "UNDER_REVIEW"
	statusFinal             = "FINAL"
	statusRejected          = "REJECTED"
	statusExpired           = "EXPIRED"
	admissionPending        = "PENDING"
	admissionAdmitted       = "ADMITTED"
	admissionRejected       = "REJECTED"
	defaultFoundingOrgLimit = 3
	factCheckWindow         = 72 * time.Hour

	// Fact-check verdict taxonomy. Deliberately small: the finalisation rule
	// needs independent fact-checkers to land on the exact same value for
	// consensus to mean anything, and a large taxonomy makes that
	// vanishingly unlikely even when checkers substantively agree.
	verdictFactual        = "factual"
	verdictOpinion        = "opinion"
	verdictMisinformation = "misinformation"
)

func isValidVerdict(v string) bool {
	return v == verdictFactual || v == verdictOpinion || v == verdictMisinformation
}

// FactCheck deliberately carries only what the on-chain consensus tally
// needs. This is not a vote — a fact-checker isn't expressing a preference,
// they're independently verifying a claim and reporting what they found;
// "vote"/election framing belongs to genuine governance decisions like org
// admission (see Vote/OrgAdmissionRequest below), not to fact-finding. The
// fact-checker's actual reasoning/evidence live off-chain, in the versioned
// IPFS document — only its CID is anchored here (ReportRecord.OffChainURI),
// never the content itself.
type FactCheck struct {
	CheckerMSP string `json:"checker_msp"`
	Verdict    string `json:"verdict"`
	TxID       string `json:"txid"`
}

type ReportRecord struct {
	ReportID          string      `json:"report_id"`
	ContentHash       string      `json:"content_hash"`
	ProposedLabel     string      `json:"proposed_label"`
	Confidence        float64     `json:"confidence"`
	ModelVersion      string      `json:"model_version"`
	Timestamp         string      `json:"timestamp"`
	SubmittedBy       string      `json:"submitted_by"`
	OffChainURI       string      `json:"off_chain_uri"`
	FactCheckDeadline string      `json:"fact_check_deadline"`
	Status            string      `json:"status"`
	FactChecks        []FactCheck `json:"fact_checks"`
	FinalLabel        string      `json:"final_label,omitempty" metadata:",optional"`
	FinalizedBy       string      `json:"finalized_by,omitempty" metadata:",optional"`
	FinalizedAt       string      `json:"finalized_at,omitempty" metadata:",optional"`
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

func validateReportInput(reportID, contentHash, label, modelVersion, timestamp string, confidence float64) error {
	if strings.TrimSpace(reportID) == "" {
		return fmt.Errorf("report_id must not be empty")
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
// the fixed 2-vote/tiebreak rule FinalizeReport uses for fact-checking a
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

func (c *MisinformationContract) SubmitReport(
	ctx contractapi.TransactionContextInterface,
	reportID, contentHash, label string,
	confidence float64,
	modelVersion, timestamp, offChainURI string,
) error {
	if err := validateReportInput(reportID, contentHash, label, modelVersion, timestamp, confidence); err != nil {
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
	deadline := time.Now().UTC().Add(factCheckWindow).Format(time.RFC3339)
	if txTS, err := ctx.GetStub().GetTxTimestamp(); err == nil && txTS != nil {
		deadline = txTS.AsTime().Add(factCheckWindow).UTC().Format(time.RFC3339)
	}
	record := ReportRecord{
		ReportID:          reportID,
		ContentHash:       contentHash,
		ProposedLabel:     label,
		Confidence:        confidence,
		ModelVersion:      modelVersion,
		Timestamp:         timestamp,
		SubmittedBy:       submittedBy,
		OffChainURI:       offChainURI,
		FactCheckDeadline: deadline,
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

// SubmitFactCheck records a fact-checker's verdict on a claim — only what the
// on-chain consensus tally needs. The actual reasoning/evidence behind the
// verdict live off-chain, in a freshly re-published, versioned IPFS
// document; newOffChainURI is that document's CID, advancing the on-chain
// pointer. Pass "" to leave the existing off_chain_uri unchanged.
func (c *MisinformationContract) SubmitFactCheck(
	ctx contractapi.TransactionContextInterface,
	reportID, verdict, newOffChainURI string,
) error {
	checkerMSP, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return fmt.Errorf("failed to read caller MSP: %v", err)
	}
	if ok, err := c.isRegisteredOrg(ctx, checkerMSP); err != nil {
		return err
	} else if !ok {
		return fmt.Errorf("org %s is not a registered stakeholder; call RegisterOrg first", checkerMSP)
	}
	if !isValidVerdict(verdict) {
		return fmt.Errorf("verdict must be one of factual/opinion/misinformation, got %q", verdict)
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
	if record.Status != statusPending && record.Status != statusUnderReview {
		return fmt.Errorf("report %s is %s; only PENDING/UNDER_REVIEW reports accept fact-checks", reportID, record.Status)
	}
	for _, v := range record.FactChecks {
		if v.CheckerMSP == checkerMSP {
			return fmt.Errorf("org %s has already fact-checked report %s", checkerMSP, reportID)
		}
	}
	record.FactChecks = append(record.FactChecks, FactCheck{
		CheckerMSP: checkerMSP,
		Verdict:    verdict,
		TxID:       ctx.GetStub().GetTxID(),
	})
	record.Status = statusUnderReview
	if strings.TrimSpace(newOffChainURI) != "" {
		record.OffChainURI = newOffChainURI
	}
	recordBytes, err = json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}
	if err := ctx.GetStub().PutState(key, recordBytes); err != nil {
		return fmt.Errorf("failed to write record: %v", err)
	}
	return nil
}

// tallyFactChecks counts fact-checks per verdict. With a binary label domain
// ("0"/"1"), at most two keys ever exist, so map iteration order cannot
// affect which verdict is reported as the winner or whether a tie is
// detected.
func tallyFactChecks(checks []FactCheck) map[string]int {
	tally := make(map[string]int)
	for _, v := range checks {
		tally[v.Verdict]++
	}
	return tally
}

// FinalizeReport closes a report once at least 2 fact-checks exist and
// one verdict has a strict majority: 2 agreeing fact-checks finalise
// immediately; 2 disagreeing fact-checks stay PENDING until a 3rd
// fact-check breaks the tie.
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
	if record.Status != statusPending && record.Status != statusUnderReview {
		return fmt.Errorf("report %s is %s; only PENDING/UNDER_REVIEW reports can be finalised", reportID, record.Status)
	}
	if len(record.FactChecks) < 2 {
		return fmt.Errorf("report %s has %d fact-check(s); at least 2 fact-checks are required to finalise", reportID, len(record.FactChecks))
	}
	tally := tallyFactChecks(record.FactChecks)
	winner, winnerCount, tied := "", 0, false
	for verdict, count := range tally {
		switch {
		case count > winnerCount:
			winner, winnerCount, tied = verdict, count, false
		case count == winnerCount && winnerCount > 0:
			tied = true
		}
	}
	if tied {
		return fmt.Errorf("report %s is tied at %d-%d; a tie-breaking fact-check is required to finalise", reportID, winnerCount, winnerCount)
	}
	record.FinalLabel = winner
	record.Status = statusFinal
	record.FinalizedBy = finalizerMSP
	record.FinalizedAt = deterministicTimestamp(ctx)
	recordBytes, err = json.Marshal(record)
	if err != nil {
		return fmt.Errorf("failed to marshal record: %v", err)
	}
	if err := ctx.GetStub().PutState(key, recordBytes); err != nil {
		return fmt.Errorf("failed to write finalised record: %v", err)
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
	if record.Status != statusPending && record.Status != statusUnderReview {
		return fmt.Errorf("report %s is %s; only PENDING/UNDER_REVIEW reports can expire", reportID, record.Status)
	}
	expired, err := isPastDeadline(record.FactCheckDeadline)
	if err != nil {
		return err
	}
	if !expired {
		return fmt.Errorf("report %s is still within its fact-check window (deadline %s)", reportID, record.FactCheckDeadline)
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

// QueryReportHistory returns every transaction that ever wrote this report's
// ledger key (submission, each fact-check, finalize/expire), oldest first, using
// Fabric's own block history rather than any bespoke version-tracking —
// the blockchain already is that history, this just exposes it.
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
