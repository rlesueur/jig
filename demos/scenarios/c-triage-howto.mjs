/* Instructional cut of the c-triage capture: the same real run at a calm pace, step by step. */
import { connectorTake, TEST_JIG } from './connector-take.mjs';

export default connectorTake({ scenario: 'triage', where: TEST_JIG, instructional: true, captureOf: 'c-triage', text: {
  "title": "Sort my *inbox*",
  "kicker": "email",
  "sub": "Real unread emails. It drafts the one reply that matters, and asks before sending.",
  "asks": [
    "Which of these emails need a reply? Draft the one that does.",
    "That's right. Send it."
  ],
  "working": "Jig reads the unread emails in the real inbox.",
  "replies": [
    "It says which one needs a reply, and shows the draft.",
    "Sent: one reply, in the right thread. Nothing else touched."
  ]
} });
