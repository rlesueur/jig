/* Connector take: the github capability run, filmed in the real UI (see connector-take.mjs). */
import { connectorTake, TEST_JIG } from './connector-take.mjs';

export default connectorTake({ scenario: 'github', where: TEST_JIG, text: {
  "title": "File a *GitHub issue*",
  "kicker": "GitHub",
  "sub": "Describe the bug in plain words. Jig writes it up and asks before posting.",
  "asks": [
    "Open an issue about the dark mode bug."
  ],
  "working": "Jig checks the repository.",
  "replies": [
    "The issue is open, in the right repository."
  ]
} });
