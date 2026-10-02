/* Connector take: the checkout capability run, filmed in the real UI (see connector-take.mjs). */
import { connectorTake, CHECKOUT_JIG } from './connector-take.mjs';

export default connectorTake({ scenario: 'checkout', where: CHECKOUT_JIG, text: {
  "title": "It stops at *payment*",
  "kicker": "shopping",
  "sub": "A real shop. Jig fills the basket, and stops before anything is bought.",
  "asks": [
    "Buy the backpack on the demo shop."
  ],
  "working": "Jig works through the shop's pages.",
  "denied": "The payment step. Jig stops and shows the total. The answer is *no*, so nothing is bought.",
  "replies": [
    "Nothing was bought. The order was never placed."
  ]
} });
