// Consensus and escalation rules from the misinformation chaincode, lifted out
// and run against every case that matters — including ones a live network
// cannot reach. No Fabric, no Docker, no network.
//
//	go run consensus_cases.go
//
// The logic below is copied verbatim from misinformation.go. If the chaincode
// changes, change it here too — this file proves the rules behave as the paper
// describes, and only holds if the two agree.
package main

import "fmt"

const (
	initialPanel    = 3
	panelStep       = 2
	maxReopenRounds = 3
)

type factCheck struct {
	org     string
	outcome string
	round   int
}

type reopen struct {
	org   string
	round int
}

// two-thirds of the panel, rounded up
func supermajorityOf(n int) int { return (2*n + 2) / 3 }

// tally only the current round; a reopen starts a fresh panel
func settle(checks []factCheck, round, panel int) (bool, string) {
	tally, n := map[string]int{}, 0
	for _, c := range checks {
		if c.round == round {
			tally[c.outcome]++
			n++
		}
	}
	if panel < initialPanel {
		panel = initialPanel
	}
	if n < panel {
		return false, ""
	}
	needed := supermajorityOf(n)
	winner, count := "", 0
	for outcome, c := range tally {
		if c > count {
			winner, count = outcome, c
		}
	}
	if count < needed {
		return false, ""
	}
	return true, winner
}

func canReopen(reopens []reopen, round, panel, orgs int, caller string) (bool, string) {
	for _, r := range reopens {
		if r.org == caller {
			return false, fmt.Sprintf("%s already reopened (round %d)", caller, r.round)
		}
	}
	if round >= maxReopenRounds {
		return false, fmt.Sprintf("round cap %d reached", maxReopenRounds)
	}
	if panel+panelStep > orgs {
		return false, fmt.Sprintf("panel %d exceeds %d registered orgs", panel+panelStep, orgs)
	}
	return true, ""
}

func mk(round int, outcomes ...string) []factCheck {
	var out []factCheck
	for i, o := range outcomes {
		out = append(out, factCheck{fmt.Sprintf("Org%dMSP", i+1), o, round})
	}
	return out
}

var failures int

func expect(label string, got, want bool, detail string) {
	mark := "ok"
	if got != want {
		mark, failures = "FAIL", failures+1
	}
	fmt.Printf("  %-42s %-28s %s\n", label, detail, mark)
}

func main() {
	fmt.Println("\n== supermajority required, and whether a tie is possible ==")
	for _, n := range []int{3, 4, 5, 6, 7, 8, 9} {
		tie := n%2 == 0
		note := "tie impossible"
		if tie {
			note = "TIE POSSIBLE — never used as a panel"
		}
		fmt.Printf("  panel %d  need %d agreeing (%3.0f%%)   %s\n",
			n, supermajorityOf(n), float64(supermajorityOf(n))/float64(n)*100, note)
	}

	fmt.Println("\n== round 0, panel 3 ==")
	for _, c := range []struct {
		name   string
		checks []factCheck
		final  bool
		label  string
	}{
		{"2 checks — below panel", mk(0, "1", "1"), false, ""},
		{"3–0 unanimous", mk(0, "1", "1", "1"), true, "1"},
		{"2–1 majority", mk(0, "1", "1", "0"), true, "1"},
		{"1–2 the other way", mk(0, "1", "0", "0"), true, "0"},
	} {
		got, label := settle(c.checks, 0, initialPanel)
		expect(c.name, got, c.final, fmt.Sprintf("final=%-5v label=%q", got, label))
		if got && label != c.label {
			failures++
		}
	}

	fmt.Println("\n== round 1 after a reopen, panel 5 ==")
	for _, c := range []struct {
		name   string
		checks []factCheck
		final  bool
	}{
		{"4 checks — below panel", mk(1, "0", "0", "0", "0"), false},
		{"4–1", mk(1, "0", "0", "0", "0", "1"), true},
		{"3–2 — short of two-thirds", mk(1, "0", "0", "0", "1", "1"), false},
		{"3–2 then a sixth check → 4–2", mk(1, "0", "0", "0", "1", "1", "0"), true},
		{"3–3", mk(1, "0", "0", "0", "1", "1", "1"), false},
	} {
		got, label := settle(c.checks, 1, 5)
		expect(c.name, got, c.final, fmt.Sprintf("final=%-5v label=%q", got, label))
	}

	fmt.Println("\n== old-round checks must not count toward a new round ==")
	mixed := append(mk(0, "1", "1", "1"), mk(1, "0", "0")...)
	got, _ := settle(mixed, 1, 5)
	expect("3 from round 0 + 2 from round 1", got, false, "final=false (round 0 ignored)")

	fmt.Println("\n== escalation bounds by consortium size ==")
	for _, orgs := range []int{3, 5, 6, 7, 10, 20, 100} {
		round, panel, n := 0, initialPanel, 0
		var reopens []reopen
		trail := ""
		for {
			caller := fmt.Sprintf("Org%dMSP", n+2)
			ok, why := canReopen(reopens, round, panel, orgs, caller)
			if !ok {
				fmt.Printf("  %3d orgs: %-34s terminal after %d reopen(s) — %s\n", orgs, trail, n, why)
				break
			}
			round++
			panel += panelStep
			n++
			reopens = append(reopens, reopen{caller, round})
			trail += fmt.Sprintf("[r%d p%d] ", round, panel)
		}
	}

	fmt.Println("\n== one org cannot spend the whole reopen budget ==")
	var rs []reopen
	round, panel, allowed := 0, initialPanel, 0
	for i := 0; i < 4; i++ {
		ok, _ := canReopen(rs, round, panel, 10, "OrgEvilMSP")
		if !ok {
			break
		}
		round++
		panel += panelStep
		allowed++
		rs = append(rs, reopen{"OrgEvilMSP", round})
	}
	expect("single actor, 10-org consortium", allowed == 1, true,
		fmt.Sprintf("consumed %d of %d reopens", allowed, maxReopenRounds))

	fmt.Println()
	if failures == 0 {
		fmt.Println("ALL CASES PASS")
	} else {
		fmt.Printf("%d FAILURE(S)\n", failures)
	}
}
