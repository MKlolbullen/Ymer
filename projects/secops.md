An IT & Cybersecurity Operations Platform pushes Ymer’s graph, file, and relational primitives even harder: it combines an Attack Surface Inventory, Vulnerability Triage, Automated Recon/Scan Pipelines, and IT Remediation Queues—plus React Flow attack-path mapping and Excalidraw network topology diagrams.
Here is the complete workspace blueprint and ready-to-import seed data.
1. Page Tree Architecture
 * SecOps Command Center (Dashboard page with embedded Chart.js widgets & SLA alerts)
   * Engagements & Scope
     * Engagements (Master Database)
     * Rules of Engagement & Attestation Reports (TipTap Docs + S3/MinIO PDFs)
   * Attack Surface & Recon
     * Assets (Database — Table & Grouped Views by Exposure / Criticality)
     * Scan Runs (Database — Pipeline execution logs & raw JSON outputs)
     * Attack Surface & Recon Graph (React Flow Mind Map)
   * Findings & Remediation
     * Vulnerabilities (Database — Kanban Board by Triage Status & Severity)
     * Remediation Tasks (Database — Kanban Board by IT/Dev Team)
     * Incident & Triage Playbook (Excalidraw Diagram)
   * Knowledge Base
     * Pentest Report Template (TipTap Doc)
     * PoC & Evidence Vault (S3/MinIO File Repository Page)
2. Core Databases, Relations & Rollups
A. Engagements
| Property | Type | Purpose / Notes |
|---|---|---|
| Name | Title / Text | e.g., NordicFin Core Banking Web & API Pentest |
| Client | Text / Select | Organization name |
| Type | Select | Web/API Pentest, Attack Surface / Recon, Red Team, AI/LLM Assessment |
| Status | Select | Scoping, Active, Reporting, Closed |
| Lead | User / Person | Engagement lead |
| Dates | Date Range | Start and end dates |
| Rules of Engagement PDF | File (S3/MinIO) | Signed authorization / scope doc |
| Total Assets | Rollup (Assets) | Count of in-scope assets |
| Open Critical/High | Rollup (Vulnerabilities) | Count of open Critical + High findings |
| Max CVSS | Rollup (Vulnerabilities) | Highest CVSS 3.1 score in engagement |
B. Assets (Attack Surface Matrix)
| Property | Type | Purpose / Notes |
|---|---|---|
| Identifier | Title / Text | Domain, subdomain, IP, S3 bucket, or host |
| Asset Type | Select | API, Web App, Subdomain, Identity/SSO, Cloud Storage, IP / Host, Domain Controller, AI/MCP Endpoint |
| Engagement | Relation (Engagements) | Parent engagement |
| Environment | Select | Prod, Staging, Dev |
| Exposure | Select | External, Internal |
| Open Ports | Text | e.g., 80, 443, 8080 |
| Tech Stack | Text / Multi-select | e.g., Go, Envoy, PostgreSQL |
| Criticality | Select | Tier 1, Tier 2, Tier 3 |
| Open Findings | Rollup (Vulnerabilities) | Count of active (non-closed, non-FP) vulnerabilities |
| Highest Open Severity | Rollup (Vulnerabilities) | Worst active severity on this asset |
C. Vulnerabilities
| Property | Type | Purpose / Notes |
|---|---|---|
| Title | Title / Text | e.g., BOLA / IDOR on /v2/accounts/{id}/transactions |
| Engagement | Relation (Engagements) | Parent engagement |
| Asset | Relation (Assets) | Affected target |
| Severity | Select | Critical, High, Medium, Low, Info |
| CVSS Score | Number | 0.0 – 10.0 |
| OWASP / CWE | Text | e.g., API1:2023 / CWE-639, LLM01:2025 |
| Status | Select | New, Confirmed, In Remediation, Retest, Closed, False Positive |
| Discovered By | Text / User | Analyst or automated scanner |
| SLA Due Date | Date | Remediation deadline |
| PoC File | File (S3/MinIO) | Burp request, exploit script, BloodHound JSON, or video |
D. Scan Runs
| Property | Type | Purpose / Notes |
|---|---|---|
| Scan ID | Title / Text | e.g., SCN-001 |
| Engagement | Relation (Engagements) | Parent engagement |
| Asset | Relation (Assets) | Target scanned |
| Tool / Playbook | Text | e.g., subfinder + dnsx + httpx + nuclei, naabu-top-1000 |
| Status | Select | Queued, Running, Completed, Failed |
| Findings Generated | Number | Raw count produced |
| Started At | Date/Time | Execution timestamp |
| Raw Output | File (S3/MinIO) | JSON/XML/log output artifact |
E. Remediation Tasks
| Property | Type | Purpose / Notes |
|---|---|---|
| Task Title | Title / Text | Actionable fix for engineering/IT |
| Vulnerability | Relation (Vulnerabilities) | Linked finding |
| Asset | Relation (Assets) | Target system to patch |
| Owner Team | Select | Backend, Frontend, Infra, DevOps, SecOps |
| Priority | Select | P0, P1, P2 |
| Stage | Select | To Do, In Progress, Blocked, Verify, Done |
| Due Date | Date | Synced with vulnerability SLA |
3. Mind Maps, Drawings, Docs & Charts
 * Mind Map 1: Attack Surface & Recon Pipeline (React Flow)
   * Model a multi-stage discovery graph: Root Domain → subfinder / dnsx → naabu / httpx → katana / ffuf → nuclei / burp-active → Vulnerabilities, mirroring how assets branch into findings.
 * Mind Map 2: Kill Chain / Attack Path (React Flow)
   * Map a concrete lateral movement path from the seed data (e.g., ENG-003: filesrv02 SMB Signing Disabled → NTLM Relay → dc01 Kerberoastable Service Account → Domain Admin).
 * Drawing: Vulnerability Triage & Retest State Machine (Excalidraw)
   * Sketch the lifecycle: Scanner/Analyst Discovery → Triage (False Positive Filter) → Ticket Created → Patch Deployed → Retest → Closed (with a loop back to In Remediation if retest fails).
 * Collaborative Docs (TipTap) & Files (S3/MinIO):
   * Pentest Report Template: Multi-section TipTap doc (Executive Summary, Scope, Findings Breakdown, Remediation Roadmap) to test Yjs real-time multi-tester report writing, inline review comments, and version history.
   * S3/MinIO Uploads: Attach dummy PoC logs, Burp exports, and RoE PDFs.
 * Analytics & Charts (Chart.js) on SecOps Command Center:
   * Doughnut Chart: Open Findings by Severity (Critical, High, Medium, Low).
   * Bar Chart: Open Critical/High Findings by Engagement.
   * Stacked Bar Chart: Remediation Tasks by Owner Team & Stage (To Do, In Progress, Blocked, Verify, Done).
   * Bar Chart: Scan Runs by Status (Completed, Running, Failed).
4. Seed Data Downloads (JSON & CSV)
All 54 records across the 5 tables are referentially intact and include realistic edge cases (a False Positive scanner alert, a Failed sqlmap run, a Running retest, and a Blocked DevOps remediation ticket).
Complete Bundle (All CSVs + Individual & Combined JSONs)
Combined JSON (All 5 Tables)
Individual CSV Files
 * engagements.csv
 * assets.csv
 * vulnerabilities.csv
 * scan_runs.csv
 * remediation_tasks.csv
Rollup & Edge-Case Verification Benchmarks
After importing and wiring up relations in Ymer, verify your rollups against these exact ground-truth numbers (excluding False Positive from valid findings):
 * ENG-001 (NordicFin Web & API Pentest): 4 assets, 6 valid findings, 3 open Critical/High, max CVSS 9.8
 * ENG-002 (CloudMesh Attack Surface Audit): 4 assets, 4 valid findings (excludes VULN-011 False Positive), 3 open Critical/High, max CVSS 9.4
 * ENG-003 (Valkyrie Internal AD Red Team): 3 assets, 4 valid findings, 2 open Critical/High, max CVSS 9.0
 * ENG-004 (LLM & MCP Gateway Review): 1 asset, 1 valid finding, 1 open Critical/High, max CVSS 8.2
Stress-test idea: Drag VULN-002 (JWT Algorithm Confusion) from Retest to Closed on the Vulnerabilities Kanban board and verify ENG-001's Open Critical/High rollup drops from 3 to 2 immediately.
