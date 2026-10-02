/* Instructional cut of the c-report capture: the same real run at a calm pace, step by step. */
import { connectorTake, TEST_JIG } from './connector-take.mjs';

export default connectorTake({ scenario: 'report', where: TEST_JIG, instructional: true, captureOf: 'c-report', text: {
  "title": "Research to *Drive*",
  "kicker": "research",
  "sub": "It reads GOV.UK, writes a short note and saves it to your cloud storage.",
  "asks": [
    "Look up the VAT rate and threshold on GOV.UK and save a short note."
  ],
  "working": "Jig reads the official pages.",
  "replies": [
    "The figures, with their source, saved where you asked."
  ]
} });
