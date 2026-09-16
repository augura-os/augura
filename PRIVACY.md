# Augura Privacy Policy

**English** | [简体中文](PRIVACY.zh-CN.md)

**Last updated: 2026-07-23**
**Effective: 2026-07-23**

Augura ("the tool", "we") is developed and operated by the author(s) of the Augura project. We take data security and user trust seriously. This policy explains how we collect, use, store and protect data in the course of providing the tool. Please read it carefully before use. **By using or continuing to use the tool, you acknowledge that you have read, understood and agreed to this policy in full.**

## 1. Data We Collect and Why

### 1.1a Operational data (required, always on)

To keep the software compatible and stable, the tool collects: error reports and crash digests, the software version, and the OS family (timestamps are coarsened into 6-hour buckets). **None of this includes creatives, delivery data, or anything that identifies you.** This is required for operation and has no toggle.

### 1.1b The Creative Genome Program (on by default, opt-out any time)

To jointly improve creative-judgement accuracy and category benchmarks, participants in the program share:

- Feature clicks (feature names only, never the content you act on)
- An anonymized instance identifier (randomly generated; contains no name, account, device ID, or any personally identifying information; rotates automatically every 90 days)
- Your corrections (when you overrule an AI judgement: the field and the direction of change; **creative-name-style identifiers are always irreversibly hashed with a per-instance salt — in any language, any naming string of 3+ hyphen-separated segments is replaced**; corrections carry three context fields — genre / creative family / market — used for family-level misjudgement analysis, with no identity or creative content)
- Anonymized, aggregated statistics (metric-range distributions across the **market × genre × creative family** dimensions, and creative-lifecycle distributions — buckets and counts only; a creative's market is cross-determined from filename prefixes and AI analysis tags, and creatives with uncertain determinations are excluded from benchmarks)

The above is used for: product iteration, industry research, and model training. **All data used for research and training is anonymized and aggregated, and cannot be traced back to any specific person or company.**

You can turn off the Creative Genome Program at any time on the tool's Settings page. After you opt out we stop collecting the above, but anonymized aggregate data already produced before opt-out remains usable.

### 1.2 Content you submit voluntarily

When you actively submit content through the tool's feedback, correction, or annotation features, **you grant us the right to use that content for product improvement, feature optimization, and model training**. Please do not include anything you do not wish to share with us in such submissions.

### 1.3 Cloud service data (only for cloud users)

If you register for and use our cloud services (including but not limited to cloud sync, team spaces, online analysis), the data you upload is stored and processed under the cloud service agreement. We may use anonymized, aggregated data from cloud services for product improvement, industry benchmark research, and model training.

### 1.4 Data we do NOT collect

We promise the tool does **not** collect:

- Your creative files themselves (videos, images, audio, or other creative assets)
- Raw delivery details from your ad accounts (spend, bids, ROAS, or other line-level data that identifies specific delivery behavior)
- Your name, phone number, government ID, or other personally identifying information (except the minimal information required to register for cloud services)
- Your directory structure or local file names

### 1.5 Where data goes during AI analysis (important)

Creative analysis calls **the model provider you configure yourself in Settings** (any OpenAI-compatible endpoint, e.g. OpenAI / Kimi). During analysis, sampled frames and structured analysis results are sent to the provider you configured — **this is a direct call between you and that model provider, not a "collection" by this tool**, and the handling of that data is governed by that provider's privacy policy. You can keep data entirely on your own machine by using a self-hosted / local model service (e.g. an Ollama-compatible endpoint).

## 2. Storage and Protection

2.1 Usage data is transmitted encrypted and stored on servers located in the People's Republic of China.

2.2 We protect data with industry-standard technical and organizational measures, including encryption in transit, access control, and tiered storage.

2.3 Anonymized aggregate data is retained long-term for product and model iteration; raw telemetry linkable to an anonymous instance is retained for no more than 24 months, after which it is deleted or thoroughly anonymized.

## 3. Sharing and Disclosure

3.1 We do not sell or rent your data to any third party.

3.2 We may publish industry research or benchmark data in anonymized, aggregated form (e.g. "the average performance range of a creative pattern in a given market"). Such data is k-anonymity aggregated (a statistical bucket is uploaded only when it contains at least 3 independent sources) and cannot be used to reverse-engineer any specific user or company.

3.3 We disclose information only when required by law, or by lawful request from judicial or administrative authorities.

## 4. Your Rights

4.1 You may turn off usage-data collection at any time (toggle on the Settings page).

4.2 You may ask us to delete telemetry associated with your anonymous instance identifier. Because the data is anonymized, we may not be able to locate a specific instance's data; if you wish to delete it, contact us via the channel below and provide your instance identifier (visible on the Settings page).

4.3 Cloud service users may export or delete their account data under the cloud service agreement.

## 5. Changes to This Policy

We may update this policy from time to time. Updates will be announced within the tool or via official channels, and material changes will be highlighted prominently. Continued use of the tool constitutes acceptance of the updated policy.

## 6. Contact Us

For questions, comments, or complaints about this policy, contact us via:
GitHub Issues (https://github.com/augura-os/augura/issues)

---

> Disclaimer: This document is a description of product practice and does not constitute legal advice.
