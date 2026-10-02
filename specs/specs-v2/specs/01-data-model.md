# 01 — Data Model (v2)

SQLite via SQLModel. IDs are short random strings (`ag_…`, `v_…`). JSON columns are stored as TEXT. Fields marked **v2** are new or changed; see `CHANGELOG-v2.md`.

## Tables

### agents
| Field | Type | Notes |
|---|---|---|
| id | str PK | |
| slug | str unique | Share URL |
| name, description | str | |
| deployed_version_id | FK? | |
| created_at | datetime | |

### agent_versions (immutable)
| Field | Type | Notes |
|---|---|---|
| id | str PK | |
| agent_id | FK | |
| number | int | Strictly increasing per agent |
| parent_version_id | FK? | |
| system_prompt | text | |
| guidelines | JSON | `[{id, section, text, addresses: [case_id]}]` |
| tools | JSON | Subset of the five tools |
| model | str? | Null = env default |
| reviewer | JSON? | **v2, P1** `{enabled, model}` (10) |
| max_steps, tool_timeout_s | int | 15, 30 |
| files | JSON | `[{name, stored_path, size}]` |
| source | enum | `created` · `manual` · `proposal` |
| change_note | str | |
| created_at | datetime | |

### conversations — **v2**
| Field | Type | Notes |
|---|---|---|
| id | str PK | |
| agent_id | FK | |
| visitor_id | str | **v2** Anonymous visitor cookie (CH-1); `owner` for playground |
| channel | enum | `share` · `api` · `playground` |
| title | str | **v2** First ~60 chars of the first user message |
| started_on_version_id | FK | **v2** Informational only (renamed from `version_id`) |
| sandbox_ref | str? | Live sandbox id; null when stopped |
| workspace_ref | str | **v2** Named volume / provider sandbox id that outlives the live sandbox |
| files_expired | bool | **v2** IS-10 |
| last_message_at | datetime | **v2** Sorts the history list |
| created_at | datetime | |

### messages
| id | conversation_id | seq | role | content | tool_calls JSON? | tool_call_id? | run_id? | version_id? (**v2**, on assistant messages) |

### runs
| Field | Type | Notes |
|---|---|---|
| id | str PK | |
| conversation_id | FK? | Null for eval trials and reproduction |
| version_id | FK | **v2** Source of truth for which version answered |
| source | enum | `chat` · `api` · `eval` · `repro` (**v2**) |
| status | enum | `running` · `succeeded` · `failed` · `step_limit` |
| trace | JSON | Events |
| final_answer | text | |
| steps | int | |
| usage | JSON | **v2** `{prompt_tokens, completion_tokens, model_calls, latency_ms, cost_usd}` (QC-1) |
| grounding | JSON | **v2** `{claims, ungrounded}` (QC-8) |
| error | text? | |
| started_at, finished_at | datetime | |

### feedback
| id | run_id | conversation_id | rating | correction? | signal_id? (**v2**) | created_at |

### signals — **v2** (11)
| id | agent_id | conversation_id? | run_id? | version_id | type | payload JSON | issue_id? | created_at |

### issues — **v2** (11)
| id | agent_id | title | lesson | axis | status | signal_count | repro JSON? | created_at | updated_at |

### eval_cases — **v2**
| Field | Type | Notes |
|---|---|---|
| id | str PK | |
| agent_id | FK | |
| name, axis | str | |
| history | JSON | Messages ending with the user message to answer |
| check_type | enum | `contains` · `python_assert` · `llm_judge` · `numeric` (**v2**) · `grounded` (**v2**) |
| check_spec | JSON | See `04` §Checks |
| pinned | bool | |
| split | enum | **v2** `improve` (improver may see failures) · `benchmark` (never shown to the improver) |
| origin | enum | **v2** `feedback` · `owner` · `ground_truth` · `variant` |
| issue_id | FK? | **v2** |
| from_feedback_id | FK? | |
| status | enum | `draft` · `active` · `dismissed` |

Removed in v2: `hidden`, `parent_case_id`.

### eval_runs
| id | agent_id | version_id | purpose (`base`·`candidate`·`adhoc`·`compare`) | status | trials_per_case | cost_usd (**v2**) | started_at | finished_at |

### eval_results
| id | eval_run_id | case_id | trial | passed | reason | run_id |

### policies — **v2**
| Field | Default |
|---|---|
| agent_id PK | |
| min_target_gain_pct | 10.0 (**v2**, replaces `min_avg_improvement_pct`) |
| max_regressions | `{"accuracy":0,"safety":0,"tool-use":1,"format":1}` |
| axis_floors | `{"safety":100}` (**v2**, minimum candidate pass rate % per axis) |
| max_cost_increase_pct | 25 (**v2**) |
| trials_per_case, pass_threshold | 3, 2 |
| min_signals, cooldown_hours, max_open_proposals | 3, 24, 1 (**v2**, 11) |
| min_signal_rate_pct, monitor_sample_pct | 5, 0 (**v2**, P1) |

### proposals — **v2**
| Field | Notes |
|---|---|
| id, agent_id, base_version_id, candidate_version_id | |
| target_axis | **v2** |
| issue_id | **v2** FK? |
| trigger_source | **v2** `issue_ready` · `manual` · `manual_override` |
| diagnoses, ops, skipped, lint | JSON |
| base_eval_run_id, cand_eval_run_id | |
| verdict | JSON (04) |
| cost_usd | **v2** Total spent producing the proposal |
| status | `generating` · `evaluating` · `ready` · `accepted` · `rejected` · `failed` |
| decision_note | |
| created_at, decided_at | |

### api_keys (P1)
| id | agent_id | key_hash | last4 | revoked | created_at |

## Invariants

- DM-1 Versions are never updated after insert.
- DM-2 (**rewritten v2**) WHEN a run starts in a conversation, THE SYSTEM SHALL use the agent's **currently deployed** version and record it on `runs.version_id` and on the resulting assistant message. IF it differs from the previous run's version in that conversation, THEN the run's first event SHALL be `version.changed {from, to}`.
- DM-3 Guideline ids are unique per version and stable when a rule is unchanged.
- DM-4 (**rewritten v2**) Cases with `split = benchmark` SHALL never be returned by `cases_for_improver()`.
- DM-5 Version numbers strictly increase per agent.
- DM-6 (**v2**) `issues.signal_count` equals the number of signals with that `issue_id`.

## Verification

`test_versions_immutable`, `test_run_uses_deployed_version_and_emits_version_changed` (replaces `test_conversation_pins_version`), `test_version_numbers_monotonic`, `test_signal_count_consistent`.
