package main

// Boundary behaviour of the report state machine: abstention, no votes, late votes,
// votes after closure, post-deadline majority finalisation and membership changes.
// These tests document what the chaincode does in each case.

import (
	"encoding/json"
	"fmt"
	"testing"
	"time"

	"google.golang.org/protobuf/types/known/timestamppb"
)

func bqOrgs(n int) []string {
	orgs := make([]string, n)
	for i := range orgs {
		orgs[i] = fmt.Sprintf("Org%dMSP", i+1)
	}
	return orgs
}

func bqSetup(t *testing.T, n int) *MockStub {
	t.Helper()
	stub := newTestStub(t)
	if n != defaultFoundingOrgLimit {
		if res := invokeAs(stub, "tx-limit", "Org1MSP", "SetFoundingOrgLimit", fmt.Sprint(n)); res.Status != 200 {
			t.Fatalf("SetFoundingOrgLimit failed: %s", res.Message)
		}
	}
	registerOrgs(t, stub, bqOrgs(n)...)
	return stub
}

// bqSubmitOld submits a report whose 72-hour window has already passed.
func bqSubmitOld(t *testing.T, stub *MockStub, id string) {
	t.Helper()
	stub.TxTimestamp = timestamppb.New(time.Date(2020, 1, 1, 0, 0, 0, 0, time.UTC))
	if res := submitReport(stub, "tx-sub-"+id, "Org1MSP", id); res.Status != 200 {
		t.Fatalf("submit failed: %s", res.Message)
	}
}

func bqVote(t *testing.T, stub *MockStub, msp, id, outcome string) {
	t.Helper()
	if res := invokeAs(stub, "tx-v-"+msp+id, msp, "SubmitFactCheck", id, outcome); res.Status != 200 {
		t.Fatalf("%s vote %s failed: %s", msp, outcome, res.Message)
	}
}

func bqRecord(t *testing.T, stub *MockStub, id string) ReportRecord {
	t.Helper()
	res := invokeAs(stub, "tx-q-"+id, "Org1MSP", "QueryReport", id)
	var rec ReportRecord
	if res.Status != 200 || json.Unmarshal(res.Payload, &rec) != nil {
		t.Fatalf("query failed: %s", res.Message)
	}
	return rec
}

// Minimum quorum: votes needed at each consortium size used in the benchmarks.
func TestBoundary_MinimumQuorumExactlyReached(t *testing.T) {
	for n, need := range map[int]int{2: 2, 3: 2, 5: 4, 10: 7, 15: 10, 20: 14} {
		stub := bqSetup(t, n)
		if res := submitReport(stub, "tx-s", "Org1MSP", "r"); res.Status != 200 {
			t.Fatalf("submit: %s", res.Message)
		}
		for i := 1; i < need; i++ {
			bqVote(t, stub, fmt.Sprintf("Org%dMSP", i), "r", "1")
			if s := bqRecord(t, stub, "r").Status; s != statusUnderReview {
				t.Fatalf("N=%d: expected UNDER_REVIEW after %d of %d votes, got %s", n, i, need, s)
			}
		}
		bqVote(t, stub, fmt.Sprintf("Org%dMSP", need), "r", "1")
		rec := bqRecord(t, stub, "r")
		if rec.Status != statusFinal || rec.FinalLabel != "1" {
			t.Fatalf("N=%d: expected FINAL/1 at %d votes, got %s/%s", n, need, rec.Status, rec.FinalLabel)
		}
	}
}

// A one-organisation consortium decides alone: the "never decides alone" guarantee needs N >= 2.
func TestBoundary_SingleOrgDecidesAlone(t *testing.T) {
	stub := bqSetup(t, 1)
	submitReport(stub, "tx-s", "Org1MSP", "r")
	bqVote(t, stub, "Org1MSP", "r", "1")
	if s := bqRecord(t, stub, "r").Status; s != statusFinal {
		t.Fatalf("expected FINAL with N=1, got %s", s)
	}
}

// Abstention: an organisation that never votes leaves the report open, and a 1-1 split stays open.
func TestBoundary_AbstentionLeavesReportOpen(t *testing.T) {
	stub := bqSetup(t, 3)
	bqSubmitOld(t, stub, "r")
	bqVote(t, stub, "Org1MSP", "r", "1")
	bqVote(t, stub, "Org2MSP", "r", "0")
	if s := bqRecord(t, stub, "r").Status; s != statusUnderReview {
		t.Fatalf("expected UNDER_REVIEW with one abstention, got %s", s)
	}
	// After the deadline the tie cannot be finalised, but an official may expire it.
	if res := invokeAs(stub, "tx-f", "Org1MSP", "FinalizeReport", "r"); res.Status == 200 {
		t.Fatalf("a tied report must not be finalised")
	}
	if res := invokeAs(stub, "tx-e", "Org1MSP", "ExpireReport", "r"); res.Status != 200 {
		t.Fatalf("expire failed: %s", res.Message)
	}
	if s := bqRecord(t, stub, "r").Status; s != statusExpired {
		t.Fatalf("expected EXPIRED, got %s", s)
	}
}

// No votes, or one vote: nothing changes on its own at the deadline, finalising is refused, expiry works.
func TestBoundary_TooFewVotesCanOnlyExpire(t *testing.T) {
	for votes := 0; votes <= 1; votes++ {
		stub := bqSetup(t, 3)
		bqSubmitOld(t, stub, "r")
		if votes == 1 {
			bqVote(t, stub, "Org2MSP", "r", "1")
		}
		want := statusPending
		if votes == 1 {
			want = statusUnderReview
		}
		if s := bqRecord(t, stub, "r").Status; s != want {
			t.Fatalf("%d votes: nothing should close automatically at the deadline, got %s", votes, s)
		}
		if res := invokeAs(stub, "tx-f", "Org1MSP", "FinalizeReport", "r"); res.Status == 200 {
			t.Fatalf("%d votes: finalise must require at least two votes", votes)
		}
		if res := invokeAs(stub, "tx-e", "Org1MSP", "ExpireReport", "r"); res.Status != 200 {
			t.Fatalf("%d votes: expire failed: %s", votes, res.Message)
		}
	}
}

// Late votes: the 72-hour window only gates the official's closing actions. While a report is
// still open a vote is accepted after the deadline and can still trigger early acceptance.
func TestBoundary_LateVoteAcceptedWhileOpen(t *testing.T) {
	stub := bqSetup(t, 3)
	bqSubmitOld(t, stub, "r")
	bqVote(t, stub, "Org1MSP", "r", "1")
	bqVote(t, stub, "Org2MSP", "r", "1")
	rec := bqRecord(t, stub, "r")
	if rec.Status != statusFinal || rec.FinalLabel != "1" {
		t.Fatalf("expected late votes to reach FINAL/1, got %s/%s", rec.Status, rec.FinalLabel)
	}
}

// Votes after a terminal state are refused, for all three terminal states.
func TestBoundary_VoteRefusedAfterTerminalState(t *testing.T) {
	stub := bqSetup(t, 3)
	submitReport(stub, "tx-a", "Org1MSP", "final")
	bqVote(t, stub, "Org1MSP", "final", "1")
	bqVote(t, stub, "Org2MSP", "final", "1")
	submitReport(stub, "tx-b", "Org1MSP", "rej")
	bqVote(t, stub, "Org1MSP", "rej", "0")
	bqVote(t, stub, "Org2MSP", "rej", "0")
	bqSubmitOld(t, stub, "exp")
	if res := invokeAs(stub, "tx-e", "Org1MSP", "ExpireReport", "exp"); res.Status != 200 {
		t.Fatalf("expire failed: %s", res.Message)
	}
	for id, want := range map[string]string{"final": statusFinal, "rej": statusRejected, "exp": statusExpired} {
		if s := bqRecord(t, stub, id).Status; s != want {
			t.Fatalf("%s: expected %s, got %s", id, want, s)
		}
		if res := invokeAs(stub, "tx-late-"+id, "Org3MSP", "SubmitFactCheck", id, "1"); res.Status == 200 {
			t.Fatalf("%s: vote after a terminal state must be refused", id)
		}
	}
}

// An organisation cannot vote twice, including the submitter.
func TestBoundary_SecondVoteRefused(t *testing.T) {
	stub := bqSetup(t, 3)
	submitReport(stub, "tx-s", "Org1MSP", "r")
	bqVote(t, stub, "Org1MSP", "r", "1")
	if res := invokeAs(stub, "tx-2", "Org1MSP", "SubmitFactCheck", "r", "1"); res.Status == 200 {
		t.Fatalf("second vote must be refused")
	}
}

// After the deadline the official route can confirm a claim BELOW the two-thirds threshold.
func TestBoundary_ManualFinaliseBelowTwoThirds(t *testing.T) {
	stub := bqSetup(t, 5) // threshold 4
	bqSubmitOld(t, stub, "r")
	bqVote(t, stub, "Org1MSP", "r", "1")
	bqVote(t, stub, "Org2MSP", "r", "1")
	bqVote(t, stub, "Org3MSP", "r", "0")
	if s := bqRecord(t, stub, "r").Status; s != statusUnderReview {
		t.Fatalf("expected UNDER_REVIEW, got %s", s)
	}
	if res := invokeAs(stub, "tx-f", "Org1MSP", "FinalizeReport", "r"); res.Status != 200 {
		t.Fatalf("finalise failed: %s", res.Message)
	}
	rec := bqRecord(t, stub, "r")
	if rec.Status != statusFinal || rec.FinalLabel != "1" {
		t.Fatalf("expected FINAL/1 on 2 of 5 votes, got %s/%s", rec.Status, rec.FinalLabel)
	}
}

// The official route is the only way to reach FINAL with label 0, and early rejection
// means it needs a consortium large enough that "0" votes can lead without closing the report.
func TestBoundary_ManualFinaliseLabelZero(t *testing.T) {
	stub := bqSetup(t, 10) // threshold 7, so up to 3 "0" votes keep the report open
	bqSubmitOld(t, stub, "r")
	bqVote(t, stub, "Org1MSP", "r", "1")
	bqVote(t, stub, "Org2MSP", "r", "0")
	bqVote(t, stub, "Org3MSP", "r", "0")
	if s := bqRecord(t, stub, "r").Status; s != statusUnderReview {
		t.Fatalf("expected UNDER_REVIEW, got %s", s)
	}
	if res := invokeAs(stub, "tx-f", "Org1MSP", "FinalizeReport", "r"); res.Status != 200 {
		t.Fatalf("finalise failed: %s", res.Message)
	}
	rec := bqRecord(t, stub, "r")
	if rec.Status != statusFinal || rec.FinalLabel != "0" {
		t.Fatalf("expected FINAL/0, got %s/%s", rec.Status, rec.FinalLabel)
	}
}

// The threshold uses current membership: registering organisations during a review raises it.
func TestBoundary_MembershipGrowthRaisesThreshold(t *testing.T) {
	stub := newTestStub(t)
	if res := invokeAs(stub, "tx-limit", "Org1MSP", "SetFoundingOrgLimit", "5"); res.Status != 200 {
		t.Fatalf("limit: %s", res.Message)
	}
	registerOrgs(t, stub, "Org1MSP", "Org2MSP", "Org3MSP")
	submitReport(stub, "tx-s", "Org1MSP", "r")
	bqVote(t, stub, "Org1MSP", "r", "1")
	registerOrgs(t, stub, "Org4MSP", "Org5MSP") // N=5, threshold now 4
	bqVote(t, stub, "Org2MSP", "r", "1")
	if s := bqRecord(t, stub, "r").Status; s != statusUnderReview {
		t.Fatalf("expected UNDER_REVIEW (2 of 4 needed), got %s", s)
	}
}

// An official must not be able to reopen founding registration and so bypass the admission vote.
func TestFoundingLimitClosedOnceOrgsRegister(t *testing.T) {
	stub := bqSetup(t, 3)
	res := invokeAs(stub, "tx-reopen", "Org1MSP", "SetFoundingOrgLimit", "9")
	if res.Status == 200 {
		t.Fatalf("raising the founding limit after registration must be refused")
	}
	res = invokeAs(stub, "tx-outsider", "Org4MSP", "RegisterOrg")
	if res.Status == 200 {
		t.Fatalf("an outsider registered without an admission vote")
	}
}
