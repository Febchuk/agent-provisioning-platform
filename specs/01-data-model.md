# 01 — Data Model

SQLite via SQLModel. IDs are short random strings (`ag_…`, `v_…`) unless noted. JSON columns are stored as TEXT.

## Tables

### agents
| Field | Type | Notes |
|---|---|---|
| id | str PK | |
| slug | str unique | Used in share URL |
| name | str | |
| description | str | |
| deployed_version_id | str FK? | Null until first deploy |
| created_at | datetime | |

### agent_versions
| Field | Type | Notes |
|---|---|---|
| id | str PK | |
| agent_id | FK | |
| number | int | 1, 2, 3… per agent |
| parent_version_id | FK? | |
| system_prompt | text | |
| guidelines | JSON | `[{id, section, text, addresses: [case_id]}]` |
| tools | JSON | Subset of `["bash","read_file","write_file","edit_file","list_files"]` |
| model | str | Model name; null = default from env |
| max_steps | int | Default 15 |
| tool_timeout_s | int | Default 30 |
| files | JSON | `[{name, path_on_disk, size}]` seeded into each sandbox |
| source | enum | `created` · `manual` · `proposal` |
| change_note | str | |
| created_at | datetime | |

### conversations
| id | agent_id | version_id (pinned at creation) | channel (`share`·`api`·`playground`) | sandbox_id | created_at |

### messages
| id | conversation_id | seq | role (`user`·`assistant`·`tool`) | content | tool_calls JSON? | tool_call_id? | run_id? |

Stored in OpenAI chat format so history can be replayed exactly.

### runs
| Field | Type | Notes |
|---|---|---|
| id | str PK | |
| conversation_id | FK? | Null for eval trials |
| version_id | FK | |
| source | enum | `chat` · `api` · `eval` |
| status | enum | `running` · `succeeded` · `failed` · `step_limit` |
| trace | JSON | List of events (see `02` §Events) |
| final_answer | text | |
| steps | int | |
| error | text? | |
| started_at / finished_at | datetime | |

### feedback
| id | run_id | conversation_id | rating (`up`·`down`) | correction text? | status (`new`·`converted`·`dismissed`) | created_at |

### eval_cases
| Field | Type | Notes |
|---|---|---|
| id | str PK | |
| agent_id | FK | |
| name | str | |
| axis | str | Free text; defaults: `accuracy`, `format`, `tool-use`, `safety` |
| history | JSON | Messages up to and including the user message to answer |
| check_type | enum | `contains` · `python_assert` · `llm_judge` |
| check_spec | JSON | See `04` §Checks |
| pinned | bool | |
| hidden | bool | True for siblings; never shown to the improver |
| parent_case_id | FK? | For siblings |
| from_feedback_id | FK? | |
| status | enum | `draft` · `active` · `dismissed` |

### eval_runs
| id | agent_id | version_id | status | trials_per_case | started_at | finished_at |

### eval_results
| id | eval_run_id | case_id | trial (0..N-1) | passed bool | reason text | run_id |

### policies (one per agent)
| agent_id PK | min_avg_improvement_pct (default 5.0) | max_regressions JSON (default `{"accuracy":0,"safety":0,"tool-use":1,"format":1}`) | trials_per_case (3) | pass_threshold (2) |

### proposals
| Field | Type | Notes |
|---|---|---|
| id | str PK | |
| agent_id, base_version_id, candidate_version_id | FK | |
| diagnoses, ops, skipped, lint | JSON | Improver output (see `05`) |
| base_eval_run_id, cand_eval_run_id | FK | |
| verdict | JSON | See `04` §Verdict |
| status | enum | `generating` · `evaluating` · `ready` · `accepted` · `rejected` · `failed` |
| decision_note | str? | Required when accepting with `verdict.meets_policy = false` |
| created_at | datetime | |

### api_keys (P1)
| id | agent_id | key_hash (sha256) | last4 | revoked bool | created_at |

## Invariants

- DM-1 THE SYSTEM SHALL never update `agent_versions` rows after insert (no UPDATE path exists in code).
- DM-2 WHEN a conversation is created, THE SYSTEM SHALL pin it to the agent's deployed version at that moment; deploying a new version does not change existing conversations.
- DM-3 `guidelines[].id` values SHALL be unique within a version and stable across versions when a rule is unchanged.
- DM-4 A case with `hidden = true` SHALL always have `parent_case_id`.
- DM-5 `agent_versions.number` SHALL be strictly increasing per agent.

## Verification

- `test_versions_immutable` — attempt to modify via service layer raises.
- `test_conversation_pins_version` — deploy v2 after a conversation started on v1; next turn still uses v1.
- `test_version_numbers_monotonic`.
