package main

import (
	"encoding/json"
	"testing"
	"time"

	"github.com/hyperledger/fabric-contract-api-go/v2/contractapi"
	"github.com/hyperledger/fabric-protos-go-apiv2/peer"
	"google.golang.org/protobuf/types/known/timestamppb"
)

const validHash = "aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa"
const validTs = "2025-01-01T00:00:00Z"
const offChain = "https://server.example/api/reports/rep"

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
	}
	stub.args = [][]byte{}
	stub.signedProposal = &peer.SignedProposal{}
	return stub
}

// invokeAs sets the mock invoker MSP for the next call.
func invokeAs(stub *MockStub, txID, msp string, args ...string) *peer.Response {
	stub.TxID = txID
	stub.CreatorMSP = msp
	call := [][]byte{[]byte(args[0])}
	for _, a := range args[1:] {
		call = append(call, []byte(a))
	}
	return stub.MockInvoke(txID, call)
}

func registerOrgs(t *testing.T, stub *MockStub, msps ...string) {
	t.Helper()
	for _, msp := range msps {
		if res := invokeAs(stub, "tx-reg", msp, "RegisterOrg"); res.Status != 200 {
			t.Fatalf("RegisterOrg for %s failed: %s", msp, res.Message)
		}
	}
}

func submitReport(stub *MockStub, txID, msp, reportID string) *peer.Response {
	return invokeAs(stub, txID, msp,
		"SubmitReport", reportID, "nso", validHash, "1", "0.97", "model-v1", validTs, offChain+reportID)
}

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
	// bootstrap window (3) exceeded -> a 4th org must use admission voting
	if res := invokeAs(stub, "tx4", "Org4MSP", "RegisterOrg"); res.Status == 200 {
		t.Fatalf("RegisterOrg should be closed once the founding limit is reached")
	}
}

func TestAdmissionWorkflow(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP", "Org2MSP")

	// org3 applies (Tier-1 channel member, not yet Tier-2 stakeholder)
	if res := invokeAs(stub, "tx1", "Org3MSP", "RequestOrgAdmission", "Org 3", "ngo"); res.Status != 200 {
		t.Fatalf("RequestOrgAdmission failed: %s", res.Message)
	}
	if res := invokeAs(stub, "tx2", "Org3MSP", "RequestOrgAdmission", "Org 3", "ngo"); res.Status == 200 {
		t.Fatalf("duplicate admission request should be rejected")
	}

	// a candidate cannot vote on its own admission
	if res := invokeAs(stub, "tx3", "Org3MSP", "VoteOnOrgAdmission", "Org3MSP", "accept"); res.Status == 200 {
		t.Fatalf("candidate should not vote on its own admission")
	}
	// an unregistered org cannot vote on admissions
	if res := invokeAs(stub, "tx4", "Org5MSP", "VoteOnOrgAdmission", "Org3MSP", "accept"); res.Status == 200 {
		t.Fatalf("unregistered org should not vote on admission")
	}

	if res := invokeAs(stub, "tx5", "Org1MSP", "VoteOnOrgAdmission", "Org3MSP", "accept"); res.Status != 200 {
		t.Fatalf("org1 vote failed: %s", res.Message)
	}
	if res := invokeAs(stub, "tx6", "Org2MSP", "VoteOnOrgAdmission", "Org3MSP", "accept"); res.Status != 200 {
		t.Fatalf("org2 vote failed: %s", res.Message)
	}
	// 2 registered orgs -> quorum 2 -> admitted
	if res := invokeAs(stub, "tx7", "Org2MSP", "FinalizeOrgAdmission", "Org3MSP"); res.Status != 200 {
		t.Fatalf("finalize admission failed: %s", res.Message)
	}

	// org3 is now a stakeholder and can submit
	if res := submitReport(stub, "tx8", "Org3MSP", "rep-1"); res.Status != 200 {
		t.Fatalf("admitted org should be able to submit: %s", res.Message)
	}
}

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
		t.Fatalf("SubmitReport failed: %s", res.Message)
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
	if record.OffChainURI != offChain+"rep-42" {
		t.Fatalf("expected off_chain_uri, got %q", record.OffChainURI)
	}
	if record.VotingDeadline == "" {
		t.Fatalf("expected voting_deadline to be set")
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
		{"", "nso", validHash, "1", "0.5", "model", validTs, offChain},
		{"rep", "fr", validHash, "1", "0.5", "model", validTs, offChain},
		{"rep", "nso", validHash, "2", "0.5", "model", validTs, offChain},
		{"rep", "nso", validHash, "1", "1.5", "model", validTs, offChain},
		{"rep", "nso", "short", "1", "0.5", "model", validTs, offChain},
		{"rep", "nso", validHash, "1", "0.5", "", validTs, offChain},
		{"rep", "nso", validHash, "1", "0.5", "model", "not-a-date", offChain},
		{"rep", "nso", validHash, "1", "0.5", "model", validTs, ""},
	}
	for i, args := range cases {
		stub := newTestStub(t)
		invokeAs(stub, "tx-reg", "Org1MSP", "RegisterOrg")
		res := invokeAs(stub, "tx1", "Org1MSP", append([]string{"SubmitReport"}, args...)...)
		if res.Status == 200 {
			t.Fatalf("case %d should have been rejected", i)
		}
	}
}

func TestVotingWorkflow(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP")

	if res := submitReport(stub, "tx1", "Org1MSP", "rep-42"); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}

	if res := invokeAs(stub, "tx2", "Org1MSP", "CastVote", "rep-42", "accept"); res.Status != 200 {
		t.Fatalf("org1 vote failed: %s", res.Message)
	}
	// org1 cannot vote twice
	if res := invokeAs(stub, "tx3", "Org1MSP", "CastVote", "rep-42", "reject"); res.Status == 200 {
		t.Fatalf("double vote should have been rejected")
	}
	// finalizing with only 1 of 3 votes must fail (quorum 2)
	if res := invokeAs(stub, "tx4", "Org2MSP", "FinalizeReport", "rep-42"); res.Status == 200 {
		t.Fatalf("finalize before quorum should fail")
	}

	if res := invokeAs(stub, "tx5", "Org2MSP", "CastVote", "rep-42", "accept"); res.Status != 200 {
		t.Fatalf("org2 vote failed: %s", res.Message)
	}
	// quorum reached: 2 of 3 accepted -> FINAL
	if res := invokeAs(stub, "tx6", "Org3MSP", "FinalizeReport", "rep-42"); res.Status != 200 {
		t.Fatalf("finalize failed: %s", res.Message)
	}

	res := invokeAs(stub, "tx7", "Org3MSP", "QueryReport", "rep-42")
	var record ReportRecord
	if res.Status != 200 || json.Unmarshal(res.Payload, &record) != nil {
		t.Fatalf("query failed: %s", res.Message)
	}
	if record.Status != statusFinal {
		t.Fatalf("expected FINAL, got %s", record.Status)
	}
	if len(record.Votes) != 2 {
		t.Fatalf("expected 2 votes, got %d", len(record.Votes))
	}
	// votes are closed once FINAL
	if res := invokeAs(stub, "tx8", "Org3MSP", "CastVote", "rep-42", "accept"); res.Status == 200 {
		t.Fatalf("vote after finalize should be rejected")
	}
}

func TestRejectionWorkflow(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP", "Org2MSP")

	if res := invokeAs(stub, "tx1", "Org1MSP",
		"SubmitReport", "rep-7", "zul", validHash, "0", "0.6", "model-v1", validTs, offChain+"rep-7"); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}
	if res := invokeAs(stub, "tx2", "Org1MSP", "CastVote", "rep-7", "reject"); res.Status != 200 {
		t.Fatalf("org1 vote failed: %s", res.Message)
	}
	if res := invokeAs(stub, "tx3", "Org2MSP", "CastVote", "rep-7", "reject"); res.Status != 200 {
		t.Fatalf("org2 vote failed: %s", res.Message)
	}
	if res := invokeAs(stub, "tx4", "Org1MSP", "FinalizeReport", "rep-7"); res.Status != 200 {
		t.Fatalf("finalize failed: %s", res.Message)
	}
	res := invokeAs(stub, "tx5", "Org1MSP", "QueryReport", "rep-7")
	var record ReportRecord
	if res.Status != 200 || json.Unmarshal(res.Payload, &record) != nil {
		t.Fatalf("query failed")
	}
	if record.Status != statusRejected {
		t.Fatalf("expected REJECTED, got %s", record.Status)
	}
}

func TestExpireReport(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP")

	// A normal (fresh) submission stays within its window and cannot expire yet.
	submitReport(stub, "tx1", "Org1MSP", "rep-fresh")
	if res := invokeAs(stub, "tx2", "Org1MSP", "ExpireReport", "rep-fresh"); res.Status == 200 {
		t.Fatalf("fresh report should not be expirable")
	}

	// Submit with an anchored tx timestamp in the past -> deadline is in the past.
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
	if res := invokeAs(stub, "tx6", "Org1MSP", "CastVote", "rep-old", "accept"); res.Status == 200 {
		t.Fatalf("vote after expire should be rejected")
	}
}

func TestFinalizeAutoExpiresPastDeadline(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP")

	stub.TxTimestamp = timestamppb.New(time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC))
	submitReport(stub, "tx1", "Org1MSP", "rep-old")
	// past deadline with no quorum -> FinalizeReport transitions to EXPIRED
	if res := invokeAs(stub, "tx2", "Org1MSP", "FinalizeReport", "rep-old"); res.Status != 200 {
		t.Fatalf("finalize auto-expire failed: %s", res.Message)
	}
	res := invokeAs(stub, "tx3", "Org1MSP", "QueryReport", "rep-old")
	var record ReportRecord
	if res.Status != 200 || json.Unmarshal(res.Payload, &record) != nil {
		t.Fatalf("query failed")
	}
	if record.Status != statusExpired {
		t.Fatalf("expected EXPIRED, got %s", record.Status)
	}
}

func TestQueryVotesAndHistory(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP", "Org2MSP")
	invokeAs(stub, "tx1", "Org1MSP", "SubmitReport", "rep-1", "nso", validHash, "1", "0.9", "model-v1", validTs, offChain+"rep-1")
	invokeAs(stub, "tx2", "Org1MSP", "CastVote", "rep-1", "accept")
	invokeAs(stub, "tx3", "Org2MSP", "CastVote", "rep-1", "reject")

	res := invokeAs(stub, "tx4", "Org1MSP", "QueryVotes", "rep-1")
	var votes []*Vote
	if res.Status != 200 || json.Unmarshal(res.Payload, &votes) != nil {
		t.Fatalf("QueryVotes failed: %s", res.Message)
	}
	if len(votes) != 2 {
		t.Fatalf("expected 2 votes, got %d", len(votes))
	}

	res = invokeAs(stub, "tx5", "Org1MSP", "QueryReportHistory", "rep-1")
	if res.Status != 200 || len(res.Payload) == 0 {
		t.Fatalf("expected history entries: %s", res.Message)
	}
}

func TestCountAndAll(t *testing.T) {
	stub := newTestStub(t)
	registerOrgs(t, stub, "Org1MSP")
	invokeAs(stub, "tx1", "Org1MSP", "SubmitReport", "rep-1", "nso", validHash, "0", "0.80", "model-v1", validTs, offChain+"rep-1")
	invokeAs(stub, "tx2", "Org1MSP", "SubmitReport", "rep-2", "zul", validHash, "1", "0.90", "model-v1", validTs, offChain+"rep-2")

	res := invokeAs(stub, "tx3", "Org1MSP", "GetReportCount")
	if res.Status != 200 || string(res.Payload) != "2" {
		t.Fatalf("expected count 2, got %s (%s)", res.Payload, res.Message)
	}

	res = invokeAs(stub, "tx4", "Org1MSP", "QueryAllReports")
	var records []*ReportRecord
	if res.Status != 200 || json.Unmarshal(res.Payload, &records) != nil {
		t.Fatalf("QueryAll failed: %s", res.Message)
	}
	if len(records) != 2 {
		t.Fatalf("expected 2 records, got %d", len(records))
	}
}

func TestComputeContentHash(t *testing.T) {
	stub := newTestStub(t)
	res := invokeAs(stub, "tx1", "Org1MSP", "ComputeContentHash", "hello misinformation")
	if res.Status != 200 || len(res.Payload) != 64 {
		t.Fatalf("unexpected hash result: %s", res.Payload)
	}
}
