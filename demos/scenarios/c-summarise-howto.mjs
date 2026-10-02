/* Instructional cut of the c-summarise capture: the same real run at a calm pace, step by step. */
import { connectorTake, TEST_JIG } from './connector-take.mjs';

export default connectorTake({ scenario: 'summarise', where: TEST_JIG, instructional: true, captureOf: 'c-summarise', text: {
  "title": "Catch up on *email*",
  "kicker": "email",
  "sub": "A real inbox, summed up in a few lines. Reading never needs a yes.",
  "asks": [
    "Summarise my unread emails in a few bullet points."
  ],
  "working": "Jig reads each unread email.",
  "replies": [
    "The gist of each email, in a few lines."
  ]
} });
