A School Platform (LMS / SIS) is an even stronger relational stress test for Ymer than HR because it introduces a true many-to-many junction table (Enrollments linking Students ↔ Courses ↔ Semesters) plus multi-level weighted grade rollups (Grades → Assignments → Enrollments → Students).
Here is the complete workspace blueprint and the ready-to-import seed data.
1. Page Tree Architecture
 * Academic Command Center (Dashboard page with embedded Chart.js widgets & semester KPIs)
   * Academic Terms & Catalog
     * Semesters (Database)
     * Courses (Database — Table & Board Views grouped by Semester)
     * Curriculum & Prerequisite Map (React Flow Mind Map)
   * Students & Enrollment
     * Students (Master Directory Database)
     * Enrollments (Junction Database — Table & Kanban Views by Status)
     * Enrollment & Add/Drop Flow (Excalidraw Diagram)
   * Coursework & Grading
     * Assignments (Database — Calendar & Table Views)
     * Grades (Database — Grouped by Course or Student)
   * Docs & Files
     * Student Handbook & Academic Policy (TipTap Doc)
     * Course Syllabi Template (TipTap Doc)
     * Transcripts & Syllabi Repository (S3/MinIO File Page)
2. Core Databases, Relations & Rollups
A. Semesters
| Property | Type | Purpose / Notes |
|---|---|---|
| Name | Title / Text | e.g., Spring 2026, Fall 2026, Spring 2027 |
| Dates | Date Range | Start and end of term |
| Status | Select | Completed, Active, Upcoming |
B. Students (Master Directory)
| Property | Type | Purpose / Notes |
|---|---|---|
| Full Name | Title / Text | Primary identifier |
| Program | Select | Computer Science, Mathematics, Physics, Data Science |
| Cohort Year | Number | Admission year |
| Status | Select | Active, Academic Probation, On Leave, Graduated |
| Advisor | User / Person | Assigned faculty advisor |
| Transcript PDF | File (S3/MinIO) | Official academic record |
| Credits Earned | Rollup (Enrollments → Courses) | Sum of credits for Completed courses |
| Cumulative GPA | Rollup (Enrollments) | Average of Grade Points across graded enrollments |
C. Courses
| Property | Type | Purpose / Notes |
|---|---|---|
| Course Code & Title | Title / Text | e.g., CS201 — Systems Security & Cryptography |
| Department | Select | Computer Science, Mathematics, Physics, Data Science |
| Semester | Relation (Semesters) | Term the course runs in |
| Instructor | User / Person | Lead faculty |
| Credits | Number | e.g., 7.5 (ECTS standard) |
| Schedule & Room | Text | e.g., Mon/Wed 13:00–15:00, Lab 3B |
| Max Capacity | Number | Seat cap (triggers waitlisting) |
| Syllabus PDF | File (S3/MinIO) | Uploaded course syllabus |
| Enrolled Count | Rollup (Enrollments) | Count of Enrolled / Completed rows |
| Avg Numeric Grade | Rollup (Enrollments) | Class average percentage |
D. Enrollments (Many-to-Many Junction)
| Property | Type | Purpose / Notes |
|---|---|---|
| Enrollment ID | Title / Text | e.g., ENR-008 |
| Student | Relation (Students) | Enrolled student |
| Course | Relation (Courses) | Target course |
| Semester | Relation (Semesters) | Mirrors course semester for fast filtering |
| Status | Select | Enrolled, Completed, Dropped, Waitlisted |
| Current Numeric Grade | Rollup / Formula (Grades) | Weighted average of graded assignments |
| Letter Grade | Formula / Select | A (≥90), B (≥80), C (≥70), D (≥60), F (<60) |
| Grade Points | Formula / Number | 4.0 to 0.0 scale |
| Attendance % | Number | Logged attendance rate |
E. Assignments
| Property | Type | Purpose / Notes |
|---|---|---|
| Title | Title / Text | e.g., Lab 1: Keplerian Orbit Simulation |
| Course | Relation (Courses) | Parent course |
| Type | Select | Lab, Exam, Project, Homework |
| Max Points | Number | e.g., 100 |
| Weight % | Number | Sums to 100% per course |
| Due Date | Date | Drives Calendar View |
F. Grades
| Property | Type | Purpose / Notes |
|---|---|---|
| Grade ID | Title / Text | e.g., GRD-001 |
| Student | Relation (Students) | Student being graded |
| Assignment | Relation (Assignments) | Specific deliverable |
| Course | Relation (Courses) | Parent course |
| Score | Number | Raw points earned |
| Percentage | Formula | (Score / Max Points) * 100 |
| Status | Select | Graded, Submitted, Late, Missing |
| Feedback | Text | Instructor notes |
3. Mind Maps, Drawings, Docs & Charts
 * Mind Map: Curriculum & Prerequisite Tree (React Flow)
   * Map course dependencies visually: CS101 (Intro to Algorithms) → CS201 (Systems Security) → CS305 (Distributed Systems), and MATH102 (Linear Algebra) → MATH205 (Probability) → DS210 (Applied ML).
 * Drawing: Enrollment & Academic Standing Flow (Excalidraw)
   * Diagram the add/drop window, waitlist promotion when a student drops, and the Academic Probation trigger when GPA dips below 2.0.
 * Collaborative Docs (TipTap) & Files (S3/MinIO):
   * Course Syllabus Template: Reusable TipTap doc with weekly schedule blocks and grading rubrics; test Yjs multi-instructor editing and version history.
   * S3/MinIO Uploads: Attach dummy syllabus and transcript PDFs to verify file previews and access boundaries.
 * Analytics & Charts (Chart.js) on Academic Command Center:
   * Bar Chart: Active Enrollment Count vs. Max Capacity per Course.
   * Doughnut Chart: Grade Distribution (A, B, C, D, F) for Fall 2026.
   * Bar Chart: Average Numeric Grade by Course.
   * Scatter / Bar Chart: Attendance % vs. Current Numeric Grade (highlights at-risk students like STU-005).
4. Seed Data Downloads (JSON & CSV)
All 86 records across the 6 tables are referentially intact and mathematically verified.
Complete Bundle (All CSVs + Individual & Combined JSONs)
Combined JSON (All 6 Tables)
Individual CSV Files
 * semesters.csv
 * students.csv
 * courses.csv
 * enrollments.csv
 * assignments.csv
 * grades.csv
Rollup & Edge-Case Verification Benchmarks
After importing and wiring up relations in Ymer, verify your rollups against these exact ground-truth numbers:
 * Fall 2026 Course Averages (Courses ← Enrollments):
   * CS201 (4 enrolled): 73.8% (pulled down by STU-005's missing midterm)
   * MATH205 (4 enrolled, 1 dropped): 86.6%
   * PHYS150 (3 enrolled): 88.6%
   * DS210 (3 enrolled, 1 waitlisted): 86.5%
 * Student GPA & Credits (Students ← Enrollments):
   * STU-001 (Freja Lindqvist): GPA 3.50, 15.0 credits earned (Spring 2026 completed)
   * STU-003 (Lukas Novak): GPA 4.00, 7.5 credits earned
   * STU-005 (Emil Karlsson — Academic Probation): GPA 0.00, 0.0 credits earned (Late lab + Missing midterm in CS201, Dropped MATH205)
Stress-test idea: Drag STU-004 (ENR-023) from Waitlisted to Enrolled on the Enrollments Kanban board and verify DS210's Enrolled Count rollup increments from 3 to 4 without distorting Avg Numeric Grade.

