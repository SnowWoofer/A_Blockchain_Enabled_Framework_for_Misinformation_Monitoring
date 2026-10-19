package main

import (
	"encoding/hex"
	"encoding/json"
	"fmt"
	"sort"
	"strings"
	"time"

	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
)

// Proposal time is common to all endorsers. Local wall clocks must never decide
// state transitions. Deployment policy must separately constrain client clocks.
func transactionTime(ctx contractapi.TransactionContextInterface) (time.Time, error) {
	ts, err := ctx.GetStub().GetTxTimestamp()
	if err != nil || ts == nil {
		return time.Time{}, fmt.Errorf("transaction timestamp unavailable")
	}
	if err := ts.CheckValid(); err != nil {
		return time.Time{}, fmt.Errorf("invalid transaction timestamp: %v", err)
	}
	return ts.AsTime().UTC(), nil
}

func checkDigest(value string) error {
	if len(value) != 64 {
		return fmt.Errorf("expected SHA-256 hex digest")
	}
	_, err := hex.DecodeString(value)
	return err
}

func (c *MisinformationContract) writer(ctx contractapi.TransactionContextInterface, roles ...string) (string, error) {
	if err := requireRole(ctx, roles...); err != nil {
		return "", err
	}
	msp, err := ctx.GetClientIdentity().GetMSPID()
	if err != nil {
		return "", err
	}
	ok, err := c.isRegisteredOrg(ctx, msp)
	if err != nil {
		return "", err
	}
	if !ok {
		return "", fmt.Errorf("org %s is not a registered stakeholder", msp)
	}
	return msp, nil
}

func (c *MisinformationContract) putReport(ctx contractapi.TransactionContextInterface, rec *ReportRecord, oldStatus string) error {
	key, err := newReportKey(ctx, rec.ReportID)
	if err != nil {
		return err
	}
	if oldStatus != "" && oldStatus != rec.Status {
		old, err := ctx.GetStub().CreateCompositeKey("report_status", []string{oldStatus, rec.ReportID})
		if err != nil {
			return err
		}
		if err := ctx.GetStub().DelState(old); err != nil {
			return err
		}
	}
	idx, err := ctx.GetStub().CreateCompositeKey("report_status", []string{rec.Status, rec.ReportID})
	if err != nil {
		return err
	}
	data, err := json.Marshal(rec)
	if err != nil {
		return err
	}
	if err := ctx.GetStub().PutState(key, data); err != nil {
		return err
	}
	return ctx.GetStub().PutState(idx, []byte(rec.ReportID))
}

type Submission struct {
	ReportID     string  `json:"id"`
	ContentHash  string  `json:"content_hash"`
	ClaimCID     string  `json:"claim_cid"`
	Label        string  `json:"label"`
	Confidence   float64 `json:"confidence"`
	ModelVersion string  `json:"model_version"`
	Timestamp    string  `json:"timestamp"`
	Supersedes   string  `json:"supersedes"`
}

// Submit is the direct-ledger benchmark interface. Its hash is an opaque
// commitment; ClaimCID is intentionally absent for synthetic ledger-only data.
func (c *MisinformationContract) Submit(ctx contractapi.TransactionContextInterface,
	reportID, hash, label string, confidence float64, version, timestamp string) error {
	return c.submit(ctx, Submission{ReportID: reportID, ContentHash: hash, Label: label,
		Confidence: confidence, ModelVersion: version, Timestamp: timestamp})
}

func (c *MisinformationContract) SubmitReport(ctx contractapi.TransactionContextInterface, input string) error {
	var sub Submission
	if err := json.Unmarshal([]byte(input), &sub); err != nil {
		return err
	}
	if strings.TrimSpace(sub.ClaimCID) == "" {
		return fmt.Errorf("claim_cid is required")
	}
	return c.submit(ctx, sub)
}

func (c *MisinformationContract) submit(ctx contractapi.TransactionContextInterface, sub Submission) error {
	msp, err := c.writer(ctx, "official", "fact_checker")
	if err != nil {
		return err
	}
	if err := validateReportInput(sub.ReportID, sub.ContentHash, sub.Label, sub.ModelVersion, sub.Timestamp, sub.Confidence); err != nil {
		return err
	}
	key, err := newReportKey(ctx, sub.ReportID)
	if err != nil {
		return err
	}
	raw, err := ctx.GetStub().GetState(key)
	if err != nil {
		return err
	}
	if raw != nil {
		var prev ReportRecord
		if err := json.Unmarshal(raw, &prev); err != nil {
			return err
		}
		// Retry after a lost acknowledgement succeeds without rewriting history.
		if prev.SchemaVersion == 3 && prev.SubmittedBy == msp && prev.ContentHash == sub.ContentHash &&
			prev.ProposedLabel == sub.Label && prev.ModelVersion == sub.ModelVersion &&
			prev.Confidence == sub.Confidence && prev.ClaimCID == sub.ClaimCID && prev.Supersedes == sub.Supersedes {
			return nil
		}
		return fmt.Errorf("conflicting reuse of report id %s", sub.ReportID)
	}
	orgs, err := c.getRegisteredOrgs(ctx)
	if err != nil {
		return err
	}
	eligible := []string{}
	for _, org := range orgs {
		if org.MSPID != msp {
			eligible = append(eligible, org.MSPID)
		}
	}
	if len(eligible) < 2 {
		return fmt.Errorf("at least two independent reviewers are required")
	}
	sort.Strings(eligible)
	now, err := transactionTime(ctx)
	if err != nil {
		return err
	}
	rec := &ReportRecord{SchemaVersion: 3, ReportID: sub.ReportID, ContentHash: sub.ContentHash,
		ProposedLabel: sub.Label, Confidence: sub.Confidence, ModelVersion: sub.ModelVersion,
		Timestamp: now.Format(time.RFC3339Nano), SubmittedBy: msp,
		FactCheckDeadline: now.Add(factCheckWindow).Format(time.RFC3339Nano), Status: statusPending,
		FactChecks: []FactCheck{}, EligibleReviewers: eligible, RequiredVotes: len(eligible)/2 + 1,
		ClaimCID: sub.ClaimCID, Supersedes: sub.Supersedes}
	if sub.Supersedes != "" {
		return fmt.Errorf("supersession is retired; append a report note instead")
	}

	return c.putReport(ctx, rec, "")
}

func validateOpen(ctx contractapi.TransactionContextInterface, rec *ReportRecord) error {
	if rec.SchemaVersion != 3 {
		return fmt.Errorf("legacy record requires explicit migration; v3 guarantees unavailable")
	}
	if rec.Status != statusPending && rec.Status != statusUnderReview {
		return fmt.Errorf("report is %s", rec.Status)
	}
	now, err := transactionTime(ctx)
	if err != nil {
		return err
	}
	deadline, err := time.Parse(time.RFC3339Nano, rec.FactCheckDeadline)
	if err != nil {
		return err
	}
	if !now.Before(deadline) {
		return fmt.Errorf("fact-check deadline has passed")
	}
	return nil
}

// SubmitFactCheck only accepts evidence committed together with the verdict.
func (c *MisinformationContract) SubmitFactCheck(ctx contractapi.TransactionContextInterface,
	reportID, outcome, evidenceCID, evidenceHash string) error {
	msp, err := c.writer(ctx, "official", "fact_checker")
	if err != nil {
		return err
	}
	if !isValidOutcome(outcome) {
		return fmt.Errorf("outcome must be 0 or 1")
	}
	if strings.TrimSpace(evidenceCID) == "" {
		return fmt.Errorf("evidence_cid is required")
	}
	if err := checkDigest(evidenceHash); err != nil {
		return err
	}
	rec, err := c.QueryReport(ctx, reportID)
	if err != nil {
		return err
	}
	for _, vote := range rec.FactChecks {
		if vote.CheckerMSP == msp {
			if vote.Outcome == outcome && vote.EvidenceCID == evidenceCID && vote.EvidenceHash == evidenceHash {
				return nil
			}
			return fmt.Errorf("organisation already voted; conflicting vote rejected")
		}
	}
	if err := validateOpen(ctx, rec); err != nil {
		return err
	}
	eligible := false
	for _, candidate := range rec.EligibleReviewers {
		if candidate == msp {
			eligible = true
		}
	}
	if !eligible {
		return fmt.Errorf("organisation is not an eligible independent reviewer")
	}
	old := rec.Status
	rec.FactChecks = append(rec.FactChecks, FactCheck{CheckerMSP: msp, Outcome: outcome,
		EvidenceCID: evidenceCID, EvidenceHash: evidenceHash, TxID: ctx.GetStub().GetTxID(),
		Timestamp: deterministicTimestamp(ctx)})
	rec.Status = statusUnderReview
	c.decide(rec, msp, deterministicTimestamp(ctx))
	return c.putReport(ctx, rec, old)
}

func tallyFactChecks(checks []FactCheck) map[string]int {
	tally := map[string]int{}
	for _, check := range checks {
		tally[check.Outcome]++
	}
	return tally
}

func (c *MisinformationContract) decide(rec *ReportRecord, actor, timestamp string) {
	tally := tallyFactChecks(rec.FactChecks)
	for _, label := range []string{"0", "1"} {
		if rec.RequiredVotes > 0 && tally[label] >= rec.RequiredVotes {
			rec.Status, rec.FinalLabel = statusFinal, label
			rec.FinalizedBy, rec.FinalizedAt = actor, timestamp
			return
		}
	}
	if len(rec.FactChecks) == len(rec.EligibleReviewers) {
		rec.Status = statusDisputed
		rec.FinalizedBy, rec.FinalizedAt = actor, timestamp
	}
}

func (c *MisinformationContract) ExpireReport(ctx contractapi.TransactionContextInterface, reportID string) error {
	msp, err := c.writer(ctx, "official")
	if err != nil {
		return err
	}
	rec, err := c.QueryReport(ctx, reportID)
	if err != nil {
		return err
	}
	if rec.Status == statusExpired {
		return nil
	}
	if rec.SchemaVersion != 3 || (rec.Status != statusPending && rec.Status != statusUnderReview) {
		return fmt.Errorf("report cannot expire from %s", rec.Status)
	}
	now, err := transactionTime(ctx)
	if err != nil {
		return err
	}
	deadline, err := time.Parse(time.RFC3339Nano, rec.FactCheckDeadline)
	if err != nil {
		return err
	}
	if now.Before(deadline) {
		return fmt.Errorf("report is still within its fact-check window")
	}
	old := rec.Status
	rec.Status, rec.FinalizedBy, rec.FinalizedAt = statusExpired, msp, now.Format(time.RFC3339Nano)
	return c.putReport(ctx, rec, old)
}

type Intervention struct {
	ID           string `json:"id"`
	ReportID     string `json:"report_id"`
	Organisation string `json:"organisation"`
	EvidenceCID  string `json:"evidence_cid"`
	EvidenceHash string `json:"evidence_hash"`
	Timestamp    string `json:"timestamp"`
	TxID         string `json:"txid"`
}

// ReportNote commits immutable evidence separately from the report state.
// Intervention is retained as the legacy read shape for historical evidence.
type ReportNote = Intervention

func (c *MisinformationContract) RecordReportNote(ctx contractapi.TransactionContextInterface,
	reportID, id, cid, hash string) error {
	msp, err := c.writer(ctx, "official", "fact_checker")
	if err != nil {
		return err
	}
	rec, err := c.QueryReport(ctx, reportID)
	if err != nil {
		return err
	}
	if rec.SubmittedBy != msp {
		return fmt.Errorf("only the submitting organisation may append notes")
	}
	if strings.TrimSpace(id) == "" || len([]rune(id)) > 256 || strings.TrimSpace(cid) == "" {
		return fmt.Errorf("note id (1..256 characters) and evidence_cid required")
	}
	if err := checkDigest(hash); err != nil {
		return err
	}
	key, err := ctx.GetStub().CreateCompositeKey("report_note", []string{reportID, msp, id})
	if err != nil {
		return err
	}
	raw, err := ctx.GetStub().GetState(key)
	if err != nil {
		return err
	}
	if raw != nil {
		var previous ReportNote
		if err := json.Unmarshal(raw, &previous); err != nil {
			return err
		}
		if previous.EvidenceCID == cid && previous.EvidenceHash == hash {
			return nil
		}
		return fmt.Errorf("conflicting note id")
	}
	row := ReportNote{id, reportID, msp, cid, hash, deterministicTimestamp(ctx), ctx.GetStub().GetTxID()}
	data, err := json.Marshal(row)
	if err != nil {
		return err
	}
	return ctx.GetStub().PutState(key, data)
}

func (c *MisinformationContract) QueryReportNotes(ctx contractapi.TransactionContextInterface,
	reportID string) ([]*ReportNote, error) {
	if _, err := c.QueryReport(ctx, reportID); err != nil {
		return nil, err
	}
	it, err := ctx.GetStub().GetStateByPartialCompositeKey("report_note", []string{reportID})
	if err != nil {
		return nil, err
	}
	defer it.Close()
	rows := []*ReportNote{}
	for it.HasNext() {
		kv, err := it.Next()
		if err != nil {
			return nil, err
		}
		var row ReportNote
		if err := json.Unmarshal(kv.Value, &row); err != nil {
			return nil, err
		}
		rows = append(rows, &row)
	}
	return rows, nil
}

func (c *MisinformationContract) QueryInterventions(ctx contractapi.TransactionContextInterface, reportID string) ([]*Intervention, error) {
	if _, err := c.QueryReport(ctx, reportID); err != nil {
		return nil, err
	}
	it, err := ctx.GetStub().GetStateByPartialCompositeKey("intervention", []string{reportID})
	if err != nil {
		return nil, err
	}
	defer it.Close()
	rows := []*Intervention{}
	for it.HasNext() {
		kv, err := it.Next()
		if err != nil {
			return nil, err
		}
		var row Intervention
		if err := json.Unmarshal(kv.Value, &row); err != nil {
			return nil, err
		}
		rows = append(rows, &row)
	}
	return rows, nil
}

type ReportPage struct {
	Items        []*ReportRecord `json:"items"`
	NextBookmark string          `json:"next_bookmark"`
}

func (c *MisinformationContract) QueryReportsPage(ctx contractapi.TransactionContextInterface,
	status string, pageSize int, bookmark string) (*ReportPage, error) {
	if pageSize < 1 || pageSize > 200 {
		return nil, fmt.Errorf("page_size must be 1..200")
	}
	kind, attrs := "pred", []string{}
	if status != "" {
		kind, attrs = "report_status", []string{status}
	}
	it, meta, err := ctx.GetStub().GetStateByPartialCompositeKeyWithPagination(kind, attrs, int32(pageSize), bookmark)
	if err != nil {
		return nil, err
	}
	defer it.Close()
	page := &ReportPage{Items: []*ReportRecord{}, NextBookmark: meta.Bookmark}
	for it.HasNext() {
		kv, err := it.Next()
		if err != nil {
			return nil, err
		}
		if status != "" {
			rec, err := c.QueryReport(ctx, string(kv.Value))
			if err != nil {
				return nil, err
			}
			page.Items = append(page.Items, rec)
		} else {
			var rec ReportRecord
			if err := json.Unmarshal(kv.Value, &rec); err != nil {
				return nil, err
			}
			page.Items = append(page.Items, &rec)
		}
	}
	return page, nil
}
