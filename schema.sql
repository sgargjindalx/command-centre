-- Command Centre schema

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

-- Seed projects (idempotent)
INSERT INTO projects (name, description, url) VALUES
  ('JIL MIS',            'Management Information System',   'https://web-production-4c602.up.railway.app'),
  ('JSL Command Centre',  'MD Command Centre on Snowflake', '')
ON CONFLICT (name) DO NOTHING;
