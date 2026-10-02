/* Instructional cut of the c-schedule capture: the same real run at a calm pace, step by step. */
import { connectorTake, TEST_JIG } from './connector-take.mjs';

export default connectorTake({ scenario: 'schedule', where: TEST_JIG, instructional: true, captureOf: 'c-schedule', text: {
  "title": "A *morning check*, every day",
  "kicker": "schedules",
  "sub": "Ask once. Jig checks your email every morning and sums it up.",
  "asks": [
    "Every morning at 8, check my email and sum up anything new."
  ],
  "working": "Jig sets up the schedule.",
  "replies": [
    "Scheduled for 8 every morning, reading only."
  ]
} });
