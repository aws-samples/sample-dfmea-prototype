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
