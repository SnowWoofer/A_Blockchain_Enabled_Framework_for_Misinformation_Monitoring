package main

import (
	"encoding/json"
	"math"
	"strings"
	"testing"
	"time"

	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
	"github.com/hyperledger/fabric-protos-go-apiv2/peer"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const validHash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
const validTs = "2025-01-01T00:00:00Z"

func newTestStub(t *testing.T) *MockStub {
	t.Helper()
	cc, err := contractapi.NewChaincode(&MisinformationContract{})
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
	for _, a := range args[1:] { //append rest fo args to call
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

// Organisation Registration

func TestRegisterOrgAndList(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP")

	var orgs []*RegisteredOrg
	if res := invokeAs(stub, "tx-list", "Org1MSP", "ListRegisteredOrgs"); res.Status != 200 {
		t.Fatalf("ListRegisteredOrgs failed: %s", res.Message)
	} else if err := json.Unmarshal(res.Payload, &orgs); err != nil {
		t.Fatalf("bad payload: %v", err)
	}
	if len(orgs) != 3 {
		t.Fatalf("expected 3 registered orgs, got %d", len(orgs))
	}
}

func TestBootstrapClosedAfterFoundingLimit(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP")
	if res := invokeAs(stub, "tx4", "Org4MSP", "RegisterOrg"); res.Status == 200 {
		t.Fatalf("RegisterOrg should be closed once the founding limit is reached")
	}
}

// Admission Workflow 
func TestAdmissionWorkflow(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP", "Org2MSP")

	if res := invokeAs(stub, "tx1", "Org3MSP", "RequestOrgAdmission", "Org 3", "ngo"); res.Status != 200 {
		t.Fatalf("RequestOrgAdmission failed: %s", res.Message)
	}
	if res := invokeAs(stub, "tx2", "Org3MSP", "RequestOrgAdmission", "Org 3", "ngo"); res.Status == 200 {
		t.Fatalf("duplicate admission request should be rejected")
	}

	if res := invokeAs(stub, "tx3", "Org3MSP", "VoteOnOrgAdmission", "Org3MSP", "1"); res.Status == 200 {
		t.Fatalf("candidate should not vote on its own admission")
	}
	if res := invokeAs(stub, "tx4", "Org5MSP", "VoteOnOrgAdmission", "Org3MSP", "1"); res.Status == 200 {
		t.Fatalf("unregistered org should not vote on admission")
	}

	if res := invokeAs(stub, "tx5", "Org1MSP", "VoteOnOrgAdmission", "Org3MSP", "1"); res.Status != 200 {
		t.Fatalf("org1 vote failed: %s", res.Message)
	}
	if res := invokeAs(stub, "tx6", "Org2MSP", "VoteOnOrgAdmission", "Org3MSP", "1"); res.Status != 200 {
		t.Fatalf("org2 vote failed: %s", res.Message)
	}
	if res := invokeAs(stub, "tx7", "Org2MSP", "FinalizeOrgAdmission", "Org3MSP"); res.Status != 200 {
		t.Fatalf("finalize admission failed: %s", res.Message)
	}

	if res := submitReport(stub, "tx8", "Org3MSP", "rep-1"); res.Status != 200 {
		t.Fatalf("admitted org should be able to submit: %s", res.Message)
	}
}

// Submit and Query 

func TestUnregisteredCannotSubmit(t *testing.T) {
	stub := newTestStub(t)
	if res := submitReport(stub, "tx1", "Org5MSP", "rep-1"); res.Status == 200 {
		t.Fatalf("unregistered org should not be able to submit")
	}
}

func TestSubmitAndQuery(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP")

	if res := submitReport(stub, "tx1", "Org1MSP", "rep-42"); res.Status != 200 {
		t.Fatalf("Submit failed: %s", res.Message)
	}

	res := invokeAs(stub, "tx2", "Org1MSP", "QueryReport", "rep-42")
	if res.Status != 200 {
		t.Fatalf("QueryReport failed: %s", res.Message)
	}
	var record ReportRecord
	if err := json.Unmarshal(res.Payload, &record); err != nil {
		t.Fatalf("bad payload: %v", err)
	}
	if record.ReportID != "rep-42" || record.ProposedLabel != "1" || record.Confidence != 0.97 {
		t.Fatalf("unexpected record: %+v", record)
	}
	if record.Status != statusPending {
		t.Fatalf("expected PENDING, got %s", record.Status)
	}
	if record.SubmittedBy != "Org1MSP" {
		t.Fatalf("expected submitted_by Org1MSP, got %s", record.SubmittedBy)
	}
	if record.FactCheckDeadline == "" {
		t.Fatalf("expected fact_check_deadline to be set")
	}
}

func TestDuplicateRejected(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP")
	if res := submitReport(stub, "tx1", "Org1MSP", "rep-42"); res.Status != 200 {
		t.Fatalf("first submit failed: %s", res.Message)
	}
	if res := submitReport(stub, "tx2", "Org1MSP", "rep-42"); res.Status == 200 {
		t.Fatalf("duplicate submit should have been rejected")
	}
}

func TestInvalidInputsRejected(t *testing.T) {
	cases := [][]string{
		{"", validHash, "1", "0.5", "model", validTs},
		{"rep", validHash, "1", "0.5", "model", validTs},
		{"rep2", validHash, "2", "0.5", "model", validTs},
		{"rep3", validHash, "1", "1.5", "model", validTs},
		{"rep4", "short", "1", "0.5", "model", validTs},
		{"rep5", validHash, "1", "0.5", "", validTs},
		{"rep6", validHash, "1", "0.5", "model", "not-a-date"},
		{"rep7", validHash, "1", "0.5", "model", ""},
	}
	for i, args := range cases {
		stub := newTestStub(t)
		invokeAs(stub, "tx-reg", "Org1MSP", "RegisterOrg")
		if i == 1 {
			invokeAs(stub, "tx-pre", "Org1MSP", "Submit", "rep", validHash, "1", "0.5", "model", validTs)
		}
		res := invokeAs(stub, "tx1", "Org1MSP", append([]string{"Submit"}, args...)...)
		if res.Status == 200 {
			t.Fatalf("case %d should have been rejected: %s", i, res.Message)
		}
	}
}

// Fact-Check and Finalization 

func TestFactCheckAndFinalize(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP")

	if res := submitReport(stub, "tx1", "Org1MSP", "rep-42"); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}

	// first fact-check -> UNDER_REVIEW
	if res := invokeAs(stub, "tx2", "Org2MSP", "SubmitFactCheck", "rep-42", "1"); res.Status != 200 {
		t.Fatalf("org2 fact-check failed: %s", res.Message)
	}
	res := invokeAs(stub, "tx-check1", "Org1MSP", "QueryReport", "rep-42")
	var rec ReportRecord
	json.Unmarshal(res.Payload, &rec)
	if rec.Status != statusUnderReview {
		t.Fatalf("expected UNDER_REVIEW after first fact-check, got %s", rec.Status)
	}

	// org2 cannot fact-check twice
	if res := invokeAs(stub, "tx3", "Org2MSP", "SubmitFactCheck", "rep-42", "0"); res.Status == 200 {
		t.Fatalf("double fact-check should have been rejected")
	}

	// finalize with only 1 fact-check must fail
	if res := invokeAs(stub, "tx4", "Org2MSP", "FinalizeReport", "rep-42"); res.Status == 200 {
		t.Fatalf("finalize before quorum should fail")
	}

	// second fact-check -> early acceptance triggers (2 "1" votes >= ceil(2/3*3)=2)
	if res := invokeAs(stub, "tx5", "Org3MSP", "SubmitFactCheck", "rep-42", "1"); res.Status != 200 {
		t.Fatalf("org3 fact-check failed: %s", res.Message)
	}

	// report should now be FINAL via early acceptance
	res = invokeAs(stub, "tx6", "Org3MSP", "QueryReport", "rep-42")
	if res.Status != 200 || json.Unmarshal(res.Payload, &rec) != nil {
		t.Fatalf("query failed: %s", res.Message)
	}
	if rec.Status != statusFinal {
		t.Fatalf("expected FINAL (early acceptance), got %s", rec.Status)
	}
	if rec.FinalLabel != "1" {
		t.Fatalf("expected FinalLabel 1, got %s", rec.FinalLabel)
	}
	if len(rec.FactChecks) != 2 {
		t.Fatalf("expected 2 fact-checks, got %d", len(rec.FactChecks))
	}
}

//  Submitter votes 

func TestSubmitterVotesButCannotDecideAlone(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP")
	if res := submitReport(stub, "tx1", "Org1MSP", "rep-own"); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}
	// Org1 (the submitter): "1" -> 1 of 2 required YES, still open
	if res := invokeAs(stub, "tx2", "Org1MSP", "SubmitFactCheck", "rep-own", "1"); res.Status != 200 {
		t.Fatalf("submitter fact-check failed: %s", res.Message)
	}
	res := invokeAs(stub, "tx-q1", "Org2MSP", "QueryReport", "rep-own")
	var rec ReportRecord
	json.Unmarshal(res.Payload, &rec)
	if rec.Status != statusUnderReview {
		t.Fatalf("the submitter's vote alone must not settle its report, got %s", rec.Status)
	}
	if res := invokeAs(stub, "tx3", "Org1MSP", "SubmitFactCheck", "rep-own", "1"); res.Status == 200 {
		t.Fatalf("the submitter must not be able to vote twice")
	}
	// Org2: "1" -> a second org agrees, FINAL
	if res := invokeAs(stub, "tx4", "Org2MSP", "SubmitFactCheck", "rep-own", "1"); res.Status != 200 {
		t.Fatalf("org2 fact-check failed: %s", res.Message)
	}
	res = invokeAs(stub, "tx-q2", "Org2MSP", "QueryReport", "rep-own")
	json.Unmarshal(res.Payload, &rec)
	if rec.Status != statusFinal || rec.FinalLabel != "1" {
		t.Fatalf("expected FINAL/1 once a second org agrees, got %s/%s", rec.Status, rec.FinalLabel)
	}
}

//  Manual finalize (only after the fact-check window) 

func TestFinalizeBlockedDuringWindow(t *testing.T) {
	stub := newTestStub(t)
	// 6 orgs: required = 4, eligible voters = 6
	invokeAs(stub, "tx-cfg", "Org1MSP", "SetFoundingOrgLimit", "6")
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP", "Org4MSP", "Org5MSP", "Org6MSP")
	if res := submitReport(stub, "tx1", "Org1MSP", "rep-win"); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}
	invokeAs(stub, "tx2", "Org2MSP", "SubmitFactCheck", "rep-win", "1")
	invokeAs(stub, "tx3", "Org3MSP", "SubmitFactCheck", "rep-win", "1")

	// 2 of 6 orgs agree: a lone official must not be able to close the vote early
	res := invokeAs(stub, "tx4", "Org4MSP", "FinalizeReport", "rep-win")
	if res.Status == 200 {
		t.Fatalf("finalize inside the fact-check window must be rejected")
	}
	if !strings.Contains(res.Message, "fact-check window") {
		t.Fatalf("expected fact-check window error, got %q", res.Message)
	}
}

func TestManualFinalizeWithMajority(t *testing.T) {
	stub := newTestStub(t)
	// 6 orgs: required = ceil(2/3*6) = 4, eligible voters = 6
	invokeAs(stub, "tx-cfg", "Org1MSP", "SetFoundingOrgLimit", "6")
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP", "Org4MSP", "Org5MSP", "Org6MSP")

	// submitted long ago, so the fact-check window has closed
	stub.TxTimestamp = timestamppb.New(time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC))
	if res := submitReport(stub, "tx1", "Org1MSP", "rep-mf"); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}

	// Org2: "1", Org3: "1", Org4: "0" -> tally 2-1, 3 orgs left: 2+3 >= 4, still open
	invokeAs(stub, "tx2", "Org2MSP", "SubmitFactCheck", "rep-mf", "1")
	invokeAs(stub, "tx3", "Org3MSP", "SubmitFactCheck", "rep-mf", "1")
	if res := invokeAs(stub, "tx4", "Org4MSP", "SubmitFactCheck", "rep-mf", "0"); res.Status != 200 {
		t.Fatalf("org4 fact-check failed: %s", res.Message)
	}
	res := invokeAs(stub, "tx-q1", "Org5MSP", "QueryReport", "rep-mf")
	var rec ReportRecord
	json.Unmarshal(res.Payload, &rec)
	if rec.Status != statusUnderReview {
		t.Fatalf("expected UNDER_REVIEW, got %s", rec.Status)
	}

	// window closed, 3 fact-checks, majority is "1"
	if res := invokeAs(stub, "tx5", "Org5MSP", "FinalizeReport", "rep-mf"); res.Status != 200 {
		t.Fatalf("finalize failed: %s", res.Message)
	}
	res = invokeAs(stub, "tx-q2", "Org5MSP", "QueryReport", "rep-mf")
	json.Unmarshal(res.Payload, &rec)
	if rec.Status != statusFinal {
		t.Fatalf("expected FINAL, got %s", rec.Status)
	}
	if rec.FinalLabel != "1" {
		t.Fatalf("expected FinalLabel 1, got %s", rec.FinalLabel)
	}
}

func TestRejectionByFactChecks(t *testing.T) {
	stub := newTestStub(t)
	// 9 orgs: required = ceil(2/3*9) = 6, eligible voters = 9
	invokeAs(stub, "tx-cfg", "Org1MSP", "SetFoundingOrgLimit", "9")
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP", "Org4MSP", "Org5MSP",
		"Org6MSP", "Org7MSP", "Org8MSP", "Org9MSP")

	stub.TxTimestamp = timestamppb.New(time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC))
	if res := submitReport(stub, "tx1", "Org1MSP", "rep-rj"); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}

	// Org2: "0", Org3: "0" -> tally1=0, 7 orgs left: 0+7 >= 6, still open
	invokeAs(stub, "tx2", "Org2MSP", "SubmitFactCheck", "rep-rj", "0")
	if res := invokeAs(stub, "tx3", "Org3MSP", "SubmitFactCheck", "rep-rj", "0"); res.Status != 200 {
		t.Fatalf("org3 fact-check failed: %s", res.Message)
	}

	// window closed: majority of the votes cast is "0" (not misinformation)
	if res := invokeAs(stub, "tx4", "Org4MSP", "FinalizeReport", "rep-rj"); res.Status != 200 {
		t.Fatalf("finalize failed: %s", res.Message)
	}
	res := invokeAs(stub, "tx-q", "Org4MSP", "QueryReport", "rep-rj")
	var rec ReportRecord
	json.Unmarshal(res.Payload, &rec)
	if rec.Status != statusFinal {
		t.Fatalf("expected FINAL, got %s", rec.Status)
	}
	if rec.FinalLabel != "0" {
		t.Fatalf("expected FinalLabel 0, got %s", rec.FinalLabel)
	}
}

// Early Acceptance 

func TestEarlyAcceptance(t *testing.T) {
	stub := newTestStub(t)
	// 6 orgs: required = 4, eligible voters = 6 (two are left over after FINAL)
	invokeAs(stub, "tx-cfg", "Org1MSP", "SetFoundingOrgLimit", "6")
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP", "Org4MSP", "Org5MSP", "Org6MSP")

	if res := submitReport(stub, "tx1", "Org1MSP", "rep-ea"); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}

	for i, msp := range []string{"Org2MSP", "Org3MSP", "Org4MSP", "Org5MSP"} {
		if res := invokeAs(stub, "tx-v"+msp, msp, "SubmitFactCheck", "rep-ea", "1"); res.Status != 200 {
			t.Fatalf("vote %d (%s) failed: %s", i, msp, res.Message)
		}
	}

	res := invokeAs(stub, "tx4", "Org1MSP", "QueryReport", "rep-ea")
	var rec ReportRecord
	if res.Status != 200 || json.Unmarshal(res.Payload, &rec) != nil {
		t.Fatalf("query failed: %s", res.Message)
	}
	if rec.Status != statusFinal {
		t.Fatalf("expected FINAL via early acceptance, got %s", rec.Status)
	}
	if rec.FinalLabel != "1" {
		t.Fatalf("expected FinalLabel 1, got %s", rec.FinalLabel)
	}
	if rec.FinalizedBy != "Org5MSP" {
		t.Fatalf("expected FinalizedBy Org5MSP, got %s", rec.FinalizedBy)
	}

	// no more fact-checks accepted after FINAL
	if res := invokeAs(stub, "tx5", "Org6MSP", "SubmitFactCheck", "rep-ea", "1"); res.Status == 200 {
		t.Fatalf("fact-check after FINAL should be rejected")
	}
}

// Early Rejection 

func TestEarlyRejection(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP")

	if res := submitReport(stub, "tx1", "Org1MSP", "rep-er"); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}

	// Org2: "0" -> Org1 and Org3 left: 0+2 >= 2, one NO does not settle it
	if res := invokeAs(stub, "tx2", "Org2MSP", "SubmitFactCheck", "rep-er", "0"); res.Status != 200 {
		t.Fatalf("org2 fact-check failed: %s", res.Message)
	}
	res := invokeAs(stub, "tx-q1", "Org1MSP", "QueryReport", "rep-er")
	var rec ReportRecord
	json.Unmarshal(res.Payload, &rec)
	if rec.Status != statusUnderReview {
		t.Fatalf("expected UNDER_REVIEW after a single NO, got %s", rec.Status)
	}

	// Org3: "0" -> only Org1 left: 0+1 < 2, the quorum is out of reach
	if res := invokeAs(stub, "tx3", "Org3MSP", "SubmitFactCheck", "rep-er", "0"); res.Status != 200 {
		t.Fatalf("org3 fact-check failed: %s", res.Message)
	}
	res = invokeAs(stub, "tx4", "Org1MSP", "QueryReport", "rep-er")
	if res.Status != 200 || json.Unmarshal(res.Payload, &rec) != nil {
		t.Fatalf("query failed: %s", res.Message)
	}
	if rec.Status != statusRejected {
		t.Fatalf("expected REJECTED via early rejection, got %s", rec.Status)
	}
}

func TestSplitVoteStaysOpenForSubmitter(t *testing.T) {
	for _, tc := range []struct{ vote, want string }{{"1", statusFinal}, {"0", statusRejected}} {
		stub := newTestStub(t)
		registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP")
		if res := submitReport(stub, "tx1", "Org1MSP", "rep-split"); res.Status != 200 {
			t.Fatalf("submit failed: %s", res.Message)
		}

		// Org2: "1", Org3: "0" -> 1 + 1 remaining (the submitter) >= 2, still open
		invokeAs(stub, "tx2", "Org2MSP", "SubmitFactCheck", "rep-split", "1")
		invokeAs(stub, "tx3", "Org3MSP", "SubmitFactCheck", "rep-split", "0")
		res := invokeAs(stub, "tx-q1", "Org1MSP", "QueryReport", "rep-split")
		var rec ReportRecord
		json.Unmarshal(res.Payload, &rec)
		if rec.Status != statusUnderReview {
			t.Fatalf("expected UNDER_REVIEW on a 1-1 split, got %s", rec.Status)
		}

		// Org1 sides with one of them: its vote plus Org2's reaches 2 YES, or not
		if res := invokeAs(stub, "tx4", "Org1MSP", "SubmitFactCheck", "rep-split", tc.vote); res.Status != 200 {
			t.Fatalf("submitter fact-check failed: %s", res.Message)
		}
		res = invokeAs(stub, "tx-q2", "Org1MSP", "QueryReport", "rep-split")
		json.Unmarshal(res.Payload, &rec)
		if rec.Status != tc.want {
			t.Fatalf("submitter voted %s: expected %s, got %s", tc.vote, tc.want, rec.Status)
		}
	}
}

// Tie Rejected on Finalize 

func TestFinalizeTieRejected(t *testing.T) {
	stub := newTestStub(t)
	// 9 orgs: required = 6, eligible voters = 9
	invokeAs(stub, "tx-cfg", "Org1MSP", "SetFoundingOrgLimit", "9")
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP", "Org4MSP", "Org5MSP",
		"Org6MSP", "Org7MSP", "Org8MSP", "Org9MSP")

	stub.TxTimestamp = timestamppb.New(time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC))
	if res := submitReport(stub, "tx1", "Org1MSP", "rep-tie"); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}

	// Org2: "1", Org3: "0" -> tie, 1 + 7 remaining >= 6, still open
	invokeAs(stub, "tx2", "Org2MSP", "SubmitFactCheck", "rep-tie", "1")
	invokeAs(stub, "tx3", "Org3MSP", "SubmitFactCheck", "rep-tie", "0")

	res := invokeAs(stub, "tx-q", "Org5MSP", "QueryReport", "rep-tie")
	var rec ReportRecord
	json.Unmarshal(res.Payload, &rec)
	if rec.Status != statusUnderReview {
		t.Fatalf("expected UNDER_REVIEW, got %s", rec.Status)
	}

	// finalize should fail: tie at 1-1
	if res := invokeAs(stub, "tx6", "Org5MSP", "FinalizeReport", "rep-tie"); res.Status == 200 {
		t.Fatalf("finalize with tie should be rejected")
	}

	// break the tie with a third fact-check
	if res := invokeAs(stub, "tx7", "Org4MSP", "SubmitFactCheck", "rep-tie", "1"); res.Status != 200 {
		t.Fatalf("org4 fact-check failed: %s", res.Message)
	}
	// now 2 "1" and 1 "0", majority is "1"
	if res := invokeAs(stub, "tx8", "Org6MSP", "FinalizeReport", "rep-tie"); res.Status != 200 {
		t.Fatalf("finalize after tie-break failed: %s", res.Message)
	}
	res = invokeAs(stub, "tx-q2", "Org6MSP", "QueryReport", "rep-tie")
	json.Unmarshal(res.Payload, &rec)
	if rec.Status != statusFinal {
		t.Fatalf("expected FINAL, got %s", rec.Status)
	}
	if rec.FinalLabel != "1" {
		t.Fatalf("expected FinalLabel 1, got %s", rec.FinalLabel)
	}
}

// Expiry 

func TestExpireReport(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP")

	submitReport(stub, "tx1", "Org1MSP", "rep-fresh")
	if res := invokeAs(stub, "tx2", "Org1MSP", "ExpireReport", "rep-fresh"); res.Status == 200 {
		t.Fatalf("fresh report should not be expirable")
	}

	stub.TxTimestamp = timestamppb.New(time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC))
	if res := submitReport(stub, "tx3", "Org1MSP", "rep-old"); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}
	if res := invokeAs(stub, "tx4", "Org1MSP", "ExpireReport", "rep-old"); res.Status != 200 {
		t.Fatalf("expire failed: %s", res.Message)
	}
	res := invokeAs(stub, "tx5", "Org1MSP", "QueryReport", "rep-old")
	var record ReportRecord
	if res.Status != 200 || json.Unmarshal(res.Payload, &record) != nil {
		t.Fatalf("query failed")
	}
	if record.Status != statusExpired {
		t.Fatalf("expected EXPIRED, got %s", record.Status)
	}
	if res := invokeAs(stub, "tx6", "Org1MSP", "SubmitFactCheck", "rep-old", "0"); res.Status == 200 {
		t.Fatalf("fact-check after expire should be rejected")
	}
}

// Query 

func TestQueryFactChecksAndHistory(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP")
	invokeAs(stub, "tx1", "Org1MSP", "Submit", "rep-1", validHash, "1", "0.9", "model-v1", validTs)
	invokeAs(stub, "tx2", "Org2MSP", "SubmitFactCheck", "rep-1", "1")
	invokeAs(stub, "tx3", "Org3MSP", "SubmitFactCheck", "rep-1", "1")

	res := invokeAs(stub, "tx4", "Org1MSP", "QueryReport", "rep-1")
	var record ReportRecord
	if res.Status != 200 || json.Unmarshal(res.Payload, &record) != nil {
		t.Fatalf("QueryReport failed: %s", res.Message)
	}
	if len(record.FactChecks) != 2 {
		t.Fatalf("expected 2 fact-checks, got %d", len(record.FactChecks))
	}

	res = invokeAs(stub, "tx5", "Org1MSP", "QueryReportHistory", "rep-1")
	if res.Status != 200 || len(res.Payload) == 0 {
		t.Fatalf("expected history entries: %s", res.Message)
	}
}

func TestQueryAllReports(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP")
	invokeAs(stub, "tx1", "Org1MSP", "Submit", "rep-1", validHash, "0", "0.80", "model-v1", validTs)
	invokeAs(stub, "tx2", "Org1MSP", "Submit", "rep-2", validHash, "1", "0.90", "model-v1", validTs)

	res := invokeAs(stub, "tx3", "Org1MSP", "QueryAllReports")
	var records []*ReportRecord
	if res.Status != 200 || json.Unmarshal(res.Payload, &records) != nil {
		t.Fatalf("QueryAll failed: %s", res.Message)
	}
	if len(records) != 2 {
		t.Fatalf("expected 2 records, got %d", len(records))
	}
}

// Quorum Math 

func TestQuorumFor(t *testing.T) {
	cases := []struct {
		n    int
		want int
	}{
		{1, 1},
		{2, 2},
		{3, 2},
		{4, 3},
		{5, 4},
		{6, 4},
		{9, 6},
		{10, 7},
	}
	for _, tc := range cases {
		got := quorumFor(tc.n)
		if got != tc.want {
			t.Errorf("quorumFor(%d) = %d, want %d", tc.n, got, tc.want)
		}
	}
}

func TestQuorumForMatchesEarlyAcceptanceThreshold(t *testing.T) {
	for n := 1; n <= 20; n++ {
		q := quorumFor(n)
		early := int(math.Ceil(2.0 / 3.0 * float64(n)))
		if q != early {
			t.Errorf("quorumFor(%d)=%d but ceil(2/3*%d)=%d — mismatch", n, q, n, early)
		}
	}
}

// ABAC (Attribute-Based Access Control) 

// newTestStubWithRole creates a stub where the caller identity carries the given "role" attribute in its X509 certificate.
func newTestStubWithRole(t *testing.T, role string) *MockStub {
	t.Helper()
	stub := newTestStub(t)
	stub.Attrs = map[string]string{"role": role}
	return stub
}

func TestABAC_FactCheckerCanSubmit(t *testing.T) {
	stub := newTestStubWithRole(t, "fact_checker")
	registerOrgs(t, stub, "Org1MSP")
	if res := submitReport(stub, "tx1", "Org1MSP", "rep-abac"); res.Status != 200 {
		t.Fatalf("fact_checker should be able to submit: %s", res.Message)
	}
}

func TestABAC_ObserverCannotSubmit(t *testing.T) {
	stub := newTestStubWithRole(t, "observer")
	registerOrgs(t, stub, "Org1MSP")
	if res := submitReport(stub, "tx1", "Org1MSP", "rep-abac"); res.Status == 200 {
		t.Fatalf("observer should not be able to submit")
	}
}

func TestABAC_NoRoleRejected(t *testing.T) {
	stub := newTestStub(t)
	stub.Attrs = nil // no role attribute
	registerOrgs(t, stub, "Org1MSP")
	if res := submitReport(stub, "tx1", "Org1MSP", "rep-abac"); res.Status == 200 {
		t.Fatalf("identity with no role should be rejected")
	}
}

func TestABAC_FactCheckerCanFactCheck(t *testing.T) {
	stub := newTestStubWithRole(t, "fact_checker")
	registerOrgs(t, stub, "Org1MSP", "Org2MSP")
	submitReport(stub, "tx1", "Org1MSP", "rep-abac")
	if res := invokeAs(stub, "tx2", "Org2MSP", "SubmitFactCheck", "rep-abac", "1"); res.Status != 200 {
		t.Fatalf("fact_checker should be able to fact-check: %s", res.Message)
	}
}

func TestABAC_ObserverCannotFactCheck(t *testing.T) {
	stub := newTestStubWithRole(t, "observer")
	registerOrgs(t, stub, "Org1MSP", "Org2MSP")
	submitReport(stub, "tx1", "Org1MSP", "rep-abac")
	if res := invokeAs(stub, "tx2", "Org2MSP", "SubmitFactCheck", "rep-abac", "1"); res.Status == 200 {
		t.Fatalf("observer should not be able to fact-check")
	}
}

func TestABAC_FactCheckerCannotFinalize(t *testing.T) {
	stub := newTestStubWithRole(t, "fact_checker")
	registerOrgs(t, stub, "Org1MSP", "Org2MSP")
	submitReport(stub, "tx1", "Org1MSP", "rep-abac")
	invokeAs(stub, "tx2", "Org2MSP", "SubmitFactCheck", "rep-abac", "1")
	// FinalizeReport requires "official"
	res := invokeAs(stub, "tx3", "Org1MSP", "FinalizeReport", "rep-abac")
	if res.Status == 200 {
		t.Fatalf("fact_checker should not be able to finalize")
	}
	if !strings.Contains(res.Message, "role") {
		t.Fatalf("expected an ABAC role error, got %q", res.Message)
	}
}

func TestABAC_ObserverCanQuery(t *testing.T) {
	stub := newTestStubWithRole(t, "official")
	registerOrgs(t, stub, "Org1MSP")
	submitReport(stub, "tx1", "Org1MSP", "rep-abac")

	// Create observer stub sharing the same ledger state
	obs := newTestStubWithRole(t, "observer")
	obs.State = stub.State
	if res := invokeAs(obs, "tx2", "Org1MSP", "QueryReport", "rep-abac"); res.Status != 200 {
		t.Fatalf("observer should be able to query: %s", res.Message)
	}
}
