# Solana Token Scoring and Classification Guide

Last updated: 2026-07-04

This document describes the current behavior of the Solana wallet investigator and utility-project watcher. It is a practical reference for:

- what the tracker looks for
- what counts as a useful signal
- what gets penalized
- what gets excluded entirely
- how the final labels and alert tiers are assigned

This guide reflects the current implementation in the project folder. It is descriptive, not a promise that every future token will be handled the same way if the rules change.

## 1. Core Purpose

The system is designed to separate:

- real utility or infrastructure projects
- projects with evidence-backed product claims
- wallet behavior that suggests insider control, distribution, or sale chains
- low-quality meme, social, or launch-only tokens

It does not try to make an investment recommendation. It tries to answer:

- Is there a real product or technical project here?
- Is the evidence independently verifiable?
- Does the wallet behavior look connected, coordinated, or suspicious?
- Are there enough supporting signals to justify a higher-confidence report?

## 2. Overall Pipeline

The watcher and analyzer typically work in this order:

1. Detect a new token or analyze a submitted mint and wallet.
2. Read token metadata.
3. Extract candidate website, GitHub, docs, and social links.
4. Crawl the project pages.
5. Score utility and infrastructure relevance.
6. Filter out meme-style or social-only projects.
7. If the project survives, run wallet tracing and cluster analysis.
8. Produce markdown, JSON, and CSV outputs.
9. Optionally send alerts to Discord.

## 3. What Counts as a Website

Only a real project website should count as a website signal.

### Accepted as a real website

Examples:

- official project homepage
- documentation site
- product landing page
- app or dashboard
- GitHub Pages or docs site that clearly belongs to the project
- domain that contains substantive product information

### Not counted as a website

These are excluded from website credit:

- `x.com`
- `twitter.com`
- `t.co`
- `t.me`
- `telegram.me`
- `discord.com`
- `discord.gg`
- `linktr.ee`
- `beacons.ai`
- `bio.link`
- social communities or community hub links
- `/communities/` pages
- image/CDN/media hosts
- IPFS or Arweave-style content links
- obvious redirect or link-aggregator pages

If the only "website" is a social/community link, it should not be treated as a real website.

## 4. Twitter/X and Social Scraping

The tracker uses Nitter RSS mirrors for X/Twitter when possible.

Important points:

- it reads tweet content from RSS items
- it merges results across mirrors to reduce stale-cache issues
- it uses social text as a source of evidence, not as a website signal
- it can parse mentions, replies, retweets, and cashtags

Social content can help with project context, but it should not automatically make a token look like a utility project.

## 5. Hard-Fail Exclusions

Some signals are strong enough to exclude a token from utility analysis.

### Meme hashtag hard-fail

If metadata, crawled pages, or social content contains a hashtag matching:

- `#meme`
- `#memes`
- or any hashtag containing the substring `meme` or `memes`

the project should hard-fail as a meme candidate.

This is intentionally strict.

### TikTok exclusion

TikTok presence is treated as a meme/social signal, not as a utility indicator.

### Community-only presence

If the project only has:

- Twitter/X
- Telegram
- Discord
- community hub pages

and no real product or docs evidence, it should not be treated as a utility candidate.

### Meme-style web content

If the website or docs are dominated by:

- animal imagery
- meme jokes
- pop-culture references
- joke-token branding
- obvious meme framing

that should count against utility classification.

## 6. Words and Phrases That Are Penalized

The scorer does not rely on a single banned-word list. It uses a mix of hard exclusions and negative signals.

### Strong meme signals

These are treated as negative or disqualifying when they appear in meaningful context:

- `meme`
- `memes`
- `shitcoin`
- `doge`
- `pepe`
- `wojak`
- `bonk`
- `floki`
- `inu`
- `cat`
- `dog`
- `hamster`
- `frog`
- `banana`
- `pump` is not a meme word by itself; only treat it as a meme signal when it appears inside explicit meme-token framing
- `community` when it is the only meaningful presence
- `tiktok`

### Social-only signals that do not count as product evidence

- `Twitter`
- `X`
- `Telegram`
- `Discord`
- `community`
- `group`
- `chat`
- `link in bio`

These may still be useful for discovery, but they should not be treated as evidence of a utility product by themselves.

## 7. Words and Phrases That Are Boosted

The scorer looks favorably on language that suggests a real product, developer tooling, or technical infrastructure.

### Utility / product signals

Examples include:

- `AI`
- `agents`
- `agent`
- `MCP`
- `API`
- `SDK`
- `docs`
- `documentation`
- `builder`
- `builders`
- `build`
- `building`
- `workflow`
- `workflows`
- `webhook`
- `automation`
- `compute`
- `developer`
- `devtools`
- `tooling`
- `product`
- `platform`
- `dashboard`
- `integrations`
- `open source`
- `GitHub`
- `repository`
- `tutorial`
- `getting started`
- `how it works`
- `smart contract`
- `protocol`
- `program`
- `on-chain`

### Infrastructure / technical signals

Examples include:

- `API`
- `SDK`
- `webhook`
- `MCP`
- `integration`
- `workflow`
- `automation`
- `backend`
- `service`
- `platform`
- `deployment`
- `infra` when the surrounding context is clearly technical

Important: generic words like `deploy`, `deployed`, and `deployment` are not enough on their own. They are too broad to be strong infra evidence by themselves.

## 8. What Counts as Useful Evidence

The tracker gives more credit when claims can be independently supported.

### Strong evidence

- website contains the contract address or mint
- docs contain the contract address or mint
- GitHub repository contains the contract address or mint
- repository predates launch
- repository has real commit history
- repository has non-trivial commit distribution
- live website actually references the repo or the shipped product
- token metadata links to a real product site, docs, or repo
- on-chain program address can be verified from a claimed deployment

### Weaker evidence

- project says it is an AI project
- project says it is infra
- project says it is a utility token
- project says it is deployed
- project says it has a backend

Claims without independent confirmation should not score as strongly.

## 9. Evidence Quality Score

The evidence quality score is meant to measure how independently supportable the project claim is.

The score improves when the project has:

- a real website
- real docs
- a valid GitHub repo
- contract or mint visible on the official site or docs
- repository age that makes sense relative to launch
- a usable backend or app
- verified on-chain program evidence

The score decreases when:

- the site is only social links
- the site is mostly meme content
- the repo is missing, empty, or obviously unrelated
- the claimed product is not independently verifiable
- the only signal is marketing language

## 10. Project Relevance Score

This score estimates whether the project is actually a utility, infra, AI, or developer-tools candidate.

Positive signals:

- AI / agents / MCP / API / SDK / docs / builder language
- project website with useful product content
- repository or docs with real technical material
- usable app, dashboard, endpoint, or workflow
- clear developer-focused language

Negative signals:

- meme or joke framing
- social-only link presence
- animal or pop-culture branding
- meme hashtags
- TikTok presence
- empty site with no product substance

If the content is strongly mixed, the token should usually remain `unclear` or `possible_utility` rather than being forced into a high-confidence category.

## 11. Wallet Risk Signals

Wallet analysis focuses on deterministic flow tracing.

### Key wallet-risk signals

- developer buys the token
- developer transfers tokens to a side wallet
- side wallet sells tokens
- sale proceeds return to the developer cluster
- repeated funding overlap across wallets
- repeated interaction with the same token family
- liquidity or exchange deposit patterns that hide the sale path
- cluster-level supply concentration

### What is not enough by itself

- a wallet-to-wallet transfer alone
- a transfer that cannot be tied to a sale
- a wallet holding tokens without disposal
- a suspicious-looking pattern without transaction-level evidence

The system should not call a transfer a sale unless the swap, DEX interaction, or exchange deposit evidence is present.

## 12. Cluster-Level Analysis

The ideal wallet view is a small entity graph, not just one wallet.

The system tries to connect:

- deployer wallet
- creator wallet
- initial funder
- side wallet
- sale wallet
- proceeds wallet
- repeated counterparties

Each connection should ideally include:

- transaction signature
- timestamp
- asset
- amount
- direction
- reason for linking
- confidence level

The goal is to detect patterns like:

- buy -> transfer -> sell
- sale proceeds -> return to cluster
- same funding wallet reused across launches
- same side wallet used multiple times

## 13. Classification Labels

The tracker uses several labels depending on the part of the pipeline.

### Project labels

- `utility_candidate`
- `infra_candidate`
- `possible_utility`
- `meme_candidate`
- `unclear`

### Attribution labels

- `Verified`
- `Likely`
- `Possible`
- `Unknown`
- `Contradicted`

### Wallet analysis status

- `found`
- `not_found`
- `unknown`

Do not confuse project relevance with wallet attribution confidence.

## 14. Confidence vs Risk

These are separate dimensions.

Examples:

- `Wallet Risk: High, Confidence: Low`
- `Project Relevance: Strong, Evidence Quality: Low`
- `Utility Candidate, but not enough attribution evidence`

This separation matters because a suspicious-looking pattern with weak attribution should not be overstated.

## 15. Alert Tiers

The system uses alert tiers to avoid treating every result the same.

### Watch

Used when:

- the project is interesting but evidence is limited
- the project may be utility/infra, but confidence is not high enough
- wallet findings are incomplete or narrow

### Review

Used when:

- utility or infra evidence is meaningful
- confidence is reasonably high
- market risk is not dominant

### Urgent Risk

Used when:

- the wallet chain looks suspicious
- side-wallet sale or proceeds consolidation is strong
- market or concentration risk is high enough to matter

## 16. Market-Risk Inputs

The system can also track market-side caution signals such as:

- liquidity quality
- holder concentration
- top-holder concentration
- supply control
- sniper concentration
- bonded/migrated status
- developer allocation
- volume quality

Market risk does not replace wallet risk. It is a separate layer.

## 17. GitHub and Repo Checks

If a GitHub repo is mentioned or discovered, the tracker tries to verify:

- repo creation date
- first commit date
- commit count
- contributor count
- whether the repo predates launch
- whether the repo contains meaningful project code
- whether the repo looks copied, empty, or generated

A GitHub link alone is not enough.

## 18. Program and Deployment Checks

When a project claims an on-chain program, the tracker should only verify deployment if the claim is explicit enough to justify it.

Useful evidence includes:

- a parseable Solana program address
- on-chain executable account info
- deploy timing relative to launch
- upgrade authority evidence where available

Generic words like `deploy` and `deployed` are not treated as strong infra proof on their own.

## 19. Things That Should Not Be Overweighted

Do not give too much credit to:

- a Twitter account existing
- a Telegram group existing
- a Discord server existing
- a generic roadmap
- the word `deploy`
- the word `deployed`
- the word `deployment`
- a random social mention of the token
- a passing reference to AI with no product evidence

Do not treat:

- community links as websites
- social hype as technical evidence
- generic launch language as infra proof

## 20. Things That Should Be Weighted More Heavily

Give more weight to:

- the mint or contract address appearing on the official site
- the mint or contract address appearing in docs
- real GitHub code with meaningful history
- a working product, API, or app
- a real technical doc site
- a verified program deployment claim
- wallet flows that show clear token movement and sale proceeds
- repeated cross-launch wallet relationships

## 21. Common Failure Modes

These are the patterns the system should be careful about:

### False utility

A token may mention:

- AI
- compute
- infrastructure
- agents
- MCP

but still be a meme or launch-only project.

### False website

A Twitter, Telegram, Discord, or community link is not a real website.

### False infra

The word `deploy` or `deployed` does not prove infrastructure.

### False sale

A transfer is not a sale unless a swap or exchange deposit is shown.

### False confidence

Risky behavior with no attribution evidence should stay low-confidence.

## 22. Current Preferred Decision Style

The system should be conservative:

- prefer `unclear` over forced certainty
- prefer `possible_utility` over overstated confidence
- prefer `Unknown` when wallet attribution is weak
- prefer hard-fail for explicit meme content
- prefer evidence-backed labels over hype-based labels

## 23. Current Practical Thresholds

These are implementation-oriented heuristics, not investment advice.

### Utility / infra candidates

Examples of useful outputs:

- `infra_candidate` when infrastructure signals are strong and meme signals are weak
- `utility_candidate` when utility signals are strong, useful links exist, and meme signals are low
- `possible_utility` when utility evidence is present but not strong enough for the top label

### Meme candidates

Examples of useful outputs:

- `meme_candidate` when meme signals are strong enough to dominate the project
- hard-fail if meme hashtags are detected in the current classification flow

## 24. V2 Classification

The project now has a parallel `v2` path that stays separate from the current utility scoring rules.

### V2 trigger concept

A token can qualify for `v2` alerts when all of the following are true:

1. There is evidence of a GitHub repo, docs page, or tweeted mention of GitHub/docs.
2. The contract address is found on the website, in GitHub, or in docs.

### V2 evidence sources

The `v2` path treats these as positive sources:

- GitHub repo discovery
- docs pages on the project website
- tweet or social-post text that references GitHub or docs
- contract evidence found on the website, GitHub, or docs

### V2 alerts

`v2` alerts are emitted separately from the existing utility/infra alerts and are clearly labeled `v2` in:

- Discord messages
- the report automation block
- the dashboard

### V2 does not replace the current rules

The existing scoring system remains in place.
`v2` is a parallel path for broader coverage, not a replacement for the current classification logic.

## 25. Summary

The tracker is designed to reward:

- real product evidence
- independent verification
- useful technical language
- real wallet flow tracing

It is designed to penalize or exclude:

- meme hashtags
- social-only pages
- community-link masquerading as a website
- generic launch language
- unverified transfer-to-sale assumptions

The most important principle is simple:

**If the evidence is weak, the label should stay conservative.**
