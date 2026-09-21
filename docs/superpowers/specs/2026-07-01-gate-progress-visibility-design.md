# Gate Progress Visibility — Design Spec
**Date:** 2026-07-01
**Status:** Approved

## Overview

Add visible pipeline progress to the Review Detail page. Currently, pressing Approve on a gate is silent — the user has no sense of overall pipeline position or confirmation that their action was received. This spec covers three targeted changes: a pipeline stepper, gate descriptions, and a post-approval confirmation state.

## Goals

- Show the user where the review is in the 4-stage pipeline at all times
- Explain what each gate validated, in plain language
- Give immediate visual feedback after pressing Approve/Reject (not silent)
- Keep gate state fresh when the pipeline advances (WebSocket-driven)

## Non-Goals

- No new API endpoints
- No polling loops
- No changes to the store or auth layer
- No changes to Gate 4 (HITL) special block — it already has explanatory text

---

## File Changes

| File | Action | Change |
|------|--------|--------|
| `ui/src/components/PipelineStepper.tsx` | Create | 4-step horizontal progress bar driven by review status |
| `ui/src/components/GateCard.tsx` | Modify | Add `description` prop; add post-approval "Approved — pipeline advancing…" state |
| `ui/src/pages/ReviewDetailPage.tsx` | Modify | Mount `PipelineStepper`; pass descriptions to `GateCard`; fix WebSocket handler to call `fetchGates` |

---

## PipelineStepper Component

**File:** `ui/src/components/PipelineStepper.tsx`

Props:
```typescript
interface PipelineStepperProps {
  status: string   // current review status string
}
```

Four steps, rendered as a horizontal row with connecting lines:

| Step | Label |
|------|-------|
| 1 | Intake Validation |
| 2 | CAD Verification |
| 3 | Agent Analysis |
| 4 | Human Approval |

Status → active step mapping:

| Review Status | Active Step |
|---------------|-------------|
| `SUBMITTED` | 1 |
| `INTAKE_COMPLETE` | 2 |
| `IN_REVIEW` | 3 |
| `HITL_PENDING`, `HITL_APPROVED`, `HITL_REJECTED` | 4 |
| `COMPLETE` | all steps complete (all green) |
| `ERROR`, `ARCHIVED`, unknown | no highlight (all gray) |

Step circle styling:
- **Past step** (stepNum < active): green filled circle, white checkmark
- **Active step** (stepNum == active): yellow/amber ring, bold label
- **Future step** (stepNum > active): gray circle, muted label
- **All complete** (`COMPLETE`): all green with checkmarks

Placed in `ReviewDetailPage` directly below the details card (the 4-column metadata grid). It is NOT inserted inside the existing `<div className="card p-5 grid ...">` — that div is a CSS grid and inserting a child into it would make the stepper a grid cell. Instead render it as a sibling card below:

```tsx
{/* Details card */}
<div className="card p-5 grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
  ...metadata cells...
</div>

{/* Pipeline progress */}
<div className="card p-4">
  <PipelineStepper status={status} />
</div>
```

---

## GateCard Enhancements

**File:** `ui/src/components/GateCard.tsx`

### New `description` prop

```typescript
interface GateCardProps {
  reviewId: string
  gateNumber: number
  gateLabel: string
  gateDescription: string   // ← new
  gateData: GateStatus
  onDecision: () => void
}
```

Rendered as muted small text below the gate label, above the status badge row. Always visible regardless of gate state.

### Post-approval confirmation state

Two new local state variables (add to the React import: `import { useState, useEffect } from 'react'`):

```typescript
const [submitted, setSubmitted] = useState(false)
const [submittedAction, setSubmittedAction] = useState<'approve' | 'reject' | null>(null)
```

The existing `loading` state and `disabled={loading}` button behavior are **preserved unchanged**. `submitted` is set only after `setLoading(false)` has been called in the `finally` block (i.e. after the API call completes successfully).

After `decide()` resolves successfully (no error), inside the `try` block before `onDecision()`:
- Set `submitted = true`
- Set `submittedAction` to `"approve"` or `"reject"`
- Hide the approve/reject buttons
- Show confirmation text (see below) with a Tailwind `animate-spin` div (`w-3 h-3 rounded-full border-2 border-gray-300 border-t-gray-600`) beside it

Confirmation text by action:
- Approve: `"✓ Approved — pipeline advancing…"`
- Reject: `"✓ Rejected — pipeline halted."`

Use a `useEffect` with `[gateData.status]` as the dependency (NOT `[gateData]`) to reset `submitted`:

```typescript
useEffect(() => {
  setSubmitted(false)
}, [gateData.status])
```

This fires only when the status actually changes (to `APPROVED` or `REJECTED` after the re-fetch), not on every re-render when the parent creates a new `gateData` object reference. Only `submitted` is reset in this effect — `submittedAction` does not need explicit reset as it is overwritten on the next `decide()` call.

---

## ReviewDetailPage Changes

**File:** `ui/src/pages/ReviewDetailPage.tsx`

### 1. Update GATE_LABELS and add GATE_DESCRIPTIONS

The existing `GATE_LABELS` constant has stale labels for gates 2, 3, and 4. Update it to match the stepper and descriptions:

```typescript
const GATE_LABELS: Record<number, string> = {
  1: 'Intake Validation',
  2: 'CAD Verification',    // was: 'Structural Analysis'
  3: 'Agent Analysis',      // was: 'Regulatory Check'
  4: 'Human Approval',      // was: 'Final Approval'
}

const GATE_DESCRIPTIONS: Record<number, string> = {
  1: 'Validates all required fields, S/O/D scores, and component structure in the uploaded file',
  2: 'Verifies assembly hierarchy and CAD component connections',
  3: 'Parallel AI agents (failure mode, structural, regulatory) have all completed analysis',
  4: 'Final human sign-off before PDF report generation',
}
```

Passed as `gateDescription={GATE_DESCRIPTIONS[gateNumber] ?? ''}` to each `GateCard`.

### 2. PipelineStepper placement

Rendered as a **sibling card** directly below the existing details card (NOT inside it — the details card is a CSS grid and inserting a child would make the stepper a grid cell). The `<div className="space-y-6">` wrapper in `ReviewDetailPage` already handles vertical spacing:

```tsx
{/* Details card — unchanged */}
<div className="card p-5 grid grid-cols-2 sm:grid-cols-4 gap-4 text-sm">
  ...metadata cells...
</div>

{/* Pipeline progress — new sibling */}
<div className="card p-4">
  <PipelineStepper status={status} />
</div>
```

### 3. WebSocket handler fix

Current handler calls `fetchReview` but not `fetchGates`. Fix:

```typescript
const unsub = ws.onMessage((data) => {
  if (data && typeof data === 'object' && 'status' in data) {
    setLiveStatus(String((data as { status: string }).status))
    fetchReview(reviewId)
    fetchGates(reviewId)   // ← add this line
  }
})
```

---

## Gate Descriptions (User-Facing)

| Gate | Label | Description |
|------|-------|-------------|
| 1 | Intake Validation | Validates all required fields, S/O/D scores, and component structure in the uploaded file |
| 2 | CAD Verification | Verifies assembly hierarchy and CAD component connections |
| 3 | Agent Analysis | Parallel AI agents (failure mode, structural, regulatory) have all completed analysis |
| 4 | Human Approval | Final human sign-off before PDF report generation |

---

## Testing

Unit tests for `PipelineStepper`:
- `status=SUBMITTED` → step 1 active, steps 2-4 gray
- `status=IN_REVIEW` → steps 1-2 green, step 3 active, step 4 gray
- `status=COMPLETE` → all 4 steps green
- `status=ERROR` → all steps gray

Unit tests for `GateCard`:
- Description text renders when `gateDescription` prop is passed
- Post-approval: after `decide("approve")` resolves, `"✓ Approved — pipeline advancing…"` appears and buttons are hidden
- Post-rejection: after `decide("reject")` resolves, `"✓ Rejected — pipeline halted."` appears and buttons are hidden
- Confirmation clears when component is rerendered with new `gateData` where `status` has changed to `"APPROVED"` (simulates re-fetch completing)
- Confirmation clears when component is rerendered with `status` changed to `"REJECTED"`
