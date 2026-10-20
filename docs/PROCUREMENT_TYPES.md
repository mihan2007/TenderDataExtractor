---
name: procurement_types
description: Reference of procurement procedure types for normalizing the "Procedure type" field (Block 1). Read ONLY when the notice/documentation does not name the procedure explicitly - otherwise copy the buyer's exact wording.
type: reference
---

# Procurement procedure types

## When to open this file

Only when the documentation does **not** name the procedure ("open tender", "auction", "request for quotations", ... appear nowhere). If a name is given, copy it **verbatim** from the document and don't open this file — including unusual, buyer-specific names (see "Non-standard names" below).

If there is no name, classify the procedure by the features below and write **one of the fixed values** (list at the end). Never write `Not specified in the documents` here: there is always some procedure, even if the documents only say "competitive procurement" or "tender".

The names differ between systems (EU directives, UNCITRAL Model Law, World Bank / multilateral development banks, national laws, corporate procurement policies), but the underlying mechanics are similar. Classify by mechanics, not by vocabulary.

## Quick classification by features

| Feature observed in the documentation | → Procedure type |
|---|---|
| Any interested supplier may submit a bid; evaluation by price **and/or** quality criteria with published weights; bids cannot be changed after the deadline | Open tender |
| Two stages: suppliers first apply / pre-qualify, only **short-listed / invited** suppliers submit bids | Restricted procedure |
| Simple, usually standard goods; **price is the only criterion** (lowest compliant price wins); short submission period; no formal opening/negotiation stage | Request for quotations (RFQ) |
| Price **and** non-price criteria; suppliers propose their own solution, and the buyer may ask them to **clarify or improve** proposals (no direct bargaining) | Request for proposals (RFP) |
| The buyer **negotiates or holds a dialogue** with one or several bidders on price, scope or technical solution before the final offers / contract | Competitive negotiation / dialogue |
| Price is the only criterion and bidders **lower their prices in rounds / in real time** on an e-procurement platform (auction step, auction start time) | Electronic reverse auction |
| A **framework agreement** is concluded with one or several suppliers, and individual orders are placed later (call-offs, mini-competitions) | Framework agreement / call-off |
| The contract goes to **one named supplier** without comparing bids (sole manufacturer, urgency, low value, exclusive rights, follow-on order) | Direct award (single source) |

When several features are present, check in this order: real-time / round-based price reduction on a platform (→ reverse auction); a pre-qualification stage (→ restricted); negotiations (→ competitive negotiation); non-price criteria (→ open tender / RFP); and only last — a short, price-only procedure (→ RFQ).

## Definitions

**Open tender** (open procedure, invitation to bid / ITB, open competitive bidding). Publicly advertised; any eligible supplier may bid. The award goes to the most economically advantageous tender (price, or price plus quality criteria with fixed weights) or to the lowest evaluated compliant bid. Bids are sealed until the opening and cannot be amended afterwards (clarifications only).

**Restricted procedure** (selective tendering, pre-qualification + ITB). Any supplier may request to participate; the buyer selects or short-lists candidates against published qualification criteria and invites only them to bid. Typical for complex works and high-value supply.

**Request for quotations (RFQ)** (shopping, price comparison, mini-tender). Used for low-value or standard, off-the-shelf goods. The buyer asks for prices from several suppliers (published or invited); the lowest compliant quotation wins. Short deadlines, minimal formalities.

**Request for proposals (RFP)**. The buyer describes needs or outcomes; bidders propose solutions. Evaluation by a combination of technical/quality criteria and price; the buyer may request clarifications or improved proposals before the final evaluation, but does not bargain directly.

**Competitive negotiation / dialogue** (competitive procedure with negotiation, competitive dialogue, negotiated procedure with prior publication). The buyer negotiates the initial offers — price, terms, technical solution — with one or several bidders, then asks for final offers. Used when the requirement cannot be specified precisely in advance or needs adaptation.

**Electronic reverse auction** (e-auction). After an initial evaluation, admitted bidders submit successively lower prices in real time (or in rounds) on an e-procurement platform; the auction has a start time and a minimum decrement. Often combined with an open or restricted procedure as its final stage.

**Framework agreement / call-off** (standing offer, dynamic purchasing system, indefinite-delivery contract). An agreement on terms for a period; individual purchases are then awarded directly under it or through a mini-competition among the framework suppliers.

**Direct award (single source)** (negotiated procedure without publication, sole-source procurement). The contract is placed with one supplier without competition, with a justification (only one possible supplier, extreme urgency, exclusive rights, compatibility with existing equipment, very low value).

## Price-reduction stages (field "Price-reduction stage")

| Stage | Meaning |
|---|---|
| `Reverse auction` | real-time / round-based price decreases on a platform |
| `Re-bidding / BAFO` | after evaluation, bidders may submit a new, lower price once or several times ("best and final offer", "re-bidding", "price improvement round") |
| `Negotiation` | price is negotiated with one or several bidders |
| `Other price reduction` | any other mechanism the documentation describes (describe it in the field) |
| `Not provided` | the documentation provides no way to lower the price after submission |

## Non-standard names

Buyers — especially corporate buyers and utilities — often use their own names: "competitive selection", "quotation session", "price inquiry", "tender for SMEs only", "market consultation with award". If such a name is written in the documentation, **copy it verbatim** — it is what the buyer and the platform use, and what the user will search for. Use the fixed list only when no name is written anywhere.

A procedure open **only to SMEs** (small and medium-sized enterprises), with no other features described, is usually a simplified procedure — classify it by its evaluation criteria (price only → RFQ; price + quality → RFP) and keep the SME restriction in "Reserved for SMEs" (Block 6).

## Fixed values

- `Open tender`
- `Restricted procedure`
- `Request for quotations (RFQ)`
- `Request for proposals (RFP)`
- `Competitive negotiation / dialogue`
- `Electronic reverse auction`
- `Framework agreement / call-off`
- `Direct award (single source)`
