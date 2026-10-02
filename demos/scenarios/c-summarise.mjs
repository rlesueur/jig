/* Connector take: the summarise capability run, filmed in the real UI (see connector-take.mjs). */
import { connectorTake, TEST_JIG } from './connector-take.mjs';

export default connectorTake({ scenario: 'summarise', where: TEST_JIG, text: {
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
