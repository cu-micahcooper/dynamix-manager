from __future__ import annotations

from pathlib import Path

import nbformat
from nbformat.v4 import new_code_cell, new_markdown_cell, new_notebook


def write_survey_health_notebook(db_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_db_path = db_path.resolve()
    notebook = new_notebook(
        cells=[
            new_markdown_cell(
                "# Survey Health\n\n"
                "This notebook reads the cached TeamDynamix survey model from DuckDB "
                "and frames the results for executive review: coverage, sentiment, time trends, "
                "team/service patterns, and low-score comment review."
            ),
            new_code_cell(
                "from pathlib import Path\n\n"
                "import duckdb\n"
                "import pandas as pd\n\n"
                "import plotly.express as px\n\n"
                "from dynamix_manager.metrics import summarize_survey_health\n"
                f"\ndb_path = Path({str(resolved_db_path)!r})\n"
                "connection = duckdb.connect(str(db_path), read_only=True)\n"
            ),
            new_code_cell(
                "survey_health = connection.execute(\n"
                "    \"select * from ticket_linked_surveys\"\n"
                ").fetchdf()\n"
                "for column in ['survey_completed_at', 'created_at', 'responded_at', 'resolved_at']:\n"
                "    if column in survey_health.columns:\n"
                "        survey_health[column] = pd.to_datetime(survey_health[column], utc=True, errors='coerce')\n"
                "survey_health.head()"
            ),
            new_markdown_cell("## Executive Summary"),
            new_code_cell(
                "summary = summarize_survey_health(survey_health)\n"
                "pd.DataFrame(\n"
                "    [\n"
                "        {'metric': 'Responses', 'value': summary['total_responses']},\n"
                "        {'metric': 'Linked responses', 'value': summary['linked_responses']},\n"
                "        {'metric': 'Link rate', 'value': summary['linked_responses'] / summary['total_responses'] if summary['total_responses'] else 0},\n"
                "        {'metric': 'Comment rate', 'value': summary['comment_rate']},\n"
                "        {'metric': 'Average score', 'value': summary['average_score']},\n"
                "        {'metric': 'Negative response rate', 'value': summary['negative_response_rate']},\n"
                "    ]\n"
                ")"
            ),
            new_markdown_cell("## Satisfaction Mix"),
            new_code_cell(
                "satisfaction_mix = (\n"
                "    survey_health.groupby('satisfaction_label', dropna=False)\n"
                "    .size()\n"
                "    .sort_values(ascending=False)\n"
                "    .to_frame('responses')\n"
                ")\n"
                "satisfaction_mix"
            ),
            new_code_cell(
                "satisfaction_mix_chart = satisfaction_mix.reset_index(names='satisfaction_label')\n"
                "px.bar(\n"
                "    satisfaction_mix_chart,\n"
                "    x='satisfaction_label',\n"
                "    y='responses',\n"
                "    title='Survey Satisfaction Mix',\n"
                ").show()\n"
            ),
            new_markdown_cell("## Monthly Trend"),
            new_code_cell(
                "monthly_trend = (\n"
                "    survey_health.assign(month=survey_health['survey_completed_at'].dt.to_period('M').dt.to_timestamp())\n"
                "    .groupby('month', dropna=False)\n"
                "    .agg(\n"
                "        responses=('response_id', 'count'),\n"
                "        linked_responses=('ticket_linked', 'sum'),\n"
                "        avg_score=('satisfaction_label', lambda s: s.map({'Very Dissatisfied': 1, 'Dissatisfied': 2, 'Satisfied': 4, 'Very Satisfied': 5}).mean()),\n"
                "        negative_rate=('satisfaction_label', lambda s: s.isin(['Very Dissatisfied', 'Dissatisfied']).mean()),\n"
                "    )\n"
                "    .sort_index()\n"
                ")\n"
                "monthly_trend.tail(12)"
            ),
            new_code_cell(
                "monthly_trend_chart = monthly_trend.reset_index()\n"
                "px.line(\n"
                "    monthly_trend_chart,\n"
                "    x='month',\n"
                "    y=['responses', 'linked_responses'],\n"
                "    title='Monthly Survey Volume',\n"
                ").show()\n"
            ),
            new_markdown_cell("## Team Breakdown"),
            new_code_cell(
                "team_summary = (\n"
                "    survey_health.loc[survey_health['ticket_linked']]\n"
                "    .groupby('team_name', dropna=False)\n"
                "    .agg(\n"
                "        responses=('response_id', 'count'),\n"
                "        avg_score=('satisfaction_label', lambda s: s.map({'Very Dissatisfied': 1, 'Dissatisfied': 2, 'Satisfied': 4, 'Very Satisfied': 5}).mean()),\n"
                "        negative_rate=('satisfaction_label', lambda s: s.isin(['Very Dissatisfied', 'Dissatisfied']).mean()),\n"
                "        avg_response_hours=('response_time_hours', 'mean'),\n"
                "        avg_resolution_hours=('resolution_time_hours', 'mean'),\n"
                "    )\n"
                "    .sort_values(['responses', 'avg_score'], ascending=[False, False])\n"
                ")\n"
                "team_summary.head(15)"
            ),
            new_code_cell(
                "team_chart = team_summary.reset_index().head(10)\n"
                "px.bar(\n"
                "    team_chart,\n"
                "    x='team_name',\n"
                "    y='responses',\n"
                "    color='avg_score',\n"
                "    title='Top Teams by Survey Response Volume',\n"
                ").show()\n"
            ),
            new_markdown_cell("## Service Breakdown"),
            new_code_cell(
                "service_summary = (\n"
                "    survey_health.loc[survey_health['ticket_linked']]\n"
                "    .groupby('service_name', dropna=False)\n"
                "    .agg(\n"
                "        responses=('response_id', 'count'),\n"
                "        avg_score=('satisfaction_label', lambda s: s.map({'Very Dissatisfied': 1, 'Dissatisfied': 2, 'Satisfied': 4, 'Very Satisfied': 5}).mean()),\n"
                "        negative_rate=('satisfaction_label', lambda s: s.isin(['Very Dissatisfied', 'Dissatisfied']).mean()),\n"
                "    )\n"
                "    .sort_values('responses', ascending=False)\n"
                ")\n"
                "service_summary.head(20)"
            ),
            new_markdown_cell("## Time-To-Serve by Satisfaction"),
            new_code_cell(
                "time_to_serve = (\n"
                "    survey_health.groupby('satisfaction_label', dropna=False)\n"
                "    .agg(\n"
                "        responses=('response_id', 'count'),\n"
                "        avg_response_hours=('response_time_hours', 'mean'),\n"
                "        avg_resolution_hours=('resolution_time_hours', 'mean'),\n"
                "    )\n"
                "    .sort_values('responses', ascending=False)\n"
                ")\n"
                "time_to_serve"
            ),
            new_markdown_cell("## Low-Score Review"),
            new_code_cell(
                "low_score_comments = survey_health.loc[\n"
                "    survey_health['satisfaction_label'].isin(['Very Dissatisfied', 'Dissatisfied']),\n"
                "    ['survey_completed_at', 'team_name', 'service_name', 'assignee_name', 'response_time_hours', 'resolution_time_hours', 'comment_text'],\n"
                "].sort_values('survey_completed_at', ascending=False)\n"
                "low_score_comments.head(25)"
            ),
            new_markdown_cell("## Recent Comment Review"),
            new_code_cell(
                "recent_comments = survey_health.loc[\n"
                "    survey_health['comment_text'].astype('string').fillna('').str.strip() != '',\n"
                "    ['survey_completed_at', 'satisfaction_label', 'team_name', 'service_name', 'comment_text'],\n"
                "].sort_values('survey_completed_at', ascending=False).head(25)\n"
                "recent_comments"
            ),
        ]
    )
    nbformat.write(notebook, output_path)
    return output_path


def write_ticket_health_notebook(db_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_db_path = db_path.resolve()
    notebook = new_notebook(
        cells=[
            new_markdown_cell(
                "# Ticket Health\n\n"
                "This notebook reads cached ticket data from DuckDB and frames a balanced scorecard across backlog, service quality, SLA health, hygiene, and ownership concentration."
            ),
            new_code_cell(
                "from pathlib import Path\n\n"
                "import duckdb\n"
                "import pandas as pd\n\n"
                "import plotly.express as px\n\n"
                "from dynamix_manager.metrics import summarize_ticket_health\n\n"
                f"db_path = Path({str(resolved_db_path)!r})\n"
                "connection = duckdb.connect(str(db_path), read_only=True)\n"
            ),
            new_code_cell(
                "tickets = connection.execute(\n"
                "    \"select * from tickets\"\n"
                ").fetchdf()\n"
                "days_off_exists = connection.execute(\n"
                "    \"select count(*) from information_schema.tables where table_schema = 'main' and table_name = 'days_off'\"\n"
                ").fetchone()[0] > 0\n"
                "days_off = connection.execute(\"select * from days_off\").fetchdf() if days_off_exists else pd.DataFrame(columns=['holiday_date'])\n"
                "quality_flags_exists = connection.execute(\n"
                "    \"select count(*) from information_schema.tables where table_schema = 'main' and table_name = 'ticket_quality_flags'\"\n"
                ").fetchone()[0] > 0\n"
                "ticket_quality_flags = connection.execute(\"select * from ticket_quality_flags\").fetchdf() if quality_flags_exists else pd.DataFrame()\n"
                "quality_interactions_exists = connection.execute(\n"
                "    \"select count(*) from information_schema.tables where table_schema = 'main' and table_name = 'ticket_quality_interactions'\"\n"
                ").fetchone()[0] > 0\n"
                "ticket_quality_interactions = connection.execute(\"select * from ticket_quality_interactions\").fetchdf() if quality_interactions_exists else pd.DataFrame()\n"
                "if 'holiday_date' in days_off.columns:\n"
                "    days_off['holiday_date'] = pd.to_datetime(days_off['holiday_date'], utc=True, errors='coerce').dt.strftime('%Y-%m-%d')\n"
                "business_holidays = set(days_off['holiday_date'].dropna()) if 'holiday_date' in days_off.columns else set()\n"
                "for column in ['created_at', 'responded_at', 'resolved_at']:\n"
                "    if column in tickets.columns:\n"
                "        tickets[column] = pd.to_datetime(tickets[column], utc=True, errors='coerce')\n"
                "for column in ['sla_begin_at', 'respond_by_at', 'resolve_by_at']:\n"
                "    if column in tickets.columns:\n"
                "        tickets[column] = pd.to_datetime(tickets[column], utc=True, errors='coerce')\n"
                "if 'response_time_hours' not in tickets.columns and {'created_at', 'responded_at'}.issubset(tickets.columns):\n"
                "    tickets['response_time_hours'] = (tickets['responded_at'] - tickets['created_at']).dt.total_seconds() / 3600\n"
                "if 'resolution_time_hours' not in tickets.columns and {'created_at', 'resolved_at'}.issubset(tickets.columns):\n"
                "    tickets['resolution_time_hours'] = (tickets['resolved_at'] - tickets['created_at']).dt.total_seconds() / 3600\n"
                "if 'last_public_interaction_at' in ticket_quality_flags.columns:\n"
                "    ticket_quality_flags['last_public_interaction_at'] = pd.to_datetime(ticket_quality_flags['last_public_interaction_at'], utc=True, errors='coerce')\n"
                "if 'last_private_interaction_at' in ticket_quality_flags.columns:\n"
                "    ticket_quality_flags['last_private_interaction_at'] = pd.to_datetime(ticket_quality_flags['last_private_interaction_at'], utc=True, errors='coerce')\n"
                "if 'created_at' in ticket_quality_interactions.columns:\n"
                "    ticket_quality_interactions['created_at'] = pd.to_datetime(ticket_quality_interactions['created_at'], utc=True, errors='coerce')\n"
                "for column, default in {\n"
                "    'client_last_interaction_flag': False,\n"
                "    'it_follow_up_without_client_response_flag': False,\n"
                "    'stale_public_update_flag': False,\n"
                "    'private_activity_since_last_public_flag': False,\n"
                "    'stale_public_update_business_days': 0,\n"
                "    'last_private_interaction_at': pd.NaT,\n"
                "    'last_private_interaction_by': pd.NA,\n"
                "}.items():\n"
                "    if column not in ticket_quality_flags.columns:\n"
                "        ticket_quality_flags[column] = default\n"
                "ticket_health_summary = summarize_ticket_health(tickets, days_off=days_off, quality_flags=ticket_quality_flags, interactions=ticket_quality_interactions)\n"
                "tickets.head()"
            ),
            new_markdown_cell("## Quality Scorecard"),
            new_code_cell(
                "pd.DataFrame([\n"
                "    {'metric': 'Avg touches per ticket', 'value': ticket_health_summary['average_touches_per_ticket']},\n"
                "    {'metric': 'Client last interaction', 'value': ticket_health_summary['quality_counts']['client_last_interaction']},\n"
                "    {'metric': 'Repeated IT follow-up', 'value': ticket_health_summary['quality_counts']['repeated_it_followup']},\n"
                "    {'metric': 'Stale Public Update', 'value': ticket_health_summary['quality_counts']['stale_public_updates']},\n"
                "    {'metric': 'Private Activity Since Last Public Update', 'value': ticket_health_summary['quality_counts']['private_activity_since_last_public']},\n"
                "    {'metric': 'Stale open tickets', 'value': ticket_health_summary['quality_counts']['stale_open_tickets']},\n"
                "])"
            ),
            new_markdown_cell("## SLA Health"),
            new_code_cell(
                "pd.DataFrame([\n"
                "    {'metric': 'Covered tickets', 'value': ticket_health_summary['sla_summary']['covered_tickets']},\n"
                "    {'metric': 'SLA violations', 'value': ticket_health_summary['sla_summary']['violated_tickets']},\n"
                "    {'metric': 'Respond breached', 'value': ticket_health_summary['sla_summary']['respond_breached']},\n"
                "    {'metric': 'Resolve breached', 'value': ticket_health_summary['sla_summary']['resolve_breached']},\n"
                "    {'metric': 'Near respond due', 'value': ticket_health_summary['sla_summary']['open_near_respond_due']},\n"
                "    {'metric': 'Near resolve due', 'value': ticket_health_summary['sla_summary']['open_near_resolve_due']},\n"
                "])"
            ),
            new_markdown_cell("## Ticket Hygiene"),
            new_code_cell(
                "pd.DataFrame([\n"
                "    {'metric': 'Missing title', 'value': ticket_health_summary['hygiene_counts']['missing_title']},\n"
                "    {'metric': 'Missing service', 'value': ticket_health_summary['hygiene_counts']['missing_service']},\n"
                "    {'metric': 'Missing team', 'value': ticket_health_summary['hygiene_counts']['missing_team']},\n"
                "    {'metric': 'Open unassigned', 'value': ticket_health_summary['hygiene_counts']['open_unassigned']},\n"
                "    {'metric': 'Missing priority', 'value': ticket_health_summary['hygiene_counts']['missing_priority']},\n"
                "])"
            ),
            new_markdown_cell("## Status Mix"),
            new_code_cell(
                "status_mix = (\n"
                "    tickets.groupby('status_name', dropna=False)\n"
                "    .size()\n"
                "    .sort_values(ascending=False)\n"
                "    .to_frame('tickets')\n"
                ")\n"
                "status_mix"
            ),
            new_code_cell(
                "status_mix_chart = status_mix.reset_index(names='status_name')\n"
                "px.bar(\n"
                "    status_mix_chart,\n"
                "    x='status_name',\n"
                "    y='tickets',\n"
                "    title='Ticket Status Mix',\n"
                ")\n"
            ),
            new_markdown_cell("## Team Breakdown"),
            new_code_cell(
                "team_breakdown = (\n"
                "    tickets.groupby('team_name', dropna=False)\n"
                "    .agg(\n"
                "        tickets=('ticket_id', 'count'),\n"
                "        avg_response_hours=('response_time_hours', 'mean'),\n"
                "        avg_resolution_hours=('resolution_time_hours', 'mean'),\n"
                "    )\n"
                "    .sort_values('tickets', ascending=False)\n"
                "    .head(15)\n"
                ")\n"
                "team_breakdown"
            ),
            new_code_cell(
                "team_breakdown_chart = team_breakdown.reset_index().head(10)\n"
                "px.bar(\n"
                "    team_breakdown_chart,\n"
                "    x='team_name',\n"
                "    y='tickets',\n"
                "    color='avg_resolution_hours',\n"
                "    title='Top Teams by Ticket Volume',\n"
                ")\n"
            ),
            new_markdown_cell("## Team Completions Per Business Day"),
            new_code_cell(
                "business_day_tickets = (\n"
                "    tickets.loc[tickets['resolved_at'].notna()]\n"
                "    .assign(resolved_date=tickets.loc[tickets['resolved_at'].notna(), 'resolved_at'].dt.strftime('%Y-%m-%d'))\n"
                ")\n"
                "business_day_tickets = business_day_tickets.loc[business_day_tickets['resolved_at'].dt.weekday < 5]\n"
                "if business_holidays:\n"
                "    business_day_tickets = business_day_tickets.loc[~business_day_tickets['resolved_date'].isin(business_holidays)]\n"
                "team_completion_daily = (\n"
                "    business_day_tickets\n"
                "    .groupby(['resolved_date', 'team_name'], dropna=False)\n"
                "    .size()\n"
                "    .reset_index(name='completed_tickets')\n"
                "    .sort_values(['resolved_date', 'completed_tickets', 'team_name'], ascending=[False, False, True])\n"
                ")\n"
                "team_completion_daily.head(30)"
            ),
            new_code_cell(
                "team_completion_chart = team_completion_daily.head(100)\n"
                "px.line(\n"
                "    team_completion_chart,\n"
                "    x='resolved_date',\n"
                "    y='completed_tickets',\n"
                "    color='team_name',\n"
                "    title='Team Completions Per Business Day',\n"
                ")\n"
            ),
            new_markdown_cell("## Member Completions Per Business Day"),
            new_code_cell(
                "member_completion_daily = (\n"
                "    business_day_tickets\n"
                "    .assign(\n"
                "        assignee_name=business_day_tickets['assignee_name'].astype('string').fillna('Unassigned').replace('', 'Unassigned'),\n"
                "        team_name=business_day_tickets['team_name'].astype('string').fillna('Unassigned team').replace('', 'Unassigned team'),\n"
                "    )\n"
                "    .groupby(['resolved_date', 'team_name', 'assignee_name'], dropna=False)\n"
                "    .size()\n"
                "    .reset_index(name='completed_tickets')\n"
                "    .sort_values(['resolved_date', 'team_name', 'completed_tickets', 'assignee_name'], ascending=[False, True, False, True])\n"
                ")\n"
                "member_completion_daily.head(40)"
            ),
            new_markdown_cell("## Backlog Aging"),
            new_code_cell(
                "pd.DataFrame(ticket_health_summary['backlog_age_buckets'])"
            ),
            new_markdown_cell("## Service Breakdown"),
            new_code_cell(
                "service_breakdown = (\n"
                "    tickets.groupby('service_name', dropna=False)\n"
                "    .agg(\n"
                "        tickets=('ticket_id', 'count'),\n"
                "        avg_response_hours=('response_time_hours', 'mean'),\n"
                "        avg_resolution_hours=('resolution_time_hours', 'mean'),\n"
                "    )\n"
                "    .sort_values('tickets', ascending=False)\n"
                "    .head(20)\n"
                ")\n"
                "service_breakdown"
            ),
            new_markdown_cell("## High-Touch Tickets"),
            new_code_cell(
                "pd.DataFrame(ticket_health_summary['high_touch_tickets'])"
            ),
            new_markdown_cell("## Team Quality Hotspots"),
            new_code_cell(
                "pd.DataFrame(ticket_health_summary['team_quality_hotspots'])"
            ),
            new_markdown_cell("## Stale Public Update"),
            new_code_cell(
                "ticket_quality_flags.loc[\n"
                "    ticket_quality_flags['stale_public_update_flag'].fillna(False),\n"
                "    ['ticket_id', 'ticket_title', 'team_name', 'service_name', 'stale_public_update_business_days', 'last_public_interaction_at', 'last_public_interaction_by'],\n"
                "].sort_values(['stale_public_update_business_days', 'last_public_interaction_at'], ascending=[False, False]).head(50)"
            ),
            new_markdown_cell("## Private Activity Since Last Public Update"),
            new_code_cell(
                "ticket_quality_flags.loc[\n"
                "    ticket_quality_flags['private_activity_since_last_public_flag'].fillna(False),\n"
                "    ['ticket_id', 'ticket_title', 'team_name', 'service_name', 'last_private_interaction_at', 'last_private_interaction_by', 'last_public_interaction_by'],\n"
                "].sort_values(['last_private_interaction_at', 'ticket_id'], ascending=[False, True]).head(50)"
            ),
            new_markdown_cell("## Quality-Adjusted SLA"),
            new_code_cell(
                "pd.DataFrame([\n"
                "    {'metric': 'Breached and high touch', 'value': ticket_health_summary['quality_adjusted_sla']['breached_and_high_touch']},\n"
                "    {'metric': 'Breached and client waiting', 'value': ticket_health_summary['quality_adjusted_sla']['breached_and_client_waiting']},\n"
                "    {'metric': 'Breached and repeated IT follow-up', 'value': ticket_health_summary['quality_adjusted_sla']['breached_and_repeated_it_followup']},\n"
                "])"
            ),
            new_markdown_cell("## SLA Hotspots"),
            new_code_cell(
                "pd.DataFrame(ticket_health_summary['sla_hotspots'])"
            ),
            new_markdown_cell("## Backlog Load Hotspots"),
            new_code_cell(
                "pd.DataFrame(ticket_health_summary['member_backlog_hotspots'])"
            ),
            new_markdown_cell("## Recurring Issue Candidates"),
            new_code_cell(
                "pd.DataFrame(ticket_health_summary['top_recurrent_titles'])"
            ),
            new_markdown_cell("## Hygiene Gaps"),
            new_code_cell(
                "pd.DataFrame(ticket_health_summary['hygiene_tickets'])"
            ),
            new_markdown_cell("## Backlog Review"),
            new_code_cell(
                "tickets.loc[\n"
                "    tickets['resolved_at'].isna(),\n"
                "    ['ticket_id', 'status_name', 'team_name', 'service_name', 'assignee_name', 'created_at', 'response_time_hours'],\n"
                "].sort_values('created_at', ascending=True).head(25)"
            ),
            new_markdown_cell("## Stale Open Tickets"),
            new_code_cell(
                "pd.DataFrame(ticket_health_summary['stale_open_tickets'])"
            ),
            new_markdown_cell("## Time To Serve"),
            new_code_cell(
                "tickets[['response_time_hours', 'resolution_time_hours']].describe()"
            ),
        ]
    )
    nbformat.write(notebook, output_path)
    return output_path


def write_ticket_quality_notebook(db_path: Path, output_path: Path) -> Path:
    output_path.parent.mkdir(parents=True, exist_ok=True)
    resolved_db_path = db_path.resolve()
    notebook = new_notebook(
        cells=[
            new_markdown_cell(
                "# Ticket Quality\n\n"
                "This notebook reads cached ticket quality flags from DuckDB and highlights tickets where the interaction pattern suggests weak response discipline."
            ),
            new_code_cell(
                "from pathlib import Path\n\n"
                "import duckdb\n"
                "import pandas as pd\n\n"
                "import plotly.express as px\n\n"
                f"db_path = Path({str(resolved_db_path)!r})\n"
                "connection = duckdb.connect(str(db_path), read_only=True)\n"
            ),
            new_code_cell(
                "ticket_quality_flags = connection.execute(\n"
                "    \"select * from ticket_quality_flags\"\n"
                ").fetchdf()\n"
                "if 'last_public_interaction_at' in ticket_quality_flags.columns:\n"
                "    ticket_quality_flags['last_public_interaction_at'] = pd.to_datetime(ticket_quality_flags['last_public_interaction_at'], utc=True, errors='coerce')\n"
                "if 'last_private_interaction_at' in ticket_quality_flags.columns:\n"
                "    ticket_quality_flags['last_private_interaction_at'] = pd.to_datetime(ticket_quality_flags['last_private_interaction_at'], utc=True, errors='coerce')\n"
                "for column, default in {\n"
                "    'client_last_interaction_flag': False,\n"
                "    'it_follow_up_without_client_response_flag': False,\n"
                "    'stale_public_update_flag': False,\n"
                "    'private_activity_since_last_public_flag': False,\n"
                "    'stale_public_update_business_days': 0,\n"
                "    'last_private_interaction_at': pd.NaT,\n"
                "    'last_private_interaction_by': pd.NA,\n"
                "}.items():\n"
                "    if column not in ticket_quality_flags.columns:\n"
                "        ticket_quality_flags[column] = default\n"
                "ticket_quality_flags.head()"
            ),
            new_markdown_cell("## Executive Summary"),
            new_code_cell(
                "pd.DataFrame([\n"
                "    {'metric': 'Tickets reviewed', 'value': len(ticket_quality_flags)},\n"
                "    {'metric': 'Client last interaction', 'value': int(ticket_quality_flags['client_last_interaction_flag'].fillna(False).sum())},\n"
                "    {'metric': 'Repeated IT follow-up', 'value': int(ticket_quality_flags['it_follow_up_without_client_response_flag'].fillna(False).sum())},\n"
                "    {'metric': 'Stale public update', 'value': int(ticket_quality_flags['stale_public_update_flag'].fillna(False).sum())},\n"
                "    {'metric': 'Private activity since last public update', 'value': int(ticket_quality_flags['private_activity_since_last_public_flag'].fillna(False).sum())},\n"
                "])"
            ),
            new_code_cell(
                "quality_summary_chart = pd.DataFrame([\n"
                "    {'metric': 'Client last', 'tickets': int(ticket_quality_flags['client_last_interaction_flag'].fillna(False).sum())},\n"
                "    {'metric': 'Repeated IT follow-up', 'tickets': int(ticket_quality_flags['it_follow_up_without_client_response_flag'].fillna(False).sum())},\n"
                "    {'metric': 'Stale public update', 'tickets': int(ticket_quality_flags['stale_public_update_flag'].fillna(False).sum())},\n"
                "    {'metric': 'Private after public', 'tickets': int(ticket_quality_flags['private_activity_since_last_public_flag'].fillna(False).sum())},\n"
                "])\n"
                "px.bar(\n"
                "    quality_summary_chart,\n"
                "    x='metric',\n"
                "    y='tickets',\n"
                "    title='Open Ticket Quality Flags',\n"
                ")\n"
            ),
            new_markdown_cell("## Client Last Interaction"),
            new_code_cell(
                "ticket_quality_flags.loc[\n"
                "    ticket_quality_flags['client_last_interaction_flag'].fillna(False),\n"
                "    ['ticket_id', 'ticket_title', 'team_name', 'service_name', 'requestor_name', 'last_public_interaction_at', 'last_public_interaction_by'],\n"
                "].sort_values('last_public_interaction_at', ascending=False).head(50)"
            ),
            new_markdown_cell("## Repeated IT Follow-Up"),
            new_code_cell(
                "ticket_quality_flags.loc[\n"
                "    ticket_quality_flags['it_follow_up_without_client_response_flag'].fillna(False),\n"
                "    ['ticket_id', 'ticket_title', 'team_name', 'service_name', 'requestor_name', 'it_follow_up_streak', 'last_public_interaction_at', 'last_public_interaction_by'],\n"
                "].sort_values(['it_follow_up_streak', 'last_public_interaction_at'], ascending=[False, False]).head(50)"
            ),
            new_markdown_cell("## Stale Public Update"),
            new_code_cell(
                "ticket_quality_flags.loc[\n"
                "    ticket_quality_flags['stale_public_update_flag'].fillna(False),\n"
                "    ['ticket_id', 'ticket_title', 'team_name', 'service_name', 'requestor_name', 'stale_public_update_business_days', 'last_public_interaction_at', 'last_public_interaction_by'],\n"
                "].sort_values(['stale_public_update_business_days', 'last_public_interaction_at'], ascending=[False, False]).head(50)"
            ),
            new_markdown_cell("## Private Activity Since Last Public Update"),
            new_code_cell(
                "ticket_quality_flags.loc[\n"
                "    ticket_quality_flags['private_activity_since_last_public_flag'].fillna(False),\n"
                "    ['ticket_id', 'ticket_title', 'team_name', 'service_name', 'requestor_name', 'last_private_interaction_at', 'last_private_interaction_by', 'last_public_interaction_by'],\n"
                "].sort_values(['last_private_interaction_at', 'ticket_id'], ascending=[False, True]).head(50)"
            ),
        ]
    )
    nbformat.write(notebook, output_path)
    return output_path
