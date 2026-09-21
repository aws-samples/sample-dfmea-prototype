import { clsx } from 'clsx'

interface Props {
  status: string
}

const STATUS_CLASSES: Record<string, string> = {
  SUBMITTED: 'bg-gray-100 text-gray-600',
  INTAKE_COMPLETE: 'bg-blue-50 text-blue-600',
  GATE_1_PENDING: 'bg-orange-50 text-orange-600',
  CAD_RUNNING: 'bg-blue-50 text-blue-600',
  GATE_2_PENDING: 'bg-orange-50 text-orange-600',
  AGENTS_RUNNING: 'bg-yellow-50 text-yellow-700',
  GATE_3_PENDING: 'bg-orange-50 text-orange-600',
  SYNTHESIS_RUNNING: 'bg-yellow-50 text-yellow-700',
  IN_REVIEW: 'bg-yellow-50 text-yellow-700',
  HITL_PENDING: 'bg-orange-50 text-orange-600',
  HITL_APPROVED: 'bg-success-50 text-success-600',
  REPORT_GENERATING: 'bg-blue-50 text-blue-600',
  HITL_REJECTED: 'bg-danger-50 text-danger-600',
  COMPLETE: 'bg-success-50 text-success-600',
  FAILED: 'bg-danger-50 text-danger-600',
  ARCHIVED: 'bg-gray-100 text-gray-400',
  ERROR: 'bg-danger-50 text-danger-600',
}

export default function StatusBadge({ status }: Props) {
  const cls = STATUS_CLASSES[status] ?? 'bg-gray-100 text-gray-600'
  return (
    <span className={clsx('inline-flex items-center px-2 py-0.5 rounded text-xs font-medium', cls)}>
      {status.replace(/_/g, ' ')}
    </span>
  )
}
