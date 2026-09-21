import { render, screen } from '@testing-library/react'
import { describe, test, expect } from 'vitest'
import PipelineStepper from '../PipelineStepper'

describe('PipelineStepper — status-only (no gates)', () => {
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

  test('COMPLETE: 4 checkmarks (all steps past)', () => {
    render(<PipelineStepper status="COMPLETE" />)
    expect(screen.queryAllByText('✓')).toHaveLength(4)
  })

  test('ERROR: no checkmarks (all steps gray)', () => {
    render(<PipelineStepper status="ERROR" />)
    expect(screen.queryAllByText('✓')).toHaveLength(0)
  })
})

const approved = { pending: false, status: 'APPROVED' }
const pending  = { pending: true,  status: 'NOT_STARTED' }
const notStarted = { pending: false, status: 'NOT_STARTED' }

describe('PipelineStepper — gate-driven step highlighting', () => {
  test('gate 1 pending → step 1 active, no checkmarks', () => {
    const gates = { '1': pending }
    render(<PipelineStepper status="INTAKE_COMPLETE" gates={gates} />)
    expect(screen.queryAllByText('✓')).toHaveLength(0)
  })

  test('gate 1 approved, gate 2 not started → step 2 active, 1 checkmark', () => {
    const gates = { '1': approved, '2': notStarted }
    render(<PipelineStepper status="INTAKE_COMPLETE" gates={gates} />)
    expect(screen.queryAllByText('✓')).toHaveLength(1)
  })

  test('gate 2 pending → step 2 active, 1 checkmark (gate 1 done)', () => {
    const gates = { '1': approved, '2': pending }
    render(<PipelineStepper status="INTAKE_COMPLETE" gates={gates} />)
    expect(screen.queryAllByText('✓')).toHaveLength(1)
  })

  test('gate 2 approved, gate 3 not started → step 3 active, 2 checkmarks', () => {
    const gates = { '1': approved, '2': approved, '3': notStarted }
    render(<PipelineStepper status="INTAKE_COMPLETE" gates={gates} />)
    expect(screen.queryAllByText('✓')).toHaveLength(2)
  })

  test('gate 3 pending → step 3 active, 2 checkmarks', () => {
    const gates = { '1': approved, '2': approved, '3': pending }
    render(<PipelineStepper status="INTAKE_COMPLETE" gates={gates} />)
    expect(screen.queryAllByText('✓')).toHaveLength(2)
  })

  test('gate 3 approved → step 4 active, 3 checkmarks (synthesis running)', () => {
    const gates = { '1': approved, '2': approved, '3': approved }
    render(<PipelineStepper status="INTAKE_COMPLETE" gates={gates} />)
    expect(screen.queryAllByText('✓')).toHaveLength(3)
  })

  test('HITL_PENDING → step 4 active, 3 checkmarks', () => {
    const gates = { '1': approved, '2': approved, '3': approved }
    render(<PipelineStepper status="HITL_PENDING" gates={gates} />)
    expect(screen.queryAllByText('✓')).toHaveLength(3)
  })

  test('COMPLETE with gates → 4 checkmarks', () => {
    const gates = { '1': approved, '2': approved, '3': approved }
    render(<PipelineStepper status="COMPLETE" gates={gates} />)
    expect(screen.queryAllByText('✓')).toHaveLength(4)
  })
})
