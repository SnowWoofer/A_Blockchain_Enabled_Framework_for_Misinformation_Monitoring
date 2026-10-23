export type Role = 'official' | 'fact_checker' | 'observer' | 'ai_submitter';
export type Label = '0' | '1';
export type ReportStatus = 'PENDING' | 'UNDER_REVIEW' | 'FINAL' | 'REJECTED' | 'EXPIRED';
export type Verdict = 'misinformation' | 'not_misinformation' | 'not_confirmed' | 'expired' | 'under_review';

export interface UserView {
  username: string;
  org: string;
  mspid: string;
  role: Role;
  display_name: string;
  active: boolean;
  must_change_password: boolean;
  mfa_enabled: boolean;
  locked: boolean;
  created_at: string;
  created_by: string;
  last_login_at: string;
}

export interface Me {
  subject: string;
  org: string;
  mspid: string;
  role: Role;
  kind: 'user' | 'api_key';
  must_change_password: boolean;
  user?: UserView;
}

export interface Session {
  access_token: string;
  expires_in: number;
  user: UserView;
}

// --- public portal ---------------------------------------------------------

export interface PublicInfo {
  ledger_backend: string;
  auth_mode: string;
  registered_orgs: number;
  required_yes: number;
  fact_check_window_hours: number;
}

export interface PublicStats {
  total_reports: number;
  by_verdict: Partial<Record<Verdict, number>>;
  registered_orgs: number;
}

export interface VoteTally {
  cast: number;
  yes: number;
  no: number;
  required_yes: number;
  registered_orgs: number;
}

export interface PublicSummary {
  id: string;
  status: ReportStatus;
  verdict: Verdict;
  claim: string;
  platform: string;
  submitted_at: string;
  closed_at: string;
  ai_label: Label;
  ai_confidence: number;
  votes: VoteTally;
}

export interface Verification {
  report_id: string;
  content_hash: string;
  on_chain_hash: string;
  off_chain_intact: boolean;
  matches_on_chain: boolean;
  verified: boolean;
  explanation: string;
}

export interface PublicDetail extends PublicSummary {
  published_at: string;
  model_version: string;
  deadline: string;
  fact_checks: { org: string; outcome: Label; tx_id: string; reasoning: string; evidence: string[]; timestamp: string }[];
  integrity: Verification | null;
  history: { tx_id: string; timestamp: string; status: ReportStatus }[];
}

export interface Page<T> {
  total: number;
  page?: number;
  page_size?: number;
  items: T[];
}

export interface TipStatus {
  reference: string;
  state: 'screening' | 'under_review' | 'closed';
  report_id?: string;
  verdict?: Verdict;
}

// --- fact-checker workspace ------------------------------------------------

export interface Consensus {
  registered_orgs: number;
  required_yes: number;
  eligible_voters: number;
  votes_cast: number;
  remaining_voters: number;
  yes?: number;
  no?: number;
}

export interface Claim {
  id: string;
  content: string;
  source_platform: string | null;
  published_at: string | null;
  msg_id: string | null;
  inference_label: Label;
  confidence: number;
  model_version: string;
  submitted_by: string;
  submitted_at: string;
  status: ReportStatus;
  fact_check_deadline: string;
  consensus: Consensus;
  my_org_is_submitter: boolean;
  my_org_vote: Label | null;
  can_vote: boolean;
  blind: boolean;
  final_label?: Label | null;
  finalized_at?: string | null;
}

export interface ClaimDetail extends Claim {
  fact_checks: { checker_msp: string; outcome: Label; tx_id: string; reasoning: string; support: string[]; timestamp: string }[];
}

export interface ChainRecord {
  id: string;
  content_hash: string;
  inference_label: Label;
  confidence: number;
  model_version: string;
  timestamp: string;
  submitted_by: string;
  fact_check_deadline: string;
  status: ReportStatus;
  fact_checks: { checker_msp: string; outcome: Label; txid: string }[];
  final_label?: Label;
  finalized_by?: string;
  finalized_at?: string;
}

export interface HistoryEntry {
  tx_id: string;
  timestamp: string;
  is_delete: boolean;
  record?: ChainRecord;
}

export interface RegisteredOrg {
  mspid: string;
  registered_at: string;
}

export interface Admission {
  candidate_msp: string;
  org_name: string;
  org_type: string;
  requested_at: string;
  votes: { voter_msp: string; verdict: Label; txid: string }[];
  status: 'PENDING' | 'ADMITTED' | 'REJECTED';
  finalized_by?: string;
  finalized_at?: string;
}

export interface AuditEntry {
  id: number;
  ts: string;
  actor: string;
  org: string;
  role: string;
  action: string;
  target: string;
  outcome: string;
  ip: string;
  details: Record<string, unknown>;
  hash: string;
}

export interface AuditVerification {
  intact: boolean;
  entries: number;
  first_bad_id: number | null;
  head?: string;
}
