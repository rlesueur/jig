/* Connector take: the schedule capability run, filmed in the real UI (see connector-take.mjs). */
import { connectorTake, TEST_JIG } from './connector-take.mjs';

export default connectorTake({ scenario: 'schedule', where: TEST_JIG, text: {
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
