package main

import (
	"encoding/json"
	"fmt"
	"math"
	"testing"
	"time"

	"github.com/hyperledger/fabric-protos-go-apiv2/peer"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const validHash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
const validTs = "2025-01-01T00:00:00Z"

func newTestStub(t *testing.T) *MockStub {
	t.Helper()
	cc, err := newMisinformationChaincode()
	if err != nil {
		t.Fatalf("failed to create chaincode: %v", err)
	}
	stub := &MockStub{
		cc:          cc,
		State:       map[string][]byte{},
		TxID:        "tx1",
		TxTimestamp: nil,
		Attrs:       map[string]string{"role": "official"},
	}
	stub.args = [][]byte{}
	stub.signedProposal = &peer.SignedProposal{}
	return stub
}

func invokeAs(stub *MockStub, txID, msp string, args ...string) *peer.Response { //constructor
	stub.TxID = txID
	stub.CreatorMSP = msp
	call := [][]byte{[]byte(args[0])} //call arg0
	for _, a := range args[1:] {      //append rest fo args to call
		call = append(call, []byte(a))
	}
	return stub.MockInvoke(txID, call)
}

func registerOrgs(t *testing.T, stub *MockStub, msps ...string) {
	t.Helper()
	for _, msp := range msps {
		if res := invokeAs(stub, "tx-reg-"+msp, msp, "RegisterOrg"); res.Status != 200 {
			t.Fatalf("RegisterOrg for %s failed: %s", msp, res.Message)
		}
	}
}

func submitReport(stub *MockStub, txID, msp, reportID string) *peer.Response {
	return invokeAs(stub, txID, msp,
		"Submit", reportID, validHash, "1", "0.97", "model-v1", validTs)
}

func setup(t *testing.T, n int) *MockStub {
	s := newTestStub(t)
	if r := invokeAs(s, "cfg", "Org1MSP", "SetFoundingOrgLimit", fmt.Sprint(n)); r.Status != 200 {
		t.Fatal(r.Message)
	}
	for i := 1; i <= n; i++ {
		registerOrgs(t, s, fmt.Sprintf("Org%dMSP", i))
	}
	s.Attrs = map[string]string{"role": "fact_checker"}
	return s
}
func good(t *testing.T, r *peer.Response) {
	t.Helper()
	if r.Status != 200 {
		t.Fatal(r.Message)
	}
}
func bad(t *testing.T, r *peer.Response) {
	t.Helper()
	if r.Status == 200 {
		t.Fatal("expected rejection")
	}
}
func record(t *testing.T, s *MockStub, id string) ReportRecord {
	t.Helper()
	r := invokeAs(s, "q", "Org1MSP", "QueryReport", id)
	good(t, r)
	var rec ReportRecord
	if err := json.Unmarshal(r.Payload, &rec); err != nil {
		t.Fatal(err)
	}
	return rec
}
func vote(s *MockStub, msp, id, outcome string) *peer.Response {
	return invokeAs(s, "v-"+msp, msp, "SubmitFactCheck", id, outcome, "cid-"+msp+"-"+id, validHash)
}

func TestMajorityAcrossConsortiumSizes(t *testing.T) {
	for _, n := range []int{3, 5, 10, 15, 20} {
		for _, label := range []string{"0", "1"} {
			t.Run(fmt.Sprintf("%d-%s", n, label), func(t *testing.T) {
				s := setup(t, n)
				good(t, submitReport(s, "s", "Org1MSP", "r"))
				rec := record(t, s, "r")
				if rec.RequiredVotes != (n-1)/2+1 || len(rec.EligibleReviewers) != n-1 {
					t.Fatal(rec)
				}
				for i := 2; i <= rec.RequiredVotes; i++ {
					good(t, vote(s, fmt.Sprintf("Org%dMSP", i), "r", label))
				}
				bad(t, invokeAs(s, "f", "Org1MSP", "FinalizeReport", "r"))
				good(t, vote(s, fmt.Sprintf("Org%dMSP", rec.RequiredVotes+1), "r", label))
				got := record(t, s, "r")
				if got.Status != statusFinal || got.FinalLabel != label {
					t.Fatal(got)
				}
				bad(t, invokeAs(s, "f", "Org1MSP", "FinalizeReport", "r"))
			})
		}
	}
}
func TestSubmitRequiresTwoIndependentReviewers(t *testing.T) {
	s := setup(t, 2)
	bad(t, submitReport(s, "s", "Org1MSP", "r"))
}
func TestSelfVoteAndConflictingDuplicateRejected(t *testing.T) {
	s := setup(t, 5)
	good(t, submitReport(s, "s", "Org1MSP", "r"))
	bad(t, vote(s, "Org1MSP", "r", "1"))
	good(t, vote(s, "Org2MSP", "r", "1"))
	good(t, vote(s, "Org2MSP", "r", "1"))
	bad(t, vote(s, "Org2MSP", "r", "0"))
	if len(record(t, s, "r").FactChecks) != 1 {
		t.Fatal("retry duplicated vote")
	}
}
func TestTieIsDisputedWithoutFactualVerdict(t *testing.T) {
	s := setup(t, 5)
	good(t, submitReport(s, "s", "Org1MSP", "r"))
	for i, label := range []string{"0", "1", "0", "1"} {
		good(t, vote(s, fmt.Sprintf("Org%dMSP", i+2), "r", label))
	}
	rec := record(t, s, "r")
	if rec.Status != statusDisputed || rec.FinalLabel != "" {
		t.Fatal(rec)
	}
	bad(t, invokeAs(s, "f", "Org1MSP", "FinalizeReport", "r"))
}
func TestLateVotesAndExpiryUseProposalTime(t *testing.T) {
	s := setup(t, 3)
	s.TxTimestamp = timestamppb.New(time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC))
	good(t, submitReport(s, "s", "Org1MSP", "r"))
	good(t, vote(s, "Org2MSP", "r", "1"))
	s.Attrs = map[string]string{"role": "official"}
	bad(t, invokeAs(s, "e", "Org1MSP", "ExpireReport", "r"))
	s.TxTimestamp = timestamppb.New(time.Date(2026, 1, 4, 0, 0, 0, 0, time.UTC))
	s.Attrs = map[string]string{"role": "fact_checker"}
	bad(t, vote(s, "Org3MSP", "r", "1"))
	bad(t, invokeAs(s, "f", "Org1MSP", "FinalizeReport", "r"))
	s.Attrs = map[string]string{"role": "official"}
	good(t, invokeAs(s, "e", "Org1MSP", "ExpireReport", "r"))
	if record(t, s, "r").Status != statusExpired {
		t.Fatal("not expired")
	}
}
func TestBootstrapAndRoleBoundaries(t *testing.T) {
	s := setup(t, 3)
	s.Attrs = map[string]string{"role": "official"}
	bad(t, invokeAs(s, "cfg2", "Org1MSP", "SetFoundingOrgLimit", "4"))
	bad(t, invokeAs(s, "reg", "Org4MSP", "RegisterOrg"))
	s.Attrs = map[string]string{"role": "fact_checker"}
	good(t, submitReport(s, "s", "Org1MSP", "r"))
	for _, role := range []string{"official", "observer", ""} {
		s.Attrs = map[string]string{"role": role}
		bad(t, submitReport(s, "s2", "Org2MSP", "new"))
		bad(t, vote(s, "Org2MSP", "r", "1"))
		bad(t, invokeAs(s, "f", "Org2MSP", "FinalizeReport", "r"))
		good(t, invokeAs(s, "q", "Org2MSP", "QueryReport", "r"))
	}
	empty := newTestStub(t)
	empty.Attrs = map[string]string{"role": "observer"}
	bad(t, invokeAs(empty, "reg", "Org1MSP", "RegisterOrg"))
}
func TestFactCheckerCanSubmitAndVoteButNotGovern(t *testing.T) {
	s := setup(t, 3)
	s.Attrs = map[string]string{"role": "fact_checker"}
	good(t, submitReport(s, "s", "Org1MSP", "r"))
	good(t, vote(s, "Org2MSP", "r", "1"))
	bad(t, invokeAs(s, "f", "Org2MSP", "FinalizeReport", "r"))
	good(t, vote(s, "Org3MSP", "r", "1"))
	bad(t, invokeAs(s, "govern", "Org1MSP", "RequestOrgAdmission", "Fourth", "media"))
	bad(t, invokeAs(s, "expire", "Org1MSP", "ExpireReport", "r"))
}

func TestOfficialCannotWriteContentOrOverrideFinalVerdict(t *testing.T) {
	s := setup(t, 3)
	good(t, submitReport(s, "s", "Org1MSP", "r"))
	s.Attrs = map[string]string{"role": "official"}
	bad(t, submitReport(s, "s2", "Org1MSP", "new"))
	payload, _ := json.Marshal(Submission{ReportID: "new", ContentHash: validHash, ClaimCID: "cid", Label: "1", Confidence: .9, ModelVersion: "v", Timestamp: validTs})
	bad(t, invokeAs(s, "s3", "Org1MSP", "SubmitReport", string(payload)))
	bad(t, vote(s, "Org2MSP", "r", "1"))
	bad(t, invokeAs(s, "note", "Org1MSP", "RecordReportNote", "r", "note", "cid", validHash))
	if len(record(t, s, "r").FactChecks) != 0 {
		t.Fatal("official changed reviews")
	}
	s.Attrs = map[string]string{"role": "fact_checker"}
	good(t, vote(s, "Org2MSP", "r", "1"))
	good(t, vote(s, "Org3MSP", "r", "1"))
	s.Attrs = map[string]string{"role": "official"}
	s.TxTimestamp = timestamppb.New(time.Now().Add(96 * time.Hour))
	bad(t, invokeAs(s, "expire-final", "Org1MSP", "ExpireReport", "r"))
	if record(t, s, "r").Status != statusFinal {
		t.Fatal("expiry replaced final verdict")
	}
}
func TestAdmissionDoesNotChangeExistingReviewerSet(t *testing.T) {
	s := setup(t, 3)
	good(t, submitReport(s, "s", "Org1MSP", "r"))
	s.Attrs = map[string]string{"role": "official"}
	good(t, invokeAs(s, "a", "Org4MSP", "RequestOrgAdmission", "Fourth", "media"))
	bad(t, invokeAs(s, "self", "Org4MSP", "VoteOnOrgAdmission", "Org4MSP", "1"))
	for _, org := range []string{"Org1MSP", "Org2MSP"} {
		good(t, invokeAs(s, "ad-"+org, org, "VoteOnOrgAdmission", "Org4MSP", "1"))
	}
	good(t, invokeAs(s, "fa", "Org1MSP", "FinalizeOrgAdmission", "Org4MSP"))
	s.Attrs = map[string]string{"role": "fact_checker"}
	bad(t, vote(s, "Org4MSP", "r", "1"))
	if record(t, s, "r").RequiredVotes != 2 {
		t.Fatal("quorum changed")
	}
	s.Attrs = map[string]string{"role": "official"}
	bad(t, invokeAs(s, "ad", "Org1MSP", "VoteOnOrgAdmission", "Org4MSP", "admit"))
}
func TestSubmissionRetryAndConflict(t *testing.T) {
	s := setup(t, 3)
	good(t, submitReport(s, "s", "Org1MSP", "r"))
	good(t, submitReport(s, "retry", "Org1MSP", "r"))
	bad(t, submitReport(s, "other", "Org2MSP", "r"))
	bad(t, invokeAs(s, "bad", "Org1MSP", "Submit", "r", validHash, "0", "0.97", "model-v1", validTs))
}
func TestInvalidEvidenceAndInputRejected(t *testing.T) {
	if validateReportInput("r", validHash, "1", "v", validTs, math.NaN()) == nil {
		t.Fatal("NaN accepted")
	}
	s := setup(t, 3)
	good(t, submitReport(s, "s", "Org1MSP", "r"))
	bad(t, invokeAs(s, "v", "Org2MSP", "SubmitFactCheck", "r", "1", "", "short"))
	bad(t, invokeAs(s, "hash", "Org2MSP", "SubmitFactCheck", "r", "1", "valid-cid", "short"))
	bad(t, invokeAs(s, "label", "Org2MSP", "SubmitFactCheck", "r", "2", "valid-cid", validHash))
	for _, args := range [][]string{{"r2", "short", "1", ".9", "v", validTs}, {"r2", validHash, "2", ".9", "v", validTs}, {"r2", validHash, "1", "1.1", "v", validTs}} {
		bad(t, invokeAs(s, "sbad", "Org1MSP", append([]string{"Submit"}, args...)...))
	}
}

func TestNonFiniteWireInputDoesNotTerminateService(t *testing.T) {
	s := setup(t, 3)
	for _, value := range []string{"NaN", "+Inf", "-Inf"} {
		bad(t, invokeAs(s, "bad-number", "Org1MSP", "Submit", "bad", validHash, "1", value, "v", validTs))
	}
	good(t, submitReport(s, "valid-after-malformed", "Org1MSP", "valid"))
}

func TestSupersessionAndLegacyWritesRetired(t *testing.T) {
	s := setup(t, 3)
	good(t, submitReport(s, "s", "Org1MSP", "old"))
	good(t, vote(s, "Org2MSP", "old", "1"))
	good(t, vote(s, "Org3MSP", "old", "1"))
	sub := Submission{ReportID: "new", ContentHash: validHash, ClaimCID: "cid", Label: "0", Confidence: .9, ModelVersion: "v", Timestamp: validTs, Supersedes: "old"}
	payload, _ := json.Marshal(sub)
	bad(t, invokeAs(s, "c", "Org1MSP", "SubmitReport", string(payload)))
	bad(t, invokeAs(s, "i", "Org1MSP", "RecordIntervention", "old", "notice", "cid", validHash))
	bad(t, invokeAs(s, "f", "Org1MSP", "FinalizeReport", "old"))
	if record(t, s, "old").Status != statusFinal {
		t.Fatal("parent changed")
	}
}

func TestNotesEnforceSubmitterOwnershipAndPreserveEveryStatus(t *testing.T) {
	for _, status := range []string{statusPending, statusUnderReview, statusFinal, statusDisputed, statusExpired, statusSuperseded} {
		t.Run(status, func(t *testing.T) {
			s := setup(t, 3)
			good(t, submitReport(s, "s", "Org1MSP", "r"))
			// Seed terminal/historical fixtures directly, without re-enabling
			// removed transitions solely to manufacture the test fixture.
			original := record(t, s, "r")
			original.Status = status
			key, _ := s.CreateCompositeKey("pred", []string{"r"})
			before, _ := json.Marshal(original)
			s.State[key] = before
			for _, role := range []string{"fact_checker"} {
				s.Attrs = map[string]string{"role": role}
				good(t, invokeAs(s, "n-"+role, "Org1MSP", "RecordReportNote", "r", role, "cid-"+role, validHash))
				good(t, invokeAs(s, "retry", "Org1MSP", "RecordReportNote", "r", role, "cid-"+role, validHash))
				bad(t, invokeAs(s, "conflict", "Org1MSP", "RecordReportNote", "r", role, "other-cid", validHash))
				bad(t, invokeAs(s, "other", "Org2MSP", "RecordReportNote", "r", "forbidden", "cid", validHash))
			}
			s.Attrs = map[string]string{"role": "observer"}
			bad(t, invokeAs(s, "observer", "Org1MSP", "RecordReportNote", "r", "forbidden", "cid", validHash))
			r := invokeAs(s, "read", "Org2MSP", "QueryReportNotes", "r")
			good(t, r)
			var notes []ReportNote
			if err := json.Unmarshal(r.Payload, &notes); err != nil {
				t.Fatal(err)
			}
			if len(notes) != 1 || notes[0].Organisation != "Org1MSP" || notes[0].TxID == "" {
				t.Fatal(notes)
			}
			if string(s.State[key]) != string(before) {
				t.Fatal("note modified report state")
			}
		})
	}
}

func TestNoteValidationAndLegacyReads(t *testing.T) {
	s := setup(t, 3)
	good(t, submitReport(s, "s", "Org1MSP", "r"))
	for _, args := range [][]string{{"r", " ", "cid", validHash}, {"r", "id", "", "short"}, {"r", "id", "cid", "short"}, {"missing", "id", "cid", validHash}} {
		bad(t, invokeAs(s, "bad", "Org1MSP", append([]string{"RecordReportNote"}, args...)...))
	}
	old := Intervention{"notice", "r", "Org2MSP", "old-cid", validHash, validTs, "old-tx"}
	key, _ := s.CreateCompositeKey("intervention", []string{"r", "Org2MSP", "notice"})
	s.State[key], _ = json.Marshal(old)
	r := invokeAs(s, "legacy", "Org1MSP", "QueryInterventions", "r")
	good(t, r)
	var legacy []Intervention
	if err := json.Unmarshal(r.Payload, &legacy); err != nil {
		t.Fatal(err)
	}
	if len(legacy) != 1 || legacy[0].TxID != "old-tx" {
		t.Fatal(legacy)
	}
}

func TestPaginationAndStatusIndex(t *testing.T) {
	s := setup(t, 3)
	for _, id := range []string{"a", "b", "c"} {
		good(t, submitReport(s, "s"+id, "Org1MSP", id))
	}
	r := invokeAs(s, "p", "Org1MSP", "QueryReportsPage", statusPending, "2", "")
	good(t, r)
	var page ReportPage
	json.Unmarshal(r.Payload, &page)
	if len(page.Items) != 2 || page.NextBookmark == "" {
		t.Fatal(page)
	}
	r = invokeAs(s, "p2", "Org1MSP", "QueryReportsPage", statusPending, "2", page.NextBookmark)
	good(t, r)
	json.Unmarshal(r.Payload, &page)
	if len(page.Items) != 1 {
		t.Fatal(page)
	}
	good(t, vote(s, "Org2MSP", "a", "1"))
	good(t, vote(s, "Org3MSP", "a", "1"))
	r = invokeAs(s, "p3", "Org1MSP", "QueryReportsPage", statusPending, "50", "")
	good(t, r)
	json.Unmarshal(r.Payload, &page)
	if len(page.Items) != 2 {
		t.Fatal(page)
	}
	bad(t, invokeAs(s, "p4", "Org1MSP", "QueryReportsPage", "", "201", ""))
}
