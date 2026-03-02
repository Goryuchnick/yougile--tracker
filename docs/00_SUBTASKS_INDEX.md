# YouGile API v2 Subtasks - Documentation Index

**Analysis Date:** 2026-03-02  
**Source:** OpenAPI spec (data/document (1).json)

---

## Quick Navigation

### For the Impatient
→ **subtasks_quick_fix.md** - 3-minute read, fix the script in seconds

### For Understanding the Problem
→ **SUBTASKS_ANSWERS.md** - Direct answers to 3 key questions

### For Deep Dive
→ **subtasks_structure_analysis.md** - Complete OpenAPI spec analysis

---

## Three Questions Answered

### 1. Is there a special endpoint for creating subtasks?

**Answer: NO**

- YouGile uses single `POST /api-v2/tasks` for all task types
- No special `/subtasks` endpoint exists
- Distinction is in request parameters, not URLs

**Key section:** SUBTASKS_ANSWERS.md → "Question 1"

---

### 2. Do I need columnId when creating a subtask?

**Answer: NO - Must be OMITTED**

From OpenAPI spec (CreateTaskDto, lines 5496-5794):
- `columnId` (optional) - tells YouGile to show task on board
- `subtasks` (optional) - tells YouGile to bind task to parent

If you send `columnId` → task appears on board as card  
If you omit `columnId` → task stays invisible on board, can be subtask only

**Key section:** SUBTASKS_ANSWERS.md → "Question 2"

---

### 3. How to create task ONLY as subtask (no board duplicate)?

**Answer: 2-Step Process**

```
STEP 1: Create without columnId
POST /api-v2/tasks
{
  "title": "Subtask 1",
  "description": "...",
  "deadline": {...},
  "checklists": [...]
  /* NO columnId */
}
→ Returns { "id": "uuid-subtask" }

STEP 2: Bind to parent
PUT /api-v2/tasks/{parent_id}
{
  "subtasks": ["uuid-subtask"]
}
→ Subtask now visible ONLY inside parent
```

**Key section:** SUBTASKS_ANSWERS.md → "Question 3"

---

## Current Problem in Your Script

**File:** `d:/Programmes projects/yougile api/scripts/tasks/add_subtasks_to_tre599.py`

**Issue:** Line 186 contains `"columnId": column_id,`

This creates subtasks on the board, then binds them to parent → **DUPLICATE CARDS**

**Solution:** Delete line 186

**Key section:** subtasks_quick_fix.md → "What to change"

---

## Document Guide

### SUBTASKS_ANSWERS.md (7.7 KB)
Comprehensive answers with:
- 3 direct answers to your questions
- Full curl examples
- Python code samples
- Complete HTTP request/response pairs
- Reference to OpenAPI spec lines

**Read if:** You want complete reference material

---

### subtasks_quick_fix.md (3.6 KB)
Practical guide with:
- Exact line numbers to modify
- Copy-paste code changes
- Testing instructions
- Why it works (table)
- curl testing examples

**Read if:** You just want to fix the script quickly

---

### subtasks_structure_analysis.md (9.0 KB)
Technical deep-dive with:
- Complete OpenAPI spec analysis
- Why duplicates occur (detailed explanation)
- Full Python function examples
- Migration guide
- CreateTaskDto/UpdateTaskDto full specs

**Read if:** You want to understand the mechanics deeply

---

## Key Findings Summary

| Finding | Details |
|---------|---------|
| **Endpoints** | Only 3: GET /tasks, POST /tasks, PUT /tasks/{id} |
| **Subtask Creation** | No special endpoint - use POST /tasks |
| **columnId Role** | Controls board visibility, NOT subtask logic |
| **subtasks Field** | Array of parent binding, independent of columnId |
| **Problem in Script** | columnId forces subtask to be card on board |
| **Fix** | Remove columnId from subtask creation |
| **Result** | Clean hierarchy, no duplicates |

---

## OpenAPI Spec Reference

**File:** `d:/Programmes projects/yougile api/data/document (1).json`

### CreateTaskDto (lines 5496-5650)

```json
{
  "title": "string (required)",
  "columnId": "string (optional) - board column ID",
  "subtasks": "array (optional) - parent binding",
  "deadline": "object - deadline config",
  "checklists": "array - checklist items",
  "assigned": "array - user IDs",
  "stickers": "object - priority, etc"
}
```

### UpdateTaskDto (lines 5753-5900)

```json
{
  "title": "string",
  "columnId": "string (optional)",
  "subtasks": "array (optional)",
  "deleted": "boolean",
  "deadline": "object",
  "checklists": "array",
  "assigned": "array",
  "stickers": "object"
}
```

**Key:** Both treat `columnId` and `subtasks` as independent optional fields.

---

## Implementation Checklist

```
For Creating Clean Subtasks:

□ Create task with POST /api-v2/tasks
□ Do NOT include columnId in body
□ Include all needed fields: title, description, deadline, checklists
□ Receive back: { "id": "uuid-of-subtask" }
□ Store the UUID
□ Call PUT /api-v2/tasks/{parent_id}
□ Pass: { "subtasks": [uuid1, uuid2, ...] }
□ Subtask now appears ONLY inside parent
□ No duplicate cards on board
```

---

## Curl Testing

### Test 1: Create subtask without columnId

```bash
curl -X POST "https://yougile.com/api-v2/tasks" \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "title": "Test Subtask",
    "description": "Testing subtask creation",
    "deadline": {"deadline": 1743638400000}
  }'
```

Expected: `{ "id": "uuid-123" }`

### Test 2: Bind to parent

```bash
curl -X PUT "https://yougile.com/api-v2/tasks/{parent_id}" \
  -H "Authorization: Bearer YOUR_KEY" \
  -H "Content-Type: application/json" \
  -d '{
    "subtasks": ["uuid-123"]
  }'
```

Expected: `{ "id": "parent_id" }`

---

## Related Files in Project

```
d:/Programmes projects/yougile api/
├── data/
│   └── document (1).json          ← OpenAPI spec source
├── scripts/tasks/
│   └── add_subtasks_to_tre599.py  ← Script to fix
└── docs/
    ├── 00_SUBTASKS_INDEX.md       ← This file
    ├── SUBTASKS_ANSWERS.md        ← Complete answers
    ├── subtasks_quick_fix.md      ← Quick fix guide
    └── subtasks_structure_analysis.md ← Deep dive
```

---

## Contact/Questions

For reference, key insights:
- No special subtask endpoint needed
- columnId is the toggle for board visibility
- subtasks binding is separate concern
- These two fields (columnId, subtasks) are independent

All info sourced directly from OpenAPI spec.

---

**Last Updated:** 2026-03-02  
**Status:** Ready to use
