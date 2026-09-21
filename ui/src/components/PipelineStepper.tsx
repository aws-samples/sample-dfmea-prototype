// ui/src/components/PipelineStepper.tsx

interface GateSummary {
  pending: boolean
  status: string
}

interface PipelineStepperProps {
  status: string
  gates?: Record<string, GateSummary> | null
}

const STEPS = [
  { num: 1, label: 'Intake Validation' },
  { num: 2, label: 'CAD Verification' },
  { num: 3, label: 'Agent Analysis' },
  { num: 4, label: 'Human Approval' },
]

/** Derive the active pipeline step from canonical statuses and gate data. */
function deriveActiveStep(
  status: string,
  gates: Record<string, GateSummary> | null | undefined,
): number | null {
  if (status === 'COMPLETE') return 5
  if (status === 'ERROR' || status === 'FAILED' || status === 'ARCHIVED' || status === 'HITL_REJECTED') return null

  // Real orchestration milestones take precedence over stale browser state.
  const statusSteps: Record<string, number> = {
    SUBMITTED: 1,
    INTAKE_COMPLETE: 1,
    GATE_1_PENDING: 1,
    CAD_RUNNING: 2,
    GATE_2_PENDING: 2,
    AGENTS_RUNNING: 3,
    IN_REVIEW: 3,
    GATE_3_PENDING: 3,
    SYNTHESIS_RUNNING: 4,
    HITL_PENDING: 4,
    HITL_APPROVED: 4,
    REPORT_GENERATING: 4,
  }
  if (statusSteps[status]) return statusSteps[status]

  if (gates) {
    const g = (n: number): GateSummary => gates[String(n)] ?? { pending: false, status: 'NOT_STARTED' }
    if (g(4).pending || g(3).status === 'APPROVED') return 4
    if (g(3).pending || g(2).status === 'APPROVED') return 3
    if (g(2).pending || g(1).status === 'APPROVED') return 2
    if (g(1).pending) return 1
  }

  return null
}

export default function PipelineStepper({ status, gates }: PipelineStepperProps) {
  const activeStep = deriveActiveStep(status, gates)

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
