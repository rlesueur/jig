/* Connector take: the freeslot capability run, filmed in the real UI (see connector-take.mjs). */
import { connectorTake, TEST_JIG } from './connector-take.mjs';

export default connectorTake({ scenario: 'freeslot', where: TEST_JIG, text: {
  "title": "Find a *free hour*",
  "kicker": "calendars",
  "sub": "Two real calendars, one question: when am I free?",
  "asks": [
    "When is there a free hour in both of my calendars?"
  ],
  "working": "Jig reads both calendars, in your timezone.",
  "replies": [
    "A time that is really free in both."
  ]
} });
