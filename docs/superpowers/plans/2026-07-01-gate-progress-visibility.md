# Gate Progress Visibility Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add a 4-step pipeline stepper, gate descriptions, and post-approval confirmation feedback to the Review Detail page so users can see where a review is in the pipeline and know their approval was received.

**Architecture:** Three targeted file changes: (1) new `PipelineStepper` component driven by review status string, (2) `GateCard` enhanced with a `gateDescription` prop and post-approval spinner confirmation, (3) `ReviewDetailPage` updated to mount the stepper, pass descriptions, fix the WebSocket handler to call `fetchGates`. No new API endpoints, no polling, no store changes.

**Tech Stack:** React 18, TypeScript, Tailwind CSS, Vite, Vitest + @testing-library/react (added in Task 1)

**Spec:** `docs/superpowers/specs/2026-07-01-gate-progress-visibility-design.md`

---

## File Map

| File | Action | Responsibility |
|------|--------|---------------|
| `ui/vitest.config.ts` | Create | Vitest config with jsdom + React plugin |
| `ui/src/test/setup.ts` | Create | @testing-library/jest-dom matchers |
| `ui/src/components/PipelineStepper.tsx` | Create | 4-step horizontal progress bar, status → active step mapping |
| `ui/src/components/__tests__/PipelineStepper.test.tsx` | Create | Unit tests: 4 status scenarios |
| `ui/src/components/GateCard.tsx` | Modify | Add `gateDescription` prop + `submitted`/`submittedAction` state + `useEffect` reset |
| `ui/src/components/__tests__/GateCard.test.tsx` | Create | Unit tests: description render, approve/reject confirmation, reset |
| `ui/src/pages/ReviewDetailPage.tsx` | Modify | Update `GATE_LABELS`, add `GATE_DESCRIPTIONS`, mount `PipelineStepper`, fix WebSocket handler |

---

### Task 1: Set up Vitest + React Testing Library

The project has no frontend test framework. Install Vitest and configure it before writing any tests.

**Files:**
- Create: `ui/vitest.config.ts`
- Create: `ui/src/test/setup.ts`
- Modify: `ui/package.json` (add test script + devDependencies)

- [ ] **Step 1: Install test dependencies**

Pin `vitest@^2.0.0` to ensure compatibility with the already-installed `vite@^5.4.1`:

```bash
cd /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype/ui
npm install --save-dev vitest@^2.0.0 @testing-library/react @testing-library/jest-dom @testing-library/user-event jsdom
```

Expected: packages installed, `node_modules` updated, `package.json` devDependencies updated.

- [ ] **Step 2: Add test script to package.json**

Edit `ui/package.json`, add to `"scripts"`:
```json
"test": "vitest run",
"test:watch": "vitest"
```

- [ ] **Step 3: Create vitest.config.ts**

Create `ui/vitest.config.ts`:
```typescript
import { defineConfig } from 'vitest/config'
import react from '@vitejs/plugin-react'

export default defineConfig({
  plugins: [react()],
  test: {
    environment: 'jsdom',
    globals: true,
    setupFiles: ['./src/test/setup.ts'],
  },
})
```

- [ ] **Step 4: Create test setup file**

Create `ui/src/test/setup.ts`:
```typescript
import '@testing-library/jest-dom'
```

- [ ] **Step 5: Verify Vitest works**

```bash
cd /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype/ui
npm test
```

Expected: `No test files found` (exits cleanly — no error, just no tests yet).

- [ ] **Step 6: Commit**

```bash
git add ui/vitest.config.ts ui/src/test/setup.ts ui/package.json ui/package-lock.json
git commit -m "test(ui): add Vitest + React Testing Library"
```

---

### Task 2: PipelineStepper component

**Files:**
- Create: `ui/src/components/PipelineStepper.tsx`
- Create: `ui/src/components/__tests__/PipelineStepper.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `ui/src/components/__tests__/PipelineStepper.test.tsx`:

```typescript
import { render, screen } from '@testing-library/react'
import { describe, test, expect } from 'vitest'
import PipelineStepper from '../PipelineStepper'

describe('PipelineStepper', () => {
  test('renders all 4 step labels regardless of status', () => {
    render(<PipelineStepper status="SUBMITTED" />)
    expect(screen.getByText('Intake Validation')).toBeInTheDocument()
    expect(screen.getByText('CAD Verification')).toBeInTheDocument()
    expect(screen.getByText('Agent Analysis')).toBeInTheDocument()
    expect(screen.getByText('Human Approval')).toBeInTheDocument()
  })

  test('SUBMITTED: no checkmarks (step 1 active, rest future)', () => {
    render(<PipelineStepper status="SUBMITTED" />)
    expect(screen.queryAllByText('✓')).toHaveLength(0)
  })

  test('IN_REVIEW: 2 checkmarks (steps 1-2 past, step 3 active)', () => {
    render(<PipelineStepper status="IN_REVIEW" />)
    expect(screen.queryAllByText('✓')).toHaveLength(2)
  })

  test('COMPLETE: 4 checkmarks (all steps past)', () => {
    render(<PipelineStepper status="COMPLETE" />)
    expect(screen.queryAllByText('✓')).toHaveLength(4)
  })

  test('ERROR: no checkmarks (all steps gray)', () => {
    render(<PipelineStepper status="ERROR" />)
    expect(screen.queryAllByText('✓')).toHaveLength(0)
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype/ui
npm test
```

Expected: FAIL — `Cannot find module '../PipelineStepper'`

- [ ] **Step 3: Implement PipelineStepper**

Create `ui/src/components/PipelineStepper.tsx`:

```typescript
// ui/src/components/PipelineStepper.tsx

interface PipelineStepperProps {
  status: string
}

const STEPS = [
  { num: 1, label: 'Intake Validation' },
  { num: 2, label: 'CAD Verification' },
  { num: 3, label: 'Agent Analysis' },
  { num: 4, label: 'Human Approval' },
]

/** Returns the active step number (1-4), 5 for COMPLETE (all done), null for no highlight. */
function statusToStep(status: string): number | null {
  switch (status) {
    case 'SUBMITTED':       return 1
    case 'INTAKE_COMPLETE': return 2
    case 'IN_REVIEW':       return 3
    case 'HITL_PENDING':
    case 'HITL_APPROVED':
    case 'HITL_REJECTED':   return 4
    case 'COMPLETE':        return 5   // sentinel: all steps complete
    default:                return null // ERROR, ARCHIVED, unknown → all gray
  }
}

export default function PipelineStepper({ status }: PipelineStepperProps) {
  const activeStep = statusToStep(status)

  return (
    <div className="flex items-start w-full">
      {STEPS.map((step, idx) => {
        const isPast   = activeStep !== null && step.num < activeStep
        const isActive = activeStep !== null && step.num === activeStep

        const circleClass = isPast
          ? 'bg-green-500 text-white'
          : isActive
          ? 'bg-amber-400 text-white ring-2 ring-amber-200'
          : 'bg-gray-200 text-gray-500'

        const labelClass = isPast
          ? 'text-green-700'
          : isActive
          ? 'text-gray-900 font-semibold'
          : 'text-gray-400'

        return (
          <div key={step.num} className="flex items-center flex-1 min-w-0">
            <div className="flex flex-col items-center shrink-0">
              <div
                className={`w-7 h-7 rounded-full flex items-center justify-center text-xs font-bold ${circleClass}`}
              >
                {isPast ? '✓' : step.num}
              </div>
              <span className={`text-xs mt-1 text-center leading-tight ${labelClass}`}>
                {step.label}
              </span>
            </div>
            {idx < STEPS.length - 1 && (
              <div
                className={`flex-1 h-0.5 mx-2 mb-5 ${isPast ? 'bg-green-400' : 'bg-gray-200'}`}
              />
            )}
          </div>
        )
      })}
    </div>
  )
}
```

- [ ] **Step 4: Run tests — expect pass**

```bash
cd /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype/ui
npm test
```

Expected: 5/5 PASS

- [ ] **Step 5: Commit**

```bash
git add ui/src/components/PipelineStepper.tsx ui/src/components/__tests__/PipelineStepper.test.tsx
git commit -m "feat(ui): add PipelineStepper component with 5 tests"
```

---

### Task 3: GateCard enhancements

**Files:**
- Modify: `ui/src/components/GateCard.tsx`
- Create: `ui/src/components/__tests__/GateCard.test.tsx`

- [ ] **Step 1: Write failing tests**

Create `ui/src/components/__tests__/GateCard.test.tsx`:

```typescript
import { render, screen, fireEvent, waitFor } from '@testing-library/react'
import { describe, test, expect, vi, beforeEach } from 'vitest'

// Mock the API module before importing GateCard
vi.mock('../../lib/api', () => ({
  apiPost: vi.fn(),
}))

import { apiPost } from '../../lib/api'
import GateCard from '../GateCard'

const mockApiPost = apiPost as ReturnType<typeof vi.fn>

const PENDING_GATE = {
  status: 'PENDING' as const,
  comment: '',
  pending: true,
}

const APPROVED_GATE = {
  status: 'APPROVED' as const,
  comment: 'Approved',
  pending: false,
}

beforeEach(() => {
  vi.clearAllMocks()
})

describe('GateCard', () => {
  test('renders gate description text', () => {
    render(
      <GateCard
        reviewId="r1"
        gateNumber={1}
        gateLabel="Intake Validation"
        gateDescription="Validates all required fields"
        gateData={PENDING_GATE}
        onDecision={() => {}}
      />
    )
    expect(screen.getByText('Validates all required fields')).toBeInTheDocument()
  })

  test('shows approval confirmation after approve', async () => {
    mockApiPost.mockResolvedValue({})
    render(
      <GateCard
        reviewId="r1"
        gateNumber={1}
        gateLabel="Intake Validation"
        gateDescription="desc"
        gateData={PENDING_GATE}
        onDecision={() => {}}
      />
    )
    fireEvent.click(screen.getByText('Approve'))
    await waitFor(() => {
      expect(screen.getByText(/Approved — pipeline advancing/)).toBeInTheDocument()
    })
    expect(screen.queryByText('Approve')).not.toBeInTheDocument()
  })

  test('shows rejection confirmation after reject', async () => {
    mockApiPost.mockResolvedValue({})
    render(
      <GateCard
        reviewId="r1"
        gateNumber={1}
        gateLabel="Intake Validation"
        gateDescription="desc"
        gateData={PENDING_GATE}
        onDecision={() => {}}
      />
    )
    fireEvent.click(screen.getByText('Reject'))
    await waitFor(() => {
      expect(screen.getByText(/Rejected — pipeline halted/)).toBeInTheDocument()
    })
    expect(screen.queryByText('Reject')).not.toBeInTheDocument()
  })

  test('confirmation clears when gateData.status changes to APPROVED', async () => {
    mockApiPost.mockResolvedValue({})
    const { rerender } = render(
      <GateCard
        reviewId="r1"
        gateNumber={1}
        gateLabel="Intake Validation"
        gateDescription="desc"
        gateData={PENDING_GATE}
        onDecision={() => {}}
      />
    )
    fireEvent.click(screen.getByText('Approve'))
    await waitFor(() => {
      expect(screen.getByText(/pipeline advancing/)).toBeInTheDocument()
    })
    // Simulate re-fetch completing: parent passes updated gateData
    rerender(
      <GateCard
        reviewId="r1"
        gateNumber={1}
        gateLabel="Intake Validation"
        gateDescription="desc"
        gateData={APPROVED_GATE}
        onDecision={() => {}}
      />
    )
    await waitFor(() => {
      expect(screen.queryByText(/pipeline advancing/)).not.toBeInTheDocument()
    })
  })

  test('confirmation clears when gateData.status changes to REJECTED', async () => {
    mockApiPost.mockResolvedValue({})
    const { rerender } = render(
      <GateCard
        reviewId="r1"
        gateNumber={1}
        gateLabel="Intake Validation"
        gateDescription="desc"
        gateData={PENDING_GATE}
        onDecision={() => {}}
      />
    )
    fireEvent.click(screen.getByText('Reject'))
    await waitFor(() => {
      expect(screen.getByText(/pipeline halted/)).toBeInTheDocument()
    })
    rerender(
      <GateCard
        reviewId="r1"
        gateNumber={1}
        gateLabel="Intake Validation"
        gateDescription="desc"
        gateData={{ status: 'REJECTED', comment: '', pending: false }}
        onDecision={() => {}}
      />
    )
    await waitFor(() => {
      expect(screen.queryByText(/pipeline halted/)).not.toBeInTheDocument()
    })
  })
})
```

- [ ] **Step 2: Run tests to verify they fail**

```bash
cd /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype/ui
npm test
```

Expected: GateCard tests FAIL — `gateDescription` prop does not exist yet, confirmation text does not appear.

- [ ] **Step 3: Implement GateCard changes**

Replace the full contents of `ui/src/components/GateCard.tsx`:

```typescript
// dfmea-prototype/ui/src/components/GateCard.tsx
import { useState, useEffect } from "react";
import { apiPost } from "../lib/api";

interface GateStatus {
  status: "NOT_STARTED" | "PENDING" | "APPROVED" | "REJECTED";
  comment: string;
  pending: boolean;
}

interface GateCardProps {
  reviewId: string;
  gateNumber: number;
  gateLabel: string;
  gateDescription: string;
  gateData: GateStatus;
  onDecision: () => void;
}

const STATUS_STYLES: Record<string, string> = {
  NOT_STARTED: "bg-gray-100 text-gray-500",
  PENDING:     "bg-yellow-100 text-yellow-800",
  APPROVED:    "bg-green-100 text-green-800",
  REJECTED:    "bg-red-100 text-red-800",
};

export default function GateCard({
  reviewId,
  gateNumber,
  gateLabel,
  gateDescription,
  gateData,
  onDecision,
}: GateCardProps) {
  const [comment, setComment]                 = useState("");
  const [loading, setLoading]                 = useState(false);
  const [error,   setError]                   = useState<string | null>(null);
  const [submitted, setSubmitted]             = useState(false);
  const [submittedAction, setSubmittedAction] = useState<"approve" | "reject" | null>(null);

  // Reset confirmation when the gate status changes (re-fetch completed)
  useEffect(() => {
    setSubmitted(false);
  }, [gateData.status]);

  async function decide(action: "approve" | "reject") {
    setLoading(true);
    setError(null);
    let succeeded = false;
    try {
      await apiPost(`/reviews/${reviewId}/gate/${gateNumber}`, { action, comment });
      succeeded = true;
      onDecision();
    } catch (err: unknown) {
      setError(err instanceof Error ? err.message : "Action failed");
    } finally {
      setLoading(false);
      if (succeeded) {
        setSubmitted(true);
        setSubmittedAction(action);
      }
    }
  }

  const confirmationText =
    submittedAction === "approve"
      ? "✓ Approved — pipeline advancing…"
      : "✓ Rejected — pipeline halted.";

  return (
    <div className="border rounded-lg p-4 mb-3">
      <div className="flex justify-between items-center mb-1">
        <h3 className="font-medium text-gray-800">
          Gate {gateNumber}: {gateLabel}
        </h3>
        <span
          className={`text-xs font-semibold px-2 py-1 rounded-full ${STATUS_STYLES[gateData.status] ?? ""}`}
        >
          {gateData.status.replace(/_/g, " ")}
        </span>
      </div>

      {gateDescription && (
        <p className="text-xs text-gray-400 mb-2">{gateDescription}</p>
      )}

      {gateData.comment && (
        <p className="text-sm text-gray-500 italic mb-2">"{gateData.comment}"</p>
      )}

      {submitted && submittedAction && (
        <div className="flex items-center gap-2 text-sm text-gray-600 py-1">
          <div className="w-3 h-3 rounded-full border-2 border-gray-300 border-t-gray-600 animate-spin" />
          {confirmationText}
        </div>
      )}

      {gateData.pending && !submitted && (
        <>
          <textarea
            value={comment}
            onChange={(e) => setComment(e.target.value)}
            placeholder="Add a comment (optional)"
            rows={2}
            className="w-full border rounded px-3 py-2 text-sm mb-2 focus:outline-none focus:ring-2 focus:ring-blue-500"
          />
          <div className="flex gap-2">
            <button
              onClick={() => decide("approve")}
              disabled={loading}
              className="flex-1 bg-green-600 text-white py-2 rounded text-sm hover:bg-green-700 disabled:opacity-50"
            >
              Approve
            </button>
            <button
              onClick={() => decide("reject")}
              disabled={loading}
              className="flex-1 bg-red-600 text-white py-2 rounded text-sm hover:bg-red-700 disabled:opacity-50"
            >
              Reject
            </button>
          </div>
          {error && <p className="text-red-600 text-sm mt-1">{error}</p>}
        </>
      )}
    </div>
  );
}
```

- [ ] **Step 4: Run TypeScript check**

```bash
cd /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype/ui
npm run type-check
```

Expected: no errors

- [ ] **Step 5: Run tests — expect pass**

```bash
npm test
```

Expected: 10/10 PASS (5 PipelineStepper + 5 GateCard)

- [ ] **Step 6: Commit**

```bash
git add ui/src/components/GateCard.tsx ui/src/components/__tests__/GateCard.test.tsx
git commit -m "feat(ui): add gate descriptions and post-approval confirmation to GateCard"
```

---

### Task 4: ReviewDetailPage wiring + TypeScript check

**Files:**
- Modify: `ui/src/pages/ReviewDetailPage.tsx`

No new tests — changes are wiring-only (import, constant updates, single JSX element, one added function call). TypeScript is the verification layer.

- [ ] **Step 1: Update ReviewDetailPage.tsx**

In `ui/src/pages/ReviewDetailPage.tsx`:

**a) Add PipelineStepper import** — append after the last existing import (`import { getConfig } from '../aws-config'`):
```typescript
import PipelineStepper from '../components/PipelineStepper'
```

**b) Update GATE_LABELS and add GATE_DESCRIPTIONS** — replace the entire `GATE_LABELS` block (from `const GATE_LABELS` through its closing `}`) with the two constants below:
```typescript
const GATE_LABELS: Record<number, string> = {
  1: 'Intake Validation',
  2: 'CAD Verification',
  3: 'Agent Analysis',
  4: 'Human Approval',
}

const GATE_DESCRIPTIONS: Record<number, string> = {
  1: 'Validates all required fields, S/O/D scores, and component structure in the uploaded file',
  2: 'Verifies assembly hierarchy and CAD component connections',
  3: 'Parallel AI agents (failure mode, structural, regulatory) have all completed analysis',
  4: 'Final human sign-off before PDF report generation',
}
```

**c) Fix WebSocket handler** — in the `ws.onMessage` callback (around line 47), add `fetchGates(reviewId)`:
```typescript
const unsub = ws.onMessage((data) => {
  if (
    data &&
    typeof data === 'object' &&
    'status' in data
  ) {
    setLiveStatus(String((data as { status: string }).status))
    fetchReview(reviewId)
    fetchGates(reviewId)   // ← add this line
  }
})
```

**d) Pass `gateDescription` to GateCard** — in the `GateCard` JSX (around line 136), add the new prop:
```tsx
<GateCard
  key={key}
  reviewId={reviewId!}
  gateNumber={gateNumber}
  gateLabel={GATE_LABELS[gateNumber] ?? `Gate ${gateNumber}`}
  gateDescription={GATE_DESCRIPTIONS[gateNumber] ?? ''}
  gateData={gateData}
  onDecision={() => {
    fetchReview(reviewId!)
    fetchGates(reviewId!)
  }}
/>
```

**e) Add PipelineStepper after the details card** — directly after the closing `</div>` of the details card (`card p-5 grid ...`), add:
```tsx
{/* Pipeline progress */}
<div className="card p-4">
  <PipelineStepper status={status} />
</div>
```

- [ ] **Step 2: Run TypeScript type check**

```bash
cd /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype/ui
npm run type-check
```

Expected: no errors

- [ ] **Step 3: Run all tests**

```bash
npm test
```

Expected: 10/10 PASS

- [ ] **Step 4: Commit**

```bash
git add ui/src/pages/ReviewDetailPage.tsx
git commit -m "feat(ui): wire PipelineStepper, gate descriptions, fix WebSocket gate refresh"
```

---

## Running the Tests

```bash
cd /mnt/c/Users/mahasrid/Downloads/Ext-repo/DFMEA_Demo/dfmea-prototype/ui

# Run all frontend tests once
npm test

# Watch mode during development
npm run test:watch

# Type check only
npm run type-check
```

## Verifying in the Browser

After deployment, open a review that is in `IN_REVIEW` status. You should see:
- A 4-step horizontal stepper with steps 1-2 green (checkmarks), step 3 amber (active), step 4 gray
- Each GateCard shows a description line in muted gray below the gate label
- Pressing Approve shows a spinner + "✓ Approved — pipeline advancing…" immediately
- When the next gate becomes pending (WebSocket push), gate cards refresh automatically
