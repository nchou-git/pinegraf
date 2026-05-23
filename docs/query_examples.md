# Query Examples

These examples assume Postgres. SQLite tests use JSON fallbacks, so a few JSON
casts and trigram operators are Postgres-specific.

## 1. Find T'07 Alumni With Founder Roles

```sql
SELECT e.canonical_name, f.content::jsonb AS position, rp.source_url
FROM facts f
JOIN entities e ON e.id = f.entity_id
LEFT JOIN raw_pages rp ON rp.id = f.source_raw_page_id
WHERE f.category = 'position'
  AND f.validation_verdict = 'keep'
  AND (f.content::jsonb ->> 'position_type') = 'founder'
  AND EXISTS (
    SELECT 1
    FROM entity_attributes ea
    WHERE ea.entity_id = e.id
      AND ea.attribute_name = 'class_year'
      AND ea.attribute_value = 'T''07'
      AND ea.validation_verdict = 'keep'
  )
ORDER BY e.canonical_name;
```

## 2. Show Errik Anderson Attributes With Sources

```sql
SELECT ea.attribute_name, ea.attribute_value, ea.source, ea.source_url,
       ea.confidence, ea.last_verified_at
FROM entities e
JOIN entity_attributes ea ON ea.entity_id = e.id
WHERE e.canonical_name ILIKE 'Errik%Anderson%'
  AND ea.validation_verdict = 'keep'
ORDER BY ea.attribute_name, ea.source;
```

## 3. List Errik Anderson Connections

```sql
SELECT c.relationship_type, c.connected_name, c.context, c.confidence_score,
       c.is_inferred, c.derivation, rp.source_url
FROM connections c
JOIN entities e ON e.id = c.entity_id
LEFT JOIN raw_pages rp ON rp.id = c.source_raw_page_id
WHERE e.canonical_name ILIKE 'Errik%Anderson%'
  AND c.validation_verdict = 'keep'
ORDER BY c.is_inferred, c.relationship_type, c.connected_name;
```

## 4. Find Paths Up To 3 Hops From Errik To Another T'07 Alumna

```sql
WITH RECURSIVE start_entity AS (
  SELECT id
  FROM entities
  WHERE canonical_name ILIKE 'Errik%Anderson%'
  LIMIT 1
),
t07_people AS (
  SELECT entity_id
  FROM entity_attributes
  WHERE attribute_name = 'class_year'
    AND attribute_value = 'T''07'
    AND validation_verdict = 'keep'
),
edges AS (
  SELECT entity_id AS left_id, connected_entity_id AS right_id, relationship_type
  FROM connections
  WHERE validation_verdict = 'keep'
    AND entity_id IS NOT NULL
    AND connected_entity_id IS NOT NULL
  UNION ALL
  SELECT connected_entity_id, entity_id, relationship_type
  FROM connections
  WHERE validation_verdict = 'keep'
    AND entity_id IS NOT NULL
    AND connected_entity_id IS NOT NULL
),
walk AS (
  SELECT s.id AS current_id, ARRAY[s.id] AS path, ARRAY[]::text[] AS edge_types, 0 AS depth
  FROM start_entity s
  UNION ALL
  SELECT e.right_id, w.path || e.right_id, w.edge_types || e.relationship_type, w.depth + 1
  FROM walk w
  JOIN edges e ON e.left_id = w.current_id
  WHERE w.depth < 3
    AND NOT e.right_id = ANY(w.path)
)
SELECT ARRAY(
         SELECT e.canonical_name
         FROM unnest(path) WITH ORDINALITY AS p(entity_id, ord)
         JOIN entities e ON e.id = p.entity_id
         ORDER BY p.ord
       ) AS entity_path,
       edge_types
FROM walk
WHERE depth > 0
  AND current_id IN (SELECT entity_id FROM t07_people)
ORDER BY depth
LIMIT 20;
```

## 5. Find Pages And Chunks Mentioning Gyrobike

```sql
SELECT rp.id AS raw_page_id, rp.source_url, pc.chunk_index,
       left(pc.text, 300) AS excerpt
FROM page_chunks pc
JOIN raw_pages rp ON rp.id = pc.raw_page_id
WHERE pc.text ILIKE '%gyrobike%'
ORDER BY rp.id, pc.chunk_index;
```

## 6. Retrieve Top Trigram Matches For A Misspelling

```sql
SELECT rp.source_url, similarity(rp.page_text, 'gyrobyke') AS score
FROM raw_pages rp
WHERE rp.page_text % 'gyrobyke'
ORDER BY score DESC
LIMIT 20;
```

## 7. Show Projects With Participants

```sql
SELECT p.project_name, array_agg(DISTINCT e.canonical_name ORDER BY e.canonical_name) AS people,
       count(DISTINCT p.source_raw_page_id) AS source_pages
FROM projects p
JOIN entities e ON e.id = p.entity_id
WHERE p.validation_verdict = 'keep'
GROUP BY p.project_name
ORDER BY count(*) DESC, p.project_name;
```

## 8. Review Conflicting Current Employer Claims

```sql
SELECT e.canonical_name,
       array_agg(DISTINCT ea.attribute_value ORDER BY ea.attribute_value) AS employer_values,
       array_agg(DISTINCT ea.source ORDER BY ea.source) AS sources
FROM entity_attributes ea
JOIN entities e ON e.id = ea.entity_id
WHERE ea.attribute_name IN ('current_employer', 'current_company')
  AND ea.validation_verdict = 'keep'
GROUP BY e.id, e.canonical_name
HAVING count(DISTINCT lower(ea.attribute_value)) > 1
ORDER BY e.canonical_name;
```

## 9. Summarize LLM Spend For The Last 30 Days

```sql
SELECT date_trunc('day', ts) AS day, model,
       count(*) AS calls,
       sum(prompt_tokens + completion_tokens) AS total_tokens,
       round(sum(dollars)::numeric, 4) AS dollars
FROM llm_usage
WHERE ts >= now() - interval '30 days'
GROUP BY day, model
ORDER BY day DESC, model;
```

## 10. Inspect The Latest Extraction Audit

```sql
SELECT id, run_at, sample_size,
       diff_summary -> 'global' AS global_summary,
       diff_summary -> 'pages' AS page_summaries
FROM audit_runs
ORDER BY run_at DESC
LIMIT 1;
```
