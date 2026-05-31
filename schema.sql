-- SG Cmd Centre schema

CREATE TABLE IF NOT EXISTS projects (
  id          serial PRIMARY KEY,
  name        varchar(100) UNIQUE NOT NULL,
  description text,
  url         varchar(200),
  status      varchar(20) DEFAULT 'active',
  created_at  timestamp DEFAULT now()
);

CREATE TABLE IF NOT EXISTS project_updates (
  id          serial PRIMARY KEY,
  project_id  int REFERENCES projects(id),
  update_text text NOT NULL,
  update_date date NOT NULL,
  source      varchar(50) DEFAULT 'claude_project',
  created_at  timestamp DEFAULT now()
);

CREATE TABLE IF NOT EXISTS action_items (
  id          serial PRIMARY KEY,
  project_id  int REFERENCES projects(id),
  action_text text NOT NULL,
  detail_text text,
  done        boolean DEFAULT false,
  created_at  timestamp DEFAULT now()
);

-- ── Seed projects (upsert) ───────────────────────────────────────────
INSERT INTO projects (name, description, url, status) VALUES
  ('JIL MIS',             'Management Information System',  'https://web-production-4c602.up.railway.app', 'active'),
  ('JSL Command Centre',  'MD Dashboard on Snowflake',      '',                                            'active'),
  ('JSL Daily Upload',    'Daily data pipeline',            '',                                            'active'),
  ('Ajay List',           'Task tracking',                  '',                                            'active'),
  ('Jindal Accounts App', 'Finance app',                    '',                                            'dev'),
  ('SG Cmd Centre',       'Personal project tracker',       'https://web-production-ee75d.up.railway.app', 'active')
ON CONFLICT (name) DO UPDATE
  SET description = EXCLUDED.description,
      url         = EXCLUDED.url,
      status      = EXCLUDED.status;

-- Remove stale naming attempts from earlier sessions
DELETE FROM projects WHERE name IN ('SG Command Centre')
  AND NOT EXISTS (SELECT 1 FROM project_updates WHERE project_id = projects.id);

-- ── Seed action items (idempotent via text match) ────────────────────
INSERT INTO action_items (project_id, action_text, detail_text)
SELECT p.id, a.action_text, a.detail_text
FROM (VALUES
  ('JIL MIS', 'Ashwani to enter May non-payroll costs',       'Facility, Misc, Consultants'),
  ('JIL MIS', 'Get PeopleStrong API credentials from Arun',   'Replaces 13-file upload'),
  ('JIL MIS', 'Set up mis.jindalx.com domain',                'DNS CNAME via Arun'),
  ('JIL MIS', 'Confirm depreciation treatment with Ashwani',  'Option (a) only at line G'),
  ('JIL MIS', 'Rotate Railway API token',                     'railway.app/account/tokens')
) AS a(project_name, action_text, detail_text)
JOIN projects p ON p.name = a.project_name
WHERE NOT EXISTS (
  SELECT 1 FROM action_items ai
  WHERE ai.project_id = p.id AND ai.action_text = a.action_text
);
