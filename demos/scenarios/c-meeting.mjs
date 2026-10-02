/* Connector take: the meeting capability run, filmed in the real UI (see connector-take.mjs). */
import { connectorTake, TEST_JIG } from './connector-take.mjs';

export default connectorTake({ scenario: 'meeting', where: TEST_JIG, text: {
  "title": "From email to *both calendars*",
  "kicker": "calendars",
  "sub": "An email asks for a meeting. Jig puts it in Google and Outlook.",
  "asks": [
    "Tom's email asks for a rehearsal. Put it in both of my calendars."
  ],
  "working": "Jig reads the email and checks both calendars.",
  "replies": [
    "Booked in both calendars, at the time the email asked for."
  ]
} });
