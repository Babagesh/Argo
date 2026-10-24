export type ActionKind = "created" | "commented" | "transitioned" | "assigned";

export interface AgentAction {
  id: string;
  kind: ActionKind;
  issueKey: string;
  title: string;
  detail: string;
  status: "done" | "pending" | "failed";
  at: string;
}

export interface Meeting {
  id: string;
  title: string;
  date: string;
  durationMin: number;
  attendees: string[];
  summary: string;
  keyPoints: string[];
  decisions: string[];
  notes: string;
  actions: AgentAction[];
}

const act = (
  id: string,
  kind: ActionKind,
  issueKey: string,
  title: string,
  detail: string,
  status: AgentAction["status"] = "done",
  at = "2m ago",
): AgentAction => ({ id, kind, issueKey, title, detail, status, at });

export const MOCK_MEETINGS: Meeting[] = [
  {
    id: "sprint-42-planning",
    title: "Sprint 42 planning",
    date: "2026-10-23",
    durationMin: 58,
    attendees: ["Maya Chen", "Dev Patel", "Lena Ortiz", "Sam Brooks"],
    summary:
      "The team committed to 34 points for Sprint 42, centred on the new onboarding flow and the payments retry work. Capacity is reduced by one engineer for the last three days.",
    keyPoints: [
      "Onboarding flow redesign is the top-priority epic",
      "Payment retry logic needs an idempotency review before merge",
      "QA capacity is tight; automate smoke tests first",
    ],
    decisions: ["Descope the referral banner to Sprint 43", "Freeze scope on Wednesday"],
    notes:
      "Maya opened with velocity trends. Dev raised concerns about idempotency keys on retries. Lena proposed pairing with QA. Sam to confirm design handoff by Monday.",
    actions: [
      act("a1", "created", "PROJ-412", "Review payment retry idempotency", "Created in Sprint 42, assigned to Dev Patel", "done", "4m ago"),
      act("a2", "transitioned", "PROJ-398", "Referral banner", "Moved to Sprint 43 backlog", "done", "4m ago"),
      act("a3", "commented", "PROJ-377", "Onboarding flow redesign", "Added scope summary and design handoff date", "done", "3m ago"),
      act("a4", "assigned", "PROJ-415", "Automate smoke tests", "Assigning to Lena Ortiz", "pending", "just now"),
    ],
  },
  {
    id: "daily-standup-oct-22",
    title: "Daily standup",
    date: "2026-10-22",
    durationMin: 14,
    attendees: ["Maya Chen", "Dev Patel", "Sam Brooks"],
    summary: "Short sync. One blocker on the staging deploy; everything else is on track.",
    keyPoints: ["Staging deploy blocked by expired certificate", "Design review moved to Thursday"],
    decisions: ["Dev to rotate the staging certificate today"],
    notes: "No other blockers raised.",
    actions: [
      act("b1", "created", "OPS-88", "Rotate staging certificate", "Created with priority High", "done", "1d ago"),
      act("b2", "commented", "PROJ-377", "Onboarding flow redesign", "Design review moved to Thursday", "done", "1d ago"),
    ],
  },
  {
    id: "retro-sprint-41",
    title: "Sprint 41 retrospective",
    date: "2026-10-21",
    durationMin: 45,
    attendees: ["Maya Chen", "Dev Patel", "Lena Ortiz", "Sam Brooks", "Ana Ruiz"],
    summary:
      "The team praised faster code reviews and flagged flaky end-to-end tests as the main drag on delivery.",
    keyPoints: ["Review turnaround dropped to under 4 hours", "Flaky e2e tests cost roughly two days", "Release notes were missed twice"],
    decisions: ["Quarantine flaky tests", "Add release notes to the definition of done"],
    notes: "Ana volunteered to own the flaky-test dashboard.",
    actions: [
      act("c1", "created", "PROJ-401", "Quarantine flaky e2e tests", "Created and assigned to Ana Ruiz", "done", "2d ago"),
      act("c2", "created", "PROJ-402", "Add release notes to definition of done", "Created in backlog", "done", "2d ago"),
      act("c3", "transitioned", "PROJ-399", "Test dashboard spike", "Moved to In Progress", "done", "2d ago"),
    ],
  },
  {
    id: "design-review-checkout",
    title: "Checkout design review",
    date: "2026-10-17",
    durationMin: 35,
    attendees: ["Sam Brooks", "Lena Ortiz", "Ana Ruiz"],
    summary: "The new checkout layout was approved with two accessibility fixes required before build.",
    keyPoints: ["Contrast on the pay button is below AA", "Error states need inline messaging"],
    decisions: ["Approve layout pending accessibility fixes"],
    notes: "Ana to share an updated prototype by Friday.",
    actions: [
      act("d1", "created", "PROJ-390", "Fix pay button contrast", "Created and labelled accessibility", "done", "6d ago"),
      act("d2", "created", "PROJ-391", "Inline checkout error states", "Created and linked to PROJ-377", "failed", "6d ago"),
    ],
  },
  {
    id: "incident-review-oct-14",
    title: "Incident review: search latency",
    date: "2026-10-14",
    durationMin: 50,
    attendees: ["Dev Patel", "Maya Chen", "Ana Ruiz"],
    summary: "A missing index caused a 3x latency spike for 40 minutes. Alerting caught it, but the runbook was out of date.",
    keyPoints: ["Root cause: index dropped during a migration", "Alert fired 9 minutes after impact started"],
    decisions: ["Add migration checks to CI", "Refresh the on-call runbook"],
    notes: "Postmortem doc to be shared by end of week.",
    actions: [
      act("e1", "created", "OPS-91", "Add index check to migration CI", "Created with priority High", "done", "9d ago"),
      act("e2", "created", "OPS-92", "Refresh on-call runbook", "Created and assigned to Maya Chen", "done", "9d ago"),
      act("e3", "commented", "OPS-85", "Search latency incident", "Linked postmortem and timeline", "done", "9d ago"),
    ],
  },
  {
    id: "roadmap-q4-sync",
    title: "Q4 roadmap sync",
    date: "2026-10-09",
    durationMin: 40,
    attendees: ["Maya Chen", "Sam Brooks"],
    summary: "Priorities for Q4 were agreed. Analytics export is pulled forward; SSO is deferred to Q1.",
    keyPoints: ["Analytics export is the top customer request", "SSO needs a security review first"],
    decisions: ["Pull analytics export into Q4", "Defer SSO to Q1"],
    notes: "Share updated roadmap with stakeholders on Monday.",
    actions: [
      act("f1", "transitioned", "PROJ-350", "Analytics export", "Moved to Q4 roadmap", "done", "2w ago"),
      act("f2", "transitioned", "PROJ-360", "SSO support", "Moved to Q1 roadmap", "done", "2w ago"),
    ],
  },
];
