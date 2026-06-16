---
name: Fintech
description: Interview coaching and CV tailoring for fintech, payments, and financial services roles
scope: coaching, cv
---

# Fintech Plugin

## When to Activate

Activate when the target role or job ad involves: fintech, payments, banking, financial services, PSD2, PCI-DSS, regulatory compliance, transaction processing, anti-money laundering (AML), Know Your Customer (KYC), or payment service providers.

## Interview Questions

Additional recruiter screening questions for fintech roles:

- "What experience do you have with PCI-DSS compliance in your infrastructure work?"
- "Have you worked in a regulated environment before? How did that affect your development process?"
- "The client processes financial transactions. What's your experience with high-availability systems in that context?"
- "Are you familiar with PSD2 requirements? How has that shaped any of your previous projects?"
- "This role involves handling sensitive financial data. Can you walk me through how you've approached data security in past projects?"
- "The team follows strict change management processes due to regulatory requirements. How do you feel about that?"
- "Have you worked with any payment gateways or transaction processing systems?"

Additional hiring manager questions:

- "Walk me through how you'd design a deployment pipeline for a system that processes live financial transactions. What constraints would you prioritize?"
- "Tell me about a time you had to balance speed of delivery with compliance requirements. What trade-offs did you make?"
- "How would you approach a situation where a security audit finds issues in code you're responsible for?"

## Anti-Patterns

Domain-specific anti-patterns to track during fintech coaching:

| # | Pattern | Description |
|---|---------|-------------|
| F1 | Compliance hand-wave | Dismissing regulatory requirements as "just process" or "overhead" rather than treating them as core engineering constraints |
| F2 | Security afterthought | Framing security as something added later ("we also did security") rather than built-in ("every deployment went through...") |
| F3 | Scale without stakes | Quoting transaction volumes or uptime numbers without acknowledging what failure means in a financial context (lost money, regulatory action, customer trust) |
| F4 | Regulation name-dropping | Mentioning PSD2, PCI-DSS, or GDPR without being able to explain how they affected actual technical decisions |

## Answering Strategies

See `strategies/regulatory-framing.md` for the full strategy on framing technical work in regulated environments.

## Session Behavior

- **Interviewer:** Fintech recruiters tend to be more process-oriented and risk-averse than startup recruiters. When probing compliance or security topics, adopt a slightly skeptical tone -- "The client takes this very seriously, so I need to understand exactly what you've done." Don't accept vague answers about regulated environments.
- **Coach:** When the candidate triggers F1 (compliance hand-wave) or F2 (security afterthought), flag it immediately and firmly. These are disqualifying in fintech interviews -- a candidate who treats compliance as overhead will not get forwarded.

## CV Rules

When generating CVs for fintech roles:

- **Compliance first:** Lead project descriptions with regulatory or compliance context when applicable (e.g. "PCI-DSS-compliant payment infrastructure" rather than just "payment infrastructure")
- **Quantify reliability:** Include uptime, availability, or SLA metrics where available. Fintech hiring managers weight these heavily.
- **Security vocabulary:** Use precise security terminology (encryption at rest, mTLS, audit logging, key rotation) rather than generic "security best practices"
- **Change management:** Mention change management, approval workflows, or deployment gates if the candidate has experience with them. These signal understanding of regulated environments.
- **Avoid "move fast" framing:** Phrases like "rapid iteration" or "ship fast" can be red flags in fintech. Reframe as "delivered within tight timelines while maintaining compliance requirements."
