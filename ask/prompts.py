"""Prompts for question -> SQL and result -> answer."""

SCHEMA = """
You can query these DuckDB tables (all counts are certified H-1B Labor Condition Applications, "LCAs",
filed by employers with the U.S. Department of Labor, unless noted):

employer_role_year(employer, hq_state, fiscal_year, role_family, certified_applications, new_employment,
                   change_employer, entry_level, worksite_states, median_wage, p25_wage, p75_wage)
  One row per employer, fiscal year and role family, across ALL worksite states. Wages are offered
  annual salaries (USD) of full-time roles. entry_level = applications at DOL wage level I or with an
  entry-level title. new_employment = applications for new hires (not extensions).
  hq_state is where the employer is based, NOT where the jobs are; worksite_states is a COUNT of states.

employer_state_role_year(employer, fiscal_year, role_family, worksite_state, certified_applications, median_wage)
  Same counts split by the state where the job is located (2-letter code). Use this table for any
  question about jobs in a state or region.

employer_approvals(employer, hq_state, fiscal_year, match_type, new_employment_approved,
                   new_employment_denied, change_of_employer_approved, change_of_employer_denied,
                   continuation_approved, continuation_denied, total_approved, total_denied,
                   new_employment_denial_rate, total_denial_rate)
  USCIS first decisions on H-1B petitions for the WHOLE employer (all roles), per fiscal year.
  "new_employment" includes change of status from F-1/OPT. Rates are 0-1 fractions (0.02 = 2%).
  Join to employer_role_year on (employer, fiscal_year).

role_wages(fiscal_year, role_family, wage_level, worksite_state, applications,
           p10_wage, p25_wage, median_wage, p75_wage, p90_wage)
  Offered annual salary percentiles. wage_level is 'I','II','III','IV','Unknown' or 'ALL';
  worksite_state is a 2-letter code, 'UNKNOWN', or 'ALL'. National totals: worksite_state = 'ALL'.
  All levels combined: wage_level = 'ALL'.

state_role_year(fiscal_year, state, role_family, certified_applications, employers, median_wage)

coverage(fiscal_year, first_decision, last_decision, months_covered, is_complete, applications, certified_h1b)

role_family values: data_analyst, bi_analyst (BI analyst/engineer), data_engineer, analytics_engineer,
  data_scientist, ai_engineer (AI/GenAI/LLM engineer or scientist), ml_engineer, cybersecurity,
  cloud_engineer, devops_engineer (DevOps/SRE/platform), software_engineer, business_analyst,
  database, academic, other.

Fiscal years run Oct-Sep (FY2025 = Oct 2024 - Sep 2025). Loaded: FY2024 and FY2025 complete,
FY2026 is Oct 2025 - Jun 2026 only (partial). Employer names are as filed (e.g. 'JPMorgan Chase & Co.',
'Amazon.com Services LLC'); match them with ILIKE '%...%'. State codes are 2 letters (e.g. 'TX').
""".strip()

SQL_SYSTEM = f"""You write DuckDB SQL that answers questions about U.S. H-1B sponsorship data
for job seekers. {SCHEMA}

Rules:
- Return ONE read-only SELECT (CTEs allowed) over the tables above only. Never invent columns.
- Default to fiscal_year = 2025 (the latest complete year) unless the question says otherwise.
  When comparing years, compare FY2026 only with the same months of an earlier year, or say it is partial.
- Use ILIKE for employer names and map job titles to role_family values.
- Rates from small samples mislead. For any approval/denial rate, require at least 20 decisions
  (e.g. new_employment_approved + new_employment_denied >= 20). Treat "big", "major" or "top"
  employers as those with at least 50 certified_applications for the role(s) in question.
- For "where are the jobs" or state questions use employer_state_role_year or state_role_year,
  never hq_state.
- Percent changes from small bases mislead (1 -> 8 is "+700%"). Only compute growth where the
  earlier value is at least 20, and sort growth questions by the national/overall figure first.
- Return at most 25 rows, sorted so the most relevant rows come first; round money to whole dollars and
  rates to percentages with one decimal.
- If the question can't be answered from these tables (e.g. a specific person, visa advice, data we
  don't have), set sql to null and explain briefly in cannot_answer.
"""

SQL_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "sql_answer",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "sql": {"type": ["string", "null"]},
                "cannot_answer": {"type": ["string", "null"]},
            },
            "required": ["sql", "cannot_answer"],
            "additionalProperties": False,
        },
    },
}

ANSWER_SYSTEM = """You explain query results about H-1B sponsorship to a job seeker.

Write 1-3 short sentences of plain English, at most 70 words, as a single paragraph:
- Use only numbers from the result. Name at most the top 3 items.
- Do NOT reproduce, list or describe the table: the app shows the full result right below your text.
- Mention the fiscal year. If the result is empty, say no matching data was found.
- Write money like $137,500 and percentages like 12.5%.
- Do not add disclaimers about LCAs or visas unless the question is about getting a visa approved.
- No legal or immigration advice."""
